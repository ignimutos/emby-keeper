"""Emby HTTP 传输适配器.

把传输层 (会话、重试/回退、流媒体、Cloudflare 解析、URL 解析、认证登录)
从 Emby 大类中拆出, 形成一条缝 (seam): Emby 依赖 EmbyTransport, 测试可注入
假 owner / 假会话而无需 monkeypatch 传输内部。

注意: 跨方法调用一律经 owner 转发, 以保留对 Emby 实例方法 (如 _request、
_get_session) 的 monkeypatch 语义——这是传输逻辑归属本模块、但测试面仍可
收口在门面上的关键。
"""

import asyncio
import random
import re
from datetime import datetime
from urllib.parse import urlparse

from loguru import logger
from curl_cffi import CurlHttpVersion
from curl_cffi.requests import AsyncSession, Response, RequestsError

from embykeeper.utils import get_proxy_str
from embykeeper.cache import cache
from embykeeper.crypto import encrypt_credential

from embykeeper.emby.errors import (
    EmbyConnectError,
    EmbyLoginError,
    EmbyPlayError,
    EmbyStatusError,
)

logger = logger.bind(scheme="embywatcher")


def _parse_json(resp: Response) -> dict:
    """解析 JSON 响应; 服务器返回非 JSON (如网关错误页/Cloudflare 验证页) 时给出可读错误."""
    try:
        return resp.json()
    except ValueError:
        raise EmbyStatusError(
            f"服务器返回了非 JSON 响应 (URL = {getattr(resp, 'url', '?')}, HTTP {resp.status_code}), "
            "可能为网关错误页或 Cloudflare 验证页"
        )


class EmbyTransport:
    """Emby 服务器 HTTP 传输. 状态 (env/token/身份指纹) 仍归属 Emby, 经 owner 读取."""

    def __init__(self, owner):
        self.owner = owner

    # --- 头部与会话 ---

    def build_headers(self):
        owner = self.owner
        headers = {}
        auth_header = (
            f'Client="{owner.env.client}", Device="{owner.env.device}", '
            f'DeviceId="{owner.env.device_id}", Version="{owner.env.client_version}"'
        )
        full_auth_header = f"Emby {auth_header}"
        headers["User-Agent"] = owner.useragent or owner.env.useragent
        headers["Accept-Language"] = "zh-cn"
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "*/*"
        headers["X-Emby-Authorization"] = full_auth_header
        if owner.token:
            headers["X-Emby-Token"] = owner.token
            headers["Authorization"] = f'MediaBrowser {auth_header}, Token="{owner.token}"'
        return headers

    def _get_session(self, **overrides) -> AsyncSession:
        owner = self.owner
        session_kwargs = dict(
            verify=owner.verify,
            headers=self.build_headers(),
            proxy=get_proxy_str(owner.proxy, curl=True),
            timeout=10.0,
            impersonate="chrome",
            allow_redirects=True,
            default_headers=False,
        )
        session_kwargs.update(overrides)
        return AsyncSession(**session_kwargs)

    def _format_connect_error(self, error: Exception, url: str) -> str:
        error_msg = re.sub(r"\s+See\s+.*?\s+first for more details\.\.?", "", str(error))
        if "TLSV1_ALERT_UNRECOGNIZED_NAME" in error_msg:
            return f"{error_msg} " f'请检查服务器地址 "{url}" 的主机名是否与证书、SNI 或反向代理配置匹配.'
        return error_msg

    # --- URL 解析 ---

    def _get_api_base_url(self) -> str:
        base_url = str(self.owner.a.url).rstrip("/")
        if base_url.endswith("/emby"):
            return base_url
        return f"{base_url}/emby"

    def _resolve_stream_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url

        api_base_url = self._get_api_base_url().rstrip("/")
        api_path = urlparse(api_base_url).path.rstrip("/")
        account_path = urlparse(str(self.owner.a.url).rstrip("/")).path.rstrip("/")
        server_path = account_path.removesuffix("/emby")

        parsed_url = urlparse(url)
        stream_path = parsed_url.path or url
        for prefix in (api_path, server_path, "/emby"):
            if prefix and prefix != "/" and stream_path.startswith(prefix + "/"):
                stream_path = stream_path[len(prefix) :]
                break

        normalized_url = parsed_url._replace(path=stream_path).geturl()
        return f"{api_base_url}/{normalized_url.lstrip('/')}"

    # --- 核心请求 (重试 / 401 重登 / 502-504 退避 / Cloudflare 403) ---

    async def _request(self, method: str, path: str, _login=False, _session_kwargs=None, **kw) -> Response:
        owner = self.owner

        if path.startswith(("http://", "https://")):
            url = path
        else:
            base_url = self._get_api_base_url().rstrip("/")
            url = f"{base_url}/{path.lstrip('/')}"

        last_err = None
        session_kwargs = _session_kwargs or {}
        for _ in range(3):
            try:
                async with owner._get_session(**session_kwargs) as session:
                    resp: Response = await session.request(method, url, **kw)
                    if resp.status_code == 401 and owner.a.username and not _login:
                        if not await owner.login():
                            raise EmbyLoginError("无法登陆到服务器")
                        continue
                    elif resp.status_code in (502, 503, 504):
                        await asyncio.sleep(random.random() * 2 + 0.5)
                        continue
                    elif resp.status_code == 403 or (
                        not kw.get("stream") and ("cf-wrapper" in resp.text or "Just a moment" in resp.text)
                    ):
                        raise EmbyStatusError(
                            "访问失败: 服务器返回 HTTP 403 或 Cloudflare 验证页 (可能启用了 Cloudflare 保护)"
                        )
                    elif not resp.ok and not _login:
                        raise EmbyStatusError(f"访问失败: 异常 HTTP 代码 {resp.status_code} (URL = {url})")
                    else:
                        return resp
            except RequestsError as e:
                last_err = e
                await asyncio.sleep(random.random() + 0.5)

        if last_err:
            raise EmbyConnectError(
                f"{last_err.__class__.__name__}: {self._format_connect_error(last_err, url)}"
            )
        else:
            raise EmbyConnectError(f'连接到 "{url}" 重试超限')

    def _is_http2_flow_control_error(self, error: Exception) -> bool:
        message = str(error)
        return "nghttp2_submit_window_update()" in message or "Flow control error" in message

    # --- 流媒体模拟 ---

    async def _open_stream_with_fallback(self, url: str, length: int, play_session_id: str):
        owner = self.owner
        stream_headers = {
            "User-Agent": owner.useragent or owner.env.useragent,
            "Accept": "*/*",
            "Icy-MetaData": "1",
            "Range": f"bytes={length}-",
        }
        return await owner._request(
            method="GET",
            path=url,
            stream=True,
            max_recv_speed=1024,
            timeout=None,
            _session_kwargs={
                "headers": stream_headers,
                "http_version": CurlHttpVersion.V1_1,
                "impersonate": None,
            },
        )

    async def _stream_media(self, url: str, play_session_id: str):
        owner = self.owner
        length = 0
        last_err_time = datetime.now()
        consecutive_errors = 0
        while True:
            resp = await owner._open_stream_with_fallback(url, length, play_session_id)
            try:
                async for chunk in resp.aiter_content(chunk_size=1024):
                    length += len(chunk)
                    await asyncio.sleep(random.random())
                return
            except RequestsError:
                if (datetime.now() - last_err_time).total_seconds() > 5:
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        raise EmbyPlayError(f"流媒体访问连续失败 {consecutive_errors} 次, 放弃播放.")
                    owner.log.debug("流媒体文件访问错误, 正在重试.")
                    last_err_time = datetime.now()
                    continue
                raise
            finally:
                await resp.aclose()

    # --- 认证 ---

    async def login(self) -> dict:
        """Login to Emby server and get authentication token."""
        owner = self.owner
        if owner.a.username is None or owner.a.password is None:
            owner.log.warning("没有提供用户名或密码, 无法登陆, 执行失败.")
            return None

        data = {
            "Username": owner.a.username,
            "Pw": owner.a.password,
        }

        resp = await self._request(
            "POST",
            "/Users/AuthenticateByName",
            json=data,
            _login=True,
        )

        if resp.status_code == 401:
            owner.log.warning(f"用户名或密码错误, 执行失败.")
            return None

        if resp.status_code != 200:
            owner.log.warning(f"登陆时出现错误 ({resp.status_code}), 执行失败.")
            return None

        user: dict = _parse_json(resp)
        owner._token = user.get("AccessToken", None)
        owner._user_id = user.get("User", {}).get("Id")
        if owner.token and owner.user_id:
            cache_data = {
                "token": owner.token,
                "userid": owner.user_id,
            }
            cache.set(
                f"emby.credential.{owner.hostname}.{owner.a.username}",
                encrypt_credential(cache_data, owner.a.password),
            )
            return owner.token
