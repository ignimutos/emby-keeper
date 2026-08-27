import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import embykeeper.emby.main as emby_main
from embykeeper.runinfo import RunStatus


def make_account(name: str, *, enabled: bool = True, time_range=None, interval_days=None):
    return SimpleNamespace(
        name=name,
        enabled=enabled,
        time_range=time_range,
        interval_days=interval_days,
    )


class FakeRunContext:
    instances = []

    def __init__(self, description=None, parent_ids=None):
        self.description = description
        self.finish_status = None
        FakeRunContext.instances.append(self)

    @classmethod
    def prepare(cls, description=None, parent_ids=None):
        return cls(description=description, parent_ids=parent_ids)

    def start(self, status=None):
        pass

    def finish(self, status=None, status_info=None):
        self.finish_status = status
        return self


def make_watch_account(name="alpha", enabled=True, play_id=None, username="user"):
    return SimpleNamespace(
        name=name,
        enabled=enabled,
        play_id=play_id,
        username=username,
        url=SimpleNamespace(host="example.com"),
    )


def patch_watch_runtime(monkeypatch, emby_cls, accounts=None):
    FakeRunContext.instances = []
    monkeypatch.setattr(emby_main, "RunContext", FakeRunContext)
    monkeypatch.setattr(
        emby_main, "config", SimpleNamespace(emby=SimpleNamespace(concurrency=1, account=accounts or []))
    )
    monkeypatch.setattr(emby_main, "format_watch_notification", lambda result: "")
    monkeypatch.setattr(emby_main, "Emby", emby_cls)


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

    # 调度骨架的日志在 scheduled_manager 模块内
    import embykeeper.scheduled_manager as scheduled_manager

    monkeypatch.setattr(scheduled_manager, "logger", SimpleNamespace(info=fake_info))

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


# --- play_url ---


def test_play_url_rejects_url_without_id(monkeypatch):
    monkeypatch.setattr(emby_main, "config", SimpleNamespace(emby=SimpleNamespace(account=[])))
    manager = emby_main.EmbyManager()
    assert asyncio.run(manager.play_url("https://example.com/web/#/details")) is False


def test_play_url_rejects_unknown_server(monkeypatch):
    account = make_watch_account("alpha")
    account.url = SimpleNamespace(host="other.com")
    monkeypatch.setattr(emby_main, "config", SimpleNamespace(emby=SimpleNamespace(account=[account])))
    manager = emby_main.EmbyManager()
    assert asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1")) is False


def test_play_url_success(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            return True

        def build_headers(self):
            return {"X-Emby-Token": "secret"}  # 非空, 触发打印循环

        async def get_item(self, iid):
            return {"Id": iid, "Name": "片"}

        async def play(self, item, time=10):
            return True

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.SUCCESS


def test_play_url_reports_missing_item(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            return True

        def build_headers(self):
            return {}

        async def get_item(self, iid):
            return None

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.ERROR


def test_play_url_reports_play_failure(monkeypatch):
    from embykeeper.emby.errors import EmbyPlayError

    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            return True

        def build_headers(self):
            return {}

        async def get_item(self, iid):
            return {"Id": iid, "Name": "片"}

        async def play(self, item, time=10):
            raise EmbyPlayError("播放失败")

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_play_url_reports_request_error(monkeypatch):
    from embykeeper.emby.errors import EmbyRequestError

    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            raise EmbyRequestError("req")

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_play_url_reports_connect_error_with_proxy(monkeypatch):
    from embykeeper.emby.errors import EmbyConnectError

    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = "socks5://proxy"

        async def login(self):
            raise EmbyConnectError("conn")

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_reports_loaded_item_count(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = "uid"
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return True

        async def load_main_page(self):
            self.items["i1"] = {"Id": "i1"}

        async def watch(self):
            return SimpleNamespace(success=True)

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.SUCCESS


def test_watch_main_play_id_login_failure(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = None
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return False

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account(play_id="pid")])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account(play_id="pid")]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_reports_watch_generic_error(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = "uid"
            self.items = {"i1": {}}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return True

        async def load_main_page(self):
            pass

        async def watch(self):
            raise RuntimeError("boom")

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_reports_multiple_failures(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = None
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return False

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account("a"), make_watch_account("b")])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    accounts = [make_watch_account("a"), make_watch_account("b")]
    asyncio.run(manager._watch_main(accounts))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_get_next_watch_time_returns_none_without_scheduler(monkeypatch):
    patch_watch_runtime(monkeypatch, object, accounts=[])
    manager = emby_main.EmbyManager()
    assert manager._get_next_watch_time(make_watch_account()) is None


def test_play_url_reports_login_failure(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            return False

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


# --- _watch_main 失败分支 ---


def test_watch_main_no_enabled_accounts(monkeypatch):
    patch_watch_runtime(monkeypatch, object, accounts=[make_watch_account(enabled=False)])
    manager = emby_main.EmbyManager()
    asyncio.run(manager._watch_main([make_watch_account(enabled=False)]))
    assert FakeRunContext.instances[-1].finish_status == RunStatus.SUCCESS


def test_watch_main_reports_login_failure(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = None
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return False

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_reports_init_failure(monkeypatch):
    class BoomEmby:
        def __init__(self, account):
            raise RuntimeError("init boom")

    patch_watch_runtime(monkeypatch, BoomEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_plays_specified_play_id(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = "uid"
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return True

        async def get_item(self, iid):
            return {"Id": iid, "Name": "片"}

        async def load_main_page(self):
            pass

        async def watch(self):
            return SimpleNamespace(success=True)

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account(play_id="pid")])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account(play_id="pid")]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.SUCCESS


def test_watch_main_reports_emby_error(monkeypatch):
    from embykeeper.emby.errors import EmbyError

    class FakeEmby:
        def __init__(self, account):
            self.user_id = None
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            raise EmbyError("boom")

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_empty_accounts(monkeypatch):
    patch_watch_runtime(monkeypatch, object, accounts=[])
    manager = emby_main.EmbyManager()
    assert asyncio.run(manager._watch_main([])) is None


def test_watch_main_reports_no_items(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = "uid"
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def login(self):
            return True

        async def load_main_page(self):
            pass

        async def watch(self):
            return SimpleNamespace(success=True)

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account()])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account()]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_watch_main_play_id_missing_item(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.user_id = "uid"
            self.items = {}
            self.log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

        async def get_item(self, iid):
            return {}  # 无 Id

        async def watch(self):
            return SimpleNamespace(success=True)

    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[make_watch_account(play_id="pid")])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    manager._get_next_watch_time = lambda account: None

    asyncio.run(manager._watch_main([make_watch_account(play_id="pid")]))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_play_url_reports_connect_error(monkeypatch):
    from embykeeper.emby.errors import EmbyConnectError

    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            raise EmbyConnectError("conn")

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.FAIL


def test_play_url_reports_generic_error(monkeypatch):
    class FakeEmby:
        def __init__(self, account):
            self.log = SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
            self.proxy = None

        async def login(self):
            raise ValueError("boom")

    account = make_watch_account("alpha")
    patch_watch_runtime(monkeypatch, FakeEmby, accounts=[account])
    monkeypatch.setattr(emby_main.asyncio, "sleep", AsyncMock())

    manager = emby_main.EmbyManager()
    asyncio.run(manager.play_url("https://example.com/web/#/details?id=abc&serverId=1"))

    assert FakeRunContext.instances[-1].finish_status == RunStatus.ERROR
