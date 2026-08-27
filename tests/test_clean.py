"""clean.py 缓存清理逻辑的测试."""

import asyncio
from types import SimpleNamespace

import embykeeper.clean as clean_module
from embykeeper.clean import clean_cache


class FakeCache:
    def __init__(self, data):
        self.data = data  # 持有引用, 使断言能观察到变更

    def find_by_prefix(self, prefix):
        return [k for k in self.data if k.startswith(prefix)]

    def delete_many(self, keys):
        for k in keys:
            self.data.pop(k, None)

    def delete(self, key):
        self.data.pop(key, None)


def make_data():
    return {
        "emby.credential.example.com.user": 1,
        "emby.env.example.com.user": 2,
        "scheduler.emby.watch.global": 3,
        "runinfo.ABC123": 4,
    }


def test_clean_cache_single_key(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    assert "已清理缓存" in clean_cache(cache_key="emby.env.example.com.user")
    assert "emby.env.example.com.user" not in data


def test_clean_cache_all_except_credentials(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    result = clean_cache(cache_prefix="all_except_credentials")
    assert "共 3 条" in result
    assert "emby.credential.example.com.user" in data  # 凭据保留
    assert "scheduler.emby.watch.global" not in data


def test_clean_cache_all(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    clean_cache(cache_prefix="all")
    assert data == {}


def test_clean_cache_prefix(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    result = clean_cache(cache_prefix="scheduler")
    assert "共 1 条" in result
    assert "scheduler.emby.watch.global" not in data


def test_clean_cache_no_args(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    result = clean_cache()
    assert "请指定" in result


def test_cleaner_deletes_selected_prefix(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "4"))  # scheduler
    asyncio.run(clean_module.cleaner())
    assert "scheduler.emby.watch.global" not in data
    assert "emby.credential.example.com.user" in data


def test_cleaner_deletes_credentials_prefix(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "5"))  # emby.credential
    asyncio.run(clean_module.cleaner())
    assert "emby.credential.example.com.user" not in data


def test_cleaner_specific_credential_key(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "5.1"))
    asyncio.run(clean_module.cleaner())
    assert "emby.credential.example.com.user" not in data


def test_cleaner_special_all(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "6"))  # 所有缓存
    asyncio.run(clean_module.cleaner())
    assert data == {}


def test_cleaner_invalid_option(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "99"))
    asyncio.run(clean_module.cleaner())  # 不崩溃即可
    assert data == make_data()  # 无删除


def test_cleaner_index_out_of_range(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "5.99"))
    asyncio.run(clean_module.cleaner())
    assert data == make_data()  # 索引越界, 无删除


def test_cleaner_invalid_index_format(monkeypatch):
    data = make_data()
    monkeypatch.setattr(clean_module, "cache", FakeCache(data))
    monkeypatch.setattr(clean_module, "Prompt", SimpleNamespace(ask=lambda *a, **k: "5.abc"))
    asyncio.run(clean_module.cleaner())
    assert data == make_data()  # 非法格式, 无删除
