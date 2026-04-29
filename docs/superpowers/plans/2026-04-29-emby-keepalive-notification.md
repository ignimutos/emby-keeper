# Emby Keepalive Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace noisy schedule notifications with one per-account Emby keepalive result notification that shows Emby-verified playback details and the next local run time.

**Architecture:** Add a small `embykeeper/emby/notification.py` module for the result model and text formatting, teach `Emby.watch()` to return structured Emby-before/after playback data instead of a bare boolean, then update `EmbyManager` to send exactly one notification per account and keep schedule messages out of the notify channel.

**Tech Stack:** Python 3.13, pytest, loguru, Pydantic config models, existing Emby HTTP client in `curl_cffi`

---

## File structure

- Create: `embykeeper/emby/notification.py` — dataclasses and pure formatting helpers for per-account keepalive notifications.
- Modify: `embykeeper/emby/api.py:840-975` — capture baseline/latest Emby item state, compare `UserData`, and return `EmbyWatchResult`.
- Modify: `embykeeper/emby/main.py:85-160` — stop marking scheduler next-time messages as notify-worthy and expose per-account next-run lookup.
- Modify: `embykeeper/emby/main.py:233-318` — switch `_watch_main()` from boolean results to per-account structured results and emit one notify log per account.
- Modify: `embykeeper/config.py:469-479` — fix the `notifier.immediately` comment so it matches the real behavior.
- Create: `tests/test_emby_notification.py` — formatter, delta detection, and manager scheduling-notification tests.
- Modify: `tests/test_emby_api.py` — unit tests for the `Emby.watch()` return shape and Emby-verified success/failure detection.

### Task 1: Add the notification model and formatter

**Files:**
- Create: `embykeeper/emby/notification.py`
- Test: `tests/test_emby_notification.py`

- [ ] **Step 1: Write the failing notification tests**

```python
from datetime import datetime, timezone

from embykeeper.emby.notification import (
    EmbyPlaybackSnapshot,
    EmbyWatchResult,
    format_watch_notification,
    has_userdata_update,
)


def test_format_watch_notification_success():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=True,
        failure_stage=None,
        item_name="片名",
        item_id="abc123",
        before=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
            play_count=11,
            playback_position_ticks=0,
            runtime_ticks=18900000000,
        ),
        after=EmbyPlaybackSnapshot(
            last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
            play_count=12,
            playback_position_ticks=18360000000,
            runtime_ticks=18900000000,
        ),
        next_time=datetime(2026, 5, 11, 11, 18),
    )

    assert format_watch_notification(result) == (
        "Emby保活成功｜premises@墨云阁｜片名\n\n"
        "视频ID: abc123\n"
        "Emby记录时间: 2026-04-29 15:08:12\n"
        "回写进度: 97% / 1836s\n"
        "播放次数: 12\n"
        "下次保活: 2026-05-11 11:18"
    )


def test_format_watch_notification_failure_uses_fallbacks():
    result = EmbyWatchResult(
        account_spec="premises@墨云阁",
        success=False,
        failure_stage="播放后校验未生效",
        item_name=None,
        item_id=None,
        before=EmbyPlaybackSnapshot(),
        after=EmbyPlaybackSnapshot(),
        next_time=None,
    )

    assert format_watch_notification(result) == (
        "Emby保活失败｜premises@墨云阁｜未获取\n\n"
        "视频ID: 未获取\n"
        "Emby记录时间: 未更新\n"
        "回写进度: 未更新\n"
        "播放次数: 未更新\n"
        "失败阶段: 播放后校验未生效\n"
        "下次保活: 未计划"
    )


def test_has_userdata_update_accepts_newer_emby_fields():
    before = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
        play_count=11,
        playback_position_ticks=0,
        runtime_ticks=18900000000,
    )
    after = EmbyPlaybackSnapshot(
        last_played_date=datetime(2026, 4, 29, 15, 8, 12, tzinfo=timezone.utc),
        play_count=12,
        playback_position_ticks=18360000000,
        runtime_ticks=18900000000,
    )

    assert has_userdata_update(before, after) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_emby_notification.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'embykeeper.emby.notification'`

- [ ] **Step 3: Add the minimal notification module**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class EmbyPlaybackSnapshot:
    last_played_date: Optional[datetime] = None
    play_count: Optional[int] = None
    playback_position_ticks: Optional[int] = None
    runtime_ticks: Optional[int] = None


@dataclass(slots=True)
class EmbyWatchResult:
    account_spec: str
    success: bool
    failure_stage: Optional[str] = None
    item_name: Optional[str] = None
    item_id: Optional[str] = None
    before: EmbyPlaybackSnapshot = field(default_factory=EmbyPlaybackSnapshot)
    after: EmbyPlaybackSnapshot = field(default_factory=EmbyPlaybackSnapshot)
    next_time: Optional[datetime] = None


def has_userdata_update(before: EmbyPlaybackSnapshot, after: EmbyPlaybackSnapshot) -> bool:
    if before.last_played_date and after.last_played_date and after.last_played_date > before.last_played_date:
        return True
    if (before.play_count or 0) < (after.play_count or 0):
        return True
    if (before.playback_position_ticks or 0) < (after.playback_position_ticks or 0):
        return True
    return False


def _format_datetime(value: Optional[datetime], fallback: str) -> str:
    if not value:
        return fallback
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M:%S")


def _format_progress(snapshot: EmbyPlaybackSnapshot) -> str:
    ticks = snapshot.playback_position_ticks
    runtime = snapshot.runtime_ticks
    if not ticks or not runtime:
        return "未更新"
    seconds = round(ticks / 10_000_000)
    percent = round(ticks / runtime * 100)
    return f"{percent}% / {seconds}s"


def format_watch_notification(result: EmbyWatchResult) -> str:
    name = result.item_name or "未获取"
    item_id = result.item_id or "未获取"
    status = "Emby保活成功" if result.success else "Emby保活失败"
    lines = [
        f"{status}｜{result.account_spec}｜{name}",
        "",
        f"视频ID: {item_id}",
        f"Emby记录时间: {_format_datetime(result.after.last_played_date, '未更新')}",
        f"回写进度: {_format_progress(result.after)}",
        f"播放次数: {result.after.play_count if result.after.play_count is not None else '未更新'}",
    ]
    if not result.success:
        lines.append(f"失败阶段: {result.failure_stage or '未说明'}")
    lines.append(f"下次保活: {_format_datetime(result.next_time, '未计划')[:16] if result.next_time else '未计划'}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_emby_notification.py -v`
Expected: PASS with 3 passing tests

- [ ] **Step 5: Commit the formatter scaffold**

```bash
git add embykeeper/emby/notification.py tests/test_emby_notification.py
git commit -m "feat(emby): add keepalive notification formatter"
```

### Task 2: Return Emby-verified watch results from the API layer

**Files:**
- Modify: `embykeeper/emby/api.py:840-975`
- Modify: `tests/test_emby_api.py`

- [ ] **Step 1: Extend the API tests to demand structured watch results**

```python
import asyncio
from unittest.mock import AsyncMock

from embykeeper.emby.api import Emby
from embykeeper.emby.notification import EmbyWatchResult
from embykeeper.schema import EmbyAccount


def test_watch_returns_success_result_when_userdata_changes(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }
    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())
    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {
                    "LastPlayedDate": "2026-04-29T15:08:12Z",
                    "PlayCount": 12,
                    "PlaybackPositionTicks": 18360000000,
                },
            },
        ]
    )
    client.get_item = AsyncMock(side_effect=lambda _iid: next(responses))

    result = asyncio.run(client.watch())

    assert isinstance(result, EmbyWatchResult)
    assert result.success is True
    assert result.item_id == "abc123"
    assert result.after.play_count == 12


def test_watch_returns_failed_result_when_userdata_stays_stale(monkeypatch):
    account = EmbyAccount(url="https://example.com", username="user", password="pass", time=60)
    client = Emby(account)
    client.items = {
        "abc123": {"Id": "abc123", "Name": "片名", "MediaType": "Video", "RunTimeTicks": 18900000000}
    }
    monkeypatch.setattr("embykeeper.emby.api.random.shuffle", lambda items: None)
    monkeypatch.setattr("embykeeper.emby.api.random.uniform", lambda *args: 0)
    monkeypatch.setattr("embykeeper.emby.api.random.random", lambda: 0)
    monkeypatch.setattr("embykeeper.emby.api.asyncio.sleep", AsyncMock())
    client.play = AsyncMock(return_value=True)
    responses = iter(
        [
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
            {
                "Id": "abc123",
                "Name": "片名",
                "RunTimeTicks": 18900000000,
                "UserData": {"PlayCount": 11, "PlaybackPositionTicks": 0},
            },
        ]
    )
    client.get_item = AsyncMock(side_effect=lambda _iid: next(responses))

    result = asyncio.run(client.watch())

    assert result.success is False
    assert result.failure_stage == "播放后校验未生效"
    assert result.item_name == "片名"
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `uv run pytest tests/test_emby_api.py -v`
Expected: FAIL because `Emby.watch()` still returns `bool`

- [ ] **Step 3: Refactor `Emby.watch()` around before/after snapshots**

```python
from .notification import EmbyPlaybackSnapshot, EmbyWatchResult, has_userdata_update


class Emby:
    ...
    def _account_spec(self) -> str:
        return f"{self.a.username}@{self.a.name or self.a.url.host}"

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
    ) -> EmbyWatchResult:
        return EmbyWatchResult(
            account_spec=self._account_spec(),
            success=success,
            failure_stage=failure_stage,
            item_name=(after_item or {}).get("Name") or (before_item or {}).get("Name") or item_name,
            item_id=(after_item or {}).get("Id") or (before_item or {}).get("Id") or item_id,
            before=self._snapshot_from_item(before_item),
            after=self._snapshot_from_item(after_item),
        )

    async def watch(self) -> EmbyWatchResult:
        ...
        before_item = await self.get_item(iid)
        await self.play(item, time=play_time)
        await asyncio.sleep(random.random())
        after_item = await self.get_item(iid)
        before_snapshot = self._snapshot_from_item(before_item)
        after_snapshot = self._snapshot_from_item(after_item)
        updated = has_userdata_update(before_snapshot, after_snapshot)
        result = self._build_watch_result(
            success=updated,
            failure_stage=None if updated else "播放后校验未生效",
            item_name=item.get("Name"),
            item_id=iid,
            before_item=before_item,
            after_item=after_item,
        )
        if not updated:
            self.log.warning("播放后校验未生效, 保活失败.")
            return result
        self.log.info(f"[yellow]成功播放视频[/], 当前该视频播放 {after_snapshot.play_count} 次.")
        ...
        return result
```

Also update every early `return False` / `return True` branch in `watch()` to return an `EmbyWatchResult` with the correct `failure_stage`, preserving `item_name` / `item_id` whenever they are already known.

- [ ] **Step 4: Run the API tests to verify they pass**

Run: `uv run pytest tests/test_emby_api.py -v`
Expected: PASS with 3 passing tests

- [ ] **Step 5: Commit the API result refactor**

```bash
git add embykeeper/emby/api.py tests/test_emby_api.py
git commit -m "refactor(emby): return verified watch results"
```

### Task 3: Emit one notification per account and silence schedule pushes

**Files:**
- Modify: `embykeeper/emby/main.py:85-160`
- Modify: `embykeeper/emby/main.py:233-318`
- Modify: `embykeeper/config.py:469-479`
- Modify: `tests/test_emby_notification.py`

- [ ] **Step 1: Add manager-level tests for next-time lookup and non-notify scheduler logs**

```python
from datetime import datetime
from types import SimpleNamespace

from embykeeper.emby.main import EmbyManager
from embykeeper.schema import EmbyAccount


class StubLogger:
    def __init__(self):
        self.bound_calls = []
        self.messages = []

    def bind(self, **kwargs):
        self.bound_calls.append(kwargs)
        return self

    def info(self, message):
        self.messages.append(message)


def test_get_next_watch_time_prefers_account_scheduler():
    manager = EmbyManager()
    account = EmbyAccount(url="https://example.com", username="user", password="pass", name="墨云阁")
    manager._schedulers[manager.get_spec(account)] = SimpleNamespace(next_time=datetime(2026, 5, 11, 11, 18))

    assert manager._get_next_watch_time(account) == datetime(2026, 5, 11, 11, 18)


def test_schedule_messages_stay_out_of_notify_channel(monkeypatch):
    stub = StubLogger()
    monkeypatch.setattr("embykeeper.emby.main.logger", stub)
    manager = EmbyManager()
    account = EmbyAccount(url="https://example.com", username="user", password="pass", name="墨云阁")
    scheduler = manager.schedule_independent_account(account)

    scheduler.on_next_time(datetime(2026, 5, 11, 11, 18))

    assert stub.bound_calls == []
    assert stub.messages == ["下一次 Emby 账号 (user@墨云阁) 的保活将在 05-11 11:18 AM 进行."]
```

- [ ] **Step 2: Run the manager tests to verify they fail**

Run: `uv run pytest tests/test_emby_notification.py -k "next_watch_time or schedule_messages" -v`
Expected: FAIL because `_get_next_watch_time()` does not exist and scheduler logs still use `bind(log=True)`

- [ ] **Step 3: Update the manager, notifications, and config comment**

```python
def make_on_next_time(spec):
    return lambda t: logger.info(f"下一次 Emby 账号 ({spec}) 的保活将在 {t.strftime('%m-%d %H:%M %p')} 进行.")


def _get_next_watch_time(self, account: EmbyAccount) -> Optional[datetime]:
    spec = self.get_spec(account)
    scheduler = self._schedulers.get(spec)
    if scheduler:
        return scheduler.next_time
    scheduler = self._schedulers.get("unified")
    return scheduler.next_time if scheduler else None


async def _watch_main(self, accounts: List[EmbyAccount], instant: bool = False):
    ...
    async def watch_wrapper(account: EmbyAccount, sem):
        ...
        return account, await emby.watch()

    results = await asyncio.gather(*tasks)
    failed_accounts = []
    successful_accounts = []
    for account, result in results:
        result.next_time = self._get_next_watch_time(account)
        logger.bind(log=True).info(format_watch_notification(result))
        if result.success:
            successful_accounts.append(self.get_spec(account))
        else:
            failed_accounts.append(self.get_spec(account))
```

```python
c.add(
    comment(
        "默认情况下, 日志推送将由 @embykeeper_bot 按其设置统一推送, 设置为 true 以立刻推送"
    )
)
```

Keep the aggregate success/failure log lines for local console visibility, but remove the old `self.log.bind(log=True).info(...)` success push inside `Emby.watch()` so each account only emits one final notification.

- [ ] **Step 4: Run the focused regression tests**

Run: `uv run pytest tests/test_emby_notification.py tests/test_emby_api.py tests/test_cli.py -v`
Expected: PASS with all selected tests green

- [ ] **Step 5: Commit the end-to-end notification behavior**

```bash
git add embykeeper/config.py embykeeper/emby/main.py embykeeper/emby/notification.py tests/test_emby_notification.py
git commit -m "feat(emby): send per-account keepalive results"
```

## Self-review

- **Spec coverage:**
  - Per-account, per-run notifications: Task 3
  - Emby-first result interpretation: Task 2
  - Success/failure message format with fallbacks: Task 1
  - Remove schedule pushes from notify channel: Task 3
  - Fix `notifier.immediately` comment: Task 3
- **Placeholder scan:** No `TODO` / `TBD` / “similar to above” references remain.
- **Type consistency:** The plan uses one shared model (`EmbyWatchResult`) and one shared snapshot type (`EmbyPlaybackSnapshot`) across tests, API code, and manager code.
