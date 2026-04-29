import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from embykeeper.emby.api import Emby, EmbyPlayError
from embykeeper.emby.notification import EmbyWatchResult
from embykeeper.schema import EmbyAccount


class FakeResponse:
    status_code = 200
    ok = True
    text = ""


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


def test_request_preserves_base_path_in_account_url():
    account = EmbyAccount(url="https://example.com/emby", username="user", password="pass")
    client = Emby(account)
    session = FakeSession()
    client._get_session = lambda: session

    asyncio.run(client._request("GET", "/Users/AuthenticateByName", _login=True))

    assert session.requested_url.endswith("/emby/Users/AuthenticateByName")


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
