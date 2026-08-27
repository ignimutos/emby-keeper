"""EmbyTransport 接口级测试: 用假 owner + 假会话验证传输逻辑.

缝的真实性: 生产用 curl_cffi, 测试用内存会话 (两个适配器). 无需 monkeypatch
传输内部.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from curl_cffi.requests import RequestsError

from embykeeper.emby.transport import EmbyTransport, _parse_json
from embykeeper.emby.errors import (
    EmbyConnectError,
    EmbyPlayError,
    EmbyStatusError,
)


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self):
        return {}


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return FakeResponse()


class RecordingLog:
    def info(self, message):
        pass

    def warning(self, message):
        pass

    def debug(self, message):
        pass


class FakeOwner:
    def __init__(self, url="https://example.com", session=None):
        self.a = SimpleNamespace(
            url=url,
            username="user",
            password="pass",
            use_proxy=False,
        )
        self.proxy = None
        self.verify = False
        self.useragent = None
        self.env = SimpleNamespace(
            client="Hills", device="D", device_id="ID", client_version="1.6.1", useragent="Hills/1.6.1"
        )
        self._token = None
        self._user_id = None
        self.hostname = "example.com"
        self.log = RecordingLog()
        self._session = session or FakeSession()
        self.login_calls = 0

    @property
    def token(self):
        return self._token

    @property
    def user_id(self):
        return self._user_id

    def build_headers(self):
        return {}

    def _get_session(self, **kwargs):
        return self._session

    async def login(self):
        self.login_calls += 1
        return True


@pytest.fixture
def frozen_sleep(monkeypatch):
    import embykeeper.emby.transport as transport

    monkeypatch.setattr(transport.asyncio, "sleep", AsyncMock())


def test_request_appends_api_base_url():
    session = FakeSession([FakeResponse(200)])
    owner = FakeOwner(url="https://example.com/myg", session=session)
    transport = EmbyTransport(owner)

    asyncio.run(transport._request("GET", "/Users/AuthenticateByName", _login=True))

    assert session.calls[0][1].endswith("/myg/emby/Users/AuthenticateByName")


def test_request_retries_on_502_then_succeeds(frozen_sleep):
    session = FakeSession([FakeResponse(502), FakeResponse(200)])
    owner = FakeOwner(session=session)
    transport = EmbyTransport(owner)

    asyncio.run(transport._request("GET", "/Users/AuthenticateByName", _login=True))

    assert len(session.calls) == 2


def test_request_relogs_on_401_then_succeeds(frozen_sleep):
    session = FakeSession([FakeResponse(401), FakeResponse(200)])
    owner = FakeOwner(session=session)
    transport = EmbyTransport(owner)

    asyncio.run(transport._request("GET", "/Users/AuthenticateByName"))

    assert owner.login_calls == 1
    assert len(session.calls) == 2


def test_request_raises_connect_error_after_retries(frozen_sleep):
    session = FakeSession([RequestsError("boom")] * 3)
    owner = FakeOwner(session=session)
    transport = EmbyTransport(owner)

    with pytest.raises(EmbyConnectError):
        asyncio.run(transport._request("GET", "/Users/AuthenticateByName", _login=True))


def test_request_no_relogin_loop_when_login_flag(frozen_sleep):
    """_login=True 时 401 不再触发重登, 直接返回."""
    session = FakeSession([FakeResponse(401), FakeResponse(200)])
    owner = FakeOwner(session=session)
    transport = EmbyTransport(owner)

    resp = asyncio.run(transport._request("GET", "/Users/AuthenticateByName", _login=True))

    assert owner.login_calls == 0
    assert resp.status_code == 401
    assert len(session.calls) == 1


def test_resolve_stream_url_strips_subpath():
    owner = FakeOwner(url="https://example.com/myg")
    transport = EmbyTransport(owner)

    url = transport._resolve_stream_url("/myg/videos/123/stream.mkv?Static=true")

    assert url == "https://example.com/myg/emby/videos/123/stream.mkv?Static=true"


def test_parse_json_rejects_non_json_body():
    """服务器返回非 JSON (网关/Cloudflare 页) 时应给出可读错误而非裸 JSONDecodeError."""

    class BadJsonResponse(FakeResponse):
        def json(self):
            raise json.JSONDecodeError("Expecting value", "<html>", 0)

    with pytest.raises(EmbyStatusError):
        _parse_json(BadJsonResponse(status_code=200, text="<html>error</html>"))


def test_parse_json_accepts_valid_body():
    class GoodJsonResponse(FakeResponse):
        def json(self):
            return {"ok": True}

    assert _parse_json(GoodJsonResponse(status_code=200)) == {"ok": True}


def test_format_connect_error_explains_sni():
    owner = FakeOwner()
    transport = EmbyTransport(owner)

    message = transport._format_connect_error(
        RequestsError("Failed: TLSV1_ALERT_UNRECOGNIZED_NAME.."),
        "https://bad-host.example.com",
    )

    assert "SNI" in message
    assert "bad-host.example.com" in message


# --- login() 全路径 ---


class FakeJsonResponse(FakeResponse):
    def __init__(self, status_code=200, payload=None):
        super().__init__(status_code=status_code)
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def test_login_success_caches_encrypted_credential(monkeypatch):
    import embykeeper.emby.transport as transport
    from embykeeper.crypto import decrypt_credential

    monkeypatch.setattr(transport, "cache", FakeCache())
    session = FakeSession([FakeJsonResponse(200, {"AccessToken": "tok", "User": {"Id": "uid"}})])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)

    token = asyncio.run(t.login())

    assert token == "tok"
    assert owner.token == "tok"
    assert owner.user_id == "uid"
    key = "emby.credential.example.com.user"
    assert decrypt_credential(transport.cache.store[key], "pass") == {
        "token": "tok",
        "userid": "uid",
    }


def test_login_returns_none_when_username_missing():
    owner = FakeOwner()
    owner.a.username = None
    t = EmbyTransport(owner)
    assert asyncio.run(t.login()) is None


def test_login_returns_none_on_401():
    session = FakeSession([FakeResponse(401)])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)
    assert asyncio.run(t.login()) is None


def test_login_returns_none_on_non_200():
    session = FakeSession([FakeResponse(500)])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)
    assert asyncio.run(t.login()) is None


# --- _request 失败分支 ---


def test_request_raises_status_error_on_cloudflare_page(frozen_sleep):
    session = FakeSession([FakeResponse(403, text="Just a moment...")])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)

    with pytest.raises(EmbyStatusError):
        asyncio.run(t._request("GET", "/x"))


def test_request_raises_status_error_on_non_ok_status(frozen_sleep):
    session = FakeSession([FakeResponse(404)])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)

    with pytest.raises(EmbyStatusError):
        asyncio.run(t._request("GET", "/x"))


def test_request_raises_connect_error_when_retries_exhausted_without_error(frozen_sleep):
    session = FakeSession([FakeResponse(502), FakeResponse(502), FakeResponse(502)])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)

    with pytest.raises(EmbyConnectError):
        asyncio.run(t._request("GET", "/x"))


def test_is_http2_flow_control_error():
    t = EmbyTransport(FakeOwner())
    assert t._is_http2_flow_control_error(RuntimeError("nghttp2_submit_window_update() failed"))
    assert t._is_http2_flow_control_error(RuntimeError("Flow control error: boom"))
    assert not t._is_http2_flow_control_error(RuntimeError("other"))


def test_stream_media_aborts_after_consecutive_errors(monkeypatch):
    import datetime as dt

    import embykeeper.emby.transport as transport

    class Resp:
        async def aiter_content(self, chunk_size=1024):
            raise RequestsError("boom")
            yield  # pragma: no cover

        async def aclose(self):
            pass

    async def fake_open(url, length, play_session_id):
        return Resp()

    owner = FakeOwner()
    owner._open_stream_with_fallback = fake_open
    state = {"n": 0}
    base = dt.datetime(2026, 1, 1, 0, 0, 0)

    class FakeDT:
        @classmethod
        def now(cls):
            state["n"] += 1
            return base + dt.timedelta(seconds=state["n"] * 6)

    monkeypatch.setattr(transport, "datetime", FakeDT)
    monkeypatch.setattr(transport.asyncio, "sleep", AsyncMock())
    t = EmbyTransport(owner)

    with pytest.raises(EmbyPlayError):
        asyncio.run(t._stream_media("/videos/1/stream", "ps"))


def test_stream_media_reraises_when_error_within_window(monkeypatch):
    import datetime as dt

    import embykeeper.emby.transport as transport

    class Resp:
        async def aiter_content(self, chunk_size=1024):
            raise RequestsError("boom")
            yield  # pragma: no cover

        async def aclose(self):
            pass

    calls = {"n": 0}

    async def fake_open(url, length, play_session_id):
        calls["n"] += 1
        return Resp()

    owner = FakeOwner()
    owner._open_stream_with_fallback = fake_open
    base = dt.datetime(2026, 1, 1, 0, 0, 0)

    class FakeDT:
        @classmethod
        def now(cls):
            return base  # 时间不变 -> 间隔 < 5s -> 直接重抛

    monkeypatch.setattr(transport, "datetime", FakeDT)
    monkeypatch.setattr(transport.asyncio, "sleep", AsyncMock())
    t = EmbyTransport(owner)

    with pytest.raises(RequestsError):
        asyncio.run(t._stream_media("/videos/1/stream", "ps"))
    assert calls["n"] == 1


def test_resolve_stream_url_returns_absolute_url():
    t = EmbyTransport(FakeOwner())
    assert t._resolve_stream_url("https://cdn.example.com/x.mkv") == "https://cdn.example.com/x.mkv"


def test_request_accepts_absolute_url(frozen_sleep):
    session = FakeSession([FakeResponse(200)])
    owner = FakeOwner(session=session)
    t = EmbyTransport(owner)

    asyncio.run(t._request("GET", "https://cdn.example.com/stream"))

    assert session.calls[0][1] == "https://cdn.example.com/stream"


def test_get_session_reused_across_requests(monkeypatch):
    import embykeeper.emby.transport as transport

    created = []

    class RecordingSession:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(transport, "AsyncSession", RecordingSession)
    owner = FakeOwner()
    t = transport.EmbyTransport(owner)

    s1 = t._get_session()
    s2 = t._get_session()

    assert s1 is s2
    assert len(created) == 1  # 只创建一次会话, 后续复用


def test_request_stream_uses_fresh_session(frozen_sleep, monkeypatch):
    import embykeeper.emby.transport as transport

    normal = FakeSession([FakeResponse(200)])
    stream = FakeSession([FakeResponse(200)])
    owner = FakeOwner(session=normal)
    t = transport.EmbyTransport(owner)
    created = []

    def fake_new_session(**kw):
        created.append(1)
        return stream

    monkeypatch.setattr(t, "_new_session", fake_new_session)

    asyncio.run(t._request("GET", "/x"))  # 普通请求复用会话
    asyncio.run(t._request("GET", "/videos/1/stream", stream=True))  # 流式独立会话

    assert normal.calls and stream.calls
    assert len(created) == 1


def test_request_raises_login_error_when_relogin_fails(frozen_sleep):
    from embykeeper.emby.errors import EmbyLoginError

    session = FakeSession([FakeResponse(401)])
    owner = FakeOwner(session=session)
    owner.login = AsyncMock(return_value=False)  # 重登失败
    t = EmbyTransport(owner)

    with pytest.raises(EmbyLoginError):
        asyncio.run(t._request("GET", "/x"))
