"""Shared metadata injection for capture manifests and evaluation summaries.

Every JSON/JSONL artifact written by the research pipeline must carry a
small, forward-compatible metadata block so that downstream consumers
(MCPT export, nor
malization, regression tests) can identify schema version,
provenance, and safety constraints without guessing.

Metadata fields
---------------
schema_version : str
    Semver-style version of the artifact schema.  Bumped when field
    semantics change (not when new optional fields are added).
created_at_utc : str
    ISO-8601 timestamp of artifact creation.
git_sha : str
    Abbreviated commit SHA at write time ("" if not in a repo).
git_branch : str
    Branch name at write time ("" if not in a repo).
capture_mode : str
    "" | "FULL_ACTIVE" | "FAST_DIAGNOSTIC" — mirrors the CLI flag.
run_args : dict
    Serialized CLI arguments (non-sensitive only).
volatility_gate_snapshot : dict | None
    If a volatility gate was evaluated before capture, its verdict and
    thresholds.  None when absent.
preflight_summary : dict | None
    If a preflight check was run, its outcome.  None when absent.
safety_mode : str
    Always "public_data_observer_only" for this subsystem.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CURRENT_SCHEMA_VERSION = "1.0.0"
SAFETY_MODE = "public_data_observer_only"


def _get_git_sha() -> str:
    """Return abbreviated HEAD SHA, or '' if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _get_git_branch() -> str:
    """Return current branch name, or '' if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _args_to_dict(args: Any) -> dict[str, Any]:
    """Convert an argparse Namespace to a dict, excluding file paths and secrets."""
    if hasattr(args, "__dict__"):
        d = dict(vars(args))
    elif isinstance(args, dict):
        d = dict(args)
    else:
        return {}

    # Strip potentially sensitive / large fields
    for key in list(d.keys()):
        kl = key.lower()
        if any(word in kl for word in ("secret", "key", "password", "token", "auth")):
            d[key] = "<redacted>"

    # Convert Path values to strings
    for k, v in d.items():
        if isinstance(v, Path):
            d[k] = str(v)

    return d


def build_metadata(
    *,
    capture_mode: str = "",
    run_args: Any | None = None,
    volatility_gate_snapshot: dict[str, Any] | None = None,
    preflight_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standard metadata block shared across all artifacts.

    Parameters
    ----------
    capture_mode : str
        "" | "FULL_ACTIVE" | "FAST_DIAGNOSTIC".
    run_args : argparse.Namespace | dict | None
        CLI arguments to serialize (sensitive fields are redacted).
    volatility_gate_snapshot : dict | None
        Volatility gate verdict, if available.
    preflight_summary : dict | None
        Preflight check result, if available.

    Returns
    -------
    dict
        Metadata block suitable for json.dump.
    """
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "git_sha": _get_git_sha(),
        "git_branch": _get_git_branch(),
        "capture_mode": capture_mode,
        "run_args": _args_to_dict(run_args) if run_args is not None else {},
        "volatility_gate_snapshot": volatility_gate_snapshot,
        "preflight_summary": preflight_summary,
        "safety_mode": SAFETY_MODE,
    }


def inject_metadata_into_manifest(
    manifest: dict[str, Any],
    *,
    capture_mode: str = "",
    run_args: Any | None = None,
    volatility_gate_snapshot: dict[str, Any] | None = None,
    preflight_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add metadata to an existing capture manifest dict (mutates in-place and returns it).

    This is a convenience wrapper for backward-compat usage where the manifest
    dict is constructed imperatively.
    """
    manifest["_metadata"] = build_metadata(
        capture_mode=capture_mode,
        run_args=run_args,
        volatility_gate_snapshot=volatility_gate_snapshot,
        preflight_summary=preflight_summary,
    )
    return manifest


def get_metadata(obj: dict[str, Any]) -> dict[str, Any]:
    """Extract metadata from a loaded manifest/summary, with backward compat.

    Returns an empty dict if no metadata is present (old-format artifacts).
    Never raises — missing keys return None.
    """
    meta = obj.get("_metadata", {})
    if not isinstance(meta, dict):
        return {}
    return meta


def get_metadata_field(obj: dict[str, Any], field: str, default: Any = None) -> Any:
    """Read a single metadata field from a manifest/summary dict.

    Returns *default* if the field is absent (including when the entire
    _metadata block is missing — i.e. old-format artifacts).
    """
    meta = get_metadata(obj)
    return meta.get(field, default)