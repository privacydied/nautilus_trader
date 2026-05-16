"""
Configuration for the Polymarket complement arb strategy.

All config fields are documented with their purpose and default values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Platform = Literal["GLOBAL"]
Mode = Literal["observe", "backtest", "live"]


@dataclass(frozen=True)
class ComplementArbConfig:
    """Configuration for the Polymarket complement arb strategy."""

    # Platform
    platform: Platform = "GLOBAL"

    # Mode
    mode: Mode = "observe"

    # Dry-run: when True, no orders are submitted even in live mode
    dry_run: bool = True

    # --- Sizing ---
    max_order_usdc: float = 100.0
    max_market_usdc: float = 500.0
    max_total_open_usdc: float = 2000.0
    max_unpaired_exposure_usdc: float = 200.0
    max_session_loss_usdc: float = 50.0
    min_order_usdc: float = 10.0
    min_top_depth_usdc: float = 50.0

    # --- Edge and cost ---
    min_net_edge_per_share: float = 0.005  # $0.005 per share minimum net edge
    leg_risk_buffer_per_share: float = 0.002  # $0.002 per share for leg risk
    signing_latency_buffer_per_share: float = 0.001  # $0.001 per share for signing latency
    gas_redeem_buffer_per_pair: float = 0.001  # $0.001 per pair for gas/redeem

    # --- Data quality ---
    max_book_age_ms: float = 5000.0  # 5 seconds max book age
    cancel_replace_min_interval_ms: float = 2000.0  # 2 seconds between cancel+submit

    # --- Timing ---
    one_leg_timeout_ms: float = 30000.0  # 30 seconds to close second leg
    resolution_danger_window_seconds: float = 3600.0  # 1 hour before resolution
    min_time_to_resolution_seconds: float = 7200.0  # 2 hours minimum
    max_time_to_resolution_seconds: float = 2592000.0  # 30 days maximum

    # --- Universe ---
    max_markets: int = 20
    market_slug_allowlist: tuple[str, ...] = ()
    event_slug_allowlist: tuple[str, ...] = ()
    category_allowlist: tuple[str, ...] = ()

    # --- Rebates (reporting only, not in gate) ---
    maker_rebates_enabled_for_reporting: bool = False

    # --- Shadow validation sufficiency gates ---
    min_observer_windows: int = 5
    min_detected_opportunities: int = 50
    min_pessimistic_paired_fills: int = 20
    min_same_condition_valid_opportunities: int = 30
    min_non_dust_opportunities: int = 30

    # --- Diagnostic size-ladder thresholds ---
    # Pinned pre-run (Task 3) — do not tune after seeing results.
    diagnostic_min_economic_net_harvest_usdc: float = 0.10
    # Minimum net harvest in USDC for a quote size to be classified as economic.
    # A 5-share quote yielding $0.003/share = $0.015 is dust.
    # A 50-share quote yielding $0.003/share = $0.15 is economic.
    # Default $0.10 = standard economic soil below which aggregate harvest is
    # indistinguishable from noise.
    diagnostic_min_net_edge_per_share: float = 0.005
    # Defaults to the same precommitted min_net_edge_per_share from strategy config.

    # --- Live guards ---
    live_acknowledgement: bool = False
