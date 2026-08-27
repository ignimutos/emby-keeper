"""RunContext 生命周期测试: prepare/finish/父子关系/取消树."""

import asyncio

import pytest

import embykeeper.runinfo as runinfo
from embykeeper.runinfo import RunContext, RunStatus


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(runinfo, "cache", cache)
    return cache


def test_prepare_start_finish_roundtrip(fake_cache):
    ctx = RunContext.prepare(description="测试任务")
    assert ctx.id
    assert ctx.description == "测试任务"
    assert ctx.status == RunStatus.PENDING
    assert ctx.id in runinfo._running_runs

    ctx.start()
    assert ctx.start_time is not None
    assert ctx.status == RunStatus.RUNNING

    ctx.finish(RunStatus.SUCCESS, "成功")
    assert ctx.status == RunStatus.SUCCESS
    assert ctx.status_info == "成功"
    assert ctx.duration is not None
    assert ctx.id not in runinfo._running_runs
    assert fake_cache.get(f"runinfo.{ctx.id}") is not None


def test_get_loads_finished_run_from_cache(fake_cache):
    ctx = RunContext.prepare(description="t")
    ctx.finish(RunStatus.SUCCESS)
    loaded = RunContext.get(ctx.id)
    assert loaded is not None
    assert loaded.description == "t"
    assert loaded.status == RunStatus.SUCCESS


def test_get_returns_none_for_unknown(fake_cache):
    assert RunContext.get("NOPE") is None


def test_parent_child_relationship(fake_cache):
    parent = RunContext.prepare(description="parent")
    child = RunContext.prepare(description="child", parent_ids=[parent.id])
    assert [p.id for p in child.get_parents()] == [parent.id]
    assert [c.id for c in parent.get_children()] == [child.id]
    parent.finish()
    child.finish()


def test_cancel_tree_cancels_children(fake_cache):
    parent = RunContext.prepare(description="parent")
    child = RunContext.prepare(description="child", parent_ids=[parent.id])
    cancelled = []
    child._cancel = lambda: cancelled.append("child")
    parent.cancel_tree()
    assert cancelled == ["child"]
    parent.finish()
    child.finish()


def test_cancel_all_marks_runs_cancelled(fake_cache):
    ctx = RunContext.prepare(description="t")
    RunContext.cancel_all()
    assert ctx.status == RunStatus.CANCELLED
    assert ctx.id not in runinfo._running_runs


def test_yield_logs_returns_records(fake_cache):
    ctx = RunContext.prepare(description="t")
    ctx.start(RunStatus.RUNNING)
    logs = list(ctx.yield_logs())
    assert logs
    assert all(log.time is not None for log in logs)
    ctx.finish()


def test_run_wraps_async_function(fake_cache):
    async def func(ctx):
        ctx.set(RunStatus.RUNNING)
        return 42

    result = asyncio.run(RunContext.run(func, description="run"))
    assert result == 42


def test_finish_ignores_invalid_handler_id(fake_cache):
    ctx = RunContext.prepare(description="t")
    ctx._handler_id = 999999  # 不存在的 loguru handler
    ctx.finish(RunStatus.SUCCESS)  # 不应抛异常
    assert ctx.status == RunStatus.SUCCESS


def test_bind_logger(fake_cache):
    from loguru import logger

    ctx = RunContext.prepare(description="t")
    bound = ctx.bind_logger(logger)
    assert bound is not None
    ctx.finish()


def test_prepare_sink_captures_run_logs(fake_cache):
    from loguru import logger

    ctx = RunContext.prepare(description="t")
    bound = logger.bind(run_id=ctx.id)
    bound.info("测试日志消息")
    assert any(record.message == "测试日志消息" for record in ctx.log)
    ctx.finish()


def test_get_or_create_reuses_existing(fake_cache):
    ctx = RunContext.prepare(description="t")
    same = RunContext.get_or_create(run_id=ctx.id, description="t")
    assert same is ctx
    ctx.finish()


def test_get_or_create_makes_new(fake_cache):
    ctx = RunContext.get_or_create(description="new")
    assert ctx.id
    assert ctx.status == RunStatus.CATAGORY
    ctx.finish()


def test_cancel_tree_cancels_self(fake_cache):
    ctx = RunContext.prepare(description="t")
    cancelled = []
    ctx._cancel = lambda: cancelled.append("self")
    ctx.cancel_tree()
    assert cancelled == ["self"]
    ctx.finish()


def test_get_running_children(fake_cache):
    parent = RunContext.prepare(description="p")
    child = RunContext.prepare(description="c", parent_ids=[parent.id])
    assert [c.id for c in parent.get_running_children()] == [child.id]
    child.finish()
    assert parent.get_running_children() == []
    parent.finish()


def test_yield_logs_include_children(fake_cache):
    parent = RunContext.prepare(description="p")
    child = RunContext.prepare(description="c", parent_ids=[parent.id])
    child.set(RunStatus.RUNNING)
    logs = list(parent.yield_logs(include_children=True))
    assert logs
    parent.finish()
    child.finish()


def test_yield_logs_fills_missing_timestamps(fake_cache):
    from datetime import datetime

    ctx = RunContext.prepare(description="t")
    record = runinfo.LogRecord(level="DEBUG", message="无时间", time=datetime.now())
    record.time = None  # 绕过 pydantic 校验, 模拟缺失时间
    ctx.log = [record]
    logs = list(ctx.yield_logs())
    assert logs
    assert logs[0].time is not None
    ctx.finish()


def test_log_sink_method_append(fake_cache):
    from datetime import datetime
    from types import SimpleNamespace

    ctx = RunContext.prepare(description="t")
    message = SimpleNamespace(
        record={
            "extra": {"run_id": ctx.id},
            "level": SimpleNamespace(name="INFO"),
            "message": "来自 sink",
            "time": datetime.now(),
        }
    )
    ctx.log_sink(message)
    assert any(record.message == "来自 sink" for record in ctx.log)
    ctx.finish()


def test_run_reports_error_when_func_raises(fake_cache):
    async def func(ctx):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(RunContext.run(func, description="err"))


def test_run_reports_cancelled_when_func_cancelled(fake_cache):
    async def func(ctx):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(RunContext.run(func, description="cancel"))


def test_log_capped_at_max_records(fake_cache):
    ctx = RunContext.prepare(description="t")
    for _ in range(runinfo.MAX_LOG_RECORDS + 100):
        ctx.set(RunStatus.RUNNING)
    assert len(ctx.log) == runinfo.MAX_LOG_RECORDS
    ctx.finish()
