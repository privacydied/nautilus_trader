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
- `run`: Executes the Stage 0-8 pipeline against archive data. Requires all four archive paths to be provided.

The archive loader handles two formats:
- **Binance Vision CSV**: columns `calc_time` (ms epoch), `funding_interval_hours`, `last_funding_rate` (decimal fraction)
- **Bybit CSV**: columns `symbol`, `fundingRate` (decimal fraction string), `fundingRateTimestamp` (ms epoch)

Both parsers are fail-closed: missing columns, unparsable rows, duplicate timestamps, and non-finite rates all raise `ValueError`. Timestamps are converted from milliseconds to nanoseconds. Series are returned sorted by timestamp.

### `tests/test_funding_dispersion_carry.py`
91 tests covering all 10 required test categories, loader validation, and additional fidelity checks. Uses only synthetic data — no network calls, no real archives. Includes specific tests for the Stage 8 verdict-assembly rule precedence (rules 5/6/7) using `reached_pass_null` / `reached_pass_pre_null` boolean flags to correctly track pipeline progress across relabeling stages. Loader tests cover Binance and Bybit CSV parsing, unit normalization through Stage 0, and all fail-closed validation paths.

## How to run (once archives are available)

```bash
cd /mnt/nasirjones/py/nautilus_trader
source .venv/bin/activate

# Print configuration without running
python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode coverage

# Run the study (requires all four archive paths)
python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry --mode run \
    --binance-btc-archive path/to/binance_btc_funding.csv \
    --binance-eth-archive path/to/binance_eth_funding.csv \
    --bybit-btc-archive path/to/bybit_btc_funding.csv \
    --bybit-eth-archive path/to/bybit_eth_funding.csv \
    --output-dir reports/funding_dispersion_carry
```

For Binance Vision, download monthly `.zip` files from `https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYMBOL}/` and extract the CSV. For Bybit, export funding history CSVs with columns `symbol,fundingRate,fundingRateTimestamp`.

## Running tests

```bash
python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_funding_dispersion_carry.py -v
```

## The study has NOT been run

No verdict file exists. No evaluation has been performed against real data. The pipeline is ready to run but has not been executed. The precommitment must be frozen (committed to the project) before the first evaluation run. Running against real data before freezing would break the precommitment property.

All three pre-freeze ambiguities have been resolved and written into the precommitment (Appendix A):
- R1: funding-unit heuristic confirmed against actual Binance Vision CSV and Bybit archive format (both are decimal fractions)
- R2: worst-decile = p10 single value
- R3: Stage 8 bug fixed with boolean tracking fields

No live, private-key, or order-placement code paths exist anywhere in the implementation.