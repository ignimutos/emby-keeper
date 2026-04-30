import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from embykeeper.notify import (
    clear_instant_notification_window,
    set_instant_notification_window,
    should_notify_log,
    should_notify_msg,
)
from embykeeper.emby.main import EmbyManager
from embykeeper.emby.notification import (
    EmbyPlaybackSnapshot,
    EmbyWatchResult,
    format_watch_notification,
    has_userdata_update,
)
from embykeeper.schema import EmbyAccount


def make_record(level_no=20, **extra):
    return {"level": SimpleNamespace(no=level_no), "extra": extra}


def test_notify_filters_suppress_instant_window_when_once_disabled():
    set_instant_notification_window(True, allow=False)
    try:
        assert should_notify_log(make_record(log=True)) is False
        assert should_notify_msg(make_record(msg=True)) is False
        assert should_notify_log(make_record(level_no=40)) is False
    finally:
        clear_instant_notification_window()


def test_notify_filters_allow_instant_window_when_once_enabled():
    set_instant_notification_window(True, allow=True)
    try:
        assert should_notify_log(make_record(log=True)) is True
        assert should_notify_msg(make_record(msg=True)) is True
        assert should_notify_log(make_record(level_no=40)) is True
    finally:
        clear_instant_notification_window()


def test_format_watch_notification_success():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=True,
        failure_stage=None,
        item_name="片名",
        item_id="abc123",
        before=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
            play_count=11,
            playback_position_ticks=0,
            runtime_ticks=18900000000,
        ),
        after=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
            play_count=12,
            playback_position_ticks=18360000000,
            runtime_ticks=18900000000,
        ),
        next_time=datetime(2026, 5, 11, 11, 18),
    )

    assert format_watch_notification(result) == (
        "Emby保活成功｜premises@墨云阁｜片名\n\n"
        "视频ID: abc123\n"
        "Emby记录时间: 2026-04-29 15:08:12\n"
        "回写进度: 97% / 1836s\n"
        "播放次数: 12\n"
        "下次保活: 2026-05-11 11:18"
    )


def test_format_watch_notification_failure_uses_fallbacks():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=False,
        failure_stage="播放后校验未生效",
        item_name=None,
        item_id=None,
        before=EmbyPlaybackSnapshot(),
        after=EmbyPlaybackSnapshot(),
        next_time=None,
    )

    assert format_watch_notification(result) == (
        "Emby保活失败｜premises@墨云阁｜未获取\n\n"
        "视频ID: 未获取\n"
        "Emby记录时间: 未更新\n"
        "回写进度: 未更新\n"
        "播放次数: 未更新\n"
        "失败阶段: 播放后校验未生效\n"
        "下次保活: 未计划"
    )


def test_has_userdata_update_accepts_newer_emby_fields():
    before = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
        play_count=11,
        playback_position_ticks=0,
        runtime_ticks=18900000000,
    )
    after = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
        play_count=12,
        playback_position_ticks=18360000000,
        runtime_ticks=18900000000,
    )

    assert has_userdata_update(before, after) is True


def test_has_userdata_update_returns_false_without_any_advancement():
    before = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
        play_count=12,
        playback_position_ticks=18360000000,
    )
    after = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
        play_count=12,
        playback_position_ticks=18360000000,
    )

    assert has_userdata_update(before, after) is False


def test_has_userdata_update_accepts_play_count_only_update():
    before = EmbyPlaybackSnapshot(play_count=1)
    after = EmbyPlaybackSnapshot(play_count=2)

    assert has_userdata_update(before, after) is True


def test_has_userdata_update_accepts_playback_position_only_update():
    before = EmbyPlaybackSnapshot(playback_position_ticks=10)
    after = EmbyPlaybackSnapshot(playback_position_ticks=20)

    assert has_userdata_update(before, after) is True


def test_has_userdata_update_handles_mixed_naive_aware_datetimes():
    before = EmbyPlaybackSnapshot(last_played_date=datetime(2026, 4, 29, 15, 8, 12))
    after = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 23, 8, 12, tzinfo=timezone(timedelta(hours=8)))
    )

    assert has_userdata_update(before, after) is False


def test_format_watch_notification_progress_fallback_when_zero_ticks():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=True,
        item_name="片名",
        item_id="abc123",
        before=EmbyPlaybackSnapshot(),
        after=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
            play_count=12,
            playback_position_ticks=0,
            runtime_ticks=18900000000,
        ),
        next_time=datetime(2026, 5, 11, 11, 18),
    )

    assert "回写进度: 未更新" in format_watch_notification(result)


def test_format_watch_notification_progress_fallback_when_position_is_none():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=True,
        item_name="片名",
        item_id="abc123",
        before=EmbyPlaybackSnapshot(),
        after=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
            play_count=12,
            playback_position_ticks=None,
            runtime_ticks=18900000000,
        ),
        next_time=datetime(2026, 5, 11, 11, 18),
    )

    assert "回写进度: 未更新" in format_watch_notification(result)


def test_format_watch_notification_normalizes_aware_datetime_to_utc():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=True,
        item_name="片名",
        item_id="abc123",
        before=EmbyPlaybackSnapshot(),
        after=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 23, 8, 12, tzinfo=timezone(timedelta(hours=8))),
            play_count=12,
            playback_position_ticks=18360000000,
            runtime_ticks=18900000000,
        ),
        next_time=datetime(2026, 5, 11, 11, 18),
    )

    assert "Emby记录时间: 2026-04-29 15:08:12" in format_watch_notification(result)


class StubLogger:
    def __init__(self):
        self.bound_calls = []
        self.info_messages = []
        self.error_messages = []
        self.warning_messages = []
        self.debug_messages = []

    def bind(self, **kwargs):
        self.bound_calls.append(kwargs)
        return self

    def info(self, message):
        self.info_messages.append(message)

    def error(self, message):
        self.error_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)

    def debug(self, message):
        self.debug_messages.append(message)


class DummyRunContext:
    def start(self, *_args, **_kwargs):
        return None

    def finish(self, status, _status_info=None):
        return status


def test_get_next_watch_time_prefers_account_scheduler():
    manager = EmbyManager()
    account = EmbyAccount(url="https://example.com", username="user", password="pass", name="墨云阁")
    manager._schedulers[manager.get_spec(account)] = SimpleNamespace(next_time=datetime(2026, 5, 11, 11, 18))
    manager._schedulers["unified"] = SimpleNamespace(next_time=datetime(2026, 5, 12, 12, 0))

    assert manager._get_next_watch_time(account) == datetime(2026, 5, 11, 11, 18)


def test_get_next_watch_time_falls_back_to_unified_scheduler():
    manager = EmbyManager()
    account = EmbyAccount(url="https://example.com", username="user", password="pass", name="墨云阁")
    manager._schedulers["unified"] = SimpleNamespace(next_time=datetime(2026, 5, 12, 12, 0))

    assert manager._get_next_watch_time(account) == datetime(2026, 5, 12, 12, 0)


def test_schedule_messages_stay_out_of_notify_channel(monkeypatch):
    stub = StubLogger()
    monkeypatch.setattr("embykeeper.emby.main.logger", stub)
    monkeypatch.setattr(
        "embykeeper.emby.main.Scheduler.from_str",
        lambda *args, **kwargs: SimpleNamespace(on_next_time=kwargs["on_next_time"]),
    )

    manager = EmbyManager()
    account = EmbyAccount(
        url="https://example.com",
        username="user",
        password="pass",
        name="墨云阁",
        interval_days="1",
        time_range="8:00AM",
    )
    scheduler = manager.schedule_independent_account(account)

    scheduler.on_next_time(datetime(2026, 5, 11, 11, 18))

    assert stub.bound_calls == []
    assert stub.info_messages == ["下一次 Emby 账号 (user@墨云阁) 的保活将在 05-11 11:18 AM 进行."]


def test_watch_main_sends_one_notify_per_account(monkeypatch):
    stub = StubLogger()
    fake_config = SimpleNamespace(
        on_list_change=lambda *_args, **_kwargs: None,
        emby=SimpleNamespace(concurrency=2),
    )

    class FakeEmby:
        def __init__(self, account):
            self.account = account
            self.user_id = "uid"
            self.items = {}
            self.log = SimpleNamespace(
                info=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None
            )

        async def get_item(self, play_id):
            return {"Id": play_id, "Name": "片名"}

        async def watch(self):
            return EmbyWatchResult(
                account_spec=f"{self.account.username}@{self.account.name or self.account.url.host}",
                success=True,
                item_name="片名",
                item_id=self.account.play_id,
            )

    monkeypatch.setattr("embykeeper.emby.main.logger", stub)
    monkeypatch.setattr("embykeeper.emby.main.config", fake_config)
    monkeypatch.setattr("embykeeper.emby.main.Emby", FakeEmby)
    monkeypatch.setattr(
        "embykeeper.emby.main.RunContext", SimpleNamespace(prepare=lambda **_kwargs: DummyRunContext())
    )
    monkeypatch.setattr("embykeeper.emby.main.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("embykeeper.emby.main.random.uniform", lambda *_args: 0)

    manager = EmbyManager()
    manager._schedulers["unified"] = SimpleNamespace(next_time=datetime(2026, 5, 11, 11, 18))
    accounts = [
        EmbyAccount(url="https://example.com", username="user1", password="pass", name="甲", play_id="a1"),
        EmbyAccount(url="https://example.com", username="user2", password="pass", name="乙", play_id="b2"),
    ]

    asyncio.run(manager._watch_main(accounts, instant=True))

    notify_messages = {message for message in stub.info_messages if message.startswith("Emby保活成功｜")}
    assert len(stub.bound_calls) == 2
    assert notify_messages == {
        "Emby保活成功｜user1@甲｜片名\n\n视频ID: a1\nEmby记录时间: 未更新\n回写进度: 未更新\n播放次数: 未更新\n下次保活: 2026-05-11 11:18",
        "Emby保活成功｜user2@乙｜片名\n\n视频ID: b2\nEmby记录时间: 未更新\n回写进度: 未更新\n播放次数: 未更新\n下次保活: 2026-05-11 11:18",
    }
