# feat/auto-paper-promotion-v0 — Auto-Paper Promotion Orchestration

## Purpose

Automatically promote research hypotheses that pass all precommitted gates through to
paper (simulated) execution within the venue-agnostic signal observer. The auto-paper
layer is the bridge between the conductor/evaluator layer and the paper execution layer.
It does not execute trades, place orders, or connect to brokers. Paper execution is
simulated via NautilusTrader's backtest/replay engine—no order routing, no live
credentials, no exchange API calls.

Once promoted, a paper strategy is automatically periodically re-falsified against fresh
evidence. If the evidence no longer supports the original promotion, the strategy is
disabled. This prevents the paper layer from becoming a one-way optimism machine.

---

## Architecture

```text
  Conductor locked run
       ↓ (precommitment hash)
  Gate verifier (frozen switch + 5-gate check)
       ↓ allowed
  Auto-promoter → writes PAPER_STRATEGY_PROMOTED ledger event
       ↓
  Paper strategy added to registry
       ↓
  Refalsification cycle (periodic, fail-closed)
       ↓
  Disabled strategy stays in registry (not deleted)
```

---

## Files to create

```text
examples/strategies/venue_agnostic_signal_observer/paper/__init__.py
examples/strategies/venue_agnostic_signal_observer/paper/models.py
examples/strategies/venue_agnostic_signal_observer/paper/registry.py
examples/strategies/venue_agnostic_signal_observer/paper/gate_verifier.py
examples/strategies/venue_agnostic_signal_observer/paper/auto_promotion.py
examples/strategies/venue_agnostic_signal_observer/paper/refalsification.py
examples/strategies/venue_agnostic_signal_observer/run_paper_promotion.py
examples/strategies/venue_agnostic_signal_observer/run_paper_refalsification.py
examples/strategies/venue_agnostic_signal_observer/paper_dashboard/__init__.py
examples/strategies/venue_agnostic_signal_observer/paper_dashboard/server.py
examples/strategies/venue_agnostic_signal_observer/paper_dashboard/templates/index.html
examples/strategies/venue_agnostic_signal_observer/paper_dashboard/templates/strategy_detail.html

examples/strategies/venue_agnostic_signal_observer/tests/test_paper_models.py
examples/strategies/venue_agnostic_signal_observer/tests/test_paper_registry.py
examples/strategies/venue_agnostic_signal_observer/tests/test_paper_gate_verifier.py
examples/strategies/venue_agnostic_signal_observer/tests/test_paper_auto_promotion.py
examples/strategies/venue_agnostic_signal_observer/tests/test_paper_refalsification.py
examples/strategies/venue_agnostic_signal_observer/tests/test_run_paper_promotion.py
examples/strategies/venue_agnostic_signal_observer/tests/test_run_paper_refalsification.py
examples/strategies/venue_agnostic_signal_observer/tests/test_paper_dashboard.py
```

Do not modify files outside this set unless absolutely required.

---

## `paper/models.py`

### `PaperExecutionMode`

```python
class PaperExecutionMode(Enum):
    NAUTILUS_BACKTEST_SIMULATED = auto()
```

v0 is single-mode. Future modes may include NAUTILUS_PAPER_LIVE or other execution
backends.

### `PaperStrategySpec`

```python
@dataclass(frozen=True)
class PaperStrategySpec:
    strategy_id: str
    signal_family: str
    study_id: str
    precommitment_hash: str
    precommitment_path: Path
    promotion_rule_id: str
    execution_mode: PaperExecutionMode
    group_id: str
    mean_net_bps: float
    valid_count: int
    win_rate: float | None
    cost_floor_bps: float
    min_events: int
    source_venue: str | None
    target_venue: str | None
    source_symbol: str | None
    target_symbol: str | None
    command: tuple[str, ...]
    capture_dir: str | None
    artifacts_dir: Path
    output_dir: Path
    promoter_verdict: str
    promoted_at_utc: str
    last_refalsified_utc: str | None
    refalsification_status: str | None
    state: PaperStrategyState
    metadata: Mapping[str, Any]
```

### `PaperStrategyState`

```python
class PaperStrategyState(Enum):
    PENDING = auto()
    ENABLED = auto()
    DISABLED = auto()
    KILLED = auto()
    EXPIRED = auto()
```

### Validation

`__post_init__` rules:

- `strategy_id` cannot be empty.
- `signal_family` and `study_id` must be lowercase snake_case (same regex as conductor).
- `precommitment_hash` cannot be empty.
- `execution_mode` must equal `PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED`.
- `promotion_rule_id` cannot be empty.
- `command` cannot be empty.
- `artifacts_dir` and `output_dir` must be non-empty paths.
- `promoted_at_utc` must be a valid ISO 8601 datetime or empty for PENDING state.
- `cost_floor_bps >= 0`.
- `min_events >= 1`.
- If `state == ENABLED`, `promoted_at_utc` must be non-empty.

## `paper/registry.py`

File-based registry in a directory.

Each strategy stored as:

```text
<registry_dir>/<strategy_id>.json
```

Implement:

```python
def save_strategy(strategy: PaperStrategySpec, registry_dir: Path) -> None: ...
def load_strategy(strategy_id: str, registry_dir: Path) -> PaperStrategySpec | None: ...
def load_all_strategies(registry_dir: Path) -> list[PaperStrategySpec]: ...
def update_strategy_state(strategy_id: str, new_state: PaperStrategyState, registry_dir: Path) -> PaperStrategySpec | None: ...
def delete_strategy(strategy_id: str, registry_dir: Path) -> bool: ...
```

Use `atomic_io.write_json_atomic` from the conductor package.

Rules:

- `save_strategy` uses atomic write.
- `delete_strategy` removes file if it exists.
- `load_all_strategies` skips non-JSON files gracefully.
- No in-memory caching in v0.
- Tests use temp directories.

## `paper/gate_verifier.py`

The gate verifier checks a precommitment hash against the frozen gate and the
five promotion gates. It is side-effect-light and does not write ledger events
or precommitment files.

### `FrozenGateDecision`

```python
@dataclass(frozen=True)
class FrozenGateDecision:
    allowed: bool
    error: str | None
    metadata: Mapping[str, Any]
```

### Frozen switch check

```python
def check_promotion_frozen(precommitment_hash: str, precommitment_dir: Path) -> FrozenGateDecision: ...
```

Behavior:

- Read the precommitment file: `<precommitment_dir>/<precommitment_hash>.json`.
- If file missing → `FAILED_MISSING_PRECOMMITMENT`.
- If `precommitment_payload["ledger_write_authorized_before_locked_run"] == False` → `FAILED_EVIDENCE_GAP`.
- If `precommitment_payload["registry_verdict_authorized"] == False` → `FAILED_VERDICT_NOT_AUTHORIZED`.
- If `registry_verdict_authorized == True` AND `ledger_write_authorized_before_locked_run == True` → `PROMOTION_FROZEN`.

The `PROMOTION_FROZEN` status means the precommitment has survived a locked run
and locked-run ledger event was written. It is now frozen for promotion review.

If `PROMOTION_FROZEN` is not achieved, the verifier returns `FAILED_*` immediately.
**Verifier must not read artifacts. Verifier must not write precommitments.
Verifier must not write ledger events.**

### `GateResult`

```python
@dataclass(frozen=True)
class GateResult:
    gate_id: str
    passed: bool
    detail: str
    evidence_path: Path | None
```

### Five promotion gates

Return `list[GateResult]`:

```python
def verify_promotion_gates(
    precommitment_hash: str,
    ledger_path: Path,
    artifacts_dir: Path,
) -> list[GateResult]:
```

Gates:

1. **`ledger_locked_run_completed`** — verify at least one locked-run ledger event
   exists in `evidence_ledger.jsonl` for this precommitment hash.
2. **`artifact_exists`** — verify the artifacts directory (output_dir from the
   locked run) contains `summary.json` with a `conductor_result.json`.
3. **`group_survives_in_locked_run`** — verify that the promoted group from the
   precommitment payload is still present in the locked-run `summary.json` and
   still has positive `mean_net_bps`.
4. **`null_not_candidate_for_rejection`** (*optional light check*) — if the
   artifacts directory has a `null_test_results.json` or similar, verify the
   promoted group was not rejected by null testing. If no null results exist,
   this gate passes with a `SKIPPED_NO_NULL_DATA` detail.
5. **`no_stage2_rejection`** — if the artifacts directory has a
   `stage2_check_results.json` or FDR results, verify the promoted group was not
   blocked by FDR/holdout. If no stage 2 data exists, this gate passes with
   `SKIPPED_NO_STAGE2_DATA`.

Gate 3 must parse the locked-run summary JSON and locate the group by `group_id`.

If gate 3 fails (group missing or negative), return the result with
`passed=False`.

### Verifier role boundary

The verifier returns `FrozenGateDecision` (from frozen switch) and
`list[GateResult]` (from the five promotion gates). The verifier does not
aggregate them into a final verdict. That is the caller's responsibility.

**Verifier must not write `AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH` or
any other ledger event.** The verifier stays side-effect-light and does not
leak gate state. The auto-promotion module owns audit ledger events.

## `paper/auto_promotion.py`

### `PromotionDecision`

```python
@dataclass(frozen=True)
class PromotionDecision:
    strategy_id: str
    allowed: bool
    reason: str
    frozen_gate: FrozenGateDecision
    gate_results: list[GateResult]
    strategy_spec: PaperStrategySpec | None
    event_hash: str | None
```

### `evaluate_promotion`

```python
def evaluate_promotion(
    precommitment_hash: str,
    precommitment_dir: Path,
    registry_dir: Path,
    paper_events_ledger_path: Path,
    artifacts_base_dir: Path,
    evidence_ledger_path: Path,
) -> PromotionDecision:
```

Behavior:

1. Derive `strategy_id` from precommitment hash:
   `strategy_id = f"paper_{precommitment_hash[:12]}"`.
2. Check if strategy already exists in registry (skip if already ENABLED/KILLED/EXPIRED).
3. Check frozen switch via `check_promotion_frozen`.
4. If `PROMOTION_FROZEN` not achieved → return `PromotionDecision(allowed=False, ...)`.
   Write `AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH` event to paper events ledger.
5. Run five promotion gates.
6. If all five gates pass → construct `PaperStrategySpec`, save to registry,
   write `PAPER_STRATEGY_PROMOTED` event, return `PromotionDecision(allowed=True)`.
7. If any gate fails → return `PromotionDecision(allowed=False, ...)`.
   Write `PAPER_PROMOTION_BLOCKED_BY_GATE` event.
8. Return full decision with evidence.
9. **Do not write `REJECTED_RESEARCH.md`.**
10. **Do not execute trades.**
11. **Do not import execution clients.**

### Promotion ledger events

Written to `paper_events_ledger_path` (JSONL).

Event types:

```text
PAPER_STRATEGY_PROMOTED
AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH
PAPER_PROMOTION_BLOCKED_BY_GATE
```

Each event contains:
```text
event_type
strategy_id
precommitment_hash
reason
gate_results (summary, not full payload)
created_at_utc
event_hash
```

Compute `event_hash` with `atomic_io.sha256_canonical_json` over payload without
`event_hash`.

## `paper/refalsification.py`

The auto-paper system must not only promote strategies in. It must also
automatically demote/disable paper strategies when fresh evidence fails.

### `RefalsificationConfig`

```python
@dataclass(frozen=True)
class RefalsificationConfig:
    registry_dir: Path
    ledger_path: Path
    pnl_ledger_path: Path
    artifacts_root: Path
    min_age_hours_before_refalsification: int
    refalsification_interval_hours: int
    disable_on_gate_failure: bool
    disable_on_cost_wall: bool
    disable_on_negative_cross_capture_median: bool
    disable_on_daily_loss_killed: bool
```

### `RefalsificationDecision`

```python
@dataclass(frozen=True)
class RefalsificationDecision:
    strategy_id: str
    should_disable: bool
    reason: str
    gate_results: Mapping[str, bool]
    evidence: Mapping[str, Any]
    event_hash: str | None
```

### `refalsify_strategy`

```python
def refalsify_strategy(
    *,
    strategy: PaperStrategySpec,
    config: RefalsificationConfig,
    now_utc: datetime,
) -> RefalsificationDecision:
```

Behavior:

- Load null, FDR, holdout, and cross-capture artifacts for the strategy's
  signal family / group from `config.artifacts_root`.
- Re-run the original promotion gates against current data.
- If any original promotion gate now fails → `should_disable=True`.
- If cross-capture median net bps ≤ `cost_floor_bps` → `should_disable=True`.
- If cross-capture median net bps negative → `should_disable=True`.
- If strategy is daily-loss killed and `disable_on_daily_loss_killed=True` →
  `should_disable=True`.
- Fail closed: if a required artifact is missing, `should_disable=True`.
- Compute `event_hash` with canonical SHA256.
- Do not delete strategy files.
- Do not delete PnL ledger entries.
- Do not mutate original precommitment files.
- Do not write `REJECTED_RESEARCH.md`.
- Do not write `TRADE_READY`, `EXECUTION_READY`, or `LIVE_READY`.

### `run_refalsification_once`

```python
def run_refalsification_once(
    config: RefalsificationConfig,
    now_utc: datetime | None = None,
) -> list[RefalsificationDecision]:
```

Behavior:

- Load all registered strategies via `registry.load_all_strategies`.
- Ignore strategies with state `DISABLED`, `KILLED`, or `EXPIRED`.
- Ignore strategies younger than `config.min_age_hours_before_refalsification`
  (compare `promoted_at_utc` against `now_utc`).
- Ignore strategies already checked within `refalsification_interval_hours`
  (compare `last_refalsified_utc` against `now_utc`).
- For each eligible strategy, call `refalsify_strategy`.
- If `should_disable=True` (and not dry-run), set strategy state to `DISABLED`
  via `registry.update_strategy_state`.
- Append `PAPER_STRATEGY_DISABLED_REFALSIFICATION` event to the paper events
  ledger.

- If no strategy is eligible (all too young, too recent, or not in ENABLED state),
  return empty list.

### Refalsification ledger event

Event type:

```text
PAPER_STRATEGY_DISABLED_REFALSIFICATION
```

Fields:

```text
event_type
strategy_id
paper_precommitment_hash
reason
gate_results
evidence
created_at_utc
event_hash
```

---

## `paper/__init__.py`

```python
__all__ = [
    "PaperExecutionMode",
    "PaperStrategySpec",
    "PaperStrategyState",
    "PaperStrategySpec",
    "save_strategy",
    "load_strategy",
    "load_all_strategies",
    "update_strategy_state",
    "delete_strategy",
    "check_promotion_frozen",
    "verify_promotion_gates",
    "evaluate_promotion",
    "PromotionDecision",
    "RefalsificationConfig",
    "RefalsificationDecision",
    "refalsify_strategy",
    "run_refalsification_once",
]
```

---

## `run_paper_promotion.py`

CLI for promoting a single precommitment:

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_paper_promotion \
  --precommitment-hash <hash> \
  --precommitment-dir reports/conductor/precommitments \
  --registry-dir reports/paper/registry \
  --paper-events-ledger reports/paper/paper_events.jsonl \
  --evidence-ledger reports/evidence_ledger.jsonl \
  --artifacts-base-dir reports \
  [--dry-run]
```

`--dry-run` evaluates all gates and reports the decision without writing to
registry or ledger.

Rules:

- No network calls.
- No Nautilus import required.
- No trade placement.

---

## `run_paper_refalsification.py`

CLI for periodic re-falsification:

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_paper_refalsification \
  --registry-dir reports/paper/registry \
  --ledger-path reports/paper/paper_events.jsonl \
  --pnl-ledger reports/paper/pnl_ledger.jsonl \
  --artifacts-root reports \
  [--once] \
  [--dry-run]
```

`--once` performs one refalsification pass.
`--dry-run` reports decisions but does not update registry state and does not
write ledger events.

Rules:

- No network.
- No Nautilus import required.
- No dashboard server start.

---

## Dashboard

Embedded minimal web dashboard (Flask or similar lightweight framework).

### Files

```text
paper_dashboard/
  __init__.py
  server.py
  templates/
    index.html
    strategy_detail.html
```

### Index page

Displays a table of all paper strategies with columns:

```text
strategy_id
signal_family
state (ENABLED/DISABLED/KILLED/EXPIRED)
promoted_at_utc
mean_net_bps
win_rate
valid_count
last_refalsified_utc
refalsification_status
disable_reason
```

Sort: newest promoted first.

### Strategy detail page

At `GET /strategy/<strategy_id>` or similar:

Show full `PaperStrategySpec`, linked precommitment, latest null/FDR/holdout
results, PnL ledger summary, and latest refalsification decision details:

```text
latest refalsification decision
gate freshness
latest null/FDR/holdout/cross-capture status
```

### Dashboard safety rules

- Dashboard must not allow overriding a refalsification-disabled strategy back
  to `ENABLED` unless a manual override field is explicitly added in a later v1.
  In v0, disabled-by-refalsification stays disabled.
- Dashboard must render strategy states from the registry only — no write-back
  from the UI.
- Dashboard must not execute strategies, start paper trading, or connect to
  brokers.
- Dashboard must not write to the paper registry or any ledger.

### Dashboard test

```python
test_paper_dashboard.py
```

Tests:

- Index page renders.
- Strategy detail page renders.
- No broken links.
- No Nautilus imports.

---

## Tests

### `test_paper_models.py`

- Valid spec construction.
- Invalid state transitions.
- Hyphenated IDs rejected.
- `execution_mode` validated.
- Empty fields rejected.

### `test_paper_registry.py`

- Save and load round-trip.
- Load all strategies.
- Strategy file persistence across load/save cycles.
- Delete strategy.
- Update state.
- Corrupt/non-JSON files skipped gracefully.
- Temp directories.

### `test_paper_gate_verifier.py`

- `check_promotion_frozen` with valid precommitment → `PROMOTION_FROZEN`.
- Missing precommitment file → `FAILED_MISSING_PRECOMMITMENT`.
- `ledger_write_authorized_before_locked_run` false → `FAILED_EVIDENCE_GAP`.
- `registry_verdict_authorized` false → `FAILED_VERDICT_NOT_AUTHORIZED`.
- Locked-run ledger event exists → gate 1 passes.
- Locked-run ledger event missing → gate 1 fails.
- Summary exists with group → gate 3 passes.
- Summary missing → gate 3 fails.
- Group negative in locked run → gate 3 fails.
- No null data → gate 4 passes with `SKIPPED_NO_NULL_DATA`.
- No stage 2 data → gate 5 passes with `SKIPPED_NO_STAGE2_DATA`.
- Verifier does not write ledger events (assert no JSONL events written by verifier).
- Verifier does not write precommitment files.

### `test_paper_auto_promotion.py`

- Full promotion flow: frozen + 5 gates pass → `PAPER_STRATEGY_PROMOTED`.
- Frozen fails → blocked, `AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH` written.
- Gate 1 fails → `PAPER_PROMOTION_BLOCKED_BY_GATE`.
- Dry-run: evaluates and reports but does not write registry or ledger.
- Existing strategy skip: already ENABLED strategy is skipped.
- Duplicate strategy detection.
- No registry writes in dry-run.
- No ledger writes in dry-run.
- No forbidden verdicts.
- Event hash verifies.

### `test_paper_refalsification.py`

- Active strategy with all fresh gates still passing → remains enabled.
- Missing artifact → disables fail-closed.
- Null now fails → disables.
- FDR now fails → disables.
- Holdout now fails → disables.
- Cross-capture median now below cost floor → disables.
- Cross-capture median negative → disables.
- Strategy younger than min age → skipped.
- Strategy checked too recently → skipped.
- Dry-run does not mutate registry and does not write ledger.
- Refalsification event hash verifies.
- No forbidden verdict strings appear in refalsification events.

### `test_run_paper_promotion.py`

- `--help` works.
- `--dry-run` exits cleanly with a valid precommitment hash/config.
- `--dry-run` writes no ledger, no registry, no precommitment mutations.
- No network calls.
- No Nautilus import required.

### `test_run_paper_refalsification.py`

- `--help` works.
- `--once --dry-run` exits cleanly.
- No network.
- No Nautilus import.
- No ledger write in dry-run.

---

## Acceptance criteria

Implementation is accepted only if:

- All spec files exist in `paper/`, `paper_dashboard/`, `tests/`, and CLI entrypoints.
- `PaperStrategySpec` includes `execution_mode` with enum type and validation.
- `execution_mode == PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED` is validated.
- `gate_verifier.py` checks `PROMOTION_FROZEN` first and returns `FAILED_*` immediately.
- Verifier does not read artifacts beyond the precommitment file.
- Verifier does not write precommitments.
- Verifier does not write ledger events.
- `auto_promotion.py` writes `AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH`.
- `paper/refalsification.py` exists.
- `run_paper_refalsification.py` exists.
- Paper strategies can be automatically disabled after promotion.
- Refalsification is fail-closed (missing artifact → disable).
- Disabled-by-refalsification strategies cannot be re-enabled by the dashboard in v0.
- Refalsification writes `PAPER_STRATEGY_DISABLED_REFALSIFICATION`.
- Refalsification never writes `TRADE_READY`, `EXECUTION_READY`, `LIVE_READY`, or `CANDIDATE_FOR_LIVE`.
- Refalsification tests pass.
- Paper dashboard exposes refalsification freshness/status.
- Paper PnL is never treated as sufficient evidence of strategy quality without fresh falsification state.
- Dashboard does not write to registry or ledger.
- Dashboard does not allow re-enabling disabled strategies.
- `--dry-run` everywhere skips ledger writes and registry mutations.
- All tests use temp directories for registry, ledgers, configs.
- No real captures, no network, no archive downloads, no auth, no execution.
- No forbidden verdicts appear except as constants or assertion targets.
- Existing evaluators, `REJECTED_RESEARCH.md`, real `evidence_ledger.jsonl` are not modified by tests.

---

## Explicit non-goals for v0

- No live trading, paper broker connection, or API key usage.
- No cross-iteration persisted state beyond registry files, ledger events, and precommitments.
- No notifications or alerting.
- No systemd unit creation or service installation.
- No multi-user dashboard.
- No strategy parameter re-optimization during refalsification — disable only.
- No deletion of strategy files on disable.
- No deletion of PnL history on disable.
- No migration from `DISABLED` back to `ENABLED` via refalsification.
- No PnL-based refalsification gate (PnL is display-only).
- No mutable dashboard (no write-back from UI).

---

## Safety grep

```bash
grep -RInE '\b(submit_order|place_order|cancel_order|private_key|api_key|live_execute|paper_broker|broker_connect|TRADE_READY|EXECUTION_READY|LIVE_READY|testnet|alpaca|ibkr)\b' \
  examples/strategies/venue_agnostic_signal_observer/paper \
  examples/strategies/venue_agnostic_signal_observer/paper_dashboard \
  examples/strategies/venue_agnostic_signal_observer/run_paper_*.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_paper_*.py \
  examples/strategies/venue_agnostic_signal_observer/tests/test_run_paper_*.py
```

Allowed hits only:

- forbidden-verdict constants (in `policy.py`-imported `FORBIDDEN_VERDICTS` or
  explicit deny tests)
- explicit deny tests (`assert "TRADE_READY" not in ...`)
- `self.submit_order(...)` inside generated NautilusTrader Strategy template code
  (not paper auto-promotion)

---

## Final report additions

```text
refalsification implemented: yes/no
refalsification tests:   (count passed / total)
disabled-by-refalsification can be dashboard-reenabled: no
paper strategies auto-promote out: yes/no
```
