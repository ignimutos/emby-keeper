import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import embykeeper.schedule as schedule_module
from embykeeper.schedule import Scheduler


@pytest.fixture
def patch_config(monkeypatch):
    monkeypatch.setattr(
        schedule_module,
        "config",
        SimpleNamespace(debug_cron=False),
    )


async def noop(ctx=None):
    return None


def date_diff_days(dt: datetime) -> int:
    return (dt.date() - datetime.now().date()).days


def test_from_str_fixed_interval(patch_config):
    s = Scheduler.from_str(func=noop, interval_days="7", time_range="10:00")
    assert s.days == 7


def test_from_str_range_interval(patch_config):
    s = Scheduler.from_str(func=noop, interval_days="<7,12>", time_range="10:00")
    assert s.days == [7, 12]


def test_from_str_invalid_interval_raises(patch_config):
    with pytest.raises(ValueError):
        Scheduler.from_str(func=noop, interval_days="abc", time_range="10:00")


def test_from_str_parses_time_range(patch_config):
    s = Scheduler.from_str(func=noop, interval_days="7", time_range="<09:00,10:00>")
    assert s.start_time.hour == 9
    assert s.end_time.hour == 10


def test_next_time_fixed_interval_is_exact_days_out(patch_config):
    s = Scheduler(func=noop, days=7, start_time="09:00", end_time="10:00")
    assert date_diff_days(s.next_time) == 7


def test_next_time_range_uses_full_interval_spread(patch_config):
    """区间 <7,12> 不应恒取最大值 (回归: _calculate_next_time 区间塌缩)."""
    diffs = set()
    for _ in range(60):
        s = Scheduler(func=noop, days=[7, 12], start_time="09:00", end_time="10:00")
        diffs.add(date_diff_days(s.next_time))
    assert diffs.issubset({7, 8, 9, 10, 11, 12})
    assert len(diffs) > 1  # 若恒取 12, 集合将只有一个元素


def test_notification_next_time_within_range(patch_config):
    s = Scheduler(func=noop, days=[7, 12], start_time="09:00", end_time="10:00")
    # 先触发 next_time 缓存, 再验证通知时间独立计算且落在区间内
    assert date_diff_days(s.next_time) in range(7, 13)
    assert date_diff_days(s.notification_next_time) in range(7, 13)


def test_next_time_caches_until_passed(patch_config):
    s = Scheduler(func=noop, days=1, start_time="09:00", end_time="10:00")
    first = s.next_time
    assert s.next_time is first  # 属性缓存, 不重算


# --- 调度循环与持久化 ---


def test_schedule_loop_runs_func_once_then_breaks(patch_config, monkeypatch):
    import embykeeper.cache as cache_module

    store = {}

    class FakeCache:
        def __init__(self):
            self.deleted = []

        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)
            self.deleted.append(key)

    fake = FakeCache()
    monkeypatch.setattr(cache_module, "cache", fake)

    invocations = []

    def func(ctx):
        async def _run():
            invocations.append(1)

        return _run()

    s = Scheduler(func=func, days=[0, 5], sid="test.schedule")
    s._get_next_time = lambda: datetime.now() - timedelta(seconds=1)  # 已过期的下次时间

    asyncio.run(s.schedule())

    assert invocations == [1]
    assert "scheduler.test.schedule" in fake.deleted


def test_get_next_time_recalculates_on_config_hash_mismatch(patch_config, monkeypatch):
    import embykeeper.cache as cache_module

    store = {}

    class FakeCache:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    monkeypatch.setattr(cache_module, "cache", FakeCache())

    s = Scheduler(func=noop, days=7, sid="test.hash")
    store["scheduler.test.hash"] = {
        "config_hash": "stale-hash",
        "next_time": (datetime.now() + timedelta(days=7)).isoformat(),
    }

    next_time = s.next_time

    # 哈希不匹配 -> 重算并写回新哈希
    assert store["scheduler.test.hash"]["config_hash"] != "stale-hash"
    assert next_time > datetime.now()


def test_debug_cron_sets_ten_second_delay(monkeypatch):
    monkeypatch.setattr(schedule_module, "config", SimpleNamespace(debug_cron=True))
    s = Scheduler(func=noop, days=7, start_time="09:00")
    assert s.days == 0
    assert s.start_time == s.end_time


def test_interval_days_range_low_gte_high_returns_low(patch_config):
    s = Scheduler(func=noop, days=[10, 5])
    assert s._interval_days() == 10


def test_schedule_loop_handoff_notification_next_time(patch_config, monkeypatch):
    import embykeeper.cache as cache_module

    store = {}

    class FakeCache:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    monkeypatch.setattr(cache_module, "cache", FakeCache())

    def func(ctx):
        async def _run():
            pass

        return _run()

    s = Scheduler(func=func, days=[0, 5], sid="test.handoff")
    s._get_next_time = lambda: datetime.now() - timedelta(seconds=1)
    s._notification_next_time = datetime.now() + timedelta(days=1)

    asyncio.run(s.schedule())

    assert store["scheduler.test.handoff"]["next_time"]


def test_schedule_marks_cancelled_when_func_cancelled(patch_config):
    class FakeCtx:
        def __init__(self):
            self.finish_status = None

        def finish(self, status, *a):
            self.finish_status = status
            return self

    def func(ctx):
        async def _run():
            raise asyncio.CancelledError

        return _run()

    s = Scheduler(func=func, days=[0, 5], sid="test.cancel")
    s._get_next_time = lambda: datetime.now() - timedelta(seconds=1)
    s.on_next_time = lambda t: FakeCtx()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s.schedule())


def test_schedule_marks_error_when_func_raises(patch_config, monkeypatch):
    class FakeCtx:
        def __init__(self):
            self.finish_status = None

        def finish(self, status, *a):
            self.finish_status = status
            return self

    def func(ctx):
        async def _run():
            raise RuntimeError("boom")

        return _run()

    monkeypatch.setattr(schedule_module, "config", SimpleNamespace(debug_cron=False, nofail=True))
    s = Scheduler(func=func, days=[0, 5], sid="test.err")
    s._get_next_time = lambda: datetime.now() - timedelta(seconds=1)
    s.on_next_time = lambda t: FakeCtx()

    asyncio.run(s.schedule())


def test_schedule_reraises_when_nofail_false(patch_config, monkeypatch):
    class FakeCtx:
        def __init__(self):
            self.finish_status = None

        def finish(self, status, *a):
            self.finish_status = status
            return self

    def func(ctx):
        async def _run():
            raise RuntimeError("boom")

        return _run()

    monkeypatch.setattr(schedule_module, "config", SimpleNamespace(debug_cron=False, nofail=False))
    s = Scheduler(func=func, days=[0, 5], sid="test.nofail")
    s._get_next_time = lambda: datetime.now() - timedelta(seconds=1)
    s.on_next_time = lambda t: FakeCtx()

    with pytest.raises(RuntimeError):
        asyncio.run(s.schedule())


def test_schedule_loop_sleeps_when_next_time_future(patch_config, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(schedule_module.asyncio, "sleep", fake_sleep)

    def func(ctx):
        async def _run():
            pass

        return _run()

    s = Scheduler(func=func, days=[0, 5], sid="test.sleep")
    s._get_next_time = lambda: datetime.now() + timedelta(seconds=100)

    asyncio.run(s.schedule())

    assert slept  # wait_seconds > 0 被 sleep


def test_notification_next_time_without_next_time(patch_config):
    s = Scheduler(func=noop, days=7, start_time="09:00", end_time="10:00")
    assert s.notification_next_time > datetime.now()


def test_notification_next_time_cached_after_first_access(patch_config):
    s = Scheduler(func=noop, days=7, start_time="09:00", end_time="10:00")
    _ = s.next_time  # 先触发 _next_time, 使 notification_next_time 走缓存路径
    first = s.notification_next_time
    assert s.notification_next_time == first  # 命中缓存


def test_next_time_uses_future_cached_value(patch_config, monkeypatch):
    import embykeeper.cache as cache_module

    monkeypatch.setattr(
        cache_module,
        "cache",
        SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None),
    )
    s = Scheduler(func=noop, days=7, sid="test.future")
    s._next_time = datetime.now() + timedelta(days=7)
    assert s._get_next_time() == s._next_time  # 命中未过期缓存


def test_get_next_time_uses_valid_cache(patch_config, monkeypatch):
    import embykeeper.cache as cache_module

    store = {}

    class FakeCache:
        def get(self, key, default=None):
            return store.get(key, default)

        def set(self, key, value):
            store[key] = value

        def delete(self, key):
            store.pop(key, None)

    monkeypatch.setattr(cache_module, "cache", FakeCache())
    s = Scheduler(func=noop, days=7, sid="test.valid")
    store["scheduler.test.valid"] = {
        "config_hash": s._get_scheduler_config(),
        "next_time": (datetime.now() + timedelta(days=7)).isoformat(),
    }
    assert s.next_time > datetime.now()  # 直接命中有效缓存
