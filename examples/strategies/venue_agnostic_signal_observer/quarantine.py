"""
Quarantine mechanism for run artifacts — simple JSONL file convention.

A quarantine file marks runs as poisoned/contaminated without deleting
the original evidence.  Consumers may skip quarantined runs by default.

Default path: ``reports/research_run_quarantine.jsonl``

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_QUARANTINE_PATH = Path("reports") / "research_run_quarantine.jsonl"


def quarantine_run(
    run_id: str,
    reason: str,
    *,
    path: Path | None = None,
    quarantined_by: str | None = None,
    notes: str | None = None,
) -> Path:
    """
    Add a run to the quarantine file.

    Parameters
    ----------
    run_id : str
        The run to quarantine.
    reason : str
        Why the run is quarantined (e.g. "malformed_manifest",
        "corrupt_jsonl", "partial_capture").
    path : Path, optional
        Path to quarantine JSONL file.  Default:
        ``reports/research_run_quarantine.jsonl``.
    quarantined_by : str, optional
        Who or what tool quarantined the run (e.g. ``validate_capture``).
    notes : str, optional
        Additional context.

    Returns
    -------
    Path
        The quarantine file that was written to.
    """
    if path is None:
        path = DEFAULT_QUARANTINE_PATH
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    row: dict[str, Any] = {
        "run_id": run_id,
        "reason": reason,
        "quarantined_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }
    if quarantined_by is not None:
        row["quarantined_by"] = quarantined_by
    if notes is not None:
        row["notes"] = notes

    with open(resolved, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()

    return resolved


def read_quarantine(
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Read all quarantine rows.

    Returns a list of parsed dicts.  Returns an empty list if the
    file does not exist.
    """
    if path is None:
        path = DEFAULT_QUARANTINE_PATH
    resolved = path.resolve()
    if not resolved.exists():
        return []

    rows: list[dict[str, Any]] = []
    with open(resolved, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def is_quarantined(run_id: str, path: Path | None = None) -> bool:
    """Check if a ``run_id`` is in the quarantine file."""
    return any(
        row.get("run_id") == run_id
        for row in read_quarantine(path=path)
    )


def get_quarantined_run_ids(path: Path | None = None) -> set[str]:
    """Return the set of quarantined run IDs."""
    return {row.get("run_id") for row in read_quarantine(path=path) if row.get("run_id")}
