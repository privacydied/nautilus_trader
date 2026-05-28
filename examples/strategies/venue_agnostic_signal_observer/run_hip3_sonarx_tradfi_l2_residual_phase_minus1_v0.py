#!/usr/bin/env python
"""Entry point for the SonarX HIP-3 TradFi L2 Residual Phase -1 Scout.

Running this module executes the Phase -1 scout with the default
configuration. All heavy lifting lives in the core module.
"""

import importlib
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    probe_mod = importlib.import_module(
        "examples.strategies.venue_agnostic_signal_observer"
        ".hip3_sonarx_tradfi_l2_residual_phase_minus1_v0"
    )
    return probe_mod.main([*sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
