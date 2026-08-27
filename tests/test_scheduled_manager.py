"""ScheduledKeepaliveManager 基类 + SubsonicManager 修复的测试."""

import asyncio
from types import SimpleNamespace

import pytest

import embykeeper.scheduled_manager as scheduled_manager
from embykeeper.subsonic.main import SubsonicManager
from embykeeper.emby.main import EmbyManager


def make_account(section="subsonic", *, interval_days=None, time_range=None, enabled=True, name="svc"):
    return SimpleNamespace(
        username="user",
        name=name,
        url=SimpleNamespace(host="example.com"),
        enabled=enabled,
        interval_days=interval_days,
        time_range=time_range,
    )


def fake_config(monkeypatch, *, subsonic_interval="9", emby_interval="3"):
    monkeypatch.setattr(
        scheduled_manager,
        "config",
        SimpleNamespace(
            on_list_change=lambda *_a, **_k: None,
            emby=SimpleNamespace(
                account=[],
                interval_days=emby_interval,
                time_range="<11:00AM,11:00PM>",
                concurrency=1,
            ),
            subsonic=SimpleNamespace(
                account=[],
                interval_days=subsonic_interval,
                time_range="<11:00AM,11:00PM>",
                concurrency=1,
            ),
        ),
    )


def patch_scheduler(monkeypatch):
    calls = {}

    def fake_from_str(*args, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(on_next_time=lambda t: None, schedule=lambda: None)

    monkeypatch.setattr(scheduled_manager, "Scheduler", SimpleNamespace(from_str=fake_from_str))
    return calls


def test_subsonic_independent_run_calls_listen_main(monkeypatch):
    """回归: 独立账号调度曾调用不存在的 _watch_main (AttributeError)."""
    fake_config(monkeypatch)
    manager = SubsonicManager()
    captured = {}

    async def fake_listen(accounts, instant=False):
        captured["accounts"] = list(accounts)
        return "ok"

    monkeypatch.setattr(manager, "_listen_main", fake_listen)

    account = make_account()
    result = asyncio.run(manager.run_accounts([account], False))

    assert captured["accounts"] == [account]
    assert result == "ok"


def test_subsonic_scheduler_uses_subsonic_config_section(monkeypatch):
    """回归: 独立账号曾读取 config.emby.* (配置泄漏)."""
    fake_config(monkeypatch, subsonic_interval="9")
    calls = patch_scheduler(monkeypatch)

    manager = SubsonicManager()
    account = make_account(interval_days=None, time_range=None)
    manager.schedule_independent_account(account)

    assert calls["interval_days"] == "9"
    assert calls["time_range"] == "<11:00AM,11:00PM>"


def test_emby_scheduler_uses_emby_config_section(monkeypatch):
    fake_config(monkeypatch, emby_interval="3")
    calls = patch_scheduler(monkeypatch)

    manager = EmbyManager()
    account = make_account(section="emby", interval_days=None, time_range=None)
    manager.schedule_independent_account(account)

    assert calls["interval_days"] == "3"


def test_independent_account_uses_own_values_without_touching_config(monkeypatch):
    """账号自带 time_range/interval 时不应读取全局配置 (短路语义)."""
    fake_config(monkeypatch)
    calls = patch_scheduler(monkeypatch)

    manager = SubsonicManager()
    account = make_account(interval_days="5", time_range="1:00PM")
    manager.schedule_independent_account(account)

    assert calls["interval_days"] == "5"
    assert calls["time_range"] == "1:00PM"


def test_handle_account_change_schedules_new_independent_account(monkeypatch):
    """配置变更时新增的独立账号会被直接调度."""
    fake_config(monkeypatch)
    manager = SubsonicManager()
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

    account = make_account(section="emby", name="svc", interval_days="5", time_range="1:00PM")
    manager._handle_account_change([account], [account])

    assert calls == []
