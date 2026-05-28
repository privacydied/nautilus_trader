"""
CLI for SonarX L2 Midbar Diagnostic — Quote-Derived Backtest
=============================================================

NOT live trading. NOT exchange-connected. Local simulated-paper only if diagnostic passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure importable without Nautilus extensions
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_diagnostic_v0 import main


if __name__ == "__main__":
    sys.exit(main())