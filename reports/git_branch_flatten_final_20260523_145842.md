# Git branch flatten final report 20260523_145842

## Starting point

- Starting branch: `stage2-watcher-runtime`
- Starting SHA: `500bb1fd4415`
- Worktree used: `/mnt/nasirjones/py/nautilus_trader_stage2_runtime`
- Target base selected: `fork/develop` @ `224d86507742`
- Consolidation branch: `chore/flatten-fork-branches-preserve-registry`
- Final SHA before final-report commit: `66788c82e7dd`
- Canonical REJECTED_RESEARCH.md path: `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`
- Canonical registry line count: `744 examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md`

## Safety snapshot / backup tags

- Fetched all remotes with `git fetch --all --prune` before consolidation.
- Created local backup tags under `backup/pre-flatten/<branch-name>/<short-sha>` for local and fork refs that may be touched.
- Did not force-push.
- Did not delete branches.
- Did not rewrite remote history.
- Did not run captures or experiments.

## Registry preservation

- Inventory report: `reports/git_branch_flatten_inventory_20260523_145842.md`
- Registry variants export directory: `reports/rejected_research_registry_variants_20260523_145842/`
- Canonical backup: `reports/rejected_research_registry_canonical_20260523_145842.md`
- Synthesized registry: `reports/rejected_research_registry_synthesized_20260523_145842.md`
- Canonical selection reason: target `fork/develop` had no registry; selected largest/current discovered docs registry from `feat/hyperliquid-btc-link-v1-precommitment`, then appended exact-line-missing registry candidates under `CONFLICT_REQUIRES_HUMAN_REVIEW` to preserve content conservatively.
- Registry variants discovered: 47.
- Unique candidate variant sections harvested into synthesized registry: see inventory section `Unique registry content candidates not in canonical` and synthesized registry tail.

## Branches merged

- `fork/polymarket-btcusd-arb-phase1` @ `1edc29c5c78b`
- `fork/kraken-btcusd-v2-maker-research` @ `2508cffa1d82`
- `feat/hyperliquid-data-observer-v0-clean` @ `745274f6c7f9`
- `feat/hyperliquid-funding-archive-backfill` @ `af4fe52086d3`

Each squash commit restored and checked `examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md` after merge.

## Branches skipped

- `fork/feat/hyperliquid-funding-divergence-phase0`: empty squash after prior merged content.
- Branches classified as already ancestor / no diff / runtime-report-only / forbidden branch name / forbidden-path human-review are recorded in the classification and merge log.

## Branches requiring human review

The following were attempted and aborted/reset because squash conflicts touched substantive source files; they were not merged silently:

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

Detailed conflict paths: `reports/git_branch_flatten_merge_log_20260523_145842.md`.

## Regression guard added

- Added `examples/strategies/venue_agnostic_signal_observer/tests/test_rejected_research_registry_presence.py`.
- Guard asserts canonical registry exists, is non-empty, has conservative line-count floor, and contains `REJECTED`, `NEEDS_MORE_DATA`, `locked`, and `funding_dispersion_carry_v1`.

## Tests run

- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_rejected_research_registry_presence.py` — PASS, 1 passed.
- `python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py` — PASS, 34 passed.

## Safety scan result

Command scanned changed files for:
`submit_order|market_order|private_key|secret_key|api_key|wallet|TradingNode|LiveNode|execution client imports`.

Findings were existing/sample strategy submit_order calls in archived Kraken research and explicit negative safety-check strings/tests/docs; no private key material was added. Raw scan:

```text
examples/strategies/kraken_btcusd_research/strategy.py:149:            self.submit_order(exit_order)
examples/strategies/kraken_btcusd_research/strategy.py:155:                self.submit_order(entry_order)
examples/strategies/kraken_btcusd_research/strategy_v2.py:151:            self.submit_order(exit_ord)
examples/strategies/kraken_btcusd_research/strategy_v2.py:210:        self.submit_order(ord)
examples/strategies/polymarket_btcusd_arb/safety_checks.py:4:BANNED_IMPORTS={'PolymarketExecutionClient','PolymarketLiveExecClientFactory','LiveNode','TradingNode','OrderFactory'}; BANNED_ENVS={'POLYMARKET_PK','POLYMARKET_API_KEY','POLYMARKET_API_SECRET','POLYMARKET_FUNDER','POLYMARKET_PASSPHRASE'}
examples/strategies/polymarket_btcusd_arb/safety_checks.py:19:                if isinstance(f,ast.Attribute) and f.attr in {'submit_order','cancel_order'}: violations.append(f'{py}: banned call .{f.attr}')
examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_archive_backfill.py:8:No wallet. No signing. No orders. No execution.
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:149:        "- No wallet",
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_archive_backfill.py:152:        "- No execution client imports",
examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_funding_divergence_phase0.py:127:        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:198:        "wallet",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:199:        "private_key",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:213:        r"submit_order",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py:216:        r"TradingNode",
examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py:169:    forbidden_runtime_terms = ["submit_order", "TradingNode", "ExecutionClient", "BotGate"]
reports/hyperliquid_funding_divergence_phase0/hyperliquid_funding_divergence_phase0_20260523T051240_339388/manifest.json:35:  "no_private_keys": true,

```

## Push status

- Pushed: not yet at time this report was written.
- Required push command: `git push fork chore/flatten-fork-branches-preserve-registry`

Do not merge this consolidation branch into `develop`/`main` without explicit human approval.

## Raw branch classification snapshot

```json
[
  {
    "name": "feat/hyperliquid-btc-link-v1-precommitment",
    "sha": "0636a61f866b",
    "status": "requires_human_review_forbidden_paths",
    "files": 614,
    "source": 361,
    "artifacts": 253
  },
  {
    "name": "fork/polymarket-btc-updown-duration-spread-v1",
    "sha": "09b7375056dd",
    "status": "requires_human_review_forbidden_paths",
    "files": 199,
    "source": 187,
    "artifacts": 12
  },
  {
    "name": "fork/feat/archive-forward-returns-gpu",
    "sha": "0fd808d28d0b",
    "status": "requires_human_review_forbidden_paths",
    "files": 591,
    "source": 339,
    "artifacts": 252
  },
  {
    "name": "fork/polymarket-btcusd-arb-phase2b-observer-campaign",
    "sha": "1901395b4018",
    "status": "requires_human_review_forbidden_paths",
    "files": 45,
    "source": 45,
    "artifacts": 0
  },
  {
    "name": "fork/feat/edge-miner-discovery-freeze-phase1",
    "sha": "1d410c2cf350",
    "status": "requires_human_review_forbidden_paths",
    "files": 629,
    "source": 306,
    "artifacts": 323
  },
  {
    "name": "fork/polymarket-btcusd-arb-phase1",
    "sha": "1edc29c5c78b",
    "status": "candidate",
    "files": 36,
    "source": 36,
    "artifacts": 0
  },
  {
    "name": "feat/funding-oi-crowding-regime-v0",
    "sha": "1f4a8f9b878b",
    "status": "requires_human_review_forbidden_paths",
    "files": 402,
    "source": 174,
    "artifacts": 228
  },
  {
    "name": "fork/docs/edge-miner-phase-plan",
    "sha": "1fef13502abf",
    "status": "requires_human_review_forbidden_paths",
    "files": 630,
    "source": 307,
    "artifacts": 323
  },
  {
    "name": "fork/fix-derivatives-lead-lag-labeling",
    "sha": "226cce57e550",
    "status": "candidate",
    "files": 173,
    "source": 105,
    "artifacts": 68
  },
  {
    "name": "fork/kraken-btcusd-v2-maker-research",
    "sha": "2508cffa1d82",
    "status": "candidate",
    "files": 80,
    "source": 28,
    "artifacts": 52
  },
  {
    "name": "integration/edge-miner-clean-no-runtime-artifacts",
    "sha": "2968407eadd4",
    "status": "requires_human_review_forbidden_paths",
    "files": 417,
    "source": 417,
    "artifacts": 0
  },
  {
    "name": "fork/polymarket-btc-updown-duration-discovery-fix-v2",
    "sha": "2c1e689ccada",
    "status": "requires_human_review_forbidden_paths",
    "files": 220,
    "source": 187,
    "artifacts": 33
  },
  {
    "name": "fork/validator/corpus-recurrence",
    "sha": "2f53dd8a76e5",
    "status": "requires_human_review_forbidden_paths",
    "files": 654,
    "source": 331,
    "artifacts": 323
  },
  {
    "name": "fork/polymarket-btc-updown-spread-regime-v1",
    "sha": "385a319684f1",
    "status": "requires_human_review_forbidden_paths",
    "files": 196,
    "source": 184,
    "artifacts": 12
  },
  {
    "name": "fork/master",
    "sha": "38b912a8b0fe",
    "status": "skip_no_diff",
    "files": 0,
    "source": 0,
    "artifacts": 0
  },
  {
    "name": "fork/feat/edge-miner-offline-discovery-runner",
    "sha": "3ea3018cbbd3",
    "status": "requires_human_review_forbidden_paths",
    "files": 367,
    "source": 163,
    "artifacts": 204
  },
  {
    "name": "feat/hyperliquid-archive-readiness-v0",
    "sha": "40bfdc6185e3",
    "status": "requires_human_review_forbidden_paths",
    "files": 610,
    "source": 357,
    "artifacts": 253
  },
  {
    "name": "fork/validator/estimator-layer",
    "sha": "4130b91daa69",
    "status": "requires_human_review_forbidden_paths",
    "files": 640,
    "source": 317,
    "artifacts": 323
  },
  {
    "name": "fork/report/polymarket-complement-arb-20260516",
    "sha": "47eab56a3ccd",
    "status": "requires_human_review_forbidden_paths",
    "files": 654,
    "source": 279,
    "artifacts": 375
  },
  {
    "name": "fork/feat/hawkes-entropy-btc-link-v0",
    "sha": "4aca45b758ec",
    "status": "requires_human_review_forbidden_paths",
    "files": 513,
    "source": 217,
    "artifacts": 296
  },
  {
    "name": "fork/kraken-v6-market-structure-scanner",
    "sha": "4dfaac34abab",
    "status": "candidate",
    "files": 76,
    "source": 49,
    "artifacts": 27
  },
  {
    "name": "fork/stage2-watcher-runtime",
    "sha": "500bb1fd4415",
    "status": "requires_human_review_forbidden_paths",
    "files": 574,
    "source": 267,
    "artifacts": 307
  },
  {
    "name": "fork/miner/frozen-grid-sweeps",
    "sha": "54ab5eb78e8c",
    "status": "requires_human_review_forbidden_paths",
    "files": 650,
    "source": 327,
    "artifacts": 323
  },
  {
    "name": "fork/polymarket-btcusd-arb-phase2-observer",
    "sha": "5721717b6288",
    "status": "requires_human_review_forbidden_paths",
    "files": 43,
    "source": 43,
    "artifacts": 0
  },
  {
    "name": "kraken-btcusd-v3-maker-mean-reversion",
    "sha": "5f50408c1b43",
    "status": "candidate",
    "files": 41,
    "source": 29,
    "artifacts": 12
  },
  {
    "name": "feat/hyperliquid-data-observer-v0-clean",
    "sha": "745274f6c7f9",
    "status": "candidate",
    "files": 11,
    "source": 10,
    "artifacts": 1
  },
  {
    "name": "fork/feat-dex-cex-spot-dislocation-v1",
    "sha": "756b8b1c5ac9",
    "status": "requires_human_review_forbidden_paths",
    "files": 598,
    "source": 275,
    "artifacts": 323
  },
  {
    "name": "fork/polymarket-btc-updown-1h-near-boundary-fix-v3",
    "sha": "767aa895b4af",
    "status": "requires_human_review_forbidden_paths",
    "files": 222,
    "source": 189,
    "artifacts": 33
  },
  {
    "name": "polymarket-btc-updown-1h-near-boundary-fix-v3",
    "sha": "8ab7b6985f24",
    "status": "requires_human_review_forbidden_paths",
    "files": 778,
    "source": 195,
    "artifacts": 583
  },
  {
    "name": "fork/polymarket-btc-updown-1h-lifecycle-window-fix-v2",
    "sha": "8b481ab845e2",
    "status": "requires_human_review_forbidden_paths",
    "files": 222,
    "source": 189,
    "artifacts": 33
  },
  {
    "name": "fork/cross-asset-beta-lag-stress-v2-worktree",
    "sha": "8c48ce292809",
    "status": "requires_human_review_forbidden_paths",
    "files": 604,
    "source": 281,
    "artifacts": 323
  },
  {
    "name": "fork/feat/hyperliquid-data-observer-v0",
    "sha": "8cbe6ba5137f",
    "status": "candidate",
    "files": 11,
    "source": 10,
    "artifacts": 1
  },
  {
    "name": "kraken-v6-market-structure-scanner",
    "sha": "8e17b09aca36",
    "status": "candidate",
    "files": 173,
    "source": 105,
    "artifacts": 68
  },
  {
    "name": "fork/update-portfolio",
    "sha": "95e9478255d2",
    "status": "requires_human_review_forbidden_paths",
    "files": 7,
    "source": 7,
    "artifacts": 0
  },
  {
    "name": "fork/bot/manifest-ledger-gated-nautilus",
    "sha": "96f13c50e5e1",
    "status": "skip_forbidden_branch_name",
    "files": 7,
    "source": 7,
    "artifacts": 0
  },
  {
    "name": "feat/hyperliquid-data-observer-v0",
    "sha": "a1b0baf4e9ff",
    "status": "requires_human_review_forbidden_paths",
    "files": 602,
    "source": 349,
    "artifacts": 253
  },
  {
    "name": "feat/hyperliquid-paper-execution-v0",
    "sha": "a52ed7c86a3f",
    "status": "skip_forbidden_branch_name",
    "files": 602,
    "source": 349,
    "artifacts": 253
  },
  {
    "name": "chore/flatten-fork-branches-preserve-registry",
    "sha": "a88b7e06defb",
    "status": "already_ancestor_head",
    "files": 602,
    "source": 349,
    "artifacts": 253
  },
  {
    "name": "fork/polymarket-btc-updown-1h-quote-lifecycle-v1",
    "sha": "a94325c59f09",
    "status": "requires_human_review_forbidden_paths",
    "files": 222,
    "source": 189,
    "artifacts": 33
  },
  {
    "name": "fork/feat/cross-asset-beta-lag-archive-v0-run2",
    "sha": "a9af4a103f3b",
    "status": "requires_human_review_forbidden_paths",
    "files": 464,
    "source": 208,
    "artifacts": 256
  },
  {
    "name": "fork/governance/grid-candidate-ledger",
    "sha": "abc9b1e65ab0",
    "status": "skip_forbidden_branch_name",
    "files": 464,
    "source": 208,
    "artifacts": 256
  },
  {
    "name": "feat/hyperliquid-funding-archive-backfill",
    "sha": "af4fe52086d3",
    "status": "candidate",
    "files": 15,
    "source": 7,
    "artifacts": 8
  },
  {
    "name": "fork/feat/hyperliquid-funding-divergence-phase0-rerun",
    "sha": "afcfc784dbb2",
    "status": "candidate",
    "files": 24,
    "source": 8,
    "artifacts": 16
  },
  {
    "name": "fork/kraken-btcusd-v4-1h-trend",
    "sha": "b27b98e136fd",
    "status": "candidate",
    "files": 41,
    "source": 29,
    "artifacts": 12
  },
  {
    "name": "fork/kraken-btcusd-v3-maker-mean-reversion",
    "sha": "b5099dfd2e6f",
    "status": "candidate",
    "files": 41,
    "source": 29,
    "artifacts": 12
  },
  {
    "name": "fork/nightly",
    "sha": "be8a5d136e54",
    "status": "already_ancestor_head",
    "files": 41,
    "source": 29,
    "artifacts": 12
  },
  {
    "name": "fork/kraken-v7-l2-maker-paper",
    "sha": "c02102ec0be6",
    "status": "candidate",
    "files": 88,
    "source": 61,
    "artifacts": 27
  },
  {
    "name": "feat/polymarket-btc-price-target-liquidity-v0",
    "sha": "c3fe2efca4b1",
    "status": "requires_human_review_forbidden_paths",
    "files": 372,
    "source": 168,
    "artifacts": 204
  },
  {
    "name": "fork/polymarket-btcusd-arb-phase2c-observer-analysis",
    "sha": "d49e7945f647",
    "status": "requires_human_review_forbidden_paths",
    "files": 48,
    "source": 48,
    "artifacts": 0
  },
  {
    "name": "fork/shadow/cross-venue-fill-model",
    "sha": "d7e3ef2ea71f",
    "status": "requires_human_review_forbidden_paths",
    "files": 658,
    "source": 335,
    "artifacts": 323
  },
  {
    "name": "feat/funding-falling-oi-unwind-v1",
    "sha": "e37145e52a44",
    "status": "requires_human_review_forbidden_paths",
    "files": 427,
    "source": 180,
    "artifacts": 247
  },
  {
    "name": "fork/feat/hyperliquid-funding-divergence-phase0",
    "sha": "ed86c69f4195",
    "status": "candidate",
    "files": 12,
    "source": 4,
    "artifacts": 8
  },
  {
    "name": "fork/feat/liquidation-cascade-aftershock-phase0",
    "sha": "fa0dc70a60eb",
    "status": "candidate",
    "files": 25,
    "source": 9,
    "artifacts": 16
  },
  {
    "name": "fork/kraken-v5-multiasset-htf-momentum",
    "sha": "fd5507964f5e",
    "status": "candidate",
    "files": 64,
    "source": 40,
    "artifacts": 24
  }
]

```
