import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace
import warnings
from typer.testing import CliRunner

import pytest

import embykeeper
import embykeeper.cli as cli
from embykeeper.cli import app
from embykeeper.config import ConfigManager

runner = CliRunner()


@pytest.fixture()
def in_temp_dir(tmp_path: Path):
    current = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(current)


def make_fake_emby_account(name, enabled=True, time_range=None, interval_days=None):
    return SimpleNamespace(
        name=name,
        enabled=enabled,
        time_range=time_range,
        interval_days=interval_days,
    )


def patch_cli_runtime(monkeypatch, accounts):
    async def fake_reload_conf(_config_file):
        cli.config.emby = SimpleNamespace(account=accounts)
        cli.config.mongodb = None
        cli.config.nofail = True
        return True

    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(
            reload_conf=fake_reload_conf,
            on_change=lambda *args, **kwargs: None,
            basedir=None,
            windows=False,
            public=False,
            mongodb=None,
            nofail=True,
            noexit=False,
            notifier=None,
            proxy=None,
            emby=SimpleNamespace(account=[]),
        ),
    )
    monkeypatch.setattr(
        "embykeeper.cache.cache",
        SimpleNamespace(
            set=lambda *a, **k: None,
            get=lambda *a, **k: "test",
            delete=lambda *a, **k: None,
        ),
    )


def test_version():
    result = runner.invoke(app, ["--version"])
    assert embykeeper.__version__ in result.stdout
    assert result.exit_code == 0


def test_create_config(in_temp_dir: Path):
    result = runner.invoke(app, ["--example-config"])
    assert "这是一个配置文件范例" in result.stdout
    assert result.exit_code == 0


def test_create_config_after_asyncio_run(in_temp_dir: Path):
    asyncio.run(asyncio.sleep(0))

    result = runner.invoke(app, ["--example-config"])

    assert "这是一个配置文件范例" in result.stdout
    assert result.exit_code == 0


def test_generate_example_config_avoids_event_loop_deprecation():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        ConfigManager.generate_example_config()

    loop_warnings = [
        warning
        for warning in caught
        if isinstance(warning.message, DeprecationWarning)
        and "There is no current event loop" in str(warning.message)
    ]

    assert loop_warnings == []


def test_create_config_includes_global_emby_fingerprint(in_temp_dir: Path):
    result = runner.invoke(app, ["--example-config"])
    emby_section = result.stdout.split("[[emby.account]]", 1)[0]

    assert "time = [300, 600]" in emby_section
    assert 'client = "Hills"' in emby_section
    assert "自动生成" in emby_section  # device/device_id 不再内置固定值
    assert 'client_version = "1.6.1"' in emby_section
    assert 'useragent = "Hills/1.6.1 (android; 15)"' in emby_section
    # 示例不应包含生效的固定 device/device_id (每台安装自动生成)
    active_device = [
        line for line in result.stdout.splitlines() if re.match(r"^\s*(device|device_id)\s*=", line)
    ]
    assert active_device == []
    assert result.exit_code == 0


def test_notifier_policy_starts_for_one_shot_instant_when_notifier_once_enabled(monkeypatch):
    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(notifier=SimpleNamespace(enabled=True, once=True), noexit=False),
    )

    assert cli._notifier_should_start(instant=True, once=True) is True
    assert cli._instant_notifications_allowed(instant=True) is True


def test_notifier_policy_skips_one_shot_instant_when_notifier_once_disabled(monkeypatch):
    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(notifier=SimpleNamespace(enabled=True, once=False), noexit=False),
    )

    assert cli._notifier_should_start(instant=True, once=True) is False
    assert cli._instant_notifications_allowed(instant=True) is False


def test_cli_starts_notifier_before_instant_emby_run(monkeypatch, in_temp_dir: Path):
    events = []

    async def fake_reload_conf(_config_file):
        cli.config.notifier = SimpleNamespace(
            enabled=True,
            once=True,
            method="apprise",
            apprise_uri="mock://token",
        )
        cli.config.mongodb = None
        cli.config.nofail = True
        return True

    async def fake_start_notifier():
        events.append("start_notifier")
        return []

    class FakeEmbyManager:
        def run_all(self, instant=False):
            async def _run():
                events.append(f"run_all:{instant}")

            return _run()

    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(
            reload_conf=fake_reload_conf,
            on_change=lambda *args, **kwargs: None,
            basedir=None,
            windows=False,
            public=False,
            mongodb=None,
            nofail=True,
            noexit=False,
            notifier=None,
        ),
    )
    monkeypatch.setattr("embykeeper.notify.start_notifier", fake_start_notifier)
    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)
    monkeypatch.setattr(
        "embykeeper.cache.cache",
        SimpleNamespace(
            set=lambda *a, **k: None,
            get=lambda *a, **k: "test",
            delete=lambda *a, **k: None,
        ),
    )

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--emby", "--instant", "--once"])

    assert result.exit_code == 0
    assert events == ["start_notifier", "run_all:True"]


def test_cli_filters_instant_emby_run_by_account_name(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("beta")]
    patch_cli_runtime(monkeypatch, accounts)

    calls = []

    class FakeEmbyManager:
        def run_all(self, instant=False):
            async def _run():
                calls.append(("run_all", instant))

            return _run()

        def run_accounts(self, selected_accounts, instant=False):
            async def _run():
                calls.append(("run_accounts", instant, [a.name for a in selected_accounts]))

            return _run()

    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "alpha",
            "--instant",
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("run_accounts", True, ["alpha"])]


def test_cli_rejects_emby_account_without_emby_flag(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [make_fake_emby_account("alpha")])

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby-account",
            "alpha",
            "--once",
        ],
    )

    assert result.exit_code == 1
    assert "--emby-account 必须与 -e/--emby 一起使用" in result.output


def test_normalize_skips_empty_and_duplicate_names():
    assert cli._normalize_emby_account_names(["alpha,,alpha,beta"]) == ["alpha", "beta"]


def test_cli_splits_comma_separated_emby_account_names(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("beta")]
    patch_cli_runtime(monkeypatch, accounts)

    calls = []

    class FakeEmbyManager:
        def run_accounts(self, selected_accounts, instant=False):
            async def _run():
                calls.append((instant, [a.name for a in selected_accounts]))

            return _run()

    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "alpha,beta",
            "--instant",
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert calls == [(True, ["alpha", "beta"])]


def test_cli_rejects_unknown_emby_account_name(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("beta")]
    patch_cli_runtime(monkeypatch, accounts)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "missing",
            "--instant",
            "--once",
        ],
    )

    assert result.exit_code == 1
    assert "未找到 Emby 账号: missing" in result.output
    assert '可用账号名: "alpha,beta"' in result.output


def test_cli_rejects_duplicate_enabled_emby_account_names(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("alpha")]
    patch_cli_runtime(monkeypatch, accounts)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "alpha",
            "--instant",
            "--once",
        ],
    )

    assert result.exit_code == 1
    assert "Emby 账号 name 重复: alpha" in result.output


def test_cli_rejects_emby_account_when_no_enabled_named_accounts(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha", enabled=False), make_fake_emby_account(None)]
    patch_cli_runtime(monkeypatch, accounts)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "alpha",
            "--instant",
            "--once",
        ],
    )

    assert result.exit_code == 1
    assert "当前没有可供 --emby-account 选择的 Emby 账号" in result.output


def test_cli_filters_scheduled_emby_run_by_account_name(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("beta")]
    patch_cli_runtime(monkeypatch, accounts)

    calls = []

    class FakeEmbyManager:
        def schedule_all(self):
            async def _run():
                calls.append(("schedule_all", None))

            return _run()

        def schedule_accounts(self, selected_accounts):
            async def _run():
                calls.append(("schedule_accounts", [a.name for a in selected_accounts]))

            return _run()

    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
            "--emby-account",
            "beta",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("schedule_accounts", ["beta"])]


def test_cli_schedules_all_emby_accounts_when_no_selection(monkeypatch, in_temp_dir: Path):
    accounts = [make_fake_emby_account("alpha"), make_fake_emby_account("beta")]
    patch_cli_runtime(monkeypatch, accounts)

    calls = []

    class FakeEmbyManager:
        def schedule_all(self):
            async def _run():
                calls.append("schedule_all")

            return _run()

        def schedule_accounts(self, selected_accounts):
            async def _run():
                calls.append(("schedule_accounts", [a.name for a in selected_accounts]))

            return _run()

    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)

    result = runner.invoke(
        app,
        [
            "--basedir",
            str(in_temp_dir),
            "--emby",
        ],
    )

    assert result.exit_code == 0
    assert calls == ["schedule_all"]


def test_cli_exits_when_config_reload_fails(monkeypatch, in_temp_dir: Path):
    async def failing_reload(_config_file):
        return False

    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(
            reload_conf=failing_reload,
            on_change=lambda *a, **k: None,
            basedir=None,
            nofail=True,
        ),
    )

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once"])

    assert result.exit_code == 1


def test_cli_play_url_branch(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    calls = []

    class FakeEmbyManager:
        def __init__(self):
            pass

        async def play_url(self, url):
            calls.append(url)
            return True

    monkeypatch.setattr("embykeeper.emby.main.EmbyManager", FakeEmbyManager)

    result = runner.invoke(
        app,
        ["--basedir", str(in_temp_dir), "--play-url", "https://example.com/web/#/details?id=abc"],
    )

    assert result.exit_code == 0
    assert calls == ["https://example.com/web/#/details?id=abc"]


def test_cli_clean_branch(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    called = []

    async def fake_cleaner():
        called.append("cleaner")

    monkeypatch.setattr("embykeeper.clean.cleaner", fake_cleaner)

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--clean"])

    assert result.exit_code == 0
    assert called == ["cleaner"]


def test_cli_debug_notify_branch(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    called = []

    async def fake_debug_notifier():
        called.append("debug_notifier")

    monkeypatch.setattr("embykeeper.notify.debug_notifier", fake_debug_notifier)

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--debug-notify"])

    assert result.exit_code == 0
    assert called == ["debug_notifier"]


def test_cli_help_option(monkeypatch, in_temp_dir: Path):
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert result.output


def test_cli_disable_color(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once", "--disable-color"])
    assert result.exit_code == 0


def test_cli_docker_env(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    monkeypatch.setenv("EK_IN_DOCKER", "1")
    try:
        result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once"])
        assert result.exit_code == 0
    finally:
        monkeypatch.delenv("EK_IN_DOCKER", raising=False)


def test_cli_debug_cron_flag(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once", "--debug-cron"])
    assert result.exit_code == 0


def test_cli_debug_verbosity_levels(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])

    for flags in (["-d"], ["-dd"], ["-ddd"]):
        result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once", *flags])
        assert result.exit_code == 0, f"flags={flags} failed: {result.output}"


def test_cli_handles_unexpected_reload_error(monkeypatch, in_temp_dir: Path):
    async def failing_reload(_config_file):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        cli,
        "config",
        SimpleNamespace(
            reload_conf=failing_reload,
            on_change=lambda *a, **k: None,
            basedir=None,
            nofail=True,
        ),
    )

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once"])

    assert result.exit_code == 1


def test_cli_runs_exit_handlers(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])
    called = []

    async def fake_handler():
        called.append("handler")

    monkeypatch.setattr(cli.var, "exit_handlers", [fake_handler])

    async def fake_cleaner():
        pass

    monkeypatch.setattr("embykeeper.clean.cleaner", fake_cleaner)

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--clean"])

    assert result.exit_code == 0
    assert called == ["handler"]


def test_cli_exits_when_cache_unusable(monkeypatch, in_temp_dir: Path):
    patch_cli_runtime(monkeypatch, [])

    def boom_set(*a, **k):
        raise RuntimeError("no cache dir")

    monkeypatch.setattr(
        "embykeeper.cache.cache",
        SimpleNamespace(set=boom_set, get=lambda *a, **k: None, delete=lambda *a, **k: None),
    )

    result = runner.invoke(app, ["--basedir", str(in_temp_dir), "--once"])

    assert "本地缓存读写失败" in result.output
