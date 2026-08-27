# 领域词汇表

本文件为架构评审提供统一的领域语言。术语按 `embykeeper/` 代码语义定义。

## 核心概念

- **保活 (keepalive)** — 周期性地向 Emby 服务器模拟播放，保持账号活跃。核心概念，判定「保活是否成功」集中于 `embykeeper/emby/keepalive.py`。
- **播放会话 (playback session)** — 一次模拟播放的完整生命周期：读取播放信息 → 启动流 → 上报进度 → Pause/Stopped。集中于 `embykeeper/emby/playback.py`。
- **传输 (transport)** — 与 Emby 服务器的 HTTP 会话、重试/退避、流媒体、URL 解析与认证登录。集中于 `embykeeper/emby/transport.py`。
- **通知/回写校验 (notification / userdata verification)** — 播放前后对比 `UserData` 快照，判断媒体服务器是否回写了播放记录。`embykeeper/emby/notification.py`。
- **调度器 (scheduler)** — 按间隔天数与时间范围安排任务的执行器。`embykeeper/schedule.py`。

## 管理器

- **保活调度管理器 (ScheduledKeepaliveManager)** — 统一 + 独立账号的保活调度骨架。`embykeeper/scheduled_manager.py`；`EmbyManager` 为其子类。
