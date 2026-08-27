# EmbyKeeper (lite)

Emby 服务器保号 / 保活工具（fork 精简版）。通过模拟观看视频，保持 Emby 账号活跃。

本分支仅保留 **Emby 保活** 功能，其余全部移除（Telegram 签到/监控/水群、Subsonic、Web 控制台、Cloudflare 自动解析、公共部署等）。

## 功能

- 按计划定期模拟播放视频保活（全局调度 + 每账号独立调度）
- 账号指纹（client / device / device_id / client_version / useragent）全局默认 + 每账号覆盖，自动缓存
- 代理支持（socks5 / http）
- HTTPS 证书校验开关 `verify`（默认关闭，自签证书服务器可直接用）
- 保活结果通过 **Apprise** 推送（telegram / email / webhook / ntfy 等）
- Emby 登录凭据以账号密码派生密钥加密后缓存（防缓存文件单独泄露）
- 调试命令：`--play-url` 手动播放指定视频、`--clean` 清理缓存、`--debug-notify` 测试推送

## 安装

```bash
uv sync            # 或 make develop
```

## 快速开始

```bash
# 首次运行会生成 config.toml 模板，编辑后填入账号信息
make run
# 或
uv run embykeeper -i          # 启动并立即执行一次保活
uv run embykeeper -e --once   # 仅执行一次
```

## 配置

示例配置：`config.example.toml`（或 `embykeeper --example-config`）。

```toml
[emby]
time_range = "<11:00AM,11:00PM>"   # 每日保活时间范围
interval_days = "<7,12>"           # 每隔 7~12 天保活一次
concurrency = 1                    # 同时保活的最大账号数
time = [300, 600]                  # 模拟观看时长范围 (秒)
verify = false                     # 是否校验 HTTPS 证书
client = "Hills"                   # 全局指纹默认值
# device / device_id: 默认不配置, 程序自动生成并缓存 (每台安装随机)
client_version = "1.6.1"
useragent = "Hills/1.6.1 (android; 15)"

[[emby.account]]
url = "https://example.com:443"
username = "user"
password = "pass"
# 可选: 覆盖全局指纹 / 时长 / 时间范围 / 证书校验
# time = [300, 600]
# interval_days = 7
# time_range = "<8:00AM,10:00AM>"
# verify = true

[proxy]                            # 可选
hostname = "127.0.0.1"
port = 1080
scheme = "socks5"                  # 或 http

[notifier]                         # 可选, apprise 推送
enabled = true
method = "apprise"
apprise_uri = "tgram://bot_token/chat_id"
```

### 证书校验

`verify` 默认 `false`，即不校验证书（多数 Emby/自建服务器为自签证书）。如服务器证书可信且希望校验证书，设 `[emby] verify = true`；每账号可用 `[emby.account]` 的 `verify` 单独覆盖。

### 通知

保活成功/失败会通过 Apprise 推送。`apprise_uri` 支持 100+ 服务，参见 [Apprise 文档](https://github.com/caronc/apprise)。用 `embykeeper --debug-notify` 测试推送。

### 凭据缓存加密

Emby 登录 token 在 `cache.json` 中以账号密码派生的密钥加密存储（scrypt + Fernet）。缓存文件单独泄露（如误提交到 git）时无法直接读取；拿到配置文件（含密码）的攻击者仍可解密。旧版本明文缓存会自动兼容读取，下次登录后重写为密文。

## CLI

```
embykeeper [OPTIONS] [CONFIG_FILE]

  -e, --emby            启用 Emby 保活
      --emby-account    仅执行指定名称的账号 (逗号分隔 / 重复传入)
  -i, --instant         启动时立即执行一次
  -o, --once            只执行一次, 不进入计划模式
  -B, --basedir         数据目录 (账号文件/缓存)
  -p, --play-url        手动播放指定视频 URL
      --clean           清理缓存
  -d, --debug           调试日志
      --debug-notify    测试 Apprise 推送
  -N, --noexit          无账号时持续监控等待
  -E, --example-config  输出示例配置
```

环境变量：`EK_CONFIG`（base64 配置）、`EK_CONFIG_FILE`、`EK_INSTANT`、`EK_DEBUG`、`EK_DEBUG_CRON`、`EK_IN_DOCKER`。

## Docker

```bash
docker build -t embykeeper-lite .
docker run -v $(pwd):/app embykeeper-lite
```

## 开发

```bash
make develop    # 安装开发依赖
make lint       # black + pre-commit
make test       # pytest
```
