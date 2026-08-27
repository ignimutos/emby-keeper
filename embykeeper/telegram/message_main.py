from __future__ import annotations

import asyncio
from typing import List, Type

from loguru import logger

from embykeeper.schema import TelegramAccount
from embykeeper.config import config
from embykeeper.runinfo import RunContext

from .messager import Messager
from .dynamic import extract, get_cls, get_names
from .link import Link
from .pyrogram import Client
from .task_manager import TelegramTaskManager

logger = logger.bind(scheme="telechecker")


class MessageManager(TelegramTaskManager):
    """消息管理器"""

    feature = "messager"
    context_key = "messager"
    verb = "自动水群"
    task_label = "自动水群任务"

    async def _run_account(self, ctx: RunContext, account: TelegramAccount, client: Client):
        """Run messagers for a single user"""
        log = logger.bind(username=client.me.full_name)

        # Get messager classes based on account config or global config
        site = None
        if account.site and account.site.messager is not None:
            site = account.site.messager
        elif config.site and config.site.messager is not None:
            site = config.site.messager
        else:
            site = get_names("messager")

        clses: List[Type[Messager]] = extract(get_cls("messager", names=site))

        if not clses:
            if site is not None:  # Only show warning if sites were specified but none were valid
                log.warning("没有任何有效自动水群站点, 自动水群将跳过.")
            return

        if not await Link(client).auth("messager", log_func=log.error):
            return

        messagers = []
        names = []

        for cls in clses:
            if hasattr(cls, "templ_name"):
                site_name = cls.templ_name
            else:
                site_name = cls.__module__.rsplit(".", 1)[-1]
            site_ctx = RunContext.prepare(f"{site_name} 站点自动水群", parent_ids=ctx.id)
            messager = cls(
                account=account,
                me=client.me,
                context=site_ctx,
                config=config.messager.get_site_config(site_name),
            )
            messagers.append(messager)
            names.append(messager.name)

        if names:
            log.debug(f'已启用自动水群器: {", ".join(names)}')

        # Start all messagers concurrently
        await asyncio.gather(*[m._start() for m in messagers])
