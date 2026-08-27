"""Keepalive 运行决策树.

把「保活是否成功」这一核心概念收拢到单一模块: 采集前后快照 → 校验是否回写
→ 失败分级 → 构建结果。Emby 类通过 EmbyClient 协约访问传输与播放, 本模块
不再依赖具体传输实现。

为什么拆出来: 过去判定散落在 api.py(决策树)、notification.py(判定谓词)、
main.py(下次时间注入) 三处, 定位「保活成功了吗」需要跨文件跳跃。此处是
该概念的单一落点 (locality)。
"""

import asyncio
import random
from datetime import datetime
from typing import Iterable, Optional, Union

from embykeeper.schema import EmbyAccount
from embykeeper.utils import show_exception, truncate_str

from embykeeper.emby.errors import EmbyError, EmbyPlayError, EmbyStoppedReportError
from embykeeper.emby.notification import EmbyPlaybackSnapshot, EmbyWatchResult, has_userdata_update


class EmbyClient:
    """KeepaliveRun 需要的最小协约 (port)。

    Emby(emby/api.py) 实现之, 使决策树与传输/播放实现解耦, 测试可注入假实现。
    """

    a: EmbyAccount
    items: dict
    log: object

    def _configured_watch_time(self): ...

    async def get_item(self, iid, **kw) -> dict: ...

    async def get_resume_item(self, iid): ...

    async def play(self, item: Union[dict, int], time: float = 10): ...


class KeepaliveRun:
    """一次保活运行的决策树: 播放候选视频直至满足账号时长要求."""

    def __init__(self, client: EmbyClient, max_retries: int = 0):
        self.client = client
        self._a = client.a
        self.max_retries = max_retries

    # --- 纯结果构造 (方便单测直接喂快照) ---

    def _account_spec(self) -> str:
        a = self._a
        return f"{a.username}@{a.name or a.url.host}"

    @staticmethod
    def parse_date(date_str: str) -> Optional[datetime]:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def _snapshot_from_item(self, item: Optional[dict]) -> EmbyPlaybackSnapshot:
        userdata = (item or {}).get("UserData", {})
        return EmbyPlaybackSnapshot(
            last_played_date=self.parse_date(userdata.get("LastPlayedDate")),
            play_count=userdata.get("PlayCount"),
            playback_position_ticks=userdata.get("PlaybackPositionTicks"),
            runtime_ticks=(item or {}).get("RunTimeTicks"),
        )

    def _build_watch_result(
        self,
        *,
        success: bool,
        failure_stage: Optional[str],
        item_name: Optional[str],
        item_id: Optional[str],
        before_item: Optional[dict],
        after_item: Optional[dict],
        warning: Optional[str] = None,
    ) -> EmbyWatchResult:
        before = before_item or {}
        after = after_item or {}
        return EmbyWatchResult(
            account_spec=self._account_spec(),
            success=success,
            failure_stage=failure_stage,
            warning=warning,
            item_name=after.get("Name") or before.get("Name") or item_name,
            item_id=after.get("Id") or before.get("Id") or item_id,
            before=self._snapshot_from_item(before_item),
            after=self._snapshot_from_item(after_item),
        )

    def _format_failed_reason_summary(self, failed_reasons: dict) -> str:
        reasons = []
        if failed_reasons["invalid"]:
            reasons.append(f"{failed_reasons['invalid']} 个视频信息无效")
        if failed_reasons["no_length"]:
            reasons.append(
                f"{failed_reasons['no_length']} 个视频无法获取时长 (allow_stream={self._a.allow_stream})"
            )
        if failed_reasons["wrong_type"]:
            reasons.append(f"{failed_reasons['wrong_type']} 个非视频项目")
        if failed_reasons["short_length"]:
            reasons.append(f"{failed_reasons['short_length']} 个视频时长不足 (未开启 allow_multiple)")
        return ", ".join(reasons) if reasons else "未记录到候选过滤原因"

    async def run(self) -> EmbyWatchResult:
        client = self.client
        a = self._a
        log = client.log

        configured_time = client._configured_watch_time()
        try:
            if isinstance(configured_time, Iterable):
                req_time = random.uniform(*configured_time)
            else:
                req_time = configured_time
        except TypeError:
            log.warning(f"无法解析 time 配置, 请检查配置: {configured_time} (应该为数字或两个数字的数组).")
            return self._build_watch_result(
                success=False,
                failure_stage="配置错误",
                item_name=None,
                item_id=None,
                before_item=None,
                after_item=None,
            )
        msg = " (允许播放多个)" if a.allow_multiple else ""
        msg = f"开始播放视频{msg}, 共需播放 {req_time:.0f} 秒."
        log.info(msg)

        played_time = 0
        last_played_time = 0
        played_videos = 0
        retry = 0
        failed_items = []
        failed_reasons = {"invalid": 0, "no_length": 0, "wrong_type": 0, "short_length": 0}

        while True:
            shuffled_items = list(client.items.items())
            random.shuffle(shuffled_items)

            for iid, item in shuffled_items:
                try:
                    if iid in failed_items:
                        failed_reasons["invalid"] += 1
                        continue
                except KeyError:
                    continue
                media_type = item.get("MediaType", None)
                if not media_type == "Video":
                    failed_reasons["wrong_type"] += 1
                    continue
                total_ticks = item.get("RunTimeTicks", None)
                if not total_ticks:
                    if a.allow_stream:
                        total_ticks = min(req_time, random.randint(480, 720)) * 10000000
                    else:
                        failed_reasons["no_length"] += 1
                        continue
                total_time = total_ticks / 10000000
                if req_time - played_time > total_time:
                    if not a.allow_multiple:
                        failed_reasons["short_length"] += 1
                        failed_items.append(iid)
                        continue
                    play_time = total_time
                else:
                    play_time = max(req_time - played_time, 10)
                name = truncate_str(item.get("Name", "(未命名视频)"), 10)
                log.info(f'开始播放 "{name}" ({play_time:.0f} 秒).')
                log.debug(f"视频 ID: {iid}.")
                while True:
                    before_item = None
                    after_item = None
                    stopped_report_error = None
                    try:
                        try:
                            before_item = await client.get_item(iid)
                        except Exception as e:
                            log.warning(f"播放前无法读取结果, 保活失败: {e}.")
                            return self._build_watch_result(
                                success=False,
                                failure_stage="结果读取失败",
                                item_name=item.get("Name"),
                                item_id=iid,
                                before_item=None,
                                after_item=None,
                            )
                        try:
                            await client.play(item, time=play_time)
                        except EmbyStoppedReportError as e:
                            stopped_report_error = e
                        await asyncio.sleep(random.random())
                        try:
                            after_item = await client.get_item(iid)
                        except Exception as e:
                            log.warning(f"播放后无法读取结果, 保活失败: {e}.")
                            return self._build_watch_result(
                                success=False,
                                failure_stage="结果读取失败",
                                item_name=item.get("Name"),
                                item_id=iid,
                                before_item=before_item,
                                after_item=None,
                            )
                        before_snapshot = self._snapshot_from_item(before_item)
                        after_snapshot = self._snapshot_from_item(after_item)
                        updated = has_userdata_update(before_snapshot, after_snapshot)
                        if not updated:
                            resume_item = await client.get_resume_item(iid)
                            if resume_item:
                                resume_snapshot = self._snapshot_from_item(resume_item)
                                if has_userdata_update(before_snapshot, resume_snapshot):
                                    after_item = resume_item
                                    after_snapshot = resume_snapshot
                                    updated = True
                        warning = None
                        if updated and stopped_report_error:
                            warning = "Stopped 上报失败，但 Emby 已回写播放记录: " f"{stopped_report_error}"
                        result = self._build_watch_result(
                            success=updated,
                            failure_stage=None if updated else "播放后校验未生效",
                            item_name=item.get("Name"),
                            item_id=iid,
                            before_item=before_item,
                            after_item=after_item,
                            warning=warning,
                        )
                        if not updated:
                            if stopped_report_error:
                                raise stopped_report_error
                            log.warning("播放后校验未生效, 保活失败.")
                            return result
                        if result.warning:
                            log.warning(result.warning)
                        if after_snapshot.play_count is not None:
                            log.info(
                                f"[yellow]成功播放视频[/], 当前该视频播放 {after_snapshot.play_count} 次."
                            )
                        else:
                            log.info("[yellow]成功播放视频[/], Emby 已回写播放记录.")
                        played_videos += 1
                        played_time += play_time
                        if played_time >= req_time - 1:
                            log.info(f"保活成功, 共播放 {played_videos} 个视频.")
                            return result
                        else:
                            log.info(f"还需播放 {req_time - played_time:.0f} 秒.")
                            rt = random.uniform(5, 15)
                            log.info(f"等待 {rt:.0f} 秒后播放下一个.")
                            await asyncio.sleep(rt)
                            break
                    except EmbyError as e:
                        retry += 1
                        if retry > self.max_retries:
                            log.warning(f"超过最大重试次数, 保活失败: {e}.")
                            return self._build_watch_result(
                                success=False,
                                failure_stage="播放中断" if isinstance(e, EmbyPlayError) else "开始播放失败",
                                item_name=item.get("Name"),
                                item_id=iid,
                                before_item=before_item,
                                after_item=after_item,
                            )
                        else:
                            rt = random.uniform(30, 60)
                            if isinstance(e, EmbyPlayError):
                                log.info(f"播放错误, 等待 {rt:.0f} 秒后重试: {e}.")
                            else:
                                log.info(f"连接失败, 等待 {rt:.0f} 秒后重试: {e}.")
                            await asyncio.sleep(rt)
                    except Exception as e:
                        log.warning(f"发生错误, 保活失败.")
                        show_exception(e, regular=False)
                        return self._build_watch_result(
                            success=False,
                            failure_stage="播放中断",
                            item_name=item.get("Name"),
                            item_id=iid,
                            before_item=before_item,
                            after_item=after_item,
                        )
            else:
                if len(failed_items) == len(client.items):
                    summary = self._format_failed_reason_summary(failed_reasons)
                    log.warning(f"所有视频均不符合要求, 保活失败. 其中: {summary}")
                    return self._build_watch_result(
                        success=False,
                        failure_stage="获取视频失败",
                        item_name=None,
                        item_id=None,
                        before_item=None,
                        after_item=None,
                    )
                elif played_time > last_played_time:
                    last_played_time = played_time
                    continue
                else:
                    summary = self._format_failed_reason_summary(failed_reasons)
                    log.warning(f"由于没有成功播放视频, 保活失败. 候选过滤统计: {summary}")
                    return self._build_watch_result(
                        success=False,
                        failure_stage="获取视频失败",
                        item_name=None,
                        item_id=None,
                        before_item=None,
                        after_item=None,
                    )
