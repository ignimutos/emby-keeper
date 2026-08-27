from __future__ import annotations

import asyncio
import random
from typing import List
from loguru import logger

from embykeeper.config import config
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.schema import SubsonicAccount
from embykeeper.utils import show_exception

from embykeeper.scheduled_manager import ScheduledKeepaliveManager

from .player import SubsonicPlayer

logger = logger.bind(scheme="subsonic")


class SubsonicManager(ScheduledKeepaliveManager):
    """Subsonic 保活管理器. 调度骨架见 scheduled_manager.ScheduledKeepaliveManager.

    修复: 独立账号调度曾错误调用 _watch_main (AttributeError), 且读取
    config.emby.* 配置段 (配置泄漏)。现统一走 _listen_main 与 config.subsonic.*。
    """

    section = "subsonic"
    title = "Subsonic"

    async def _run_accounts(self, accounts: List[SubsonicAccount], instant: bool = False):
        return await self._listen_main(accounts, instant)

    async def _listen_main(self, accounts: List[SubsonicAccount], instant: bool = False):
        if not accounts:
            return None
        logger.info("开始执行 Subsonic 保活.")
        tasks = []
        sem = asyncio.Semaphore(config.subsonic.concurrency or 100000)

        ctx = RunContext.prepare(description="使用全局设置的 Subsonic 统一保活")
        ctx.start(RunStatus.INITIALIZING)

        async def watch_wrapper(account: SubsonicAccount, sem):
            async with sem:
                try:
                    player = SubsonicPlayer(account)
                except Exception as e:
                    logger.error(f"初始化失败: {e}")
                    show_exception(e, regular=False)
                    return account, False
                if not instant:
                    wait = random.uniform(180, 360)
                    player.log.info(f"播放音频前随机等待 {wait:.0f} 秒.")
                    await asyncio.sleep(wait)
                try:
                    subsonic = await player.login()
                    if not subsonic:
                        return account, False
                    await asyncio.sleep(random.uniform(2, 5))
                    return account, await player.play(subsonic)
                except Exception as e:
                    player.log.error(f"播放任务执行失败: {e}")
                    show_exception(e, regular=False)
                    return account, False

        for account in accounts:
            if account.enabled:
                tasks.append(watch_wrapper(account, sem))

        failed_accounts = []
        successful_accounts = []
        results = await asyncio.gather(*tasks)
        for a, success in results:
            if success:
                successful_accounts.append(self.get_spec(a))
            else:
                failed_accounts.append(self.get_spec(a))
        fails = len(failed_accounts)

        if fails:
            if len(accounts) == 1:
                logger.error(f"保活失败: {', '.join(failed_accounts)}")
            else:
                logger.error(f"保活失败 ({fails}/{len(tasks)}): {', '.join(failed_accounts)}")
            return ctx.finish(RunStatus.FAIL, f"保活失败")
        if len(accounts) == 1:
            logger.bind(log=True).info(f"保活成功: {', '.join(successful_accounts)}.")
        else:
            logger.bind(log=True).info(
                f"保活成功 ({len(tasks)}/{len(tasks)}): {', '.join(successful_accounts)}."
            )
        return ctx.finish(RunStatus.SUCCESS, f"保活成功")
