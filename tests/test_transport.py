"""EmbyTransport 接口级测试: 用假 owner + 假会话验证传输逻辑.

缝的真实性: 生产用 curl_cffi, 测试用内存会话 (两个适配器). 无需 monkeypatch
传输内部.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from curl_cffi.requests import RequestsError

from embykeeper.emby.transport import EmbyTransport
from embykeeper.emby.errors import EmbyConnectError


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
            cf_challenge=False,
            use_proxy=False,
        )
        self.proxy = None
        self.cf_clearance = None
        self.useragent = None
        self.env = SimpleNamespace(
            client="Hills", device="D", device_id="ID", client_version="1.6.1", useragent="Hills/1.6.1"
        )
        self.token = None
        self.hostname = "example.com"
        self.log = RecordingLog()
        self._session = session or FakeSession()
        self.login_calls = 0

    def _get_session(self, **kwargs):
        return self._session

    async def login(self):
        self.login_calls += 1
        return True

    async def use_cfsolver(self):
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


def test_format_connect_error_explains_sni():
    owner = FakeOwner()
    transport = EmbyTransport(owner)

    message = transport._format_connect_error(
        RequestsError("Failed: TLSV1_ALERT_UNRECOGNIZED_NAME.."),
        "https://bad-host.example.com",
    )

    assert "SNI" in message
    assert "bad-host.example.com" in message
