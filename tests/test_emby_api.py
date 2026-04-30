import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from curl_cffi import CurlHttpVersion
from curl_cffi.requests import RequestsError

import embykeeper.emby.api as emby_api_module
from embykeeper.emby.api import Emby, EmbyConnectError, EmbyPlayError
from embykeeper.emby.notification import EmbyWatchResult
from embykeeper.schema import EmbyAccount


class FakeResponse:
    status_code = 200
    ok = True
    text = ""

    def json(self):
        return {}


class FakeJsonResponse(FakeResponse):
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.requested_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.requested_url = url
        return FakeResponse()


class FakeStreamResponse(FakeResponse):
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    async def aiter_content(self, chunk_size=1024):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def patch_cache(monkeypatch, store=None):
    store = dict(store or {})

    class FakeCache:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    monkeypatch.setattr(emby_api_module, "cache", FakeCache())
    return store


def test_request_appends_emby_api_base_to_public_account_url():
    account = EmbyAccount(url="https://example.com/myg", username="user", password="pass")
    client = Emby(account)
    session = FakeSession()
    client._get_session = lambda: session

    asyncio.run(client._request("GET", "/Users/AuthenticateByName", _login=True))

    assert session.requested_url.endswith("/myg/emby/Users/AuthenticateByName")


def test_request_keeps_existing_emby_api_base_in_account_url():
    account = EmbyAccount(url="https://example.com/myg/emby", username="user", password="pass")
    client = Emby(account)
    session = FakeSession()
    client._get_session = lambda: session

    asyncio.run(client._request("GET", "/Users/AuthenticateByName", _login=True))

    assert session.requested_url.endswith("/myg/emby/Users/AuthenticateByName")


def test_request_passes_http_version_override_to_session():
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)
    recorded = {}

    class RecordingSession(FakeSession):
        async def request(self, method, url, **kwargs):
            recorded["request_kwargs"] = kwargs
            return await super().request(method, url, **kwargs)

    def build_session(**session_kwargs):
        recorded["session_kwargs"] = session_kwargs
        return RecordingSession()

    client._get_session = build_session

    asyncio.run(
        client._request(
            "GET",
            "/Users/AuthenticateByName",
            _login=True,
            _session_kwargs={"http_version": CurlHttpVersion.V1_1},
        )
    )

    assert recorded["session_kwargs"]["http_version"] == CurlHttpVersion.V1_1


def test_format_connect_error_explains_unrecognized_name():
    account = EmbyAccount(url="https://bad-host.example.com", username="user", password="pass")
    client = Emby(account)

    message = client._format_connect_error(
        RequestsError(
            "Failed to perform, curl: (35) TLS connect error: error:10000458:SSL routines:OPENSSL_internal:TLSV1_ALERT_UNRECOGNIZED_NAME.."
        ),
        "https://bad-host.example.com",
    )

    assert "SNI" in message
    assert "证书" in message
    assert "bad-host.example.com" in message


def test_get_fake_env_defaults_to_hills_android(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)
    patch_cache(monkeypatch)

    env = client.get_fake_env()

    assert env.client == "Hills"
    assert env.device == "PLC110"
    assert env.client_version == "1.6.1"
    assert env.useragent == "Hills/1.6.1 (android; 15)"
    assert len(env.device_id) == 16


def test_env_rebuilds_when_cached_default_client_is_legacy_fileball(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)
    patch_cache(
        monkeypatch,
        {
            "emby.env.example.com.user": {
                "client": "Fileball",
                "device": "Mock Device",
                "device_id": "device-id",
                "client_version": "1.3.24",
                "useragent": "Fileball/1.3.24",
            }
        },
    )

    env = client.env

    assert env.client == "Hills"
    assert env.device == "PLC110"
    assert env.client_version == "1.6.1"
    assert env.useragent == "Hills/1.6.1 (android; 15)"


def test_open_stream_uses_http11_hills_android_headers(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)
    client._env = SimpleNamespace(
        client="Hills",
        device="PLC110",
        device_id="dc92b1ddef2981c4",
        client_version="1.6.1",
        useragent="Hills/1.6.1 (android; 15)",
    )
    calls = []

    async def fake_request(method, path, _session_kwargs=None, **kwargs):
        calls.append(_session_kwargs)
        return FakeResponse()

    monkeypatch.setattr(client, "_request", fake_request)

    response = asyncio.run(client._open_stream_with_fallback("/Videos/abc/stream", 0, "play-session"))

    assert response.ok is True
    assert calls == [
        {
            "headers": {
                "User-Agent": "Hills/1.6.1 (android; 15)",
                "Accept": "*/*",
                "Range": "bytes=0-",
            },
            "http_version": CurlHttpVersion.V1_1,
        }
    ]


def test_stream_media_stops_after_clean_eof(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)
    response = FakeStreamResponse([b"a" * 1024, b"b" * 104])
    calls = []

    async def fake_open(url, length, play_session_id):
        calls.append((url, length, play_session_id))
        return response

    monkeypatch.setattr(client, "_open_stream_with_fallback", fake_open)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0.5)

    asyncio.run(client._stream_media("/Videos/abc/stream", "play-session"))

    assert calls == [("/Videos/abc/stream", 0, "play-session")]
    assert response.closed is True


def test_resolve_stream_url_uses_emby_api_base_for_root_video_paths():
    account = EmbyAccount(url="https://example.com/myg", username="user", password="pass")
    client = Emby(account)

    url = client._resolve_stream_url("/videos/123/stream.mkv?Static=true")

    assert url == "https://example.com/myg/emby/videos/123/stream.mkv?Static=true"


def test_resolve_stream_url_preserves_server_relative_subpaths_under_emby_api_base():
    account = EmbyAccount(url="https://example.com/myg", username="user", password="pass")
    client = Emby(account)

    url = client._resolve_stream_url("/myg/videos/123/stream.mkv?Static=true")

    assert url == "https://example.com/myg/emby/myg/videos/123/stream.mkv?Static=true"


def test_resolve_stream_url_does_not_duplicate_existing_emby_prefix():
    account = EmbyAccount(url="https://example.com", username="user", password="pass")
    client = Emby(account)

    url = client._resolve_stream_url("/emby/videos/123/stream.mkv?Static=true")

    assert url == "https://example.com/emby/videos/123/stream.mkv?Static=true"


def test_play_uses_single_hills_android_playback_info_request(monkeypatch):
    account = EmbyAccount(url="https://example.com/myg", username="user", password="pass")
    client = Emby(account)
    client._user_id = "user-id"
    client._token = "token"
    client._env = SimpleNamespace(
        client="Hills",
        device="PLC110",
        device_id="dc92b1ddef2981c4",
        client_version="1.6.1",
        useragent="Hills/1.6.1 (android; 15)",
    )
    calls = []

    class DummyTask:
        def cancel(self):
            pass

        def __await__(self):
            async def _cancelled():
                raise asyncio.CancelledError

            return _cancelled().__await__()

    def fake_create_task(coro):
        coro.close()
        return DummyTask()

    async def fake_request(method, path, _session_kwargs=None, **kwargs):
        calls.append(
            {
                "method": method,
                "path": path,
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
                "headers": kwargs.get("headers"),
            }
        )
        if path.endswith("/AdditionalParts"):
            return FakeJsonResponse({"Items": []})
        if path.endswith("/PlaybackInfo"):
            return FakeJsonResponse(
                {
                    "PlaySessionId": "play-session-id",
                    "MediaSources": [
                        {
                            "Id": "media-source-id",
                            "DirectStreamUrl": "/myg/videos/123/stream.mkv?Static=true",
                        }
                    ],
                }
            )
        return FakeJsonResponse({})

    def fake_uniform(a, b):
        if (a, b) == (0.95, 1.0):
            return 0.95
        return 0

    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("embykeeper.emby.api.asyncio.create_task", fake_create_task)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", fake_uniform)
    monkeypatch.setattr(client, "_request", fake_request)

    item = {
        "Id": "123",
        "Name": "片名",
        "UserData": {"PlaybackPositionTicks": 5400000000},
    }
    assert asyncio.run(client.play(item, time=10)) is True

    playback_info_calls = [call for call in calls if call["path"].endswith("/PlaybackInfo")]
    assert len(playback_info_calls) == 1
    assert playback_info_calls[0]["params"] == {
        "UserId": "user-id",
        "IsPlayback": "true",
        "X-Emby-Authorization": 'Emby Client="Hills", Device="PLC110", DeviceId="dc92b1ddef2981c4", Version="1.6.1"',
        "X-Emby-Client": "Hills",
        "X-Emby-Device-Name": "PLC110",
        "X-Emby-Device-Id": "dc92b1ddef2981c4",
        "X-Emby-Client-Version": "1.6.1",
        "X-Emby-Language": "zh-cn",
        "X-Emby-Token": "token",
    }
    profile = playback_info_calls[0]["json"]["DeviceProfile"]
    assert "CodecProfiles" not in profile
    assert profile["MaxStaticBitrate"] == 200000000
    assert profile["MaxStreamingBitrate"] == 200000000
    assert profile["DirectPlayProfiles"] == [{"Type": "Video"}, {"Type": "Audio"}]

    session_params = {
        "reqformat": "json",
        "UserId": "user-id",
        "X-Emby-Authorization": 'Emby Client="Hills", Device="PLC110", DeviceId="dc92b1ddef2981c4", Version="1.6.1"',
        "X-Emby-Client": "Hills",
        "X-Emby-Device-Name": "PLC110",
        "X-Emby-Device-Id": "dc92b1ddef2981c4",
        "X-Emby-Client-Version": "1.6.1",
        "X-Emby-Language": "zh-cn",
        "X-Emby-Token": "token",
    }
    playing_call = next(call for call in calls if call["path"] == "/Sessions/Playing")
    assert playing_call["params"] == session_params
    assert playing_call["headers"] == {"Content-Type": "text/plain"}
    assert playing_call["json"]["PositionTicks"] == 5400000000
    assert playing_call["json"]["AudioStreamIndex"] == 0

    progress_calls = [call for call in calls if call["path"] == "/Sessions/Playing/Progress"]
    assert [call["json"]["EventName"] for call in progress_calls] == [
        "TimeUpdate",
        "Pause",
        "Unpause",
        "TimeUpdate",
        "Pause",
    ]
    assert [call["json"]["PositionTicks"] for call in progress_calls] == [
        5400000000,
        5400000000,
        5400000000,
        5500000000,
        5500000000,
    ]
    assert all(call["params"] == session_params for call in progress_calls)
    assert all(call["headers"] == {"Content-Type": "text/plain"} for call in progress_calls)
    assert all(call["json"]["AudioStreamIndex"] == 0 for call in progress_calls)

    stopped_call = next(call for call in calls if call["path"] == "/Sessions/Playing/Stopped")
    assert stopped_call["params"] == session_params
    assert stopped_call["headers"] == {"Content-Type": "text/plain"}
    assert stopped_call["json"]["PositionTicks"] == 5500000000
    assert stopped_call["json"]["AudioStreamIndex"] == 0


def test_watch_returns_success_result_when_userdata_changes(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())

    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {
                    "LastPlayedDate": "2026-04-29T15:08:12Z",
                    "PlayCount": 12,
                    "PlaybackPositionTicks": 18360000000,
                },
            },
        ]
    )
    client.get_item = AsyncMock(side_effect=lambda _iid: next(responses))

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is True
    assert result.item_id == "abc123"
    assert result.item_name == "片名"
    assert result.before.play_count == 11
    assert result.after.play_count == 12
    assert result.after.last_played_date == datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc)
    assert result.after.playback_position_ticks == 18360000000
    assert result.after.runtime_ticks == 18900000000


def test_watch_returns_success_result_when_resume_updates_before_item_details(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())

    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
        ]
    )
    client.get_item = AsyncMock(side_effect=lambda _iid: next(responses))
    client.get_resume_items = AsyncMock(
        return_value={
            "Items": [
                {
                    "Id": "abc123",
                    "Name": "片名",
                    "RunTimeTicks": 18900000000,
                    "UserData": {
                        "PlayCount": 11,
                        "PlaybackPositionTicks": 18360000000,
                        "LastPlayedDate": "2026-04-29T15:08:12Z",
                    },
                }
            ]
        }
    )

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is True
    assert result.failure_stage is None
    assert result.after.last_played_date == datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc)
    assert result.after.playback_position_ticks == 18360000000


def test_watch_returns_failed_result_when_userdata_stays_stale(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())

    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
        ]
    )
    client.get_item = AsyncMock(side_effect=lambda _iid: next(responses))
    client.get_resume_items = AsyncMock(return_value={"Items": []})

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "播放后校验未生效"
    assert result.item_id == "abc123"
    assert result.item_name == "片名"


def test_watch_returns_failed_result_when_baseline_item_cannot_be_read(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())

    client.play = AsyncMock(return_value=True)
    client.get_item = AsyncMock(side_effect=RuntimeError("boom"))

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "结果读取失败"
    assert result.item_id == "abc123"
    assert result.item_name == "片名"


def test_watch_returns_failed_result_when_latest_item_cannot_be_read(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())

    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            RuntimeError("boom"),
        ]
    )

    def get_item(_iid):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    client.get_item = AsyncMock(side_effect=get_item)

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "结果读取失败"
    assert result.item_id == "abc123"
    assert result.item_name == "片名"
    assert result.before.play_count == 11


def test_watch_returns_failed_result_when_retry_is_exhausted(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "embykeeper.emby.api.config",
        SimpleNamespace(emby=SimpleNamespace(retries=0)),
    )

    client.play = AsyncMock(side_effect=[EmbyPlayError("boom")])
    client.get_item = AsyncMock(
        return_value={
            "Id": "abc123",
            "Name": "片名",
            "RunTimeTicks": 18900000000,
            "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
        }
    )

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "播放中断"
    assert result.item_id == "abc123"
    assert result.item_name == "片名"


def test_watch_returns_failed_result_when_no_playable_items_exist(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {"bad": {"Id": "bad", "Name": "坏片", "MediaType": "Audio"}}

    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda _items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *_args: 0)

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "获取视频失败"
    assert result.item_id is None
    assert result.item_name is None


def test_watch_returns_failed_result_when_time_config_is_invalid():
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.a.time = {}

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is False
    assert result.failure_stage == "配置错误"
