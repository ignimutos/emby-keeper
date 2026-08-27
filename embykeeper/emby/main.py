import asyncio
import random
from typing import List, Dict, Set, Optional
from urllib.parse import parse_qs, urlparse
from datetime import datetime

from loguru import logger

from embykeeper.config import config
from embykeeper.utils import redact_headers, show_exception, truncate_str
from embykeeper.runinfo import RunContext, RunStatus
from embykeeper.var import console
from embykeeper.schema import EmbyAccount

from embykeeper.scheduled_manager import ScheduledKeepaliveManager

from .api import Emby, EmbyPlayError, EmbyConnectError, EmbyRequestError, EmbyError
from .notification import EmbyWatchResult, format_watch_notification

logger = logger.bind(scheme="embywatcher")


class EmbyManager(ScheduledKeepaliveManager):
    """Emby 保活管理器. 调度骨架见 scheduled_manager.ScheduledKeepaliveManager."""

    section = "emby"
    title = "Emby"

    def __init__(self):
        super().__init__()
        self._selected_account_names: Optional[Set[str]] = None

    def _is_account_selected(self, account: EmbyAccount) -> bool:
        if self._selected_account_names is None:
            return True
        return bool(account.name and account.name in self._selected_account_names)

    async def _run_accounts(self, accounts: List[EmbyAccount], instant: bool = False):
        return await self._watch_main(accounts, instant)

    async def schedule_accounts(self, accounts: List[EmbyAccount]):
        self._selected_account_names = {account.name for account in accounts if account.name}
        return await self._schedule_accounts(accounts)

    async def schedule_all(self, instant: bool = False):
        self._selected_account_names = None
        return await self._schedule_accounts(config.emby.account)

    async def play_url(self, url: str):
        parsed = urlparse(url)

        fragment_parts = parsed.fragment.split("?", 1)
        if len(fragment_parts) > 1:
            params = parse_qs(fragment_parts[1])
        else:
            params = {}

        if not params.get("id"):
            logger.error(
                "无效的 URL 格式, 无法解析视频 ID. 应为类似:\nhttps://example.com/web/#/details?id=xxx&serverId=xxx"
            )
            return False

        iid = params["id"][0]

        # 在config中查找匹配的emby配置
        account = None
        for a in config.emby.account:
            if a.url.host == parsed.netloc:
                account = a
                break

        if not account:
            logger.error(f"在配置中未找到匹配的 Emby 服务器: {parsed.netloc}")
            return False

        ctx = RunContext.prepare(description="播放指定 URL 视频")
        ctx.start(RunStatus.INITIALIZING)

        emby = Emby(account)
        try:
            if not await emby.login():
                return ctx.finish(RunStatus.FAIL, "登陆失败")
            emby.log.info("使用以下 Headers:")
            console.rule("Headers")
            headers = redact_headers(emby.build_headers())
            for k, v in headers.items():
                console.print(f"{k.title()}: {v}")
            console.rule()
            item = await emby.get_item(iid)
            if not item:
                raise ValueError(f"无法找到 ID 为 {iid} 的视频")
            name = truncate_str(item.get("Name", "(未命名视频)"), 10)
            emby.log.info(f'10 秒后, 将开始播放该视频 300 秒: "{name}"')
            await asyncio.sleep(1)
            emby.log.info(f'开始播放视频 300 秒: "{name}"')
            try:
                await emby.play(item, time=300)
            except EmbyPlayError as e:
                emby.log.error(f"播放失败: {e}")
                return ctx.finish(RunStatus.FAIL, "播放失败")
            return ctx.finish(RunStatus.SUCCESS, "播放成功")
        except EmbyConnectError as e:
            if emby.proxy:
                emby.log.error(f"无法连接到服务器, 可能是您的代理服务器设置错误或无法连通: {e}")
            else:
                emby.log.error(f"无法连接到服务器, 可能是您没有使用代理: {e}")
            return ctx.finish(RunStatus.FAIL, "连接失败")
        except EmbyRequestError as e:
            emby.log.error(f"服务器异常: {e}")
            return ctx.finish(RunStatus.FAIL, "服务器异常")
        except Exception as e:
            emby.log.error("播放视频时发生错误, 播放失败.")
            show_exception(e, regular=False)
            return ctx.finish(RunStatus.ERROR, "异常错误")

    def _get_next_watch_time(self, account: EmbyAccount) -> Optional[datetime]:
        spec = self.get_spec(account)
        scheduler = self._schedulers.get(spec)
        if scheduler:
            return getattr(scheduler, "notification_next_time", scheduler.next_time)
        scheduler = self._schedulers.get("unified")
        if scheduler:
            return getattr(scheduler, "notification_next_time", scheduler.next_time)
        return None

    async def _watch_main(self, accounts: List[EmbyAccount], instant: bool = False):
        if not accounts:
            return None
        logger.info("开始执行 Emby 保活.")
        sem = asyncio.Semaphore(config.emby.concurrency or 100000)

        ctx = RunContext.prepare(description="使用全局设置的 Emby 统一保活")
        ctx.start(RunStatus.INITIALIZING)

        def build_failed_result(account: EmbyAccount, stage: str, warning: str = None) -> EmbyWatchResult:
            return EmbyWatchResult(
                account_spec=self.get_spec(account), success=False, failure_stage=stage, warning=warning
            )

        async def watch_wrapper(account: EmbyAccount, sem):
            async with sem:
                try:
                    emby = Emby(account)
                except Exception as e:
                    logger.error(f"初始化失败: {e}")
                    show_exception(e, regular=False)
                    return account, build_failed_result(account, "开始播放失败")
                if not instant:
                    wait = random.uniform(180, 360)
                    emby.log.info(f"播放视频前随机等待 {wait:.0f} 秒.")
                    await asyncio.sleep(wait)
                try:
                    if not account.play_id:
                        emby.log.info(f"正在登陆并获取首页视频项目.")
                        if not emby.user_id:
                            if not await emby.login():
                                emby.log.warning(f"保活失败: 无法登陆.")
                                return account, build_failed_result(account, "登录失败")
                        await emby.load_main_page()
                        if not emby.items:
                            emby.log.warning("保活失败: 无法获取首页中的视频项目")
                            return account, build_failed_result(account, "获取视频失败")
                        else:
                            emby.log.info(f"成功登陆, 获取了 {len(emby.items)} 个首页视频项目.")
                        await asyncio.sleep(random.uniform(2, 5))
                    else:
                        emby.log.info(f"正在登陆并播放您指定的视频, ID 为 {account.play_id}.")
                        if not emby.user_id:
                            if not await emby.login():
                                emby.log.warning(f"保活失败: 无法登陆.")
                                return account, build_failed_result(account, "登录失败")
                        item = await emby.get_item(account.play_id)
                        if "Id" not in item:
                            emby.log.warning("保活失败: 无法获取视频项目")
                            return account, build_failed_result(account, "获取视频失败")
                        else:
                            emby.items[item["Id"]] = item
                            emby.log.info(f"成功登陆, 获取了视频项目.")
                        await asyncio.sleep(random.uniform(2, 5))
                    return account, await emby.watch()
                except EmbyError as e:
                    emby.log.warning(f"保活失败: {e}.")
                    return account, build_failed_result(account, "获取视频失败", warning=str(e))
                except Exception as e:
                    emby.log.warning(f"保活失败: {e}")
                    show_exception(e, regular=False)
                    return account, build_failed_result(account, "开始播放失败", warning=str(e))

        tasks = [asyncio.create_task(watch_wrapper(account, sem)) for account in accounts if account.enabled]
        if not tasks:
            logger.info("没有需要执行的 Emby 保活账号")
            return ctx.finish(RunStatus.SUCCESS, "无可执行账号")

        failed_accounts = []
        successful_accounts = []
        completed_results = []
        for task in asyncio.as_completed(tasks):
            account, result = await task
            completed_results.append((account, result))
            if result.success:
                successful_accounts.append(self.get_spec(account))
            else:
                failed_accounts.append(self.get_spec(account))
        for account, result in completed_results:
            result.next_time = self._get_next_watch_time(account)
            logger.bind(log=True).info(format_watch_notification(result))
        fails = len(failed_accounts)

        if fails:
            if len(tasks) == 1:
                logger.error(f"保活失败: {', '.join(failed_accounts)}")
            else:
                logger.error(f"保活失败 ({fails}/{len(tasks)}): {', '.join(failed_accounts)}")
            return ctx.finish(RunStatus.FAIL, f"保活失败")
        if len(tasks) == 1:
            logger.info(f"保活成功: {', '.join(successful_accounts)}.")
        else:
            logger.info(f"保活成功 ({len(tasks)}/{len(tasks)}): {', '.join(successful_accounts)}.")
        return ctx.finish(RunStatus.SUCCESS, f"保活成功")
