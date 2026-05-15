"""
Burn file convention for the research pipeline.

A burn file marks run IDs / corpora that have already been evaluated under
failed criteria and must not be reused for the same signal-family
precommitment.  Default path: ``reports/research_run_burned.jsonl``

Every burn row includes the signal family, the precommitment git SHA and
JSON hash, the full corpus (discovery and test run IDs), and a structured
reason.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BURN_PATH = Path("reports") / "research_run_burned.jsonl"


def _compute_json_sha256(payload: dict[str, Any] | None) -> str:
    """Compute SHA-256 of a JSON-serialised dict.

    Sorts keys for deterministic output.
    """
    raw = json.dumps(payload or {}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _make_burn_id() -> str:
    """Generate a unique burn identifier."""
    import secrets

    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"_{now.microsecond:06d}"
    rand = secrets.token_hex(3)
    return f"burn_{ts}_{rand}"


def burn_corpus(
    signal_family: str,
    reason: str,
    precommitment_git_sha: str,
    *,
    precommitment_json: dict[str, Any] | None = None,
    run_ids: list[str] | None = None,
    discovery_run_ids: list[str] | None = None,
    test_run_ids: list[str] | None = None,
    path: Path | None = None,
    burned_by: str | None = None,
    notes: str | None = None,
) -> Path:
    """Add a burn record to the burn file.

    Parameters
    ----------
    signal_family : str
        The signal family this burn applies to (e.g.
        ``derivatives_source_spot_target_lead_lag_v2``).
    reason : str
        Why the corpus is being burned.
    precommitment_git_sha : str
        Git SHA of the precommitment that was in effect.
    precommitment_json : dict, optional
        The precommitment JSON dict (used to compute its SHA-256).
    run_ids : list[str], optional
        All run IDs in the burned corpus.
    discovery_run_ids : list[str], optional
        Discovery run IDs from the split.
    test_run_ids : list[str], optional
        Test run IDs from the split (recorded but never inspected during
        discovery failure).
    path : Path, optional
        Path to burn JSONL file.  Default:
        ``reports/research_run_burned.jsonl``.
    burned_by : str, optional
        Who or what tool burned the corpus.
    notes : str, optional
        Additional context.

    Returns
    -------
    Path
        The burn file that was written to.
    """
    if path is None:
        path = DEFAULT_BURN_PATH
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    precommitment_json_sha256 = _compute_json_sha256(precommitment_json)

    row: dict[str, Any] = {
        "burn_id": _make_burn_id(),
        "burned_at": _ts_now_iso(),
        "signal_family": signal_family,
        "reason": reason,
        "precommitment_git_sha": precommitment_git_sha,
        "precommitment_json_sha256": precommitment_json_sha256,
    }
    if run_ids is not None:
        row["run_ids"] = run_ids
    if discovery_run_ids is not None:
        row["discovery_run_ids"] = discovery_run_ids
    if test_run_ids is not None:
        row["test_run_ids"] = test_run_ids
    if burned_by is not None:
        row["burned_by"] = burned_by
    if notes is not None:
        row["notes"] = notes

    with open(resolved, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()

    return resolved


def read_burned(path: Path | None = None) -> list[dict[str, Any]]:
    """Read all burn rows.

    Returns a list of parsed dicts.  Returns an empty list if the
    file does not exist.
    """
    if path is None:
        path = DEFAULT_BURN_PATH
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


def get_burned_run_ids(path: Path | None = None) -> set[str]:
    """Return the set of all run IDs mentioned in any burn record.

    Aggregates ``run_ids``, ``discovery_run_ids``, and ``test_run_ids``
    from every burn row.
    """
    ids: set[str] = set()
    for row in read_burned(path=path):
        for key in ("run_ids", "discovery_run_ids", "test_run_ids"):
            for rid in row.get(key, []):
                if rid:
                    ids.add(rid)
    return ids


def is_burned(run_id: str, path: Path | None = None) -> bool:
    """Check if a ``run_id`` appears in any burn record."""
    return run_id in get_burned_run_ids(path=path)


def get_burned_signal_families(path: Path | None = None) -> set[str]:
    """Return the set of signal families that have been burned."""
    return {
        row.get("signal_family")
        for row in read_burned(path=path)
        if row.get("signal_family")
    }
