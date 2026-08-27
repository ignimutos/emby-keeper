"""notify.py 通知过滤逻辑与 apprise.py AppriseStream 的测试."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import apprise
import pytest

import embykeeper.notify as notify
from embykeeper.apprise import AppriseStream


def make_record(level=logging.INFO, extra=None):
    return {"level": SimpleNamespace(no=level), "extra": extra or {}}


def test_should_notify_log_on_explicit_log_flag():
    assert notify.should_notify_log(make_record(extra={"log": True})) is True


def test_should_notify_log_on_error_level():
    assert notify.should_notify_log(make_record(level=logging.ERROR)) is True


def test_should_notify_log_suppressed_by_nonotify():
    assert notify.should_notify_log(make_record(extra={"log": True, "nonotify": True})) is False


def test_should_notify_msg():
    assert notify.should_notify_msg(make_record(extra={"msg": True})) is True
    assert notify.should_notify_msg(make_record(extra={"msg": False})) is False
    assert notify.should_notify_msg(make_record(extra={"msg": True, "nonotify": True})) is False


def test_instant_notification_window_blocks():
    notify.set_instant_notification_window(True, allow=False)
    assert notify.should_notify_log(make_record(extra={"log": True})) is False
    notify.clear_instant_notification_window()
    assert notify.should_notify_log(make_record(extra={"log": True})) is True


@pytest.fixture(autouse=True)
def reset_notify_state(monkeypatch):
    monkeypatch.setattr(notify, "stream_log", None)
    monkeypatch.setattr(notify, "stream_msg", None)
    monkeypatch.setattr(notify, "handler_log_id", None)
    monkeypatch.setattr(notify, "handler_msg_id", None)
    monkeypatch.setattr(notify, "change_handle_notifier", None)


def test_start_notifier_returns_none_when_disabled(reset_notify_state, monkeypatch):
    monkeypatch.setattr(
        notify,
        "config",
        SimpleNamespace(notifier=SimpleNamespace(enabled=False), on_change=lambda *a, **k: None),
    )
    assert asyncio.run(notify.start_notifier()) is None


def test_start_notifier_returns_none_without_uri(reset_notify_state, monkeypatch):
    monkeypatch.setattr(
        notify,
        "config",
        SimpleNamespace(
            notifier=SimpleNamespace(enabled=True, apprise_uri=None), on_change=lambda *a, **k: None
        ),
    )
    assert asyncio.run(notify.start_notifier()) is None


def test_start_and_stop_notifier(reset_notify_state, monkeypatch):
    class FakeStream:
        def __init__(self):
            self.closed = 0

        def write(self, message):
            pass

        def close(self):
            self.closed += 1

        async def join(self):
            pass

    monkeypatch.setattr(
        notify,
        "config",
        SimpleNamespace(
            notifier=SimpleNamespace(enabled=True, apprise_uri="mock://u"), on_change=lambda *a, **k: None
        ),
    )
    monkeypatch.setattr(notify, "AppriseStream", lambda uri: FakeStream())

    streams = asyncio.run(notify.start_notifier())
    assert streams is not None
    assert notify.handler_log_id is not None
    assert notify.handler_msg_id is not None

    asyncio.run(notify._stop_notifier())
    assert notify.handler_log_id is None
    assert notify.handler_msg_id is None
    assert streams[0].closed == 1


# --- AppriseStream ---


def test_apprise_stream_write_maps_warning_level(monkeypatch):
    stream = AppriseStream(uri="")
    notified = []
    stream.apobj = SimpleNamespace(notify=lambda **kw: notified.append(kw) or True, add=lambda u: None)

    stream.write("WARNING#[orange3]某些消息[/]")

    assert notified[-1]["notify_type"] == apprise.NotifyType.WARNING
    assert "某些消息" in notified[-1]["body"]


def test_apprise_stream_write_maps_error_to_failure(monkeypatch):
    stream = AppriseStream(uri="")
    notified = []
    stream.apobj = SimpleNamespace(notify=lambda **kw: notified.append(kw) or True, add=lambda u: None)

    stream.write("ERROR#boom")

    assert notified[-1]["notify_type"] == apprise.NotifyType.FAILURE


def test_apprise_stream_warns_when_notify_fails(monkeypatch):
    stream = AppriseStream(uri="")
    stream.apobj = SimpleNamespace(notify=lambda **kw: False, add=lambda u: None)
    warnings = []
    monkeypatch.setattr("embykeeper.apprise.logger", SimpleNamespace(warning=lambda m: warnings.append(m)))

    stream.write("INFO#hello")

    assert warnings


def test_apprise_stream_maps_success_level():
    stream = AppriseStream(uri="")
    notified = []
    stream.apobj = SimpleNamespace(notify=lambda **kw: notified.append(kw) or True, add=lambda u: None)

    stream.write("SUCCESS#done")

    assert notified[-1]["notify_type"] == apprise.NotifyType.SUCCESS


def test_apprise_stream_close_join_noop():
    stream = AppriseStream(uri="")
    stream.apobj = SimpleNamespace(notify=lambda **kw: True, add=lambda u: None)
    stream.close()
    asyncio.run(stream.join())


def test_handle_config_change_restarts_notifier(reset_notify_state, monkeypatch):
    stopped = []

    monkeypatch.setattr(notify, "_stop_notifier", AsyncMock(side_effect=lambda: stopped.append(1)))
    monkeypatch.setattr(
        notify,
        "start_notifier",
        AsyncMock(side_effect=lambda: (SimpleNamespace(), SimpleNamespace())),
    )
    monkeypatch.setattr(notify, "config", SimpleNamespace(notifier=SimpleNamespace(enabled=True)))
    monkeypatch.setattr(notify, "logger", SimpleNamespace(debug=lambda m: None))

    async def main():
        notify._handle_config_change()
        await asyncio.sleep(0.05)

    asyncio.run(main())

    assert stopped == [1]


def test_debug_notifier_without_streams(reset_notify_state, monkeypatch):
    monkeypatch.setattr(notify, "start_notifier", AsyncMock(return_value=None))
    monkeypatch.setattr(notify, "logger", SimpleNamespace(error=lambda m: None))
    asyncio.run(notify.debug_notifier())


def test_debug_notifier_with_streams(reset_notify_state, monkeypatch):
    class FakeStream:
        async def join(self):
            pass

    monkeypatch.setattr(notify, "start_notifier", AsyncMock(return_value=[FakeStream()]))
    monkeypatch.setattr(notify, "logger", SimpleNamespace(info=lambda m: None, error=lambda m: None))
    monkeypatch.setattr(
        notify,
        "debug_logger",
        SimpleNamespace(bind=lambda **k: SimpleNamespace(info=lambda m: None)),
    )
    asyncio.run(notify.debug_notifier())
