"""V7: Configuration for L2 maker paper simulator."""
from dataclasses import dataclass, field
from typing import List


@dataclass
class V7Config:
    """Configuration for the maker-fill paper simulator."""

    symbols: List[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD"])

    # Observation duration
    duration_seconds: float = 600.0

    # WebSocket reconnect
    max_reconnect_attempts: int = 5
    reconnect_delay_seconds: float = 2.0

    # Quote parameters
    quote_side: str = "both"  # "bid", "ask", "both"
    quote_offset_bps: float = 0.0  # offset from best bid/ask (0 = at touch)
    quote_lifetime_seconds: float = 5.0  # max time a quote stays active
    max_cancel_rate: float = 0.95  # if cancel rate exceeds this, stop quoting

    # Quote cancellation triggers
    cancel_on_mid_move_bps: float = 5.0  # cancel if mid moves this far
    cancel_on_spread_collapse_bps: float = 1.0  # cancel if spread shrinks below
    cancel_on_imbalance_flip: bool = True  # cancel if book imbalance reverses

    # Fill model
    fill_model: str = "pessimistic"  # "pessimistic", "neutral", "optimistic"
    fill_penalty_bps: float = 2.0  # extra cost for queue uncertainty

    # Adverse selection
    adverse_selection_windows: List[float] = field(
        default_factory=lambda: [1.0, 5.0, 30.0, 60.0]
    )

    # Stale book detection
    stale_book_max_age_seconds: float = 10.0

    # Fees (bps)
    maker_fee_bps: float = 3.0  # Kraken maker fee ~0.16% for BTC/USD tier 1, use 3bps conservative

    # Minimum spread to quote (bps) — don't quote if spread too tight
    min_spread_bps: float = 0.01

    # Output
    output_dir: str = "reports/v7_l2_maker_paper"

    # Kraken WebSocket URL (public)
    ws_url: str = "wss://ws.kraken.com/v2"
    rest_url: str = "https://api.kraken.com"
