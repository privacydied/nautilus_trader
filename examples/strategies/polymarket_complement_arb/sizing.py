"""
Sizing logic for Polymarket complement arb strategy.

Determines the maximum safe share quantity for a given opportunity given
configured limits, available depth, and exposure constraints.
"""

from __future__ import annotations

from decimal import Decimal

from .config import ComplementArbConfig
from .models import BookSnapshot, OpportunityDiagnostic


def compute_quote_size(
    diagnostic: OpportunityDiagnostic,
    yes_book: BookSnapshot | None,
    no_book: BookSnapshot | None,
    config: ComplementArbConfig,
    current_total_open_usdc: float = 0.0,
    current_unpaired_exposure_usdc: float = 0.0,
) -> tuple[float | None, str | None]:
    """
    Compute the maximum safe share quantity for a YES+NO pair.

    Equal quantity for both legs. Size is bound by the smallest of:
    - max_order_usdc / max(price, 0.01)
    - available depth (top-of-book or book-walk)
    - remaining market cap
    - remaining total open cap
    - remaining unpaired exposure cap

    Parameters
    ----------
    diagnostic : OpportunityDiagnostic
        The detected opportunity.
    yes_book : BookSnapshot or None
        Current YES book snapshot for depth sizing.
    no_book : BookSnapshot or None
        Current NO book snapshot for depth sizing.
    config : ComplementArbConfig
        Strategy configuration.
    current_total_open_usdc : float
        Current total open exposure across all markets.
    current_unpaired_exposure_usdc : float
        Current unpaired exposure.
    Returns
    -------
    tuple[float | None, str | None]
        (max_quantity, rejection_reason)
        None for max_quantity means the opportunity cannot be sized.
    """
    # 1. Price-normalized max order size
    max_price = max(diagnostic.yes_ask, diagnostic.no_ask, 0.01)
    max_order_shares = min(
        config.max_order_usdc / max_price,
        config.max_market_usdc / max_price,
    )

    if max_order_shares < config.min_order_usdc / max_price:
        return None, f"max_order_shares {max_order_shares:.1f} < min_order requirement"

    if max_order_shares < 1:
        return None, f"max_order_shares {max_order_shares:.1f} < 1 share"

    qty = min(max_order_shares, 1000.0)  # Hard cap in V1

    # 2. Depth constraint
    depth_shares = float("inf")
    if yes_book and yes_book.asks:
        depth_shares = min(depth_shares, yes_book.asks[0][1])
    if no_book and no_book.asks:
        depth_shares = min(depth_shares, no_book.asks[0][1])
    qty = min(qty, depth_shares)

    if qty < 1:
        return None, f"insufficient depth ({depth_shares:.1f} shares)"

    qty_notional = qty * max_price

    # 3. Remaining market cap
    remaining_market = config.max_market_usdc - qty_notional  # approximate
    if remaining_market < config.min_order_usdc:
        return None, f"remaining_market {remaining_market:.2f} < min_order"

    # 4. Total open cap
    new_total = current_total_open_usdc + qty_notional * 2  # both legs
    if new_total > config.max_total_open_usdc:
        return None, f"total_open {new_total:.2f} > max {config.max_total_open_usdc}"

    # 5. Unpaired exposure
    new_unpaired = current_unpaired_exposure_usdc + qty_notional
    if new_unpaired > config.max_unpaired_exposure_usdc:
        return None, f"unpaired {new_unpaired:.2f} > max {config.max_unpaired_exposure_usdc}"

    return qty, None


def compute_min_order_shares(config: ComplementArbConfig) -> float:
    """Compute minimum order size in shares."""
    return config.min_order_usdc / 0.50  # Conservative at p=0.50
