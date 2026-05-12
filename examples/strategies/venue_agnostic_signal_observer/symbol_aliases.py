"""Explicit symbol-alias mapping for cross-venue signal research.

This module provides a single source of truth for mapping venue-specific
symbol strings into a canonical (asset, quote) tuple.  It does not infer
mappings silently — unknown symbols raise ``ValueError``.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Canonical asset / quote pair
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalSymbol:
    """Normalized (asset, quote) pair."""

    asset: str   # e.g. "BTC", "ETH", "SOL"
    quote: str   # e.g. "USD", "USDT"

    def __repr__(self) -> str:
        return f"{self.asset}/{self.quote}"


# ---------------------------------------------------------------------------
# Explicit alias registry
# ---------------------------------------------------------------------------

# Mapping: raw venue symbol → CanonicalSymbol.
# All keys are stored in lowercase for case-insensitive lookup.
# Only known, explicitly confirmed mappings are included.

_ALIAS_REGISTRY: dict[str, CanonicalSymbol] = {
    # Coinbase (REST product names, WebSocket product_ids)
    "btc-usd": CanonicalSymbol("BTC", "USD"),
    "eth-usd": CanonicalSymbol("ETH", "USD"),
    "sol-usd": CanonicalSymbol("SOL", "USD"),
    # Coinbase REST also uses "BTC/USD" style in some endpoints
    "btc/usd": CanonicalSymbol("BTC", "USD"),
    "eth/usd": CanonicalSymbol("ETH", "USD"),
    "sol/usd": CanonicalSymbol("SOL", "USD"),

    # Kraken (REST pairs, WebSocket pairs)
    "xbt/usd": CanonicalSymbol("BTC", "USD"),
    "xbtzusd": CanonicalSymbol("BTC", "USD"),
    "xxbtzusd": CanonicalSymbol("BTC", "USD"),
    "btc/usd": CanonicalSymbol("BTC", "USD"),
    "eth/usd": CanonicalSymbol("ETH", "USD"),
    "xethzusd": CanonicalSymbol("ETH", "USD"),
    "sol/usd": CanonicalSymbol("SOL", "USD"),
    "solusd": CanonicalSymbol("SOL", "USD"),

    # Binance
    "btcusdt": CanonicalSymbol("BTC", "USDT"),
    "btcusd": CanonicalSymbol("BTC", "USD"),
    "ethusdt": CanonicalSymbol("ETH", "USDT"),
    "ethusd": CanonicalSymbol("ETH", "USD"),
    "solusdt": CanonicalSymbol("SOL", "USDT"),
    "solusd": CanonicalSymbol("SOL", "USD"),
}


def resolve_symbol(raw_symbol: str) -> CanonicalSymbol:
    """Map a raw venue symbol string to a canonical (asset, quote) pair.

    Args:
        raw_symbol: symbol as returned by the venue, e.g. "XBT/USD", "BTC-USD"

    Returns:
        CanonicalSymbol with the normalized asset and quote currency.

    Raises:
        ValueError: if the symbol is not in the explicit registry.
    """
    key = raw_symbol.strip().lower().replace(" ", "")
    result = _ALIAS_REGISTRY.get(key)
    if result is None:
        raise ValueError(
            f"Unknown symbol '{raw_symbol}'. "
            f"Add an explicit mapping to symbol_aliases._ALIAS_REGISTRY."
        )
    return result


def symbols_match(sym_a: str, sym_b: str) -> bool:
    """Check whether two raw symbols map to the same canonical asset and quote."""
    ca = resolve_symbol(sym_a)
    cb = resolve_symbol(sym_b)
    return ca == cb


def same_asset(sym_a: str, sym_b: str) -> bool:
    """Check whether two raw symbols share the same canonical asset."""
    ca = resolve_symbol(sym_a)
    cb = resolve_symbol(sym_b)
    return ca.asset == cb.asset


def same_quote(sym_a: str, sym_b: str) -> bool:
    """Check whether two raw symbols share the same canonical quote currency."""
    ca = resolve_symbol(sym_a)
    cb = resolve_symbol(sym_b)
    return ca.quote == cb.quote


def quote_mismatch(sym_a: str, sym_b: str) -> bool:
    """Return True if two raw symbols have different quote currencies."""
    return not same_quote(sym_a, sym_b)
