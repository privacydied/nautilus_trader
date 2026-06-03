"""
Append-only JSONL run index for the research pipeline.

The run index records every run (capture, evaluation, validation, campaign, etc.)
as append-only rows.  Consumers must group by ``run_id`` and take the latest
row by ``created_at``.

Default path: ``reports/research_run_index.jsonl``

If an artifact has its own ``schema_version`` (in ``_metadata``), that artifact
version wins over the copied run-index field.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


_RESERVED_RUN_TYPES = frozenset({
    "capture", "evaluation", "null", "cost_sensitivity", "heatmap",
    "latency", "corpus", "falsification", "campaign", "validation",
})

_RUN_STATUSES = frozenset({"started", "completed", "failed", "interrupted", "skipped"})


def _get_git_sha_dirty() -> str:
    """
    Return git SHA with ``-dirty`` suffix when the working tree has uncommitted changes.

    Returns ``""`` if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return ""
        sha = result.stdout.strip()
        # Check dirty status
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if dirty_result.returncode == 0 and dirty_result.stdout.strip():
            return f"{sha}-dirty"
        return sha
    except Exception:
        return ""


def _get_cwd() -> str:
    """Return resolved current working directory."""
    try:
        return str(Path.cwd().resolve())
    except OSError:
        return ""


def validate_run_type(run_type: str) -> str:
    """
    Validate and normalise *run_type*.

    Raises ``ValueError`` if unrecognised.
    """
    if run_type not in _RESERVED_RUN_TYPES:
        raise ValueError(
            f"Unknown run_type {run_type!r}. "
            f"Allowed: {sorted(_RESERVED_RUN_TYPES)}"
        )
    return run_type


def validate_status(status: str) -> str:
    """Validate *status*."""
    if status not in _RUN_STATUSES:
        raise ValueError(
            f"Unknown status {status!r}. "
            f"Allowed: {sorted(_RUN_STATUSES)}"
        )
    return status


def build_run_index_row(
    *,
    run_id: str,
    run_type: str = "capture",
    status: str = "started",
    command_args: str | None = None,
    capture_dir: str | None = None,
    report_dir: str | None = None,
    output_dir: str | None = None,
    schema_version: str | None = None,
    summary_path: str | None = None,
    manifest_path: str | None = None,
    parent_run_id: str | None = None,
    campaign_id: str | None = None,
    notes: str | None = None,
    errors: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a run-index row dict.

    Parameters
    ----------
    run_id : str
        Unique run identifier.
    run_type : str
        One of the reserved run types.
    status : str
        One of ``started``, ``completed``, ``failed``, ``interrupted``,
        ``skipped``.
    command_args : str, optional
        CLI command string used to launch the run.
    capture_dir : str, optional
        Absolute path to the capture data directory.
    report_dir : str, optional
        Absolute path to the report directory.
    output_dir : str, optional
        Absolute path to the output directory.
    schema_version : str, optional
        Copied from the persisted artifact; the artifact's own version wins.
    summary_path : str, optional
        Absolute path to summary file.
    manifest_path : str, optional
        Absolute path to manifest file.
    parent_run_id : str, optional
        If this run was spawned by another (e.g. campaign run index ref).
    campaign_id : str, optional
        Campaign identifier if part of a campaign.
    notes : str, optional
        Human-readable notes about this run.
    errors : str, optional
        Error summary if status is ``failed`` or ``interrupted``.
    extra : dict, optional
        Additional key-value pairs to include.
    """
    validate_run_type(run_type)
    validate_status(status)

    row: dict[str, Any] = {
        "run_id": run_id,
        "run_type": run_type,
        "status": status,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "git_sha": _get_git_sha_dirty(),
        "cwd": _get_cwd(),
    }

    if command_args is not None:
        row["command_args"] = command_args
    if capture_dir is not None:
        row["capture_dir"] = str(Path(capture_dir).resolve())
    if report_dir is not None:
        row["report_dir"] = str(Path(report_dir).resolve())
    if output_dir is not None:
        row["output_dir"] = str(Path(output_dir).resolve())
    if schema_version is not None:
        row["schema_version"] = schema_version
    if summary_path is not None:
        row["summary_path"] = str(Path(summary_path).resolve())
    if manifest_path is not None:
        row["manifest_path"] = str(Path(manifest_path).resolve())
    if parent_run_id is not None:
        row["parent_run_id"] = parent_run_id
    if campaign_id is not None:
        row["campaign_id"] = campaign_id
    if notes is not None:
        row["notes"] = notes
    if errors is not None:
        row["errors"] = errors
    if extra:
        row.update(extra)

    return row


def append_run_index_row(row: dict[str, Any], path: Path | None = None) -> Path:
    """
    Append one row to the JSONL run index.

    If *path* is None, defaults to ``reports/research_run_index.jsonl``
    relative to the current working directory.

    Returns the path to the index file.
    """
    if path is None:
        path = Path.cwd() / "reports" / "research_run_index.jsonl"
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()
    return resolved


def read_run_index(
    path: Path | None = None,
    run_id_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read all rows from a JSONL run index.

    Returns a list of parsed dicts.
    Optionally filter to a specific ``run_id``.
    """
    if path is None:
        path = Path.cwd() / "reports" / "research_run_index.jsonl"
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
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id_filter is None or row.get("run_id") == run_id_filter:
                rows.append(row)
    return rows


def get_latest_status(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Group rows by ``run_id`` and return the latest row by ``created_at`` for each.

    *rows* is a list of parsed run-index dicts (from ``read_run_index``).
    Returns a dict mapping ``run_id`` -> the most recent row.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("run_id")
        if not rid:
            continue
        existing = latest.get(rid)
        if existing is None or row.get("created_at", "") > existing.get("created_at", ""):
            latest[rid] = row
    return latest
