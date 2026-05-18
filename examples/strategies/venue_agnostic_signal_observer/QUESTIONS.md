# QUESTIONS.md

Ambiguities and potential issues found in the cross-exchange funding dispersion carry precommitment document during implementation.

## Q1: Funding-unit detection heuristic not specified

**Section:** 7, step 2
**Issue:** The precommitment says "detect the source unit and convert to bps per settlement" and "if a series cannot be confidently classified as decimal vs percent, the run stops with verdict FUNDING_UNIT_AMBIGUOUS." However, it does not specify the detection heuristic. What scoring threshold separates "confident" from "unconfident" classification?

**Implementation choice:** I used a heuristic based on max absolute value: if max ≤ 0.05 and median ≤ 0.01, classify as "decimal"; if max > 1.0, classify as "percent"; otherwise, use median-based disambiguation. Values that can't be classified return "unknown" which triggers FUNDING_UNIT_AMBIGUOUS. This is reasonable but not specified in the doc.

**Risk:** Different heuristics could classify the same data differently. The ±300 bps sanity band provides a backstop (a misclassified series will likely flag), but a marginal series near 0.05 could go either way.

**Resolution needed before freeze:** Human must inspect the first ~20 rows of each funding archive and confirm or revise the heuristic. See `docs/PRECOMMITMENT_RESOLUTIONS.md` R1.

## Q2: "Worst decile" definition — p10 or bottom 10th percentile?

**Section:** 12.1 (Stage 4 pre-null economic gates)
**Issue:** The precommitment says "worst decile net_carry_bps > −50" but doesn't specify whether "worst decile" means the 10th percentile (single value) or the average of the bottom 10% of campaigns. The implementation takes the single value at the 10th-percentile index.

**Implementation choice:** `sorted_nc[max(0, len(sorted_nc) // 10 - 1)]` — the p10 value, not the mean of the bottom decile.

**Resolution needed before freeze:** Human must choose p10 (single value) or bottom-decile-mean. See `docs/PRECOMMITMENT_RESOLUTIONS.md` R2.

## Q3: Convergence rate — diagnostic only, not specified

**Section:** 6 (cell grid) references convergence as a "diagnostic"
**Issue:** The precommitment mentions convergence rate as a diagnostic but doesn't define the convergence metric. The implementation records `convergence_rate=0.0` as a placeholder. This is correct per the spec (diagnostic only, never enters a gate), but it's an incomplete data field.

**Implementation choice:** Left as 0.0 placeholder. The convergence diagnostic is explicitly scoped out of the measurement — it's recorded but doesn't affect any gate.

**No action needed.** Diagnostic-only, enters no gate, harmless placeholder.

## Q4: Stage 8 verdict rule 5/6 precedence — BUG FOUND AND FIXED

**Section:** 13 (verdict assembly)
**Issue:** The precommitment's rule 5 says "≥1 cell survived FDR but every such cell ended as HOLDOUT_FAILED_DIAGNOSTIC" and rule 6 says "≥1 cell reached PASS_NULL but every such cell ended as FDR_BLOCKED_DIAGNOSTIC." In a mixed case where cell X survived FDR then failed holdout (final: HOLDOUT_FAILED), and cell Y was FDR-blocked (final: FDR_BLOCKED), rule 5 correctly fires because its condition is met. But the original implementation of rule 6 checked `LABEL_PASS_NULL in verdicts or VERDICT_FDR_BLOCKED in verdicts` — which was wrong because by Stage 8, cells no longer carry the intermediate label PASS_NULL (it has been overwritten by their final verdict).

**Fix applied:** Added `reached_pass_null: bool` and `reached_pass_pre_null: bool` fields to `CellRecord`. These booleans are set in Stages 4 and 5 respectively and persist through relabeling in Stages 6 and 7. Stage 8 rules 5, 6, and 7 now use these boolean flags instead of checking intermediate label strings in the cell_verdict field. This correctly implements the doc's "cell reached X" language, which refers to pipeline progress, not current label.

**Tests added:** `test_mixed_holdout_failed_and_fdr_blocked_rule5_wins`, `test_rule6_fires_when_all_pass_null_cells_are_fdr_blocked`, `test_rule6_does_not_fire_if_pass_null_cell_progressed`, `test_failed_cell_does_not_set_reached_pass_pre_null`. All 73 tests pass.

**See also:** `docs/PRECOMMITMENT_RESOLUTIONS.md` R3 for the full code-vs-doc diff and checklist.

## Q5: Non-overlapping campaign counting — what counts as overlapping?

**Section:** 4.6 (non-overlapping campaigns)
**Issue:** The precommitment says "primary evaluation suppresses new events in a cell until the open hold ends" but doesn't specify whether the entry settlement itself is also suppressed. In other words, if event A starts at settlement t and another event B also fires at settlement t (same threshold crossing), does B get suppressed?

**Implementation choice:** Entry at the same settlement as an existing campaign IS suppressed (since it falls within the hold window that starts at t). This is the conservative interpretation.

**No action needed.** The conservative interpretation is correct; two campaigns cannot open on the same settlement under a unit-size model.

## Q6: Holdout split boundary — exclusive on train side

**Section:** 3 / Stage 0
**Issue:** The precommitment says "70/30 chronological split" but doesn't specify whether the split boundary settlement belongs to train or holdout.

**Implementation choice:** The split boundary settlement belongs to holdout (train ends at split_date - 1). This keeps the 70/30 split exact and avoids any settlement appearing in both sets.

**No action needed.** Conservative interpretation, no ambiguity in practice.

## Q7: Block-shuffle secondary null diagnostic — not implemented

**Section:** 12.2 mentions a "block-shuffle secondary null" as "diagnostic only"
**Issue:** The precommitment describes a block-shuffle null as a secondary diagnostic but doesn't fully specify it (block size, seed, iterations). The primary spec says "1000 iterations, alpha 0.05, seed 42" for the event-vector shift.

**Implementation choice:** Block-shuffle null is not implemented. It's explicitly labeled "diagnostic only" and "enters no gate," so omitting it does not affect any verdict. If needed later, it can be added without changing any gate or verdict logic.

**No action needed.** Diagnostic-only, enters no gate, can be added later without affecting any verdict.

## Q8: Gate A and Gate B — asset-level or per-cell?

**Section:** 8 (Gate A) and 9 (Gate B)
**Issue:** Gate A checks "both assets are below 50 events at every threshold." Gate B evaluates "(threshold, N) combos." The precommitment specifies Gate A kills only if BOTH assets fail, and Gate B evaluates across all assets. The implementation follows this — Gate A is per-asset (exit only if both fail), Gate B combines all asset/threshold/N combos.

**No ambiguity on this point**, documenting for clarity.

## No silent deviations

I have not deviated from the precommitment on any frozen parameter, verdict name, gate logic, null structure, FDR family size, cost model, window logic, or cell grid definition. The ambiguities listed above are the only places where the doc left room for interpretation, and each is documented with the implementation choice made. The bug in Q4 has been fixed and verified with additional tests.