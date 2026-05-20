# Study Report: cross-exchange-funding-dispersion-carry-v1

## Run metadata

- **Branch:** `feat/edge-miner-offline-discovery-runner`
- **Starting SHA:** `15a336493fc19242aca5ec9ac78aff56dfb92ed7`
- **Ending SHA:** `27119f35c7f07b41c5e3212441ada9b6661821fa`
- **Working tree before run:** Clean (only unrelated untracked files from other studies)
- **Study ID:** `cross-exchange-funding-dispersion-carry-v1`

## Archive inputs

| Series | File | Rows | SHA256 |
|--------|------|------|--------|
| Binance BTC | `data/funding_dispersion_carry_v1_archives/binance_btc_funding.csv` | 1551 | `3b3917e1a06e7e8207fa8fa9adcec10dfb60eacab2724b3475b20f9ebde47b24` |
| Binance ETH | `data/funding_dispersion_carry_v1_archives/binance_eth_funding.csv` | 1551 | `8f9516c49915dde7fb47f9a8241f6840d0b130419042bf7f24c4b3b398c2fc6d` |
| Bybit BTC | `data/funding_dispersion_carry_v1_archives/bybit_btc_funding.csv` | 1552 | `754f09debaa0b3b37a8331a2931223207bdddc4023bf876e0989199f006963e2` |
| Bybit ETH | `data/funding_dispersion_carry_v1_archives/bybit_eth_funding.csv` | 1552 | `969d02c10f5018a00ca2f03f9ea9f1862d4fb145148bb2fb75edc285484d998a` |

Pipeline content hashes (computed from normalized series, not raw CSVs):

| Series | Pipeline content hash |
|--------|----------------------|
| binance_BTC | `d81a3adeb3b35951097dd403da44fa4b29b5617f90c7cdfd3b8163d817e1e39d` |
| binance_ETH | `1971db3ce1a44792cf794fe668da53676fc4b430237d2e64c8bfc412055ec368` |
| bybit_BTC | `8c0e37af5e13441136a3a1492bcc737cef4a5df318df6e4a0135742232f2363a` |
| bybit_ETH | `7224b3e477a6709821bb676b75b3887e776b8843a9fd6a98a275f0f9e7c5096b` |

- **Binance source:** Binance Vision monthly archive ZIPs (`BTCUSDT-fundingRate-YYYY-MM.zip`, `ETHUSDT-fundingRate-YYYY-MM.zip`), concatenated from 2024-01 through 2025-05.
- **Bybit source:** Bybit v5 public API historical funding endpoint (no auth), paginated monthly from 2024-01 through 2025-05. `fundingRate` field returns decimal fraction strings.
- **Both sources are archive-only.** No REST/WebSocket/authenticated endpoints were used during the evaluation. No live data was fetched. The Bybit CSVs were built from the public API historical endpoint in a single batch before the run.
- **Data window:** 2024-01-01 00:00 UTC to 2025-05-31 16:00 UTC (last common Binance settlement).
- **Aligned settlements:** 1261 (290 dropped as unaligned across venues).

## Commands run

```bash
# Test suite
uv run --no-sync -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_funding_dispersion_carry.py -v
# Result: 91 passed in 0.20s

# Coverage mode
uv run --no-sync -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode coverage
# Result: passed (parameter summary correct, NULL_ITERATIONS=1000, NULL_SEED=42)

# Real evaluation run
uv run --no-sync -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode run \
  --binance-btc-archive .../binance_btc_funding.csv \
  --binance-eth-archive .../binance_eth_funding.csv \
  --bybit-btc-archive .../bybit_btc_funding.csv \
  --bybit-eth-archive .../bybit_eth_funding.csv \
  --output-dir .../cross_exchange_funding_dispersion_carry_v1_27119f3 \
  --seed 42
# Result: completed successfully, exit code 0
```

## Validation results

| Check | Result |
|-------|--------|
| Test suite | 91 passed, 0 failed |
| Coverage mode | Passed (all frozen parameters displayed correctly) |
| Real evaluation run | Completed |
| No forbidden verdicts produced | Confirmed |
| No live/private/order/execution path touched | Confirmed |

## Study-level verdict

**`NEEDS_MORE_DATA_OR_NO_TAIL`**

Produced at **Gate A** (Stage 1 — distribution sizing).

## Gate A summary

Gate A found **zero dispersion events** at every threshold for both assets:

| Asset | 5 bps | 10 bps | 20 bps | 40 bps |
|-------|-------|--------|--------|--------|
| BTC   | 0     | 0      | 0      | 0      |
| ETH   | 0     | 0      | 0      | 0      |

Since both BTC and ETH have zero events at every threshold, Gate A fires `NEEDS_MORE_DATA_OR_NO_TAIL` per precommitment Section 9.

## Gate B summary

Not reached. Gate B was never evaluated because Gate A terminated the pipeline.

## Powered cells

Zero. No cell was evaluated past Gate A.

## Dispersion statistics (diagnostic)

The cross-exchange funding spread (Bybit minus Binance) is extremely tight:

| Asset | Mean abs dispersion | Median abs dispersion | Max abs dispersion |
|-------|-------------------|----------------------|-------------------|
| BTC   | 0.44 bps           | 0.31 bps             | 4.14 bps          |
| ETH   | 0.42 bps           | 0.27 bps             | 4.49 bps          |

No single settlement in the entire 17-month window (1261 aligned settlements) produced a cross-exchange funding spread ≥ 5 bps. The maximum observed dispersion was 4.49 bps (ETH).

## Cells reaching labels

- `PASS_PRE_NULL`: 0
- `PASS_NULL`: 0
- FDR survivors: 0
- Holdout survivors: 0

## Precommitment parameter changes after seeing results

**None.** The precommitment parameters (thresholds, holds, costs, null iteration count, seed, FDR alpha, family size) were not modified after seeing the results.

## No live/private/order/execution path was touched

Confirmed. No REST/WebSocket/authenticated endpoints, no order placement, no exchange API keys, no private key handling, no execution simulation. The run was purely archive-only data processing.

## Output directory

```
examples/strategies/venue_agnostic_signal_observer/reports/funding_dispersion_carry/cross_exchange_funding_dispersion_carry_v1_27119f3/fdc_20260518T202319_073672_d67801/
├── metadata.json   (full reproducibility metadata)
└── verdict.json     (study verdict)
```

No grid evaluation was produced because the pipeline exited at Gate A with zero events.