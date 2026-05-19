# Source Structure Stress Gates v0 — Precommitment

**Study ID:** `source_structure_stress_gates_v0`

**Status:** Phase 0 — Instrumentation only

**Date frozen:** 2026-05-19

---

## Purpose

A source-only Phase 0 instrument for measuring Hawkes self-exciting volatility
labels and permutation entropy on BTC/ETH source tick streams.

This is intended for later integration into cross-asset beta-lag evaluation
as an alternative regime-detection mechanism.

---

## Structural-Change Rationale

Existing deterministic stress labels ask:
> "Did BTC/ETH cross a fixed bps threshold within a lookback window?"

This Phase 0 module asks two different questions:
> 1. "Was BTC/ETH volatility self-exciting (Hawkes cascade)?"
> 2. "How structured/complex was the source-side return sequence (permutation entropy)?"

That is a **different regime mechanism**, not a lookback/threshold retune.

---

## Non-Goals

- No target-return evaluation
- No candidate promotion
- No rejection update
- No trading / execution / bot path
- No order path
- No target leakage (SOL/LINK/DOGE/AVAX)
- No null/FDR tuning
- No entropy cutoff in v0
- No entropy gating in v0

---

## Hawkes Parameter Freeze

This is a **fixed-parameter calculator only**.

- No grid fitter
- No optimizer
- No alpha tuning

| Parameter | Value | Notes |
|-----------|-------|-------|
| Event threshold | 5.0 bps (absolute 1s log return) | Phase 0 implementation default |
| tau (decay) | 30.0 seconds | Fixed |
| Branching ratio (eta) | 0.5 | Fixed |
| alpha | eta / tau = 0.0167 | Derived, not independently tunable |
| Min events | 30 | Gate for intensity computation |
| Intensity multiple threshold | 3.0 (lambda/mu) | Gate for label emission |
| Prior-event lookback | 60 seconds | Window for prior event count |
| Min prior events | 3 | Gate for label emission |
| Label cooldown | 30 seconds | Prevents duplicate labels |
| Merge gap | 30 seconds | Window merge threshold |

These are Phase 0 implementation defaults, not edge claims.

---

## Permutation Entropy Rule

Permutation entropy is a **source-side complexity/predictability measurement**.

- Low normalized entropy → more structured/predictable source series
- High normalized entropy → more random/complex source series

It is intended as a **future gate, not an entry signal**.

In **v0**, the module **only writes** the source-only entropy distribution.
A later **v1 precommitment** may freeze an entropy cutoff after reviewing this
source-only distribution, but **before any target-return evaluation**.

**v0 entropy config (frozen):**
- Embedding dimension: 3
- Delay: 1 bucket
- Window: 120 seconds
- Min patterns: 30

---

## Leakage Rule

Hawkes fitting, Hawkes labeling, and permutation entropy measurement may use
**only BTC/ETH source-side ticks** up to label time.

They must **NOT** use:
- SOL/LINK/DOGE/AVAX prices
- Target forward returns
- Target coverage
- Random baselines
- Candidate gates
- FDR output
- Null-test outcomes
- Registry outcomes
- Any beta-lag result

---

## Output Semantics

### Hawkes Stress Label

Means: "This BTC/ETH source window shows source-side self-exciting volatility
under the frozen Phase 0 Hawkes definition."

### Permutation Entropy Point

Means: "This is the measured source-side ordinal complexity at this timestamp
under the frozen Phase 0 entropy definition."

### Neither Means

- "There is an edge."
- "The target should move."
- "The signal passed null."
- "The strategy is tradeable."
- "The window is approved for target-return evaluation."

A later v1 precommitment is required before pointing these labels at target returns.

---

## Allowed Verdicts

| Verdict | Meaning |
|---------|---------|
| `HAWKES_STRESS_LABELS_READY` | Labels emitted successfully |
| `HAWKES_INSUFFICIENT_SOURCE_EVENTS` | Too few source events for Hawkes |
| `ENTROPY_MEASUREMENTS_READY` | Entropy computed with sufficient patterns |
| `ENTROPY_INSUFFICIENT_PATTERNS` | Too few patterns for reliable entropy |
| `NO_HAWKES_STRESS_LABELS` | Enough events but no gates passed |
| `SOURCE_INPUT_UNUSABLE` | Input data insufficient |
| `PHASE0_IMPLEMENTATION_READY` | Code is ready for evaluation |

## Forbidden Verdicts

Any verdict implying tradability, rejection, candidate status, or promotion:
`CANDIDATE`, `REJECTED`, `TRADE_READY`, `EXECUTION_READY`, `BOT_READY`,
`SOURCE_STRUCTURE_STRESS_READY`, or any equivalent.

---

## Future Work (v1)

After reviewing the Phase 0 entropy distribution on source-only data:
1. Freeze an entropy cutoff threshold
2. Update precommitment
3. Wire entropy gate into evaluation pipeline
4. Run full target-return evaluation
