# Nautilus Polymarket Data Adapter Inspection

Inspection performed for the Polymarket BTC Price Target liquidity probe v0.

Date: 2026-05-18

## Nautilus Polymarket Integration Files

### Data-side (safe, no auth needed)

| File | Component | Safe? | Notes |
|---|---|---|---|
| `common/gamma_markets.py` | `list_markets()`, `fetch_fee_schedules()` | Yes | Public Gamma API only. Uses `HttpClient` from nautilus_trader core (Rust-backed). No auth. |
| `common/parsing.py` | `parse_polymarket_instrument()`, `update_instrument()`, `determine_trade_id()` | Yes | Pure decoding/parsing functions. No auth. |
| `common/symbol.py` | `get_polymarket_condition_id()`, `get_polymarket_token_id()`, `get_polymarket_instrument_id()` | Yes | Pure string manipulation. No auth. |
| `schemas/book.py` | `PolymarketBookSnapshot`, `PolymarketQuote`, etc. | Yes | Data structures only. No auth. |

### Instrument-side (requires auth for ClobClient)

| File | Component | Safe? | Notes |
|---|---|---|---|
| `providers.py` | `PolymarketInstrumentProvider` | No | Requires `ClobClient` (API credentials) |
| `config.py` | `PolymarketDataClientConfig` | No | Contains credential fields |
| `common/credentials.py` | Credential helpers | No | Auth-specific code |

### Execution/auth (forbidden for this probe)

| File | Component | Forbidden? |
|---|---|---|
| `data.py` | `PolymarketLiveDataClient` | Yes — requires `ClobClient` |
| `execution.py` | Execution client | Yes |
| `factories.py` | Factory wiring | Yes |
| `loaders.py` | Data loader | Yes — may require config |
| `websocket/client.py` | WebSocket client | Yes — requires `ClobClient` |
| `common/conversion.py` | Conversion/allowance | Yes — auth |
| `order_fill_tracker.py` | Fill tracking | Yes — execution |

## Selected Data Path

`PUBLIC_REST_FALLBACK_USED`

### Reason

1. Nautilus safe data-side components (`gamma_markets.py`) require `nautilus_trader.core.nautilus_pyo3.HttpClient` which is a Rust extension unavailable on this platform (GLIBC 2.36 < required 2.39).
2. Safe parsing modules (`parsing.py`, `symbol.py`) are importable but the `schemas/book.py` structures couple to Nautilus instrument types (`BinaryOption`) which require the full Nautilus model package.
3. The existing `polymarket_btc_updown_liquidity_probe.py` proves httpx works directly with the public Gamma and CLOB REST APIs without any Nautilus imports.
4. Direct public REST calls to `gamma-api.polymarket.com` and `clob.polymarket.com/book/{asset_id}` require no authentication.

### Rejection reasons for other paths

- `NAUTILUS_POLYMARKET_DATA_ADAPTER_USED`: Required `ClobClient` authentication. Excluded by Phase 0 safety policy.
- `MIXED_NAUTILUS_AND_PUBLIC_REST_USED`: Nautilus core imports fail on this platform (GLIBC). If GLIBC were sufficient, this path would be preferred using `gamma_markets` for discovery + public CLOB REST for books.
