"""账号级任务管理器基类.

MonitorManager 与 MessageManager 过去是逐行相同的复制, 仅站点字段、上下文键
与日志措辞不同。此处把「账号任务注册/注销 + 配置变更重排」的生命周期收拢为
单一深层模块, 子类只需提供站点解析与运行体 (locality)。
"""

import asyncio
from typing import Dict, List, Set

from loguru import logger

from embykeeper.schema import TelegramAccount
from embykeeper.config import config
from embykeeper.runinfo import RunContext

from .session import ClientsSession

logger = logger.bind(scheme="telechecker")


class TelegramTaskManager:
    """账号级任务管理器: 持有账号任务的启停与配置变更重排生命周期."""

    # 子类覆盖
    feature = "monitor"  # 控制启用的账号字段 (monitor / messager)
    context_key = "monitor"  # RunContext 键前缀
    verb = "监控"  # 日志措辞
    task_label = "群组监控任务"  # 日志措辞

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}  # phone -> task
        self._running: Set[str] = set()  # Currently running phones

        config.on_list_change("telegram.account", self._handle_account_change)

    def _handle_account_change(self, added: List[TelegramAccount], removed: List[TelegramAccount]):
        """处理账号增删: 注销移除的任务, 启动新增的任务."""
        for account in removed:
            logger.info(f"{account.phone} 账号的{self.task_label}已被清除.")
            self.stop_account(account.phone)

        for account in added:
            if getattr(account, self.feature) and account.enabled:
                logger.info(f"新增的 {account.phone} 账号的{self.task_label}已增加.")
                self.start_account(account)

    def stop_account(self, phone: str):
        """停止一个账号的运行任务."""
        if phone in self._tasks:
            self._tasks[phone].cancel()
            del self._tasks[phone]
        self._running.discard(phone)

    def start_account(self, account: TelegramAccount):
        """为一个账号启动运行任务."""
        if not getattr(account, self.feature) or account.phone in self._running:
            return
        task = asyncio.create_task(self.run_account(account))
        self._tasks[account.phone] = task
        return task

    async def run_account(self, account: TelegramAccount):
        """运行单个账号: 建立客户端会话后交给子类运行体."""
        if account.phone in self._running:
            logger.warning(f"账户 {account.phone} 的{self.verb}已经在执行.")
            return

        account_ctx = RunContext.get_or_create(f"{self.context_key}.account.{account.phone}")

        self._running.add(account.phone)
        try:
            async with ClientsSession([account]) as clients:
                async for a, client in clients:
                    await RunContext.run(
                        lambda c: self._run_account(c, a, client),
                        description=f"{account.phone} 账号{self.verb}",
                        parent_ids=[account_ctx.id],
                    )
        finally:
            self._running.discard(account.phone)

    async def _run_account(self, ctx: RunContext, account: TelegramAccount, client):
        """子类运行体."""
        raise NotImplementedError

    async def run_all(self):
        """运行所有启用账号."""
        accounts = [a for a in config.telegram.account if a.enabled and getattr(a, self.feature)]
        tasks = []
        for account in accounts:
            task = self.start_account(account)
            if task:  # start_account 在账号已运行时返回 None
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)
