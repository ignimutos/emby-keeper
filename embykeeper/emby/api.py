import asyncio
import random
import uuid
from typing import List, Union, Optional

from loguru import logger
from pydantic import BaseModel, ValidationError

from embykeeper import __version__
from embykeeper.cache import cache
from embykeeper.schema import EmbyAccount
from embykeeper.config import config
from embykeeper.emby.notification import EmbyWatchResult
from embykeeper.emby.errors import (
    EmbyError,
    EmbyRequestError,
    EmbyConnectError,
    EmbyLoginError,
    EmbyStatusError,
    EmbyPlayError,
    EmbyStoppedReportError,
)
from embykeeper.emby.keepalive import KeepaliveRun
from embykeeper.emby.playback import PlaybackSession
from embykeeper.emby.transport import EmbyTransport

logger = logger.bind(scheme="embywatcher")

EMBY_FINGERPRINT_FIELDS = ("client", "device", "device_id", "client_version", "useragent")
DEFAULT_EMBY_CLIENT = "Hills"
DEFAULT_EMBY_CLIENT_VERSION = "1.6.1"
DEFAULT_EMBY_WATCH_TIME = [300, 600]


class EmbyEnv(BaseModel):
    client: str
    device: str
    device_id: str
    client_version: str
    useragent: str


class Emby:
    playing_count = 0

    def __init__(self, account: EmbyAccount):
        self.a = account

        self._env = None
        self._token = None
        self._user_id = None

        self.run_id = str(uuid.uuid4()).upper()
        self.cf_clearance = None
        self.useragent = None
        self.items = {}

        self.log = logger.bind(server=self.a.name or self.hostname, username=self.a.username)

        self._transport = EmbyTransport(self)

    @property
    def proxy(self):
        return config.proxy if self.a.use_proxy else None

    @property
    def hostname(self):
        return self.a.url.host

    @property
    def token(self):
        if not self._token:
            self._load_credentials()
        return self._token

    @property
    def env(self):
        if not self._env:
            self._load_env()
        if not self._env:
            self._env = self.get_fake_env()
        return self._env

    @property
    def user_id(self):
        if not self._user_id:
            self._load_credentials()
        return self._user_id

    def _load_credentials(self):
        data: dict = cache.get(f"emby.credential.{self.hostname}.{self.a.username}", {})
        self._token = data.get("token", None)
        self._user_id = data.get("userid", None)

    def _configured_env_value(self, key: str) -> Optional[str]:
        account_value = getattr(self.a, key)
        if account_value:
            return account_value
        try:
            return getattr(config.emby, key, None)
        except RuntimeError:
            return None

    def _config_snapshot(self):
        return {key: self._configured_env_value(key) for key in EMBY_FINGERPRINT_FIELDS}

    def _configured_watch_time(self):
        if self.a.time is not None and "time" in self.a.model_fields_set:
            return self.a.time
        try:
            global_time = getattr(config.emby, "time", None)
        except RuntimeError:
            global_time = None
        if global_time is not None:
            return global_time
        return self.a.time if self.a.time is not None else DEFAULT_EMBY_WATCH_TIME

    def _load_env(self):
        cache_key = f"emby.env.{self.hostname}.{self.a.username}"
        data: dict = cache.get(cache_key, {})
        if data:
            should_clear = False
            snapshot = self._config_snapshot()
            cached_snapshot = data.get("config_snapshot")

            if cached_snapshot is None:
                for key, configured_value in snapshot.items():
                    if configured_value and data.get(key) != configured_value:
                        should_clear = True
                        break
                if (
                    not should_clear
                    and snapshot["client"] is None
                    and data.get("client") in {"Fileball", "Filebar"}
                ):
                    should_clear = True
            elif cached_snapshot != snapshot:
                should_clear = True

            if should_clear:
                logger.info("账户设置已修改, 将重新生成环境 (Headers).")
                self._env = None
                cache.delete(cache_key)
            else:
                try:
                    self._env = EmbyEnv.model_validate(data)
                except ValidationError:
                    logger.warning("缓存加载失败, 将重新生成环境 (Headers).")
                    self._env = None

    @staticmethod
    def get_random_device():
        from faker import Faker

        device_type = random.choice(("iPhone", "iPad"))

        # All patterns with their weights
        patterns = [
            ("chinese_normal", 20),
            ("chinese_lastname_pinyin", 40),
            ("chinese_firstname_pinyin", 10),
            ("english_normal", 20),
            ("english_upper", 10),
            ("english_name_only", 10),
        ]

        pattern = random.choices([p[0] for p in patterns], weights=[p[1] for p in patterns])[0]

        if pattern.startswith("chinese"):
            fake = Faker("zh_CN")
            surname = fake.last_name()
            given_name = fake.first_name_male() if random.random() < 0.5 else fake.first_name_female()

            if pattern == "chinese_normal":
                return f"{surname}{given_name}的{device_type}"
            else:
                from xpinyin import Pinyin

                p = Pinyin()
                if pattern == "chinese_lastname_pinyin":
                    pinyin = p.get_pinyin(surname).capitalize()
                    return f"{pinyin}的{device_type}"
                else:  # chinese_firstname_pinyin
                    pinyin = "".join([word[0].upper() for word in p.get_pinyin(given_name).split("-")])
                    return f"{pinyin}的{device_type}"
        else:
            fake = Faker("en_US")
            name = fake.first_name()

            if pattern == "english_normal":
                return f"{name}'s {device_type}"
            elif pattern == "english_upper":
                return f"{name.upper()}{device_type.upper()}"
            else:  # english_name_only
                return name

    @staticmethod
    def get_device_uuid():
        rd = random.Random()
        rd.seed(uuid.getnode())
        return uuid.UUID(int=rd.getrandbits(128))

    def get_fake_env(self):
        cache_key = f"emby.env.{self.hostname}.{self.a.username}"
        cached_env: dict = cache.get(cache_key, {})
        snapshot = self._config_snapshot()

        if snapshot["client"] is None and cached_env.get("client") in {"Fileball", "Filebar"}:
            cached_env = {}

        version = (
            snapshot["client_version"] or cached_env.get("client_version") or DEFAULT_EMBY_CLIENT_VERSION
        )
        client = snapshot["client"] or cached_env.get("client") or DEFAULT_EMBY_CLIENT
        device = snapshot["device"] or cached_env.get("device") or self.get_random_device()
        device_id = snapshot["device_id"] or cached_env.get("device_id") or str(uuid.uuid4()).upper()
        useragent = (
            self.useragent
            or snapshot["useragent"]
            or cached_env.get("useragent")
            or cached_env.get("ua")
            or f"{client}/{version} (android; 15)"
        )

        data = {
            "client": client,
            "device": device,
            "device_id": device_id,
            "client_version": version,
            "useragent": useragent,
            "config_snapshot": snapshot,
        }

        env = EmbyEnv(**data)
        cache.set(cache_key, data)
        return env

    # --- 传输委托 (逻辑见 emby/transport.py) ---

    def build_headers(self):
        return self._transport.build_headers()

    def _get_session(self, **overrides):
        return self._transport._get_session(**overrides)

    def _format_connect_error(self, error, url):
        return self._transport._format_connect_error(error, url)

    def _get_api_base_url(self):
        return self._transport._get_api_base_url()

    def _resolve_stream_url(self, url):
        return self._transport._resolve_stream_url(url)

    async def _request(self, method, path, _login=False, _session_kwargs=None, **kw):
        return await self._transport._request(
            method, path, _login=_login, _session_kwargs=_session_kwargs, **kw
        )

    def _is_http2_flow_control_error(self, error):
        return self._transport._is_http2_flow_control_error(error)

    async def _open_stream_with_fallback(self, url, length, play_session_id):
        return await self._transport._open_stream_with_fallback(url, length, play_session_id)

    async def _stream_media(self, url, play_session_id):
        return await self._transport._stream_media(url, play_session_id)

    async def use_cfsolver(self):
        return await self._transport.use_cfsolver()

    async def login(self):
        return await self._transport.login()

    async def play(self, item: Union[dict, int], time: float = 10):
        """开始一次模拟播放会话. 会话生命周期已下沉到 playback.PlaybackSession."""
        return await PlaybackSession(client=self, item=item, time=time).run()

    async def load_main_page(self):
        views = await self._request(
            method="GET",
            path=f"/Users/{self.user_id}/Views",
            params=dict(IncludeExternalContent=False),
        )

        col_ids = []
        for i in views.json().get("Items", []):
            cid: str = i.get("Id", None)
            type: str = i.get("CollectionType")
            if cid and type and type.lower() in ("movies", "tvshows"):
                col_ids.append(cid)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        user = await self._request(method="GET", path=f"/Users/{self.user_id}")
        last_login_date = user.json().get("LastLoginDate", None)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        await self._request(
            method="GET",
            path=f"/DisplayPreferences/usersettings",
            params=dict(client="emby", userId=self.user_id),
        )
        await asyncio.sleep(random.uniform(0.1, 0.3))

        await self.get_resume_items(media_types=["Video"])
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await self.get_resume_items(media_types=["Audio"])
        await asyncio.sleep(random.uniform(0.1, 0.3))

        for cid in col_ids[:25]:
            items = await self.get_latest_items(parent_id=cid)
            for item in items:
                try:
                    iid = item["Id"]
                    self.items[iid] = item
                except KeyError:
                    pass

        if not self.items:
            if col_ids:
                self.log.info("无法获取最新视频, 尝试从文件夹中读取.")

                for col_id in col_ids[:3]:
                    await asyncio.sleep(4)
                    items = await self.get_folder_items(parent_id=col_id)
                    for item in items:
                        try:
                            iid = item["Id"]
                            self.items[iid] = item
                        except KeyError:
                            pass
                    if len(self.items) >= 3:
                        break

        return last_login_date

    async def get_latest_items(
        self,
        enable_image_types=None,
        fields=None,
        limit=16,
        group_items=True,
        parent_id=None,
        **kw,
    ) -> List[dict]:
        if not enable_image_types:
            enable_image_types = ["Primary", "Backdrop", "Thumb"]
        if not fields:
            fields = [
                "PrimaryImageAspectRatio",
                "BasicSyncInfo",
                "ProductionYear",
                "Status",
                "EndDate",
                "CanDelete",
            ]
        resp = await self._request(
            method="GET",
            path=f"/Users/{self.user_id}/Items/Latest",
            params={
                "EnableImageTypes": ",".join(enable_image_types),
                "Fields": ",".join(fields),
                "GroupItems": group_items,
                "Limit": limit,
                "ParentId": parent_id,
                **kw,
            },
        )
        return resp.json()

    async def get_resume_items(
        self,
        enable_image_types=None,
        fields=None,
        limit=12,
        media_types=None,
        **kw,
    ) -> List[dict]:
        if not enable_image_types:
            enable_image_types = ["Primary", "Backdrop", "Thumb"]
        if not fields:
            fields = ["PrimaryImageAspectRatio", "BasicSyncInfo", "ProductionYear", "CanDelete"]
        if not media_types:
            media_types = ["Video"]
        resp = await self._request(
            method="GET",
            path=f"/Users/{self.user_id}/Items/Resume",
            params={
                "EnableImageTypes": ",".join(enable_image_types),
                "Fields": ",".join(fields),
                "Limit": limit,
                "MediaTypes": ",".join(media_types),
                "Recursive": "true",
                **kw,
            },
        )
        return resp.json()

    async def get_resume_item(self, iid):
        items = await self.get_resume_items(media_types=["Video"], limit=50)
        if isinstance(items, dict):
            items = items.get("Items", [])
        return next((item for item in items if item.get("Id") == iid), None)

    async def get_folder_items(
        self,
        parent_id,
        enable_image_types=None,
        fields=None,
        limit=50,
        **kw,
    ) -> List[dict]:
        if not enable_image_types:
            enable_image_types = ["Primary", "Backdrop", "Thumb"]
        if not fields:
            fields = ["BasicSyncInfo", "CanDelete", "PrimaryImageAspectRatio", "ProductionYear"]
        resp = await self._request(
            method="GET",
            path=f"/Users/{self.user_id}/Items",
            params={
                "EnableImageTypes": ",".join(enable_image_types),
                "Fields": ",".join(fields),
                "ImageTypeLimit": 1,
                "IncludeItemTypes": "Movie",
                "Limit": limit,
                "ParentId": parent_id,
                "Recursive": "true",
                "SortBy": "SortName",
                "SortOrder": "Ascending",
                "StartIndex": 0,
                **kw,
            },
        )
        return resp.json().get("Items", [])

    async def get_item(self, iid, **kw) -> dict:
        resp = await self._request(method="GET", path=f"/Users/{self.user_id}/Items/{iid}")
        return resp.json()

    async def get_user(self) -> dict:
        """Get current user information."""
        response = await self._request("GET", f"/Users/{self.user_id}")
        return response.json()

    async def mark_played(self, item_id: str) -> bool:
        """Mark an item as played."""
        response = await self._request("POST", f"/Users/{self.user_id}/PlayedItems/{item_id}")
        return response.status_code == 200

    async def watch(self) -> EmbyWatchResult:
        """播放一个或多个视频直到满足账号播放时长要求.

        决策树已下沉到 keepalive.KeepaliveRun, 此处仅解析配置并委托.
        """
        try:
            max_retries = config.emby.retries
        except RuntimeError:
            # 配置未加载时无重试语义 (仅当配置被显式覆盖时才进入重试分支)
            max_retries = 0
        return await KeepaliveRun(client=self, max_retries=max_retries).run()
