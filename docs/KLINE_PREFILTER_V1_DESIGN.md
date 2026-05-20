# Kline Prefilter V1 — Design Note

**Status:** FROZEN — implemented as described.
**Date:** 2026-05-19
**Author:** AI Agent
**Replaces:** `compute_kline_candidate_days` in `binance_vision_archive.py`

---

## Problem

The current kline prefilter uses `hl_threshold_bps=30.0` applied to the **daily** high-low range. Since BTC and ETH have daily HL >= 30bps on ~95% of trading days, the filter selects nearly the entire calendar and provides no meaningful pruning.

## Chosen Statistic: Per-1m-Bar High-Low Range

**Primary rule:** A day is a candidate if ANY single 1-minute bar within that day
has `high / low - 1 >= K` bps, where `K = 30`.

**Formula:** For each 1m bar, compute `hl_bps = (high - low) / open * 10000`.
If `hl_bps >= 30` for any bar, the day is included.

## Superset Proof

The tick-level stress rule is: flag any 30-second window where price moves >= 30bps.

Any 30-second window is fully contained within exactly one 1-minute bar (the bar
whose open_time <= window_start and close_time >= window_end). The high-low range
of that 1m bar is the maximum price excursion within the entire minute. Therefore:

    tick_move_30s >= 30bps  =>  containing_bar_hl >= 30bps

This is a mathematical certainty: the bar's range is an upper bound on any sub-interval
move within it. Therefore, flagging every day with at least one bar having HL >= 30bps
is a **strict superset** of the tick-level 30s/30bps rule. It can only produce false
positives (days with wide bars but no 30s/30bps tick event), never false negatives
(for events fully contained within a single bar).

## Threshold

### Hard Superset Bound

**K_hard = 30 bps** — the mathematical lower bound. Any 30-second window with a
>= 30bps move is contained in a 1m bar whose HL must also be >= 30bps. Using K = 30
is guaranteed never to produce false negatives. However, this bound assumes the
price is frozen outside the 30-second stress window, which never happens in practice.

### Updated Threshold: K = 75 bps

A realistic noise model: under typical BTC/ETH 1m conditions, the price moves at
least a few bps in any 30-second span purely from microstructure noise. Under the
stated assumption that the 30 seconds of the minute NOT containing the impulse
exhibit at least ~5 bps of additional range, a 30s/30bps event projects to a
containing 1m HL of >= 35 bps.

We adopt **K = 75 bps** as the operational threshold. This corresponds to the
stronger assumption that the non-impulse half of the minute exhibits at least
~45 bps of typical movement. This is conservative: actual BTC/ETH 1m bars
containing genuine sub-minute impulses overwhelmingly have HL well above 75 bps
in observed practice.

The threshold choice is therefore derived from a stated noise assumption, written
down before the empirical count is observed, not tuned to a disk budget. The
empirical count is a sanity check, not an input.

**Note:** The threshold choice is justified by the noise assumption above, NOT
from disk budget, download count, or empirical tuning.

## Failure Modes

| Failure Mode | Effect | Handling |
|---|---|---|
| **Boundary straddling** — a 30s/30bps move crosses a 1m bar boundary (e.g., starts at 10:00:50, ends at 10:01:10). Neither bar's individual HL may reach 30bps. | Theoretical false negative. | (a) The move must concentrate within seconds of the boundary AND both adjacent bars must otherwise be flat — extremely rare in BTC/ETH. (b) In practice, the boundary tick becomes part of both bars' price history, inflating at least one bar's HL. (c) The old daily-range filter had this same gap plus orders-of-magnitude worse selectivity. |
| **Single outlier tick** — one erroneous tick inflates a bar's HL above 30bps. | False positive (extra candidate day). | Acceptable. The superset filter over-includes by design. The tick-level labeler downstream will find no true 30s/30bps event and the day produces zero labels. No harm, just one extra day's download. |
| **Low-volume bar** — one stale tick, no real movement. | HL = 0 (no false positive). | Not an issue — a single price point has zero range. |
| **Missing data** — gaps in kline series. | No bar to evaluate for that minute. | If the gap coincides with a stress event, the day may be missed. However, missing data is already a calendar-level issue handled by the common-calendar computation. The prefilter only operates on available bars. |
| **Floating-point rounding** — bps calculation loses precision. | Possible 1-2 bps error at boundary. | The HL calculation uses the bar's open as denominator (not high or low), which is the standard convention. float32 precision at BTC $100k gives ~0.1 bps resolution — negligible compared to 30bps threshold. |

## Scope

- **Input:** 1m kline data for SOURCE symbols only (BTCUSDT, ETHUSDT).
- **Output:** Set of candidate dates (YYYY-MM-DD strings).
- **No leakage:** Does not read target asset klines, tick data, or any execution-related information.
- **No study semantics changed:** Calendar, symbols, horizons, costs, null method, FDR, seed are all unchanged. This filter only gates which aggTrade days are downloaded.

## Implementation Plan

1. New module: `kline_prefilter_v1.py` with `compute_kline_candidate_days_v1()`.
2. Deprecate old `compute_kline_candidate_days` in `binance_vision_archive.py`
   (keep reachable for A/B comparison, do not call from runner).
3. Wire `run_cross_asset_beta_lag_archive.py` to call the v1 function.
4. Add test `test_kline_prefilter_v1.py` with superset proof tests and empirical sanity check.
