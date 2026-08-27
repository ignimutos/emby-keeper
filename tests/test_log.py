"""log.py 日志适配器与格式化器的测试."""

import logging
from types import SimpleNamespace

import pytest

from embykeeper.log import InterceptHandler, formatter


def test_formatter_embywatcher_scheme():
    record = {
        "extra": {"scheme": "embywatcher", "username": "user", "server": "example.com"},
        "message": "hello",
    }
    text = formatter(record)
    assert "Emby保活" in text
    # 占位符形式: {extra[username]}@{extra[server]}
    assert "{extra[username]}@{extra[server]}" in text


def test_formatter_plain_scheme():
    record = {"extra": {}, "message": "hello"}
    assert formatter(record) == "{message}"


def test_intercept_handler_emits_to_loguru(monkeypatch):
    import embykeeper.log as log_module

    emitted = []

    def fake_opt(depth=0, exception=None):
        return SimpleNamespace(debug=lambda text: emitted.append(text))

    monkeypatch.setattr(
        log_module,
        "logger",
        SimpleNamespace(
            level=lambda name: SimpleNamespace(name=name),
            opt=fake_opt,
        ),
    )

    handler = InterceptHandler()
    record = logging.LogRecord("mymod", logging.INFO, "/path/file.py", 1, "hello", None, None)
    handler.emit(record)

    assert emitted and "[INFO]" in emitted[0]
    assert "hello" in emitted[0]


def test_intercept_handler_falls_back_to_levelno(monkeypatch):
    import embykeeper.log as log_module

    emitted = []

    def fake_opt(depth=0, exception=None):
        return SimpleNamespace(debug=lambda text: emitted.append(text))

    def unknown_level(name):
        raise ValueError("unknown level")

    monkeypatch.setattr(
        log_module,
        "logger",
        SimpleNamespace(level=unknown_level, opt=fake_opt),
    )

    handler = InterceptHandler()
    record = logging.LogRecord("m", logging.DEBUG, "/f.py", 1, "hi", None, None)
    handler.emit(record)

    assert emitted and "[10]" in emitted[0]


def test_apply_logging_adapter(monkeypatch):
    import logging as logging_module

    import embykeeper.log as log_module

    monkeypatch.setattr(logging_module, "basicConfig", lambda **kw: None)
    log_module.apply_logging_adapter(level=logging.DEBUG)  # 不崩溃即可
