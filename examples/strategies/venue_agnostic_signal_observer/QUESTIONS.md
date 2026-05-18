# QUESTIONS.md

Ambiguities and potential issues found in the cross-exchange funding dispersion carry precommitment document during implementation.

## Q1: Funding-unit detection heuristic — RESOLVED

**Section:** 7, step 2
**Status:** ✅ Resolved 2026-05-19

Archive-source inspection confirmed both sources return decimal fractions:

- Binance Vision CSV (`last_funding_rate` column): `0.00010000`, `0.00037409`,
  even extreme values like `0.00098653` (May 2021). Decimal across all tested
  months (2023-01, 2024-01, 2025-01, 2021-05).
- Bybit v5 API JSON (`fundingRate` field): `0.00005491`, `-0.00001269`. Decimal.

The implementation's heuristic (max ≤ 0.05, median ≤ 0.01 → decimal, ×10000 to bps)
correctly classifies all four series. Resolution written into the precommitment as
Appendix A.2, confirmed against archive sources (not live API).

## Q2: "Worst decile" definition — RESOLVED

**Section:** 12.1 (Stage 4 pre-null economic gates)
**Status:** ✅ Resolved 2026-05-19

Ruling: **p10 single value** (`sorted_nc[max(0, len(sorted_nc) // 10 - 1)]`).
This matches standard statistical usage and is the more conservative
interpretation. Resolution written into the precommitment as Appendix A.1.

## Q3: Convergence rate — diagnostic only, not specified

**Section:** 6 (cell grid) references convergence as a "diagnostic"
**Issue:** The precommitment mentions convergence rate as a diagnostic but doesn't define the convergence metric. The implementation records `convergence_rate=0.0` as a placeholder.

**No action needed.** Diagnostic-only, enters no gate, harmless placeholder.

## Q4: Stage 8 verdict rule 5/6 precedence — BUG FOUND AND FIXED

**Section:** 13 (verdict assembly)

The original implementation checked intermediate labels (`LABEL_PASS_NULL`) in
the cell_verdict field at Stage 8, but by that point cells have been relabeled
with their final verdict. Fixed by adding `reached_pass_null` and
`reached_pass_pre_null` boolean tracking fields on CellRecord. Resolution
written into the precommitment as Appendix A.3. Three new tests cover the
mixed case. 73 tests pass.

## Q5: Non-overlapping campaign counting — RESOLVED

**Section:** 4.6 (non-overlapping campaigns)

Conservative interpretation (same-settlement suppression) is correct. No
ambiguity in practice.

## Q6: Holdout split boundary — RESOLVED

**Section:** 3 / Stage 0

Split boundary belongs to holdout (train ends at split_date - 1). Conservative,
no ambiguity in practice.

## Q7: Block-shuffle secondary null diagnostic — not implemented

**Section:** 12.2 mentions a "block-shuffle secondary null" as "diagnostic only"

**No action needed.** Diagnostic-only, enters no gate, can be added later
without affecting any verdict.

## Q8: Gate A and Gate B — asset-level or per-cell?

**No ambiguity.** Gate A kills only if both assets fail. Gate B combines all
asset/threshold/N combos. Documenting for clarity.

## No silent deviations

I have not deviated from the precommitment on any frozen parameter, verdict
name, gate logic, null structure, FDR family size, cost model, window logic,
or cell grid definition. All ambiguities have been resolved and written into
the precommitment document as Appendix A.