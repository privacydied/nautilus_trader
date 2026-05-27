# HIP-3 Builder-DEX TradFi Forward Recorder v0

## Purpose

Public-data-only forward recorder for TSLA/AAPL/MSFT/NVDA builder DEX symbols.

Captures the missing execution-feasibility evidence going forward, since the official
Hyperliquid S3 historical L2 archive does not include builder DEX symbols.

This is **not** a strategy. Not paper trading. Not live trading. Not Phase 0.

## What It Captures

1. **Current L2 books** — public `l2Book` for each resolved builder DEX symbol
2. **Asset contexts** — marks, oracles, funding, open interest via `metaAndAssetCtxs`
3. **Candle continuity** — `candleSnapshot` for 1m (2h) and 15m (24h) every 5 minutes
4. **External anchors** — Yahoo Finance prices for TSLA/AAPL/MSFT/NVDA (optional)
5. **Calendar/off-hours labels** — regular_hours, premarket, after_hours, overnight, weekend
6. **Spread/depth/staleness metrics** — computed per-poll, no PnL
7. **Optional diagnostic residuals** — mid vs anchor in bps, no trade triggers

## Safety Constraints

- Public data only
- No orders, no private keys, no exchange auth
- No signing, no wallet, no user/account endpoints
- No live execution, no paper broker, no conductor promotion
- No PnL, no trade signals, no position sizing
- No registry mutation, no Phase 0 precommitment
- No `subprocess`, `os.system`, or `eval` in production code

## Allowed Request Types

- `perpDexs` — builder DEX enumeration
- `meta` — universe metadata
- `metaAndAssetCtxs` — asset context per DEX
- `l2Book` — current limit order book
- `candleSnapshot` — historical OHLCV bars

## Forbidden Request Types

- `clearinghouseState`, `userState`, `openOrders`
- `historicalOrders`, `orderStatus`, `userFills`
- `userFees`, `portfolio`, `subAccounts`

## Symbol Resolution

At startup, the recorder:

1. Queries `perpDexs` to enumerate all builder DEX namespaces
2. Queries `metaAndAssetCtxs` for each DEX
3. Resolves TSLA/AAPL/MSFT/NVDA to their actual API symbols
4. Persists resolution into `symbol_resolution.json`

Expected forms (may vary):
- `cash:TSLA`
- `km:AAPL`
- `cash:MSFT`
- `cash:NVDA`

## CLI Usage

### One-shot smoke test

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
  --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
  --symbols TSLA,AAPL,MSFT,NVDA \
  --poll-seconds 60 \
  --once \
  --allow-network-public \
  --enable-anchors \
  --anchor-source yahoo
```

### Long-running capture (7 days)

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
  --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
  --symbols TSLA,AAPL,MSFT,NVDA \
  --poll-seconds 60 \
  --duration-minutes 10080 \
  --allow-network-public \
  --enable-anchors \
  --anchor-source yahoo
```

### Dry run (no network)

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
  --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
  --symbols TSLA,AAPL,MSFT,NVDA \
  --dry-run
```

### Stop after init (resolve symbols, write manifest, exit)

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
  --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
  --symbols TSLA,AAPL,MSFT,NVDA \
  --stop-after-init \
  --allow-network-public
```

### Summarize captured data

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
  --summarize reports/hip3_builder_dex_tradfi_forward_recorder_v0/<run_id>
```

## Output Structure

```
reports/hip3_builder_dex_tradfi_forward_recorder_v0/<run_id>/
  run_manifest.json          # Run metadata, symbols, safety flags
  symbol_resolution.json     # Display -> API symbol mapping
  capture_config.json        # CLI configuration snapshot
  capture_status.json        # Live status, poll counts, errors
  l2_snapshots/YYYYMMDD.jsonl  # L2 book snapshots (append-only)
  asset_context_snapshots/YYYYMMDD.jsonl  # Asset context (append-only)
  candle_snapshots/YYYYMMDD.jsonl  # Candle continuity (append-only)
  anchor_snapshots/YYYYMMDD.jsonl  # External anchors (append-only)
  derived_metrics/YYYYMMDD.jsonl   # Spread/depth/residuals (append-only)
  heartbeat.jsonl            # Poll heartbeats (append-only)
  capture_index.jsonl        # Event index (append-only)
  errors.jsonl               # Per-poll errors (append-only)
  forward_capture_summary.json  # Post-hoc summary
  forward_capture_summary.md    # Post-hoc summary (Markdown)
```

## Status Taxonomy

| Status | Meaning |
|--------|---------|
| `HIP3_FORWARD_RECORDER_READY` | Initial state |
| `HIP3_FORWARD_RECORDER_DRY_RUN_READY` | Dry run complete, no network |
| `HIP3_FORWARD_RECORDER_SYMBOLS_RESOLVED` | Symbols found, ready to poll |
| `HIP3_FORWARD_RECORDER_CAPTURE_STARTED` | Polling loop started |
| `HIP3_FORWARD_RECORDER_CAPTURE_RUNNING` | Actively capturing |
| `HIP3_FORWARD_RECORDER_CAPTURE_COMPLETE` | Normal exit |
| `HIP3_FORWARD_RECORDER_CAPTURE_ERROR` | Errors during capture |
| `HIP3_FORWARD_RECORDER_PUBLIC_API_BLOCKED` | API unreachable |
| `HIP3_FORWARD_RECORDER_SYMBOL_RESOLUTION_FAILED` | Cannot resolve symbols |
| `HIP3_FORWARD_RECORDER_ANCHOR_UNAVAILABLE` | Anchors failed (non-fatal) |
| `HIP3_FORWARD_RECORDER_PARTIAL_CAPTURE` | Some errors, some data |
| `HIP3_FORWARD_RECORDER_NO_L2_BOOK` | No L2 data received |
| `HIP3_FORWARD_RECORDER_NO_CANDLES` | No candle data received |

## Data Sufficiency Thresholds

For future Phase -1 tail scout readiness:

- At least 7 calendar days of capture
- At least 3 US regular sessions
- At least 3 overnight sessions
- At least 500 off-hours L2 samples per primary symbol
- At least 500 anchor-aligned off-hours samples per primary symbol
- Median spread <= 50 bps
- P75 spread <= 100 bps
- Non-empty two-sided book rate >= 80%

## Summary Verdicts

| Verdict | Meaning |
|---------|---------|
| `FORWARD_DATA_UNDERPOWERED` | Insufficient data for analysis |
| `FORWARD_DATA_EXECUTION_FEASIBILITY_MEASURABLE` | Enough data for feasibility study |
| `FORWARD_DATA_ANCHOR_BLOCKED` | No anchor data available |
| `FORWARD_DATA_LIQUIDITY_BLOCKED` | Insufficient liquidity evidence |

## No-Go List

The recorder does NOT emit:
- `REJECTED`
- `PROFITABLE`
- `ALPHA_FOUND`
- `TRADE_READY`
- `EXECUTION_READY`
- `LIVE_READY`
- `READY_FOR_PHASE_0`
- `CANDIDATE_FOR_LIVE`
- `PAPER_STRATEGY_PROMOTED`
- `PROMOTION_AUTHORIZED`
- `EDGE_CONFIRMED`

## Relationship to Prior Work

This recorder addresses the gap identified by the corrected HIP-3 builder DEX scout:

- **Phase C**: Official S3 L2/asset_ctxs absent for builder DEX symbols
- **Phase C3**: Official `candleSnapshot` provides ~125d of OHLCV price history
- **Forward Recorder**: Captures current L2 + execution-feasibility data going forward

The recorder runs in parallel to the scout. It does not depend on scout results.
It does not mutate the scout's registry entries.

## Suggested Systemd Command

For a 7-day capture:

```bash
ExecStart=/usr/bin/bash -c '\
  cd /mnt/nasirjones/py/nautilus_trader && \
  source .venv/bin/activate && \
  uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
    --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
    --symbols TSLA,AAPL,MSFT,NVDA \
    --poll-seconds 60 \
    --duration-minutes 10080 \
    --allow-network-public \
    --enable-anchors \
    --anchor-source yahoo \
    >> /var/log/hip3_forward_recorder.log 2>&1 \
'
```
