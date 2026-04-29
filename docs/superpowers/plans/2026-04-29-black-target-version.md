# Black Target Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit Black target version so Python 3.13 environments stop emitting the Python 3.14 safety-check warning.

**Architecture:** Keep the change local to Black configuration in `pyproject.toml`. Do not modify runtime code, test logic, or other tool settings; verify the result by reproducing the original warning before the edit and re-running the same formatting commands after the edit.

**Tech Stack:** Python 3.13, Black, pre-commit, pytest, TOML

---

### Task 1: Set Black target version to py313

**Files:**
- Modify: `pyproject.toml:103-113`
- Verify: `tests/test_emby_notification.py`

- [ ] **Step 1: Reproduce the current warning before editing**

Run:

```bash
python -m black --check "embykeeper/emby/notification.py" "embykeeper/emby/main.py" "embykeeper/config.py" "tests/test_emby_notification.py"
```

Expected: output includes the Python 3.14 safety-check warning before the final summary.

- [ ] **Step 2: Add the explicit Black target version**

Update `pyproject.toml` so the `[tool.black]` section becomes:

```toml
[tool.black]
line-length = 110
target-version = ['py313']
extend-exclude = '''
# A regex preceded with ^/ will apply only to files and directories
# in the root of the project.
(
  ^/config.example.toml
  # | .*.toml
)
'''
```

- [ ] **Step 3: Re-run Black check to verify the warning is gone**

Run:

```bash
python -m black --check "embykeeper/emby/notification.py" "embykeeper/emby/main.py" "embykeeper/config.py" "tests/test_emby_notification.py"
```

Expected: `All done!` and `would be left unchanged`, with no Python 3.14 safety-check warning.

- [ ] **Step 4: Run the Black pre-commit hook on the same files**

Run:

```bash
uv run pre-commit run black --files "embykeeper/emby/notification.py" "embykeeper/emby/main.py" "embykeeper/config.py" "tests/test_emby_notification.py"
```

Expected: `black...Passed`

- [ ] **Step 5: Run the targeted regression tests**

Run:

```bash
uv run pytest "tests/test_emby_notification.py"
```

Expected: all tests pass.

- [ ] **Step 6: Commit the config update and planning docs**

Run:

```bash
git add pyproject.toml docs/superpowers/specs/2026-04-29-black-target-version-design.md docs/superpowers/plans/2026-04-29-black-target-version.md
git commit -m "$(cat <<'EOF'
ci(lint): pin black target version to py313

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Expected: commit created successfully.
