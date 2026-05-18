# IMPLEMENTATION_NOTES.md

Cross-exchange funding dispersion carry pipeline — implementation notes.

## Module guide

### `funding_dispersion_carry.py`
Frozen constants, data contracts (dataclasses), and the verdict guard. All precommitment-specified values are defined here as module-level constants. The cell grid (24 cells = 2 assets × 4 thresholds × 3 holds) is constructed via `build_all_cell_identifiers()`. The verdict guard enforces Section 14's allowed and forbidden verdicts. Cell-level intermediate labels (PASS_PRE_NULL, PASS_NULL) are accepted during pipeline processing but are not allowed as final study-level verdicts. Two boolean tracking fields on `CellRecord` — `reached_pass_pre_null` and `reached_pass_null` — record whether a cell reached each pipeline stage, persisting across relabeling so that Stage 8 can correctly evaluate "cell reached X" conditions without relying on intermediate verdict labels that have been overwritten.

### `funding_dispersion_stages.py`
The eight-stage pipeline logic (Stages 0-8). Each stage is a pure function that takes its inputs and returns its outputs plus an optional early-exit verdict. Stages do not mutate global state. The pipeline function `run_pipeline()` orchestrates all stages in sequence.

- **Stage 0** (`stage0_load_and_normalize`): Data ingestion, unit detection/normalization, alignment, 70/30 split. Fail-closed funding-unit detection.
- **Stage 1** (`stage1_gate_a`): Phase 0 Gate A — distribution sizing. Counts events per asset per threshold. Kills if both assets have <50 events at every threshold.
- **Stage 2** (`stage2_gate_b`): Phase 0 Gate B — economic feasibility. Two ordered checks: no powered combos → NEEDS_MORE_DATA; powered but all negative median → REJECTED_COST_WALL.
- **Stage 3** (`stage3_grid_evaluation`): Evaluates all 24 cells on train data. Always produces 24 cell records.
- **Stage 4** (`stage4_pre_null_economic_gates`): Labels each cell by the first matching rule (underpowered / cost-wall / rejected / pass).
- **Stage 5** (`stage5_event_vector_shift_null`): Event-vector circular shift null test on PASS_PRE_NULL cells. 1000 iterations, alpha 0.05, seed 42. Preserves the future-carry series intact; shifts event/direction positions. Non-tested cells get p=1.
- **Stage 6** (`stage6_fdr`): Benjamini-Yekutieli FDR correction across all 24 cells. Family size frozen at 24 regardless of how many cells were null-tested.
- **Stage 7** (`stage7_holdout_confirmation`): Holdout re-evaluation of FDR survivors with ≥20 valid non-overlapping events.
- **Stage 8** (`stage8_study_verdict`): Deterministic verdict assembly from ordered rule list.

### `run_funding_dispersion_carry.py`
CLI entry point with argparse. Two modes:
- `coverage`: Prints frozen parameters, grid dimensions, data requirements. Does NOT run the study.
- `run`: Executes the Stage 0-8 pipeline against archive data. **The data loading function raises NotImplementedError** — it must be implemented before running.

### `tests/test_funding_dispersion_carry.py`
73 tests covering all 10 required test categories plus additional fidelity checks. Uses only synthetic data — no network calls, no real archives. Includes specific tests for the Stage 8 verdict-assembly rule precedence (rules 5/6/7) using `reached_pass_null` / `reached_pass_pre_null` boolean flags to correctly track pipeline progress across relabeling stages.

## How to run (once archives are available)

```bash
cd /mnt/nasirjones/py/nautilus_trader
source .venv/bin/activate

# Print configuration without running
python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode coverage

# Run the study (requires implementing load_archive_data first)
python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode run \
    --binance-btc-archive path/to/binance_btc_funding.csv \
    --binance-eth-archive path/to/binance_eth_funding.csv \
    --bybit-btc-archive path/to/bybit_btc_funding.csv \
    --bybit-eth-archive path/to/bybit_eth_funding.csv \
    --output-dir reports/funding_dispersion_carry
```

## Running tests

```bash
python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_funding_dispersion_carry.py -v
```

## The study has NOT been run

No verdict file exists. No evaluation has been performed against real data. The `run_funding_dispersion_carry.py` entry point calls `load_archive_data()` which raises `NotImplementedError`. This is intentional — the precommitment must be frozen (committed to the project) before the first evaluation run. Running against real data before freezing would break the precommitment property.

## Data loading interface

The `load_archive_data()` function in `run_funding_dispersion_carry.py` is a stub. Before running the study, it must be implemented to:
1. Read Binance Vision funding-rate archives (CSV format from data.binance.vision)
2. Read Bybit funding-rate archives (CSV format from bybit.com/api)
3. Return a dict mapping `"binance_BTC"`, `"binance_ETH"`, `"bybit_BTC"`, `"bybit_ETH"` to lists of `(timestamp_ns, rate)` tuples

Each series must contain per-settlement funding rates with nanosecond-precision timestamps. Binance and Bybit both settle BTC/ETH perp funding on approximately 8-hour intervals.