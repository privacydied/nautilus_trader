"""Capture/data fingerprint helpers for the discovery freeze system.

Provides sha256_file and build_capture_manifest_ref to pin specific capture
artifacts.  This is a local file fingerprint helper only — it does not connect
to any network endpoint or hash large stream files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import CaptureFingerprintError


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def sha256_file(path: str | Path) -> str:
    """Compute SHA-256 hex digest of a file's raw bytes.

    Raises FileNotFoundError if the file does not exist.
    """
    path = Path(path)
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# CaptureManifestRef
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaptureManifestRef:
    """A frozen reference to a capture manifest artifact.

    This pins one specific capture artifact via its raw bytes hash.
    It is NOT used to prove equivalence between two captures — only to
    record which specific artifact was used to surface a candidate.

    capture_id resolution:
    1. If the manifest JSON contains a "run_id" field, that is used.
    2. Otherwise, if it contains a "capture_id" field, that is used.
    3. Otherwise, capture_id defaults to "sha256:" + first 16 hex chars
       of manifest_sha256.
    capture_id is therefore always a non-empty str and is never None.

    manifest_path is convenience metadata only. The trust anchor is
    manifest_sha256.
    """

    capture_id: str
    manifest_path: str
    manifest_sha256: str
    data_window_start_utc: str | None
    data_window_end_utc: str | None
    stream_count: int | None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_capture_manifest_ref(path: str | Path) -> CaptureManifestRef:
    """Build a CaptureManifestRef from a capture manifest JSON file.

    Args:
        path: Path to the manifest JSON file.

    Returns:
        A CaptureManifestRef with the manifest's fingerprint.

    Raises:
        CaptureFingerprintError: If the manifest file is missing, truncated,
            or contains invalid JSON.
    """
    path = Path(path)

    if not path.exists():
        raise CaptureFingerprintError(f"Capture manifest not found: {path}")

    # Read raw bytes first for hashing
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        raise CaptureFingerprintError(f"Cannot read capture manifest {path}: {e}")

    if not raw_bytes:
        raise CaptureFingerprintError(f"Capture manifest is empty: {path}")

    manifest_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # Parse JSON
    try:
        data: dict[str, Any] = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        raise CaptureFingerprintError(
            f"Invalid JSON in capture manifest {path}: {e}"
        )

    # Resolve capture_id
    capture_id: str | None = data.get("run_id") or data.get("capture_id")
    if capture_id is None:
        capture_id = "sha256:" + manifest_sha256[:16]

    # Extract optional fields
    window_start: str | None = data.get("data_window_start_utc")
    window_end: str | None = data.get("data_window_end_utc")
    stream_count: int | None = data.get("stream_count")

    return CaptureManifestRef(
        capture_id=str(capture_id),
        manifest_path=str(path.resolve()),
        manifest_sha256=manifest_sha256,
        data_window_start_utc=window_start,
        data_window_end_utc=window_end,
        stream_count=stream_count,
    )
