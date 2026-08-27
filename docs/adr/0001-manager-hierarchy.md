# ADR-0001: 管理器拆为两个族，而非单基类

## 状态

已接受 (2026-08-27)

## 背景

`/improve-codebase-architecture` 评审曾提出「五个调度管理器共用一个基类」。实现前通读五个管理器后认定：它们分属两个真正相同的族，加上一个形态不同的例外，强行单基类是过度抽象。

## 决策

- **账号任务族**：`MonitorManager` 与 `MessageManager` 是逐行相同的复制，合并到 `TelegramTaskManager`（`embykeeper/telegram/task_manager.py`），子类仅保留站点解析与运行体。
- **保活调度族**：`EmbyManager` 与 `SubsonicManager` 共享统一/独立账号的调度骨架，合并到 `ScheduledKeepaliveManager`（`embykeeper/scheduled_manager.py`）。同时修复 Subsonic 的 `_watch_main` AttributeError 与 `config.emby.*` 配置泄漏。
- **例外**：`CheckinerManager` 按站点独立调度（每站 time_range、站点级重排、`schedule_site`），形态与上述两族不同，**不**并入任何基类。

## 理由

- 账号任务族与保活调度族的生命周期不同：前者是「每账号一个常驻任务 + 配置变更启停」，后者是「Scheduler 驱动的周期执行 + 独立账号直管」。
- 单基类需要为 Checkiner 引入站点级调度钩子，会让基类接口与实现几乎等宽（浅），违背深化目标。

## 后果

- 未来新增「保活类」管理器（如另一个媒体服务器）时继承 `ScheduledKeepaliveManager` 即可获得调度骨架与配置变更反应。
- 未来新增「账号任务类」管理器时继承 `TelegramTaskManager` 即可获得账号生命周期。
- 评审不应再建议把 Checkiner 或跨族管理器合并进同一基类。
