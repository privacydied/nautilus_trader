# Polymarket BTC Price Target Liquidity Probe V0

**Run ID:** polymarket_btc_price_target_liquidity_probe_v0_20260519T000249_8e2bb954
**Branch:** feat/funding-oi-crowding-regime-v0
**SHA:** 5a258e61cfef391931153915f4f1d44e5e307e12
**Dirty:** True

## Adapter Path

**Selected data path:** `MIXED_NAUTILUS_AND_PUBLIC_REST_USED`

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

**Reason for path choice:** Nautilus core importable; safe gamma_markets module available for market discovery. Using mixed mode: gamma_markets for discovery + public CLOB REST for orderbooks.

## Discovery

- Total markets discovered: 100
- BTC Price Target markets: 1
- BTC Up/Down excluded: 0
- BTC Other excluded: 0
- Non-BTC excluded: 99
- Markets with strike extracted: 1

## Capture

- Duration: 300.88s (requested: 300s)
- Poll interval: 5s
- BTC proxy: binance BTC/USDT
- Markets sampled: 1
- Total samples: 120
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

Reason: Insufficient primary subset samples: 0 < 5 minimum

**V1 recommendation:** V1_BLOCKED_BY_INSUFFICIENT_DATA

---

*No orders. No wallet. No signing. No auth. Observer-only.*
