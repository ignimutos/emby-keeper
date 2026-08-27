from __future__ import annotations

import asyncio
from typing import List, Type

from loguru import logger

from embykeeper.schema import TelegramAccount
from embykeeper.config import config
from embykeeper.runinfo import RunContext

from .monitor import Monitor
from .dynamic import extract, get_cls, get_names
from .link import Link
from .pyrogram import Client
from .task_manager import TelegramTaskManager

logger = logger.bind(scheme="telechecker")


class MonitorManager(TelegramTaskManager):
    """监控管理器"""

    feature = "monitor"
    context_key = "monitor"
    verb = "监控"
    task_label = "群组监控任务"

    async def _run_account(self, ctx: RunContext, account: TelegramAccount, client: Client):
        """Run monitors for a single user"""
        log = logger.bind(username=client.me.full_name)

        # Get monitor classes based on account config or global config
        site = None
        if account.site and account.site.monitor is not None:
            site = account.site.monitor
        elif config.site and config.site.monitor is not None:
            site = config.site.monitor
        else:
            site = get_names("monitor")

        clses: List[Type[Monitor]] = extract(get_cls("monitor", names=site))

        if not clses:
            if site is not None:  # Only show warning if sites were specified but none were valid
                log.warning("没有任何有效监控站点, 监控将跳过.")
            return

        if not await Link(client).auth("monitor", log_func=log.error):
            return

        monitors = []
        names = []

        for cls in clses:
            if hasattr(cls, "templ_name"):
                site_name = cls.templ_name
            else:
                site_name = cls.__module__.rsplit(".", 1)[-1]
            site_ctx = RunContext.prepare(f"{site_name} 站点监控", parent_ids=ctx.id)
            monitor = cls(
                client,
                context=site_ctx,
                config=config.monitor.get_site_config(site_name),
            )
            monitors.append(monitor)
            names.append(monitor.name)

        if names:
            log.debug(f'已启用监控器: {", ".join(names)}')

        # Start all monitors concurrently
        await asyncio.gather(*[m._start() for m in monitors])
