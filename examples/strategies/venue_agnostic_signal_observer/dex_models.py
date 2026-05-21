"""
Models for DEX-to-CEX spot dislocation research.

**Observer-only. No execution, no orders, no private endpoints, no keys.**
"""
from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# 1. DEX pool snapshot — one observation from DEX Screener / GeckoTerminal
# ---------------------------------------------------------------------------

@dataclass
class DexPoolSnapshot:
    """A single snapshot of a DEX pool from public data."""

    source: str                        # "dexscreener" | "geckoterminal"
    chain: str                         # "ethereum" | "solana" | "base" | ...
    dex: str                           # "uniswap" | "raydium" | ...
    pair_address: str                  # 0x... pool address
    base_symbol: str                   # e.g. "SOL"
    quote_symbol: str                  # e.g. "USDC" | "USDT" | "WETH"
    base_address: str
    quote_address: str
    price_usd: float | None            # current pool price in USD
    liquidity_usd: float | None        # total pool liquidity in USD
    volume_5m_usd: float | None
    volume_1h_usd: float | None
    volume_24h_usd: float | None
    txns_5m_buys: int | None
    txns_5m_sells: int | None
    price_change_5m_pct: float | None
    price_change_1h_pct: float | None
    ts_event: int                      # nanosecond epoch
    ts_recv: int                       # nanosecond epoch (wall-clock when received)
    raw: dict | None = None

    @property
    def buy_sell_imbalance_5m(self) -> float | None:
        """5-minute buy/sell transaction imbalance in [-1, 1]."""
        if self.txns_5m_buys is None or self.txns_5m_sells is None:
            return None
        total = self.txns_5m_buys + self.txns_5m_sells
        if total <= 0:
            return None
        return (self.txns_5m_buys - self.txns_5m_sells) / total

    def to_dict(self) -> dict:
        d: dict[str, Any] = asdict(self)
        imb = self.buy_sell_imbalance_5m
        if imb is not None:
            d["buy_sell_imbalance_5m"] = round(imb, 4)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: dict) -> DexPoolSnapshot:
        return cls(
            source=d.get("source", ""),
            chain=d.get("chain", ""),
            dex=d.get("dex", ""),
            pair_address=d.get("pair_address", ""),
            base_symbol=d.get("base_symbol", ""),
            quote_symbol=d.get("quote_symbol", ""),
            base_address=d.get("base_address", ""),
            quote_address=d.get("quote_address", ""),
            price_usd=_maybe_float(d.get("price_usd")),
            liquidity_usd=_maybe_float(d.get("liquidity_usd")),
            volume_5m_usd=_maybe_float(d.get("volume_5m_usd")),
            volume_1h_usd=_maybe_float(d.get("volume_1h_usd")),
            volume_24h_usd=_maybe_float(d.get("volume_24h_usd")),
            txns_5m_buys=_maybe_int(d.get("txns_5m_buys")),
            txns_5m_sells=_maybe_int(d.get("txns_5m_sells")),
            price_change_5m_pct=_maybe_float(d.get("price_change_5m_pct")),
            price_change_1h_pct=_maybe_float(d.get("price_change_1h_pct")),
            ts_event=int(d.get("ts_event", 0)),
            ts_recv=int(d.get("ts_recv", 0)),
            raw=d.get("raw"),
        )


# ---------------------------------------------------------------------------
# 2. Dislocation event — a detected DEX-side anomaly
# ---------------------------------------------------------------------------

@dataclass
class DexDislocationEvent:
    """One detected dislocation event from a DEX pool."""

    event_id: str
    chain: str
    dex: str
    pair_address: str
    asset: str                      # canonical CEX symbol, e.g. "SOL"
    quote: str
    signal_type: str                # "dex_price_shock" | "dex_volume_burst" | "dex_liquidity_shock"
    direction: str                  # "long" | "short" | "unknown"
    strength_bps: float
    price_change_bps: float
    volume_zscore: float | None
    liquidity_change_bps: float | None
    buy_sell_imbalance: float | None
    ts_event: int                   # nanosecond epoch
    ts_recv: int                    # nanosecond epoch
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# 3. Forward-return result — DEX event -> CEX spot forward measurement
# ---------------------------------------------------------------------------

@dataclass
class DexCexForwardResult:
    """One forward-return observation for a DEX dislocation event."""

    event_id: str
    asset: str
    source_chain: str
    source_dex: str
    target_venue: str
    target_symbol: str
    horizon_ms: int
    entry_price: float | None
    forward_price: float | None
    gross_bps: float | None
    fee_bps: float | None
    slippage_bps: float | None
    stale_data_buffer_bps: float | None
    quote_mismatch_buffer_bps: float | None
    net_bps: float | None
    valid: bool = True
    rejection_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maybe_float(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _maybe_int(x) -> int | None:
    if x is None:
        return None
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return None
