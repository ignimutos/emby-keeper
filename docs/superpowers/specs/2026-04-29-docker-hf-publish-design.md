# Docker / Hugging Face 发布流程调整设计

## 目标

调整现有 GitHub Actions 发布流程：

1. 屏蔽 `docker-dev` workflow，不删除代码。
2. 正式 `docker` workflow 在 `main` 分支有提交时也发布到 Docker Hub。
3. Hugging Face 同步仅在 GitHub Release 事件时执行。

## 当前行为

- `.github/workflows/docker-dev.yml` 在 `workflow_dispatch`、`push(main)`、`release` 时发布 dev 镜像。
- `.github/workflows/docker.yml` 在 `workflow_dispatch`、`release` 时发布正式镜像到 Docker Hub。
- `.github/workflows/docker.yml` 中的 `sync-to-hf` job 会在 `docker` workflow 触发时执行，因此当前会在 `workflow_dispatch` 和 `release` 时同步到 Hugging Face。

## 方案

### 1. 关闭 docker-dev workflow

在 `.github/workflows/docker-dev.yml` 的 jobs 层给入口 job 增加 `if: false`，使整个 workflow 保留但不执行。

### 2. 让正式 docker workflow 响应 main 分支提交

在 `.github/workflows/docker.yml` 的 `on:` 中增加：

- `push`
- `branches: [main]`
- 保持现有 `workflow_dispatch` 和 `release`

这样 `main` 分支提交后会直接构建并发布正式 Docker Hub 镜像。

### 3. 仅在 release 时同步 Hugging Face

在 `.github/workflows/docker.yml` 的 `sync-to-hf` job 增加 job 级条件：

```yaml
if: github.event_name == 'release'
```

不额外引入开关，也不新增 secret 存在性判断，保持现有 Hugging Face 凭据使用方式。

## 影响范围

- `.github/workflows/docker-dev.yml`
- `.github/workflows/docker.yml`

## 风险与取舍

- `main` 的每次提交都会发布正式 Docker Hub 镜像，发布频率会高于当前配置。
- `workflow_dispatch` 手动触发正式 docker workflow 时，Hugging Face 不会同步，因为它不是 `release` 事件。
- `docker-dev` workflow 仍保留在仓库内，后续恢复只需去掉 `if: false`。

## 验证

修改后检查：

1. `docker-dev` workflow 的 job 是否被静态禁用。
2. `docker` workflow 是否包含 `push` 到 `main` 的触发条件。
3. `sync-to-hf` job 是否只在 `release` 事件下执行。
4. YAML 结构和缩进是否正确。
