# Precommitment Resolution Appendix

Three ambiguities identified during implementation require human decisions
before the precommitment is frozen and the study runs. This document records
each decision point, the implementation's current default, and the action
needed to close it.

---

## R1: Funding-unit detection heuristic (Q1)

### What the precommitment says

Section 7, step 2: "detect the source unit and convert to bps per settlement.
If a series cannot be confidently classified as decimal vs percent, the run
stops with verdict FUNDING_UNIT_AMBIGUOUS." The ±300 bps per-settlement
sanity band provides a hard backstop, but the doc does not specify the
*detection heuristic* — the rule that decides "this series is decimal" vs
"this series is percent" vs "this series is unclassifiable."

### Why a human must decide

The heuristic determines the normalization factor (×10_000 for decimal,
×100 for percent). A misclassification by a factor of 100 is caught by the
±300 bps band, but a subtler mismatch near the boundary (a series with
max ≈ 0.05) could normalize correctly or incorrectly depending on the rule.
This cannot be resolved in the abstract — it needs eyes on the actual archive
data.

### Current implementation default

```python
# funding_dispersion_carry.py, function detect_funding_unit()
if max_abs <= 0.05 and median_abs <= 0.01:
    return "decimal"      # normalize ×10,000
elif max_abs > 1.0:
    return "percent"      # normalize ×100
elif median_abs <= 0.1:
    return "percent"      # likely a low-rate percent series
else:
    return "unknown"      # → FUNDING_UNIT_AMBIGUOUS stop
```

The ±300 bps post-normalization sanity check runs after normalization and
will catch any misclassification that pushes a value outside that band.

### Action needed before freeze

1. Pull the first ~20 rows of each funding archive (Binance BTC, Binance ETH,
   Bybit BTC, Bybit ETH).
2. Eyeball the rate column: are values like `0.0001` (decimal) or `0.01%`
   (percent)?
3. Confirm the heuristic above matches reality, or write a replacement that
   does. Write the confirmed heuristic into the precommitment as a resolved
   ambiguity.

**Time estimate:** 5 minutes once you have the archive files.

---

## R2: Worst-decile definition (Q2)

### What the precommitment says

Section 12.1 (Stage 4, pre-null economic gates), rule 3c:
"worst decile net_carry_bps > −50". The word "decile" is ambiguous:
it could mean (a) the single value at the 10th percentile (p10), or
(b) the average of the bottom 10% of observations.

### Why a human must decide

These are different numbers for any non-trivial sample. For a cell with
55 campaigns, p10 is the value at index 5 (sorted ascending), while the
bottom-decile mean averages indices 0–4. A borderline cell with p10 = −48
but bottom-decile-mean = −55 would *pass* under interpretation (a) but
*fail* under interpretation (b). This directly changes verdicts at Gate B
and the pre-null economic gates.

### Current implementation default

```python
# funding_dispersion_stages.py, _worst_decile()
sorted_nc = sorted(net_carry_values)
decile_idx = max(0, len(sorted_nc) // 10 - 1)
worst_decile = sorted_nc[decile_idx]  # p10 single value
```

This is interpretation (a): the 10th-percentile single value.

### Action needed before freeze

Pick one:

- **Option A (p10 single value):** No code change needed. Write into the
  precommitment: "Worst decile means the value at the 10th percentile index
  (p10), not the mean of the bottom 10%." This is the current default.

- **Option B (bottom-decile mean):** Replace the `_worst_decile` function
  with the mean of the bottom 10% of sorted values. Write into the
  precommitment: "Worst decile means the arithmetic mean of the bottom 10%
  of campaigns."

Write the chosen resolution into the precommitment as a one-line addition
to Section 12.1.

**Time estimate:** 30 seconds to choose, 1 minute to write the line.

---

## R3: Stage 8 verdict-assembly rule precedence (Q4)

### What the precommitment says

Section 13 defines ten ordered rules. The critical interaction is between
rules 5 and 6:

- **Rule 5:** ≥1 cell survived BY FDR (Stage 6) but every such cell ended as
  `HOLDOUT_FAILED_DIAGNOSTIC` → study verdict `HOLDOUT_FAILED_DIAGNOSTIC`.

- **Rule 6:** ≥1 cell reached `PASS_NULL` but every such cell ended as
  `FDR_BLOCKED_DIAGNOSTIC` → study verdict `FDR_BLOCKED_DIAGNOSTIC`.

### The potential interaction

Consider a mixed case: cell X survives FDR then fails holdout (final label:
`HOLDOUT_FAILED_DIAGNOSTIC`); cell Y reaches `PASS_NULL` but is FDR-blocked
(final label: `FDR_BLOCKED_DIAGNOSTIC`).

- Rule 5 checks: "some cells survived FDR?" → yes (cell X). "Every FDR
  survivor ended as HOLDOUT_FAILED?" → yes (cell X did). → Rule 5 fires,
  verdict = `HOLDOUT_FAILED_DIAGNOSTIC`.

- Rule 6 would check: "some cells reached PASS_NULL?" → yes (both X and Y
  did, before their respective later stages). "Every such cell ended as
  FDR_BLOCKED?" → no (cell X ended as HOLDOUT_FAILED, not FDR_BLOCKED). →
  Rule 6 does NOT fire.

This is actually **correct** per the doc's text. The doc says Rule 6 fires
only when *every* cell that reached PASS_NULL ended as FDR_BLOCKED. In the
mixed case, cell X reached PASS_NULL but ended as HOLDOUT_FAILED, so the
"every such cell" condition fails. Rule 5 fires first (it's ordered earlier)
and produces the more informative verdict.

### What the implementation actually does

```python
# Rule 4 (doc rule 4):
if VERDICT_ARCHIVE_CANDIDATE in verdicts:
    return VERDICT_ARCHIVE_CANDIDATE, None

# Rule 5 (doc rule 5):
fdr_survivors = any(c.fdr_survived is True for c in cells)
if fdr_survivors and all(
    c.cell_verdict == VERDICT_HOLDOUT_FAILED
    for c in cells if c.fdr_survived is True
):
    return VERDICT_HOLDOUT_FAILED, None

# Rule 6 (doc rule 6):
if LABEL_PASS_NULL in verdicts or VERDICT_FDR_BLOCKED in verdicts:
    fdr_blocked_cells = [c for c in cells if c.cell_verdict == VERDICT_FDR_BLOCKED]
    if fdr_blocked_cells and not any(c.cell_verdict == VERDICT_ARCHIVE_CANDIDATE for c in cells):
        return VERDICT_FDR_BLOCKED, None
```

### Code-vs-doc discrepancy found

The Rule 6 implementation is **buggy relative to the doc text**. The doc says:

> "≥1 cell reached PASS_NULL but every such cell ended as FDR_BLOCKED_DIAGNOSTIC"

Meaning: the condition is about cells that *reached* PASS_NULL (regardless
of their final label) — and *all* of those must have ended as FDR_BLOCKED.
But the implementation checks:

1. Whether `LABEL_PASS_NULL` or `VERDICT_FDR_BLOCKED` appears in the
   cell-verdict set. But by Stage 8, cells that were `PASS_NULL` at Stage 5
   have been relabeled — they carry their final verdict (`FDR_BLOCKED`,
   `HOLDOUT_FAILED`, or `ARCHIVE_CANDIDATE`), not the intermediate label
   `PASS_NULL`.

2. A weaker condition (`fdr_blocked_cells` exist and no `ARCHIVE_CANDIDATE`),
   which fires even when some PASS_NULL cells ended as HOLDOUT_FAILED —
   contradicting the doc's "every such cell ended as FDR_BLOCKED" requirement.

**In practice**, Rule 5 fires first in the mixed case, so the bug only
matters when there are *no* FDR survivors (all FDR-blocked) but also no
HOLDOUT_FAILED cells — which is the pure FDR_BLOCKED case. In that case
the implementation *does* produce the correct verdict. But the logic path
is wrong and could produce incorrect results if the rule ordering or the
early-exit conditions change.

### Identified bugs

There are actually two issues:

1. **Intermediate labels are not preserved.** By Stage 8, cells carry their
   *final* verdict (`FDR_BLOCKED_DIAGNOSTIC`, `HOLDOUT_FAILED_DIAGNOSTIC`,
   `ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION`), not the intermediate label
   `PASS_NULL`. The doc's Rule 6 says "reached PASS_NULL" — but that
   information is lost after Stage 6 replaces the label. The implementation
   tries to recover it by checking for `LABEL_PASS_NULL in verdicts`, which
   will never be True at Stage 8 because Stage 6 has already relabeled all
   PASS_NULL cells. It also checks `VERDICT_FDR_BLOCKED in verdicts`, which
   *is* present, but doesn't correctly implement "every cell that reached
   PASS_NULL ended as FDR_BLOCKED."

2. **Rule 6's condition is too weak.** The implementation checks
   `fdr_blocked_cells exist AND no ARCHIVE_CANDIDATE`. This would fire even
   when some PASS_NULL cells ended as HOLDOUT_FAILED, as long as at least one
   cell is FDR_BLOCKED. But the doc says Rule 6 fires only when *every*
   PASS_NULL cell ended as FDR_BLOCKED.

### Required fix

The `CellRecord` needs a field that records whether a cell ever reached
`PASS_NULL` (the Stage 5 label), so that Stage 8 can correctly evaluate
the doc's "cell reached PASS_NULL" condition. Adding a boolean
`reached_pass_null: bool = False` to `CellRecord` and setting it in Stage 5
when a cell passes the null test would resolve both issues.

The Stage 8 Rule 6 implementation should then be:

```python
# Rule 6: ≥1 cell reached PASS_NULL, and every such cell ended as FDR_BLOCKED_DIAGNOSTIC
cells_that_reached_pass_null = [c for c in cells if c.reached_pass_null]
if cells_that_reached_pass_null and all(
    c.cell_verdict == VERDICT_FDR_BLOCKED for c in cells_that_reached_pass_null
):
    return VERDICT_FDR_BLOCKED, None
```

Similarly, Rule 7 should use a `reached_pass_pre_null` boolean:

```python
# Rule 7: ≥1 cell reached PASS_PRE_NULL, and every such cell ended as NULL_REJECTED_DIAGNOSTIC
cells_that_reached_pre_null = [c for c in cells if c.reached_pass_pre_null]
if cells_that_reached_pre_null and all(
    c.cell_verdict == VERDICT_NULL_REJECTED for c in cells_that_reached_pre_null
):
    return VERDICT_NULL_REJECTED, None
```

### Action needed before freeze

1. Add `reached_pass_null: bool` and `reached_pass_pre_null: bool` fields to
   `CellRecord` (with default `False`).
2. Set `reached_pass_null = True` in Stage 5 for cells that pass the null
   test (currently relabeled to `LABEL_PASS_NULL` or `VERDICT_FDR_BLOCKED`).
3. Set `reached_pass_pre_null = True` in Stage 4 for cells that pass
   pre-null gates (currently labeled `LABEL_PASS_PRE_NULL`).
4. Rewrite Stage 8 Rules 5, 6, 7 to use `reached_pass_null` and
   `reached_pass_pre_null` instead of checking `cell_verdict` in the
   intermediate-label set.
5. Add tests for the mixed case (cell X = FDR survivor → HOLDOUT_FAILED,
   cell Y = not FDR survivor → FDR_BLOCKED) and verify that Rule 5 fires
   (not Rule 6).
6. After code fixes, re-verify all 10 verdict-assembly tests against the
   doc's rule list.

This is a real bug, not just an ambiguity. It must be fixed before the
freeze.

**Time estimate:** 20–30 minutes for the fix and test updates.

---

## Freeze checklist

The study cannot run until all three resolutions are complete:

- [ ] R1: Human has inspected the archive data and confirmed or revised the
      funding-unit heuristic. Resolution written into the precommitment.
- [ ] R2: Human has chosen p10 vs bottom-decile-mean. Resolution written
      into the precommitment.
- [ ] R3: Code bug fixed (add `reached_pass_null` / `reached_pass_pre_null`
      fields, rewrite Stage 8 rules 5–7). All tests pass. Code matches doc.

After all three are done, commit the precommitment with these resolutions
appended, then ungate the `load_archive_data()` function and run the study.