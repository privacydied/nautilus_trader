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

# Schema version compatibility — bump SUPPORTED_SCHEMA_VERSIONS when
# the reader explicitly supports reading older artifacts.
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0.0", "v0"})


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


def _get_git_sha_dirty() -> str:
    """Return git SHA with ``-dirty`` suffix when the working tree has uncommitted changes.

    Returns ``""`` if not in a git repo.
    """
    sha = _get_git_sha()
    if not sha:
        return ""
    try:
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if dirty_result.returncode == 0 and dirty_result.stdout.strip():
            return f"{sha}-dirty"
    except Exception:
        pass
    return sha


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


# ---------------------------------------------------------------------------
# Schema version validation
# ---------------------------------------------------------------------------


def check_schema_version(
    obj: dict[str, Any],
    *,
    supported: frozenset[str] | None = None,
    allow_missing: bool = False,
) -> tuple[bool, str]:
    """Check an artifact's schema version for compatibility.

    Parameters
    ----------
    obj : dict
        The loaded JSON artifact (manifest, summary, etc.).
    supported : frozenset, optional
        Set of supported schema versions.  Defaults to
        ``SUPPORTED_SCHEMA_VERSIONS``.
    allow_missing : bool
        If True, a missing ``schema_version`` is treated as v0
        and allowed.  If False, missing schema causes a skip.

    Returns
    -------
    (ok, reason)
        ``ok`` is True if acceptable, False if the artifact should
        be skipped.  ``reason`` explains the result.
    """
    if supported is None:
        supported = SUPPORTED_SCHEMA_VERSIONS

    sv = get_metadata_field(obj, "schema_version")
    if sv is None or sv == "":
        if allow_missing:
            return True, "schema_missing_treated_as_v0"
        return False, "schema_missing_not_allowed"

    if sv in supported:
        return True, f"schema_version_{sv}_supported"

    # Compare numerically if possible
    try:
        sv_parts = [int(x) for x in sv.split(".")]
        current_parts = [int(x) for x in CURRENT_SCHEMA_VERSION.split(".")]
        if sv_parts > current_parts:
            return False, f"schema_version_{sv}_higher_than_reader_{CURRENT_SCHEMA_VERSION}"
    except (ValueError, AttributeError):
        pass

    return False, f"schema_version_{sv}_not_in_supported_set"