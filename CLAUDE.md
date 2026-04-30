# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- `make develop` — create the local `.venv` with uv and install runtime + dev dependencies.
- `make install` — install runtime dependencies only.
- `make run` — run the CLI with the default `config.toml`.
- `make run/debug` — run the CLI with debug logging.
- `make run/web` — run the web console.
- `make lint` — run Python formatting/check hooks (`black .` and `pre-commit run -a`).
- `make test` — run the default pytest suite.
- Single test: `uv run pytest tests/test_cli.py::test_version`.
- `tox` — run the auxiliary multi-version test matrix (`py310`, `py311`, `py312`, `py313`).
- `uv build` — build the Python package.
- Docs only: `make docs/dev`, `make docs/build`, `make docs/preview`.
- `make help/simple` — show the canonical top-level make targets.

## Architecture

- `embykeeper/cli.py` is the main orchestration entrypoint. It is a Typer CLI that loads config, initializes enabled managers, optionally runs an immediate pass, then keeps long-running schedulers and monitors alive in a shared async task pool.
- Config is centralized in `embykeeper/config.py`. Runtime config comes from base64 TOML in `EK_CONFIG` or from a TOML file (`config.toml` by default). If a config file is used, it is watched with `watchfiles`, and managers react through `config.on_change(...)` / `config.on_list_change(...)` callbacks.
- Persistent runtime state is split between config and cache. `embykeeper/cache.py` stores cache data in local JSON under the app data directory or in MongoDB when configured. `embykeeper/schedule.py` uses that cache to persist next-run times across restarts.
- `embykeeper/telegram/` is the Telegram automation domain. The manager entrypoints are `checkin_main.py`, `monitor_main.py`, `message_main.py`, and `registrar_main.py`. Site implementations are plugin-style modules under `checkiner/`, `monitor/`, `messager/`, and `registrar/`.
- Telegram site modules are discovered dynamically by `embykeeper/telegram/dynamic.py`. New modules become available by placement and naming convention; modules marked with `__ignore__ = True` or named like `test*` are excluded from normal default enablement/config generation.
- `embykeeper/telegram/session.py` manages pooled Telegram client sessions with shared login lifecycle and cleanup. Most Telegram features run through `ClientsSession` instead of creating raw clients ad hoc.
- `embykeeper/emby/` and `embykeeper/subsonic/` implement the keepalive flows for media servers. Both follow the same pattern: manager class + scheduler-backed execution, with support for global schedules and per-account overrides.
- `embykeeperweb/app.py` is not a separate business backend. It launches `embykeeper` in a PTY subprocess, passes config through environment variables, and streams terminal I/O over Socket.IO. `scripts/docker-entrypoint.sh` chooses CLI vs web mode based on `EK_WEBPASS`.
- The Node toolchain is only for the VitePress docs site in `docs/`; product runtime is Python.
- The default automated test surface is small: CI and `tox` run `pytest tests`. Package-internal `test_*.py` files under Telegram subpackages are often support assets or opt-in module tests, not part of the default suite.

## Repository-specific notes

- Prefer the actual package layout under `embykeeper/telegram/*` when navigating the code. Some contributor docs still reference older `telechecker` paths.
- If you add or debug a new Telegram site integration, read `docs/guide/参与开发.md` first. It documents the expected log-capture workflow (`embykeeper config.toml -D all@<bot> | tee log.json`) and the plugin conventions maintainers expect.
- `package.json` is docs-focused. `npm run cli:dev` points to `make run/dev`, but there is no `run/dev` target in the Makefile, so do not rely on that script.
