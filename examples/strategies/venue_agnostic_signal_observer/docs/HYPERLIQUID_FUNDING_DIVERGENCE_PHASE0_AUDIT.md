# Hyperliquid funding divergence Phase 0 audit

Study ID: `hyperliquid_funding_divergence_phase0`

This is a one-shot Phase 0 distribution-only funding-divergence audit. It asks whether Hyperliquid funding diverges from reference venue funding enough to justify a future, separate v1 precommitment.

This is not a precommitment. This is not an evaluator. This is not a return study. This is not a PnL study. This is not a shadow execution study. This uses public/archive data only, observer-only, no API keys, no auth, no private keys, no orders, no execution, no live trading, no paper trading, no bot path, no forward returns, no PnL, no post-event price-path inspection, no dependent-variable inspection, no null testing, no FDR, no holdout, and no candidate promotion.

## Mechanical benchmark

The prior cross-exchange funding dispersion carry v1 study found Binance-vs-Bybit BTC/ETH funding dispersion had no useful tail: BTC max absolute dispersion 4.14 bps, ETH max absolute dispersion 4.49 bps, and zero events above the frozen 5 bps minimum threshold. The prior should be read as: probably no tail unless Hyperliquid is structurally looser.

## Kill criteria recorded before any histogram

The runner writes `pre_data_kill_criteria` into `manifest.json` before data loading. The source constants are defined in `hyperliquid_funding_divergence_phase0.py` and are quoted here to prevent document/code drift:

- `KILL_CRITERION_MAX_ABS_BPS = 10.0`
- `KILL_CRITERION_P99_ABS_BPS = 8.0`

Kill rule:

- If both BTC and ETH have max absolute Hyperliquid-vs-reference funding divergence below `KILL_CRITERION_MAX_ABS_BPS = 10.0` bps hourly, status is `PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`.
- Also kill if both BTC and ETH have p99 absolute Hyperliquid-vs-reference funding divergence below `KILL_CRITERION_P99_ABS_BPS = 8.0` bps hourly, status is `PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`.
- If exactly one asset clears, status is `PHASE0_DISTRIBUTION_READY_SINGLE_ASSET`.
- If both assets clear, status is `PHASE0_DISTRIBUTION_READY`.
- If either BTC or ETH has fewer than 100 aligned Hyperliquid-reference observations, status is `PHASE0_NEEDS_MORE_DATA`, unless source unavailable or unusable.

Allowed statuses are only:

- `PHASE0_DISTRIBUTION_READY`
- `PHASE0_DISTRIBUTION_READY_SINGLE_ASSET`
- `PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`
- `PHASE0_NEEDS_MORE_DATA`
- `PHASE0_ALIGNMENT_FAILED`
- `PHASE0_SOURCE_UNAVAILABLE`
- `PHASE0_DATA_UNUSABLE`

Forbidden statuses include candidate, rejected, trade-ready, execution-ready, shadow-ready, bot-ready, promoted, ready-for-v1, and precommitment-ready language.

## Data-source rules

Minimum required assets are BTC and ETH. Reference venues are Binance funding if available and Bybit funding if available. Hyperliquid must come from an existing local Hyperliquid funding/archive path or another safe public unauthenticated source already available to the repo. If no safe source is available, stop with `PHASE0_SOURCE_UNAVAILABLE`; do not substitute Binance/Bybit-only data.

## Normalization

For each row, preserve venue, asset, native symbol, timestamp UTC, native funding rate, native interval hours, hourly funding bps, projected 8h funding bps, source path/endpoint, and parse status.

`hourly_funding_bps = native_funding_rate * 10000 / native_interval_hours`

`projected_8h_funding_bps = hourly_funding_bps * 8`

Every CSV column carrying funding or divergence units must include `_bps_hourly` or `_bps_projected_8h`.

## Interval detection and hard fail

Native funding interval is detected from adjacent timestamps. Expected intervals are Hyperliquid 1 hour, Binance 8 hours, and Bybit 8 hours. Any mismatch produces `PHASE0_DATA_UNUSABLE` and records the offending source, expected interval, detected interval, and unusable reason in the manifest.

## Alignment method and no-lookahead proof

For each Hyperliquid timestamp, the reference row is the latest reference row with `reference_timestamp <= hyperliquid_timestamp`. Future reference rows are never eligible. There is no interpolation and no covering-interval logic. Missing prior reference rows are unaligned and counted.

Primary divergence:

`divergence_bps_hourly = hyperliquid_funding_bps_hourly - reference_funding_bps_hourly`

`absolute_divergence_bps_hourly = abs(divergence_bps_hourly)`

If both Binance and Bybit are available, the kill decision prefers HL minus median reference. If only one reference exists, the report clearly states that limitation.

## Required outputs

Each run writes under `reports/hyperliquid_funding_divergence_phase0/<run_id>/`:

- `summary.json`
- `alignment_summary.csv`
- `divergence_distribution.csv`
- `divergence_bucket_counts.csv`
- `persistence_half_life.csv`
- `calendar_stratification.csv`
- `PHASE0_REPORT.md`
- `manifest.json`

## Non-coverage and mechanism separation

This Phase 0 is separate from Family 2 funding crowding reversal, Family 3 funding x OI variants, and Hyperliquid BTC->LINK fixed-cell paper replay.

The invalidated +39.47 bps BTC->LINK replay result is not evidence for this funding-divergence hypothesis.

The corrected +6.13 bps BTC->LINK replay result is not evidence against this funding-divergence hypothesis.

BTC->LINK replay involved cross-asset beta-lag. This audit involves same-asset funding divergence and venue-specific crowding pressure.

If the future return leg is BTC spot, the hypothesis risks laundering Family 2 / Family 3. A future v1 must make the structural difference load-bearing, preferably through a Hyperliquid perp return leg, Hyperliquid venue crowding, explicit funding-paid-while-held treatment, or another clearly distinct mechanism.

## Self-test for any future v1

If this hypothesis advances to v1 and the v1 return leg is BTC spot, the specific difference must be: the venue-specific crowding pressure on Hyperliquid, identified via HL-vs-reference funding divergence, is the conditioning variable, not BTC spot funding extremes alone. If that distinction is not load-bearing in the v1 design, the separation is not clean and the future v1 return leg should be Hyperliquid perp specifically.

## Next-step rules

If killed: no v1 precommitment should be written.

If single-asset distribution-ready: future v1 must predeclare one-asset vs two-asset scope and count family size accordingly.

If two-asset distribution-ready: next artifact is a separate v1 precommitment, not an evaluator.
