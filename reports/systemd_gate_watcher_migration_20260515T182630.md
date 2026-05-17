# systemd Gate Watcher Migration — Preservation Note

## Timestamp
UTC: Fri 15 May 18:26:30 UTC 2026

## Git
- SHA: 8a5ab0309d3e42e58006e9129e7b802d39368bc2
- Dirty:  M reports/stage2_readiness/readiness_report.md
 M reports/stage2_readiness/readiness_summary.json

## Old systemd Unit
- File: ~/.config/systemd/user/nautilus-stage2-gate-watcher.service
- Status: inactive/disabled
- Last ran: 15:38–18:03 UTC (2h25m)

## Old Hermes Job (ca223755d1e6)
- Name: cross-asset-beta-lag readiness-check-and-collect-if-ready
- Status: active (last run 19:24 UTC — ok, readiness passed, no stress detected)
- Schedule: every 30m
- Will be: disabled/converted to audit-only before systemd is enabled

## Hermes Gateway
- Running: yes (systemd service active since 03:35 BST)

## Data Integrity
No old data or reports were deleted. No execution/auth/order paths were added.

## Safety
Public data observer only. No auth. No orders. No execution.
