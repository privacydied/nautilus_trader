# Hyperliquid Supertrend 4h/1d Phase 0 precommitment

Study ID: `hyperliquid_supertrend_4h1d_altcoin_perp_v0`

This document is the frozen Phase 0A/0B/0C feasibility-only precommitment. It is written before any empirical run. It is not a v1 evaluator, null test, FDR run, holdout run, shadow execution study, registry approval, strategy promotion path, paper trading path, live trading path, or bot path.

## Frozen universe

Included symbols, in this exact order:

```text
HYPE
XRP
DOGE
BNB
ADA
LINK
AVAX
SUI
TRX
LTC
BCH
TON
DOT
AAVE
UNI
APT
ARB
OP
SEI
INJ
```

Excluded symbols:

```text
BTC
ETH
SOL
MKR
PEPE
```

Exclusion rationale:

- BTC, ETH, SOL: large-cap majors, likely more arbitraged.
- MKR: insufficient coverage in prior work.
- PEPE: data unavailable in prior work.

Universe hash definition: SHA256 of the UTF-8 text formed by joining the included symbols with `\n`, preserving the exact order above, with no trailing newline.

Universe hash:

```text
2ef0bbb8ae5b4790a8abf4ba479a508671d3d43acb52fe93650654fe4d186eed
```

No symbol substitution is allowed. If any frozen symbol lacks required coverage, Phase 0A fails.

## Structural distinction from locked rejected families

This is not a rerun of the locked Kraken spot OHLCV indicator rejection:

- Venue: Hyperliquid perpetuals, not Kraken spot.
- Cost model: 10 bps primary total round trip, not roughly 80 bps Kraken retail spot round trip.
- Instruments: altcoin perps, not BTC/USD or BTC/ETH majors.
- Timeframes: 4h and 1d, not 5m or 1h.
- Return leg: perp price return plus funding accrual while held.
- Regime filter: exogenous past-only realized volatility percentile.
- Phase: feasibility only, not backtest promotion.

This is distinct from the killed Hyperliquid OI velocity compression study. OI must not be used as a signal, filter, ranking feature, fallback regime proxy, or imported dependency. Archive columns containing OI must be ignored.

## Timeframes

Frozen timeframes:

- 4h
- 1d

Bars use strict UTC alignment. Partial bars are dropped.

## Supertrend parameters

Frozen parameters:

- ATR period: 10.
- ATR multiplier: 3.0.
- ATR method: Wilder-style ATR. The first ATR value is the simple average of the first 10 true ranges; subsequent values use `(prior_atr * 9 + current_tr) / 10`.
- Direction: standard Supertrend direction, encoded +1 for long trend and -1 for short trend.
- Entry: bar close where Supertrend direction flips.
- Exit: next opposite Supertrend flip at bar close, or max hold cap, whichever occurs first.
- Max hold: 30 bars.

No parameter tuning, threshold search, confirmation filter, universe substitution, or post-result redesign is allowed.

## Regime filter definition

Frozen regime filter:

- Asset-level 24h trailing realized volatility.
- Computed from hourly close-to-close log returns.
- Annualized using `sqrt(24 * 365)`.
- 30-day trailing percentile rank.
- Strictly past-only: at timestamp T, the percentile distribution uses prior realized-volatility observations strictly before T; the current observation is ranked against prior observations only.
- Qualification threshold: realized_vol_percentile >= 0.50.
- Regime evaluated at entry timestamp only.
- Regime is not reevaluated during the hold.
- Exit is signal flip or max hold only.

Forbidden v0 filters:

- Funding threshold, sign, percentile, ranking, tail detector, or Phase 0B gate.
- OI, OI velocity, OI compression, open-interest change, or any OI-derived proxy.
- BTC volatility regime, BTC stress window, post-liquidation window, cross-sectional ranking, volume filter, spread filter, or additional Supertrend confirmation filter.

## Funding quarantine rule

Funding may be used only as realized funding paid or received during an already-open Supertrend position. Funding is forbidden as entry signal, direction signal, regime filter, universe filter, threshold, ranking variable, candidate selector, tail detector, or Phase 0B gate.

Funding accrual convention:

- A long receives funding when funding_rate is negative and pays when funding_rate is positive.
- A short receives funding when funding_rate is positive and pays when funding_rate is negative.
- Funding contribution in bps is `-position_sign * funding_rate * 10000`.
- Include settlements with `entry_ts < funding_ts <= exit_ts`.
- Funding at or before entry is excluded.
- Funding after exit is excluded.

## Cost model

Frozen costs:

- Primary: 10 bps total round trip: 6 bps Hyperliquid taker round trip plus 4 bps slippage allowance.
- Diagnostic: 6 bps taker-only round trip.
- Diagnostic cost changes no verdict.
- Funding accrual is included in net return.
- Borrow/financing ignored because perp structure is assumed.

## Data source contract

Accept local archive inputs that can produce canonical tables.

Hourly price table:

```text
timestamp_utc
symbol
open
high
low
close
price_source
```

Funding table:

```text
timestamp_utc
symbol
funding_rate
funding_source
```

Price source may be mark, oracle, mid, or last. Prefer mark price if available. If only validated timestamped snapshots exist, they may be resampled to hourly OHLC. Timestamp parsing must preserve intraday timestamps and must not collapse rows to midnight. Funding settlement history may come from the same validated public archive if present. Funding values are used only for held-position accrual.

Minimum data requirement:

- Longest common available window across all 20 frozen symbols.
- At least 12 months usable coverage per symbol after 30-day warmup exclusion.
- Max single gap < 48h per symbol.
- Total gap < 5% of usable window per symbol.
- Funding data overlaps price data sufficiently to compute held-position funding accrual.
- 30-day realized-vol percentile warmup excluded from event counts and coverage denominators.

If these requirements fail, the Phase 0A verdict is `PHASE0A_INSUFFICIENT_COVERAGE` and Phase 0B/0C do not run.

## Phase 0A gate definitions

Phase 0A checks per symbol:

- Price rows exist.
- Funding rows exist.
- Usable span after warmup >= 12 months.
- Max single price gap < 48h.
- Total price gap < 5%.
- Funding data overlaps price data sufficiently.
- Timestamp parser preserves intraday timestamps.
- No duplicate timestamp collapse.
- No future rows used in derived features.

Phase 0A verdicts:

- `PHASE0A_PASSED`
- `PHASE0A_INSUFFICIENT_COVERAGE`
- `PRECOMMITMENT_HASH_MISMATCH`
- `ARCHIVE_SCHEMA_UNSUPPORTED`
- `ARCHIVE_TIMESTAMP_COLLAPSE_DETECTED`

If Phase 0A is anything except `PHASE0A_PASSED`, stop.

## Phase 0B gate definitions

Phase 0B runs only if Phase 0A passed. For each symbol × timeframe:

- Resample hourly prices to 4h or 1d bars.
- Compute past-only realized-volatility percentile.
- Compute frozen Supertrend.
- Identify trend flips at bar close.
- Apply realized-volatility percentile filter at entry timestamp only.
- Compute holding periods until opposite flip or max hold cap.

4h aggregate gates:

- Total regime-qualified entries >= 200.
- At least 10 symbols with >= 5 qualified entries.
- Max single-symbol fraction <= 0.30.
- Median holding period in [2, 30] bars.

1d aggregate gates:

- Total regime-qualified entries >= 100.
- At least 10 symbols with >= 5 qualified entries.
- Max single-symbol fraction <= 0.30.
- Median holding period in [2, 30] bars.

Phase 0B verdicts:

- `PHASE0B_PASSED`
- `PHASE0B_INSUFFICIENT_QUALIFIED_ENTRIES`
- `PHASE0B_CONCENTRATED_SINGLE_SYMBOL`
- `PHASE0B_HOLD_PERIOD_DEGENERATE`
- `PHASE0B_NO_TIMEFRAME_PASSED`

If one timeframe passes and the other fails, continue to Phase 0C only for the passing timeframe. If neither passes, stop.

## Phase 0C mechanism sanity diagnostic

Phase 0C runs only for Phase-0B-passing timeframes. It is a mechanism sanity check only, not a v1 evaluator.

For each qualified entry:

- Direction comes from Supertrend direction at entry.
- Entry price is the entry bar close.
- Exit price is the exit bar close.
- Gross return is direction-adjusted.
- Funding accrual is summed over funding settlements inside the hold window using the convention above.
- Net primary = gross + funding accrual - 10 bps.
- Net diagnostic = gross + funding accrual - 6 bps.

Chop diagnostic:

- 4h fails if any symbol has > 100 flips per year.
- 1d fails if any symbol has > 30 flips per year.

Funding dominance diagnostic:

- Compute `abs(median_funding_accrual_bps) / abs(median_gross_return_bps)`.
- If the denominator is zero or near-zero, funding dominance fails unless median funding is also zero.

Verdict ladder, frozen sequence:

1. If flip count per symbol per year > 100 on 4h or > 30 on 1d: `PHASE0C_CHOP_REGIME_NOT_RESOLVED`.
2. Else if `abs(median funding) / abs(median gross) > 1.0`: `PHASE0C_FUNDING_DOMINATES_NOT_TREND`.
3. Else if `median_gross_return_bps <= 0`: `PHASE0C_NO_GROSS_EDGE`.
4. Else if `median_net_return_bps_primary <= 0` and `median_gross_return_bps > 0`: `PHASE0C_GROSS_POSITIVE_NET_NEGATIVE`.
5. Else if `median_net_return_bps_primary > 0` and `win_rate_primary < 0.45`: `PHASE0C_POSITIVE_EXPECTANCY_LOW_HIT_RATE`.
6. Else if `median_net_return_bps_primary > 0` and `win_rate_primary >= 0.45`: `PHASE0_READY_FOR_V1_PRECOMMITMENT`.

Do not reorder this ladder.

Overall Phase 0 status:

- If any timeframe gets `PHASE0_READY_FOR_V1_PRECOMMITMENT`, overall status is `PHASE0_READY_FOR_V1_PRECOMMITMENT_DIAGNOSTIC_ONLY`.
- If no timeframe reaches readiness, overall status is the strongest failure reason reached.
- Do not call anything candidate, promoted, approved, trade-ready, execution-ready, authorized, live-ready, bot-ready, or shadow-ready.

## Forbidden outputs

The implementation must not emit these strings as verdicts, statuses, recommendations, or report labels:

```text
CANDIDATE
TRADE_READY
EXECUTION_READY
PROMOTED
APPROVED
AUTHORIZED
LIVE_READY
BOT_READY
SHADOW_READY
```

Allowed readiness phrase is only `PHASE0_READY_FOR_V1_PRECOMMITMENT`, meaning only that a future v1 precommitment may be worth writing.

## Safety constraints

- Public/archive data only.
- No orders.
- No private keys.
- No authentication.
- No execution clients.
- No live trading.
- No paper trading.
- No bot path.
- No shadow execution.
- No strategy promotion.
- No manifest approval.
- No governance ledger approval.
- No `REJECTED_RESEARCH.md` update unless explicitly requested later.
- No null/FDR/holdout.
- No tuning after seeing results.
- No threshold search.
- No parameter optimization.
- No universe substitution.
- No hidden network campaign.
- No long-running backfill unless explicitly requested later.

## No-registry-update rule

This Phase 0 scaffold must not update `REJECTED_RESEARCH.md`, governance registries, promotion manifests, approval ledgers, or any strategy registry. A later explicit user request is required for any registry action.
