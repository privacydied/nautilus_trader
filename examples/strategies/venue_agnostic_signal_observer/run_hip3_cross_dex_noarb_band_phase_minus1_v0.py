#!/usr/bin/env python
"""Entry point for HIP-3 Cross-DEX No-Arb-Band Phase -1 Scout."""

import importlib
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    mod = importlib.import_module(
        "examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0"
    )
    parser = mod.build_arg_parser()
    args = parser.parse_args()
    return mod.main([*sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
