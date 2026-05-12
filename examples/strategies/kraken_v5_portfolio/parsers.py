#!/usr/bin/env python3
"""Lightweight report helpers for fee/PnL parsing.

Extracted from kraken_btcusd_research.reports to avoid cross-package coupling.
"""


def parse_pnl(val) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, list):
        return sum(parse_pnl(item) for item in val)
    s = str(val).strip().replace("USD", "").replace("'", "").replace('"', "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_commission(val) -> float:
    return parse_pnl(val)
