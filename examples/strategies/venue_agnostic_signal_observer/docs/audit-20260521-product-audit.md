# Product audit — venue_agnostic_signal_observer — 2026-05-21

Scope: `/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/`

Branch/SHA observed: `feat/cross-asset-beta-lag-archive-v0-run2` at `f003898aa40d`.

Important state note: this audit was run while the tree already had local uncommitted edits from the interrupted Phase 5 cross-asset archive run:

- `examples/strategies/venue_agnostic_signal_observer/run_cross_asset_beta_lag_archive.py`
- `examples/strategies/venue_agnostic_signal_observer/streaming_stress_labels.py`

No fixes are made by this audit report. The report records observed product/code risks and verification evidence.

## Executive summary

This package is a large research scaffold, not one coherent product module. It contains:

- bar-level signal observer code;
- tick-level event-study code;
- cross-venue / derivatives-source capture and replay CLIs;
- offline discovery/freeze/validation/governance/bot-gate scaffolding;
- cross-asset beta-lag archive backfill code;
- Polymarket and funding/OI probes;
- many research-specific reports and registries.

The strongest parts are the explicit research-safety posture, append-only research registry conventions, no-order boundaries in many tests, deterministic discovery grid/candidate hashing, and the existence of isolated tests for core archive and governance paths.

The highest-risk parts are product cohesion and correctness drift:

1. The active archive runner has checkpoint/resume behavior that is materially weaker than the CLI suggests.
2. CPU/GPU and timestamp-shift/return-vector-shift null semantics are easy to conflate.
3. Discovery candidate-lock implementation and tests are currently out of sync.
4. Several safety gates are documented more strictly than they are enforced.
5. The package has many implicit sortedness/alignment/direction assumptions that are not validated at model boundaries.
6. Runner/reporting paths have accumulated duplicated, partial, and unused code, so artifacts are not consistently produced by the canonical report writer.

## Audit method

Tools/evidence used:

- Loaded `product-audit` skill and references.
- Inventoried Python files under the target: 252 Python files, 84 test files.
- Delegated three focused source reads:
  - core models/signal/statistics/GPU modules;
  - runners/data pipeline modules;
  - tests/governance/bot/discovery/shadow/corpus modules.
- Directly read key files:
  - `pyproject.toml` test/lint config;
  - `run_cross_asset_beta_lag_archive.py`;
  - `cross_asset_beta_lag_archive.py`;
  - `streaming_stress_labels.py`;
  - `models.py`;
  - `tick_models.py`;
  - `docs/REJECTED_RESEARCH.md`.
- Ran scoped tests and lint commands listed below.

## Verification evidence

Commands run from repo root:

```bash
PYTHONPATH=. python3.14 -m pytest \
  examples/strategies/venue_agnostic_signal_observer/tests/test_cross_asset_beta_lag_archive.py \
  -q
```

Result:

```text
87 passed in 1.41s
```

```bash
PYTHONPATH=. python3.14 -m pytest \
  examples/strategies/venue_agnostic_signal_observer/tests/test_governance_phase2.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_bot_phase6.py \
  -q
```

Result:

```text
27 passed in 0.14s
```

```bash
PYTHONPATH=. python3.14 -m pytest \
  examples/strategies/venue_agnostic_signal_observer/tests/test_discovery_candidate_lock.py \
  -q
```

Result:

```text
26 failed, 19 passed in 0.53s
```

Primary failure families:

- tests expect error messages containing `unexpected keys`, `missing keys`, and `duplicate cell`, but implementation emits different wording: `extra key(s)`, `missing key(s)`, `duplicate of an earlier cell`.
- tests call `create_candidate_lock(..., discovery_capture_refs=...)`, but implementation signature expects capture paths/refs differently and raises `TypeError: create_candidate_lock() got an unexpected keyword argument 'discovery_capture_refs'`.

Combined scoped run:

```bash
PYTHONPATH=. python3.14 -m pytest \
  examples/strategies/venue_agnostic_signal_observer/tests/test_cross_asset_beta_lag_archive.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_governance_phase2.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_bot_phase6.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_discovery_candidate_lock.py \
  -q
```

Result:

```text
133 passed, 26 failed in 2.87s
```

Scoped lint command:

```bash
ruff check \
  examples/strategies/venue_agnostic_signal_observer/run_cross_asset_beta_lag_archive.py \
  examples/strategies/venue_agnostic_signal_observer/streaming_stress_labels.py \
  examples/strategies/venue_agnostic_signal_observer/discovery/candidate_lock.py \
  examples/strategies/venue_agnostic_signal_observer/bot/gate.py \
  examples/strategies/venue_agnostic_signal_observer/governance/ledger.py \
  --output-format=concise
```

Result:

```text
219 errors
```

Most lint findings are style/debt, but several are product-relevant:

- unused imports in critical modules;
- high complexity: `run_cross_asset_beta_lag_archive.main` complexity 122, `validate_candidate_lock` 17, `BotGate.authorize` 13;
- unused variables in archive runner such as `prefilter_data_reloaded`, `signal_rows`, `computed_median`, `computed_wr`, `reconciliation_result`;
- many module-level import-order/E402 issues from path/import hacks.

## Architecture/product map

### 1. Bar-level observer core

Representative files:

- `models.py`
- `config.py`
- `signals.py`
- `lead_lag.py`
- `forward_returns.py`
- `run_signal_observer.py`

Purpose:

- CSV/bar-level signal events and forward-return summaries.

Key risks:

- Timestamp type is float seconds, while tick-level code uses integer nanoseconds. This is fine only if the boundary is explicit; right now the package mixes both generations of code.
- `SignalEvent.from_dict()` defaults missing `direction` to `long` and missing `signal_type` to `unknown`. That is unsafe for research evidence because malformed records can silently become real long signals.
- Direction validation is weak across the older stack: anything other than `short` is usually treated as long.
- Sortedness and equal-length assumptions for timestamps/prices are pervasive and mostly unchecked.

### 2. Tick-level event-study core

Representative files:

- `tick_models.py`
- `event_study.py`
- `forward_returns_gpu.py`
- `permutation_null.py`
- `permutation_null_gpu.py`
- `trade_flow_impulse.py`
- `cross_asset_impulse.py`

Purpose:

- Nanosecond tick events, forward returns, null/permutation testing, and impulse signal families.

Key risks:

- `TradeTickLite.from_dict()` casts but does not validate positive/finite `price` or `size`.
- `QuoteTickLite.from_dict()` validates bid/ask, but direct dataclass construction bypasses that validation.
- CPU/GPU semantic drift risk:
  - GPU forward-return code filters invalid prices before lookup, which can skip an invalid first eligible tick rather than rejecting it.
  - GPU null code supports return-vector style operations in places where some precommitments require timestamp-shift nulls. These are not interchangeable.
- Multi-GPU wrappers appear to shard sequentially rather than run devices concurrently. Product naming should not imply speedup/parallelism unless actual parallel execution is implemented.

### 3. Cross-asset beta-lag archive v0 pipeline

Representative files:

- `cross_asset_beta_lag_archive.py`
- `run_cross_asset_beta_lag_archive.py`
- `streaming_stress_labels.py`
- `binance_vision_archive.py`
- `convert_archive_zips_to_parquet.py`
- `docs/KLINE_PREFILTER_V1_DESIGN.md`
- `precommitments/cross_asset_beta_lag_archive_v0.json`

Purpose:

- Frozen archive backfill for BTC/ETH spot stress labels -> SOL/LINK/DOGE/AVAX spot forward returns.

Observed implementation behavior:

- `run_cross_asset_beta_lag_archive.py` exposes `--tick-source {binance_vision,parquet}`, `--null-engine {cpu,gpu}`, `--null-device`, `--null-batch-size`, `--resume`, `--seed`, `--null-iterations`.
- Final success path builds its own summary and calls `_write_summary`, not the comprehensive `write_report()` imported from `cross_asset_beta_lag_archive.py`.
- Checkpoint files are written phase-by-phase, but `checkpoint_manifest.json` is written only at the successful end of the full run.

High-risk issues:

#### A. `--resume` is misleading

`--resume` looks for run directories with a valid `checkpoint_manifest.json`. Crashed partial runs with `checkpoint_phase_*.json` but no manifest are not resumable. Even when a manifest exists, much of the pre-Phase-7 path is still re-entered before checkpoint-aware sections.

Impact: long Phase 4/5/7 failures restart from the beginning despite `--resume`. This already happened during the Phase 5 run.

Recommendation: write/update checkpoint manifest after every checkpoint write, make `compute_resume_phase()` capable of using partial phase files, and actually branch around already-completed phases starting at Phase 1, not only from forward returns onward.

#### B. Runtime args are not fully represented in checkpoint identity

The runner accepts `--seed`, `--null-iterations`, `--null-engine`, `--null-device`, and `--null-batch-size`, but checkpoint identity/validation does not strongly bind all effective values. `validate_checkpoint_config()` accepts `git_sha`, `precommitment_sha`, and `null_engine`, but the implementation primarily checks config hash and manifest existence.

Impact: a resumed run can be treated as compatible even when values that define evidence lineage changed.

Recommendation: include every evidence-shaping runtime value in config identity: precommitment hash, git SHA/dirty status policy, seed, null iterations, null engine, null device, null batch size, tick source, tick source dir fingerprint, prefilter version/K, and family constants hash.

#### C. Parquet path diverges from canonical JSONL/binance path

Observed risks from source:

- Parquet mode counts files in some summaries where field names imply tick counts.
- Parquet mode source-date handling uses candidate-date sets and can ignore the download plan's previous-day source context and next-day forward buffer in some paths.
- Parquet stress generation has hardcoded rules rather than iterating the shared `STRESS_RULES` constant.
- Candidate-day records hardcode `source_symbol: BTCUSDT` in places even when ETH caused the candidate.
- Parquet schema is assumed without an upfront hard diagnostic.

Impact: archive evidence can drift between data-source modes.

Recommendation: make `build_stress_day_download_plan()` the single source of required files for both zip/JSONL and parquet; add explicit parquet schema validation; use `STRESS_RULES` only; make candidate-day source attribution real or explicitly `source_symbols_triggered`.

#### D. Final report artifacts are not canonical

`cross_asset_beta_lag_archive.write_report()` can emit comprehensive artifacts including `FINAL_REPORT.md`, `forward_returns.jsonl`, `cell_results.json`, `null_results.json`, `fdr_results.json`, and holdout data. The active runner's final path currently builds a narrower `summary.json` and checkpoint manifest, leaving canonical report writer unused.

Impact: downstream registry updates and human review depend on incomplete or inconsistent artifact sets.

Recommendation: make the active runner call the canonical writer, or delete the unused writer and make the runner's artifact contract explicit and tested.

#### E. Baseline generation currently has methodology and memory pitfalls

During the interrupted Phase 5 run, the old baseline implementation attempted to build a monolithic timestamp->price dict across hundreds of millions of ticks and was killed. A local fix replaced that with sorted arrays and `bisect`, but this is uncommitted and unaudited.

Methodology concern in the current sorted-array baseline helper:

- random timestamps are sampled across a global min/max over targets;
- a random target symbol is selected;
- baseline rows are emitted with `target_symbol="BASELINE"`, not the actual target symbol;
- baseline direction is always long/raw return, not matched to the real cell's bullish/bearish direction mix;
- baseline sample count is `n` per cell but emits up to `n * len(HORIZONS_MS)` rows, then `compute_cell_stats()` aggregates all horizons together unless filtered by the caller.

Impact: baseline delta can be non-comparable with cell stats unless tightly controlled.

Recommendation: baseline should be keyed by the exact cell axes: source symbol, target symbol, horizon, direction, date eligibility, and coverage. Generate one baseline return per real event for the same target/horizon/direction distribution, or explicitly document a different null/baseline definition and freeze it.

#### F. Null engine hard gate needs enforcement

User-level precommitments can require timestamp-shift null. The GPU null path is not a safe drop-in if it implements return-vector-shift semantics. The runner exposes both `--null-engine cpu` and `--null-engine gpu`, and if GPU availability check fails it currently prints a diagnostic and falls back to CPU.

Impact: silent or semi-silent null-method substitution can invalidate a run.

Recommendation: bind null method and engine in the precommitment. If requested engine/method is unavailable, stop with a typed early-stop code rather than substitute. Use names like `timestamp_shift_cpu` and `return_vector_shift_gpu` instead of generic `cpu/gpu` where semantics differ.

### 4. Binance Vision / parquet data layer

Representative file: `binance_vision_archive.py`.

Strengths:

- Timestamp-unit detection exists for ms/µs/ns input.
- Price positivity checks exist in some parsers.
- Cache reuse is tested in the archive test suite.

Risks:

- `_download_with_retry()` / download wrappers conflate transient network errors with not-found conditions.
- Archive availability scan checks sparse first/mid/last dates, then assumes the interior range.
- `append_ticks_jsonl()` is not atomic and can leave partial lines if interrupted.
- `parse_1m_klines_csv()` validates open/close but should also validate high/low before using them in stress-day filters.

Recommendation: hard separate `not_found`, `network_error`, `parse_error`, and `schema_error`; write manifests with per-file status; use atomic temp-file replacement for generated JSONL/parquet outputs.

### 5. Discovery/freeze/governance/bot boundary

Representative files:

- `discovery/search_space.py`
- `discovery/grid_lock.py`
- `discovery/candidate_lock.py`
- `discovery/promotion_boundary.py`
- `governance/events.py`
- `governance/ledger.py`
- `governance/replay.py`
- `bot/manifest.py`
- `bot/gate.py`

Strengths:

- Grid lock and candidate lock concepts are good: freeze search space, pin capture manifest hashes, reject overwrite conflicts, and define a promotion boundary.
- Governance ledger validates JSONL parseability, monotone indices, and event hash integrity.
- Bot gate requires both ledger replay and manifest presence; manifest alone is insufficient.

Critical drift:

- `test_discovery_candidate_lock.py` fails 26 tests against current implementation. Most failures are API/message drift around `discovery_capture_refs` and expected validation messages.

Safety gaps:

- Missing ledger is treated as empty/successful replay. The test name says `missing_ledger_replay_fails_closed`, but implementation/test expectation allows success with zero events. Functionally bot authorization still fails because no candidate is approved, but the ledger-integrity invariant is weaker than the wording.
- `BotGate.authorize()` does not validate manifest hashes.
- `BotGate.authorize()` does not cross-check manifest `grid_hash` against replay/grid state.
- `BotGate.authorize()` comments refer to current estimator evidence, but approval does not require current estimator evidence.
- Grid-scoped kill switch and explicit `LEDGER_INTEGRITY` failure need stronger direct tests.

Recommendation: choose and freeze one of these semantics:

- strict fail-closed: missing ledger is an integrity failure; or
- empty-ledger-safe: missing ledger is allowed but explicitly documented as `NO_LEDGER_EMPTY_STATE`, not fail-closed.

Then update tests and code together. Add manifest-hash and grid-hash validation to bot gate before any order-capable integration exists.

### 6. Shadow/corpus/miner/report registry

Representative files:

- `shadow/shadow_executor.py`
- `corpus/*`
- `miner/*`
- `research_report_miner.py`
- `docs/REJECTED_RESEARCH.md`

Strengths:

- Research registry exists and uses precise verdict language.
- Shadow package is intended as diagnostic/simulation only.
- Corpus/gov loop can emit estimator evidence and demotion events.

Risks:

- Shadow output reports missed/partial rates, but the fill model may not actually emit missed/partial outcomes in all relevant paths.
- Configured fill-model uncertainty can be reported but not necessarily applied to pass/fail when empirical std exists.
- Corpus aggregator should validate homogeneous candidate/grid/corpus identity across captures before producing aggregate evidence.
- Registry is append-only by convention, not enforced by tooling.

Recommendation: add invariant tests for shadow uncertainty application, corpus homogeneous IDs, and registry append-only/tamper behavior.

## Findings by severity

### P0 — Must fix before trusting archive Phase 5 verdict automation

1. **Archive resume/checkpoint contract is false in practice.**
   - Evidence: `--resume` requires final `checkpoint_manifest.json`; partial crashes restart fresh.
   - Fix: checkpoint manifest after every phase and skip already completed phases from Phase 1 onward.

2. **Null method/engine ambiguity can invalidate statistical evidence.**
   - Evidence: precommitment can require timestamp-shift CPU null while GPU module may implement return-vector shift semantics.
   - Fix: make null method explicit and typed; no fallback/substitution unless precommitted.

3. **Baseline generation is not yet a clearly frozen, comparable cell-level baseline.**
   - Evidence: current helper samples global target/time and emits `BASELINE` target, not same target/horizon/direction cell distribution.
   - Fix: define baseline as same-cell random timestamps or update precommitment to the actual baseline semantics.

4. **Final artifacts are incomplete/inconsistent.**
   - Evidence: active runner imports but does not call `write_report()` final path.
   - Fix: one canonical artifact writer, tested.

### P1 — Must fix before promoting discovery/governance/bot scaffold

5. **Discovery candidate-lock tests and implementation are out of sync.**
   - Evidence: 26/45 candidate-lock tests fail.
   - Fix: decide whether API should accept `discovery_capture_refs` or only paths; update code/tests and preserve semantic hash guarantees.

6. **Bot gate does not verify manifest hash/grid hash/current evidence.**
   - Evidence: source read; tests lack tamper/grid mismatch/current evidence coverage.
   - Fix: verify manifest record hash; cross-check grid hash; define estimator-evidence freshness requirement.

7. **Missing ledger semantics contradict fail-closed wording.**
   - Evidence: test name/docstrings vs implementation/test behavior.
   - Fix: freeze semantics and rename tests or change code.

### P2 — Research correctness hardening

8. **Implicit sorted/aligned input assumptions are too broad.**
   - Modules: `signals.py`, `lead_lag.py`, `forward_returns.py`, `forward_returns_gpu.py`, `permutation_null.py`, `streaming_stress_labels.py`.
   - Fix: add cheap monotonicity/length validation at ingestion boundaries and explicit `ASSUME_SORTED` internal helpers after validation.

9. **Direction defaults are unsafe.**
   - Evidence: `SignalEvent.from_dict()` defaults to long; multiple modules treat non-short as long.
   - Fix: validate enum-like fields and reject unknown direction/side.

10. **Data availability errors are under-typed.**
    - Evidence: transient network failures can look like not-found.
    - Fix: typed status in manifests and early-stop codes.

11. **Lint/complexity debt is high in critical runners.**
    - Evidence: 219 lint errors in five scoped files; archive runner `main` complexity 122.
    - Fix: do not broad-format all at once; extract phase functions with typed phase payloads.

### P3 — Product hygiene

12. **Package has too many unrelated experiments in one namespace.**
    - Impact: import hacks, stale tests, and safety scans become hard to reason about.
    - Fix: split long-lived subsystems into subpackages with explicit contracts: `archive_beta_lag`, `discovery`, `governance`, `capture`, `legacy_bar_observer`.

13. **Some tests are string scans rather than AST/integration safety gates.**
    - Fix: use existing discovery AST scanner pattern for bot/shadow/offline runners too.

## Recommended execution order

1. Stop treating the current archive Phase 5 runner as verdict-capable until P0s are fixed.
2. Fix checkpoint/resume and artifact writer first; those are operational correctness blockers.
3. Freeze null method naming and enforce no-substitution semantics.
4. Redesign baseline generation around exact cell comparability.
5. Add regression tests for the three Phase 5 failures observed during the run:
   - missing imported cost constants in runner;
   - missing `math` import in streaming helper;
   - baseline memory blow-up.
6. Reconcile discovery candidate-lock API/tests.
7. Add bot-gate manifest-hash/grid-hash/current-evidence tests before any live/paper execution integration.

## Non-goals / what this audit does not claim

- It does not validate profitability or edge existence.
- It does not certify any archive verdict.
- It does not inspect every line of all 252 Python files.
- It does not approve live trading or paper order routing.
- It does not update `docs/REJECTED_RESEARCH.md`.

## Files changed by this audit

- Created: `examples/strategies/venue_agnostic_signal_observer/docs/audit-20260521-product-audit.md`
