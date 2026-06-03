from __future__ import annotations

FORBIDDEN_STATUSES = frozenset([
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "EDGE_CONFIRMED",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "PAPER_READY", "SHADOW_READY",
    "CANDIDATE_FOR_LIVE", "CANDIDATE_FOR_PAPER", "PAPER_STRATEGY_PROMOTED",
    "PROMOTION_AUTHORIZED", "READY_FOR_PHASE_0",
])

STUDY_SALT = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"

CANDIDATE_NAMESPACES = [
    "node_fills_by_block/hourly/",
    "hyperliquid/node_fills_by_block/hourly/",
    "hl-mainnet-node-data/node_fills_by_block/hourly/",
]

__all__ = (
    "FORBIDDEN_STATUSES",
    "STUDY_SALT",
    "CANDIDATE_NAMESPACES",
)
