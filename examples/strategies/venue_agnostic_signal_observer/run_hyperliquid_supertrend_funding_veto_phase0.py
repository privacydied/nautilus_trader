"""Run script for Hyperliquid Supertrend Funding Veto Phase 0.
\nInvokes the `run_phase_a` function from the companion module, pointing it at the
latest v0 report directory.
"""

import sys
from pathlib import Path
from .hyperliquid_supertrend_funding_veto_phase0 import run_phase_a

def main():
    if len(sys.argv) != 2:
        print("Usage: python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_supertrend_funding_veto_phase0 <v0_report_dir>")
        sys.exit(1)
    report_dir = Path(sys.argv[1])
    result = run_phase_a(report_dir)
    print("Phase A completed. Verdict:", result["phase_a_verdict"])

if __name__ == "__main__":
    main()
