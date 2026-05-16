# Stage 2 Worktree Service Setup

## Purpose

The Stage 2 gate watcher (`stage2_gate_watcher.py`) runs as a user-level systemd
service and needs to report clean git metadata.  If it runs from the main
development repo (`nautilus_trader`), any uncommitted strategy work marks the
watcher status as `-dirty`.

The fix is to run the watcher from a **dedicated clean git worktree**.
A git worktree shares the same repository objects but has its own working tree
and HEAD, so the main repo can be dirty without contaminating the watcher.

## Layout

| Purpose | Path |
|---|---|
| Main development repo | `/mnt/nasirjones/py/nautilus_trader` |
| Stage 2 runtime worktree | `/mnt/nasirjones/py/nautilus_trader_stage2_runtime` |
| Reports/status (existing) | `/mnt/nasirjones/py/nautilus_trader/reports/` |
| Capture data (existing) | `/mnt/nasirjones/py/nautilus_trader/data/` |

## Setup Commands

### 1. Create the worktree

```bash
cd /mnt/nasirjones/py/nautilus_trader
git worktree add /mnt/nasirjones/py/nautilus_trader_stage2_runtime -b stage2-watcher-runtime HEAD
```

This creates a branch `stage2-watcher-runtime` based on the current HEAD and a
worktree at the target path.

If the worktree already exists, skip this step or verify it is on the correct
branch:

```bash
git -C /mnt/nasirjones/py/nautilus_trader_stage2_runtime status
```

### 2. Sync dependencies in the worktree

```bash
cd /mnt/nasirjones/py/nautilus_trader_stage2_runtime
uv sync --all-extras
```

NautilusTrader's Rust extensions will build inside the worktree's own `.venv`.

### 3. Verify worktree isolation

The main repo may be dirty:

```bash
git -C /mnt/nasirjones/py/nautilus_trader status --short
# Expected: shows your uncommitted strategy work
```

The worktree should be clean:

```bash
git -C /mnt/nasirjones/py/nautilus_trader_stage2_runtime status --short
# Expected: no output (unless files were modified in the worktree)
```

### 4. Install / reload the systemd service

```bash
# Copy the service file (adjust if needed)
cp /mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/systemd/nautilus-stage2-gate-watcher.service \
   ~/.config/systemd/user/nautilus-stage2-gate-watcher.service

# Reload and restart
systemctl --user daemon-reload
systemctl --user stop nautilus-stage2-gate-watcher.service
systemctl --user start nautilus-stage2-gate-watcher.service
```

### 5. Verify the service

```bash
systemctl --user status nautilus-stage2-gate-watcher.service --no-pager
journalctl --user -u nautilus-stage2-gate-watcher.service -n 50 --no-pager
```

Check that the status JSON shows a clean SHA from the worktree:

```bash
cat /mnt/nasirjones/py/nautilus_trader/reports/stage2_gate_watcher_status.json
```

Expected:
- `git_sha` has no `-dirty` suffix
- `runtime_repo_root` points to the worktree
- `reports_root` and `data_root` point to the main repo's artifact directories

## Updating the Worktree

When you need to update the watcher code in the worktree:

```bash
cd /mnt/nasirjones/py/nautilus_trader_stage2_runtime
git pull
```

Or from the main repo:

```bash
cd /mnt/nasirjones/py/nautilus_trader
git push . HEAD:stage2-watcher-runtime
cd /mnt/nasirjones/py/nautilus_trader_stage2_runtime
git reset --hard origin/stage2-watcher-runtime
```

Then restart the service.

## How Path Resolution Works

The watcher uses a three-layer path configuration:

1. **CLI args** override everything
2. **Environment variables** override defaults:

   | Variable | Purpose |
   |---|---|
   | `NAUTILUS_STAGE2_REPO_ROOT` | Source code / git worktree root |
   | `NAUTILUS_STAGE2_REPORTS_ROOT` | Reports and status output directory |
   | `NAUTILUS_STAGE2_DATA_ROOT` | Capture data directory |
   | `NAUTILUS_STAGE2_STATUS_PATH` | Exact path for watcher status JSON |
   | `NAUTILUS_STAGE2_PYTHON_EXE` | Python executable (default: worktree `.venv/bin/python`) |

3. **Defaults** preserve the original behaviour: all paths relative to CWD,
   python from `.../nautilus_trader/.venv/bin/python`.

After `os.chdir()` to `--workdir` (or `NAUTILUS_STAGE2_REPO_ROOT`), all git
subprocesses operate on the runtime worktree, so `_get_git_sha()` reports the
worktree's commit with dirty status based solely on the worktree's working tree.

## Safety

- No auth, no orders, no execution.
- No private keys or exchange credentials.
- No threshold or gate logic changes.
- The worktree eliminates dirty-tree contamination without weakening
  dirty-tree checks.
