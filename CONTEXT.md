# 领域词汇表

本文件为架构评审提供统一的领域语言。术语按 `embykeeper/` 代码语义定义。

## 核心概念

- **保活 (keepalive)** — 周期性地向媒体服务器 (Emby/Subsonic) 模拟播放，保持账号活跃。核心概念，判定「保活是否成功」集中于 `embykeeper/emby/keepalive.py`。
- **播放会话 (playback session)** — 一次模拟播放的完整生命周期：读取播放信息 → 启动流 → 上报进度 → Pause/Stopped。集中于 `embykeeper/emby/playback.py`。
- **传输 (transport)** — 与媒体服务器的 HTTP 会话、重试/退避、流媒体、URL 解析与认证登录。集中于 `embykeeper/emby/transport.py`。
- **通知/回写校验 (notification / userdata verification)** — 播放前后对比 `UserData` 快照，判断媒体服务器是否回写了播放记录。`embykeeper/emby/notification.py`。
- **调度器 (scheduler)** — 按间隔天数与时间范围安排任务的执行器。`embykeeper/schedule.py`。
- **签到 (checkin)** — 在 Telegram 机器人站点执行每日签到。
- **监控 (monitor)** — 监控 Telegram 群组消息。
- **水群 (messager)** — 在 Telegram 站点自动发送消息。

## 管理器

- **保活调度管理器 (ScheduledKeepaliveManager)** — 统一 + 独立账号的保活调度骨架。`embykeeper/scheduled_manager.py`；Emby 与 Subsonic 各为其子类。
- **账号任务管理器 (TelegramTaskManager)** — 账号级任务启停与配置变更重排生命周期。`embykeeper/telegram/task_manager.py`；Monitor 与 Message 各为其子类。
- **签到管理器 (CheckinerManager)** — 按站点独立调度，形态与上述两族不同，不做强制合并。

## 插件

- **插件模块 (plugin module)** — 站点实现，按命名约定被 `dynamic.py` 发现，可显式声明 `__export__` 类列表与 `__ignore__` 标记。
