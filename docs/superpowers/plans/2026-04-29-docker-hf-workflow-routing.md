# Docker and Hugging Face Workflow Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable the `docker-dev` workflow, publish the formal Docker image on pushes to `main`, and only sync to Hugging Face on GitHub release events.

**Architecture:** Update the two existing GitHub Actions workflow files in place. Keep the current Docker build and push logic intact, and only change trigger routing plus job gating so the behavior shift stays local to CI configuration.

**Tech Stack:** GitHub Actions workflow YAML, Docker Buildx, `uv run python` with PyYAML for local workflow syntax checks.

---

## File map

- `.github/workflows/docker-dev.yml` — editable-image workflow to disable without deleting.
- `.github/workflows/docker.yml` — formal image workflow; add the `push` trigger for `main` and gate `sync-to-hf` to release events only.
- No application code, runtime code, or docs files change.

### Task 1: Disable docker-dev workflow

**Files:**
- Modify: `.github/workflows/docker-dev.yml:23-131`

- [ ] **Step 1: Run a failing check for the disabled workflow marker**

```bash
uv run python - <<'PY'
from pathlib import Path

text = Path(".github/workflows/docker-dev.yml").read_text()
expected = """jobs:
  build:
    if: false
    runs-on: ubuntu-latest
"""
assert expected in text, "docker-dev build job is not disabled yet"
print("docker-dev workflow is disabled")
PY
```

Expected: FAIL with `AssertionError: docker-dev build job is not disabled yet`

- [ ] **Step 2: Add `if: false` to the build job**

```yaml
jobs:
  build:
    if: false
    runs-on: ubuntu-latest
```

Insert the `if: false` line directly under `build:` in `.github/workflows/docker-dev.yml`.

- [ ] **Step 3: Run the disabled-workflow check again**

```bash
uv run python - <<'PY'
from pathlib import Path

text = Path(".github/workflows/docker-dev.yml").read_text()
expected = """jobs:
  build:
    if: false
    runs-on: ubuntu-latest
"""
assert expected in text, "docker-dev build job is not disabled yet"
print("docker-dev workflow is disabled")
PY
```

Expected: PASS with `docker-dev workflow is disabled`

- [ ] **Step 4: Parse the workflow file to confirm valid YAML**

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml

path = Path(".github/workflows/docker-dev.yml")
yaml.safe_load(path.read_text())
print(f"{path}: yaml ok")
PY
```

Expected: PASS with `.github/workflows/docker-dev.yml: yaml ok`

### Task 2: Publish formal images on `main` pushes and gate HF sync to releases

**Files:**
- Modify: `.github/workflows/docker.yml:3-6`
- Modify: `.github/workflows/docker.yml:113-139`

- [ ] **Step 1: Run a failing check for the target routing**

```bash
uv run python - <<'PY'
from pathlib import Path

text = Path(".github/workflows/docker.yml").read_text()

expected_on = """on:
  workflow_dispatch:
  push:
    branches:
      - 'main'
  release:
    types: [released]
"""
assert expected_on in text, "docker workflow is not triggered by pushes to main yet"

expected_sync = """  sync-to-hf:
    if: github.event_name == 'release'
    runs-on: ubuntu-latest
    needs:
      - merge
"""
assert expected_sync in text, "sync-to-hf is not gated to release events yet"

print("docker workflow routing is updated")
PY
```

Expected: FAIL with `AssertionError: docker workflow is not triggered by pushes to main yet`

- [ ] **Step 2: Add the `push` trigger for `main`**

```yaml
on:
  workflow_dispatch:
  push:
    branches:
      - 'main'
  release:
    types: [released]
```

Replace the current `on:` block at the top of `.github/workflows/docker.yml` with this exact block.

- [ ] **Step 3: Gate the Hugging Face sync job to release events**

```yaml
  sync-to-hf:
    if: github.event_name == 'release'
    runs-on: ubuntu-latest
    needs:
      - merge
```

Insert the `if:` line directly under `sync-to-hf:` in `.github/workflows/docker.yml`.

- [ ] **Step 4: Run the routing check again**

```bash
uv run python - <<'PY'
from pathlib import Path

text = Path(".github/workflows/docker.yml").read_text()

expected_on = """on:
  workflow_dispatch:
  push:
    branches:
      - 'main'
  release:
    types: [released]
"""
assert expected_on in text, "docker workflow is not triggered by pushes to main yet"

expected_sync = """  sync-to-hf:
    if: github.event_name == 'release'
    runs-on: ubuntu-latest
    needs:
      - merge
"""
assert expected_sync in text, "sync-to-hf is not gated to release events yet"

print("docker workflow routing is updated")
PY
```

Expected: PASS with `docker workflow routing is updated`

- [ ] **Step 5: Parse both workflow files as YAML**

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml

for name in (
    ".github/workflows/docker-dev.yml",
    ".github/workflows/docker.yml",
):
    yaml.safe_load(Path(name).read_text())
    print(f"{name}: yaml ok")
PY
```

Expected:

- `.github/workflows/docker-dev.yml: yaml ok`
- `.github/workflows/docker.yml: yaml ok`

- [ ] **Step 6: Inspect the final diff**

```bash
git diff -- .github/workflows/docker-dev.yml .github/workflows/docker.yml
```

Expected: the diff only shows `docker-dev` gaining `if: false`, `docker.yml` gaining the `push` trigger for `main`, and `sync-to-hf` gaining the release-only `if:` condition.

## Self-review checklist

- Spec coverage: Task 1 covers `docker-dev` disablement; Task 2 covers Docker Hub publish on `main` pushes and HF release-only sync.
- Placeholder scan: no TODO, TBD, or deferred decisions remain.
- Type and field consistency: workflow names, job names (`build`, `merge`, `sync-to-hf`), and the `release` event name match the current repository state.
