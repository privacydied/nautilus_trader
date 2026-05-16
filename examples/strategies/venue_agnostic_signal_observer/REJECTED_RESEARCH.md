# Rejected Research Registry

**Status reference:** Entries here are frozen — the hypothesis was tested with sufficient evidence and did not pass its acceptance gate. Future re-evaluation requires a new precommitment document.

---

## 1. Polymarket BTC Up/Down Short-Expiry Chainlink/CLOB Lag

**Date:** 2026-05-16
**Observer:** venue_agnostic_signal_observer
**Pipeline:** run_polymarket_btc_updown_liquidity_probe + capture_all_durations (direct slug injection)
**Probe module:** polymarket_btc_updown_liquidity_probe.py
**Status:** REJECTED — blocked by near-expiry depth wall under current observed liquidity

---

### Hypothesis (Original)

> Polymarket BTC Up/Down binary option markets near expiry have a liquidity desert that creates systematic Chainlink/CLOB price lag, producing exploitable mispricing.

### Correction History

This hypothesis was initially supported by a parser bug. The original probe found a ~98c median spread on some market samples. This was traced to incorrect orderbook parsing (using min bid / max ask instead of max bid / min ask). After the parser fix:

- **2026-05-16 Parser fix verified:** Max-bid/min-ask logic applied. Regression tests cover missing, crossed, stale, and one-sided books.
- **2026-05-16 TTE bucketing verified:** `_check_tte_sanity()` diagnostic added. TTE uses real clock math (`expiry_dt.timestamp() - ts_event`). Proven correct across 6 TTE buckets in a 30-minute multi-duration capture (5m, 15m, 1h, 4h, 1d).

This rejection supersedes the earlier parser-confounded liquidity read.

### Experiment

| Parameter | Value |
|---|---|
| Duration | 30 minutes (1800s) |
| Poll interval | 5 seconds |
| Markets | 17 BTC Up/Down markets across 5m, 15m, 1h, 4h, 1d durations |
| Tokens | 34 (YES and NO per market) |
| Total samples | 12,240 |
| Valid samples | ~8,948 |
| Reference proxy | Binance BTC/USDT via REST (30 polls, 60s interval) |
| Capture start | 2026-05-16 04:45 UTC |
| Raw payloads | 100 (for audit) |

### Acceptance Gate Results

| Gate | Result |
|---|---|
| Parser fix produces realistic spreads | PASS (median 1c global, was 98c before fix) |
| TTE bucketing correctly measures clock time | PASS (422 near-expiry samples in correct buckets) |
| Near-expiry (tte<=120s) spread < 3c | **MARGINAL PASS** (median 1c, p95 up to 7c in 0-30s bucket) |
| Near-expiry depth > $100 non-dust floor | **FAIL** (collapses from ~$89 at 5-15m to ~$13 at 0-30s) |
| Proxy routing populates distance-to-strike | PASS (30 BINANCE prices, reference_source: BINANCE) |
| Distance-to-strike measurable on Up/Down | **FAIL** (structural — no strike price in directional questions) |
| Convex danger zone (tte<=120s AND dist<=10bps) populated | **FAIL** (cannot compute distance-to-strike on Up/Down markets) |

### Near-Expiry Depth Collapse (Primary Rejection Evidence)

| TTE Bucket | Samples | Median Spread | P95 Spread | Top Depth (bid+ask) | $100 Threshold |
|---|---|---|---|---|---|
| 5-15m | 1,554 | 1c | 1c | $89 | BELOW |
| 2-5m | 576 | 1c | 1c | $43 | BELOW |
| 1-2m | 206 | 1c | 2c | $28 | BELOW |
| 30s-1m | 108 | 1c | 4c | $18 | BELOW |
| 0-30s | 108 | 1c | 7c | $13 | BELOW |

Near-expiry liquidity is **GREEN on spread** (median 1c) but **RED on depth** below $100 threshold. The $100 non-dust floor was precommitted in `POLYMARKET_BTC_UPDOWN_LIQUIDITY_PROBE_V0.md` and confirmed as the acceptance criterion.

### Observations

1. **Spread was a parser artifact; depth is real.** The original 98c spread finding was caused by wrong min/max parsing. After correction, spreads are 1c median globally. But near-expiry depth drops monotonically from ~$89 to ~$13 in the final 2 minutes, below the $100 threshold.

2. **Two-sided rate degrades near expiry.** At 30s-1m: 92.6%. At 0-30s: 75.9%. Expired: 0%. The book loses one side in the final seconds as the market resolves.

3. **P95 spread widens near expiry.** Stable at 1-2c for TTE > 1 minute, then rises to 4c at 30s-1m and 7c at 0-30s. Still tight in absolute terms, but the trend is clear.

4. **Distance-to-strike is structurally unavailable.** BTC Up/Down markets have no numerical strike price in their question text. `_extract_price_to_beat()` correctly returns None for questions like "Bitcoin Up or Down - May 16, 12:45AM-12:50AM ET." This is not a code bug — these markets resolve directionally, not against a price target.

5. **Proxy routing infrastructure works.** 30 Binance proxy prices were successfully captured with `reference_source: BINANCE`. The code correctly labels and uses them. The pipeline is ready for Price Target markets.

### Verdict

**REJECTED.** The Polymarket BTC Up/Down short-expiry liquidity hypothesis is rejected under current observed conditions. The core finding is that near-expiry depth collapses below the precommitted $100 non-dust threshold, and spread tail widens measurably, even though median spreads are tight. This combination makes the book unreliable for the original latency-measurement hypothesis near the binary resolution boundary.

### Preserved Infrastructure

The liquidity probe module (`polymarket_btc_updown_liquidity_probe.py`) and its CLI runner (`run_polymarket_btc_updown_liquidity_probe.py`) remain reusable:

- TTE bucket computation (verified correct, includes `_check_tte_sanity` diagnostic)
- Orderbook parser with regression tests (max-bid/min-ask)
- CEX proxy price routing (Binance/Kraken/Coinbase)
- Distance-to-strike bucket computation (requires Price Target markets with strike prices)
- Two-axis liquidity grid (TTE x distance, with convex danger zone cell)
- Duration coverage breakdown
- Raw payload audit
- Verification status pipeline (gates: raw payload, TTE, distance, two-axis, duration coverage)

### Price Target Markets — Separate Future Hypothesis

BTC Price Target markets (e.g., "Will BTC be above $X by Y?") embed a numerical strike price in their question text. These would:

1. Enable `_extract_price_to_beat` to populate `price_to_beat` on samples.
2. Enable distance-to-strike bucket computation.
3. Enable the two-axis grid and convex danger zone cell.
4. Test the genuine Chainlink/CLOB lag hypothesis where a tiny BTC move flips a YES/NO binary near its strike.

This is a **separate hypothesis** requiring:
- Its own precommitment document (`PRECOMMITMENT.md`).
- Its own market discovery logic (Price Target markets have different slug/question patterns).
- Independent liquidity thresholds (different floor, different TTE windows).
- Fresh data capture — the current capture infrastructure is reusable, but a new precommitment must define acceptance gates before capture.

The Up/Down rejection does not automatically disqualify Price Target markets. They differ in market structure (numerical strike exists, thinner liquidity possible at specific strike levels, different expiration mechanics) and require independent evaluation.

### Artifacts

- **Capture output:** `/tmp/polymarket_liquidity_v2/`
  - `tte_bucket_summary.json` — TTE bucket breakdown with near-expiry rollup
  - `distance_to_strike_bucket_summary.json` — all unknown (structural, no strike)
  - `two_axis_liquidity_grid.json` — all cells 0 (no distance data)
  - `duration_coverage.md` — per-duration breakdown
  - `clob_orderbook_samples.jsonl` — 12,240 raw samples
  - `raw_clob_orderbook_payloads.jsonl` — 100 raw CLOB responses for audit
  - `capture_manifest.json` — capture metadata
  - `liquidity_probe_summary.json` — summary statistics
- **Code changes:** feat(probe): add TTE sanity diagnostic (`750a52a832`)
- **Probe spec:** `POLYMARKET_BTC_UPDOWN_LIQUIDITY_PROBE_V0.md`
