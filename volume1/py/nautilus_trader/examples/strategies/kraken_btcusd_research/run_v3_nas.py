#!/usr/bin/env python3
"""Run V3 4-window research on NAS with fixed PnL parsing."""
import subprocess, sys

r = subprocess.run(
    [sys.executable, "examples/strategies/kraken_btcusd_research/run_v3_research.py"],
    capture_output=True, text=True, timeout=600
)
print(r.stdout[-6000:] if len(r.stdout) > 6000 else r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[-2000:] if len(r.stderr) > 2000 else r.stderr, file=sys.stderr)
sys.exit(r.returncode)
