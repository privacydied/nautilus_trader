# DERIVATIVES V2 CAPTURE RUNBOOK

## Purpose

Authoritative decision tree for running derivatives-source spot-target v2 captures.
Replaces the previous hourly-gate-only approach with a fast-diagnostic capture path.

---

## Phase 1 -- Gate Check

```bash
python examples/strategies/volatility_gate.py
```

Output includes three new fields:

- `capture_permission`: one of `NO_CAPTURE`, `FAST_DIAGNOSTIC_CAPTURE_ONLY`, `FULL_ACTIVE_CAPTURE`
- `fast_capture_eligible`: bool
- `fast_capture_reason`: human-readable explanation or `not_fast_capture_eligible`

The gate checks:

1. **Main hourly gate** (authoritative): BTC/ETH 3h range vs 75/90 bps active thresholds.
2. **Acceleration sub-check**: current 1h / previous 1h ratio >= 2.0.
3. **Fast diagnostic** (early warning): 15m/30m range and acceleration from 1-minute bars.

---

## Decision Tree

### A. `capture_permission == NO_CAPTURE`

Stop cleanly.

- No preflight
- No capture
- No evaluation
- No registry update

Retry windows: US open 14:30 UTC, CPI/FOMC, ETF/news shock, liquidation cascade.

### B. `capture_permission == FAST_DIAGNOSTIC_CAPTURE_ONLY`

The main hourly gate is NOT active but fast diagnostics show a genuine front-of-move setup.

**Capture rules:**

- Run stream-health preflight (Phase 2)
- If preflight passes, run a **900-second diagnostic capture**
- Evaluate after capture
- **Allowed verdicts**: NEEDS_MORE_DATA, MARKET_MODERATE_DIAGNOSTIC, SINGLE_PAIR_CANDIDATE_DIAGNOSTIC, CANDIDATE_FOR_LONGER_OBSERVATION
- **Forbidden verdict**: REJECTED (fast gate did not confirm volatile window)

Output directory pattern:

```
data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS
reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS
```

### C. `capture_permission == FULL_ACTIVE_CAPTURE`

Both main gate and acceleration confirmed.

**Capture rules:**

- Run stream-health preflight (Phase 2)
- If preflight passes, run full **1800-second capture**
- Evaluate after capture
- All verdicts allowed including REJECTED

Output directory pattern:

```
data/derivatives_spot_capture_v2_ACTIVE_YYYYMMDD_HHMMSS
reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS
```

---

## Phase 2 -- Stream-Health Preflight

Required before ANY capture (full or fast). Duration: 30-60 seconds.

**Source streams (Binance USD-M perp aggTrade):**

- BTCUSDT aggTrade
- ETHUSDT aggTrade
- SOLUSDT aggTrade

**Target streams (spot):**

- Kraken: BTC/USD, ETH/USD, SOL/USD
- Coinbase: BTC-USD, ETH-USD, SOL-USD

**OI polling:**

- BTCUSDT, ETHUSDT, SOLUSDT via `https://fapi.binance.com/fapi/v1/openInterest`

**Pass criteria:**

- All Binance perp source streams produce at least one tick
- At least one target venue per symbol produces ticks
- OI returns valid observations or is explicitly marked `flat_or_unknown`
- No zero-tick source stream
- No full subscription failure

**If preflight fails:**

Stop with `STREAM_PREFLIGHT_FAILED`. Report exact failing stream. No capture. No evaluation. No registry update.

---

## Phase 3 -- Capture Commands

### Fast Diagnostic Capture (900s)

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture \
  --source-venue binance_perp \
  --source-symbols BTC/USDT,ETH/USDT,SOL/USDT \
  --target-venues kraken,coinbase \
  --target-symbols BTC/USD,ETH/USD,SOL/USD \
  --duration-seconds 900 \
  --capture-open-interest \
  --open-interest-interval-seconds 5 \
  --out data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS
```

### Full Active Capture (1800s)

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture \
  --source-venue binance_perp \
  --source-symbols BTC/USDT,ETH/USDT,SOL/USDT \
  --target-venues kraken,coinbase \
  --target-symbols BTC/USD,ETH/USD,SOL/USD \
  --duration-seconds 1800 \
  --capture-open-interest \
  --open-interest-interval-seconds 5 \
  --out data/derivatives_spot_capture_v2_ACTIVE_YYYYMMDD_HHMMSS
```

Hard rule: single combined async event loop. No sequential captures. No two-terminal manual capture.

---

## Phase 4 -- Manifest Validation

Before evaluation, inspect `capture_manifest.json`.

Required:

- All source streams present with non-zero ticks
- All target streams present, at least one venue per symbol with non-zero ticks
- OI observations exist or missing OI is explicitly `flat_or_unknown`
- True source-target overlap with meaningful duration
- Per-stream price ranges exist

If no overlap or insufficient overlap:
- Verdict: `NEEDS_MORE_DATA`
- Do NOT mark REJECTED

---

## Phase 5 -- Evaluation Command

**CRITICAL: always pass `--capture-mode` matching the gate decision.** Omitting it
means the evaluator cannot enforce verdict rules (e.g., FAST_DIAGNOSTIC must
never produce REJECTED). Do not use bare defaults when the runbook specifies a
mode.

### FAST_DIAGNOSTIC evaluation

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag \
  --capture-dir data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS \
  --source-venues binance_perp \
  --target-venues kraken,coinbase \
  --symbols BTC/USD,ETH/USD,SOL/USD \
  --signal-types notional_burst,large_trade,signed_imbalance \
  --lookbacks-ms 1000,5000,10000,30000 \
  --baseline-window-ms 60000 \
  --horizons-ms 1000,2000,5000,10000,30000,60000,300000 \
  --cooldown-ms 10000 \
  --fee-bps 40 \
  --slippage-bps 5 \
  --quote-mismatch-buffer-bps 5 \
  --min-events 50 \
  --capture-mode FAST_DIAGNOSTIC \
  --out reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS
```

Allowed verdicts: NEEDS_MORE_DATA, MARKET_MODERATE_DIAGNOSTIC,
SINGLE_PAIR_CANDIDATE_DIAGNOSTIC, CANDIDATE_FOR_LONGER_OBSERVATION.
**REJECTED is forbidden.**

### FULL_ACTIVE evaluation

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag \
  --capture-dir data/derivatives_spot_capture_v2_ACTIVE_YYYYMMDD_HHMMSS \
  --source-venues binance_perp \
  --target-venues kraken,coinbase \
  --symbols BTC/USD,ETH/USD,SOL/USD \
  --signal-types notional_burst,large_trade,signed_imbalance \
  --lookbacks-ms 1000,5000,10000,30000 \
  --baseline-window-ms 60000 \
  --horizons-ms 1000,2000,5000,10000,30000,60000,300000 \
  --cooldown-ms 10000 \
  --fee-bps 40 \
  --slippage-bps 5 \
  --quote-mismatch-buffer-bps 5 \
  --min-events 50 \
  --capture-mode FULL_ACTIVE \
  --out reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS
```

All verdicts allowed including REJECTED (only when all full-active conditions
are met).

Evaluation requirements:

- Clip to true overlap windows only
- Compare against random baseline
- Split by OI bucket
- Missing OI = `flat_or_unknown`
- Report by: source symbol, target venue, target symbol, signal type, lookback, horizon, OI bucket

**OI buckets:**

- `price_up_oi_up`
- `price_up_oi_down`
- `price_down_oi_up`
- `price_down_oi_down`
- `flat_or_unknown`

---

## Phase 6 -- Verdict Rules

### Cost Wall

- Fee: 40 bps
- Slippage: 5 bps
- Quote mismatch: 5 bps
- **Total: 50 bps all-in**

### REJECTED (FULL_ACTIVE_CAPTURE only)

REJECTED is forbidden from FAST_DIAGNOSTIC_CAPTURE_ONLY captures.

Full rejection requires ALL of:

- Main gate was MARKET_ACTIVE + ACCELERATING
- Preflight passed
- Capture overlapped correctly
- Source movement during capture was sufficient
- Target movement during capture was sufficient
- Event count was sufficient
- Random baseline was computed
- No signal group survives 50 bps
- No OI bucket shows candidate behavior
- No pair/horizon/signal-type survives mechanism checks

### CANDIDATE_FOR_LONGER_OBSERVATION

Requires:

- Mean net positive after 50 bps cost
- Median not deeply negative
- Win rate beats baseline
- Real mean beats baseline by margin
- Not single-event-driven
- Not one-cluster-driven
- OI bucket supports the mechanism
- Plausible UK-legal spot-only execution path

### SINGLE_PAIR_CANDIDATE_DIAGNOSTIC

One pair/config looks promising but may be one-cluster, one-symbol, one-horizon, or one-venue driven. Needs longer observation.

---

## Phase 7 -- Registry Update Rules

Only edit `examples/strategies/REJECTED_RESEARCH.md` after a valid empirical capture and evaluation.

- **No capture** = no registry update
- **Preflight failed** = no registry update
- **NEEDS_MORE_DATA** = do not increment REJECTED, keep derivatives v2 open
- **REJECTED** = update only if full active capture with sufficient movement/overlap/events
- **Candidate** = do not implement execution; create longer-observation plan

---

## Fast Diagnostic Trigger Rules (internal to volatility_gate.py)

These determine `FAST_DIAGNOSTIC_CAPTURE_ONLY`:

**ETH path:**

- `fast_market_state` == ACTIVE
- `fast_acceleration_state` == ACCELERATING
- `range_15m_bps` >= 50 OR `range_30m_bps` >= 60
- `accel_15m_ratio` >= 2.0 OR `accel_30m_ratio` >= 2.0

**BTC path (requires ETH at least BUILDING):**

- `fast_market_state` in (ACTIVE, BUILDING)
- `fast_acceleration_state` == ACCELERATING
- `range_15m_bps` >= 30 OR `range_30m_bps` >= 40
- `accel_15m_ratio` >= 1.75 OR `accel_30m_ratio` >= 1.75
- AND `eth_fast_market_state` in (ACTIVE, BUILDING)

If either ETH path or BTC path passes: `FAST_DIAGNOSTIC_CAPTURE_ONLY`.

---

## Phase 8 -- MCPT Export (Post-Evaluation)

After evaluation, run the MCPT export adapter. This decides whether any group
is MCPT-worthy and exports candidate event return series if so.

### FAST_DIAGNOSTIC MCPT export

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
  --report-dir reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS \
  --min-events 50 \
  --cost-floor-bps 50 \
  --max-groups 3
```

FAST_DIAGNOSTIC MCPT exports are **diagnostic only**. They cannot support final
rejection. If the evaluation verdict was MARKET_MODERATE_DIAGNOSTIC, MCPT
results add context but never override the single-window limitation.

### FULL_ACTIVE MCPT export

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
  --report-dir reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS \
  --min-events 50 \
  --cost-floor-bps 50 \
  --max-groups 3
```

FULL_ACTIVE MCPT exports carry more weight because the underlying capture
satisfies all gate conditions. A candidate that survives MCPT falsification
from a FULL_ACTIVE capture is a stronger finding than one from FAST_DIAGNOSTIC.

### Output

- If no candidate exists: prints `MCPT_SKIPPED: <reason>` and writes
  `mcpt_export_summary.json` with `mcpt_skipped: true`.
- If candidates exist: writes CSVs to `<report-dir>/mcpt_inputs/`, one per
  candidate group, plus the summary JSON.
- See `examples/strategies/docs/MCPT_ADAPTER_NOTES.md` for the exported CSV
  schema and MCPT approach descriptions.

### MCPT Approaches (future)

When a candidate survives evaluation and MCPT export identifies it:

- **Approach A (recommended)**: Permute source tick data, re-run signal
  generator and evaluator on permuted data, compute p-value. This is the
  full neurotrader-style bar/path permutation. Requires raw capture data.
- **Approach B (quick sanity check)**: Bootstrap/shuffle the exported event
  return series. Weaker test — does not account for temporal structure.
  **Does not prove a strategy survives MCPT.** Only useful as a fast diagnostic.

---

## Safety Rules

- No private keys
- No orders
- No live trading
- No derivatives execution
- Public-data observer only
- No order-related imports in capture/evaluation path
