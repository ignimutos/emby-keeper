"""保活类调度管理器基类.

EmbyManager 与 SubsonicManager 过去各自手写「统一 + 独立账号的 Scheduler 构建、
配置变更重排、任务启停」这套骨架, 且 Subsonic 的副本引入了 _watch_main
AttributeError 与 emby 配置泄漏。此处把该骨架收拢为单一深层模块; 子类仅提供
配置段名、账号选择谓词与保活运行体 (locality)。
"""

import asyncio
from typing import List, Optional

from loguru import logger

from embykeeper.config import config
from embykeeper.schedule import Scheduler
from embykeeper.utils import AsyncTaskPool

logger = logger.bind(scheme="embywatcher")


class ScheduledKeepaliveManager:
    """统一 + 独立账号的保活调度管理器基类."""

    # 子类覆盖
    section = "emby"  # config 段名
    title = "Emby"  # 日志措辞
    run_verb = "保活"  # 日志措辞

    def __init__(self):
        self._tasks: dict = {}  # account_spec -> task
        self._schedulers: dict = {}  # account_spec -> scheduler
        self._running: set = set()
        self._pool = AsyncTaskPool()

        config.on_list_change(f"{self.section}.account", self._handle_account_change)

    # --- 钩子 ---

    def _section(self):
        return getattr(config, self.section)

    def get_spec(self, a):
        return f"{a.username}@{a.name or a.url.host}"

    def _is_account_selected(self, account) -> bool:
        """子类可覆盖以限定账号子集."""
        return True  # pragma: no cover  # EmbyManager 覆盖了该方法

    def _is_independent(self, account) -> bool:
        return bool(account.time_range or account.interval_days)

    async def _run_accounts(self, accounts, instant: bool = False):
        """子类保活运行体."""
        raise NotImplementedError

    # --- 任务生命周期 ---

    def stop_account(self, account_spec: str):
        """停止一个独立账号的调度与运行任务."""
        if account_spec in self._schedulers:
            del self._schedulers[account_spec]

        if account_spec in self._tasks:
            self._tasks[account_spec].cancel()
            del self._tasks[account_spec]

        self._running.discard(account_spec)

    def stop_unified_accounts(self):
        """停止整体调度任务."""
        if "unified" in self._schedulers:
            del self._schedulers["unified"]

        if "unified" in self._tasks:
            self._tasks["unified"].cancel()
            del self._tasks["unified"]

    def _handle_account_change(self, added, removed):
        """处理账号增删: 独立账号直接启停, 整体账号标记重新调度."""
        need_reschedule_unified = False

        for account in removed:
            if not self._is_account_selected(account):
                continue
            spec = self.get_spec(account)
            if self._is_independent(account):
                self.stop_account(spec)
                logger.info(f"账号 {spec} 的 {self.title} {self.run_verb}及其计划任务已被清除.")
            else:
                need_reschedule_unified = True
                logger.info(
                    f"账号 {spec} {self.title} {self.run_verb}已被移除, 将重新调度{self.run_verb}任务."
                )

        for account in added:
            if not self._is_account_selected(account):
                continue
            if account.enabled:
                if self._is_independent(account):
                    scheduler = self.schedule_independent_account(account)
                    if scheduler:
                        self._pool.add(scheduler.schedule())
                        logger.info(
                            f"新增的账号 {self.get_spec(account)} 的 {self.title} {self.run_verb}计划任务已添加."
                        )
                else:
                    need_reschedule_unified = True
                    logger.debug(
                        f"新增的账号 {self.get_spec(account)}, 将重新调度 {self.title} {self.run_verb}任务."
                    )

        if need_reschedule_unified:
            self.stop_unified_accounts()
            self.schedule_unified_accounts()

    # --- 调度构建 ---

    def schedule_independent_account(self, account) -> Optional[Scheduler]:
        """为独立账号建立保活调度器."""
        if not account.enabled:
            return None

        account_spec = self.get_spec(account)
        time_range = account.time_range
        interval = account.interval_days
        if not time_range or not interval:
            # 仅在账号未显式配置时读取全局段 (保持原短路语义)
            section = self._section()
            time_range = time_range or section.time_range
            interval = interval or section.interval_days

        def make_on_next_time(spec):
            return lambda t: logger.info(
                f"下一次 {self.title} 账号 ({spec}) 的{self.run_verb}将在 {t.strftime('%m-%d %H:%M %p')} 进行."
            )

        def func(ctx):
            task = self._tasks[self.get_spec(account)] = asyncio.create_task(
                self.run_accounts([account], False)
            )
            return task

        scheduler = Scheduler.from_str(
            func=func,
            interval_days=interval,
            time_range=time_range,
            on_next_time=make_on_next_time(account_spec),
            sid=f"{self.section}.watch.{account_spec}",
            description=f"{self.title} {self.run_verb}任务 - {account_spec}",
        )
        self._schedulers[account_spec] = scheduler
        return scheduler

    def schedule_unified_accounts(self, accounts: Optional[List] = None):
        """为整体账号建立统一保活调度器."""
        source_accounts = accounts if accounts is not None else self._section().account
        unified_accounts = [
            a
            for a in source_accounts
            if self._is_account_selected(a) and a.enabled and not self._is_independent(a)
        ]

        if not unified_accounts:
            return None

        def on_next_time(t):
            return logger.info(
                f"下一次 {self.title} {self.run_verb}将在 {t.strftime('%m-%d %H:%M %p')} 进行."
            )

        def func(ctx):
            task = self._tasks["unified"] = asyncio.create_task(self.run_accounts(unified_accounts, False))
            return task

        scheduler = Scheduler.from_str(
            func=func,
            interval_days=self._section().interval_days,
            time_range=self._section().time_range,
            on_next_time=on_next_time,
            sid=f"{self.section}.watch.global",
            description=f"{self.title} {self.run_verb}任务",
        )
        self._schedulers["unified"] = scheduler
        self._pool.add(scheduler.schedule())

    # --- 编排 ---

    async def _schedule_accounts(self, accounts: List):
        self.schedule_unified_accounts(accounts)

        for account in accounts:
            if account.enabled and self._is_independent(account):
                scheduler = self.schedule_independent_account(account)
                if scheduler:
                    self._pool.add(scheduler.schedule())

        if not self._schedulers:
            logger.info(f"没有需要执行的 {self.title} {self.run_verb}任务")
            return None

        await self._pool.wait()

    async def schedule_all(self, instant: bool = False):
        return await self._schedule_accounts(
            self._section().account
        )  # pragma: no cover  # EmbyManager 覆盖了该方法

    async def run_accounts(self, accounts: List, instant: bool = False):
        return await self._run_accounts(accounts, instant)

    async def run_all(self, instant: bool = False):
        return await self.run_accounts(self._section().account, instant)
