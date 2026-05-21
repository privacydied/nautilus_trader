"""
Shared utilities for Stage 2 precommitment tooling.

Provides:
- load_precommitment(): load and parse stage2_precommitment.json
- get_test_family_dimensions(): return the declared dimensions
- compute_sha256(): compute SHA-256 of a file
- CollectionLock: create, verify, and compare collection lock state

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


PRECOMMITMENT_JSON_PATH = Path("stage2_precommitment.json")
LOCK_PATH = Path("reports") / "stage2_collection_lock.json"


def _repo_root() -> Path:
    """Return the repo root via git, or current working directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass
    return Path.cwd().resolve()


def _get_git_sha() -> str:
    """Return abbreviated HEAD SHA with -dirty suffix if modified."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if rev.returncode != 0:
            return ""
        sha = rev.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            return f"{sha}-dirty"
        return sha
    except Exception:
        return ""


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def load_precommitment(
    path: Path | None = None,
) -> dict[str, Any]:
    """
    Load and return the stage2_precommitment.json content.

    Raises FileNotFoundError if the file is missing.
    Raises ValueError if JSON parsing fails.
    """
    if path is None:
        root = _repo_root()
        path = root / "stage2_precommitment.json"
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"stage2_precommitment.json not found at {resolved}"
        )
    try:
        with open(resolved, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"stage2_precommitment.json is not valid JSON: {e}")
    return data


def get_test_family_dimensions(precommit: dict[str, Any] | None = None) -> list[str]:
    """Return the test family dimensions declared in the precommitment."""
    if precommit is None:
        precommit = load_precommitment()
    return list(
        precommit.get("primary_fdr", {}).get("test_family_dimensions", [])
    )


def get_signal_family(precommit: dict[str, Any] | None = None) -> str:
    """Return the signal family from the precommitment."""
    if precommit is None:
        precommit = load_precommitment()
    return str(precommit.get("signal_family", ""))


def validate_markdown_json_match(
    precommit: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    Check that key values in the JSON match expected markdown content.

    This is a cross-reference check — the markdown and JSON should declare
    the same committed values.  Returns (ok, reason).
    """
    if precommit is None:
        try:
            precommit = load_precommitment()
        except (FileNotFoundError, ValueError) as e:
            return False, f"Cannot load precommitment JSON: {e}"

    # Check key structural elements
    checks: list[tuple[str, bool]] = []

    primary_fdr = precommit.get("primary_fdr", {})
    checks.append(
        ("BH method", primary_fdr.get("method") == "benjamini_hochberg")
    )
    checks.append(("BH q=0.10", primary_fdr.get("q") == 0.10))
    checks.append(
        ("p-value source native_permutation",
         primary_fdr.get("pvalue_source") == "native_permutation")
    )
    checks.append(
        ("test family dims present",
         len(primary_fdr.get("test_family_dimensions", [])) >= 5)
    )

    sensitivity = precommit.get("sensitivity_fdr", {})
    checks.append(
        ("BY method", sensitivity.get("method") == "benjamini_yekutieli")
    )
    checks.append(("BY q=0.10", sensitivity.get("q") == 0.10))

    holdout = precommit.get("holdout", {})
    checks.append(
        ("temporal split", holdout.get("primary_split") == "temporal")
    )
    checks.append(("discovery 0.70", holdout.get("discovery_fraction") == 0.70))
    checks.append(("test 0.30", holdout.get("test_fraction") == 0.30))
    checks.append(
        ("min 10 captures", holdout.get("minimum_validated_captures") == 10)
    )

    discovery = precommit.get("discovery_acceptance", {})
    checks.append(
        ("+2 bps", discovery.get("minimum_aggregate_mean_net_bps_per_event") == 2.0)
    )
    checks.append(
        ("70% same sign", discovery.get("minimum_same_sign_capture_fraction") == 0.70)
    )
    checks.append(
        ("min 50 events", discovery.get("minimum_valid_events_per_config") == 50)
    )
    checks.append(
        ("-5 bps floor", discovery.get("worst_capture_mean_net_bps_floor") == -5.0)
    )

    holdout_accept = precommit.get("holdout_acceptance", {})
    checks.append(
        ("holdout +2 bps",
         holdout_accept.get("minimum_aggregate_mean_net_bps_per_event") == 2.0)
    )
    checks.append(
        ("holdout min 50 events",
         holdout_accept.get("minimum_valid_events_per_config") == 50)
    )
    checks.append(
        ("holdout -5 bps floor",
         holdout_accept.get("worst_capture_mean_net_bps_floor") == -5.0)
    )

    failed = [(name, val) for name, val in checks if not val]
    if failed:
        details = "; ".join(f"{name}" for name, _ in failed)
        return False, f"Mismatches: {details}"
    return True, "All precommit values match"


# ---------------------------------------------------------------------------
# Collection lock
# ---------------------------------------------------------------------------


class CollectionLock:
    """
    Manage the Stage 2 collection lock file.

    The lock records the precommitment state at the moment the first
    corpus-admissible FULL_ACTIVE capture attempt starts.  Every later
    Stage 2 run must verify the lock has not changed.
    """

    def __init__(
        self,
        lock_path: Path | None = None,
        precommit_path: Path | None = None,
        md_path: Path | None = None,
        rationale_path: Path | None = None,
    ) -> None:
        self._root = _repo_root()
        self._lock_path = (
            lock_path.resolve() if lock_path else self._root / "reports" / "stage2_collection_lock.json"
        )
        self._precommit_path = (
            precommit_path.resolve() if precommit_path else self._root / "stage2_precommitment.json"
        )
        self._md_path = (
            md_path.resolve() if md_path else self._root / "STAGE2_PRECOMMITMENT.md"
        )
        self._rationale_path = (
            rationale_path.resolve() if rationale_path else self._root / "STAGE2_THRESHOLDS_RATIONALE.md"
        )

    def exists(self) -> bool:
        return self._lock_path.exists()

    def read(self) -> dict[str, Any] | None:
        """Read the existing lock file, or None if missing."""
        if not self._lock_path.exists():
            return None
        try:
            with open(self._lock_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _compute_current_hashes(self) -> dict[str, str]:
        """Compute SHA-256 hashes of the three precommitment files."""
        return {
            "stage2_precommitment_md_sha256": compute_file_sha256(self._md_path),
            "stage2_precommitment_json_sha256": compute_file_sha256(self._precommit_path),
            "thresholds_rationale_sha256": compute_file_sha256(self._rationale_path),
        }

    def create(
        self,
        run_id: str,
        signal_family: str | None = None,
        lock_reason: str = "first FULL_ACTIVE capture attempt starting",
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Create the collection lock file.

        Must be called immediately before the first FULL_ACTIVE capture
        subprocess starts.

        Raises FileExistsError if lock already exists.
        """
        if self._lock_path.exists():
            raise FileExistsError(
                f"Collection lock already exists at {self._lock_path}. "
                "Use verify() to check consistency."
            )

        hashes = self._compute_current_hashes()

        precommit = load_precommitment(self._precommit_path)
        if signal_family is None:
            signal_family = get_signal_family(precommit)

        lock: dict[str, Any] = {
            "schema_version": 1,
            "created_utc": _ts_now_iso(),
            "lock_creation_run_id": run_id,
            "lock_creation_git_sha": _get_git_sha(),
            **hashes,
            "signal_family": signal_family,
            "lock_reason": lock_reason,
        }
        if notes is not None:
            lock["notes"] = notes

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=2, default=str)
        return lock

    def verify(self) -> tuple[bool, str]:
        """
        Verify that the existing lock matches current precommitment state.

        Returns (ok, reason).
        """
        lock = self.read()
        if lock is None:
            return False, "Collection lock does not exist"

        current_hashes = self._compute_current_hashes()
        lock_hashes = {
            "stage2_precommitment_md_sha256": lock.get("stage2_precommitment_md_sha256"),
            "stage2_precommitment_json_sha256": lock.get("stage2_precommitment_json_sha256"),
            "thresholds_rationale_sha256": lock.get("thresholds_rationale_sha256"),
        }

        mismatches = []
        for key in current_hashes:
            if current_hashes[key] != lock_hashes.get(key):
                mismatches.append(key)

        if mismatches:
            details = "; ".join(
                f"{k}: lock={lock_hashes.get(k)}, current={current_hashes[k]}"
                for k in mismatches
            )
            return False, f"Hash mismatch for: {details}"

        return True, "Lock hashes match current precommitment files"

    def get_sha256(self, key: str) -> str | None:
        """Get a specific hash from the lock."""
        lock = self.read()
        if lock is None:
            return None
        return lock.get(key)
