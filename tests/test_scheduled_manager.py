"""ScheduledKeepaliveManager 基类 + EmbyManager 调度的测试."""

import asyncio
from types import SimpleNamespace

import pytest

import embykeeper.scheduled_manager as scheduled_manager
from embykeeper.emby.main import EmbyManager


def make_account(*, interval_days=None, time_range=None, enabled=True, name="svc"):
    return SimpleNamespace(
        username="user",
        name=name,
        url=SimpleNamespace(host="example.com"),
        enabled=enabled,
        interval_days=interval_days,
        time_range=time_range,
    )


def fake_config(monkeypatch, *, emby_interval="3"):
    import embykeeper.schedule as schedule_module

    config = SimpleNamespace(
        on_list_change=lambda *_a, **_k: None,
        debug_cron=False,
        emby=SimpleNamespace(
            account=[],
            interval_days=emby_interval,
            time_range="<11:00AM,11:00PM>",
            concurrency=1,
        ),
    )
    monkeypatch.setattr(scheduled_manager, "config", config)
    monkeypatch.setattr(schedule_module, "config", config)  # 真实 Scheduler 读 schedule.config


def patch_scheduler(monkeypatch):
    calls = {}

    def fake_from_str(*args, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(on_next_time=lambda t: None, schedule=lambda: None)

    monkeypatch.setattr(scheduled_manager, "Scheduler", SimpleNamespace(from_str=fake_from_str))
    return calls


def test_emby_scheduler_uses_emby_config_section(monkeypatch):
    fake_config(monkeypatch, emby_interval="3")
    calls = patch_scheduler(monkeypatch)

    manager = EmbyManager()
    account = make_account(interval_days=None, time_range=None)
    manager.schedule_independent_account(account)

    assert calls["interval_days"] == "3"


def test_independent_account_uses_own_values_without_touching_config(monkeypatch):
    """账号自带 time_range/interval 时不应读取全局配置 (短路语义)."""
    fake_config(monkeypatch)
    calls = patch_scheduler(monkeypatch)

    manager = EmbyManager()
    account = make_account(interval_days="5", time_range="1:00PM")
    manager.schedule_independent_account(account)

    assert calls["interval_days"] == "5"
    assert calls["time_range"] == "1:00PM"


def test_handle_account_change_schedules_new_independent_account(monkeypatch):
    """配置变更时新增的独立账号会被直接调度."""
    fake_config(monkeypatch)
    manager = EmbyManager()
    pool_adds = []

    monkeypatch.setattr(manager, "_pool", SimpleNamespace(add=lambda _c: pool_adds.append(1)))
    monkeypatch.setattr(
        manager, "schedule_independent_account", lambda account: SimpleNamespace(schedule=lambda: None)
    )

    account = make_account(interval_days="5", time_range="1:00PM")
    manager._handle_account_change([account], [])

    assert len(pool_adds) == 1


def test_handle_account_change_ignores_unselected(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    manager._selected_account_names = {"other"}

    calls = []
    monkeypatch.setattr(manager, "stop_account", lambda spec: calls.append(("stop", spec)))
    monkeypatch.setattr(manager, "schedule_independent_account", lambda a: calls.append(("add", a.name)))

    account = make_account(name="svc", interval_days="5", time_range="1:00PM")
    manager._handle_account_change([account], [account])

    assert calls == []


def test_schedule_unified_accounts_creates_scheduler(monkeypatch):
    fake_config(monkeypatch)
    calls = patch_scheduler(monkeypatch)
    manager = EmbyManager()
    account = make_account()  # 无独立配置 -> 统一调度
    pool_adds = []
    manager._pool = SimpleNamespace(add=lambda c: pool_adds.append(1))
    manager.schedule_unified_accounts([account])
    assert calls["interval_days"] == "3"
    assert len(pool_adds) == 1


def test_schedule_unified_accounts_returns_none_when_no_accounts(monkeypatch):
    fake_config(monkeypatch)
    patch_scheduler(monkeypatch)
    manager = EmbyManager()
    assert manager.schedule_unified_accounts([]) is None


def test_stop_account_removes_scheduler_and_cancels_task(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    account = make_account()
    spec = manager.get_spec(account)
    manager._schedulers[spec] = object()
    cancelled = []
    manager._tasks[spec] = SimpleNamespace(cancel=lambda: cancelled.append(spec))
    manager._running.add(spec)

    manager.stop_account(spec)

    assert spec not in manager._schedulers
    assert spec not in manager._tasks
    assert spec not in manager._running
    assert cancelled == [spec]


def test_stop_unified_accounts(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    manager._schedulers["unified"] = object()
    cancelled = []
    manager._tasks["unified"] = SimpleNamespace(cancel=lambda: cancelled.append("unified"))

    manager.stop_unified_accounts()

    assert "unified" not in manager._schedulers
    assert "unified" not in manager._tasks
    assert cancelled == ["unified"]


def test_handle_account_change_reschedules_unified_on_removed(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    calls = []
    monkeypatch.setattr(manager, "stop_unified_accounts", lambda: calls.append("stop"))
    monkeypatch.setattr(manager, "schedule_unified_accounts", lambda accounts=None: calls.append("schedule"))

    removed = make_account()  # 统一账号被移除 -> 重新调度
    manager._handle_account_change([], [removed])

    assert calls == ["stop", "schedule"]


def test_is_account_selected_default_true(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    assert manager._is_account_selected(make_account()) is True


def test_schedule_independent_account_none_when_disabled(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    assert manager.schedule_independent_account(make_account(enabled=False)) is None


def test_run_all_delegates_to_run_accounts(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    captured = []

    async def fake_run_accounts(accounts, instant):
        captured.append((accounts, instant))

    monkeypatch.setattr(manager, "run_accounts", fake_run_accounts)
    asyncio.run(manager.run_all(instant=True))
    assert captured


def test_handle_account_change_removed_independent(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    calls = []
    removed = make_account(interval_days="5", time_range="1:00PM")  # 独立账号
    monkeypatch.setattr(manager, "stop_account", lambda spec: calls.append(("stop", spec)))

    manager._handle_account_change([], [removed])

    assert calls == [("stop", manager.get_spec(removed))]


def test_handle_account_change_added_unified(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    calls = []
    added = make_account()  # 统一账号
    monkeypatch.setattr(manager, "stop_unified_accounts", lambda: calls.append("stop"))
    monkeypatch.setattr(manager, "schedule_unified_accounts", lambda accounts=None: calls.append("schedule"))

    manager._handle_account_change([added], [])

    assert calls == ["stop", "schedule"]


def _fake_pool():
    def add(coro):
        if hasattr(coro, "close"):  # 关闭未 await 的 schedule() 协程, 避免 RuntimeWarning
            coro.close()

    return SimpleNamespace(add=add)


def test_schedule_independent_account_func_creates_task(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    manager._pool = _fake_pool()
    account = make_account(interval_days="5", time_range="1:00PM")
    scheduler = manager.schedule_independent_account(account)

    ran = []

    async def fake_run_accounts(accounts, instant):
        ran.append(1)

    monkeypatch.setattr(manager, "run_accounts", fake_run_accounts)

    async def main():
        await scheduler.func(None)  # func 返回一个 task, 需在 loop 内 await

    asyncio.run(main())
    assert ran == [1]


def test_schedule_accounts_adds_independent_scheduler(monkeypatch):
    fake_config(monkeypatch)
    manager = EmbyManager()
    manager._pool = _fake_pool()
    manager.schedule_unified_accounts = lambda accounts=None: None
    monkeypatch.setattr(
        manager,
        "schedule_independent_account",
        lambda account: SimpleNamespace(schedule=lambda: None),
    )
    account = make_account(interval_days="5", time_range="1:00PM")
    asyncio.run(manager._schedule_accounts([account]))


def test_schedule_unified_func_and_on_next_time(monkeypatch):
    from datetime import datetime, timedelta

    fake_config(monkeypatch)
    manager = EmbyManager()
    manager._pool = _fake_pool()
    account = make_account()
    manager.schedule_unified_accounts([account])
    scheduler = manager._schedulers["unified"]  # schedule_unified_accounts 无返回值

    # on_next_time 回调
    scheduler.on_next_time(datetime.now() + timedelta(days=1))

    ran = []

    async def fake_run_accounts(accounts, instant):
        ran.append(1)

    monkeypatch.setattr(manager, "run_accounts", fake_run_accounts)

    async def main():
        await scheduler.func(None)

    asyncio.run(main())
    assert ran == [1]
