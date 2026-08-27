"""PlaybackSession 接口级测试: 用脚本化 _request 的假 client 验证完整会话.

深化后的测试面: 不再 monkeypatch 传输内部, 直接对会话接口喂请求序列.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from embykeeper.emby.playback import PlaybackSession
from embykeeper.emby.errors import EmbyPlayError, EmbyStoppedReportError


class RecordingLog:
    def __init__(self):
        self.infos = []

    def info(self, message):
        self.infos.append(message)

    def debug(self, message):
        pass

    def warning(self, message):
        pass


class DummyTask:
    def cancel(self):
        pass

    def __await__(self):
        async def _cancelled():
            raise asyncio.CancelledError

        return _cancelled().__await__()


class FakePlaybackClient:
    """实现 PlaybackClient 协约的最小假实现."""

    playing_count = 0  # 与 Emby 相同的类级并发仪表

    def __init__(self, playback_info=None, stopped_error=None, resolved_url="https://cdn.example.com/stream"):
        self.user_id = "user-id"
        self.token = "token"
        self.useragent = "Hills/1.6.1 (android; 15)"
        self.env = SimpleNamespace(
            client="Hills",
            device="Test Device",
            device_id="0123456789abcdef",
            client_version="1.6.1",
        )
        self.log = RecordingLog()
        self.playback_info = playback_info or {
            "PlaySessionId": "play-session-id",
            "MediaSources": [
                {
                    "Id": "media-source-id",
                    "DirectStreamUrl": "/myg/videos/123/stream.mkv?Static=true",
                    "DefaultAudioStreamIndex": 2,
                    "DefaultSubtitleStreamIndex": None,
                }
            ],
        }
        self.stopped_error = stopped_error
        self.resolved_url = resolved_url
        self.requests = []

    def _resolve_stream_url(self, url):
        self.requests.append(("resolve_stream", url))
        return self.resolved_url

    async def _stream_media(self, url, play_session_id):
        self.requests.append(("stream", url, play_session_id))

    async def _request(self, method, path, _session_kwargs=None, **kwargs):
        self.requests.append((method, path, kwargs.get("json")))
        if path.endswith("/AdditionalParts"):
            return SimpleNamespace(json=lambda: {"Items": []})
        if path.endswith("/PlaybackInfo"):
            return SimpleNamespace(json=lambda: self.playback_info)
        if path == "/Sessions/Playing/Stopped" and self.stopped_error:
            raise self.stopped_error
        return SimpleNamespace(json=lambda: {})


@pytest.fixture
def frozen_playback_random(monkeypatch):
    import embykeeper.emby.playback as playback

    def fake_create_task(coro):
        coro.close()
        return DummyTask()

    monkeypatch.setattr(playback.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(playback.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(playback.asyncio, "wait_for", lambda coro, timeout: coro)
    monkeypatch.setattr(playback.random, "uniform", lambda *a: 0)


def run(client, item, time=10):
    return asyncio.run(PlaybackSession(client=client, item=item, time=time).run())


ITEM = {"Id": "123", "Name": "片名", "UserData": {"PlaybackPositionTicks": 5400000000}}


def test_session_runs_full_playback_sequence(frozen_playback_random):
    client = FakePlaybackClient()
    assert run(client, ITEM) is True

    paths = [req[0] + " " + req[1] for req in client.requests]
    assert "POST /Items/123/PlaybackInfo" in paths
    assert "POST /Sessions/Playing" in paths
    assert "POST /Sessions/Playing/Stopped" in paths
    assert "POST /Sessions/Playing/Progress" in paths

    playing = next(req for req in client.requests if req[1] == "/Sessions/Playing")
    assert playing[2]["MediaSourceId"] == "media-source-id"
    assert playing[2]["AudioStreamIndex"] == 2
    assert playing[2]["SubtitleStreamIndex"] == -1  # DefaultSubtitleStreamIndex=None -> -1

    # 类级并发仪表在会话结束后归零
    assert FakePlaybackClient.playing_count == 0


def test_session_falls_back_to_random_media_source_id(frozen_playback_random):
    client = FakePlaybackClient(playback_info={"PlaySessionId": "ps", "MediaSources": []})
    assert run(client, ITEM) is True

    stopped = next(req for req in client.requests if req[1] == "/Sessions/Playing/Stopped")
    assert stopped[2]["MediaSourceId"]
    assert len(stopped[2]["MediaSourceId"]) == 32


def test_session_resolves_stream_url(frozen_playback_random):
    client = FakePlaybackClient()
    run(client, ITEM)
    assert ("resolve_stream", "/myg/videos/123/stream.mkv?Static=true") in client.requests


def test_session_raises_play_error_when_item_lacks_id(frozen_playback_random):
    client = FakePlaybackClient()
    with pytest.raises(EmbyPlayError):
        run(client, {"Name": "无名"})
    assert not any(req[1].startswith("/Items/") for req in client.requests)


def test_session_raises_stopped_error_when_stopped_report_fails(frozen_playback_random):
    from embykeeper.emby.errors import EmbyStatusError

    client = FakePlaybackClient(stopped_error=EmbyStatusError("500"))
    with pytest.raises(EmbyStoppedReportError):
        run(client, ITEM)
