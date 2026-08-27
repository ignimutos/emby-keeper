import asyncio
import json
from types import SimpleNamespace

import pytest

import embykeeper.cache as cache_module
from embykeeper.cache import Cache


@pytest.fixture
def cache_with_dir(tmp_path):
    from embykeeper.config import config

    original = config._basedir
    config._basedir = tmp_path
    c = Cache()
    yield c, tmp_path
    config._basedir = original


def test_set_flush_persists_to_disk(cache_with_dir):
    c, tmp_path = cache_with_dir
    c.set("emby.env.test", {"i": 1})
    c.flush()
    data = json.loads((tmp_path / "cache.json").read_text())
    assert data["emby"]["env"]["test"] == {"i": 1}


def test_delete_noop_keeps_other_keys(cache_with_dir):
    c, tmp_path = cache_with_dir
    c.set("a.b", 1)
    # 无事件循环时 set 立即同步写盘
    assert json.loads((tmp_path / "cache.json").read_text())["a"]["b"] == 1
    c.delete("a.missing")  # 不存在的键: 不报错也不影响已有数据
    assert json.loads((tmp_path / "cache.json").read_text())["a"]["b"] == 1
    c.delete("a.b")
    assert json.loads((tmp_path / "cache.json").read_text()) == {}


def test_debounced_flush_coalesces_many_sets(cache_with_dir, monkeypatch):
    c, tmp_path = cache_with_dir
    monkeypatch.setattr(cache_module, "_FLUSH_DEBOUNCE", 0.01)

    async def main():
        c.set("a.b", 1)
        await asyncio.sleep(0.02)  # 等待首轮 flush
        c._dirty = False  # 模拟干净状态
        c.delete("a.missing")
        assert c._dirty is False  # 键不存在: 不标记写盘
        c.delete("a.b")
        assert c._dirty is True  # 键存在: 标记写盘
        await asyncio.sleep(0.02)
        assert c._dirty is False
        assert json.loads((tmp_path / "cache.json").read_text()) == {}

        for i in range(50):
            c.set(f"k{i}", i)
        assert c._dirty is True
        # 去抖窗口内多次 set 应合并为一次写盘
        await asyncio.sleep(0.05)
        assert c._dirty is False
        data = json.loads((tmp_path / "cache.json").read_text())
        assert data["k49"] == 49

    asyncio.run(main())


def test_set_without_event_loop_writes_synchronously(cache_with_dir):
    c, tmp_path = cache_with_dir
    c.set("sync.key", 1)
    assert c._dirty is False
    assert json.loads((tmp_path / "cache.json").read_text())["sync"]["key"] == 1


def test_get_dotted_traversal_and_default(cache_with_dir):
    c, _ = cache_with_dir
    c.set("emby.env.test", {"a": 1})
    assert c.get("emby.env.test") == {"a": 1}
    assert c.get("emby.env.test.a") == 1
    assert c.get("emby.env.missing") is None
    assert c.get("emby.env.missing", "fallback") == "fallback"


def test_delete_by_prefix_and_find_by_prefix(cache_with_dir):
    c, _ = cache_with_dir
    c.set("emby.env.a", 1)
    c.set("emby.env.b", 2)
    c.set("scheduler.x", 3)
    assert c.find_by_prefix("emby") == ["emby.env.a", "emby.env.b"]
    c.delete_by_prefix("emby.env")
    assert c.find_by_prefix("") == ["scheduler.x"]


def test_delete_many_cleans_empty_dicts(cache_with_dir):
    c, _ = cache_with_dir
    c.set("a.b", 1)
    c.set("a.c", 2)
    c.delete_many(["a.b", "a.c"])
    assert c.find_by_prefix("") == []
    # 删除不存在的键不标记写盘
    c._dirty = False
    c.delete_many(["zzz.missing"])
    assert c._dirty is False


def test_load_corrupt_cache_file_warns(tmp_path, monkeypatch):
    import embykeeper.cache as cache_module
    from embykeeper.config import config

    original = config._basedir
    config._basedir = tmp_path
    try:
        (tmp_path / "cache.json").write_text("{ not valid json !!!")
        cache = cache_module.Cache()
        assert cache._data == {}
    finally:
        config._basedir = original


def test_get_through_non_dict_value_returns_default(cache_with_dir):
    c, _ = cache_with_dir
    c.set("a", 1)
    assert c.get("a.b", "fallback") == "fallback"


def test_delete_mid_path_returns_when_intermediate_not_dict(cache_with_dir):
    c, _ = cache_with_dir
    c.set("a", 1)
    c.delete("a.b")  # 中间值不是 dict -> 直接返回, 不报错
    assert c.get("a") == 1


def test_flush_keeps_dirty_on_oserror(tmp_path, monkeypatch):
    import embykeeper.cache as cache_module
    from embykeeper.config import config

    # 静默预期内的写盘失败日志
    monkeypatch.setattr(cache_module, "logger", SimpleNamespace(warning=lambda m: None))
    original = config._basedir
    config._basedir = tmp_path
    try:
        c = cache_module.Cache()
        c._dirty = True
        c._cache_file = tmp_path  # 目录作为文件 -> open 失败
        c.flush()
        assert c._dirty is True  # 保持 dirty, 等待重试
        c._dirty = False  # 阻止 atexit flush 再次触发写失败日志
    finally:
        config._basedir = original
