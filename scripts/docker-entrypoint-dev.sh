#!/bin/bash

set -e

if [ -d "/src" ]; then
    if [ ! "$(ls -A /src)" ]; then
        cp -rT /build /src
    fi
    echo ">> 正在根据源码配置程序, 请稍候."
    cd /src
    sync_stamp="/src/.venv/.sync-stamp"
    if [ ! -f "$sync_stamp" ] || [ "/src/pyproject.toml" -nt "$sync_stamp" ] || [ "/src/uv.lock" -nt "$sync_stamp" ]; then
        uv sync --locked
        touch "$sync_stamp"
    fi
    export PATH="/src/.venv/bin:$PATH"
    echo ">> 已配置完成."
    echo
else
    echo ">> 请挂载目录 /src, 以释放源码."
    exit 1
fi

if [ -z "${EK_WEBPASS}" ]; then
    exec "embykeeper" "--basedir" "/app" "$@"
else
    exec "embykeeperweb" "--basedir" "/app" "--public" "$@"
fi
