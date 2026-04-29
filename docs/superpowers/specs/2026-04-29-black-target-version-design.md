# Black target-version 显式配置设计

## 目标

消除 Black 在 Python 3.13 环境中的安全检查 warning，并保持当前代码格式化行为稳定。

## 当前行为

- 当前运行环境是 Python 3.13。
- `pyproject.toml` 的 `[tool.black]` 目前未显式设置 `target-version`。
- Black 25.9 在 Python 3.13 下检查这批文件时会提示：当前解释器无法解析按 Python 3.14 目标格式化后的代码。
- 本次失败的直接原因是 4 个文件存在格式漂移；warning 本身不一定导致失败，但会污染 lint 输出。

## 备选方案

### 方案 A：在 `pyproject.toml` 显式设置 `target-version = ['py313']`

推荐方案。

优点：

- 与当前开发/CI 解释器一致。
- 只改 Black 配置，影响面最小。
- 消除当前 warning，输出更稳定。

缺点：

- 未来如果项目升级到 Python 3.14，需要同步更新该配置。

### 方案 B：把 Black 运行环境升级到 Python 3.14

优点：

- 不需要在 Black 配置中显式锁目标版本。

缺点：

- 需要同步调整本地和 CI hook 运行环境。
- 改动面比本次需求大。

### 方案 C：对 Black 使用 `--fast` 跳过安全检查

优点：

- 可以绕过当前 warning。

缺点：

- 这是规避，不是明确配置。
- 会放弃 Black 的等价性安全检查，不适合默认方案。

## 选定方案

采用方案 A：在 `[tool.black]` 下增加：

```toml
target-version = ['py313']
```

不改业务代码，不调整其他 lint 工具，不修改 Python 版本声明。

## 影响范围

- `pyproject.toml`

## 验证

修改后检查：

1. `python -m black --check ...` 不再出现 Python 3.14 安全检查 warning。
2. `uv run pre-commit run black --files ...` 通过。
3. 受影响测试保持通过。
