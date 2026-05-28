# HIP-3 SonarX TradFi L2 Residual Phase -1 Scout

**Study ID**: `hip3_sonarx_tradfi_l2_residual_phase_minus1_v0`

## Scope

Phase -1 historical data-plane feasibility study for SonarX L2 summary
snapshots of Hyperliquid HIP-3 builder DEX TradFi symbols.

### What This Is

- A bounded data-plane feasibility scout
- Parses historical L2 summary snapshots from the public SonarX S3 bucket
- Measures spread, depth, two-sided book rates across sessions
- Joins with official Hyperliquid candle data for diagnostics
- Attempts public equity anchors (Yahoo Finance) for residual analysis
- Produces a Phase -1 gate verdict on whether a Phase 0 precommitment
  review is worth drafting

### What This Is NOT

- A strategy
- A PnL evaluator
- A backtest
- A return study
- A Phase 0 precommitment
- Paper trading
- Live trading
- Conductor promotion
- Registry mutation
- Profitability evidence

## Hard Constraints

- Public data only
- No orders, no private keys, no exchange auth
- No wallet/account endpoints, no signing
- No live execution, no paper broker
- No conductor promotion, no strategy evaluator
- No PnL, no trade signals, no position sizing
- No registry mutation, no Phase 0 precommitment
- No production `subprocess`, `os.system`, or `eval`

## Data Sources

1. **SonarX public requester-pays S3**
   `s3://sonarx-hyperliquid-public/market_data/hip3/{market}/l2-summary-snapshots/{partition}/{height}.json.gz`

2. **Hyperliquid public info endpoint**
   - `perpDexs`, `meta`, `metaAndAssetCtxs`, `candleSnapshot`

3. **Public equity anchors**
   - Yahoo Finance chart endpoint (no auth required)

## Session Classification

| Bucket | ET Time | Description |
|---|---|---|
| `regular_hours` | Mon-Fri 09:30-16:00 | US equity market open |
| `premarket` | Mon-Fri 06:00-09:30 | Pre-market session |
| `after_hours` | Mon-Fri 16:00-23:59 | After-hours session |
| `overnight` | Mon-Fri 00:00-06:00 | Deep overnight |
| `weekend_or_holiday` | Sat-Sun | No US equity session |

**Premarket/overnight boundary**: 06:00 ET

## CLI

```bash
# Dry run (no network calls)
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 \
  --out-root reports/hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 \
  --dry-run

# Real run
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 \
  --out-root reports/hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 \
  --markets xyz:TSLA,flx:TSLA,km:TSLA,cash:TSLA,xyz:AAPL,km:AAPL,xyz:MSFT,cash:MSFT,xyz:NVDA,flx:NVDA,km:NVDA,cash:NVDA \
  --sample-days 30 \
  --sample-mode stratified \
  --max-markets 12 \
  --max-files-per-market 500 \
  --download-budget-bytes 2000000000 \
  --allow-s3-archive-read \
  --allow-network-public \
  --enable-candle-join \
  --enable-anchors \
  --anchor-source yahoo
```

## Artifacts

All artifacts include: study_id, run_id, created_at_utc, git_sha,
git_dirty, repo_root, branch, command_args, safety_mode, source_name,
source_class, and safety confirmation fields.

## Gate Status Taxonomy

| Status | Meaning |
|---|---|
| `SONARX_PHASE_MINUS1_NEXT_PRECOMMITMENT_REVIEW_ALLOWED` | Enough evidence to draft Phase 0 review |
| `SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT` | Default: insufficient evidence |
| `SONARX_L2_LIQUIDITY_BLOCKED` | Spread/depth too poor |
| `SONARX_ANCHOR_BLOCKED` | No usable public anchor |
| `SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED` | Sample too small |
