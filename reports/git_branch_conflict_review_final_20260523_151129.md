# Git conflict branch review final 20260523_151129

- Starting SHA: `e300dec3556d`
- Ending SHA: `9537e8f00dc3` (`9537e8f00dc3d163f338d01e471b739b5aaadca1`)
- Consolidation branch: `chore/flatten-fork-branches-preserve-registry`
- Canonical registry: `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`
- Registry line count before: 744
- Registry line count after: 744
- Registry deletion accepted: no
- Review report: `reports/git_branch_conflict_review_20260523_150637.md`

## Branches reviewed

- `fork/fix-derivatives-lead-lag-labeling`
- `fork/kraken-v6-market-structure-scanner`
- `kraken-btcusd-v3-maker-mean-reversion`
- `fork/feat/hyperliquid-data-observer-v0`
- `kraken-v6-market-structure-scanner`
- `fork/feat/hyperliquid-funding-divergence-phase0-rerun`
- `fork/kraken-btcusd-v4-1h-trend`
- `fork/kraken-btcusd-v3-maker-mean-reversion`
- `fork/kraken-v7-l2-maker-paper`
- `fork/feat/liquidation-cascade-aftershock-phase0`
- `fork/kraken-v5-multiasset-htf-momentum`

## Branches harvested

### `fork/feat/hyperliquid-funding-divergence-phase0-rerun`

- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py`

### `fork/feat/liquidation-cascade-aftershock-phase0`

- `examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_CASCADE_AFTERSHOCK_V0_RECONNAISSANCE.md`

## Branches skipped

- `fork/fix-derivatives-lead-lag-labeling` — skipped for code harvest; useful registry variant lines already preserved in canonical appendix; broad historical/stale source not revived
- `fork/kraken-v6-market-structure-scanner` — skipped; obsolete/rejected historical scanner code only
- `kraken-btcusd-v3-maker-mean-reversion` — skipped; obsolete/rejected historical Kraken strategy tests/source only
- `fork/feat/hyperliquid-data-observer-v0` — skipped; already represented by clean branch merged in prior flatten pass
- `kraken-v6-market-structure-scanner` — skipped; broad stale historical research code and registry variant already represented
- `fork/kraken-btcusd-v4-1h-trend` — skipped; obsolete/rejected historical Kraken strategy code only
- `fork/kraken-btcusd-v3-maker-mean-reversion` — skipped; obsolete/rejected historical Kraken strategy tests/source only
- `fork/kraken-v7-l2-maker-paper` — skipped; paper/live-like maker branch remains human-review due execution-adjacent ambiguity
- `fork/kraken-v5-multiasset-htf-momentum` — skipped; stale historical Kraken portfolio strategy code not revived

## Branches still requiring human review

- `fork/kraken-v7-l2-maker-paper` — execution/paper-maker adjacency; do not auto-harvest.
- Other Kraken historical strategy branches only if a human explicitly wants to archive or revive rejected code.

## Files changed since starting SHA

- `examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_CASCADE_AFTERSHOCK_V0_RECONNAISSANCE.md`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py`
- `examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py`
- `reports/git_branch_conflict_review_20260523_150637.md`
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

## Tests run and results

- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_rejected_research_registry_presence.py -q` — PASS, 1 passed.
- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py -q` — PASS, 35 passed.

## Safety scan result

Changed-file grep for `submit_order`, `market_order`, `private_key`, `secret_key`, `api_key`, `wallet`, `TradingNode`, `LiveNode`, and execution-client import text found only explicit negative safety assertions/docstrings/tests. No private key, API key, wallet, live node, trading node, order submission, or execution-client path was added. Raw hits:

```text
examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:8:No wallet. No signing. No orders. No execution.
examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:303:        "api_keys_used": False,
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py:127:        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:157:    assert manifest["api_keys_used"] is False
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:213:        "wallet",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:214:        "private_key",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:228:        r"submit_order",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:231:        r"TradingNode",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py:190:    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
```

## Push status

- Pending at report creation. Intended command: `git push fork chore/flatten-fork-branches-preserve-registry`.
