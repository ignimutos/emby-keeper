from dataclasses import dataclass, field
from datetime import datetime, timezone


FALLBACK_ITEM = "未获取"
FALLBACK_UPDATE = "未更新"
FALLBACK_NEXT_TIME = "未计划"


@dataclass(slots=True)
class EmbyPlaybackSnapshot:
    last_played_date: datetime | None = None
    play_count: int | None = None
    playback_position_ticks: int | None = None
    runtime_ticks: int | None = None


@dataclass(slots=True)
class EmbyWatchResult:
    account_spec: str
    success: bool
    failure_stage: str | None = None
    item_name: str | None = None
    item_id: str | None = None
    before: EmbyPlaybackSnapshot = field(default_factory=EmbyPlaybackSnapshot)
    after: EmbyPlaybackSnapshot = field(default_factory=EmbyPlaybackSnapshot)
    next_time: datetime | None = None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def has_userdata_update(before: EmbyPlaybackSnapshot, after: EmbyPlaybackSnapshot) -> bool:
    if before.last_played_date and after.last_played_date:
        before_normalized = _normalize_datetime(before.last_played_date)
        after_normalized = _normalize_datetime(after.last_played_date)
        if after_normalized > before_normalized:
            return True
    if (after.play_count or 0) > (before.play_count or 0):
        return True
    if (after.playback_position_ticks or 0) > (before.playback_position_ticks or 0):
        return True
    return False


def _format_datetime(value: datetime | None, fallback: str, with_seconds: bool = True) -> str:
    if value is None:
        return fallback
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime(fmt)


def _format_progress(snapshot: EmbyPlaybackSnapshot) -> str:
    if not snapshot.playback_position_ticks or not snapshot.runtime_ticks:
        return FALLBACK_UPDATE

    seconds = round(snapshot.playback_position_ticks / 10_000_000)
    percent = round(snapshot.playback_position_ticks / snapshot.runtime_ticks * 100)
    return f"{percent}% / {seconds}s"


def format_watch_notification(result: EmbyWatchResult) -> str:
    status = "Emby保活成功" if result.success else "Emby保活失败"
    lines = [
        f"{status}｜{result.account_spec}｜{result.item_name or FALLBACK_ITEM}",
        "",
        f"视频ID: {result.item_id or FALLBACK_ITEM}",
        f"Emby记录时间: {_format_datetime(result.after.last_played_date, FALLBACK_UPDATE)}",
        f"回写进度: {_format_progress(result.after)}",
        f"播放次数: {result.after.play_count if result.after.play_count is not None else FALLBACK_UPDATE}",
    ]

    if not result.success:
        lines.append(f"失败阶段: {result.failure_stage or '未说明'}")

    lines.append(
        f"下次保活: {_format_datetime(result.next_time, FALLBACK_NEXT_TIME, with_seconds=False)}"
    )
    return "\n".join(lines)
