"""utils.py 纯函数辅助工具的测试."""

import asyncio
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest

from embykeeper.schema import ProxyConfig
from embykeeper.utils import (
    CachedFuncProxy,
    FuncProxy,
    Proxy,
    batch,
    deep_update,
    distribute_numbers,
    flatten,
    format_byte_human,
    format_exception,
    format_timedelta_human,
    get_cls_fullpath,
    get_proxy_str,
    next_random_datetime,
    nonblocking,
    optional,
    redact_headers,
    remove_prefix,
    time_in_range,
    to_iterable,
    truncate_str,
)

# --- get_proxy_str ---


def test_get_proxy_str_http_with_auth():
    proxy = ProxyConfig(hostname="127.0.0.1", port=1080, scheme="http", username="u", password="p")
    assert get_proxy_str(proxy) == "http://u:p@127.0.0.1:1080"


def test_get_proxy_str_socks5_becomes_socks5h_for_curl():
    proxy = ProxyConfig(hostname="127.0.0.1", port=1080, scheme="socks5")
    assert get_proxy_str(proxy, curl=True) == "socks5h://127.0.0.1:1080"
    assert get_proxy_str(proxy) == "socks5://127.0.0.1:1080"


def test_get_proxy_str_none():
    assert get_proxy_str(None) is None


# --- redact_headers ---


def test_redact_headers_hides_tokens():
    headers = {
        "X-Emby-Token": "secret",
        "Authorization": 'MediaBrowser Client="Hills", Token="tok"',
        "User-Agent": "Hills/1.6.1",
    }
    redacted = redact_headers(headers)
    assert redacted["X-Emby-Token"] == "<redacted>"
    assert 'Token="<redacted>"' in redacted["Authorization"]
    assert redacted["User-Agent"] == "Hills/1.6.1"


# --- 容器/字符串工具 ---


def test_deep_update_recurses_into_dicts():
    base = {"emby": {"account": []}, "proxy": None}
    deep_update(base, {"emby": {"concurrency": 3}, "proxy": {"hostname": "x"}})
    assert base == {"emby": {"account": [], "concurrency": 3}, "proxy": {"hostname": "x"}}


def test_truncate_str():
    assert truncate_str("短", 10) == "短"
    assert truncate_str("1234567890", 5) == "12345678..."


def test_batch():
    assert list(batch([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_flatten():
    assert flatten([[1, 2], [3]]) == [1, 2, 3]


def test_to_iterable():
    assert to_iterable(None) == ()
    assert to_iterable("str") == ("str",)
    assert to_iterable([1, 2]) == [1, 2]


def test_time_in_range():
    assert time_in_range(time(9), time(17), time(12))
    assert not time_in_range(time(9), time(17), time(20))
    assert time_in_range(time(22), time(2), time(23))  # 跨夜
    assert time_in_range(time(22), time(2), time(1))


def test_remove_prefix():
    assert remove_prefix("hello", "hel") == "lo"
    assert remove_prefix("hello", "xyz") == "hello"


def test_get_cls_fullpath():
    # builtins 类不加前缀; 普通类带模块名
    assert get_cls_fullpath(ValueError) == "ValueError"
    assert get_cls_fullpath(SimpleNamespace) == "types.SimpleNamespace"


def test_format_timedelta_human():
    assert format_timedelta_human(timedelta(seconds=90)) == "1 分钟, 30 秒"
    assert format_timedelta_human(timedelta(seconds=0)) == "0 秒"


def test_format_byte_human():
    assert format_byte_human(512) == "512.0 Byte"
    assert format_byte_human(2048) == "2.00 KB"
    assert format_byte_human(5 * 1024**2) == "5.00 MB"


def test_next_random_datetime_zero_interval_uses_now():
    result = next_random_datetime(start_time=time(8), end_time=time(9), interval_days=0)
    assert result >= datetime.now()


def test_distribute_numbers_raises_on_bad_range():
    with pytest.raises(ValueError):
        distribute_numbers(10, 1)


def test_distribute_numbers_basic():
    nums = distribute_numbers(0, 100, num_elements=3, min_distance=10)
    assert len(nums) == 3
    assert all(0 <= n <= 100 for n in nums)
    # 升序且满足最小间距
    for a, b in zip(nums, nums[1:]):
        assert b - a >= 10


def test_distribute_numbers_respects_base():
    nums = distribute_numbers(0, 100, num_elements=1, min_distance=10, base=[50])
    assert len(nums) == 1
    assert 0 <= nums[0] <= 100


def test_nonblocking_runs_when_free():
    async def main():
        ran = []
        async with nonblocking(asyncio.Lock()):
            ran.append(1)
        assert ran == [1]

    asyncio.run(main())


def test_nonblocking_releases_lock():
    async def main():
        lock = asyncio.Lock()
        async with nonblocking(lock):
            pass
        assert not lock.locked()  # 已释放

    asyncio.run(main())


def test_nonblocking_runs_body_without_waiting_when_locked():
    # 实现语义: 锁被占用时不等待, 直接无锁执行 body (而非跳过)
    async def main():
        lock = asyncio.Lock()
        await lock.acquire()
        ran = []
        async with nonblocking(lock):
            ran.append(1)
        assert ran == [1]

    asyncio.run(main())


def test_optional_context_manager():
    async def main():
        ran = []
        async with optional(None):
            ran.append(1)
        async with optional(asyncio.Lock()):
            ran.append(2)
        assert ran == [1, 2]

    asyncio.run(main())


def test_format_exception_contains_class_and_message():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        text = format_exception(exc, regular=False)
    assert "ValueError" in text
    assert "boom" in text


# --- Proxy 系列 ---


def test_proxy_operator_overloads():
    p = Proxy(5)
    assert p + 3 == 8
    assert 3 + p == 8
    assert -Proxy(3) == -3
    assert Proxy(2) ** 3 == 8
    assert divmod(Proxy(7), 2) == (3, 1)
    assert Proxy(10) // 3 == 3
    assert Proxy(5) < 10
    p += 1
    assert p == 6


def test_proxy_item_and_attr_delegation():
    p = Proxy({"a": 1})
    assert p["a"] == 1
    p["b"] = 2
    assert p["b"] == 2
    assert "a" in p
    assert bool(p)
    assert not bool(Proxy(0))
    del p["a"]
    assert p == {"b": 2}
    assert callable(p.items)  # 属性转发到 subject
    # Python 3 不自动调用 __getslice__, 直接调用以覆盖
    assert Proxy([1, 2, 3]).__getslice__(0, 2) == [1, 2]
    Proxy([1, 2, 3]).__setslice__(0, 1, [9])
    p2 = Proxy([1, 2, 3])
    p2.__delslice__(0, 1)
    assert p2 == [2, 3]


def test_proxy_setattr_delegates_to_subject():
    class Obj:
        def __init__(self):
            self.x = 1

    o = Obj()
    p = Proxy(o)
    p.x = 5
    assert o.x == 5
    del p.x
    assert not hasattr(o, "x")


def test_func_and_cached_func_proxy():
    p = FuncProxy(lambda: 42)
    assert p == 42

    c = CachedFuncProxy(lambda: {"n": 1})
    assert c == {"n": 1}
    assert c._cached_value == {"n": 1}
    assert c == {"n": 1}  # 命中缓存


def test_proxy_call_and_remaining_dunders():
    import operator

    assert Proxy(lambda: 5)() == 5
    # 显式调用 __index__ 会被 __getattribute__ 转发到 subject, 需经 operator 协议触发
    assert operator.index(Proxy(5)) == 5
    assert divmod(3, Proxy(5)) == divmod(3, 5)
    assert 3 ** Proxy(2) == 9
    p = Proxy(2)
    p **= 3
    assert p == 8
    q = Proxy({"a": 1})
    del q.__subject__
    assert not hasattr(q, "__subject__")


def test_get_path_frame_none():
    from embykeeper.utils import get_path_frame

    try:
        raise ValueError("boom")
    except ValueError as exc:
        frame = get_path_frame(exc, "/nonexistent/prefix")
    assert frame is None


def test_random_time_overnight():
    from embykeeper.utils import random_time

    t = random_time(time(22), time(2))
    assert t is not None


def test_async_partial_and_idle():
    from embykeeper.utils import async_partial, idle

    async def add(a, b):
        return a + b

    assert asyncio.run(async_partial(add, 1)(2)) == 3

    async def main():
        task = asyncio.create_task(idle())
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())


def test_async_count_pool_append():
    from embykeeper.utils import AsyncCountPool

    async def main():
        pool = AsyncCountPool()
        key = await pool.append("x")
        assert key == 1001
        assert pool[1001] == "x"

    asyncio.run(main())


def test_async_task_pool():
    from embykeeper.utils import AsyncTaskPool

    async def main():
        pool = AsyncTaskPool()
        pool.add(asyncio.sleep(0), "t1")
        results = await pool.wait()
        assert results == [None]

    asyncio.run(main())


def test_async_task_pool_yields_already_done():
    from embykeeper.utils import AsyncTaskPool

    async def main():
        pool = AsyncTaskPool()
        pool.add(asyncio.sleep(0), "t1")
        await asyncio.sleep(0.01)  # t1 已完成
        done = []
        async for t in pool.as_completed():
            done.append(t.get_name())
        assert done == ["t1"]

    asyncio.run(main())


def test_format_byte_human_gb_and_tb():
    assert format_byte_human(2 * 1024**3) == "2.00 GB"
    assert format_byte_human(2 * 1024**4) == "2.00 TB"


def test_distribute_numbers_with_max_distance():
    nums = distribute_numbers(0, 100, num_elements=1, min_distance=10, max_distance=15)
    assert len(nums) == 1


def test_distribute_numbers_rejects_bad_distance_range():
    with pytest.raises(ValueError):
        distribute_numbers(0, 100, min_distance=10, max_distance=5)


def test_show_exception_debug_branch(monkeypatch):
    import embykeeper.utils as utils
    from embykeeper import var

    monkeypatch.setattr(var, "debug", 2)
    called = []
    monkeypatch.setattr(
        utils,
        "logger",
        SimpleNamespace(opt=lambda **kw: SimpleNamespace(debug=lambda m: called.append(m))),
    )
    utils.show_exception(ValueError("x"), regular=True)
    assert called
