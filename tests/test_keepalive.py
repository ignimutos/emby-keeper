"""KeepaliveRun 接口级测试: 通过假 EmbyClient 注入, 直接验证决策树.

这是深化后的测试面: 不再需要 monkeypatch 传输层, 喂快照即可判定成功与否.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from embykeeper.emby.keepalive import KeepaliveRun
from embykeeper.emby.errors import EmbyPlayError, EmbyStoppedReportError
from embykeeper.emby.notification import EmbyWatchResult


class RecordingLog:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.debugs = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def debug(self, message):
        self.debugs.append(message)


def make_account(**overrides):
    fields = dict(
        username="user",
        password="pass",
        name=None,
        url=SimpleNamespace(host="example.com"),
        allow_stream=False,
        allow_multiple=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeClient:
    """实现 EmbyClient 协约的最小假实现."""

    def __init__(
        self,
        *,
        account,
        items,
        watch_time=60,
        get_item_side_effect=None,
        play_side_effect=None,
        resume_items=None,
    ):
        self.a = account
        self.items = items
        self._watch_time = watch_time
        self._get_item = AsyncMock(side_effect=get_item_side_effect)
        self._play = AsyncMock(side_effect=play_side_effect or (lambda *a, **k: True))
        self._resume_items = resume_items or []
        self.log = RecordingLog()

    def _configured_watch_time(self):
        return self._watch_time

    async def get_item(self, iid, **kw):
        return await self._get_item(iid)

    async def get_resume_item(self, iid):
        return next((item for item in self._resume_items if item.get("Id") == iid), None)

    async def play(self, item, time):
        return await self._play(item, time=time)


ITEM = {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}


def before_item():
    return {**ITEM, "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0}}


def after_item():
    return {
        **ITEM,
        "UserData": {
            "LastPlayedDate": "2026-04-29T15:08:12Z",
            "PlayCount": 12,
            "PlaybackPositionTicks": 18360000000,
        },
    }


@pytest.fixture
def frozen_random(monkeypatch):
    import embykeeper.emby.keepalive as keepalive

    monkeypatch.setattr(keepalive.random, "shuffle", lambda _items: None)
    monkeypatch.setattr(keepalive.random, "uniform", lambda *args: 0)
    monkeypatch.setattr(keepalive.random, "random", lambda: 0)
    monkeypatch.setattr(keepalive.asyncio, "sleep", AsyncMock())


def sync(awaitable):
    return asyncio.run(awaitable)


def test_run_success_when_userdata_changes(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        get_item_side_effect=[before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is True
    assert result.after.play_count == 12


def test_run_success_via_resume_fallback(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        get_item_side_effect=[before_item(), before_item()],  # 直接回写未变
        resume_items=[after_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True
    assert result.after.playback_position_ticks == 18360000000


def test_run_warns_when_stopped_report_fails_but_userdata_changed(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        play_side_effect=EmbyStoppedReportError("停止上报失败: boom"),
        get_item_side_effect=[before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True
    assert result.failure_stage is None
    assert "Stopped 上报失败" in result.warning


def test_run_fails_when_userdata_stays_stale(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        get_item_side_effect=[before_item(), before_item()],
        resume_items=[],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is False
    assert result.failure_stage == "播放后校验未生效"


def test_run_fails_when_baseline_unreadable(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        get_item_side_effect=RuntimeError("boom"),
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is False
    assert result.failure_stage == "结果读取失败"


def test_run_fails_when_no_playable_items(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"bad": {"Id": "bad", "Name": "坏片", "MediaType": "Audio"}},
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is False
    assert result.failure_stage == "获取视频失败"


def test_run_fails_when_time_config_invalid(frozen_random, monkeypatch):
    import embykeeper.emby.keepalive as keepalive

    # 空 dict 配置: uniform(*{}) 无参数调用应抛 TypeError 触发配置错误分支
    def strict_uniform(*args):
        if not args:
            raise TypeError("uniform expected at least 1 argument")
        return 0

    monkeypatch.setattr(keepalive.random, "uniform", strict_uniform)

    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        watch_time={},  # 不可解析的 time 配置
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is False
    assert result.failure_stage == "配置错误"


def test_run_fails_when_retries_exhausted(frozen_random):
    def boom(*args, **kwargs):
        raise EmbyPlayError("boom")

    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        play_side_effect=boom,
        get_item_side_effect=[before_item()],
    )
    result = sync(KeepaliveRun(client=client, max_retries=0).run())

    assert result.success is False
    assert result.failure_stage == "播放中断"


def test_run_allow_stream_synthesizes_runtime(frozen_random, monkeypatch):
    import embykeeper.emby.keepalive as keepalive

    monkeypatch.setattr(keepalive.random, "randint", lambda a, b: 600)
    client = FakeClient(
        account=make_account(allow_stream=True),
        items={"abc123": {"Id": "abc123", "Name": "片", "MediaType": "Video", "RunTimeTicks": None}},
        get_item_side_effect=[before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True


def test_run_allow_multiple_plays_second_video(frozen_random, monkeypatch):
    import embykeeper.emby.keepalive as keepalive

    monkeypatch.setattr(keepalive.random, "randint", lambda a, b: 0)
    item_short = {"Id": "s1", "Name": "短", "MediaType": "Video", "RunTimeTicks": 600000000}
    item_long = {"Id": "l1", "Name": "长", "MediaType": "Video", "RunTimeTicks": 1200000000}

    def before_after_gen():
        while True:
            yield before_item()
            yield after_item()

    client = FakeClient(
        account=make_account(allow_multiple=True),
        items={"s1": item_short, "l1": item_long},
        watch_time=120,
        get_item_side_effect=before_after_gen(),
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True
    assert client._play.await_count == 2


def test_run_retries_then_succeeds(frozen_random):
    def play_fail_once(*args, **kwargs):
        if not getattr(play_fail_once, "called", False):
            play_fail_once.called = True
            raise EmbyPlayError("boom")
        return True

    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        play_side_effect=play_fail_once,
        get_item_side_effect=[before_item(), before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client, max_retries=2).run())

    assert result.success is True
    assert client._play.await_count == 2


def test_run_retries_connect_error(frozen_random):
    from embykeeper.emby.errors import EmbyConnectError

    def fail_once(*args, **kwargs):
        if not getattr(fail_once, "called", False):
            fail_once.called = True
            raise EmbyConnectError("conn")
        return True

    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        play_side_effect=fail_once,
        get_item_side_effect=[before_item(), before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client, max_retries=2).run())

    assert result.success is True
    assert client._play.await_count == 2


def test_run_success_when_play_count_missing(frozen_random):
    after = {
        **ITEM,
        "UserData": {
            "LastPlayedDate": "2026-04-29T15:08:12Z",
            "PlaybackPositionTicks": 18360000000,
        },
    }
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        get_item_side_effect=[before_item(), after],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True


def test_run_multiple_loops_when_requirement_not_met(frozen_random, monkeypatch):
    import embykeeper.emby.keepalive as keepalive

    monkeypatch.setattr(keepalive.random, "randint", lambda a, b: 0)
    item_short = {"Id": "s1", "Name": "短", "MediaType": "Video", "RunTimeTicks": 600000000}

    def before_after_gen():
        while True:
            yield before_item()
            yield after_item()

    client = FakeClient(
        account=make_account(allow_multiple=True),
        items={"s1": item_short},
        watch_time=120,
        get_item_side_effect=before_after_gen(),
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True
    assert client._play.await_count == 2


def test_run_skips_wrong_media_type_and_succeeds(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={
            "audio": {"Id": "audio", "Name": "音频", "MediaType": "Audio"},
            "video": ITEM,
        },
        get_item_side_effect=[before_item(), after_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is True


def test_parse_date_malformed():
    assert KeepaliveRun.parse_date("not-a-date") is None
    assert KeepaliveRun.parse_date(None) is None
    assert KeepaliveRun.parse_date("2026-04-29T15:08:12Z") is not None


def test_run_reports_generic_error_in_play(frozen_random):
    client = FakeClient(
        account=make_account(),
        items={"abc123": ITEM},
        play_side_effect=RuntimeError("boom"),
        get_item_side_effect=[before_item()],
    )
    result = sync(KeepaliveRun(client=client).run())

    assert result.success is False
    assert result.failure_stage == "播放中断"
