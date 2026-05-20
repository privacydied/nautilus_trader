# Polymarket BTC Price Target Liquidity Probe V0

**Run ID:** polymarket_btc_price_target_liquidity_probe_v0_20260518T222316_41596bd7
**Branch:** feat/polymarket-btc-price-target-liquidity-v0
**SHA:** a5e32d51521812ecbaafa9bb9259e8476a5cc283
**Dirty:** True

## Adapter Path

**Selected data path:** `PUBLIC_REST_FALLBACK_USED`

### Nautilus components inspected

- `nautilus_trader.adapters.polymarket.common.gamma_markets`
- `nautilus_trader.adapters.polymarket.common.parsing`
- `nautilus_trader.adapters.polymarket.common.symbol`
- `nautilus_trader.adapters.polymarket.providers`
- `nautilus_trader.adapters.polymarket.config`
- `nautilus_trader.adapters.polymarket.schemas.book`
- `nautilus_trader.adapters.polymarket.data`
- `nautilus_trader.adapters.polymarket.execution`
- `nautilus_trader.adapters.polymarket.factories`
- `nautilus_trader.adapters.polymarket.loaders`
- `nautilus_trader.adapters.polymarket.websocket.client`
- `nautilus_trader.adapters.polymarket.common.credentials`
- `nautilus_trader.adapters.polymarket.common.conversion`

**Reason for path choice:** Nautilus core not importable (No module named 'nautilus_trader.core.data'). Using direct httpx calls to public Gamma and CLOB REST APIs. The Nautilus data-side modules (PolymarketLiveDataClient, PolymarketInstrumentProvider) require ClobClient (authenticated) and are excluded by Phase 0 safety policy regardless.

## Discovery

- Total markets discovered: 0
- BTC Price Target markets: 0
- BTC Up/Down excluded: 0
- BTC Other excluded: 0
- Non-BTC excluded: 0
- Markets with strike extracted: 0

## Capture

- Duration: 1.65s (requested: 1800s)
- Poll interval: 5s
- BTC proxy: binance BTC/USDT
- Markets sampled: 0
- Total samples: 0
- Valid two-sided: 0
- Primary subset count: 0
- Near-strike / near-expiry: 0
- Convex danger zone: 0

## Liquidity Diagnostics

- Median spread cents: None
- P95 spread cents: None
- Median top depth USD: None
- Two-sided rate: None
- Spread status: None
- Depth status: None
- Two-sided status: None

## Verdict

**NEEDS_MORE_DATA**

Reason: No active BTC Price Target markets found (0 Up/Down markets excluded, 0 total BTC markets discovered)

**V1 recommendation:** V1_BLOCKED_BY_INSUFFICIENT_DATA

---

*No orders. No wallet. No signing. No auth. Observer-only.*
