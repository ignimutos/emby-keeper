"""PlaybackSession 接口级测试: 用脚本化 _request 的假 client 验证完整会话.

深化后的测试面: 不再 monkeypatch 传输内部, 直接对会话接口喂请求序列.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from embykeeper.emby.playback import PlaybackSession
from embykeeper.emby.errors import (
    EmbyPlayError,
    EmbyStatusError,
    EmbyStoppedReportError,
    EmbyStreamRejectedError,
)


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

    def done(self):
        return True

    def cancelled(self):
        return True

    def exception(self):
        return None

    def __await__(self):
        async def _cancelled():
            raise asyncio.CancelledError

        return _cancelled().__await__()


class FakePlaybackClient:
    """实现 PlaybackClient 协约的最小假实现."""

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

    async def _stream_media(self, url, play_session_id, holder=None):
        self.requests.append(("stream", url, play_session_id))

    def stop_stream(self, holder):
        self.requests.append(("stop_stream", None, None))

    async def await_stream_stop(self, task, timeout=5.0):
        self.requests.append(("await_stream_stop", None, None))

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

    paths = [req[0] + " " + req[1] for req in client.requests if isinstance(req[1], str)]
    assert "POST /Items/123/PlaybackInfo" in paths
    assert "POST /Sessions/Playing" in paths
    assert "POST /Sessions/Playing/Stopped" in paths
    assert "POST /Sessions/Playing/Progress" in paths

    playing = next(req for req in client.requests if req[1] == "/Sessions/Playing")
    assert playing[2]["MediaSourceId"] == "media-source-id"
    assert playing[2]["AudioStreamIndex"] == 2
    assert playing[2]["SubtitleStreamIndex"] == -1  # DefaultSubtitleStreamIndex=None -> -1


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


def test_session_runs_with_int_item_id(frozen_playback_random):
    client = FakePlaybackClient()
    assert run(client, 123, time=10) is True


def test_session_raises_play_error_when_start_progress_fails(frozen_playback_random):
    from embykeeper.emby.errors import EmbyStatusError

    class FailingStartClient(FakePlaybackClient):
        async def _request(self, method, path, _session_kwargs=None, **kwargs):
            if path == "/Sessions/Playing/Progress":
                raise EmbyStatusError("500")
            return await super()._request(method, path, _session_kwargs=_session_kwargs, **kwargs)

    with pytest.raises(EmbyPlayError) as exc_info:
        run(FailingStartClient(), ITEM)
    assert "无法开始播放" in str(exc_info.value)


def test_session_raises_when_too_many_progress_errors(frozen_playback_random):
    from embykeeper.emby.errors import EmbyStatusError

    class LoopProgressFailingClient(FakePlaybackClient):
        async def _request(self, method, path, _session_kwargs=None, **kwargs):
            payload = kwargs.get("json") or {}
            if (
                path == "/Sessions/Playing/Progress"
                and payload.get("EventName") == "TimeUpdate"
                and payload.get("PositionTicks") != 5400000000  # 循环中的进度上报
            ):
                raise EmbyStatusError("500")
            return await super()._request(method, path, _session_kwargs=_session_kwargs, **kwargs)

    with pytest.raises(EmbyPlayError) as exc_info:
        run(LoopProgressFailingClient(), ITEM, time=200)
    assert "播放状态设定连续错误次数过多" in str(exc_info.value)


def test_session_sends_playing_before_progress(frozen_playback_random):
    client = FakePlaybackClient()
    run(client, ITEM)
    playing = next(i for i, req in enumerate(client.requests) if req[1] == "/Sessions/Playing")
    progress = next(
        i
        for i, req in enumerate(client.requests)
        if req[1] == "/Sessions/Playing/Progress" and (req[2] or {}).get("EventName") == "TimeUpdate"
    )
    assert playing < progress


def test_session_stops_stream_without_cancel(frozen_playback_random, monkeypatch):
    import embykeeper.emby.playback as playback

    calls = []

    class StreamTask:
        def cancel(self):
            calls.append("cancel")

        def done(self):
            return True

        def cancelled(self):
            return True

        def exception(self):
            return None

    monkeypatch.setattr(playback.asyncio, "create_task", lambda coro: coro.close() or StreamTask())
    client = FakePlaybackClient()
    assert run(client, ITEM) is True

    assert "cancel" not in calls  # curl_cffi 流任务不可 cancel
    assert any(req[0] == "stop_stream" for req in client.requests)
    assert any(req[0] == "await_stream_stop" for req in client.requests)


def test_session_warns_when_stream_task_raises(frozen_playback_random, monkeypatch):
    import embykeeper.emby.playback as playback

    class BoomTask:
        def cancel(self):
            pass

        def done(self):
            return True

        def cancelled(self):
            return False

        def exception(self):
            return RuntimeError("boom")

    monkeypatch.setattr(playback.asyncio, "create_task", lambda coro: coro.close() or BoomTask())
    client = FakePlaybackClient()
    assert run(client, ITEM) is True


def test_session_raises_stream_rejected_when_grant_denied(frozen_playback_random, monkeypatch):
    import embykeeper.emby.playback as playback
    from embykeeper.emby.errors import EmbyStatusError, EmbyStreamRejectedError

    class RejectedTask:
        def cancel(self):
            pass

        def done(self):
            return True

        def cancelled(self):
            return False

        def exception(self):
            return EmbyStatusError(
                "访问失败: 服务器返回 HTTP 403: grant_scope_mismatch", error_code="grant_scope_mismatch"
            )

    monkeypatch.setattr(playback.asyncio, "create_task", lambda coro: coro.close() or RejectedTask())
    client = FakePlaybackClient()

    with pytest.raises(EmbyStreamRejectedError):
        run(client, ITEM)


# --- 租约中途失效 (409 playback_lease_inactive) 的分类与分层恢复 ---

START_TICK = ITEM["UserData"]["PlaybackPositionTicks"]  # 5400000000, 用于区分初始与循环上报


class LeaseRejectingClient(FakePlaybackClient):
    """让循环中的进度上报返回租约失效 (409 playback_lease_inactive).

    loop_rejections: 恢复触发前需被拒的循环上报次数; None 表示未恢复前始终拒绝.
    recover_on: "playing" -> 重发 /Sessions/Playing 即恢复; "ping" -> 需 /Sessions/Playing/Ping;
                None -> 永不恢复.
    统计 playing_posts / ping_posts 以断言恢复机制.
    """

    def __init__(self, loop_rejections, recover_on, **kwargs):
        super().__init__(**kwargs)
        self.loop_rejections = loop_rejections
        self.recover_on = recover_on
        self.loop_seen = 0
        self.playing_posts = 0
        self.ping_posts = 0
        self._recovered = False

    @staticmethod
    def _lease_error():
        return EmbyStatusError(
            "访问失败: 异常 HTTP 代码 409: playback_lease_inactive",
            error_code="playback_lease_inactive",
        )

    @staticmethod
    def _is_loop_progress(payload):
        return payload.get("EventName") == "TimeUpdate" and payload.get("PositionTicks") != START_TICK

    async def _request(self, method, path, _session_kwargs=None, **kwargs):
        payload = kwargs.get("json") or {}
        if method == "POST" and path == "/Sessions/Playing":
            self.playing_posts += 1
            if self.recover_on == "playing" and self.playing_posts > 1:  # 第 1 次为初始, 重发才算恢复
                self._recovered = True
        if method == "POST" and path == "/Sessions/Playing/Ping":
            self.ping_posts += 1
            if self.recover_on == "ping":
                self._recovered = True
        if (
            method == "POST"
            and path == "/Sessions/Playing/Progress"
            and self._is_loop_progress(payload)
            and not self._recovered
        ):
            self.loop_seen += 1
            if self.loop_rejections is None or self.loop_seen <= self.loop_rejections:
                raise self._lease_error()
        return await super()._request(method, path, _session_kwargs=_session_kwargs, **kwargs)


def test_session_recovers_lease_by_reasserting_start(frozen_playback_random):
    # 前 2 次循环上报被拒 -> 触发恢复; 机制 (a) 重发 PlaybackStart 后探测即成功.
    client = LeaseRejectingClient(loop_rejections=2, recover_on="playing")
    assert run(client, ITEM, time=200) is True
    assert client.playing_posts == 2  # 初始 + 恢复时重发一次
    assert client.ping_posts == 0  # (a) 已足够, 无需 Ping


def test_session_recovers_lease_by_ping_fallback(frozen_playback_random):
    # 重发 PlaybackStart 后探测仍被拒, 回退到 Ping 才恢复.
    client = LeaseRejectingClient(loop_rejections=None, recover_on="ping")
    assert run(client, ITEM, time=200) is True
    assert client.ping_posts == 1
    assert client.playing_posts == 2  # 初始 + 恢复重发


def test_session_raises_stream_rejected_when_lease_never_recovers(frozen_playback_random):
    client = LeaseRejectingClient(loop_rejections=None, recover_on=None)
    with pytest.raises(EmbyStreamRejectedError) as exc_info:
        run(client, ITEM, time=200)
    assert "playback_lease_inactive" in str(exc_info.value)
    assert client.playing_posts <= 2  # 初始 + 至多一次恢复重发
    assert client.ping_posts <= 1  # 恢复有界


def test_session_tolerates_single_transient_lease_rejection(frozen_playback_random):
    # 仅 1 次循环上报被拒 (未达触发阈值), 不触发恢复, 会话照常成功.
    client = LeaseRejectingClient(loop_rejections=1, recover_on="playing")
    assert run(client, ITEM, time=200) is True
    assert client.playing_posts == 1  # 未发生恢复
    assert client.ping_posts == 0


def test_session_final_pause_lease_inactive_raises_stream_rejected(frozen_playback_random):
    class FinalPauseLeaseClient(FakePlaybackClient):
        async def _request(self, method, path, _session_kwargs=None, **kwargs):
            payload = kwargs.get("json") or {}
            if (
                path == "/Sessions/Playing/Progress"
                and payload.get("EventName") == "Pause"
                and payload.get("PositionTicks") != START_TICK  # 收尾 Pause, 非初始 Pause
            ):
                raise EmbyStatusError(
                    "访问失败: 异常 HTTP 代码 409: playback_lease_inactive",
                    error_code="playback_lease_inactive",
                )
            return await super()._request(method, path, _session_kwargs=_session_kwargs, **kwargs)

    with pytest.raises(EmbyStreamRejectedError):
        run(FinalPauseLeaseClient(), ITEM)
