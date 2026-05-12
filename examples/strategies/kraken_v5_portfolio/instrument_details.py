#!/usr/bin/env python3
"""Shared instrument precision for V5 multi-asset research."""

# (price_precision, size_precision) for each asset.
# size_precision clamped >= 2 so volume bars are valid.
PRICE_SIZE_PRECISION: dict[str, tuple[int, int]] = {
    "BTC/USD.KRAKEN":  (2, 8),
    "ETH/USD.KRAKEN":  (2, 8),
    "SOL/USD.KRAKEN":  (2, 4),
    "XRP/USD.KRAKEN":  (4, 2),
    "ADA/USD.KRAKEN":  (5, 2),
    "LINK/USD.KRAKEN": (2, 4),
    "DOGE/USD.KRAKEN": (5, 2),
    "AVAX/USD.KRAKEN": (2, 4),
    "LTC/USD.KRAKEN":  (2, 8),
    "BCH/USD.KRAKEN":  (2, 8),
}

SIZE_PRECISION = {k: v[1] for k, v in PRICE_SIZE_PRECISION.items()}
PRICE_PRECISION = {k: v[0] for k, v in PRICE_SIZE_PRECISION.items()}
