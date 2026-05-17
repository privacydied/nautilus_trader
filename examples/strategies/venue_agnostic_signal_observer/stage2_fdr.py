#!/usr/bin/env python3
"""
Stage 2 FDR correction — BH and BY on native permutation p-values.

Applies the precommitted FDR rules to discovery-set native permutation
p-values.  Reads required dimensions from stage2_precommitment.json.

BH implementation:
- Sort p-values ascending.
- For m tests and rank i, threshold is i*q/m.
- Apply the standard step-up procedure.

BY implementation:
- c(m) = sum(1/i for i in 1..m)
- For m tests and rank i, threshold is i*q/(m*c(m)).
- Apply the standard step-up procedure.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .stage2_precommitment_utils import load_precommitment, get_test_family_dimensions
from .run_artifacts import atomic_write_json


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _bh_threshold(rank: int, m: int, q: float) -> float:
    """Return BH critical value for rank i."""
    return (rank * q) / m


def _by_c_sum(m: int) -> float:
    """Compute c(m) = sum(1/i for i in 1..m)."""
    return sum(1.0 / i for i in range(1, m + 1))


def _by_threshold(rank: int, m: int, q: float) -> float:
    """Return BY critical value for rank i."""
    c_m = _by_c_sum(m)
    return (rank * q) / (m * c_m)


def _step_up(p_values: list[float], thresholds: list[float]) -> int:
    """Standard step-up procedure.

    Returns the largest rank i where p[i] <= threshold[i].
    Returns -1 if none meet the criterion (all rejected).
    """
    for i in range(len(p_values) - 1, -1, -1):
        if p_values[i] <= thresholds[i]:
            return i
    return -1


def _fdr_correct(
    test_results: list[dict[str, Any]],
    q: float,
    method: str,
) -> list[dict[str, Any]]:
    """Apply BH or BY FDR correction to test results.

    Each element in test_results must have 'p_value' and dimension keys.
    Mutates elements in-place to add 'rejected' and 'threshold' fields.
    """
    # Sort by p-value ascending
    sorted_results = sorted(test_results, key=lambda r: r.get("p_value", 1.0))
    m = len(sorted_results)

    if m == 0:
        return sorted_results

    # Compute thresholds
    if method == "benjamini_hochberg":
        thresholds = [_bh_threshold(i + 1, m, q) for i in range(m)]
    elif method == "benjamini_yekutieli":
        thresholds = [_by_threshold(i + 1, m, q) for i in range(m)]
    else:
        raise ValueError(f"Unknown FDR method: {method}")

    # Step-up
    reject_until = _step_up(
        [r.get("p_value", 1.0) for r in sorted_results], thresholds
    )

    for i, r in enumerate(sorted_results):
        if reject_until >= 0 and i <= reject_until:
            r["rejected"] = True
        else:
            r["rejected"] = False
        r["fdr_threshold"] = round(thresholds[i], 6)
        r["fdr_method"] = method
        r["fdr_q"] = q

    # Return in original order (sort back)
    index_map = {id(item): item for item in test_results}
    # Better: track original index
    for i, r in enumerate(sorted_results):
        r["_original_index"] = test_results.index(r) if r in test_results else i

    return sorted_results


def run_fdr(
    pvalue_data: list[dict[str, Any]],
    required_dimensions: list[str],
    primary_q: float = 0.10,
    sensitivity_q: float = 0.10,
    pvalue_source: str = "native_permutation",
) -> dict[str, Any]:
    """Run BH and BY FDR correction on p-value data.

    Parameters
    ----------
    pvalue_data : list[dict]
        Each dict must have dimension keys matching required_dimensions
        and a 'p_value' key from the native permutation source.
    required_dimensions : list[str]
        The expected grouping dimensions (from stage2_precommitment.json).
    primary_q : float
        BH q-value.
    sensitivity_q : float
        BY q-value.
    pvalue_source : str
        Required p-value source identifier.

    Returns
    -------
    dict with keys: fdr_created_at, primary_result, sensitivity_result,
    rejected_bh_count, accepted_bh_count, rejected_by_count,
    accepted_by_count, errors.
    """
    errors: list[str] = []

    # Validate dimensions
    if not pvalue_data:
        return {"status": "NO_DATA", "errors": ["No p-value data provided"]}

    # Check every row has required dimensions
    missing_dims: set[str] = set()
    missing_pvalues: int = 0
    non_native_source: int = 0

    for row in pvalue_data:
        for dim in required_dimensions:
            if dim not in row or row[dim] is None:
                missing_dims.add(dim)
        # Check p-value
        pv = row.get("p_value")
        if pv is None:
            missing_pvalues += 1
        # Check source
        source = row.get("pvalue_source") or row.get("source")
        if source is not None and source != pvalue_source:
            non_native_source += 1

    if missing_dims:
        errors.append(
            f"Missing required dimensions: {sorted(missing_dims)}"
        )
    if missing_pvalues > 0:
        errors.append(
            f"{missing_pvalues} row(s) missing native permutation p-values"
        )
    if non_native_source > 0:
        errors.append(
            f"{non_native_source} row(s) have non-native-permutation p-value source; "
            "FDR requires native_permutation"
        )

    if errors:
        return {
            "fdr_created_at": _ts_now_iso(),
            "status": "FDR_FAILED",
            "errors": errors,
            "primary_result": None,
            "sensitivity_result": None,
        }

    # Run BH
    primary = _fdr_correct(
        [dict(r) for r in pvalue_data],
        q=primary_q,
        method="benjamini_hochberg",
    )

    # Run BY
    sensitivity = _fdr_correct(
        [dict(r) for r in pvalue_data],
        q=sensitivity_q,
        method="benjamini_yekutieli",
    )

    bh_rejected = sum(1 for r in primary if r.get("rejected"))
    by_rejected = sum(1 for r in sensitivity if r.get("rejected"))

    return {
        "fdr_created_at": _ts_now_iso(),
        "status": "FDR_COMPLETED",
        "errors": [],
        "primary_fdr_method": "benjamini_hochberg",
        "primary_fdr_q": primary_q,
        "sensitivity_fdr_method": "benjamini_yekutieli",
        "sensitivity_fdr_q": sensitivity_q,
        "pvalue_source": pvalue_source,
        "total_tests": len(pvalue_data),
        "rejected_bh_count": bh_rejected,
        "accepted_bh_count": len(pvalue_data) - bh_rejected,
        "rejected_by_count": by_rejected,
        "accepted_by_count": len(pvalue_data) - by_rejected,
        "primary_result": primary,
        "sensitivity_result": sensitivity,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 2 FDR correction: apply BH and BY to "
                    "native permutation p-values."
    )
    p.add_argument(
        "--pvalue-file", type=str, required=True,
        help="Path to JSON file containing array of p-value results",
    )
    p.add_argument(
        "--out-dir", type=str, default=None,
        help="Output directory (default: reports/stage2_fdr)",
    )
    p.add_argument(
        "--primary-q", type=float, default=0.10,
        help="BH q-value (default: 0.10)",
    )
    p.add_argument(
        "--sensitivity-q", type=float, default=0.10,
        help="BY q-value (default: 0.10)",
    )
    p.add_argument(
        "--precommit-path", type=str, default=None,
        help="Path to stage2_precommitment.json (default: auto-discover)",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    precommit = load_precommitment(
        Path(args.precommit_path) if args.precommit_path else None
    )
    required_dimensions = get_test_family_dimensions(precommit)

    # Read p-value data
    pvalue_path = Path(args.pvalue_file).resolve()
    if not pvalue_path.exists():
        print(f"ERROR: p-value file not found: {pvalue_path}", file=sys.stderr)
        sys.exit(1)

    with open(pvalue_path, encoding="utf-8") as f:
        pvalue_data: list[dict[str, Any]] = json.load(f)

    if not isinstance(pvalue_data, list):
        print("ERROR: p-value file must contain a JSON array", file=sys.stderr)
        sys.exit(1)

    # Run FDR
    result = run_fdr(
        pvalue_data=pvalue_data,
        required_dimensions=required_dimensions,
        primary_q=args.primary_q,
        sensitivity_q=args.sensitivity_q,
        pvalue_source="native_permutation",
    )

    # Output
    root = Path.cwd().resolve()
    out_dir = Path(args.out_dir) if args.out_dir else root / "reports" / "stage2_fdr"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write main result
    result_path = out_dir / "stage2_fdr_result.json"
    atomic_write_json(result_path, result)

    # Write BH and BY separately for downstream consumption
    if result.get("primary_result"):
        bh_path = out_dir / "stage2_fdr_bh.json"
        atomic_write_json(bh_path, {
            "method": "benjamini_hochberg",
            "q": args.primary_q,
            "results": result["primary_result"],
        })

    if result.get("sensitivity_result"):
        by_path = out_dir / "stage2_fdr_by.json"
        atomic_write_json(by_path, {
            "method": "benjamini_yekutieli",
            "q": args.sensitivity_q,
            "results": result["sensitivity_result"],
        })

    print(f"  FDR status:      {result.get('status', '?')}")
    print(f"  Total tests:     {result.get('total_tests', 0)}")
    print(f"  BH rejected:     {result.get('rejected_bh_count', 0)}")
    print(f"  BH accepted:     {result.get('accepted_bh_count', 0)}")
    print(f"  BY rejected:     {result.get('rejected_by_count', 0)}")
    print(f"  BY accepted:     {result.get('accepted_by_count', 0)}")

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)

    if result.get("status") == "FDR_FAILED":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
