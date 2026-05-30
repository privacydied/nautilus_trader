# Hyperliquid Liquidation/Cascade Overshoot Snapback Phase -2 + Phase 0A v0 — Precommitment

## Study ID
`hyperliquid_liq_overshoot_snapback_phase0a_v0`

## What This Is
- This is **not** a strategy implementation.
- This is **not** exact liquidation attribution unless explicit public fields are found in node_fills_by_block or misc_events_by_block.
- The expected Phase -2 attribution result is `LIQ_OVERSHOOT_BLOCKED_LIQUIDATION_ATTRIBUTION_UNRESOLVED`.
- The cascade-proxy path is the design target: we measure fill-quality during severe downside high-volume cascade-proxy events, not exact liquidation events.

## Data Sources
- **Primary fills:** Official Hyperliquid S3 `s3://hl-mainnet-node-data/node_fills_by_block/hourly/` via existing `node_fills_by_block_adapter.py`.
- **Primary L2/mid:** Official Hyperliquid S3 L2 book archive via existing `hyperliquid_s3_archive.py`, flat 20-level bid/ask parquet.
- **SonarX:** Excluded. SonarX HIP-3 coverage is for builder DEX TradFi-style markets and is not a valid source for this non-HIP-3 altcoin perp universe.

## Filled Hypothesis
During downside cascade-proxy events in Hyperliquid mid-cap perps, passive resting bids placed below the pre-cascade mid may be filled at mechanical overshoot prices. The edge is measured as: simulated passive fill price vs. later public L2 mid after the book reforms.

## Frozen Parameters

### Event Universe (excludes BTC/ETH from event gates)
AAVE, ADA, APT, ARB, ATOM, AVAX, BCH, BNB, DOGE, DOT, ENA, FET, HYPE, INJ, JUP, LINK, LTC, MKR, NEAR, ONDO, OP, PENDLE, SEI, SOL, SUI, TIA, TON, TRX, UNI, WIF, WLD, XRP

### Cascade-Proxy Detector (frozen)
- Pre-event L2 mid available with `book_staleness_ms <= 60_000`
- Trailing 5-minute mid or trade-price return `<= -200 bps`
- 5-minute fills notional >= symbol's rolling 30-day 95th percentile for same 5-minute bucket
- Event low trade price is below pre_event_mid by at least 50 bps
- Cooldown: 60 minutes per symbol
- Past-only thresholds only

### Passive Bid Offsets (bps below pre_event_mid)
25, 50, 75, 100, 150, 200

**Primary offset:** 75 bps

### Fill Models
1. **optimistic_touch_fill_v0:** Filled if any trade print <= bid_px. Fill price = bid_px.
2. **conservative_trade_through_5bps_v0:** Filled if any trade print <= bid_px - 5 bps of pre_event_mid. Fill price = bid_px.

Both must reach 100 filled events for pass. Optimistic-only cannot emit pass.

### Return Horizons
- Primary: 60 minutes
- Secondary: 5, 15, 240 minutes

### Cost Config (frozen)
- `maker_entry_fee_bps = 0.0`
- `taker_exit_fee_bps = 4.5`
- `extra_uncertainty_bps = 5.0`
- `primary_cost_bps = 9.5`
- Stress tiers: net25, net50

### Book Staleness
- `pre_mid_book_staleness_ms <= 60_000`
- `future_mid_book_staleness_ms <= 60_000`
- Lookup: prefer latest snapshot at or before requested timestamp; allow first after only if within threshold and record direction = after.

## Kill Gates
1. L2/fills temporal overlap must exist
2. Conservative fill model must reach >= 100 filled events
3. Filled-event forward net return must beat non-touched cascade controls
4. Circular-shift clustering null must pass (p <= 0.05)
5. Max day concentration: `max_day_event_share <= 0.35`
6. Survivorship ambiguity explicitly reported

## Null/Falsification
- **Timestamp placebo:** 1,000 iterations, seed 20260530
- **Circular-shift clustering:** 1,000 iterations, seed 20260531 (primary falsification gate)

## Auto-Promotion Firewall
- `registry_verdict_authorized: false`
- `promotion_candidate: false`
- `observer_only: true`
- `no_order_intent: true`
- `paper_registry_write_authorized: false`
- `paper_registry_written: false`
- `conductor_promotion_authorized: false`
- `shadow_or_live_unlock: false`

No registry mutation, no paper/shadow/live/bot/systemd unlock.

## Safety
- No orders, private keys, trading auth, live execution, paper trading, shadow execution.
- S3 access requires explicit `--allow-s3-archive-read` and `--allow-network-public` flags.
- Default mode is dry-run/plan-only.
