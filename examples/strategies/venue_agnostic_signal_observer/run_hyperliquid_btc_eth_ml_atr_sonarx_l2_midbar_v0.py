"""
CLI for SonarX L2 Midbar Builder — L2 Snapshot → 1h Midquote Bars
================================================================

NOT live trading. NOT exchange-connected. S3 archive only.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure importable without Nautilus extensions
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_v0 import main


if __name__ == "__main__":
    sys.exit(main())