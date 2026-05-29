#!/usr/bin/env python
"""Entry point for the HIP-3 replica_cmds setOracle reconstruction scout."""

import importlib
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    probe_mod = importlib.import_module(
        "examples.strategies.venue_agnostic_signal_observer"
        ".hip3_replica_cmds_setoracle_reconstruction_v0"
    )
    return probe_mod.main([*sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
