#!/usr/bin/env python3
"""
Test position sizing logic.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research.strategy import _calculate_position_size
from examples.strategies.kraken_btcusd_research.config import MIN_POSITION_SIZE_BTC


def test_position_sizing():
    """Test position sizing calculations."""
    equity = Decimal("10000")
    risk_percent = 0.0025
    entry_price = Decimal("50000")
    stop_price = entry_price - Decimal("100")  # 100 USD stop distance
    notional_limit = 0.30
    fee_rate = 0.004

    result = _calculate_position_size(
        account_value=equity,
        risk_percent=risk_percent,
        entry_price=entry_price,
        stop_price=stop_price,
        notional_limit=notional_limit,
        min_size=Decimal(str(MIN_POSITION_SIZE_BTC)),
        fee_rate=fee_rate,
    )

    # Expected: risk = 10000 * 0.0025 = 25 USD
    # stop_distance = 100 USD
    # raw_qty = 25 / 100 = 0.25 BTC
    # max_notional = 10000 * 0.30 = 3000 USD
    # max_qty_by_notional = 3000 / 50000 = 0.06 BTC
    # capped_qty = min(0.25, 0.06) = 0.06 BTC
    assert result is not None, "Position size should not be None"
    qty_float = float(result.as_decimal())
    assert 0.05999999 < qty_float < 0.06000001, f"Expected ~0.06 BTC, got {qty_float}"


def test_min_position_size():
    """Test that position size below minimum returns None."""
    # Small equity with wide stop → tiny position
    equity = Decimal("100")
    risk_percent = 0.0025
    entry_price = Decimal("50000")
    stop_price = entry_price - Decimal("10000")  # very wide stop → tiny qty
    notional_limit = 0.30
    fee_rate = 0.004

    result = _calculate_position_size(
        account_value=equity,
        risk_percent=risk_percent,
        entry_price=entry_price,
        stop_price=stop_price,
        notional_limit=notional_limit,
        min_size=Decimal(str(MIN_POSITION_SIZE_BTC)),
        fee_rate=fee_rate,
    )

    # risk = 100 * 0.0025 = 0.25 USD
    # stop_distance = 10000 USD
    # qty = 0.25 / 10000 = 0.000025 BTC  → below 0.001 min → None
    assert result is None, f"Expected None (below min), got {result}"


if __name__ == "__main__":
    test_position_sizing()
    test_min_position_size()
    print("Position sizing tests passed!")
