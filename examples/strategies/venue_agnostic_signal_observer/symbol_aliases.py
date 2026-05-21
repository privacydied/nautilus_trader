"""
Explicit symbol-alias mapping for cross-venue signal research.

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
    "xethzusd": CanonicalSymbol("ETH", "USD"),
    "solusd": CanonicalSymbol("SOL", "USD"),

    # Binance (spot and perp -- perp uses USDT quote, not USD)
    "btcusdt": CanonicalSymbol("BTC", "USDT"),
    "btc/usdt": CanonicalSymbol("BTC", "USDT"),
    "ethusdt": CanonicalSymbol("ETH", "USDT"),
    "eth/usdt": CanonicalSymbol("ETH", "USDT"),
    "solusdt": CanonicalSymbol("SOL", "USDT"),
    "sol/usdt": CanonicalSymbol("SOL", "USDT"),
    "btcusd": CanonicalSymbol("BTC", "USD"),
    "ethusd": CanonicalSymbol("ETH", "USD"),

    # Binance altcoins (USDT quote)
    "linkusdt": CanonicalSymbol("LINK", "USDT"),
    "link/usdt": CanonicalSymbol("LINK", "USDT"),
    "avaxusdt": CanonicalSymbol("AVAX", "USDT"),
    "avax/usdt": CanonicalSymbol("AVAX", "USDT"),
    "adausdt": CanonicalSymbol("ADA", "USDT"),
    "ada/usdt": CanonicalSymbol("ADA", "USDT"),
    "dogeusdt": CanonicalSymbol("DOGE", "USDT"),
    "doge/usdt": CanonicalSymbol("DOGE", "USDT"),

    # Kraken altcoins
    "link/usd": CanonicalSymbol("LINK", "USD"),
    "linkusd": CanonicalSymbol("LINK", "USD"),
    "avax/usd": CanonicalSymbol("AVAX", "USD"),
    "avaxusd": CanonicalSymbol("AVAX", "USD"),
    "ada/usd": CanonicalSymbol("ADA", "USD"),
    "adausd": CanonicalSymbol("ADA", "USD"),
    "doge/usd": CanonicalSymbol("DOGE", "USD"),
    "dogeusd": CanonicalSymbol("DOGE", "USD"),
    "btcust": CanonicalSymbol("BTC", "UST"),
    "ethust": CanonicalSymbol("ETH", "UST"),
    "solust": CanonicalSymbol("SOL", "UST"),
    "link:usd": CanonicalSymbol("LINK", "USD"),
    "link:ust": CanonicalSymbol("LINK", "UST"),
    "doge:usd": CanonicalSymbol("DOGE", "USD"),
    "doge:ust": CanonicalSymbol("DOGE", "UST"),
    "avax:usd": CanonicalSymbol("AVAX", "USD"),
    "avax:ust": CanonicalSymbol("AVAX", "UST"),
    # Also accept plain concatenated forms for 4-letter assets (just in case)
    "linkust": CanonicalSymbol("LINK", "UST"),
    "dogeust": CanonicalSymbol("DOGE", "UST"),
    "avaxust": CanonicalSymbol("AVAX", "UST"),

    # OKX (instId form uses hyphenated quote, e.g. BTC-USDT, BTC-USD)
    "btc-usdt": CanonicalSymbol("BTC", "USDT"),
    "eth-usdt": CanonicalSymbol("ETH", "USDT"),
    "sol-usdt": CanonicalSymbol("SOL", "USDT"),
    "link-usdt": CanonicalSymbol("LINK", "USDT"),
    "doge-usdt": CanonicalSymbol("DOGE", "USDT"),
    "avax-usdt": CanonicalSymbol("AVAX", "USDT"),
    "btc-usdc": CanonicalSymbol("BTC", "USDC"),
    "eth-usdc": CanonicalSymbol("ETH", "USDC"),
    "sol-usdc": CanonicalSymbol("SOL", "USDC"),
    "btcusdc": CanonicalSymbol("BTC", "USDC"),
    "ethusdc": CanonicalSymbol("ETH", "USDC"),
    "solusdc": CanonicalSymbol("SOL", "USDC"),

    # Coinbase altcoins
    "link-usd": CanonicalSymbol("LINK", "USD"),
    "avax-usd": CanonicalSymbol("AVAX", "USD"),
    "ada-usd": CanonicalSymbol("ADA", "USD"),
    "doge-usd": CanonicalSymbol("DOGE", "USD"),
}


def resolve_symbol(raw_symbol: str) -> CanonicalSymbol:
    """
    Map a raw venue symbol string to a canonical (asset, quote) pair.

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
