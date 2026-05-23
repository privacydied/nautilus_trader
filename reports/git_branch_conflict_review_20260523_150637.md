# Git conflict branch review 20260523_150637

- Base: `chore/flatten-fork-branches-preserve-registry` @ `e300dec3556d`
- Canonical registry: `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`
- Registry line count at start: 744
- Raw diff files: `reports/git_branch_conflict_review_raw_20260523_150637/`

## Summary table

| branch | category | files | touches canonical registry | deletes/renames registry | registry unique candidates | disposition |
|---|---|---:|---:|---:|---:|---|
| `fork/fix-derivatives-lead-lag-labeling` | source changes still useful | 173 | False | False | 2 | harvest candidates: examples/strategies/REJECTED_RESEARCH.md, examples/strategies/docs/audit-20260513-report-v2.md, examples/strategies/kraken_btcusd_research/README.md, example... |
| `fork/kraken-v6-market-structure-scanner` | obsolete/rejected historical code only | 76 | False | False | 0 | skip: old Kraken rejected strategy code; do not revive obsolete scanner/strategy code |
| `kraken-btcusd-v3-maker-mean-reversion` | source changes still useful | 41 | False | False | 0 | harvest candidates: examples/strategies/kraken_btcusd_research/README.md, examples/strategies/kraken_btcusd_research/tests/helpers.py, examples/strategies/kraken_btcusd_research... |
| `fork/feat/hyperliquid-data-observer-v0` | already represented | 11 | False | False | 0 | skip: content superseded by already merged clean branch or current consolidation |
| `kraken-v6-market-structure-scanner` | source changes still useful | 173 | False | False | 0 | harvest candidates: examples/strategies/REJECTED_RESEARCH.md, examples/strategies/docs/audit-20260513-report-v2.md, examples/strategies/kraken_btcusd_research/README.md, example... |
| `fork/feat/hyperliquid-funding-divergence-phase0-rerun` | unsafe or ambiguous | 24 | False | False | 24 | skip: touches forbidden/ambiguous path terms |
| `fork/kraken-btcusd-v4-1h-trend` | obsolete/rejected historical code only | 41 | False | False | 0 | skip: old Kraken rejected strategy code; do not revive obsolete scanner/strategy code |
| `fork/kraken-btcusd-v3-maker-mean-reversion` | source changes still useful | 41 | False | False | 0 | harvest candidates: examples/strategies/kraken_btcusd_research/README.md, examples/strategies/kraken_btcusd_research/tests/helpers.py, examples/strategies/kraken_btcusd_research... |
| `fork/kraken-v7-l2-maker-paper` | source changes still useful | 88 | False | False | 0 | harvest candidates: examples/strategies/kraken_btcusd_research/README.md, examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md, examples/strategies/kraken_btcusd_r... |
| `fork/feat/liquidation-cascade-aftershock-phase0` | unsafe or ambiguous | 25 | False | False | 24 | skip: touches forbidden/ambiguous path terms |
| `fork/kraken-v5-multiasset-htf-momentum` | source changes still useful | 64 | False | False | 0 | harvest candidates: examples/strategies/kraken_btcusd_research/README.md, examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md, examples/strategies/kraken_btcusd_r... |

## Branch details

### `fork/fix-derivatives-lead-lag-labeling`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__fix-derivatives-lead-lag-labeling.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__fix-derivatives-lead-lag-labeling.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: `examples/strategies/REJECTED_RESEARCH.md`
- Registry unique candidate lines: 2
- Targeted harvest candidates:
  - `examples/strategies/REJECTED_RESEARCH.md`
  - `examples/strategies/docs/audit-20260513-report-v2.md`
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md`
  - `examples/strategies/kraken_btcusd_research/tests/__init__.py`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v4_trend_smoke.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/__init__.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/test_v7_l2_maker_paper.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6_scanner.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6b_funding.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6c_alt_funding.py`
  - `examples/strategies/venue_agnostic_signal_observer/README.md`
  - `examples/strategies/venue_agnostic_signal_observer/reports/deliverables_v2.md`
  - `examples/strategies/venue_agnostic_signal_observer/reports/lead_lag_v1_rejection.md`
  - `examples/strategies/venue_agnostic_signal_observer/tests/__init__.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_all.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_cross_venue_lead_lag.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_lead_lag_pipeline.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_research_report_miner.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_synthetic_fixtures.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_trade_flow_impulse.py`
- Registry unique sample:

```text
examples/strategies/REJECTED_RESEARCH.md: - **Derivatives flow impulse → spot lead-lag** (perp/futures venue as source, not spot). IMPLEMENTED_OBSERVER_ONLY. The `derivatives_lead_lag_v1` observer is built but has never been tested against an actual derivatives source (Binance/Bybit/Kraken futures perps). The initial v1 smoke run used Coinbase spot as source — a same-asset spot/spot pair already in locked gate #2. Actual derivatives-source thesis: OPEN_UNTESTED.
examples/strategies/REJECTED_RESEARCH.md: ## Instrument Type Note
```

### `fork/kraken-v6-market-structure-scanner`

- Category: obsolete/rejected historical code only
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v6-market-structure-scanner.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v6-market-structure-scanner.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Skip reason: old Kraken rejected strategy code; do not revive obsolete scanner/strategy code

### `kraken-btcusd-v3-maker-mean-reversion`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/kraken-btcusd-v3-maker-mean-reversion.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/kraken-btcusd-v3-maker-mean-reversion.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Targeted harvest candidates:
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v3_helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v3_maker_entry.py`

### `fork/feat/hyperliquid-data-observer-v0`

- Category: already represented
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-data-observer-v0.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-data-observer-v0.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Skip reason: content superseded by already merged clean branch or current consolidation

### `kraken-v6-market-structure-scanner`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/kraken-v6-market-structure-scanner.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/kraken-v6-market-structure-scanner.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: `examples/strategies/REJECTED_RESEARCH.md`
- Registry unique candidate lines: 0
- Targeted harvest candidates:
  - `examples/strategies/REJECTED_RESEARCH.md`
  - `examples/strategies/docs/audit-20260513-report-v2.md`
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md`
  - `examples/strategies/kraken_btcusd_research/tests/__init__.py`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v4_trend_smoke.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/__init__.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/test_v7_l2_maker_paper.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6_scanner.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6b_funding.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6c_alt_funding.py`
  - `examples/strategies/venue_agnostic_signal_observer/README.md`
  - `examples/strategies/venue_agnostic_signal_observer/reports/deliverables_v2.md`
  - `examples/strategies/venue_agnostic_signal_observer/reports/lead_lag_v1_rejection.md`
  - `examples/strategies/venue_agnostic_signal_observer/tests/__init__.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_all.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_cross_venue_lead_lag.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_lead_lag_pipeline.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_research_report_miner.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_synthetic_fixtures.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py`
  - `examples/strategies/venue_agnostic_signal_observer/tests/test_trade_flow_impulse.py`

### `fork/feat/hyperliquid-funding-divergence-phase0-rerun`

- Category: unsafe or ambiguous
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-funding-divergence-phase0-rerun.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-funding-divergence-phase0-rerun.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: `examples/strategies/REJECTED_RESEARCH.md`
- Registry unique candidate lines: 24
- Skip reason: touches forbidden/ambiguous path terms
- Registry unique sample:

```text
examples/strategies/REJECTED_RESEARCH.md: ## Hyperliquid BTC/ETH Funding Divergence Phase 0 — Distribution Kill Detail
examples/strategies/REJECTED_RESEARCH.md: ### Study identity
examples/strategies/REJECTED_RESEARCH.md: ### Mechanism tested
examples/strategies/REJECTED_RESEARCH.md: ### Data window
examples/strategies/REJECTED_RESEARCH.md: ### Inputs
examples/strategies/REJECTED_RESEARCH.md: |---|---|---:|---|---|---|
examples/strategies/REJECTED_RESEARCH.md: ### Pre-registered kill criteria
examples/strategies/REJECTED_RESEARCH.md: ### Result table
examples/strategies/REJECTED_RESEARCH.md: |---|---:|---:|---:|---:|---|
examples/strategies/REJECTED_RESEARCH.md: ### Interpretation
examples/strategies/REJECTED_RESEARCH.md: ### What this closes
examples/strategies/REJECTED_RESEARCH.md: ### What this does NOT close
examples/strategies/REJECTED_RESEARCH.md: ### Locked gate
examples/strategies/REJECTED_RESEARCH.md: ### Run artifacts
examples/strategies/REJECTED_RESEARCH.md: ### Safety statement
examples/strategies/REJECTED_RESEARCH.md: ## Polymarket BTC Up/Down Liquidity Probe v0 — Rejection Detail
examples/strategies/REJECTED_RESEARCH.md: ### Correction History
examples/strategies/REJECTED_RESEARCH.md: ### Experiment
examples/strategies/REJECTED_RESEARCH.md: ### Near-Expiry Depth Collapse (Primary Rejection Evidence)
examples/strategies/REJECTED_RESEARCH.md: ### Key Findings
```

### `fork/kraken-btcusd-v4-1h-trend`

- Category: obsolete/rejected historical code only
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v4-1h-trend.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v4-1h-trend.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Skip reason: old Kraken rejected strategy code; do not revive obsolete scanner/strategy code

### `fork/kraken-btcusd-v3-maker-mean-reversion`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v3-maker-mean-reversion.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v3-maker-mean-reversion.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Targeted harvest candidates:
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v3_helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v3_maker_entry.py`

### `fork/kraken-v7-l2-maker-paper`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v7-l2-maker-paper.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v7-l2-maker-paper.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Targeted harvest candidates:
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md`
  - `examples/strategies/kraken_btcusd_research/tests/__init__.py`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v4_trend_smoke.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/__init__.py`
  - `examples/strategies/kraken_l2_maker_paper/tests/test_v7_l2_maker_paper.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6_scanner.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6b_funding.py`
  - `examples/strategies/kraken_market_structure_scanner/tests/test_v6c_alt_funding.py`

### `fork/feat/liquidation-cascade-aftershock-phase0`

- Category: unsafe or ambiguous
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__liquidation-cascade-aftershock-phase0.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__liquidation-cascade-aftershock-phase0.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: `examples/strategies/REJECTED_RESEARCH.md`
- Registry unique candidate lines: 24
- Skip reason: touches forbidden/ambiguous path terms
- Registry unique sample:

```text
examples/strategies/REJECTED_RESEARCH.md: ## Hyperliquid BTC/ETH Funding Divergence Phase 0 — Distribution Kill Detail
examples/strategies/REJECTED_RESEARCH.md: ### Study identity
examples/strategies/REJECTED_RESEARCH.md: ### Mechanism tested
examples/strategies/REJECTED_RESEARCH.md: ### Data window
examples/strategies/REJECTED_RESEARCH.md: ### Inputs
examples/strategies/REJECTED_RESEARCH.md: |---|---|---:|---|---|---|
examples/strategies/REJECTED_RESEARCH.md: ### Pre-registered kill criteria
examples/strategies/REJECTED_RESEARCH.md: ### Result table
examples/strategies/REJECTED_RESEARCH.md: |---|---:|---:|---:|---:|---|
examples/strategies/REJECTED_RESEARCH.md: ### Interpretation
examples/strategies/REJECTED_RESEARCH.md: ### What this closes
examples/strategies/REJECTED_RESEARCH.md: ### What this does NOT close
examples/strategies/REJECTED_RESEARCH.md: ### Locked gate
examples/strategies/REJECTED_RESEARCH.md: ### Run artifacts
examples/strategies/REJECTED_RESEARCH.md: ### Safety statement
examples/strategies/REJECTED_RESEARCH.md: ## Polymarket BTC Up/Down Liquidity Probe v0 — Rejection Detail
examples/strategies/REJECTED_RESEARCH.md: ### Correction History
examples/strategies/REJECTED_RESEARCH.md: ### Experiment
examples/strategies/REJECTED_RESEARCH.md: ### Near-Expiry Depth Collapse (Primary Rejection Evidence)
examples/strategies/REJECTED_RESEARCH.md: ### Key Findings
```

### `fork/kraken-v5-multiasset-htf-momentum`

- Category: source changes still useful
- Raw stat: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v5-multiasset-htf-momentum.stat.txt`
- Raw name-status: `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v5-multiasset-htf-momentum.name-status.txt`
- Touches canonical registry path: False
- Branch has canonical registry path: False
- Deletes/renames canonical registry: False
- Registry paths on branch: none
- Registry unique candidate lines: 0
- Targeted harvest candidates:
  - `examples/strategies/kraken_btcusd_research/README.md`
  - `examples/strategies/kraken_btcusd_research/reports/RESEARCH_LOG.md`
  - `examples/strategies/kraken_btcusd_research/tests/__init__.py`
  - `examples/strategies/kraken_btcusd_research/tests/helpers.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_all.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_basic.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_instrument.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_live_guard.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_real_reports.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py`
  - `examples/strategies/kraken_btcusd_research/tests/test_v4_trend_smoke.py`
  - `examples/strategies/kraken_v5_portfolio/tests/__init__.py`
  - `examples/strategies/kraken_v5_portfolio/tests/test_v5_portfolio.py`
  - `examples/strategies/kraken_v5_portfolio/tests/test_v5_smoke.py`



## Harvest decisions executed

- `fork/feat/hyperliquid-funding-divergence-phase0-rerun`: harvested targeted source/test deltas for Hyperliquid funding archive JSONL ingestion, provenance/cadence manifest hardening, runner `load_funding_file` dispatch, and associated focused tests. Deliberately did not import branch `reports/`, `data/`, `manifest.json`, or alternate `examples/strategies/REJECTED_RESEARCH.md` path. Canonical registry remained unchanged at 744 lines and passed the registry guard immediately after checkout.
- `fork/feat/liquidation-cascade-aftershock-phase0`: harvested only `examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_CASCADE_AFTERSHOCK_V0_RECONNAISSANCE.md`, a static public-data reconnaissance/no-go note. Deliberately did not import reports, manifests, source, runtime artifacts, or alternate registry path. Canonical registry remained unchanged at 744 lines and passed the registry guard immediately after checkout.
- Kraken conflict branches and `fork/fix-derivatives-lead-lag-labeling`: skipped for code harvest. Their useful registry variant lines were already preserved in the canonical registry conflict-review appendix or were obsolete/rejected historical code; reviving strategy/scanner source would add broad stale research code without clear current value.
- `fork/feat/hyperliquid-data-observer-v0`: skipped as already represented by the clean branch merged during the prior flattening pass.

Registry deletion/rename accepted: no.
