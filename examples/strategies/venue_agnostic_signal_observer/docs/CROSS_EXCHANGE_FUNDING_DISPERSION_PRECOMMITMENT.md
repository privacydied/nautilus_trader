# Cross-Exchange Funding Dispersion Carry — Phase 0 Precommitment

**Study ID:** `cross-exchange-funding-dispersion-carry-v1`
**Status:** PRECOMMITMENT — frozen before first evaluation run
**Date drafted:** 2026-05-18
**Precommitment version:** v1

---

## 0. Why this document exists

This is a frozen Phase 0 precommitment. It declares the hypothesis, data,
signal definition, cell grid, cost model, null methodology, the full evaluation
pipeline, and the verdict taxonomy **before** any evaluation run. It exists so
that the result — pass or fail — is a real falsification test and not a
parameter search.

It mirrors the structure of the Family 2 funding crowding reversal precommitment,
because that study's frozen 60-cell design produced a clean, trustworthy
rejection. The discipline carried over here:

- No threshold, horizon, hold length, cost level, or window may be changed
  after the first evaluation run without recording a new precommitment version.
- Phase 0 sizing gates (Stages 1–2) **must not** tune the cell grid. They run
  first and can only kill the study early; they cannot reshape it.
- Window selection is mechanical (Section 3), not discretionary. No handpicked
  volatile or high-dispersion regimes.

"Frozen" takes effect when this document is committed into the project
alongside the other precommitment docs. Until then it is a draft and may be
edited freely. After commit, changes require a new version (`v2`, etc.).

---

## 1. Hypothesis

> Test whether BTC/ETH Binance-vs-Bybit perpetual funding dispersion produces
> repeatable positive funding-carry campaigns after one-time entry/exit costs,
> using only precommitted historical settlement data, with convergence as the
> mechanism but realized funding accrual as the measured return.

**Mechanism (theory):** When the same perpetual carries a meaningfully different
funding rate on two venues, the crowded-funding venue's rate tends to revert
toward the cheaper venue's over subsequent settlements.

**Measured return (what actually decides the verdict):** Realized cumulative
differential funding PnL collected on a two-leg perp position over a fixed hold,
net of a one-time campaign cost. Convergence of the funding *rates* is a
diagnostic only — it is never the primary target. A campaign where dispersion
"converges" but the position collected too little funding before it did is a
**failure**, not a success.

This is structurally distinct from every locked gate in `REJECTED_RESEARCH.md`:

- Family 2 tested **single-venue** (Binance) funding extremes predicting **spot
  price** reversal. This tests **cross-venue funding dispersion** predicting
  **funding convergence**, with the return captured as **funding accrual**, not
  price. Family 2's "what this does NOT reject" section explicitly lists
  cross-exchange funding (Bybit/OKX) as untested.
- It is not a price-prediction lead-lag study. The return source is funding
  paid/received, not a forward price move.
- It does not depend on a maker/rebate cost assumption. It uses a conservative
  campaign cost and asks whether carry clears it anyway.

---

## 2. Scope and what this study does NOT claim

This is an **archive-only economic-signal falsification study**. It tests
whether the historical funding record contains a carry signal that clears
campaign-level cost. It does **not** test executable portfolio safety.

Explicitly out of scope for v1 (archive data cannot observe these; they are not
modeled and the verdict makes no claim about them):

- Liquidation-path risk on either perp leg if the underlying moves hard.
- Margin fragmentation across two venues and forced-deleveraging behavior.
- Borrow/inventory constraints and venue withdrawal/transfer latency.
- Exchange counterparty risk.
- Intra-settlement basis divergence between the two perps.

The word "market-neutral" is **not** used unqualified anywhere in this study.
The position is directionally neutral to the underlying *if both perp legs
track cleanly*; it is not risk-free. Any pass verdict means "the archive-level
economic signal survived" and nothing more.

**Wording discipline for the final report:** a `REJECTED_COST_WALL` outcome
means *rejected under the frozen 50 bps campaign cost model* — it does **not**
mean "funding dispersion carry has no edge." If the 50 bps gate fails while the
6 bps diagnostic tier (Section 10) shows positive carry, the report must state
the rejection is cost-conditional, exactly as the registry records the V6
funding/basis monitors as rejected under specific cost assumptions rather than
universally.

---

## 3. Data and window

| Item | Value |
|---|---|
| Venues | Binance, Bybit (perpetual / USDⓈ-M funding) |
| Assets | BTC, ETH |
| Instruments | BTC perp + ETH perp on each venue |
| Data source | Binance Vision archive; Bybit historical funding-rate archive |
| Data fields | Per-settlement funding rate, settlement timestamp, per venue per asset |
| Transport | Archive download only — no REST, no WebSocket, no authenticated endpoints |

**Window selection (mechanical, frozen):**

The evaluation window is the **strict intersection** of all four funding-rate
series (Binance-BTC, Binance-ETH, Bybit-BTC, Bybit-ETH): from the latest of the
four series start dates to the earliest of the four series end dates. BTC and
ETH share one common window for v1 — they are **not** split into separate
windows. The exact window dates are resolved once, at the start of the run,
from the archive files, and recorded in the run metadata. They are not chosen
by the researcher and not selected for volatility.

**Funding-cadence alignment:** Binance and Bybit both settle BTC/ETH perp
funding on an 8-hour cadence over the bulk of the archive. Any settlement
timestamp on one venue without a matching settlement window on the other (gaps,
schedule changes, listing edges) is dropped from the paired series. The count
of dropped settlements is recorded.

---

## 4. Signal definition (frozen)

### 4.1 Dispersion event

At each shared settlement time `t`, for each asset, compute:

```
dispersion_bps(t) = funding_binance(t) - funding_bybit(t)   [in bps, per settlement]
```

A **dispersion event** fires at settlement `t` when `|dispersion_bps(t)|`
is at or above a threshold (see grid, Section 6).

### 4.2 No-lookahead entry rule (frozen)

The funding value at `t` is treated as **settled information** — known only at
or after settlement `t`. Therefore:

- The **signal** is the dispersion observed at settlement `t`.
- **Entry occurs after `t`.** The position is considered established for the
  settlement window beginning at `t+1`.
- **Realized carry is measured over settlements `t+1 … t+N`** — never including
  `t` itself.

This rule exists so the signal funding observation is never also counted as
collected carry. v1 does not attempt to enter before `t`; doing so would
require proving the funding rate was observable pre-settlement, which the
archive does not establish.

### 4.3 Carry leg assignment (frozen, explicit)

Based on the dispersion sign at `t`:

- If `funding_binance > funding_bybit` by the threshold:
  **short Binance perp / long Bybit perp.**
- If `funding_bybit > funding_binance` by the threshold:
  **short Bybit perp / long Binance perp.**

In words: short the higher funding-rate venue and long the lower funding-rate
venue. Under the standard perp convention, positive funding is paid by longs to
shorts and negative funding is paid by shorts to longs; realized PnL is always
computed from the archive's actual payer/receiver direction at each
settlement, not from this prose shortcut.

### 4.4 Realized return (the measured object)

Hold for exactly `N` settlements over the window `t+1 … t+N` (4.2). Over those
`N` settlements, accumulate the realized funding PnL on both legs from the
archive's actual signed per-settlement rates.

**Explicit algebra (frozen — implement exactly this, not the prose):**

At each settlement `s` in `t+1 … t+N`, let `f_short(s)` be the funding rate
(in bps) of the venue the **short** leg is on, and `f_long(s)` be the funding
rate of the venue the **long** leg is on. Each is read from that leg's own
venue — the short leg reads its venue's funding, the long leg reads its
venue's funding.

Under the standard perp convention `funding_rate_bps > 0` means longs pay
shorts. Therefore, per settlement:

```
short_leg_pnl_bps(s) = + f_short(s)     # short receives positive funding, pays negative
long_leg_pnl_bps(s)  = - f_long(s)      # long pays positive funding, receives negative

settlement_carry_bps(s) = short_leg_pnl_bps(s) + long_leg_pnl_bps(s)
                        = f_short(s) - f_long(s)

realized_carry_bps = sum over s in (t+1 .. t+N) of settlement_carry_bps(s)
                   = sum over s in (t+1 .. t+N) of ( f_short(s) - f_long(s) )
```

The two forms are identical by construction (`short_pnl + long_pnl =
f_short - f_long`); both are shown so the per-leg reasoning and the compact
sum cannot drift apart in implementation. The sign convention is handled
automatically because the arithmetic operates on **signed** rates — a negative
funding rate flips payer and receiver with no special-casing.

This is the realized cumulative differential funding. It is computed from the
archive's actual per-settlement rates over the fixed hold — **not** projected
from the entry-time dispersion, and **not** assumed to persist. If dispersion
collapses after one settlement, the remaining settlements simply contribute
whatever funding actually paid out.

`net_carry_bps = realized_carry_bps - campaign_cost_bps` (Section 10).

### 4.5 Convergence is diagnostic only

Whether `|dispersion_bps|` narrows over the hold is recorded as a diagnostic
field. It never enters a gate and never defines the verdict. The verdict is
decided by `net_carry_bps` only.

### 4.6 Non-overlapping primary campaigns (frozen)

Primary evaluation is **non-overlapping per `(asset, threshold, N)` cell**.
Once a campaign fires at `t`, any new dispersion event in the same cell during
`t+1 … t+N` is **suppressed** — it does not start a second campaign. The next
eligible event is the first one at or after `t+N+1`.

Rationale: the v1 model is a unit-size two-leg campaign. Allowing overlapping
campaigns would silently assume pyramiding / capital reuse that is not modeled,
and would inflate event counts with non-independent observations. The
`valid_count` used by every gate is the non-overlapping count.

The count of events that *would have* fired during open holds is recorded
separately as `overlapping_events_suppressed`, a diagnostic only — it shows how
much the signal clusters but never enters a gate.

### 4.7 Event validity

An event is **valid** only if all `N` post-entry settlements (`t+1 … t+N`)
exist in the aligned paired series — no truncation at the window's end. Events
whose hold would run past the window end are dropped and counted as
`truncated_events`.

---

## 5. Pipeline overview (end-to-end, frozen execution order)

The study runs as a single ordered pipeline. Each stage has fixed inputs,
outputs, and exit conditions. A stage's kill condition terminates the pipeline
with a study-level verdict; it does not fall through to later stages.

```
STAGE 0  Data load & window resolution
            -> resolves common window, normalizes funding units, aligns
               settlements, hashes archives, applies 70/30 chronological split
            -> exit: FUNDING_UNIT_AMBIGUOUS  (unit unclassifiable / out of band)
            -> exit: DATA_INSUFFICIENT  (a series missing / window too short)

STAGE 1  Phase 0 Gate A — distribution sizing
            -> dispersion tail counts by threshold
            -> exit: NEEDS_MORE_DATA_OR_NO_TAIL  (tail absent at all thresholds)

STAGE 2  Phase 0 Gate B — economic feasibility (across frozen threshold set)
            -> realized carry distribution per (threshold, N)
            -> exit: NEEDS_MORE_DATA_OR_NO_TAIL  (no powered threshold x N)
            -> exit: REJECTED_COST_WALL  (powered combos exist, none clear cost)

STAGE 3  Grid evaluation (24 cells, non-overlapping, on the 70% train portion)
            -> per-cell event series, realized_carry_bps, net_carry_bps
            -> no exit; always produces 24 cell records

STAGE 4  Per-cell pre-null economic gates
            -> labels each cell: NEEDS_MORE_DATA_OR_NO_TAIL / REJECTED /
               REJECTED_COST_WALL / (provisional) PASS_PRE_NULL

STAGE 5  Null test (event-vector circular shift) on PASS_PRE_NULL cells
            -> empirical p-value per cell; non-tested cells assigned p = 1
            -> labels: NULL_REJECTED_DIAGNOSTIC / (provisional) PASS_NULL

STAGE 6  Family-wide BY FDR across all 24 cells' p-values (frozen denominator 24)
            -> labels PASS_NULL cells: FDR_BLOCKED_DIAGNOSTIC / (proceed)

STAGE 7  Holdout confirmation on FDR survivors (30% holdout portion)
            -> labels: HOLDOUT_FAILED_DIAGNOSTIC /
               ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION

STAGE 8  Study-level verdict assembly
            -> deterministic rule (Section 13) maps 24 cell verdicts to one
               study-level verdict
```

Stages 0–2 are the early-kill path. Stages 3–8 only run if Stage 2 passes.

---

## 6. Cell grid (frozen)

| Axis | Values | Count |
|---|---|---|
| Asset | BTC, ETH | 2 |
| Dispersion threshold | 5, 10, 20, 40 bps per settlement | 4 |
| Hold length N | 3, 6, 12 settlements (≈1, 2, 4 days at 8h cadence) | 3 |

**Total: 2 × 4 × 3 = 24 primary cells.**

N is deliberately capped at 12. Holds requiring weeks of exposure to clear cost
are excluded — the archive might show positive math over very long holds, but
the executable version becomes margin-risk theatre, and v1 will not chase it.

---

## 7. Stage 0 — Data load & window resolution

**Input:** Binance Vision + Bybit funding archives for BTC perp and ETH perp.

**Steps:**
1. Load all four per-settlement funding series.
2. **Normalize funding units to bps per settlement.** Binance and Bybit
   archives may express funding as a decimal fraction or as a percent; the two
   venues may differ. For each series, detect the source unit and convert to
   bps per settlement. Record both the detected source unit
   (`funding_rate_unit_detected`) and confirmation of normalization
   (`funding_rate_unit_normalized_to_bps`) per series in the metadata. A silent
   decimal-vs-percent mismatch between venues would manufacture a spurious
   dispersion signal — this step exists to make that failure visible rather
   than invisible.

   **Fail-closed:** unit detection does not guess. If a series cannot be
   confidently classified as decimal vs percent, **or** if the normalized
   per-settlement funding values fall outside a sanity band of ±300 bps per
   settlement (any single 8h funding rate beyond ±3% is far outside anything
   the BTC/ETH archive should contain and indicates a misclassified unit), the
   run **stops** with verdict `FUNDING_UNIT_AMBIGUOUS`. It does not proceed on
   a guess. The offending series and the values that triggered the stop are
   recorded.
3. Resolve the common window (Section 3): strict four-way intersection.
4. Align settlements to shared 8h windows; drop unaligned settlements, count
   them as `dropped_unaligned_settlements`.
5. Hash each input series (content hash) for the reproducibility record.
6. Split the common window chronologically 70/30 into train and holdout; record
   the split date. Stages 1–6 run on train; Stage 7 uses holdout.

**Output:** four aligned funding series on one common settlement index; the
resolved window dates; the train/holdout split date; archive hashes;
dropped-settlement count.

**Exit conditions:**

- **`FUNDING_UNIT_AMBIGUOUS`** — if step 2's unit detection cannot confidently
  classify a series, or a normalized series falls outside the ±300 bps
  per-settlement sanity band. Pipeline stops; no guess is made.
- **`DATA_INSUFFICIENT`** — if any of the four series is missing, or the
  resolved common window contains fewer than 200 aligned settlements (≈66 days)
  — too short to power even the shortest-hold cells across a 70/30 split.
  Pipeline stops.

---

## 8. Stage 1 — Phase 0 Gate A (distribution sizing)

> Does cross-venue funding dispersion on BTC/ETH actually exist at meaningful
> per-settlement magnitudes?

**Input:** the four aligned series (train portion).

**Steps:** for each asset, count settlements where `|dispersion_bps|` is at or
above each of **5, 10, 20, 40 bps per settlement**. Report counts and
frequencies. Actual per-settlement bps only — no annualized figures.

**Output:** `gate_a_distribution.json` — tail counts per asset per threshold.

**Exit condition — `NEEDS_MORE_DATA_OR_NO_TAIL`:** if, for **both** assets, the
count is below 50 at **every** threshold in the frozen set — i.e. no threshold
produces even a minimal event count for either asset. The tail is absent and no
cell could be powered. Pipeline stops.

Gate A **does not** select thresholds. It reports the full distribution and
either passes the whole frozen grid forward or kills the study.

---

## 9. Stage 2 — Phase 0 Gate B (economic feasibility)

> When dispersion events fire, does the realized cumulative carry over the
> frozen N set ever clear the frozen campaign cost?

**Input:** dispersion events from Gate A; the aligned series (train portion).

**Steps:** evaluate feasibility across the **full frozen threshold set** — not
only the inclusive 5 bps set. For every `(threshold, N)` combination in
{5,10,20,40} bps × {3,6,12}, with non-overlapping campaigns (Section 4.6),
compute the distribution of `realized_carry_bps` and `net_carry_bps` per
Section 4.4. Report all combinations.

**Output:** `gate_b_feasibility.json` — realized/net carry distributions per
`(threshold, N)`, with the non-overlapping event count for each.

**Exit conditions (two checks, in order):**

1. **`NEEDS_MORE_DATA_OR_NO_TAIL`** — if **no** `(threshold, N)` combination has
   at least 50 non-overlapping events. No combination is powered, so there is
   nothing to falsify. This is a not-testable outcome, **not** a cost-wall
   rejection. Pipeline stops.
2. **`REJECTED_COST_WALL`** — if powered combinations exist (at least one
   `(threshold, N)` with ≥ 50 non-overlapping events) but **none** of the
   powered combinations produces a positive **median** `net_carry_bps`. The
   carry cannot clear the 50 bps wall anywhere it could actually be tested.
   Pipeline stops.

If at least one powered combination shows positive median net carry, Gate B
passes the full frozen grid forward.

Gate B evaluates the **whole frozen grid** and selects no winners — it either
passes the full grid forward or kills the study. It is deliberately *not* a
5-bps-only check: the low threshold may be mostly noise while the 20/40 bps
tail carries the real economics, and Gate B must not become a low-threshold
veto that kills a study whose actual signal it never examined. Equally, an
absence of events is never collapsed into a cost-wall verdict — low/no-event
outcomes are not falsification.

---

## 10. Cost model (frozen, decomposed)

The primary promotion gate is **net carry must beat a 50 bps frozen campaign
cost**, consistent with the rest of the registry. The 50 bps is **not** a
blended constant — it is a visible decomposition so no later reader can quietly
swap in an optimistic number:

| Component | Frozen value (bps) | Note |
|---|---|---|
| Open leg 1 | 12.5 | Taker, one perp leg |
| Open leg 2 | 12.5 | Taker, other perp leg |
| Close leg 1 | 12.5 | Taker |
| Close leg 2 | 12.5 | Taker |
| Slippage / buffer | folded into the taker assumption | conservative |
| Venue-mismatch / risk haircut | 0 (named, not numerically applied in v1) | scoped out per Section 2; named for transparency |
| **Primary campaign cost** | **50.0** | one-time, charged once per campaign |

The cost is charged **once per campaign**, not per settlement — that is the
entire reason a funding-accrual hypothesis can clear a 50 bps wall where a
per-signal price study cannot.

A **6 bps diagnostic-only sensitivity tier** is also computed (mirroring Family
2's diagnostic tier). It changes **no verdict**. It exists so the report can
state whether the conclusion is cost-wall-driven or signal-driven. If 6 bps
also shows no edge, the signal itself is absent; if 6 bps passes but 50 bps
fails, the blocker is genuinely cost — and the report must say so explicitly
(Section 2 wording discipline). Either way the primary verdict stands on the
50 bps gate.

If a future study wants a maker/rebate cost tier, that is a **new
precommitment**, not an edit to this one.

---

## 11. Stage 3 — Grid evaluation

**Input:** the 24 frozen cells; the aligned series (70% train portion).

**Steps:** for each cell `(asset, threshold, N)`:
1. Identify dispersion events for that asset at that threshold (Section 4.1),
   applying non-overlapping suppression for that cell's `N` (Section 4.6).
2. For each valid event (Section 4.7), assign carry legs (4.3) and compute
   `realized_carry_bps` and `net_carry_bps` over `t+1 … t+N` (4.4).
3. Compute per-cell statistics: `valid_count` (non-overlapping),
   `mean_net_carry_bps`, `median_net_carry_bps`, `win_rate`,
   `worst_decile_net_carry_bps`, the diagnostic convergence rate (4.5), and
   `overlapping_events_suppressed`.

**Output:** 24 cell records, written to `grid_evaluation.jsonl`. No kill
condition — Stage 3 always produces all 24 records.

---

## 12. Stages 4–7 — Per-cell gates, null, FDR, holdout

### 12.1 Stage 4 — Pre-null economic gates

Each cell is labeled by the first matching rule, in order:

1. `valid_count < 50` → **`NEEDS_MORE_DATA_OR_NO_TAIL`**
2. `median_net_carry_bps <= 0` at 50 bps cost → **`REJECTED_COST_WALL`**
   (dispersion events existed but carry did not clear cost)
3. `mean_net_carry_bps <= 0` OR `win_rate < 0.55` OR
   `worst_decile_net_carry_bps <= -50` → **`REJECTED`**
   (cleared the cost-wall median check but failed an economic gate)
4. otherwise → **`PASS_PRE_NULL`** (provisional; proceeds to null)

### 12.2 Stage 5 — Null test (primary)

Applied to `PASS_PRE_NULL` cells.

**Event-vector circular shift.** The null holds the realized future
funding-differential series intact and holds event clustering and direction
assignments intact, then **circularly shifts the event/direction vector by a
random whole number of funding periods relative to the future-carry series**.
Realized carry is recomputed by reading the (unchanged) future-carry series at
the shifted event positions. This tests precisely whether dispersion *timing*
predicts future carry, while preserving the funding-calendar structure, event
clustering, and direction mix.

A generic timestamp shuffle is **not** used — funding rates are settlement-time
objects, and naive shuffling destroys the persistence structure the hypothesis
is about. A plain rotation of the whole settlement series is also avoided,
because it can preserve the original event-to-carry relationship; shifting the
event vector *relative to* an intact carry series is the clean test.

- Iterations: 1000
- Alpha: 0.05
- Seed: 42 (recorded)
- Empirical p-value: `p = (#{null_mean >= observed_mean} + 1) / (iterations + 1)`
  — i.e. the count of null iterations whose mean net carry is at least the
  observed mean, plus one, over iterations plus one (standard add-one).

A **block-shuffle** secondary null (consecutive settlement blocks, block size
recorded) is computed as a diagnostic only; it does not change any label.

Cells with empirical `p > 0.05` → **`NULL_REJECTED_DIAGNOSTIC`**.
Cells with `p <= 0.05` → **`PASS_NULL`** (provisional).

**FDR denominator preservation:** every cell *not* null-tested (any cell not
labeled `PASS_PRE_NULL`) is assigned `p = 1` so the FDR family size in Stage 6
is always exactly 24, matching the frozen grid. The family size is declared in
advance and does not depend on how many cells happened to reach the null.

### 12.3 Stage 6 — Family-wide FDR

Benjamini–Yekutieli across **all 24 cells' p-values** (the null p-values from
Stage 5 plus `p = 1` for non-tested cells), BY (not BH) for consistency with
Family 2 and because the cells are correlated — shared assets, overlapping
holds. Alpha = 0.05. Family size is the frozen 24.

`PASS_NULL` cells failing BY FDR → **`FDR_BLOCKED_DIAGNOSTIC`**.
`PASS_NULL` cells surviving BY FDR → proceed to holdout.

### 12.4 Stage 7 — Holdout confirmation

Stages 3–6 run on the 70% chronological train portion. Cells surviving FDR are
re-evaluated independently on the 30% chronological holdout:

- The holdout re-evaluation must independently satisfy: ≥ 20 valid
  non-overlapping events on the holdout, `mean_net_carry_bps > 0`,
  `median_net_carry_bps > 0`, `win_rate >= 0.55`, and
  `worst_decile_net_carry_bps > -50`, all at the 50 bps primary cost.
- A cell that fails the holdout → **`HOLDOUT_FAILED_DIAGNOSTIC`**.
- A cell that passes → **`ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION`**.

The holdout uses the **same frozen thresholds** — no re-tuning. The split
boundary is never crossed by any window, campaign hold, or null shift.

---

## 13. Stage 8 — Study-level verdict assembly (deterministic)

After all 24 cells carry a final per-cell verdict, the study-level verdict is
assigned by the first matching rule, in order:

1. Pipeline exited at Stage 0 → the verdict Stage 0 produced: either
   **`FUNDING_UNIT_AMBIGUOUS`** (unit unclassifiable or out of sanity band) or
   **`DATA_INSUFFICIENT`** (a series missing or the window too short).
2. Pipeline exited at Stage 1 → **`NEEDS_MORE_DATA_OR_NO_TAIL`**.
3. Pipeline exited at Stage 2 → the verdict Gate B produced: either
   **`NEEDS_MORE_DATA_OR_NO_TAIL`** (no powered `(threshold, N)` combination) or
   **`REJECTED_COST_WALL`** (powered combinations exist but none clear cost).
4. ≥ 1 cell is `ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION` →
   **`ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION`** (study-level). The report
   lists which cells, with full statistics.
5. Else if ≥ 1 cell survived BY FDR (Stage 6) but every such cell ended as
   `HOLDOUT_FAILED_DIAGNOSTIC` → **`HOLDOUT_FAILED_DIAGNOSTIC`** (study-level):
   an edge survived the null and family-wide FDR but did not confirm
   out-of-sample. This is the "statistically interesting but not out-of-sample"
   outcome and is kept distinct from FDR blockage.
6. Else if ≥ 1 cell reached `PASS_NULL` but every such cell ended as
   `FDR_BLOCKED_DIAGNOSTIC` → **`FDR_BLOCKED_DIAGNOSTIC`** (study-level): a real
   edge survived the null but did not survive multiple-comparisons correction.
7. Else if ≥ 1 cell reached `PASS_PRE_NULL` but every such cell ended as
   `NULL_REJECTED_DIAGNOSTIC` → **`NULL_REJECTED_DIAGNOSTIC`** (study-level):
   carry cleared cost but is indistinguishable from randomly-shifted timing.
8. Else if ≥ 1 cell is `REJECTED` and no cell did better →
   **`REJECTED`** (study-level): cleared the cost-wall median check but failed
   an economic gate.
9. Else if all non-underpowered cells are `REJECTED_COST_WALL` →
   **`REJECTED_COST_WALL`** (study-level).
10. Else (all 24 cells `NEEDS_MORE_DATA_OR_NO_TAIL`) →
    **`NEEDS_MORE_DATA_OR_NO_TAIL`** (study-level).

This rule is frozen. The study-level verdict is whatever it produces — there is
no discretionary override.

---

## 14. Verdict taxonomy (frozen)

| Verdict | Meaning |
|---|---|
| `FUNDING_UNIT_AMBIGUOUS` | Stage 0: a funding series could not be confidently classified as decimal vs percent, or a normalized series fell outside the ±300 bps per-settlement sanity band. The run stopped rather than guess. Not a trade rejection — a data-integrity stop. |
| `DATA_INSUFFICIENT` | Stage 0: a series is missing or the common window is too short to power any cell. Not a trade rejection. |
| `NEEDS_MORE_DATA_OR_NO_TAIL` | Gate A, or all cells underpowered: dispersion tail barely exists, or too few events to power a cell. Not a trade rejection — the signal was not testable. |
| `REJECTED_COST_WALL` | Dispersion exists, but realized cumulative carry over N cannot clear the **frozen 50 bps campaign cost**. Cost-conditional — not a claim that the signal has no edge at any cost. |
| `REJECTED` | Cell(s) had ≥ 50 events and cleared the cost-wall median check but failed the pre-null economic gates (negative mean net carry, sub-0.55 win rate, or worst-decile below −50). |
| `NULL_REJECTED_DIAGNOSTIC` | Cleared cost and pre-null gates but the event-vector-shift null was not exceeded. Diagnostic evidence, **not** a general `REJECTED` for the family. |
| `FDR_BLOCKED_DIAGNOSTIC` | Survived the null but failed family-wide BY FDR. Diagnostic evidence. |
| `HOLDOUT_FAILED_DIAGNOSTIC` | Survived the null and family-wide FDR on train but failed out-of-sample on the holdout. Statistically interesting, not confirmed out-of-sample. |
| `ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION` | Passed all archive gates: positive net carry after 50 bps, beat the null, survived FDR, confirmed on holdout. Means **archive-level economic signal survived** — warrants execution modeling and longer observation. Does **not** mean tradeable, executable, or live-ready. |

**Forbidden verdicts** (consistent with the runbook's falsification-module rule
— these would trip `ValueError` and they imply a live-trading claim this study
cannot make): `CANDIDATE_FOR_LIVE`, `EXECUTION_READY`, `TRADE_READY`,
`ARCHIVE_CANDIDATE_FOR_EXECUTION_MODELING`. The pass verdict is
`ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION` — the same vocabulary the registry
already uses for a promotion that still needs more observation.

---

## 15. Acceptance gates for a per-cell pass

A cell earns `ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION` only if **all** hold:

1. Sufficient events: at least 50 valid non-overlapping dispersion events in
   the cell (train).
2. `mean_net_carry_bps > 0` at the 50 bps primary cost.
3. `median_net_carry_bps > 0` at the 50 bps primary cost.
4. Win rate (fraction of campaigns with positive `net_carry_bps`) ≥ 0.55.
5. Worst-decile `net_carry_bps` floor > −50 bps.
6. Beats the event-vector-shift null (empirical p ≤ 0.05).
7. Survives family-wide BY FDR at alpha = 0.05 (frozen family size 24).
8. Independently re-satisfies gates 2–5 on the 30% chronological holdout, with
   at least 20 valid non-overlapping events on the holdout.

Gates 1–5 are the pre-null economic gates (Stage 4). 6 is the null (Stage 5).
7 is FDR (Stage 6). 8 is the holdout (Stage 7). All thresholds are frozen and
mirror Family 2's gate structure.

---

## 16. Run metadata / reproducibility

Every run writes a metadata block recording: `git_sha`, `schema_version`,
`seed`, the **resolved common window** start/end (the mechanically-derived
dates), the train/holdout split date, per-series archive content hashes,
per-series `funding_rate_unit_detected` and `funding_rate_unit_normalized_to_bps`
(Section 7 step 2), `run_args`, `generated_at`, `python_version`, key package
versions, the `dropped_unaligned_settlements` / `truncated_events` /
`overlapping_events_suppressed` counts, and the Stage 1 / Stage 2 gate outputs.
Prior runs are never overwritten in place; each run writes to a run-specific
output directory.

---

## 17. Kill condition (one sentence)

If cross-venue funding dispersion on BTC/ETH Binance-vs-Bybit rarely produces
enough realized cumulative carry over a fixed 3/6/12-settlement hold to clear a
50 bps one-time campaign cost — at any stage of the pipeline — archive the
hypothesis under the study-level verdict produced by Section 13 and move on; do
not reopen it by tuning thresholds, hold lengths, the window, or the cost model.

---

## 18. Pre-declared adjacent hypotheses NOT tested by v1

Recorded here as boundaries, not promises:

- **Convergence-triggered exit** — holding until dispersion narrows below a
  threshold. A v2 with its own precommitment, worth doing only if v1 fixed-N
  shows real carry. It reintroduces exit-rule discretion that v1 deliberately
  avoids.
- **OKX (or a third venue)** — widens the dispersion universe but adds archive
  validation and funding-cadence alignment complexity. Two-venue v1 first.
- **A mark-to-market basis component** — v1 is pure funding accrual. A spread
  component would need to be explicitly proven executable in its own study.
- **Altcoin funding dispersion** — a separate hypothesis if BTC/ETH shows a
  tail; not a rescue patch if it doesn't.
- **Maker/rebate cost tier** — a different cost model, hence a new
  precommitment.
- **Funding + OI regime conditioning** — not evaluated as a filter here.
- **Variable / signal-dependent position sizing** — v1 treats every campaign as
  a unit-size two-leg position; size-weighting is a separate design.
- **Pre-settlement entry** — entering before settlement `t` using an
  intra-period funding estimate. v1 uses the safe `t+1` rule; pre-settlement
  entry needs a separate precommitment that establishes pre-settlement
  observability.

---

## Appendix A: Resolved ambiguities (pre-freeze)

These were identified during implementation and must be recorded here before
the first evaluation run to maintain the precommitment property.

### A.1: Worst-decile definition (Q2)

**Section:** 12.1 (Stage 4, pre-null economic gates), rule 3c

**Ambiguity:** "worst decile net_carry_bps > −50" — is "worst decile" the p10
single value or the mean of the bottom 10%?

**Resolution (2026-05-19):** Worst decile means the **p10 single value** —
the value at the 10th-percentile index of the sorted net-carry distribution.
That is: `sorted_nc[max(0, len(sorted_nc) // 10 - 1)]`.

This matches the standard statistical usage of "decile" and is the more
conservative of the two interpretations (a single outlier can fail the gate;
the bottom-decile mean would dilute it).

### A.2: Funding-rate unit detection (Q1)

**Section:** 7, step 2

**Ambiguity:** The precommitment requires detecting the source unit and
converting to bps, but does not specify the detection heuristic.

**Resolution (2026-05-19):** Both archive sources return funding rates as
**decimal fractions**, not percentages.

Confirmed by inspecting the actual archive sources the pipeline will load:

- **Binance Vision** (CSV from data.binance.vision): column
  `last_funding_rate` contains decimal fractions like `0.00010000`,
  `0.00037409`. Even during the May 2021 bull run, the maximum BTC rate
  was `0.00098653` (= 9.87 bps). Units are decimal in all months tested
  (2023-01, 2024-01, 2025-01, 2021-05).

- **Bybit v5 API** (JSON, the archive source specified in Section 3):
  field `fundingRate` contains decimal fraction strings like
  `"0.00005491"`, `"-0.00001269"`. Units are decimal.

The detection heuristic is:

1. If `max_abs ≤ 0.05` and `median_abs ≤ 0.01` → classify as **decimal**,
   normalize by `× 10_000` to get bps.
2. If `max_abs > 1.0` → classify as **percent**, normalize by `× 100` to
   get bps.
3. If `median_abs ≤ 0.1` → classify as **percent**, normalize by `× 100`.
4. Otherwise → **unclassifiable**, stop with `FUNDING_UNIT_AMBIGUOUS`.

After normalization, if any value falls outside ±300 bps per settlement,
stop with `FUNDING_UNIT_AMBIGUOUS`.

Based on the archive data, all four series (Binance BTC, Binance ETH,
Bybit BTC, Bybit ETH) have rates in the range approximately ±0.001
(max ≈ 0.001 in extreme periods), which will correctly classify as decimal
by rule 1 and normalize to approximately ±10 bps per settlement — well
within the ±300 bps sanity band.

### A.3: Stage 8 verdict-assembly tracking (Q4, bug fix)

**Section:** 13 (verdict assembly)

**Ambiguity:** Rules 5, 6, and 7 refer to cells that "reached PASS_NULL" or
"reached PASS_PRE_NULL" — but by Stage 8, cells carry their final verdict
label (e.g., HOLDOUT_FAILED_DIAGNOSTIC, FDR_BLOCKED_DIAGNOSTIC), not the
intermediate PASS_NULL label they held after Stage 5. A "cell reached
PASS_NULL" is a statement about pipeline progress, not about the current
label.

**Resolution (2026-05-19):** Implementation uses two boolean tracking fields
on CellRecord — `reached_pass_pre_null` (set True in Stage 4) and
`reached_pass_null` (set True in Stage 5). These persist through relabeling
in Stages 6 and 7 via `dataclasses.replace()`. Stage 8 rules 5, 6, and 7
use these booleans to evaluate "cell reached X" conditions, not the
intermediate verdict labels. This correctly implements the precommitment
text and has been verified with tests for the mixed case (cell X survives
FDR then fails holdout; cell Y is FDR-blocked — Rule 5 fires, not Rule 6).
