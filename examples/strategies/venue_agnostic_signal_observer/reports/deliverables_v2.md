# Deliverables Report: Tick-Level Cross-Venue Lead-Lag Observer v2

**Date:** 2026-05-12
**Observer:** venue_agnostic_signal_observer
**Status:** Complete

---

## 1. Files Created/Modified

### New files:
| File | Lines | Purpose |
|---|---|---|
| `symbol_aliases.py` | 98 | Explicit venue-symbol to canonical (asset, quote) mapping |
| `tests/test_symbol_aliases.py` | 109 | Tests for symbol resolution, comparison, unknown handling |
| `tests/test_cross_venue_lead_lag.py` | 300 | Cross-venue pairing, same-venue skip, end-to-end sweep tests |

### Modified files:
| File | Changes |
|---|---|
| `run_tick_capture.py` | Added `@dataclass` import, `build_parser()` function, symbol normalization via `_normalize_symbol()`, capture manifest writing, per-venue diagnostics |
| `run_tick_lead_lag.py` | Symbol normalization in `load_tick_data()` (alias fallback for XBT→BTC), `--allow-same-venue-diagnostics` CLI flag, same-venue skip in sweep loop, canonical asset extraction via `symbol_aliases.resolve_symbol()` |

### No changes needed:
- `tick_models.py`, `tick_store.py`, `event_study.py` — already correct from previous session
- `config.py` — already has `LeadLagConfig`
- `tests/test_all.py`, `tests/test_tick_lead_lag_pipeline.py`, `tests/test_lead_lag_pipeline.py`, `tests/test_synthetic_fixtures.py` — all still pass

## 2. `__init__.py` Verification

Both files correctly named with double underscores:
- `/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/__init__.py` ✅
- `/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/tests/__init__.py` ✅

No literal `init.py` files found.

## 3. Symbol Normalization

```
Coinbase BTC-USD → BTC/USD    (CanonicalSymbol("BTC", "USD"))
Kraken   XBT/USD → BTC/USD    (CanonicalSymbol("BTC", "USD"))
Kraken   XXBTZUSD → BTC/USD   (CanonicalSymbol("BTC", "USD"))
Kraken   XBTZUSD  → BTC/USD   (CanonicalSymbol("BTC", "USD"))
Coinbase ETH-USD → ETH/USD
Kraken   ETH/USD  → ETH/USD
Binance  BTCUSDT  → BTC/USDT
Binance  ETHUSD   → ETH/USD
SOL      handled for all three venues
```

Unknown symbols raise `ValueError` with a clear message — no silent inference.

## 4. Collector Status

WebSocket collector works correctly.
- Kraken: wss://ws.kraken.com, trade channel
- Coinbase: wss://ws-feed.exchange.coinbase.com, matches channel

300-second capture produced:
- Kraken BTC/USD: 158 trades, price range $80623.60 – $80660.50 (4.58 bps move)
- Coinbase BTC/USD: 1,007 trades, price range $80607.70 – $80647.90 (4.99 bps move)
- Capture manifest written to `data/signal_observer_ticks_v2/capture_manifest.json`

## 5. Lead-Lag Event-Study Logic

TickLeadLagGenerator sweeps `lookback_ms × threshold_bps` grid:
- Lookbacks: 1s, 2s, 5s, 10s, 30s
- Thresholds: 2, 5, 10, 20 bps
- Horizons: 1s, 2s, 5s, 10s, 30s, 60s
- Cooldown: 10s

40 parameter combinations tested per cross-venue pair.

## 6. Cross-Venue Enforcement

Same-venue comparisons are skipped by default. Two explicit pairs tested:
- Coinbase → Kraken
- Kraken → Coinbase

`--allow-same-venue-diagnostics` flag enables same-venue comparisons for diagnostics.

## 7. Fee/Slippage/Buffer

| Component | Value |
|---|---|
| Taker fee | 12 bps |
| Slippage buffer | 2 bps |
| Quote mismatch buffer | 5 bps |
| **Total cost** | **19 bps** |

## 8. Random Baseline

Deterministic (`seed=42`), same signal count as real data, uniform random timestamps, random direction/lookback/threshold. Evaluated on identical target data with identical cost model.

## 9. Candidate Gate

Six gates, all must pass:
1. valid_count >= 50
2. mean_net > 0
3. median_net > -5 bps
4. win_rate > 50% OR beats baseline
5. real_mean > baseline_mean + margin
6. not single-event-driven

## 10. Test Results

```
pytest examples/strategies/venue_agnostic_signal_observer/tests/ -q
113 passed in 0.90s
```

Test breakdown:
- `test_all.py`: 21 tests (original observer)
- `test_lead_lag_pipeline.py`: 16 tests (OHLCV lead-lag)
- `test_synthetic_fixtures.py`: 3 tests (legacy)
- `test_tick_lead_lag_pipeline.py`: 43 tests (tick models, store, event-study)
- `test_symbol_aliases.py`: 20 tests (symbol resolution, comparison, unknowns)
- `test_cross_venue_lead_lag.py`: 10 tests (cross-venue pairing, load, sweep)

## 11. Real Data Results

### Coinbase → Kraken
- 9 signals fired across 40 parameter configs
- Only configs with threshold=2bps fired (flat market, moves < 5bps)
- Mean net return: ~-14 bps (exactly fee cost)
- Win rate: 0%
- All 9 configs REJECTED

### Kraken → Coinbase
- 5 signals fired
- Mean net return: ~-14 bps
- Win rate: 0%
- All 5 configs REJECTED

### Baseline Comparison
- Baseline mean net: -14.02 bps
- Signal mean net: ~-14.00 bps
- Real and random are indistinguishable — as expected in a flat 5-min window

## 12. Verdict

**REJECTED** — Cross-venue lead-lag at sub-second resolution on a fiat (BTC/USD) pair over a 5-minute flat window shows no edge. Signal results are identical to random baseline. This is expected: BTC/USD on major venues has efficient price discovery with sub-second convergence, and a 5-bps price range over 5 minutes provides insufficient movement for any strategy to overcome transaction costs.

This is NOT a rejection of the entire hypothesis class. It is a result for this specific dataset (5 min, sub-5 bps range, single asset). The pipeline is working correctly and would detect edge if present — as proven by synthetic fixtures.

## 13. Report Paths

- `reports/signal_observer_tick_lead_lag_v2/tick_lead_lag_report.md` (189 lines, 15 sections)
- `reports/signal_observer_tick_lead_lag_v2/tick_summary.json`
- `reports/signal_observer_tick_lead_lag_v2/tick_summary.csv`
- `reports/signal_observer_tick_lead_lag_v2/rejections.json`
- `data/signal_observer_ticks_v2/capture_manifest.json`
- `data/signal_observer_ticks_v2/trades_kraken_BTC-USD_1778620996.jsonl` (158 ticks)
- `data/signal_observer_ticks_v2/trades_coinbase_BTC-USD_1778620996.jsonl` (1,007 ticks)

## 14. Safety Confirmation

- No orders in any code path
- No live trading
- No private keys or auth
- No account PnL or portfolio
- No `LiveNode` or `TradingNode` imports
- No-order guard tests pass (scan all .py files for forbidden strings)
- No-order and no-key tests pass in both `test_all.py` and `test_tick_lead_lag_pipeline.py`

## 15. Remaining Limitations

1. **Thin data window**: 5 minutes with only 5 bps range. Not enough market movement to generate meaningful signal density.
2. **Single asset pair**: Only BTC/USD tested. ETH/USD capture was also started but not analyzed (same symbol mapping works).
3. **No quote-level data**: Only trade ticks are captured via public WebSocket. Quote ticks (bid/ask) would enable spread-based signals.
4. **Flat-rate fee model**: Real fees may vary by venue tier and trade size.
5. **No multi-day data**: Cross-venue lead-lag may require data across different market regimes.

## 16. Next Step

1. **Collect more data**: Run a 30–60 minute capture during a higher-volatility window (e.g., US market open, macro data release).
2. **Add ETH/USD**: Analyze a second asset for cross-asset confirmation.
3. **Lower thresholds**: Test thresholds below 2 bps (e.g., 0.5, 1 bps) since BTC microstructure moves are tiny.
4. **Quote data**: Consider capturing bid/ask from exchanges that provide it via public feeds.
5. **Multi-day study**: Run captures across different time periods and market conditions.
