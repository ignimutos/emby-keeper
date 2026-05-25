import asyncio
from types import SimpleNamespace

import embykeeper.emby.main as emby_main


def make_account(name: str, *, enabled: bool = True, time_range=None, interval_days=None):
    return SimpleNamespace(
        name=name,
        enabled=enabled,
        time_range=time_range,
        interval_days=interval_days,
    )


def test_run_accounts_passes_selected_accounts_to_watch_main(monkeypatch):
    monkeypatch.setattr(
        emby_main,
        "config",
        SimpleNamespace(
            on_list_change=lambda *args, **kwargs: None,
            emby=SimpleNamespace(
                account=[],
                time_range="<11:00AM,11:00PM>",
                interval_days="<7,12>",
                concurrency=1,
            ),
        ),
    )

    manager = emby_main.EmbyManager()
    accounts = [make_account("alpha"), make_account("beta")]

    captured = {}

    async def fake_watch_main(selected_accounts, instant):
        captured["names"] = [account.name for account in selected_accounts]
        captured["instant"] = instant

    monkeypatch.setattr(manager, "_watch_main", fake_watch_main)

    asyncio.run(manager.run_accounts(accounts, instant=True))

    assert captured["names"] == ["alpha", "beta"]
    assert captured["instant"] is True


def test_schedule_accounts_uses_only_selected_accounts(monkeypatch):
    monkeypatch.setattr(
        emby_main,
        "config",
        SimpleNamespace(
            on_list_change=lambda *args, **kwargs: None,
            emby=SimpleNamespace(
                account=[],
                time_range="<11:00AM,11:00PM>",
                interval_days="<7,12>",
                concurrency=1,
            ),
        ),
    )

    manager = emby_main.EmbyManager()

    accounts = [
        make_account("unified"),
        make_account("independent", time_range="<1:00PM,2:00PM>"),
        make_account("disabled", enabled=False, time_range="<3:00PM,4:00PM>"),
    ]

    calls = []

    def fake_schedule_unified_accounts(selected_accounts=None):
        names = [account.name for account in (selected_accounts or [])]
        calls.append(("unified", names))
        manager._schedulers["unified"] = object()

    def fake_schedule_independent_account(account):
        calls.append(("independent", account.name))
        return None

    pool_state = {"wait_called": 0}

    async def fake_wait():
        pool_state["wait_called"] += 1
        calls.append(("wait", None))

    manager._pool = SimpleNamespace(add=lambda *_args, **_kwargs: None, wait=fake_wait)
    monkeypatch.setattr(manager, "schedule_unified_accounts", fake_schedule_unified_accounts)
    monkeypatch.setattr(manager, "schedule_independent_account", fake_schedule_independent_account)

    asyncio.run(manager.schedule_accounts(accounts))

    assert ("unified", ["unified", "independent", "disabled"]) in calls
    assert ("independent", "independent") in calls
    assert ("independent", "disabled") not in calls
    assert pool_state["wait_called"] == 1


def test_handle_account_change_ignores_unselected_accounts_when_subset_active(monkeypatch):
    monkeypatch.setattr(
        emby_main,
        "config",
        SimpleNamespace(
            on_list_change=lambda *args, **kwargs: None,
            emby=SimpleNamespace(
                account=[],
                time_range="<11:00AM,11:00PM>",
                interval_days="<7,12>",
                concurrency=1,
            ),
        ),
    )

    manager = emby_main.EmbyManager()
    manager._selected_account_names = {"alpha"}

    calls = []
    beta = make_account("beta", time_range="<1:00PM,2:00PM>")

    monkeypatch.setattr(manager, "stop_account", lambda spec: calls.append(("stop_account", spec)))
    monkeypatch.setattr(
        manager, "stop_unified_accounts", lambda: calls.append(("stop_unified_accounts", None))
    )
    monkeypatch.setattr(
        manager, "schedule_unified_accounts", lambda: calls.append(("schedule_unified_accounts", None))
    )
    monkeypatch.setattr(
        manager,
        "schedule_independent_account",
        lambda account: calls.append(("schedule_independent_account", account.name)),
    )

    manager._handle_account_change([beta], [beta])

    assert calls == []


def test_schedule_accounts_returns_none_when_no_schedulers(monkeypatch):
    monkeypatch.setattr(
        emby_main,
        "config",
        SimpleNamespace(
            on_list_change=lambda *args, **kwargs: None,
            emby=SimpleNamespace(
                account=[],
                time_range="<11:00AM,11:00PM>",
                interval_days="<7,12>",
                concurrency=1,
            ),
        ),
    )

    manager = emby_main.EmbyManager()
    accounts = [make_account("disabled", enabled=False, time_range="<3:00PM,4:00PM>")]

    pool_state = {"wait_called": 0}

    async def fake_wait():
        pool_state["wait_called"] += 1

    manager._pool = SimpleNamespace(add=lambda *_args, **_kwargs: None, wait=fake_wait)

    messages = []

    def fake_info(message):
        messages.append(message)

    monkeypatch.setattr(emby_main, "logger", SimpleNamespace(info=fake_info))

    result = asyncio.run(manager.schedule_accounts(accounts))

    assert result is None
    assert any("没有需要执行的 Emby 保活任务" in message for message in messages)
    assert pool_state["wait_called"] == 0


def test_schedule_all_uses_config_accounts_and_clears_subset_scope(monkeypatch):
    accounts = [make_account("alpha"), make_account("beta")]
    monkeypatch.setattr(
        emby_main,
        "config",
        SimpleNamespace(
            on_list_change=lambda *args, **kwargs: None,
            emby=SimpleNamespace(
                account=accounts,
                time_range="<11:00AM,11:00PM>",
                interval_days="<7,12>",
                concurrency=1,
            ),
        ),
    )

    manager = emby_main.EmbyManager()
    manager._selected_account_names = {"stale"}
    captured = {"names": None, "scope": None}
    sentinel = object()

    async def fake_schedule_accounts(selected_accounts):
        captured["names"] = [account.name for account in selected_accounts]
        captured["scope"] = manager._selected_account_names
        return sentinel

    monkeypatch.setattr(manager, "_schedule_accounts", fake_schedule_accounts)

    result = asyncio.run(manager.schedule_all())

    assert captured["names"] == ["alpha", "beta"]
    assert captured["scope"] is None
    assert result is sentinel
