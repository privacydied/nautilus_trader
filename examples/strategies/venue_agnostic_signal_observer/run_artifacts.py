"""
Run artifact helpers — run IDs, safe output dirs, atomic writes.

Provides:
- create_run_id(prefix) -> str
- create_run_dir(base_dir, run_id) -> Path
- safe_output_dir(path, *, allow_existing=False) -> Path
- atomic_write_json(path, payload) -> None
- atomic_write_text(path, text) -> None
- atomic_write_jsonl(path, rows) -> None

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import tempfile
from collections.abc import Iterable
from datetime import UTC
from datetime import datetime
from pathlib import Path


def create_run_id(prefix: str = "run") -> str:
    """
    Produce a filesystem-safe unique run ID.

    Format: ``<prefix>_<UTC-timestamp-microseconds>_<6-hex-rand>``

    The random suffix ensures uniqueness even when called rapidly
    in the same process.
    """
    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"_{now.microsecond:06d}"
    rand = secrets.token_hex(3)  # 6 hex chars
    safe_prefix = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix)
    return f"{safe_prefix}_{ts}_{rand}"


def create_run_dir(base_dir: Path, run_id: str) -> Path:
    """
    Create a unique output directory for a run.

    The directory is created as ``base_dir / run_id``.
    Returns the absolute path.
    """
    path = base_dir.resolve() / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_output_dir(path: Path, *, allow_existing: bool = False) -> Path:
    """
    Return the resolved output directory.

    Rules
    -----
    * If the directory does not exist, it is created.
    * If the directory exists and is empty, it may be reused.
    * If the directory exists and is non-empty, ``allow_existing``
      must be True or ``FileExistsError`` is raised.
    * Directories are never automatically deleted.

    Returns the resolved absolute path.
    """
    resolved = path.resolve()
    if resolved.exists():
        if not resolved.is_dir():
            raise NotADirectoryError(f"Path exists but is not a directory: {resolved}")
        # Check if empty
        if any(resolved.iterdir()) and not allow_existing:
            raise FileExistsError(
                f"Output directory exists and is not empty: {resolved}. "
                "Set allow_existing=True to reuse."
            )
        return resolved
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _atomic_replace(src: Path, dst: Path) -> None:
    """
    Atomically replace *dst* with *src*.

    Uses ``os.replace`` (POSIX atomic rename).  On Windows this is
    atomic within the same volume.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(src), str(dst))


def atomic_write_json(path: Path, payload: object) -> None:
    """
    Atomically write a JSON payload to *path*.

    Writes to a temporary file in the same directory, then renames
    atomically.  If the write is interrupted, *path* is never left
    half-written — either the old file remains intact or the new
    file is fully written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"._{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            f.flush()
            os.fsync(fd)
        _atomic_replace(Path(tmp_path_str), path)
    except BaseException:
        # Clean up temp file on failure
        with contextlib.suppress(OSError):
            os.unlink(tmp_path_str)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write a text payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"._{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(fd)
        _atomic_replace(Path(tmp_path_str), path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path_str)
        raise


def atomic_write_jsonl(path: Path, rows: Iterable[object]) -> None:
    """
    Atomically write JSONL rows to *path*.

    Each item in *rows* is serialised as a single JSON line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f"._{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
            f.flush()
            os.fsync(fd)
        _atomic_replace(Path(tmp_path_str), path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path_str)
        raise
