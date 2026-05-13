# Architecture Notes: kraken_market_structure_scanner & venue_agnostic_signal_observer

---

## 1. kraken_market_structure_scanner

**Location:** `examples/strategies/kraken_market_structure_scanner/`
**Purpose:** Observer-only market scanning. Three phases: V6 (cross-venue spread), V6-B (funding/basis), V6-C (alt-coin funding with persistence tracking). No orders, no API keys.

---

### 1.1 V6: Cross-Venue Spread Scanner

**Entry point:** `run_v6_scanner.py`
**Core files:** `config.py`, `scanner.py`, `symbols.py`, `venues.py`, `opportunity.py`

**Architecture:**
```
run_v6_scanner.py ── CLI (argparse), builds ScannerConfig + FeeConfig, invokes Scanner.run()
    │
    ├─ config.py
    │   ├─ FeeConfig: default taker fees per venue (kraken=40bps, coinbase=40bps, binance=10bps)
    │   └─ ScannerConfig: symbols list, venues list, poll_interval, latency_buffer, output_dir
    │
    ├─ symbols.py
    │   ├─ SymbolSpec: frozen dataclass with base/quote + per-venue API symbol strings
    │   └─ STANDARD_SYMBOLS: dict of 5 pairs (BTC/USD, BTC/USDT, ETH/USD, ETH/USDT, SOL/USD)
    │      IMPORTANT: USD and USDT are NOT treated as identical
    │
    ├─ venues.py
    │   ├─ Ticker: frozen dataclass (venue, symbol, quote, bid, ask, last, ts_exchange_ms, ts_recv_ms)
    │   ├─ fetch_kraken: /0/public/Ticker → parses bid/ask from nested arrays ["b"][0], ["a"][0]
    │   ├─ fetch_binance: /api/v3/ticker/bookTicker → bidPrice/askPrice
    │   └─ FETCHERS: {"kraken": fetch_kraken, "binance": fetch_binance}
    │
    ├─ opportunity.py
    │   ├─ Opportunity: frozen dataclass capturing full trade math
    │   └─ calculate_opportunity(t1, t2, fee_a, fee_b, latency_buffer):
    │      - Validates: quote match, different venues, all bid/ask > 0
    │      - Tries both directions: buy_t1→sell_t2 and buy_t2→sell_t1
    │      - Picks direction with higher gross edge in bps
    │      - net = gross_bps - fee_buy - fee_sell - latency_buffer
    │      - Returns Opportunity or None
    │
    └─ scanner.py
        └─ Scanner class:
            - __init__(ScannerConfig, FeeConfig): initializes stats dict
            - _fetch_tickers(): loops symbols x venues, calls FETCHERS, returns {symbol: {venue: Ticker}}
            - _check_opportunities(): all pairs of venues per symbol, calls calculate_opportunity
            - run(): polling loop → fetch → check → log to opportunities.jsonl → summary.json
```

**Data flow:**
```
CLI args → ScannerConfig + FeeConfig → Scanner.run()
  Loop until deadline:
    1. _fetch_tickers() → polls public REST for each symbol×venue
    2. _check_opportunities() → cross-venue pair comparison with fee math
    3. Log opportunities to opportunities.jsonl
    4. Sleep poll_interval_seconds
  Post-loop: read JSONL, compute max net edge, write summary.json
```

---

### 1.2 V6-B: Funding/Basis Scanner

**Entry point:** `run_v6_funding_basis.py`
**Core files:** `funding_config.py`, `funding_models.py`, `funding_scanner.py`, `funding_venues.py`, `funding_reports.py`

**Architecture:**
```
run_v6_funding_basis.py ── CLI, builds FundingConfig, invokes run_funding_scan()
    │
    ├─ funding_config.py
    │   ├─ FundingConfig: assets, spot_venues, perp_venues, fee assumptions, buffers
    │   │   - min_funding_apr=20%, min_net_edge_bps=25
    │   │   - Buffers: slippage=10bps, latency=10bps, basis_risk=25bps, quote_mismatch=20bps
    │   │   - venue_fees overrides: binance=5, bybit=5.5, kraken=3
    │   │   - min_persistence_polls=3 (for V6-C compatibility)
    │   ├─ SYMBOL_MAP: 16 assets → {venue_type: api_symbol} mappings
    │   │   Assets: BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, SUI, ARB, OP, APT, PEPE, WIF, TON
    │   │   Some have None for kraken_perp (DOGE, SUI, ARB, OP, APT, WIF, TON)
    │   │   PEPE has no kraken_spot
    │   ├─ QUOTE_MAP: kraken=USD, binance/bybit=USDT
    │   └─ FUNDING_INTERVAL: all 8.0 hours (kraken, binance, bybit)
    │
    ├─ funding_models.py
    │   ├─ PriceLevel: frozen dataclass with bid/ask/mark/index/last prices
    │   │   - .mid property: bid/ask average, falls back to mark price
    │   │   - .is_stale property: (ts_recv - ts_exchange) > max_age_ms
    │   └─ FundingObservation: comprehensive observation dataclass (~30 fields)
    │      - basis_bps, funding_rate, funding_apr
    │      - Three cost buckets: entry_fees + exit_fees + buffers
    │      - is_candidate, reason_if_rejected
    │
    ├─ funding_scanner.py
    │   ├─ fetch_spot(asset) → kraken_spot_ticker
    │   ├─ fetch_kraken_perp(asset) → futures.kraken.com/tickers + historical-funding-rates
    │   ├─ fetch_binance_perp(asset) → fapi.binance.com/fundingRate + bookTicker
    │   ├─ fetch_bybit_perp(asset) → api.bybit.com/v5/market/funding/history + tickers
    │   ├─ make_observation(asset, spot_pl, perp_pl, frate, cfg, perp_venue):
    │      - Computes basis_bps = (perp_mid - spot_mid) / spot_mid * 10000
    │      - funding_apr = frate * (24/interval) * 365 * 100
    │      - expected_funding_bps = frate * 10000
    │      - total_cost = entry_fees + exit_fees + slippage + latency + basis_risk + mismatch
    │      - estimated_net_edge = expected_funding - total_cost
    │      - Candidate gates: frate > 0, no quote mismatch, apr >= min, edge >= min
    │   └─ run_funding_scan(cfg): main polling loop
    │      - For each asset: fetch spot + all perps, make observations, track stats
    │      - write_observation to JSONL, write_summary.json at end
    │
    ├─ funding_venues.py
    │   ├─ kraken_spot_ticker → /0/public/Ticker → PriceLevel
    │   ├─ kraken_futures_tickers → /derivatives/api/v3/tickers → dict of tickers
    │   ├─ kraken_funding_rates → /derivatives/api/v3/historical-funding-rates
    │   ├─ kraken_futures_funding_latest → combines tickers + historical
    │   ├─ binance_fapi_latest_funding → /fapi/v1/fundingRate
    │   ├─ binance_perp_ticker → /fapi/v1/ticker/bookTicker
    │   ├─ bybit_funding_history → /v5/market/funding/history (category=linear)
    │   └─ bybit_perp_ticker → /v5/market/tickers
    │
    └─ funding_reports.py
        ├─ write_observation: appends FundingObservation as JSON line
        └─ write_summary: reads JSONL, computes max/median APR, basis, net edge → summary.json
```

**Key insight:** V6-B scans spot vs perp basis + funding rates to identify cash-and-carry opportunities.
It uses Kraken spot as the sole spot venue and Kraken/Binance/Bybit as perp venues.
Quote currency mismatch (USD vs USDT) adds a 20bps penalty buffer.

---

### 1.3 V6-C: Alt Funding/Basis Anomaly Monitor

**Entry point:** `run_v6_alt_funding_monitor.py`
**Core files:** `funding_models_alt.py`, `funding_scanner_alt.py`, `funding_reports_alt.py`
(Reuses `funding_config.py` and `funding_venues.py`)

**Architecture adds:**
```
funding_models_alt.py
    ├─ DepthLevel: single order book depth level (price + optional size)
    ├─ FundingObservationAlt: extended model (~35 fields)
    │   - Adds depth fields (spot_bid_size, spot_ask_size, etc.)
    │   - Three cost scenarios per observation:
    │     * conservative_taker_cost_bps (taker fees everywhere)
    │     * mixed_maker_taker_cost_bps (maker spot + maker perp, reduced buffers)
    │     * optimistic_maker_cost_bps (maker everywhere, minimal buffers)
    │   - candidate (based on conservative scenario)
    │   - durable_candidate (persistence-tracked)
    │   - quote_stale, quote_age_ms
    ├─ CostScenario: dataclass with name, fees, buffers
    │   - total_cost_bps(round_trip, has_mismatch): entry + exit + buffers
    ├─ PersistenceTracker: tracks consecutive candidate polls per asset+venue
    │   - poll_candidate(ts_ms) → increments consecutive_count
    │   - poll_non_candidate(ts_ms) → resets consecutive_count
    │   - is_durable(min_polls) → max_consecutive_count >= threshold
    └─ make_cost_scenarios() → returns dict of 3 CostScenario objects
       Defaults: spot_taker=40bps, perp_taker=5bps, spot_maker=20bps, perp_maker=2bps

funding_scanner_alt.py
    └─ AltFundingScanner class:
        - __init__: filters assets to those with spot + at least one perp mapping
        - run(): polling loop with:
          1. Fetch kraken spot for each asset
          2. Fetch perp funding+ticker for each perp venue
          3. make_observation_alt() → 3-scenario cost computation
          4. PersistenceTracker → marks durable after min_persistence_polls consecutive hits
          5. Writes to 2 JSONLs: observations + candidates
        - Outputs durable candidate alerts to stdout
    Also has standalone functions: fetch_kraken_spot, fetch_perp_funding_and_ticker, _compute_net_edges

funding_reports_alt.py
    ├─ write_observation_alt: single observation JSONL
    ├─ CandidateState: tracks candidate persistence (duplicate of PersistenceTracker logic)
    ├─ write_candidate_alt: writes candidate records with key fields only
    └─ write_summary_alt: comprehensive summary with:
        - Top 10 observations by conservative net edge
        - Top 10 durable candidates
        - Max/median APR, basis
        - Missing asset/venue counts, rejection reason breakdown
```

**Key differences from V6-B:**
- 3-tier cost scenarios instead of single cost estimate
- Persistence tracking: candidate must survive N consecutive polls to be "durable"
- Dual JSONL output (observations + filtered candidates)
- Top-N rankings in summary
- Defaults target altcoins (SOL, XRP, DOGE, LINK, AVAX, ADA, etc.)
- Longer default duration (600s vs 120s)
- Stricter thresholds (APR=30% vs 20%, net_edge=25bps)

---

### 1.4 Test Coverage

**test_v6_scanner.py** (82 lines):
- Symbol normalization: parse, get_spec, USDT spec
- Opportunity calculation: cross-venue opp, same-venue rejection, quote mismatch, fee erosion, missing data, latency buffer
- Tests calculate_opportunity() with synthetic Ticker objects

**test_v6b_funding.py** (183 lines):
- Symbol mapping: all assets have required keys, quote map consistency, funding intervals
- PriceLevel: mid price, mark fallback, staleness detection
- Opportunity logic: positive funding candidate, negative funding rejection, zero funding, fee calculation
- Reports: JSONL write+read, summary stats (max/median APR, basis, net edge)

**test_v6c_alt_funding.py** (460 lines):
- Alt symbol mapping: SOL, XRP, DOGE (no kraken perp), PEPE (no kraken spot), missing perps
- Cost scenarios: 3 scenarios exist, cost ordering (conservative > mixed > optimistic), mismatch impact
- Make observation alt: creation, three scenarios, quote mismatch rejection, match+high funding = candidate, negative funding
- Perp mid calculation: bid/ask, mark fallback, preference order, edge cases
- Compute net edges: positive funding, no funding, mismatch increases cost
- PersistenceTracker: consecutive increments, broken streak, durability, new tracker not durable
- CandidateState: consecutive polls, durability met, non-candidate breaks streak
- Reports: observation write, candidate write, summary with data
- Security test: no place_order/create_order/submit_order in source

---

## 2. venue_agnostic_signal_observer

**Location:** `examples/strategies/venue_agnostic_signal_observer/`
**Purpose:** Observer-only signal evaluation. Takes signal events (from CSV or generated cross-market moves), evaluates forward returns on a target instrument across multiple time horizons, with fee/slippage modeling. No orders, no API keys.

### 2.1 Module Structure

```
run_signal_observer.py ── CLI entry point
    │
    ├─ config.py
    │   ├─ Horizon: (name, seconds) - e.g. ("10s", 10.0)
    │   ├─ DEFAULT_HORIZONS: [10s, 30s, 60s, 5m, 15m, 1h]
    │   ├─ FeeModel: fee_bps + slippage_bps + quote_mismatch_buffer_bps
    │   │   - total_cost_bps(quote_mismatch) → fee + slippage [+ mismatch buffer]
    │   ├─ SignalSourceConfig: CSV path + column mapping OR cross-market config
    │   │   - cross_market_source_venue/instrument, target_venue/instrument
    │   │   - move_threshold_bps, lookback_seconds, cooldown_seconds
    │   └─ ObserverConfig: horizons, fee_model, signal_source, bars_csv_path, output_dir, synthetic flag
    │
    ├─ models.py
    │   ├─ SignalEvent: signal_id, timestamp, source/target venue+instrument, signal_type, direction, strength
    │   │   - to_dict(), to_json(), from_dict()
    │   ├─ ForwardReturnResult: signal + horizon + entry_price + forward_price + raw/net returns + excursions
    │   │   - to_dict(), to_json()
    │   ├─ HorizonSummary: aggregated stats per horizon (mean/median ret, win rate, percentiles, best/worst)
    │   ├─ SignalTypeSummary: aggregated stats per signal type
    │   └─ SignalEvaluationSummary: overall run summary with recommendation
    │
    ├─ signals.py
    │   ├─ load_signals_from_csv(path, mapping): reads CSV → List[SignalEvent]
    │   │   - Columns: timestamp, source_venue, source_instrument, target_venue, target_instrument, direction, signal_type, strength
    │   └─ CrossMarketSignalGenerator: generates signals when source price moves > threshold in lookback window
    │       - Sequential, no lookahead
    │       - Cooldown prevents signal spam
    │       - Direction = long (price up) or short (price down)
    │       - Strength = absolute move in bps
    │
    ├─ data_loading.py
    │   ├─ load_bars_from_csv(path, timestamp_col, close_col): reads CSV → sorted (timestamps, prices)
    │   └─ generate_synthetic_data(): deterministic (seed=42) source+target price series
    │       - Source: periodic jumps every jump_interval bars + Gaussian noise
    │       - Target: follows source with delay + attenuation fraction + noise
    │       - Creates exploitable signal patterns for testing
    │
    ├─ forward_returns.py
    │   ├─ get_entry_price(timestamps, prices, signal_ts): finds bar at/after signal using bisect_right
    │   ├─ find_price_at_or_after(timestamps, prices, target_ts): same logic for horizon endpoint
    │   ├─ compute_forward_return(entry, forward, direction): bps return, inverted for short
    │   ├─ compute_excursion(timestamps, prices, entry_ts, entry_price, forward_ts, direction):
    │   │   - Max favorable and adverse excursion during holding period
    │   └─ evaluate_signal(signal, target_ts, target_prices, horizons, fee_model, quote_mismatch):
    │       - For each horizon: find entry price, find horizon price, compute return, subtract costs
    │       - Returns list of ForwardReturnResult (one per horizon)
    │       - Handles missing entry/forward prices gracefully → rejection results
    │
    ├─ observer.py
    │   └─ SignalObserver class:
    │       - run(): orchestrates signal loading → price loading → evaluation → summary
    │       - Signal source priority: signals list > CSV > cross-market generator
    │       - Price source priority: direct lists > bars CSV
    │       - Checks quote currency mismatch (USDT vs USD) → applies buffer
    │       - Calls evaluate_signal for each signal
    │       - _build_summary(): aggregates by horizon, signal type, venue pair
    │         - Statistics: mean/median net return, win rate, percentiles (p25/p50/p75/p90)
    │         - Best/worst signal group identification
    │         - Final recommendation: positive/negative mean net return
    │
    └─ reports.py
        └─ write_outputs(signals_dicts, results, summary, output_dir):
            1. signal_events.jsonl
            2. forward_returns.jsonl
            3. summary.json
            4. summary.csv (horizon summary table)
            5. by_signal_type.csv
            6. rejections.json
```

### 2.2 Data Flow

```
CLI args → ObserverConfig → SignalObserver.run()
    │
    ├─ Load signals:
    │   - --synthetic → generate_synthetic_data() + CrossMarketSignalGenerator.generate()
    │   - --signals-csv + --bars-csv → load_signals_from_csv() + load_bars_from_csv()
    │
    ├─ Load target prices (same source as signals in synthetic mode)
    │
    ├─ For each signal:
    │   evaluate_signal(signal, target_timestamps, target_prices, horizons, fee_model)
    │   → One ForwardReturnResult per horizon
    │
    └─ _build_summary() → SignalEvaluationSummary
        → write_outputs() → 6 report files
```

### 2.3 Key Design Properties

- **Venue-agnostic**: SignalEvent carries source/target venue+instrument fields, but the math works on any price series
- **Multi-horizon**: Each signal is evaluated at 6 horizons simultaneously (10s to 1h)
- **No lookahead**: CrossMarketSignalGenerator uses only past data; bisect_right ensures entry/horizon prices come from correct timestamps
- **Direction-aware**: Short signals invert the return sign
- **Full cost modeling**: fee + slippage + optional quote mismatch buffer
- **Excursion tracking**: Max favorable and adverse excursion within each holding period
- **Synthetic mode**: Deterministic (seed=42) price generation with realistic delay patterns

### 2.4 Test Coverage

**test_all.py** (550 lines), 18 test classes:
1. Config defaults (6 horizons, fee model)
2. Fee model deduction
3. Quote mismatch buffer
4. SignalEvent serialization (to_dict, to_json, from_dict)
5. ForwardReturnResult serialization
6. CSV signal parser
7. Cross-market signal generator (threshold crossing)
8. Cooldown prevents signal spam
9. No lookahead in signal generator
10. Forward returns compute correct bps
11. Direction-adjusted returns (long gains, short losses)
12. Missing entry price rejects signal
13. Missing horizon price rejects only that horizon
14-15. Summary aggregation (by horizon, by signal type)
16. Reports write all 6 output files
17. Synthetic observer run produces results
18-20. Security: no submit_order/market/limit/api_key in source code

---

## Summary of Architectural Patterns

| Aspect | kraken_market_structure_scanner | venue_agnostic_signal_observer |
|--------|--------------------------------|--------------------------------|
| **Core purpose** | Scan live venues for spread/funding edges | Evaluate pre-existing signals for forward returns |
| **Data source** | Live REST API polling | CSV files or synthetic data |
| **Time model** | Real-time polling loop | Backtest-style event replay |
| **Cost model** | Flat bps fees + latency/risk buffers | FeeModel with fee+slippage+optional mismatch |
| **Output** | JSONL observations + summary.json | JSONL signals + forward returns + multi-format reports |
| **Venue support** | Kraken, Binance, Bybit (hardcoded APIs) | Any venue (agnostic, signal carries venue metadata) |
| **Test quality** | 3 test files, ~725 lines total | 1 test file, 550 lines, 18 test classes |
| **Security** | All tests assert no order placement | Tests scan all .py files for forbidden tokens |
| **Evolution** | V6 → V6-B → V6-C (progressive complexity) | Single phase, but supports multiple signal sources |
