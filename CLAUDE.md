# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Overview

EmbyKeeper (lite) is an Emby keep-alive tool: it periodically simulates video playback to keep Emby accounts active. Everything else (Telegram automation, Subsonic, web console, Cloudflare auto-solving) was removed in this fork (`lite` branch). Do not reintroduce it.

## Development commands

- `make develop` — create the local `.venv` with uv and install runtime + dev dependencies.
- `make run` — run the CLI with the default `config.toml`.
- `make run/debug` — run the CLI with debug logging.
- `make lint` — run `black .` and `pre-commit run -a`.
- `make test` — run the default pytest suite.
- Single test: `uv run pytest tests/test_cli.py::test_version`.

## Architecture

- `embykeeper/cli.py` is the single entrypoint. It is a Typer CLI that loads config, initializes the `EmbyManager`, optionally runs an immediate pass, then keeps the scheduled keepalive running in a shared async task pool. Only Emby options remain.
- `embykeeper/config.py` centralizes config. Runtime config comes from base64 TOML in `EK_CONFIG` or a TOML file (`config.toml` by default). The file is watched with `watchfiles`; managers react via `config.on_change(...)` / `config.on_list_change(...)`.
- `embykeeper/schema.py` holds the pydantic models: `Config`, `EmbyConfig`, `EmbyAccount`, `ProxyConfig`, `NotifierConfig`.
- `embykeeper/cache.py` persists cache data as local JSON under the data dir (`config.basedir/cache.json`). Emby login tokens are stored encrypted via `embykeeper/crypto.py` (scrypt + Fernet, key derived from the account password) to survive a lone `cache.json` leak.
- `embykeeper/schedule.py` persists next-run times across restarts; `embykeeper/scheduled_manager.py` provides the shared `ScheduledKeepaliveManager` base used by `EmbyManager`.
- `embykeeper/emby/` is the Emby keepalive domain:
  - `api.py` — `Emby` facade: config resolution, env/device fingerprint, login, item fetching.
  - `transport.py` — `EmbyTransport`: HTTP session, retry/401-relogin/backoff, streaming, auth login, encrypted credential caching.
  - `playback.py` — `PlaybackSession`: one simulated playback session lifecycle.
  - `keepalive.py` — `KeepaliveRun`: the keepalive decision tree (whether playback succeeded).
  - `main.py` — `EmbyManager`: scheduling and orchestration.
  - `notification.py` — result formatting; `errors.py` — error hierarchy.
- `embykeeper/notify.py` + `embykeeper/apprise.py` push keepalive results via Apprise (the only notification backend).

## Repository-specific notes

- `verify` on `EmbyConfig`/`EmbyAccount` controls HTTPS certificate verification (default `false`).
- Tests live in `tests/` (pytest, no network). `test_crypto.py` covers the encrypted credential cache.
- Dependency footprint is intentionally small; only add back what the Emby keepalive path actually needs.
