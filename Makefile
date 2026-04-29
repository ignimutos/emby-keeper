.RECIPEPREFIX := >
.DEFAULT_GOAL := help
.PHONY: help help/simple help/all install develop venv venv/require venv/clean python/venv run run/debug run/noinstant run/web systemd systemd/install systemd/uninstall lint lint/node lint/python test debugpy debugpy/cli debugpy/web version version/patch version/minor version/major config/generate push clean clean/build clean/pyc clean/test node/require docs/dev docs/build docs/preview

UV ?= uv
PYTHON ?= 3.13
VENV := .venv

help: help/simple

help/simple:
>   @echo "欢迎您使用 Embykeeper!"
>   @echo "\n使用方法: make <子命令>"
>   @echo "子命令:"
>   @echo "  install - 使用 uv 同步运行时依赖"
>   @echo "  develop - 使用 uv 同步开发环境"
>   @echo "  run - 运行 Embykeeper (使用默认配置文件 config.toml)"
>   @echo "  run/debug - 运行 Embykeeper (使用默认配置文件 config.toml), 并启用调试日志输出"
>   @echo "  run/web - 运行 Embykeeper 的在线网页服务器"
>   @echo "  systemd - 启用 Embykeeper 自动启动"
>   @echo "  lint - 使用 black 和 pre-commit 检查代码风格"
>   @echo "  test - 使用 pytest 运行代码测试"
>   @echo "  help/all - 显示所有子命令"
>   @echo "\n例如, 运行以下命令以启动 Embykeeper:"
>   @echo "  make develop && make run"

help/all:
>   @echo "欢迎您使用 Embykeeper!"
>   @echo "\n使用方法: make <子命令>"
>   @echo "子命令:"
>   @echo "  install - 使用 uv 同步运行时依赖"
>   @echo "  develop - 使用 uv 同步开发环境"
>   @echo '  venv - 安装 Python $(PYTHON) 并创建项目虚拟环境'
>   @echo '  venv/clean - 删除项目虚拟环境 "$(VENV)"'
>   @echo '  python/venv - 等同于 venv'
>   @echo "  run - 运行 Embykeeper (使用默认配置文件 config.toml)"
>   @echo "  run/debug - 运行 Embykeeper (使用默认配置文件 config.toml), 并启用调试日志输出"
>   @echo "  run/noinstant - 运行 Embykeeper, 但不执行立即运行"
>   @echo "  run/web - 运行 Embykeeper 的在线网页服务器"
>   @echo "  systemd - 启用 Embykeeper 自动启动 (当前用户登录时)"
>   @echo "  systemd (当 sudo / root) - 启用 Embykeeper 自动启动 (系统启动时)"
>   @echo "  systemd/uninstall - 停止 Embykeeper 自动启动 (当前用户登录时)"
>   @echo "  systemd/uninstall (当 sudo / root) - 停止 Embykeeper 自动启动 (系统启动时)"
>   @echo "  lint - 使用 black 和 pre-commit 检查代码风格"
>   @echo "  test - 使用 pytest 运行代码测试"
>   @echo "  debugpy - 以远程连接方式在本地主机上启动 Embykeeper 的 Debugpy 调试服务器 (vscode 调试模块)"
>   @echo "  debugpy/web - 以远程连接方式在本地主机上启动 Embykeeper 在线网页服务器的 Debugpy 调试服务器"
>   @echo "  version - 等同于 version/patch"
>   @echo "  version/patch - 运行 bump2version 版本更新 (patch, 例如 1.0.0 -> 1.0.1)"
>   @echo "  version/minor - 运行 bump2version 版本更新 (minor, 例如 1.0.0 -> 1.1.0)"
>   @echo "  version/major - 运行 bump2version 版本更新 (major, 例如 1.0.0 -> 2.0.0)"
>   @echo "  config/generate - 生成示例配置文件"
>   @echo "  push - 推送提交和标签"
>   @echo "  clean - 删除所有 Python 缓存, 构建缓存和测试缓存 (不包括 Python 虚拟环境)"
>   @echo "  clean/build - 删除构建缓存"
>   @echo "  clean/pyc - 删除 Python 缓存"
>   @echo "  clean/test - 删除测试缓存"
>   @echo "  node/require - 安装 NPM 依赖"
>   @echo "  docs/dev - 在本地启动文档服务器"
>   @echo "  docs/build - 构建文档"
>   @echo "  docs/preview - 预览文档"

install:
>   @$(UV) python install "$(PYTHON)"
>   @$(UV) sync --locked --python "$(PYTHON)" --no-dev

develop: venv

venv:
>   @$(UV) python install "$(PYTHON)"
>   @$(UV) sync --locked --python "$(PYTHON)"

python/venv: venv

venv/require:
>   @[ ! -d "$(VENV)" ] && echo 'Error: 尚未安装, 请先运行 "make develop" 以安装!' && exit 1 || :

venv/clean:
>   rm -R -f "$(VENV)" "venv" &>/dev/null

run: venv/require
>   @"$(UV)" run embykeeper -i

run/debug: venv/require
>   @"$(UV)" run embykeeper -i -dd

run/noinstant: venv/require
>   @"$(UV)" run embykeeper

run/web: venv/require
>   @"$(UV)" run embykeeperweb --public

systemd: systemd/install

systemd/install: venv/require
>   @if ! type systemctl > /dev/null; then \
>       echo "Error: 找不到 systemctl 命令."; \
>       exit 1; \
>   elif [ ! -f config.toml ]; then \
>       echo 'Error: config.toml 还没有被生成. 您应该首先运行 "make run" 并编辑生成的配置文件.'; \
>       exit 1; \
>   else \
>       if [ "$$(id -u)" -eq 0 ]; then \
>           mkdir -p "/etc/systemd/system" && echo -e " \
>           [Unit]\n \
>           Description=Embykeeper Daemon\n \
>           After=network.target\n\n \
>           [Service]\n \
>           Type=simple\n \
>           RestartSec=5s\n \
>           Restart=on-failure\n \
>           WorkingDirectory=$$(readlink -f .)\n \
>           ExecStart="$$(readlink -f .)/$(VENV)/bin/python" -m embykeeper --simple-log\n\n \
>           [Install]\n \
>           WantedBy=multi-user.target" \
>           | sed 's/^[[:space:]]*//' \
>           > "/etc/systemd/system/embykeeper.service" && \
>           systemctl enable embykeeper && \
>           systemctl start embykeeper && \
>           echo "Info: 已经将 embykeeper 添加到系统的 systemd 配置文件目录 (/etc/systemd/system/). Embykeeper 会在系统启动时自动启动." && \
>           echo 'Info: 运行 "systemctl status embykeeper" 以检查程序状态.' && \
>           echo 'Info: 运行 "sudo make systemd/uninstall" 以移除.'; \
>       else \
>           mkdir -p "$(HOME)/.config/systemd/user" && echo " \
>           [Unit]\n \
>           Description=Embykeeper Daemon\n \
>           After=network.target\n\n \
>           [Service]\n \
>           Type=simple\n \
>           RestartSec=5s\n \
>           Restart=on-failure\n \
>           WorkingDirectory=$$(readlink -f .)\n \
>           ExecStart="$$(readlink -f .)/$(VENV)/bin/python" -m embykeeper --simple-log\n\n \
>           [Install]\n \
>           WantedBy=default.target" \
>           | sed 's/^[[:space:]]*//' \
>           > "$(HOME)/.config/systemd/user/embykeeper.service" && \
>           systemctl --user enable embykeeper && \
>           systemctl --user start embykeeper && \
>           echo "Info: 已经将 embykeeper 添加到用户的 systemd 配置文件目录 ($(HOME)/.config/systemd/user). Embykeeper 会在当前用户登录时自动启动." && \
>           echo 'Info: 运行 "sudo make systemd/uninstall" 添加到系统的 systemd 配置文件目录.' && \
>           echo 'Info: 运行 "systemctl --user status embykeeper" 以检查程序状态.' && \
>           echo 'Info: 运行 "make systemd/uninstall" 以移除.'; \
>       fi \
>   fi

systemd/uninstall:
>   @if [ "$$(id -u)" -eq 0 ]; then \
>       systemctl stop embykeeper && \
>       systemctl disable embykeeper && \
>       rm "/etc/systemd/system/embykeeper.service" && \
>       echo 'Info: 已移除 systemd 配置. Embykeeper 不再自动启动.'; \
>   else \
>       systemctl --user stop embykeeper && \
>       systemctl --user disable embykeeper && \
>       rm "$(HOME)/.config/systemd/user/embykeeper.service" && \
>       echo 'Info: 已移除 systemd 配置. Embykeeper 不再自动启动.'; \
>   fi

lint: lint/python lint/node

lint/node: node/require
>   npm run lint

lint/python: venv/require
>   "$(UV)" run black .
>   "$(UV)" run pre-commit run -a

test: venv/require
>   "$(UV)" run pytest

debugpy: debugpy/cli

debugpy/cli: venv/require
>   "$(UV)" run python -m debugpy --listen localhost:5678 --wait-for-client cli.py

debugpy/web: venv/require
>   "$(UV)" run python -m debugpy --listen localhost:5678 --wait-for-client web.py --public

version: version/patch

version/patch: venv/require
>   "$(UV)" run python -m bumpversion patch
>   $(MAKE) config/generate
>   $(MAKE) push

version/minor: venv/require
>   "$(UV)" run python -m bumpversion minor
>   $(MAKE) config/generate
>   $(MAKE) push

version/major: venv/require
>   "$(UV)" run python -m bumpversion major
>   $(MAKE) config/generate
>   $(MAKE) push

config/generate: venv/require
>   "$(UV)" run embykeeper -E > config.example.toml
>   git add config.example.toml
>   git commit -m "Generate example config file for $$(git describe --tags --abbrev=0)"

push:
>   git push && git push --tags

clean: clean/build clean/pyc clean/test
>   @echo "Info: 清除了构建和测试缓存."

clean/build:
>   rm -fr build/
>   rm -fr dist/
>   rm -fr .eggs/
>   find . -name '*.egg-info' -exec rm -fr {} +
>   find . -name '*.egg' -exec rm -f {} +

clean/pyc:
>   find . -name '*.pyc' -exec rm -f {} +
>   find . -name '*.pyo' -exec rm -f {} +
>   find . -name '*~' -exec rm -f {} +
>   find . -name '__pycache__' -exec rm -fr {} +

clean/test:
>   rm -fr .tox/
>   rm -f .coverage
>   rm -fr htmlcov/

node/require:
>   @if [ ! -d "node_modules" ]; then \
>       echo "正在安装 NPM 依赖..."; \
>       npm install; \
>   fi

docs/dev: node/require
>   npm run docs:dev

docs/build: node/require
>   npm run docs:build

docs/preview: node/require
>   npm run docs:preview
