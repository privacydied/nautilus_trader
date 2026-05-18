# Family 1 Resolution Diagnosis

## Status

Diagnostic-only. No experiment run.

## Inputs inspected

### Code files
- `tick_models.py` — TradeTickLite, QuoteTickLite, TickSignalEvent, TickForwardReturn data models
- `forward_returns_gpu.py` — `batch_evaluate_signals_gpu`, the GPU-accelerated tick-level forward-return evaluator
- `event_study.py` — `evaluate_tick_signal` (CPU path), `TickLeadLagGenerator`
- `cross_asset_impulse.py` — an existing tick-level signal generator (produces TickSignalEvent, uses forward-return evaluator)
- `trade_flow_impulse.py` — another tick-level signal generator
- `offline_discovery_plan.py` — plan construction, cell creation, `_make_cell`, `build_offline_discovery_plan`
- `offline_train_evaluation.py` — `_evaluate_family1` (line 217), train evaluation dispatch (line 564)
- `offline_holdout_evaluation.py` — `_evaluate_family1` (line 397), holdout evaluation dispatch (line 876)
- `offline_historical_models.py` — RESOLUTION_TRADE, RESOLUTION_BAR, RESOLUTION_AGG_TRADE
- `offline_train_survivor_freeze.py` — freeze logic
- `offline_train_holdout_comparison.py` — comparison logic

### Data/config files
- `data/kraken_20250203/discovery_config_family1.json` — config with lookbacks_ms=[30000, 60000, 120000], horizons_ms=[60000, 300000], required_resolution=trade
- `data/kraken_20250203/offline_sources_price_series.json` — source config: both BTC/USD and BTC/USDT are resolution_type=trade, stream_type=agg_trades
- `data/kraken_20250203/XXBTZUSD_price_series.json` — per-trade price points with `{timestamp_ns, close}`

### Artifact files (committed-run outputs, read-only)
- `offline_discovery_plan_20260518_015416/offline_discovery_plan.json` — 6 Family 1 cells: 3 lookbacks × 2 horizons
- `offline_train_evaluation_20260518_020154/offline_train_evaluation.json` — train cell results showing bit-identical metrics across lookback variants
- `offline_holdout_evaluation_20260518_020557/offline_holdout_evaluation.json` — holdout cell results showing same
- `offline_train_survivor_freeze_20260518_020233/offline_train_survivor_freeze.json` — freeze state
- `offline_train_holdout_comparison_20260518_020618/offline_train_holdout_comparison.json` — comparison

## Q1 — Tick evaluator existence

### Evidence

**Tick-level forward-return infrastructure does exist:**

| Component | File | Function/Class | Purpose |
|-----------|------|----------------|---------|
| Data models | `tick_models.py` | `TickSignalEvent`, `TickForwardReturn`, `TradeTickLite`, `QuoteTickLite` | Event/return/tick dataclasses with nanosecond timestamps |
| GPU evaluator | `forward_returns_gpu.py` | `batch_evaluate_signals_gpu` | Takes `list[TickSignalEvent]` + target ticks, returns `list[TickForwardReturn]` per horizon |
| CPU evaluator | `event_study.py` | `evaluate_tick_signal` | Same contract, CPU implementation |
| Test coverage | `tests/test_forward_returns_gpu.py`, `tests/test_tick_lead_lag_pipeline.py` | Various | Unit tests for tick-level forward-return evaluation |

The tick forward-return pipeline is already used by other signal families:
- `cross_asset_impulse.py` generates `TickSignalEvent` → feeds `batch_evaluate_signals_gpu` → evaluates forward returns
- `trade_flow_impulse.py` generates `TickSignalEvent` through the same path
- `derivatives_lead_lag.py` same pattern

**However, Family 1 has its own dedicated evaluator that bypasses this pipeline entirely:**

In `offline_train_evaluation.py` (line 564-565):
```python
elif cell.family_id == "family_1_same_venue_quote_basis":
    result = _evaluate_family1(cell, cell_train_window_ids, window_by_id, price_series, discovery_plan.plan_hash)
```

In `offline_holdout_evaluation.py` (line 876-877):
```python
if cell.family_id == "family_1_same_venue_quote_basis":
    result = _evaluate_family1(cell, holdout_window_ids, window_by_id, price_series, evaluation_hash, survivor_freeze_hash, plan_hash)
```

Both call `_evaluate_family1` — a bar-resolution evaluator function that:
1. Loads price series via `_load_price_series` (looks for `"close"` field in bar-like data)
2. Uses `_PricePoint(timestamp_ns, price)` — a bar-oriented model
3. Computes basis as instantaneous divergence: `(anchor_b - anchor_a) / anchor_a` at the trigger timestamp
4. Evaluates forward returns by finding the next price point at `trigger_ts + horizon_ms * 1_000_000`
5. Constructs signal inline instead of producing `TickSignalEvent` records

The resolution gate was widened (the committed fix at `0e369edd66` added `RESOLUTION_TRADE` and `RESOLUTION_AGG_TRADE` to the allowed list at lines 219 and 407), but this only prevented exclusion — it did not change the evaluator used. Family 1 still runs through the bar-tier `_evaluate_family1` function, which happens to accept per-trade price points but processes them identically to bars.

**No tick-level Family 1 signal generator exists.** Existing tick generators (cross_asset_impulse, trade_flow_impulse) are designed for single-source movement detection, not two-asset quote-basis divergence. Neither `TickLeadLagGenerator` nor any other class computes a same-venue quote-basis signal from two tick streams.

### Verdict

`PARTIAL_WIRING_TASK`

The tick forward-return evaluation pipeline exists and is battle-tested (`batch_evaluate_signals_gpu` / `evaluate_tick_signal` consuming `TickSignalEvent` → `TickForwardReturn`). What does NOT exist is a tick-level signal generator that computes the same-venue quote-basis divergence that Family 1's hypothesis requires — i.e., a function that takes two tick streams (BTC/USD and BTC/USDT on Kraken) and produces `TickSignalEvent` records with appropriate `source_move_bps` representing the basis divergence.

If such a signal generator were built and wired into the offline pipeline (replacing the `_evaluate_family1` bar path with a tick-evaluation path), the existing `batch_evaluate_signals_gpu` could handle the forward-return evaluation on the target side.

## Q2 — Lookback inertness

### Evidence

**The discovery plan correctly creates 3 cells with distinct lookback values:**

From `offline_discovery_plan_20260518_015416/offline_discovery_plan.json`:
```
cell_id=e679f73bcc60... lookback_ms=30000 horizon_ms=60000
cell_id=40b4d97fd92f... lookback_ms=60000 horizon_ms=60000
cell_id=15a23e16fd36... lookback_ms=120000 horizon_ms=60000
```

Each has a unique `cell_id` (because the payload includes `lookback_ms` in the hash input at `offline_discovery_plan.py` line 533). So lookback is threaded through plan construction correctly.

**But the train evaluation produces identical metrics for all three:**

From `offline_train_evaluation_20260518_020154/offline_train_evaluation.json`:
```
30s: raw_mean=136.87868166699155 net_mean=119.87868166699157 win_rate=0.642857
60s: raw_mean=136.87868166699155 net_mean=119.87868166699157 win_rate=0.642857
120s: raw_mean=136.87868166699155 net_mean=119.87868166699157 win_rate=0.642857
```

**Same in holdout:**

From `offline_holdout_evaluation_20260518_020557/offline_holdout_evaluation.json`:
```
30s: raw_mean=-140.78495034014202 net_mean=-157.78495034014205 win_rate=0.428571
60s: raw_mean=-140.78495034014202 net_mean=-157.78495034014205 win_rate=0.428571
120s: raw_mean=-140.78495034014202 net_mean=-157.78495034014205 win_rate=0.428571
```

### Root cause

The `_evaluate_family1` function in **both** `offline_train_evaluation.py` and `offline_holdout_evaluation.py` **never reads `cell.lookback_ms` in its computation**.

In `offline_train_evaluation.py`, lines 240-253:
```python
trigger_ts = int(window["trigger_timestamp_ns"])
anchor_a = _find_price_at_or_after(source_a, trigger_ts)
anchor_b = _find_price_at_or_after(source_b, trigger_ts)
entry = _find_price_at_or_after(target, trigger_ts)
exit_price = _find_price_at_or_after(target, trigger_ts + (cell.horizon_ms * 1_000_000))
# ...
basis_signal = (anchor_b - anchor_a) / anchor_a     # INSTANTANEOUS basis
```

In `offline_holdout_evaluation.py`, lines 428-438 — identical pattern.

The basis is computed as an **instantaneous** divergence: the difference between the first BTC/USD trade price and the first BTC/USDT trade price at the trigger timestamp. There is no lookback window, no price-difference-over-window, no change-from-lookback-ago — the `cell.lookback_ms` value is completely unreferenced.

**Compare with Family 2**, whose evaluator DOES use lookback (`offline_train_evaluation.py` line 315):
```python
source_exit = _find_price_at_or_after(source, trigger_ts + (cell.lookback_ms * 1_000_000))
```

Family 2 uses lookback as the "observation window" for the source move. Family 1 was presumably intended to measure the **change in basis** over the lookback window, i.e.:
```
basis_start = anchor_b at (trigger_ts - lookback_ms) - anchor_a at (trigger_ts - lookback_ms)
basis_end   = anchor_b at trigger_ts - anchor_a at trigger_ts
signal      = basis_end - basis_start   (change in basis over lookback window)
```

Instead, Family 1 measures only the instantaneous basis at the trigger point, which is the same regardless of lookback value.

- **Exact file/function where lookback becomes inert:**  
  `offline_train_evaluation.py`::`_evaluate_family1` (lines 240-251) and  
  `offline_holdout_evaluation.py`::`_evaluate_family1` (lines 428-438)

- **Exact field/key name involved:** `cell.lookback_ms` — never accessed in either `_evaluate_family1` implementation.

- **Bug type:** **Evaluator bug** — the lookback parameter is correctly threaded through plan construction into the cell, but the evaluator that consumes the cell ignores it. Each cell produces a structurally different result (different `cell_id`) with identical computational output.

- **One-line fix description:**  
  In both `_evaluate_family1` functions, replace instantaneous basis measurement with a lookback-window measurement: compute `anchor_a_start` and `anchor_b_start` at `trigger_ts - (cell.lookback_ms * 1_000_000)`, then define the basis signal as the *change* in basis over the window, not the instantaneous snapshot.

### Fix needed, not applied

This diagnosis identifies the root cause but does not apply the fix. Applying the fix requires modifying `_evaluate_family1` in two files (`offline_train_evaluation.py` and `offline_holdout_evaluation.py`) to actually use `cell.lookback_ms` to define the basis-divergence measurement window, then re-running the offline pipeline.

## Q3 — Tick vs bar vs untestable

### Mechanism reasoning

Family 1's hypothesis: when Kraken BTC/USD and BTC/USDT quote-basis diverges, the subsequent reversion or continuation may be predictable.

This is fundamentally a **liquidity-segmentation** signal. On a single venue like Kraken, BTC/USD and BTC/USDT are different order books. When one book's liquidity thins relative to the other (e.g., USDT book thins during a stablecoin stress), the quoted prices diverge even though both instruments reference the same underlying BTC spot.

Key dimensions:

1. **Observability rate:** Kraken XBTUSDT historical data shows roughly 700 trades/hour — approximately one print every 5 seconds. The BTC/USD pair trades at a similar or higher rate. Sub-second basis divergence cannot be observed from trade prints alone.

2. **Signal formation:** Quote-basis divergence is an order-book phenomenon — it forms when one book's best bid/ask drift apart from the other's. Trade prints are a lagging indicator: a divergence only becomes visible through trade data after executions at the diverged prices. The fastest observable divergence via trades is bounded by the inter-trade interval (~5s for the slower leg).

3. **Edge resolution:** If the basis diverges and then reverts within seconds (arbitrage response), trade-only data at ~5s resolution may miss the reversal entirely or catch only one tail of it. If the edge operates at minute+ timescales (persistent basis drift driven by structural liquidity imbalance), trade prints provide adequate sampling.

4. **Evaluation approach:** The signal is inherently about the *relationship* between two price streams, not an absolute move in one stream. The natural evaluation is: at a tick where the basis exceeds a threshold, long-short the two legs and measure the convergence/divergence forward return. This is a tick-level hypothesis that requires tick-level signal construction.

5. **Current data:** The prepared data (`XXBTZUSD_price_series.json`, `XBTUSDT_price_series.json`) is per-trade, not bar-aggregated. Each trade is a `{timestamp_ns, close}` row with true nanosecond timestamps. This is native tick data, already suitable for tick-level analysis if the lookback window is ≥~15s (three inter-trade intervals on the slower leg).

### Observability limit

At ~5s between Kraken XBTUSDT trades:

| Timescale | Observable? | Notes |
|-----------|------------|-------|
| <1s | No | Below Nyquist limit for ~5s sampling |
| 1-10s | Marginal | 1-2 samples per window, unreliable |
| 10-30s | Moderate | 2-6 samples, adequate for tick evaluation |
| 30s+ | Yes | Comfortably oversampled |
| 60s+ (minute-scale) | Yes | Many samples per window |

### Recommendation

`TICK_FRAMING_RECOMMENDED`

- The hypothesis is about a tick/order-book level relationship (same-venue quote-basis divergence).
- The existing data is per-trade, not aggregated into bars — it is already at tick resolution.
- The bar evaluator was an implementation accident: it happened to process tick data through a bar-oriented path without using the lookback parameter.
- A proper tick framing would: (a) compute basis divergence over a lookback window from two trade streams, (b) emit `TickSignalEvent` records when the divergence exceeds a threshold, (c) evaluate forward returns on the target stream using the existing `batch_evaluate_signals_gpu` pipeline.
- Lookback windows ≥30s are recommended to account for the ~5s sampling gap.
- Order-book data (quote ticks) would be strictly superior for this hypothesis since the basis forms at the book level, not the trade level. However, the trade-only data can still test the hypothesis at minute-scale time horizons.

## Next run scope

**Small wiring/fix task.**

The diagnosis shows:

1. **Q1 verdict (PARTIAL_WIRING_TASK):** The tick forward-return pipeline already exists and is used by other families. Family 1 needs a tick-level signal generator for same-venue quote-basis divergence, plus a wiring change to route Family 1 cells through the tick evaluation pipeline instead of the bar-tier `_evaluate_family1`.

2. **Q2 root cause:** The lookback parameter is structurally wired through plan construction to the cell, but `_evaluate_family1` never reads it. The fix is contained to two functions in two files — a small change.

3. **Q3 recommendation (TICK_FRAMING_RECOMMENDED):** Tick framing is the correct approach. The data supports it at ≥30s lookback windows.

Therefore, the next scope is a **small wiring + lookback-fix + re-precommit** task:

1. Build a tick-level signal generator for Family 1's same-venue quote-basis divergence (computes basis over lookback window, produces `TickSignalEvent` records).
2. Wire Family 1 cells to use the tick evaluation pipeline (via `batch_evaluate_signals_gpu` or `evaluate_tick_signal`) instead of the bar-tier `_evaluate_family1`.
3. Fix the lookback inertness in both `_evaluate_family1` functions (or simply decommission them once the tick path is wired).
4. Regenerate the precommitment and re-run the pipeline.

This is **not** a larger evaluator-build task because the heavy machinery already exists. And Family 1 is **not** untestable on this data source — the data is suitable for tick framing at ≥30s windows.

## Explicit non-actions

- No evaluator code was added or modified.
- No lookback fix was applied.
- No data was fetched.
- No pipeline was run.
- No precommitment file was created or modified.
- No threshold, cost, FDR, or verdict-gate settings were changed.
- No live capture, private-key path, order path, execution path, adapter, wallet, signing code, or bot authorization was touched.
- No new signal family was introduced.
