#!/usr/bin/env python
"""Entry point for the SonarX HIP-3 L2 Summary Coverage Probe.

Running this module executes the probe with the default configuration used in the
specification. The script is deliberately thin – all heavy lifting lives in the
``hip3_sonarx_l2_summary_coverage_probe_v0`` module.
"""

import importlib
import sys
from pathlib import Path

def main() -> int:
    # Ensure the package root is on the path so ``examples.strategies`` imports work
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    probe_mod = importlib.import_module(
        "examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0"
    )
    parser = probe_mod.build_arg_parser()
    args = parser.parse_args()
    return probe_mod.main([*sys.argv[1:]])

if __name__ == "__main__":
    sys.exit(main())
