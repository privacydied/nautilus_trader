# Cross-Asset Beta-Lag Stress V2 Worktree Runbook

This runbook installs the stage2 gate watcher so it runs from a dedicated git worktree while writing status, reports, and data to the main repository artifact paths.

Signal family: `cross_asset_beta_lag_stress_v2`

Worktree path used by the repo-owned service defaults:

`/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress`

Main repo/report path:

`/mnt/nasirjones/py/nautilus_trader`

Status JSON path:

`/mnt/nasirjones/py/nautilus_trader/reports/stage2_gate_watcher_status.json`

## Why use a worktree

The worktree improves runtime hygiene and provenance. The long-running watcher should not execute arbitrary dirty files from the active development tree.

This is not code immutability. A worktree can still be changed, pulled, checked out, or dirtied. Protocol integrity comes from the recorded `git_sha`, `git_dirty`, precommitment hash/lock, readiness checks, tests, and append-only run artifacts.

Keep these concepts separate when interpreting status JSON.

## Create or update the worktree

From the main repo:

```bash
cd /mnt/nasirjones/py/nautilus_trader

git worktree add /mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress HEAD
```

If the worktree already exists:

```bash
git -C /mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress status --short --branch
```

## Pin or check out the intended branch/commit

Use an explicit branch or commit before starting the service:

```bash
cd /mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress

git fetch --all --prune
git checkout cross-asset-beta-lag-stress-v2-worktree
# or pin a specific audited commit:
# git checkout <commit-sha>
```

Confirm the exact runtime SHA:

```bash
git rev-parse --short=12 HEAD
```

## Confirm the worktree is clean

```bash
git -C /mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress status --short
```

No output means clean.

The main repo may be dirty during development, but the service status should report the worktree root and dirty state of the worktree, not the main tree.

## Sync dependencies with uv

```bash
cd /mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress
uv sync --all-extras
```

Do not use curl-to-bash installers here.

## Optional environment override file

The repo-owned unit includes:

```ini
EnvironmentFile=-%h/.config/nautilus/stage2-gate-watcher.env
```

Create it if you need local overrides:

```bash
mkdir -p ~/.config/nautilus
cat > ~/.config/nautilus/stage2-gate-watcher.env <<'EOF'
NAUTILUS_STAGE2_WORKTREE=/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress
NAUTILUS_STAGE2_REPO_ROOT=/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress
NAUTILUS_STAGE2_SIGNAL_FAMILY=cross_asset_beta_lag_stress_v2
NAUTILUS_STAGE2_STATUS_PATH=/mnt/nasirjones/py/nautilus_trader/reports/stage2_gate_watcher_status.json
NAUTILUS_STAGE2_REPORTS_ROOT=/mnt/nasirjones/py/nautilus_trader/reports
NAUTILUS_STAGE2_DATA_ROOT=/mnt/nasirjones/py/nautilus_trader/data
EOF
```

## Install or update the user systemd service

```bash
mkdir -p ~/.config/systemd/user
cp /mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/systemd/nautilus-stage2-gate-watcher.service \
  ~/.config/systemd/user/nautilus-stage2-gate-watcher.service
```

Inspect what systemd will run:

```bash
systemctl --user cat nautilus-stage2-gate-watcher.service
systemctl --user show nautilus-stage2-gate-watcher.service \
  -p FragmentPath -p WorkingDirectory -p ExecStart -p Environment
```

## Reload and restart

```bash
systemctl --user daemon-reload
systemctl --user restart nautilus-stage2-gate-watcher.service
```

## Check service status

```bash
systemctl --user status nautilus-stage2-gate-watcher.service --no-pager
```

## Tail logs

```bash
journalctl --user -u nautilus-stage2-gate-watcher.service -n 100 --no-pager
journalctl --user -u nautilus-stage2-gate-watcher.service -f
```

## Verify status JSON

```bash
cat /mnt/nasirjones/py/nautilus_trader/reports/stage2_gate_watcher_status.json
python -m json.tool /mnt/nasirjones/py/nautilus_trader/reports/stage2_gate_watcher_status.json
```

Expected provenance fields include:

- `git_worktree_root`
- `git_sha`
- `git_dirty`
- `signal_family`
- `trigger_name`
- `trigger_threshold_bps`
- `trigger_window_seconds`
- `stress_confirmation_status`

## Stop or restart safely

Stop:

```bash
systemctl --user stop nautilus-stage2-gate-watcher.service
```

Restart after changing the worktree branch/commit:

```bash
systemctl --user daemon-reload
systemctl --user restart nautilus-stage2-gate-watcher.service
```

Check logs and status JSON after every restart.

## Trigger/capture safety

The service is observer-only public-data research. It must not require API keys, private keys, wallets, live trading nodes, execution clients, or order submission.

Do not start a real capture manually unless the precommitted gate condition is satisfied or you are using an explicit no-capture/dry-run mode.
