# Consolidation merge final 20260523_151743

- Target branch: `develop`
- Target branch selection: `develop`; fork remote HEAD is `develop`, local `develop` matched `fork/develop` before merge, and this repo uses develop as the active integration branch. Local `develop` was configured to track upstream `origin/develop`, which had diverged, so the safe fast-forward check was performed against writable `fork/develop` instead of rewriting/pulling upstream.
- Starting target SHA: `224d86507742ade00ff454da6dcadadf22c53d88`
- Consolidation SHA merged: `f84a99b9b25f58121850c665270e1f0608ab0586`
- Final target SHA: `074b2b988ee34a45b69871f630c3873a1c5aab9e` (`074b2b988ee3`)
- Target contains consolidation SHA: True
- Backup tag: `backup/pre-consolidation-merge/develop/224d86507742`
- Canonical registry path: `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`
- Registry line count before: missing on target branch before merge; expected consolidation registry count was 744.
- Registry line count after: 744
- Registry deletion/move/empty/shrink accepted: no
- Merge conflicts encountered: none; `git merge --no-ff chore/flatten-fork-branches-preserve-registry` completed with the ort strategy.
- Post-merge cleanup: removed three forbidden-path report artifacts before push: one `manifest.json` under reports and two registry-variant files whose filenames contained `bot__manifest...` and `governance__...`. The merge commit still preserves ancestry to the consolidation branch; the cleanup commit removes those path artifacts from final develop.
- Working tree clean at report creation: True

## Tests run and results

- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_rejected_research_registry_presence.py -q` — PASS, 1 passed.
- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py -q` — PASS, 35 passed.

## Safety scan result

Changed-file path grep after cleanup found no remaining paths matching forbidden live/private/key/order/bot/governance/ledger/manifest/execution path tokens.

Changed-file content grep for forbidden terms produced only archived/sample strategy code and explicit negative safety assertions/docs/tests already documented by the consolidation reports. Raw hits:

```text
examples/strategies/kraken_btcusd_research/README.md:73:export KRAKEN_SPOT_API_KEY="your_key"
examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py:9:   - KRAKEN_API_KEY=*** API key
examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py:16:- Requires KRAKEN_API_KEY and KRAKEN_API_SECRET
examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py:59:    kraken_key = environ.get("KRAKEN_API_KEY", "")
examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py:63:        return GuardResult(safe=False, reason="KRAKEN_API_KEY is not set")
examples/strategies/kraken_btcusd_research/strategy.py:149:            self.submit_order(exit_order)
examples/strategies/kraken_btcusd_research/strategy.py:155:                self.submit_order(entry_order)
examples/strategies/kraken_btcusd_research/strategy_v2.py:151:            self.submit_order(exit_ord)
examples/strategies/kraken_btcusd_research/strategy_v2.py:210:        self.submit_order(ord)
examples/strategies/kraken_btcusd_research/tests/test_live_guard.py:27:    environ = {"KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": "s"}
examples/strategies/kraken_btcusd_research/tests/test_live_guard.py:46:        "KRAKEN_API_KEY": "test-key",
examples/strategies/polymarket_btcusd_arb/safety_checks.py:4:BANNED_IMPORTS={'PolymarketExecutionClient','PolymarketLiveExecClientFactory','LiveNode','TradingNode','OrderFactory'}; BANNED_ENVS={'POLYMARKET_PK','POLYMARKET_API_KEY','POLYMARKET_API_SECRET','POLYMARKET_FUNDER','POLYMARKET_PASSPHRASE'}
examples/strategies/polymarket_btcusd_arb/safety_checks.py:19:                if isinstance(f,ast.Attribute) and f.attr in {'submit_order','cancel_order'}: violations.append(f'{py}: banned call .{f.attr}')
examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:8:No wallet. No signing. No orders. No execution.
examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:303:        "api_keys_used": False,
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:149:        "- No wallet",
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:152:        "- No execution client imports",
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py:127:        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:157:    assert manifest["api_keys_used"] is False
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:213:        "wallet",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:214:        "private_key",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:228:        r"submit_order",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:231:        r"TradingNode",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py:190:    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
reports/git_branch_conflict_review_final_20260523_151129.md:96:Changed-file grep for `submit_order`, `market_order`, `private_key`, `secret_key`, `api_key`, `wallet`, `TradingNode`, `LiveNode`, and execution-client import text found only explicit negative safety assertions/docstrings/tests. No private 
reports/git_branch_conflict_review_final_20260523_151129.md:99:examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:8:No wallet. No signing. No orders. No execution.
reports/git_branch_conflict_review_final_20260523_151129.md:100:examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:303:        "api_keys_used": False,
reports/git_branch_conflict_review_final_20260523_151129.md:101:examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py:127:        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
reports/git_branch_conflict_review_final_20260523_151129.md:102:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:157:    assert manifest["api_keys_used"] is False
reports/git_branch_conflict_review_final_20260523_151129.md:103:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:213:        "wallet",
reports/git_branch_conflict_review_final_20260523_151129.md:104:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:214:        "private_key",
reports/git_branch_conflict_review_final_20260523_151129.md:105:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:228:        r"submit_order",
reports/git_branch_conflict_review_final_20260523_151129.md:106:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:231:        r"TradingNode",
reports/git_branch_conflict_review_final_20260523_151129.md:107:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py:190:    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
reports/git_branch_flatten_final_20260523_145842.md:78:`submit_order|market_order|private_key|secret_key|api_key|wallet|TradingNode|LiveNode|execution client imports`.
reports/git_branch_flatten_final_20260523_145842.md:80:Findings were existing/sample strategy submit_order calls in archived Kraken research and explicit negative safety-check strings/tests/docs; no private key material was added. Raw scan:
reports/git_branch_flatten_final_20260523_145842.md:83:examples/strategies/kraken_btcusd_research/strategy.py:149:            self.submit_order(exit_order)
reports/git_branch_flatten_final_20260523_145842.md:84:examples/strategies/kraken_btcusd_research/strategy.py:155:                self.submit_order(entry_order)
reports/git_branch_flatten_final_20260523_145842.md:85:examples/strategies/kraken_btcusd_research/strategy_v2.py:151:            self.submit_order(exit_ord)
reports/git_branch_flatten_final_20260523_145842.md:86:examples/strategies/kraken_btcusd_research/strategy_v2.py:210:        self.submit_order(ord)
reports/git_branch_flatten_final_20260523_145842.md:87:examples/strategies/polymarket_btcusd_arb/safety_checks.py:4:BANNED_IMPORTS={'PolymarketExecutionClient','PolymarketLiveExecClientFactory','LiveNode','TradingNode','OrderFactory'}; BANNED_ENVS={'POLYMARKET_PK','POLYMARKET_API_KEY','POLYMARK
reports/git_branch_flatten_final_20260523_145842.md:88:examples/strategies/polymarket_btcusd_arb/safety_checks.py:19:                if isinstance(f,ast.Attribute) and f.attr in {'submit_order','cancel_order'}: violations.append(f'{py}: banned call .{f.attr}')
reports/git_branch_flatten_final_20260523_145842.md:89:examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:8:No wallet. No signing. No orders. No execution.
reports/git_branch_flatten_final_20260523_145842.md:90:examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:149:        "- No wallet",
reports/git_branch_flatten_final_20260523_145842.md:91:examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:152:        "- No execution client imports",
reports/git_branch_flatten_final_20260523_145842.md:92:examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py:127:        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
reports/git_branch_flatten_final_20260523_145842.md:93:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:198:        "wallet",
reports/git_branch_flatten_final_20260523_145842.md:94:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:199:        "private_key",
reports/git_branch_flatten_final_20260523_145842.md:95:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:213:        r"submit_order",
reports/git_branch_flatten_final_20260523_145842.md:96:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:216:        r"TradingNode",
reports/git_branch_flatten_final_20260523_145842.md:97:examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py:169:    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
reports/git_branch_flatten_final_20260523_145842.md:98:reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/manifest.json:35:  "no_private_keys": true,
```

## Files changed versus starting target SHA

- `data/kraken/BTCUSD_15m_2024h1.csv`
- `data/kraken/BTCUSD_15m_2024h2.csv`
- `data/kraken/BTCUSD_15m_2025.csv`
- `data/kraken/BTCUSD_15m_2026.csv`
- `data/kraken/BTCUSD_5m.csv`
- `data/kraken/BTCUSD_5m_2024h1.csv`
- `data/kraken/BTCUSD_5m_2024h2.csv`
- `data/kraken/BTCUSD_5m_2025.csv`
- `data/kraken/BTCUSD_5m_2026.csv`
- `examples/__init__.py`
- `examples/strategies/__init__.py`
- `examples/strategies/kraken_btcusd_research/README.md`
- `examples/strategies/kraken_btcusd_research/__init__.py`
- `examples/strategies/kraken_btcusd_research/config.py`
- `examples/strategies/kraken_btcusd_research/config_v2.py`
- `examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py`
- `examples/strategies/kraken_btcusd_research/import_15m_catalogs.py`
- `examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py`
- `examples/strategies/kraken_btcusd_research/reports.py`
- `examples/strategies/kraken_btcusd_research/run_backtest.py`
- `examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py`
- `examples/strategies/kraken_btcusd_research/run_v2_research.py`
- `examples/strategies/kraken_btcusd_research/strategy.py`
- `examples/strategies/kraken_btcusd_research/strategy_v2.py`
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
- `examples/strategies/polymarket_btcusd_arb/README.md`
- `examples/strategies/polymarket_btcusd_arb/STATUS.md`
- `examples/strategies/polymarket_btcusd_arb/__init__.py`
- `examples/strategies/polymarket_btcusd_arb/baseline.py`
- `examples/strategies/polymarket_btcusd_arb/binance_data.py`
- `examples/strategies/polymarket_btcusd_arb/config.py`
- `examples/strategies/polymarket_btcusd_arb/data_cache.py`
- `examples/strategies/polymarket_btcusd_arb/empirical_model.py`
- `examples/strategies/polymarket_btcusd_arb/fair_probability.py`
- `examples/strategies/polymarket_btcusd_arb/forward_returns.py`
- `examples/strategies/polymarket_btcusd_arb/gates.py`
- `examples/strategies/polymarket_btcusd_arb/models.py`
- `examples/strategies/polymarket_btcusd_arb/observer_strategy.py`
- `examples/strategies/polymarket_btcusd_arb/parity_tests/__init__.py`
- `examples/strategies/polymarket_btcusd_arb/parity_tests/fixtures/probability_cases.json`
- `examples/strategies/polymarket_btcusd_arb/parity_tests/fixtures/settlement_cases.json`
- `examples/strategies/polymarket_btcusd_arb/parity_tests/test_probability_parity.py`
- `examples/strategies/polymarket_btcusd_arb/parity_tests/test_settlement_parity.py`
- `examples/strategies/polymarket_btcusd_arb/polymarket_data.py`
- `examples/strategies/polymarket_btcusd_arb/reports.py`
- `examples/strategies/polymarket_btcusd_arb/run_backtest.py`
- `examples/strategies/polymarket_btcusd_arb/safety_checks.py`
- `examples/strategies/polymarket_btcusd_arb/settlement_predictor.py`
- `examples/strategies/polymarket_btcusd_arb/signal_generator.py`
- `examples/strategies/polymarket_btcusd_arb/tests/__init__.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_backtest_smoke.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_baseline.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_binance_data.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_config.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_data_cache.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_forward_returns.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_gates.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_polymarket_data.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_reports.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_safety_checks.py`
- `examples/strategies/polymarket_btcusd_arb/tests/test_signal_generator.py`
- `examples/strategies/venue_agnostic_signal_observer/docs/HYPERLIQUID_FUNDING_DIVERGENCE_PHASE0_AUDIT.md`
- `examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_CASCADE_AFTERSHOCK_V0_RECONNAISSANCE.md`
- `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_observer.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_s3_archive.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_cost_feasibility.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_observer.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_s3_archive.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_cost_feasibility.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_observer.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_s3_archive.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_rejected_research_registry_presence.py`
- `reports/baseline_v1/kraken_2024h1/backtest_summary.json`
- `reports/baseline_v1/kraken_2024h1/equity_curve.csv`
- `reports/baseline_v1/kraken_2024h1/fills.csv`
- `reports/baseline_v1/kraken_2024h1/positions.csv`
- `reports/baseline_v1/kraken_2024h1/trades.csv`
- `reports/baseline_v1/kraken_2024h2/backtest_summary.json`
- `reports/baseline_v1/kraken_2024h2/equity_curve.csv`
- `reports/baseline_v1/kraken_2024h2/fills.csv`
- `reports/baseline_v1/kraken_2024h2/positions.csv`
- `reports/baseline_v1/kraken_2024h2/trades.csv`
- `reports/baseline_v1/kraken_2025/backtest_summary.json`
- `reports/baseline_v1/kraken_2025/equity_curve.csv`
- `reports/baseline_v1/kraken_2025/fills.csv`
- `reports/baseline_v1/kraken_2025/positions.csv`
- `reports/baseline_v1/kraken_2025/trades.csv`
- `reports/baseline_v1/kraken_2026/backtest_summary.json`
- `reports/baseline_v1/kraken_2026/equity_curve.csv`
- `reports/baseline_v1/kraken_2026/fills.csv`
- `reports/baseline_v1/kraken_2026/positions.csv`
- `reports/baseline_v1/kraken_2026/trades.csv`
- `reports/git_branch_conflict_review_20260523_150637.md`
- `reports/git_branch_conflict_review_final_20260523_151129.md`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-data-observer-v0.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-data-observer-v0.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-funding-divergence-phase0-rerun.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__hyperliquid-funding-divergence-phase0-rerun.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__liquidation-cascade-aftershock-phase0.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__feat__liquidation-cascade-aftershock-phase0.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__fix-derivatives-lead-lag-labeling.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__fix-derivatives-lead-lag-labeling.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v3-maker-mean-reversion.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v3-maker-mean-reversion.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v4-1h-trend.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-btcusd-v4-1h-trend.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v5-multiasset-htf-momentum.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v5-multiasset-htf-momentum.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v6-market-structure-scanner.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v6-market-structure-scanner.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v7-l2-maker-paper.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/fork__kraken-v7-l2-maker-paper.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/kraken-btcusd-v3-maker-mean-reversion.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/kraken-btcusd-v3-maker-mean-reversion.stat.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/kraken-v6-market-structure-scanner.name-status.txt`
- `reports/git_branch_conflict_review_raw_20260523_150637/kraken-v6-market-structure-scanner.stat.txt`
- `reports/git_branch_flatten_final_20260523_145842.md`
- `reports/git_branch_flatten_inventory_20260523_145842.md`
- `reports/git_branch_flatten_merge_log_20260523_145842.md`
- `reports/hyperliquid_cost_probe_phase1.json`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/PHASE0_REPORT.md`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/alignment_summary.csv`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/calendar_stratification.csv`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/divergence_bucket_counts.csv`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/divergence_distribution.csv`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/persistence_half_life.csv`
- `reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/summary.json`
- `reports/kraken_2024h1/backtest_summary.json`
- `reports/kraken_2024h1/equity_curve.csv`
- `reports/kraken_2024h1/fills.csv`
- `reports/kraken_2024h1/positions.csv`
- `reports/kraken_2024h1/trades.csv`
- `reports/kraken_2024h2/backtest_summary.json`
- `reports/kraken_2024h2/equity_curve.csv`
- `reports/kraken_2024h2/fills.csv`
- `reports/kraken_2024h2/positions.csv`
- `reports/kraken_2024h2/trades.csv`
- `reports/kraken_2025/backtest_summary.json`
- `reports/kraken_2025/equity_curve.csv`
- `reports/kraken_2025/fills.csv`
- `reports/kraken_2025/positions.csv`
- `reports/kraken_2025/trades.csv`
- `reports/kraken_2026/backtest_summary.json`
- `reports/kraken_2026/equity_curve.csv`
- `reports/kraken_2026/fills.csv`
- `reports/kraken_2026/positions.csv`
- `reports/kraken_2026/trades.csv`
- `reports/kraken_v2_2026/backtest_summary.json`
- `reports/kraken_v2_2026/equity_curve.csv`
- `reports/kraken_v2_2026/trades.csv`
- `reports/rejected_research_registry_canonical_20260523_145842.md`
- `reports/rejected_research_registry_synthesized_20260523_145842.md`
- `reports/rejected_research_registry_variants_20260523_145842/audit__edge-miner-six-phase-verification__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/backup__develop-edge-miner-before-artifact-cleanup__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/backup__hyperliquid-phase0-local-develop__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/cross-asset-beta-lag-stress-v2-worktree__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/develop__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/docs__edge-miner-phase-plan__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat-dex-cex-spot-dislocation-v1__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__edge-miner-discovery-freeze-phase1__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__edge-miner-offline-discovery-runner__examples__strategies__venue_agnostic_signal_observer__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__funding-falling-oi-unwind-v1__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__funding-oi-crowding-regime-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__hyperliquid-archive-readiness-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__hyperliquid-btc-link-v1-precommitment__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__hyperliquid-data-observer-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__hyperliquid-funding-divergence-phase0-rerun__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__liquidation-cascade-aftershock-phase0__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/feat__polymarket-btc-price-target-liquidity-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fix-derivatives-lead-lag-labeling__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__bot__manifest-ledger-gated-nautilus__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__cross-asset-beta-lag-stress-v2-worktree__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__docs__edge-miner-phase-plan__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat-dex-cex-spot-dislocation-v1__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__archive-forward-returns-gpu__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__cross-asset-beta-lag-archive-v0-run2__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__edge-miner-discovery-freeze-phase1__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__edge-miner-offline-discovery-runner__examples__strategies__venue_agnostic_signal_observer__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__hawkes-entropy-btc-link-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__hyperliquid-funding-divergence-phase0-rerun__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__feat__liquidation-cascade-aftershock-phase0__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__fix-derivatives-lead-lag-labeling__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__governance__grid-candidate-ledger__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__miner__frozen-grid-sweeps__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__report__polymarket-complement-arb-20260516__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__shadow__cross-venue-fill-model__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__stage2-watcher-runtime__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__validator__corpus-recurrence__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/fork__validator__estimator-layer__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/integration__edge-miner-clean-no-runtime-artifacts__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/kraken-v6-market-structure-scanner__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/miner__frozen-grid-sweeps__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/report__polymarket-complement-arb-20260516__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/shadow__cross-venue-fill-model__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/stage2-watcher-runtime__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/validator__corpus-recurrence__examples__strategies__REJECTED_RESEARCH.md.md`
- `reports/rejected_research_registry_variants_20260523_145842/validator__estimator-layer__examples__strategies__REJECTED_RESEARCH.md.md`
- `systemd/nautilus-hyperliquid-observer-v0.service`

## Push status

- Pushed: yes. Command used: `git push fork develop`.
