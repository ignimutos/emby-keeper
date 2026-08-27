"""ConfigManager 加载/重载/监听/回调的测试."""

import asyncio
import base64
import os
from types import SimpleNamespace

import pytest

from embykeeper.config import ConfigManager
from embykeeper.schema import Config


@pytest.fixture
def fake_awatch(monkeypatch):
    """把 watchfiles.awatch 换成空异步生成器, 避免真实文件监听."""

    import watchfiles

    async def noop(path):
        if False:
            yield

    monkeypatch.setattr(watchfiles, "awatch", noop)


# --- load_config_str (环境变量) ---


def test_load_config_str_decodes_env_payload(monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    payload = base64.b64encode(b"emby = { account = [] }\n").decode()
    assert ConfigManager().load_config_str(payload) == {"emby": {"account": []}}


def test_load_config_str_strips_whitespace(monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    payload = "  " + base64.b64encode(b"emby = {}\n").decode() + "\n"
    assert ConfigManager().load_config_str(payload) == {"emby": {}}


def test_load_config_str_rejects_invalid_base64(monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    assert ConfigManager().load_config_str("not-base64!!!") is None


def test_load_config_str_rejects_invalid_toml(monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    payload = base64.b64encode(b"this is = not toml [").decode()
    assert ConfigManager().load_config_str(payload) is None


# --- reload_conf (文件) ---


def test_reload_conf_loads_toml_file(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    conf = tmp_path / "config.toml"
    conf.write_text('proxy = { hostname = "127.0.0.1", port = 1080, scheme = "socks5" }\n')
    manager = ConfigManager()
    assert asyncio.run(manager.reload_conf(conf)) is True
    assert manager.proxy.hostname == "127.0.0.1"
    assert manager.proxy.port == 1080


def test_reload_conf_missing_file_returns_false(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    manager = ConfigManager()
    assert asyncio.run(manager.reload_conf(tmp_path / "missing.toml")) is False


def test_reload_conf_invalid_toml_returns_false(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    conf = tmp_path / "config.toml"
    conf.write_text("invalid [[ toml")
    assert asyncio.run(ConfigManager().reload_conf(conf)) is False


def test_reload_conf_non_toml_suffix_returns_false(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    conf = tmp_path / "config.yaml"
    conf.write_text("emby: {}")
    assert asyncio.run(ConfigManager().reload_conf(conf)) is False


def test_reload_conf_generates_default_file_when_missing(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert asyncio.run(ConfigManager().reload_conf()) is False
        assert (tmp_path / "config.toml").exists()
    finally:
        os.chdir(original)


# --- reload_conf (环境变量) ---


def test_reload_conf_applies_env_config(fake_awatch, monkeypatch):
    payload = base64.b64encode(b'proxy = { hostname = "env-proxy", port = 1, scheme = "http" }\n').decode()
    monkeypatch.setenv("EK_CONFIG", payload)
    manager = ConfigManager()
    assert asyncio.run(manager.reload_conf()) is True
    assert manager.proxy.hostname == "env-proxy"


def test_reload_conf_rejects_bad_env(fake_awatch, monkeypatch):
    monkeypatch.setenv("EK_CONFIG", "!!! not base64")
    assert asyncio.run(ConfigManager().reload_conf()) is False


# --- validate_config ---


def test_validate_config_none_returns_none():
    assert ConfigManager().validate_config(None) is None


def test_validate_config_invalid_returns_none():
    assert ConfigManager().validate_config({"emby": {"concurrency": "not-int"}}) is None


def test_validate_config_valid_returns_config():
    model = ConfigManager().validate_config({"emby": {"concurrency": 3}})
    assert isinstance(model, Config)
    assert model.emby.concurrency == 3


# --- set / 回调 ---


def test_set_dict_validates_and_triggers_change(monkeypatch):
    manager = ConfigManager()
    calls = []
    manager.on_change("emby.concurrency", lambda old, new: calls.append((old, new)))
    assert manager.set({"emby": {"concurrency": 1}}) is True
    assert manager.set({"emby": {"concurrency": 3}}) is True
    assert calls == [(None, 1), (1, 3)]


def test_set_list_change_fires_with_added_deleted(monkeypatch):
    manager = ConfigManager()
    calls = []
    manager.on_list_change("emby.account", lambda added, deleted: calls.append((added, deleted)))
    acc1 = {"url": "https://example.com", "username": "u", "password": "p", "name": "a"}
    acc2 = {"url": "https://example.com", "username": "u", "password": "p", "name": "b"}
    assert manager.set({"emby": {"account": [acc1]}}) is True
    assert manager.set({"emby": {"account": [acc1, acc2]}}) is True
    added, deleted = calls[-1]
    assert [a.name for a in added] == ["b"]
    assert deleted == []


def test_callback_handle_unregisters_on_exit(monkeypatch):
    manager = ConfigManager()
    fired = []
    with manager.on_change("emby.concurrency", lambda old, new: fired.append(new)):
        manager.set({"emby": {"concurrency": 2}})
    manager.set({"emby": {"concurrency": 5}})
    assert fired == [2]


# --- start_observer (配置监听) ---


def test_basedir_default_and_setter(tmp_path):
    manager = ConfigManager()
    assert manager.basedir  # 默认返回 user_data_dir
    target = tmp_path / "nested" / "dir"
    manager.basedir = target
    assert target.is_dir()  # setter 自动创建


def test_set_invalid_dict_returns_false():
    assert ConfigManager().set({"emby": {"concurrency": "not-int"}}) is False


def test_set_non_dict_non_config_returns_false():
    assert ConfigManager().set(None) is False


def test_reset_clears_cache():
    manager = ConfigManager()
    manager.set({"emby": {}})
    assert manager._cache is not None
    manager.reset()
    assert manager._cache is None


def test_callback_exception_is_swallowed(monkeypatch):
    import embykeeper.config as config_module

    manager = ConfigManager()

    def boom(old, new):
        raise RuntimeError("callback boom")

    warnings = []
    monkeypatch.setattr(config_module, "logger", SimpleNamespace(warning=lambda m: warnings.append(m)))
    monkeypatch.setattr(config_module, "show_exception", lambda e, regular=False: None)

    manager.on_change("emby.concurrency", boom)
    manager.set({"emby": {"concurrency": 3}})

    assert warnings  # 回调异常被捕获并记录警告


def test_change_callback_missing_path_not_called(monkeypatch):
    manager = ConfigManager()
    calls = []
    manager.on_change("nonexistent.deep.key", lambda old, new: calls.append((old, new)))
    manager.set({"emby": {}})
    # 路径不存在 -> old/new 均为 None -> 不触发回调
    assert calls == []


def test_list_change_callback_exception_is_swallowed(monkeypatch):
    import embykeeper.config as config_module

    manager = ConfigManager()

    def boom(added, deleted):
        raise RuntimeError("callback boom")

    warnings = []
    monkeypatch.setattr(config_module, "logger", SimpleNamespace(warning=lambda m: warnings.append(m)))
    monkeypatch.setattr(config_module, "show_exception", lambda e, regular=False: None)

    manager.on_list_change("emby.account", boom)
    acc1 = {"url": "https://example.com", "username": "u", "password": "p", "name": "a"}
    manager.set({"emby": {"account": [acc1]}})

    assert warnings


def test_start_observer_reloads_on_file_change(tmp_path, monkeypatch):
    conf = tmp_path / "config.toml"
    conf.write_text("emby = {}\n")

    import watchfiles

    async def change_awatch(path):
        yield [("modified", path)]
        await asyncio.sleep(3600)

    monkeypatch.setattr(watchfiles, "awatch", change_awatch)

    class RecordingManager(ConfigManager):
        def __init__(self):
            super().__init__()
            # ProxyBase 会把普通 setattr 转发到 __subject__, 需用 object.__setattr__ 绕过
            object.__setattr__(self, "reload_calls", [])

        async def reload_conf(self, conf_file=None):
            self.reload_calls.append(conf_file)
            return True

    manager = RecordingManager()
    manager._conf_file = conf

    async def main():
        await manager.start_observer()
        await asyncio.sleep(0.1)
        if manager._observer:
            manager._observer.cancel()

    asyncio.run(main())

    assert manager.reload_calls == [conf]


def test_reload_conf_reuses_prior_conf_file(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    conf = tmp_path / "config.toml"
    conf.write_text("emby = {}\n")
    manager = ConfigManager()
    assert asyncio.run(manager.reload_conf(conf)) is True
    # 再 reload_conf() (无参数) 应复用已记录的 _conf_file
    assert asyncio.run(manager.reload_conf()) is True


def test_reload_conf_uses_default_config_toml(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    (tmp_path / "config.toml").write_text('proxy = { hostname = "def", port = 1, scheme = "http" }\n')
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        manager = ConfigManager()
        assert asyncio.run(manager.reload_conf()) is True
        assert manager.proxy.hostname == "def"
    finally:
        os.chdir(original)


def test_reload_conf_invalid_schema_returns_false(fake_awatch, tmp_path, monkeypatch):
    monkeypatch.delenv("EK_CONFIG", raising=False)
    conf = tmp_path / "config.toml"
    conf.write_text('emby = { concurrency = "bad" }\n')
    assert asyncio.run(ConfigManager().reload_conf(conf)) is False


def test_start_observer_restarts_existing_observer(fake_awatch, tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text("emby = {}\n")
    manager = ConfigManager()
    manager._conf_file = conf

    async def main():
        await manager.start_observer()
        first = manager._observer
        await manager.start_observer()
        assert manager._observer is not first
        manager._observer.cancel()

    asyncio.run(main())
