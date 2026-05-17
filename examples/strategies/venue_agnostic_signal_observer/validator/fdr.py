"""
False Discovery Rate (FDR) control for Edge Miner grid-swept p-values.

Default method: Benjamini-Yekutieli (BY, 2001).
Reason: Edge Miner evaluates many overlapping cells across shared captures,
shared horizons, shared source/target streams, and shared forward-return
windows. These p-values are correlated, not independent. BY controls FDR
under arbitrary dependence. BH (1995) is available only when independence
or PRDS-style positive-regression dependence is defensible and explicitly
selected by the caller.

BY uses the harmonic correction factor c(m) = sum(1/i for i in 1..m), where
m is the primary family size (diagnostic rows excluded from denominator).

References:
  Benjamini & Hochberg (1995). "Controlling the False Discovery Rate: A
    Practical and Powerful Approach to Multiple Testing." JRSS-B 57(1).
  Benjamini & Yekutieli (2001). "The Control of the False Discovery Rate in
    Multiple Testing under Dependency." Ann. Statist. 29(4).

No TRADE_READY status is produced here.
No filesystem writes, no network, no ledger, no subprocess.
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Sequence

from .metadata import EstimatorMetadata, make_metadata


ESTIMATOR_NAME = "fdr"
ESTIMATOR_VERSION = "1.0.0"

VALID_METHODS = frozenset({"BY", "BH"})
VALID_ROW_KINDS = frozenset({"primary", "diagnostic"})

_BY_DEPENDENCE_NOTE = (
    "Benjamini-Yekutieli (2001): controls FDR under arbitrary dependence. "
    "Default for Edge Miner grid-swept correlated p-values."
)
_BH_DEPENDENCE_NOTE = (
    "Benjamini-Hochberg (1995): controls FDR under independence or PRDS. "
    "Caller asserts that independence or positive-regression dependence holds."
)


@dataclass
class FDRRow:
    label: str | None
    row_kind: str          # "primary" | "diagnostic"
    p_value: float
    adjusted_p_value: float | None  # None for diagnostic rows
    rejected: bool | None          # None for diagnostic rows


@dataclass
class FDRResult:
    method: str
    alpha: float
    dependence_note: str
    primary_row_count: int
    diagnostic_row_count: int
    family_size: int           # == primary_row_count
    rejected_count: int
    harmonic_correction: float | None  # c(m) for BY; None for BH
    status: str                # "OK" | "INSUFFICIENT_DATA"
    rows: list[FDRRow]
    estimator_metadata: EstimatorMetadata
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["estimator_metadata"] = self.estimator_metadata.to_dict()
        return d


# ---------------------------------------------------------------------------
# Internal math
# ---------------------------------------------------------------------------

def _harmonic(m: int) -> float:
    """c(m) = sum(1/i for i in 1..m). Returns 1.0 for m <= 1."""
    if m <= 1:
        return 1.0
    return sum(1.0 / i for i in range(1, m + 1))


def _validate_pvalues(pvalues: Sequence[float]) -> None:
    for i, p in enumerate(pvalues):
        if math.isnan(p):
            raise ValueError(f"p-value at index {i} is NaN")
        if math.isinf(p):
            raise ValueError(f"p-value at index {i} is infinite")
        if p < 0.0 or p > 1.0:
            raise ValueError(
                f"p-value at index {i} is {p!r}: must be in [0.0, 1.0]"
            )


def _bh_adjusted(sorted_pvalues: list[float], m: int) -> list[float]:
    """Step-up BH adjusted p-values for m already-sorted (ascending) p-values.

    Returns adjusted p-values in the same sorted order.
    adj[i] = min(p[i] * m / (i+1), 1.0), enforced monotone from the top.
    """
    n = len(sorted_pvalues)
    adj = [min(sorted_pvalues[i] * m / (i + 1), 1.0) for i in range(n)]
    # Enforce monotonicity: step-up means adj[i] <= adj[i+1]
    for i in range(n - 2, -1, -1):
        if adj[i] > adj[i + 1]:
            adj[i] = adj[i + 1]
    return adj


def _compute_adjusted(
    primary_pvalues: list[float],
    method: str,
) -> tuple[list[float], float | None]:
    """Return (adjusted_pvalues_in_original_order, harmonic_correction_or_None)."""
    m = len(primary_pvalues)
    if m == 0:
        return [], None

    harmonic = None
    effective_m = m

    if method == "BY":
        harmonic = _harmonic(m)
        effective_m_float = m * harmonic
        # Scale p-values for BH step with effective denominator
        # BY: treat as BH on p * c(m), then cap at 1.0 and enforce monotone
        sorted_idx = sorted(range(m), key=lambda i: primary_pvalues[i])
        sorted_p = [primary_pvalues[i] for i in sorted_idx]
        # Adjusted: p[i] * m * c(m) / (i+1), monotone enforced
        adj_sorted = [
            min(sorted_p[i] * effective_m_float / (i + 1), 1.0)
            for i in range(m)
        ]
        for i in range(m - 2, -1, -1):
            if adj_sorted[i] > adj_sorted[i + 1]:
                adj_sorted[i] = adj_sorted[i + 1]
        # Map back to original order
        adj = [0.0] * m
        for rank, orig in enumerate(sorted_idx):
            adj[orig] = adj_sorted[rank]
        return adj, harmonic

    else:  # BH
        sorted_idx = sorted(range(m), key=lambda i: primary_pvalues[i])
        sorted_p = [primary_pvalues[i] for i in sorted_idx]
        adj_sorted = _bh_adjusted(sorted_p, m)
        adj = [0.0] * m
        for rank, orig in enumerate(sorted_idx):
            adj[orig] = adj_sorted[rank]
        return adj, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_fdr(
    p_values: Sequence[float],
    alpha: float = 0.05,
    method: str = "BY",
    labels: Sequence[str | None] | None = None,
    row_kinds: Sequence[str] | None = None,
    estimator_metadata: EstimatorMetadata | None = None,
    input_metadata: dict[str, Any] | None = None,
) -> FDRResult:
    """Compute FDR-adjusted p-values for a family of hypothesis tests.

    Parameters
    ----------
    p_values:
        Raw p-values. Must all be in [0.0, 1.0]. NaN and inf raise.
    alpha:
        FDR threshold (q-level). Default 0.05.
    method:
        "BY" (default) or "BH". BY is the correct default for Edge Miner
        grid-swept correlated p-values (see module docstring).
    labels:
        Optional label/cell-ID per row. Length must match p_values if given.
    row_kinds:
        Optional kind per row: "primary" or "diagnostic".
        If omitted, all rows are treated as "primary".
        Diagnostic rows remain visible in output but do not enter the primary
        FDR denominator and are never marked rejected.
    """
    # --- Validate method ---
    if method not in VALID_METHODS:
        raise ValueError(
            f"Unknown FDR method {method!r}. Valid methods: {sorted(VALID_METHODS)}"
        )

    meta = estimator_metadata or make_metadata(
        ESTIMATOR_NAME,
        ESTIMATOR_VERSION,
        config={"method": method, "alpha": alpha},
    )

    plist = list(p_values)
    n_total = len(plist)

    # --- Validate p-values ---
    _validate_pvalues(plist)

    # --- Validate / default labels ---
    if labels is not None:
        llist = list(labels)
        if len(llist) != n_total:
            raise ValueError(
                f"labels length {len(llist)} does not match p_values length {n_total}"
            )
    else:
        llist = [None] * n_total

    # --- Validate / default row_kinds ---
    if row_kinds is not None:
        klist = list(row_kinds)
        if len(klist) != n_total:
            raise ValueError(
                f"row_kinds length {len(klist)} does not match p_values length {n_total}"
            )
        for i, k in enumerate(klist):
            if k not in VALID_ROW_KINDS:
                raise ValueError(
                    f"Unknown row_kind {k!r} at index {i}. "
                    f"Valid kinds: {sorted(VALID_ROW_KINDS)}"
                )
    else:
        # Default: all primary
        klist = ["primary"] * n_total

    # --- Split primary / diagnostic ---
    primary_indices = [i for i, k in enumerate(klist) if k == "primary"]
    diagnostic_indices = [i for i, k in enumerate(klist) if k == "diagnostic"]

    primary_pvalues = [plist[i] for i in primary_indices]
    primary_row_count = len(primary_pvalues)
    diagnostic_row_count = len(diagnostic_indices)

    dependence_note = _BY_DEPENDENCE_NOTE if method == "BY" else _BH_DEPENDENCE_NOTE

    # --- Insufficient data ---
    if primary_row_count == 0:
        rows = [
            FDRRow(
                label=llist[i],
                row_kind=klist[i],
                p_value=float(plist[i]),
                adjusted_p_value=None,
                rejected=None,
            )
            for i in range(n_total)
        ]
        return FDRResult(
            method=method,
            alpha=float(alpha),
            dependence_note=dependence_note,
            primary_row_count=0,
            diagnostic_row_count=diagnostic_row_count,
            family_size=0,
            rejected_count=0,
            harmonic_correction=None,
            status="INSUFFICIENT_DATA",
            rows=rows,
            estimator_metadata=meta,
            error_message="No primary p-values provided",
        )

    # --- Compute adjusted p-values for primary rows ---
    adj_primary, harmonic = _compute_adjusted(primary_pvalues, method)

    # --- Build per-row results ---
    # Map primary adjusted back to full row list
    adj_map: dict[int, float] = {}
    for rank, orig_idx in enumerate(primary_indices):
        adj_map[orig_idx] = float(adj_primary[rank])

    rows: list[FDRRow] = []
    rejected_count = 0
    for i in range(n_total):
        kind = klist[i]
        p = float(plist[i])
        if kind == "primary":
            adj = adj_map[i]
            rej = bool(adj <= alpha)
            if rej:
                rejected_count += 1
            rows.append(FDRRow(
                label=llist[i],
                row_kind=kind,
                p_value=p,
                adjusted_p_value=adj,
                rejected=rej,
            ))
        else:
            rows.append(FDRRow(
                label=llist[i],
                row_kind=kind,
                p_value=p,
                adjusted_p_value=None,
                rejected=None,
            ))

    return FDRResult(
        method=method,
        alpha=float(alpha),
        dependence_note=dependence_note,
        primary_row_count=primary_row_count,
        diagnostic_row_count=diagnostic_row_count,
        family_size=primary_row_count,
        rejected_count=rejected_count,
        harmonic_correction=float(harmonic) if harmonic is not None else None,
        status="OK",
        rows=rows,
        estimator_metadata=meta,
    )
