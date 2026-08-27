from datetime import datetime
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
