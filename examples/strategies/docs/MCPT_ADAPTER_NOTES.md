# MCPT Adapter Notes

## What is MCPT?

Monte Carlo Permutation Testing (MCPT) is a **falsification** tool, not a signal generator.
It tests whether a strategy's observed performance (e.g., profit factor, mean return) could
have arisen by chance from randomly permuted data. Low p-value → signal is likely real;
high p-value → signal is likely overfit noise.

Reference: `examples/strategies/mcpt-main/` (neurotrader888/mcpt, MIT license).

---

## MCPT Repo — Expected Input Shape

The MCPT repo (`bar_permute.py`) expects **OHLC bar data** as a `pd.DataFrame` with columns:
- `open`, `high`, `low`, `close` (prices)
- Datetime index

It decomposes bars into:
1. Log-price open gaps (open relative to previous close)
2. Intra-bar relative prices (H/L/C relative to open)
3. Shuffles these components independently to generate permuted price series

The API entrypoint is:
```python
from bar_permute import get_permutation
permuted_df = get_permutation(ohlc_df, start_index=0, seed=None)
```

Usage pattern in example scripts:
```python
# For each permutation:
perm_df = get_permutation(real_df, start_index=train_window)
# Re-run strategy on permuted data, compute profit factor
# Count how many permuted PFs >= real PF → p-value
```

---

## Our Data vs. MCPT Expected Input

**The mismatch**: Our signal observer produces **event-level return series** (forward returns
after impulse signals), not OHLC bar series. The MCPT `bar_permute.get_permutation()` needs
OHLC bars to permute.

**Two approaches to bridge this gap:**

## Approach Comparison

### Approach A: Permute Source Tick Data, Re-run Signal+Evaluation (Recommended)

This is the conceptually correct MCPT route, matching neurotrader888's bar-permutation
methodology. The key insight: MCPT falsifies a *strategy*, not a *return series*.
Permuting the raw data and re-running the signal generator preserves the strategy's
selection logic (e.g., "fire on notional burst ratio >= 3x") while destroying temporal
patterns, producing a null distribution of strategy performance under random data.

Steps:
1. Take the original captured trade tick data (source + target).
2. Permute source tick timestamps (or shuffle source-side intra-bar structure using
   `bar_permute.get_permutation` reconstructed from tick data).
3. Re-run `TradeFlowImpulseSignalGenerator` on the permuted source data.
4. Re-evaluate forward returns on the real target data.
5. Compare the resulting mean_net_bps distribution to the observed candidate's mean_net_bps.
6. Compute p-value = fraction of permutations where permuted performance >= observed.

Requires raw capture data (not just the report). This is the only approach that
constitutes a proper MCPT falsification.

### Approach B: Bootstrap on Exported Event Returns (Quick Sanity Check Only)

Treat the signed net returns from the exported CSV as a return series, then:
1. Bootstrap/shuffle the returns N times.
2. Recompute mean and win rate for each shuffle.
3. Compute p-value = fraction where shuffled mean >= observed mean.

**This is a weaker test.** It does not account for temporal structure, signal selection
bias, or look-ahead effects. It answers "could random returns produce this mean?" —
not "could random data fool the signal generator into producing these signals?"

**Approach B does not prove a strategy survives MCPT.** It is only useful as a fast
diagnostic sanity check. Any candidate worth pursuing must eventually be tested with
Approach A.

---

## Command to Run After Capture

### Step 1: Evaluate (pick the correct capture-mode)

FAST_DIAGNOSTIC:
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

FULL_ACTIVE:
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

### Step 2: Export MCPT candidates

```bash
# Use the report dir from Step 1:
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
  --report-dir reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_YYYYMMDD_HHMMSS \
  --min-events 50 \
  --cost-floor-bps 50 \
  --max-groups 3
```

Or for FULL_ACTIVE:
```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
  --report-dir reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS \
  --min-events 50 \
  --cost-floor-bps 50 \
  --max-groups 3
```

If candidates exist, exported CSVs will appear in `<report-dir>/mcpt_inputs/`.
If no candidates (all cost-floor dust), will print `MCPT_SKIPPED: ...`.

### Complete Tomorrow Sequence

```
gate → preflight → capture (with correct --duration-seconds) → 
evaluation (with --capture-mode) → MCPT export
```

Our `mcpt_export.py` produces CSVs with these columns:

| Column | Type | Description |
|---|---|---|
| `event_timestamp_ns` | int | Nanosecond Unix epoch of signal event |
| `source_venue` | str | e.g., "binance_perp" |
| `source_symbol` | str | e.g., "BTC/USD" |
| `target_venue` | str | e.g., "kraken" |
| `target_symbol` | str | e.g., "BTC/USD" |
| `asset` | str | e.g., "BTC" |
| `signal_type` | str | e.g., "trade_flow_impulse" |
| `flow_signal_type` | str | e.g., "notional_burst", "signed_imbalance" |
| `lookback_ms` | int | Signal lookback window |
| `horizon_ms` | int | Forward return horizon |
| `oi_bucket` | str | e.g., "price_up_oi_up" |
| `direction` | str | "long" or "short" |
| `raw_return_bps` | float | Raw (pre-cost) forward return in bps |
| `net_return_bps` | float | Post-cost forward return in bps |
| `strength` | float | Signal strength metric |
| `source_move_bps` | float | Source-side price move in bps |
| `fee_bps` | float | Fee cost in bps |
| `slippage_bps` | float | Slippage cost in bps |
| `quote_mismatch_buffer_bps` | float | Quote mismatch buffer in bps |
| `valid` | bool | Whether the forward return was valid |
| `signal_id` | str | UUID linking to the signal event |

---

## Conversion Layer Needed

To use `bar_permute.py` directly, we would need a converter that:

1. Loads our export CSV → extracts event timestamps and returns
2. Optionally: reconstructs a pseudo-OHLC series from tick data for permutation
3. OR: implements a return-series bootstrap permutation (Approach B)

**Recommendation**: Build `mcpt_runner.py` later, after a real candidate emerges.
For now, the export adapter provides the clean data interface. When a candidate
survives evaluation, we can either:
- Wire `bar_permute.py` with pseudo-bars from tick data (Approach A)
- Write a simple bootstrap p-value calculator using only the return series (Approach B)

---

## Exported Candidate CSV Format