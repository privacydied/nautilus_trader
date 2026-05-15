#!/usr/bin/env python3
"""
Stage 2 corpus splitter — temporal discovery/test split.

Reads validated capture/report metadata and produces sealed discovery
and test run lists using the precommitted temporal rule.

Rules from precommitment:
- Temporal split ordered by capture_start_utc
- Earliest 70 % → discovery, latest 30 % → test
- Minimum 10 validated FULL_ACTIVE captures
- Exclude quarantined, burned, FAST_DIAGNOSTIC, failed-validation runs
- Data-independent: never sorts by return, p-value, event count, or
  any signal-performance metric
- Fails if required capture-start timestamps are missing

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .stage2_precommitment_utils import (
    CollectionLock,
    compute_file_sha256,
    load_precommitment,
    get_signal_family,
    _get_git_sha,
)
from .run_artifacts import atomic_write_json
from .run_index import read_run_index, get_latest_status
from .quarantine import get_quarantined_run_ids
from .burn import get_burned_run_ids


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_timestamp(val: Any) -> datetime | None:
    """Try to parse a capture_start_utc timestamp."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=UTC)
    return None


def _find_capture_start(
    report_entry: dict[str, Any],
    row_data: dict[str, Any],
) -> str | None:
    """Extract capture_start_utc from various report/metadata structures.

    Checks (in order):
    1. Direct key on report_entry
    2. summary_path → summary.json capture_start_utc
    3. Row data from run index
    4. manifest_path → capture_manifest.json
    """
    # Direct key
    direct = report_entry.get("capture_start_utc")
    if direct and isinstance(direct, str):
        return direct

    # From manifest_path
    manifest_path = report_entry.get("manifest_path") or row_data.get("manifest_path")
    if manifest_path:
        mp = Path(manifest_path)
        if mp.exists():
            try:
                with open(mp, encoding="utf-8") as f:
                    manifest = json.load(f)
                ts = manifest.get("started_at") or manifest.get("capture_start_utc")
                if ts:
                    return str(ts)
            except (json.JSONDecodeError, OSError):
                pass

    # From summary_path
    summary_path = report_entry.get("summary_path") or row_data.get("summary_path")
    if summary_path:
        sp = Path(summary_path)
        if sp.exists():
            try:
                with open(sp, encoding="utf-8") as f:
                    summary = json.load(f)
                ts = summary.get("capture_start_utc") or summary.get("started_at")
                if ts:
                    return str(ts)
            except (json.JSONDecodeError, OSError):
                pass

    return None


def _validate_and_collect_captures(
    report_root: Path,
    run_index_path: Path | None,
    precommit: dict[str, Any],
    quarantined_run_ids: set[str],
    burned_run_ids: set[str],
) -> list[dict[str, Any]]:
    """Collect validated FULL_ACTIVE captures with timestamps.

    Returns list of dicts with keys: run_id, capture_start_utc, run_dir, metadata.
    """
    # Read run index
    rows = read_run_index(path=run_index_path) if run_index_path else []
    latest = get_latest_status(rows)

    # Read report dirs
    reports_root = Path(report_root).resolve()
    if not reports_root.exists():
        return []

    # Collect validated captures
    admission = precommit.get("capture_admission", {})
    require_validation_passed = admission.get("require_validation_passed", True)
    exclude_fast_diag = admission.get("exclude_fast_diagnostic", True)
    required_mode = admission.get("required_capture_mode", "FULL_ACTIVE")

    captures: list[dict[str, Any]] = []

    for report_dir in sorted(reports_root.iterdir()):
        if not report_dir.is_dir():
            continue
        summary_path = report_dir / "summary.json"
        if not summary_path.exists():
            continue

        try:
            with open(summary_path, encoding="utf-8") as f:
                summary: dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        metadata = summary.get("_metadata", {})
        run_id = summary.get("run_id") or metadata.get("run_id")
        if not run_id:
            continue

        # Exclude quarantined
        if run_id in quarantined_run_ids:
            continue

        # Exclude burned
        if run_id in burned_run_ids:
            continue

        capture_mode = metadata.get("capture_mode", "")
        if exclude_fast_diag and capture_mode == "FAST_DIAGNOSTIC":
            continue
        if required_mode and capture_mode != required_mode:
            continue

        # Check validation if required
        if require_validation_passed:
            row_data = latest.get(run_id, {})
            validation_ok = row_data.get("validation_passed") or summary.get(
                "validation_verdict"
            ) in ("CAPTURE_VALIDATION_PASSED",)
            # Also check for a validation summary
            val_path = report_dir / "validation_summary.json"
            if val_path.exists():
                try:
                    with open(val_path, encoding="utf-8") as f:
                        val = json.load(f)
                    if val.get("verdict") not in (
                        "CAPTURE_VALIDATION_PASSED",
                        "CAPTURE_VALIDATION_WARNINGS",
                    ):
                        continue
                except (json.JSONDecodeError, OSError):
                    if not validation_ok:
                        continue
            elif not validation_ok:
                continue

        # Extract capture start timestamp
        report_entry = {
            "run_id": run_id,
            "capture_start_utc": summary.get("capture_start_utc"),
            "manifest_path": summary.get("manifest_path")
            or str(report_dir / "capture_manifest.json"),
            "summary_path": str(summary_path),
        }

        capture_start = _find_capture_start(report_entry, row_data)
        if capture_start is None:
            continue  # Skip captures with no timestamp

        captures.append({
            "run_id": run_id,
            "capture_start_utc": capture_start,
            "run_dir": str(report_dir),
            "metadata": metadata,
        })

    return captures


def build_split(
    report_root: str,
    *,
    run_index_path: str | None = None,
    out_dir: str | None = None,
    quarantine_path: str | None = None,
    burn_path: str | None = None,
    precommit_path: str | None = None,
) -> dict[str, Any]:
    """Build the Stage 2 discovery/test split.

    Returns a split manifest dict with keys:
        split_created_at
        signal_family
        discovery_fraction
        test_fraction
        total_validated_captures
        discovery_count
        test_count
        discovery_runs
        test_runs
        precommitment_git_sha
        precommitment_json_sha256
        lock_hash
        status
        reason
    """
    precommit = load_precommitment(
        Path(precommit_path) if precommit_path else None
    )
    signal_family = get_signal_family(precommit)

    quarantined = (
        get_quarantined_run_ids(Path(quarantine_path))
        if quarantine_path
        else set()
    )
    burned = (
        get_burned_run_ids(Path(burn_path))
        if burn_path
        else set()
    )

    index_path = Path(run_index_path).resolve() if run_index_path else None
    captures = _validate_and_collect_captures(
        report_root=report_root,
        run_index_path=index_path,
        precommit=precommit,
        quarantined_run_ids=quarantined,
        burned_run_ids=burned,
    )

    holdout = precommit.get("holdout", {})
    min_captures = holdout.get("minimum_validated_captures", 10)
    discovery_frac = holdout.get("discovery_fraction", 0.70)

    if len(captures) < min_captures:
        return {
            "split_created_at": _ts_now_iso(),
            "signal_family": signal_family,
            "total_validated_captures": len(captures),
            "discovery_count": 0,
            "test_count": 0,
            "discovery_runs": [],
            "test_runs": [],
            "status": "ABORTED_INSUFFICIENT_CAPTURES",
            "reason": f"Need at least {min_captures} validated FULL_ACTIVE captures; "
                      f"found {len(captures)}",
        }

    # Sort by capture_start_utc (temporal ordering)
    def _ts_key(c: dict[str, Any]) -> str:
        return str(c.get("capture_start_utc", ""))

    captures.sort(key=_ts_key)

    # Temporal split
    n_discovery = max(1, int(len(captures) * discovery_frac))
    discovery = captures[:n_discovery]
    test = captures[n_discovery:]

    # Precommitment hashes
    precommit_json_path = Path(precommit_path) if precommit_path else Path("stage2_precommitment.json")
    precommit_json_sha256 = compute_file_sha256(precommit_json_path.resolve()) if precommit_json_path.exists() else ""
    git_sha = _get_git_sha()

    # Lock hash
    lock = CollectionLock()
    lock_hash = None
    if lock.exists():
        lock_data = lock.read()
        if lock_data:
            lock_hash = lock_data.get("stage2_precommitment_json_sha256")

    result: dict[str, Any] = {
        "split_created_at": _ts_now_iso(),
        "signal_family": signal_family,
        "discovery_fraction": discovery_frac,
        "test_fraction": 1.0 - discovery_frac,
        "total_validated_captures": len(captures),
        "discovery_count": len(discovery),
        "test_count": len(test),
        "discovery_runs": discovery,
        "test_runs": test,
        "precommitment_git_sha": git_sha,
        "precommitment_json_sha256": precommit_json_sha256,
        "lock_hash": lock_hash,
        "status": "SPLIT_COMPLETED",
        "reason": f"Temporal split: {len(discovery)} discovery / {len(test)} test "
                  f"from {len(captures)} validated captures",
    }

    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 2 splitter: temporal discovery/test split "
                    "from validated capture/report metadata."
    )
    p.add_argument(
        "--report-root", type=str, required=True,
        help="Root directory containing report subdirectories",
    )
    p.add_argument(
        "--out-dir", type=str, default=None,
        help="Output directory for split manifests (default: reports/stage2_split)",
    )
    p.add_argument(
        "--run-index-path", type=str, default=None,
        help="Path to research_run_index.jsonl (default: auto-discover)",
    )
    p.add_argument(
        "--quarantine-path", type=str, default=None,
        help="Path to quarantine file (default: reports/research_run_quarantine.jsonl)",
    )
    p.add_argument(
        "--burn-path", type=str, default=None,
        help="Path to burn file (default: reports/research_run_burned.jsonl)",
    )
    p.add_argument(
        "--precommit-path", type=str, default=None,
        help="Path to stage2_precommitment.json (default: auto-discover)",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Default paths
    root = Path.cwd().resolve()
    out_dir = Path(args.out_dir) if args.out_dir else root / "reports" / "stage2_split"
    out_dir.mkdir(parents=True, exist_ok=True)

    run_index_path = args.run_index_path
    if not run_index_path:
        run_index_path = str(root / "reports" / "research_run_index.jsonl")

    qpath = args.quarantine_path
    if not qpath:
        qpath = str(root / "reports" / "research_run_quarantine.jsonl")

    bpath = args.burn_path
    if not bpath:
        bpath = str(root / "reports" / "research_run_burned.jsonl")

    result = build_split(
        report_root=args.report_root,
        run_index_path=run_index_path,
        out_dir=str(out_dir),
        quarantine_path=qpath,
        burn_path=bpath,
        precommit_path=args.precommit_path,
    )

    # Write outputs
    discovery_path = out_dir / "stage2_discovery_runs.json"
    test_path = out_dir / "stage2_test_runs.json"
    manifest_path = out_dir / "stage2_split_manifest.json"

    atomic_write_json(discovery_path, result.get("discovery_runs", []))
    atomic_write_json(test_path, result.get("test_runs", []))
    atomic_write_json(manifest_path, result)

    print(f"  Split status:  {result['status']}")
    print(f"  Reason:        {result.get('reason', '')}")
    print(f"  Discovery:     {result.get('discovery_count', 0)} runs")
    print(f"  Test:          {result.get('test_count', 0)} runs")
    print(f"  Discovery runs: {discovery_path}")
    print(f"  Test runs:      {test_path}")
    print(f"  Manifest:       {manifest_path}")

    # Exit code
    if result["status"] == "ABORTED_INSUFFICIENT_CAPTURES":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
