import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typer.testing import CliRunner

import pytest

import embykeeper
import embykeeper.cli as cli
from embykeeper.cli import app

runner = CliRunner()


@pytest.fixture()
def in_temp_dir(tmp_path: Path):
    current = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(current)


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
