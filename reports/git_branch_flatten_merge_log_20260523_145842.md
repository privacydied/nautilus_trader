# Merge log 20260523_145842

## fork/polymarket-btcusd-arb-phase1 `1edc29c5c78b`

merge --squash stdout:
```
Squash commit -- not updating HEAD

```
stderr:
```
Automatic merge went well; stopped before committing as requested

```
cached stat:
```
 .../strategies/polymarket_btcusd_arb/README.md     |   3 +
 .../strategies/polymarket_btcusd_arb/STATUS.md     |  79 +++++
 .../strategies/polymarket_btcusd_arb/__init__.py   |   1 +
 .../strategies/polymarket_btcusd_arb/baseline.py   |   9 +
 .../polymarket_btcusd_arb/binance_data.py          |  36 +++
 .../strategies/polymarket_btcusd_arb/config.py     |  29 ++
 .../strategies/polymarket_btcusd_arb/data_cache.py |  29 ++
 .../polymarket_btcusd_arb/empirical_model.py       |   7 +
 .../polymarket_btcusd_arb/fair_probability.py      |  17 +
 .../polymarket_btcusd_arb/forward_returns.py       |  11 +
 examples/strategies/polymarket_btcusd_arb/gates.py |  14 +
 .../strategies/polymarket_btcusd_arb/models.py     |  39 +++
 .../polymarket_btcusd_arb/observer_strategy.py     |   1 +
 .../polymarket_btcusd_arb/parity_tests/__init__.py |   0
 .../parity_tests/fixtures/probability_cases.json   | 114 +++++++
 .../parity_tests/fixtures/settlement_cases.json    | 198 ++++++++++++
 .../parity_tests/test_probability_parity.py        |   7 +
 .../parity_tests/test_settlement_parity.py         |  44 +++
 .../polymarket_btcusd_arb/polymarket_data.py       |  48 +++
 .../strategies/polymarket_btcusd_arb/reports.py    |  34 ++
 .../polymarket_btcusd_arb/run_backtest.py          | 136 ++++++++
 .../polymarket_btcusd_arb/safety_checks.py         |  29 ++
 .../polymarket_btcusd_arb/settlement_predictor.py  | 351 +++++++++++++++++++++
 .../polymarket_btcusd_arb/signal_generator.py      |  26 ++
 .../polymarket_btcusd_arb/tests/__init__.py        |   0
 .../tests/test_backtest_smoke.py                   |   2 +
 .../polymarket_btcusd_arb/tests/test_baseline.py   |   5 +
 .../tests/test_binance_data.py                     |  24 ++
 .../polymarket_btcusd_arb/tests/test_config.py     |   7 +
 .../polymarket_btcusd_arb/tests/test_data_cache.py |   6 +
 .../tests/test_forward_returns.py                  |   8 +
 .../polymarket_btcusd_arb/tests/test_gates.py      |   8 +
 .../tests/test_polymarket_data.py                  |  18 ++
 .../polymarket_btcusd_arb/tests/test_reports.py    |   4 +
 .../tests/test_safety_checks.py                    |   4 +
 .../tests/test_signal_generator.py                 |   7 +
 36 files changed, 1355 insertions(+)

```
commit:
```
[chore/flatten-fork-branches-preserve-registry c9b9b29071] Squash merge fork/polymarket-btcusd-arb-phase1
 36 files changed, 1355 insertions(+)
 create mode 100644 examples/strategies/polymarket_btcusd_arb/README.md
 create mode 100644 examples/strategies/polymarket_btcusd_arb/STATUS.md
 create mode 100644 examples/strategies/polymarket_btcusd_arb/__init__.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/baseline.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/binance_data.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/config.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/data_cache.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/empirical_model.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/fair_probability.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/forward_returns.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/gates.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/models.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/observer_strategy.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/parity_tests/__init__.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/parity_tests/fixtures/probability_cases.json
 create mode 100644 examples/strategies/polymarket_btcusd_arb/parity_tests/fixtures/settlement_cases.json
 create mode 100644 examples/strategies/polymarket_btcusd_arb/parity_tests/test_probability_parity.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/parity_tests/test_settlement_parity.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/polymarket_data.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/reports.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/run_backtest.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/safety_checks.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/settlement_predictor.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/signal_generator.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/__init__.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_backtest_smoke.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_baseline.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_binance_data.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_config.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_data_cache.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_forward_returns.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_gates.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_polymarket_data.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_reports.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_safety_checks.py
 create mode 100644 examples/strategies/polymarket_btcusd_arb/tests/test_signal_generator.py

```
post status: `?? reports/git_branch_flatten_merge_log_20260523_145842.md`

## fork/fix-derivatives-lead-lag-labeling `226cce57e550`

merge --squash stdout:
```
Auto-merging examples/strategies/venue_agnostic_signal_observer/data_adapters.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/data_adapters.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/data_fetcher.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/data_fetcher.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/event_study.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/event_study.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/forward_returns.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/forward_returns.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/observer.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/observer.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/signals.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/signals.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_all.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_all.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tick_store.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tick_store.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/venue_agnostic_signal_observer/data_adapters.py', 'examples/strategies/venue_agnostic_signal_observer/data_fetcher.py', 'examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/event_study.py', 'examples/strategies/venue_agnostic_signal_observer/forward_returns.py', 'examples/strategies/venue_agnostic_signal_observer/lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/observer.py', 'examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py', 'examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py', 'examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py', 'examples/strategies/venue_agnostic_signal_observer/signals.py', 'examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_all.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py', 'examples/strategies/venue_agnostic_signal_observer/tick_store.py', 'examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py']
requires human review: squash conflicts; aborted/reset

## fork/kraken-btcusd-v2-maker-research `2508cffa1d82`

merge --squash stdout:
```
Squash commit -- not updating HEAD

```
stderr:
```
Automatic merge went well; stopped before committing as requested

```
cached stat:
```
 data/kraken/BTCUSD_15m_2024h1.csv                  |  14594 +++
 data/kraken/BTCUSD_15m_2024h2.csv                  |  20546 ++++
 data/kraken/BTCUSD_15m_2025.csv                    |  35042 ++++++
 data/kraken/BTCUSD_15m_2026.csv                    |  12571 +++
 data/kraken/BTCUSD_5m.csv                          |      1 +
 data/kraken/BTCUSD_5m_2024h1.csv                   |  43778 ++++++++
 data/kraken/BTCUSD_5m_2024h2.csv                   |  61634 ++++++++++
 data/kraken/BTCUSD_5m_2025.csv                     | 105122 ++++++++++++++++++
 data/kraken/BTCUSD_5m_2026.csv                     |  37710 +++++++
 examples/__init__.py                               |      1 +
 examples/strategies/__init__.py                    |      1 +
 .../strategies/kraken_btcusd_research/README.md    |    115 +
 .../strategies/kraken_btcusd_research/__init__.py  |      4 +
 .../strategies/kraken_btcusd_research/config.py    |     54 +
 .../strategies/kraken_btcusd_research/config_v2.py |     50 +
 .../download_kraken_ohlcv.py                       |    293 +
 .../kraken_btcusd_research/import_15m_catalogs.py  |     66 +
 .../import_kraken_ohlcv_to_catalog.py              |    101 +
 .../strategies/kraken_btcusd_research/reports.py   |    315 +
 .../kraken_btcusd_research/run_backtest.py         |    188 +
 .../run_live_kraken_guarded.py                     |    119 +
 .../kraken_btcusd_research/run_v2_research.py      |    136 +
 .../strategies/kraken_btcusd_research/strategy.py  |    412 +
 .../kraken_btcusd_research/strategy_v2.py          |    306 +
 .../kraken_btcusd_research/tests/helpers.py        |    184 +
 .../kraken_btcusd_research/tests/test_all.py       |     45 +
 .../tests/test_backtest_smoke.py                   |      1 +
 .../kraken_btcusd_research/tests/test_basic.py     |     58 +
 .../tests/test_breakout_smoke.py                   |     77 +
 .../tests/test_catalog_importer.py                 |    103 +
 .../tests/test_instrument.py                       |     57 +
 .../tests/test_kraken_btcusd_research.py           |     75 +
 .../tests/test_live_guard.py                       |     50 +
 .../tests/test_position_sizing.py                  |     75 +
 .../tests/test_real_reports.py                     |    134 +
 .../tests/test_reports_smoke.py                    |    167 +
 .../tests/test_synthetic_backtest.py               |     63 +
 .../kraken_2024h1/backtest_summary.json            |     19 +
 reports/baseline_v1/kraken_2024h1/equity_curve.csv |      2 +
 reports/baseline_v1/kraken_2024h1/fills.csv        |    559 +
 reports/baseline_v1/kraken_2024h1/positions.csv    |    280 +
 reports/baseline_v1/kraken_2024h1/trades.csv       |    280 +
 .../kraken_2024h2/backtest_summary.json            |     19 +
 reports/baseline_v1/kraken_2024h2/equity_curve.csv |      2 +
 reports/baseline_v1/kraken_2024h2/fills.csv        |    795 +
 reports/baseline_v1/kraken_2024h2/positions.csv    |    398 +
 reports/baseline_v1/kraken_2024h2/trades.csv       |    398 +
 .../baseline_v1/kraken_2025/backtest_summary.json  |     19 +
 reports/baseline_v1/kraken_2025/equity_curve.csv   |      2 +
 reports/baseline_v1/kraken_2025/fills.csv          |   1339 +
 reports/baseline_v1/kraken_2025/positions.csv      |    670 +
 reports/baseline_v1/kraken_2025/trades.csv         |    670 +
 .../baseline_v1/kraken_2026/backtest_summary.json  |     19 +
 reports/baseline_v1/kraken_2026/equity_curve.csv   |      2 +
 reports/baseline_v1/kraken_2026/fills.csv          |    447 +
 reports/baseline_v1/kraken_2026/positions.csv      |    224 +
 reports/baseline_v1/kraken_2026/trades.csv         |    224 +
 reports/kraken_2024h1/backtest_summary.json        |     19 +
 reports/kraken_2024h1/equity_curve.csv             |      2 +
 reports/kraken_2024h1/fills.csv                    |    559 +
 reports/kraken_2024h1/positions.csv                |    280 +
 reports/kraken_2024h1/trades.csv                   |    280 +
 reports/kraken_2024h2/backtest_summary.json        |     19 +
 reports/kraken_2024h2/equity_curve.csv             |      2 +
 reports/kraken_2024h2/fills.csv                    |    795 +
 reports/kraken_2024h2/positions.csv                |    398 +
 reports/kraken_2024h2/trades.csv                   |    398 +
 reports/kraken_2025/backtest_summary.json          |     19 +
 reports/kraken_2025/equity_curve.csv               |      2 +
 reports/kraken_2025/fills.csv                      |   1339 +
 reports/kraken_2025/positions.csv                  |    670 +
 reports/kraken_2025/trades.csv                     |    670 +
 reports/kraken_2026/backtest_summary.json          |     19 +
 reports/kraken_2026/equity_curve.csv               |      2 +
 reports/kraken_2026/fills.csv                      |    447 +
 reports/kraken_2026/positions.csv                  |    224 +
 reports/kraken_2026/trades.csv                     |    224 +
 reports/kraken_v2_2026/backtest_summary.json       |     19 +
 reports/kraken_v2_2026/equity_curve.csv            |      2 +
 reports/kraken_v2_2026/trades.csv                  |      1 +
 80 files changed, 347006 insertions(+)

```
commit:
```
[chore/flatten-fork-branches-preserve-registry b59150e3b5] Squash merge fork/kraken-btcusd-v2-maker-research
 80 files changed, 347006 insertions(+)
 create mode 100644 data/kraken/BTCUSD_15m_2024h1.csv
 create mode 100644 data/kraken/BTCUSD_15m_2024h2.csv
 create mode 100644 data/kraken/BTCUSD_15m_2025.csv
 create mode 100644 data/kraken/BTCUSD_15m_2026.csv
 create mode 100644 data/kraken/BTCUSD_5m.csv
 create mode 100644 data/kraken/BTCUSD_5m_2024h1.csv
 create mode 100644 data/kraken/BTCUSD_5m_2024h2.csv
 create mode 100644 data/kraken/BTCUSD_5m_2025.csv
 create mode 100644 data/kraken/BTCUSD_5m_2026.csv
 create mode 100644 examples/strategies/__init__.py
 create mode 100644 examples/strategies/kraken_btcusd_research/README.md
 create mode 100644 examples/strategies/kraken_btcusd_research/__init__.py
 create mode 100644 examples/strategies/kraken_btcusd_research/config.py
 create mode 100644 examples/strategies/kraken_btcusd_research/config_v2.py
 create mode 100644 examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py
 create mode 100644 examples/strategies/kraken_btcusd_research/import_15m_catalogs.py
 create mode 100644 examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
 create mode 100644 examples/strategies/kraken_btcusd_research/reports.py
 create mode 100644 examples/strategies/kraken_btcusd_research/run_backtest.py
 create mode 100644 examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py
 create mode 100644 examples/strategies/kraken_btcusd_research/run_v2_research.py
 create mode 100644 examples/strategies/kraken_btcusd_research/strategy.py
 create mode 100644 examples/strategies/kraken_btcusd_research/strategy_v2.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/helpers.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_all.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_basic.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_instrument.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_live_guard.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_real_reports.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py
 create mode 100644 examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py
 create mode 100644 reports/baseline_v1/kraken_2024h1/backtest_summary.json
 create mode 100644 reports/baseline_v1/kraken_2024h1/equity_curve.csv
 create mode 100644 reports/baseline_v1/kraken_2024h1/fills.csv
 create mode 100644 reports/baseline_v1/kraken_2024h1/positions.csv
 create mode 100644 reports/baseline_v1/kraken_2024h1/trades.csv
 create mode 100644 reports/baseline_v1/kraken_2024h2/backtest_summary.json
 create mode 100644 reports/baseline_v1/kraken_2024h2/equity_curve.csv
 create mode 100644 reports/baseline_v1/kraken_2024h2/fills.csv
 create mode 100644 reports/baseline_v1/kraken_2024h2/positions.csv
 create mode 100644 reports/baseline_v1/kraken_2024h2/trades.csv
 create mode 100644 reports/baseline_v1/kraken_2025/backtest_summary.json
 create mode 100644 reports/baseline_v1/kraken_2025/equity_curve.csv
 create mode 100644 reports/baseline_v1/kraken_2025/fills.csv
 create mode 100644 reports/baseline_v1/kraken_2025/positions.csv
 create mode 100644 reports/baseline_v1/kraken_2025/trades.csv
 create mode 100644 reports/baseline_v1/kraken_2026/backtest_summary.json
 create mode 100644 reports/baseline_v1/kraken_2026/equity_curve.csv
 create mode 100644 reports/baseline_v1/kraken_2026/fills.csv
 create mode 100644 reports/baseline_v1/kraken_2026/positions.csv
 create mode 100644 reports/baseline_v1/kraken_2026/trades.csv
 create mode 100644 reports/kraken_2024h1/backtest_summary.json
 create mode 100644 reports/kraken_2024h1/equity_curve.csv
 create mode 100644 reports/kraken_2024h1/fills.csv
 create mode 100644 reports/kraken_2024h1/positions.csv
 create mode 100644 reports/kraken_2024h1/trades.csv
 create mode 100644 reports/kraken_2024h2/backtest_summary.json
 create mode 100644 reports/kraken_2024h2/equity_curve.csv
 create mode 100644 reports/kraken_2024h2/fills.csv
 create mode 100644 reports/kraken_2024h2/positions.csv
 create mode 100644 reports/kraken_2024h2/trades.csv
 create mode 100644 reports/kraken_2025/backtest_summary.json
 create mode 100644 reports/kraken_2025/equity_curve.csv
 create mode 100644 reports/kraken_2025/fills.csv
 create mode 100644 reports/kraken_2025/positions.csv
 create mode 100644 reports/kraken_2025/trades.csv
 create mode 100644 reports/kraken_2026/backtest_summary.json
 create mode 100644 reports/kraken_2026/equity_curve.csv
 create mode 100644 reports/kraken_2026/fills.csv
 create mode 100644 reports/kraken_2026/positions.csv
 create mode 100644 reports/kraken_2026/trades.csv
 create mode 100644 reports/kraken_v2_2026/backtest_summary.json
 create mode 100644 reports/kraken_v2_2026/equity_curve.csv
 create mode 100644 reports/kraken_v2_2026/trades.csv

```
post status: `?? reports/git_branch_flatten_merge_log_20260523_145842.md`

## fork/kraken-v6-market-structure-scanner `4dfaac34abab`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset

## kraken-btcusd-v3-maker-mean-reversion `5f50408c1b43`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset

## feat/hyperliquid-data-observer-v0-clean `745274f6c7f9`

merge --squash stdout:
```
Squash commit -- not updating HEAD

```
stderr:
```
Automatic merge went well; stopped before committing as requested

```
cached stat:
```
 .../hyperliquid_cost_feasibility.py                | 242 ++++++++++++
 .../hyperliquid_observer.py                        | 403 ++++++++++++++++++++
 .../hyperliquid_s3_archive.py                      | 409 +++++++++++++++++++++
 .../run_hyperliquid_cost_feasibility.py            |  43 +++
 .../run_hyperliquid_observer.py                    |  45 +++
 .../run_hyperliquid_s3_archive.py                  |  73 ++++
 .../tests/test_hyperliquid_cost_feasibility.py     |  92 +++++
 .../tests/test_hyperliquid_observer.py             |  43 +++
 .../tests/test_hyperliquid_s3_archive.py           | 247 +++++++++++++
 reports/hyperliquid_cost_probe_phase1.json         |  33 ++
 systemd/nautilus-hyperliquid-observer-v0.service   |  15 +
 11 files changed, 1645 insertions(+)

```
commit:
```
[chore/flatten-fork-branches-preserve-registry faabc847e4] Squash merge feat/hyperliquid-data-observer-v0-clean
 11 files changed, 1645 insertions(+)
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/hyperliquid_observer.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_observer.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_observer.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py
 create mode 100644 reports/hyperliquid_cost_probe_phase1.json
 create mode 100644 systemd/nautilus-hyperliquid-observer-v0.service

```
post status: `?? reports/git_branch_flatten_merge_log_20260523_145842.md`

## fork/feat/hyperliquid-data-observer-v0 `8cbe6ba5137f`

merge --squash stdout:
```
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py', 'examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py', 'examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py', 'examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py']
requires human review: squash conflicts; aborted/reset

## kraken-v6-market-structure-scanner `8e17b09aca36`

merge --squash stdout:
```
ers.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/data_fetcher.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/data_fetcher.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/event_study.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/event_study.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/forward_returns.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/forward_returns.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/observer.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/observer.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/signals.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/signals.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_all.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_all.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tick_store.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tick_store.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/config.py', 'examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py', 'examples/strategies/venue_agnostic_signal_observer/data_adapters.py', 'examples/strategies/venue_agnostic_signal_observer/data_fetcher.py', 'examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/event_study.py', 'examples/strategies/venue_agnostic_signal_observer/forward_returns.py', 'examples/strategies/venue_agnostic_signal_observer/lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/observer.py', 'examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py', 'examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py', 'examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py', 'examples/strategies/venue_agnostic_signal_observer/signals.py', 'examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_all.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py', 'examples/strategies/venue_agnostic_signal_observer/tick_store.py', 'examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py']
requires human review: squash conflicts; aborted/reset

## feat/hyperliquid-funding-archive-backfill `af4fe52086d3`

merge --squash stdout:
```
Squash commit -- not updating HEAD

```
stderr:
```
Automatic merge went well; stopped before committing as requested

```
cached stat:
```
 .../HYPERLIQUID_FUNDING_DIVERGENCE_PHASE0_AUDIT.md | 105 +++++
 .../hyperliquid_funding_archive_backfill.py        | 332 +++++++++++++++
 .../hyperliquid_funding_divergence_phase0.py       | 360 ++++++++++++++++
 .../run_hyperliquid_funding_archive_backfill.py    | 289 +++++++++++++
 .../run_hyperliquid_funding_divergence_phase0.py   | 207 +++++++++
 .../test_hyperliquid_funding_archive_backfill.py   | 470 +++++++++++++++++++++
 .../test_hyperliquid_funding_divergence_phase0.py  | 186 ++++++++
 .../PHASE0_REPORT.md                               |  60 +++
 .../alignment_summary.csv                          |   1 +
 .../calendar_stratification.csv                    |   1 +
 .../divergence_bucket_counts.csv                   |   1 +
 .../divergence_distribution.csv                    |   1 +
 .../manifest.json                                  |  48 +++
 .../persistence_half_life.csv                      |   1 +
 .../summary.json                                   |   5 +
 15 files changed, 2067 insertions(+)

```
commit:
```
[chore/flatten-fork-branches-preserve-registry b5fbd3f987] Squash merge feat/hyperliquid-funding-archive-backfill
 15 files changed, 2067 insertions(+)
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/docs/HYPERLIQUID_FUNDING_DIVERGENCE_PHASE0_AUDIT.md
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py
 create mode 100644 examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/PHASE0_REPORT.md
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/alignment_summary.csv
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/calendar_stratification.csv
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/divergence_bucket_counts.csv
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/divergence_distribution.csv
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/manifest.json
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/persistence_half_life.csv
 create mode 100644 reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/summary.json

```
post status: `?? reports/git_branch_flatten_merge_log_20260523_145842.md`

## fork/feat/hyperliquid-funding-divergence-phase0-rerun `afcfc784dbb2`

merge --squash stdout:
```
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py', 'examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py', 'examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py']
requires human review: squash conflicts; aborted/reset

## fork/kraken-btcusd-v4-1h-trend `b27b98e136fd`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset

## fork/kraken-btcusd-v3-maker-mean-reversion `b5099dfd2e6f`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset

## fork/kraken-v7-l2-maker-paper `c02102ec0be6`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset

## fork/feat/hyperliquid-funding-divergence-phase0 `ed86c69f4195`

merge --squash stdout:
```
Squash commit -- not updating HEAD

```
stderr:
```
Automatic merge went well; stopped before committing as requested

```
cached stat:
```

```
skipped: empty squash

## fork/feat/liquidation-cascade-aftershock-phase0 `fa0dc70a60eb`

merge --squash stdout:
```
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py
Auto-merging examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
CONFLICT (add/add): Merge conflict in examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py', 'examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py', 'examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py', 'examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py']
requires human review: squash conflicts; aborted/reset

## fork/kraken-v5-multiasset-htf-momentum `fd5507964f5e`

merge --squash stdout:
```
Auto-merging examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
Auto-merging examples/strategies/kraken_btcusd_research/reports.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/reports.py
Auto-merging examples/strategies/kraken_btcusd_research/run_backtest.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/run_backtest.py
Auto-merging examples/strategies/kraken_btcusd_research/strategy.py
CONFLICT (add/add): Merge conflict in examples/strategies/kraken_btcusd_research/strategy.py
Squash commit -- not updating HEAD
Automatic merge failed; fix conflicts and then commit the result.

```
stderr:
```

```
conflicts: ['examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py', 'examples/strategies/kraken_btcusd_research/reports.py', 'examples/strategies/kraken_btcusd_research/run_backtest.py', 'examples/strategies/kraken_btcusd_research/strategy.py']
requires human review: squash conflicts; aborted/reset


## Summary

Merged:
- fork/polymarket-btcusd-arb-phase1 1edc29c5c78b
- fork/kraken-btcusd-v2-maker-research 2508cffa1d82
- feat/hyperliquid-data-observer-v0-clean 745274f6c7f9
- feat/hyperliquid-funding-archive-backfill af4fe52086d3

Skipped:
- fork/feat/hyperliquid-funding-divergence-phase0: empty squash

Requires human review:
- fork/fix-derivatives-lead-lag-labeling: squash conflicts: examples/strategies/venue_agnostic_signal_observer/data_adapters.py,examples/strategies/venue_agnostic_signal_observer/data_fetcher.py,examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/event_study.py,examples/strategies/venue_agnostic_signal_observer/forward_returns.py,examples/strategies/venue_agnostic_signal_observer/lead_lag.py,examples/strategies/venue_agnostic_signal_observer/observer.py,examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py,examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py,examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py,examples/strategies/venue_agnostic_signal_observer/signals.py,examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py,examples/strategies/venue_agnostic_signal_observer/tests/test_all.py,examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py,examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py,examples/strategies/venue_agnostic_signal_observer/tick_store.py,examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
- fork/kraken-v6-market-structure-scanner: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
- kraken-btcusd-v3-maker-mean-reversion: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
- fork/feat/hyperliquid-data-observer-v0: squash conflicts: examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py,examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py,examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py,examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py
- kraken-v6-market-structure-scanner: squash conflicts: examples/strategies/kraken_btcusd_research/config.py,examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py,examples/strategies/venue_agnostic_signal_observer/data_adapters.py,examples/strategies/venue_agnostic_signal_observer/data_fetcher.py,examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/event_study.py,examples/strategies/venue_agnostic_signal_observer/forward_returns.py,examples/strategies/venue_agnostic_signal_observer/lead_lag.py,examples/strategies/venue_agnostic_signal_observer/observer.py,examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py,examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py,examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py,examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py,examples/strategies/venue_agnostic_signal_observer/signals.py,examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
- fork/feat/hyperliquid-funding-divergence-phase0-rerun: squash conflicts: examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py,examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py,examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
- fork/kraken-btcusd-v4-1h-trend: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
- fork/kraken-btcusd-v3-maker-mean-reversion: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
- fork/kraken-v7-l2-maker-paper: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
- fork/feat/liquidation-cascade-aftershock-phase0: squash conflicts: examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py,examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py,examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py,examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py
- fork/kraken-v5-multiasset-htf-momentum: squash conflicts: examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py,examples/strategies/kraken_btcusd_research/reports.py,examples/strategies/kraken_btcusd_research/run_backtest.py,examples/strategies/kraken_btcusd_research/strategy.py
