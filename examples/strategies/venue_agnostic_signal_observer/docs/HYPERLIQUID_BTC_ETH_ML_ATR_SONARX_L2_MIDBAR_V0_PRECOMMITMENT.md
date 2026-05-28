HYPERLIQUID BTC/ETH ML+ATR SonarX L2 Midbar V0 — Precommitment
================================================================

study_id: hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_v0
Data source: SonarX public Hyperliquid L2 summary snapshots
Source kind: SONARX_L2_SUMMARY_MIDQUOTE

CRITICAL SEMANTIC BOUNDARY:
- This is NOT trade OHLCV.
- This is NOT the original v0.
- This is a quote-derived diagnostic using midquote OHLC bars.
- Volume is unavailable from this source and must not be inferred from book depth.
- top20 depth/spread may be recorded as diagnostics, but do not add them as model features unless a separate precommitment explicitly expands features.

Symbols: BTC and ETH standard perps only.
Timeframe: 1h.
Model: Logistic regression first.
Calibration: Platt.
Thresholds: long 0.55, short 0.40.
Exits: ATR stop + ATR trailing using midquote prices.
Funding: actual Hyperliquid funding from existing local archive.
Splits: derive chronological all-available split from SonarX coverage:
  - train 60%
  - validation 20%
  - test 20%

Minimum gates:
  - min_total_bars_per_symbol: 3000
  - min_train_rows: 1500
  - min_validation_rows: 500
  - min_test_rows: 500

No retuning if gates fail.
Passing only permits local simulated-paper once with explicit quote-derived caveat.
Passing does not unlock live, exchange-paper, bot, order routing, or systemd.

Feature set for first diagnostic:
  - Same as original ML+ATR except any volume-dependent feature is disabled/forbidden.
  - Volume feature set to 0.0 placeholder.
  - Volume must not be used as a model feature.

Statuses:
  SONARX_L2_MIDBAR_V0_PLAN_READY
  SONARX_L2_MIDBAR_V0_READY_FOR_DIAGNOSTIC
  SONARX_L2_MIDBAR_V0_NEEDS_MORE_DATA
  SONARX_L2_MIDBAR_V0_BLOCKED_AWS_CLI_MISSING
  SONARX_L2_MIDBAR_V0_BLOCKED_REQUESTER_PAYS
  SONARX_L2_MIDBAR_V0_BLOCKED_S3_LIST_FAILED
  SONARX_L2_MIDBAR_V0_BLOCKED_COST_OR_SIZE_CAP
  SONARX_L2_MIDBAR_V0_BLOCKED_SCHEMA_UNRECOGNIZED
  SONARX_L2_MIDBAR_V0_BLOCKED_PARSE_FAILED
  SONARX_L2_MIDBAR_V0_BLOCKED_INSUFFICIENT_COVERAGE
  SONARX_L2_MIDBAR_V0_ERROR_INVALID_OUTPUT

Diagnostic statuses:
  SONARX_L2_MIDBAR_DIAG_V0_BACKTEST_READY
  SONARX_L2_MIDBAR_DIAG_V0_NEEDS_MORE_DATA
  SONARX_L2_MIDBAR_DIAG_V0_VALIDATION_CALIBRATION_FAILED
  SONARX_L2_MIDBAR_DIAG_V0_INSUFFICIENT_TEST_TRADES
  SONARX_L2_MIDBAR_DIAG_V0_TEST_ECONOMIC_GATES_FAILED
  SONARX_L2_MIDBAR_DIAG_V0_TEST_DIAGNOSTIC_PASS_PAPER_ONCE_ELIGIBLE
  SONARX_L2_MIDBAR_DIAG_V0_ERROR_INVALID_INPUT
  SONARX_L2_MIDBAR_DIAG_V0_ERROR_LOOKAHEAD_AUDIT_FAILED

Hard constraints:
  - Official/public SonarX S3 data only.
  - No third-party paid full L2/L4 products.
  - No Binance/Kraken/Coinbase proxy bars.
  - No exchange orders.
  - No private keys.
  - No exchange auth.
  - No signing.
  - No live execution.
  - No exchange/broker client imports.
  - No Nautilus live adapter.
  - No systemd changes.
  - No bot path changes.
  - No REJECTED_RESEARCH.md update.
  - No threshold/model/feature/exit retuning.
  - No --allow-nonpassing-bundle-for-test-fixtures for real data.
  - No --loop.
  - No live WebSocket/REST calls.
  - AWS S3 requester-pays access is allowed only for SonarX public archive listing/download.
  - Do not print, log, store, or commit AWS credentials.

Official SonarX public bucket paths:
  - Standard perp BTC: s3://sonarx-hyperliquid-public/market_data/perp/BTC/l2-summary-snapshots/
  - Standard perp ETH: s3://sonarx-hyperliquid-public/market_data/perp/ETH/l2-summary-snapshots/

Data format:
  - Gzipped JSON arrays
  - Each record has: height, block_time, market, bids, asks
  - bids/asks contain up to top 20 levels with px, sz, n
  - Snapshots taken every 20 blocks
  - Bucket is requester-pays

Output naming:
  - mid_open, mid_high, mid_low, mid_close
  - best_bid, best_ask, spread_bps
  - bid_depth_top20, ask_depth_top20
  - source_kind = SONARX_L2_SUMMARY_MIDQUOTE

Compatibility mapping (for ML+ATR):
  - open/high/low/close from midquote OHLC
  - volume = 0.0 placeholder
  - Record loudly in manifest/summary/config that volume is a placeholder
  - Do not use volume as a model feature