"""Atomic IO helpers: deterministic JSON serialization and safe file writes."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def sha256_canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 hash of a JSON payload.

    Uses sorted keys, compact separators (no whitespace), and UTF-8 encoding.
    Equivalent dict ordering must produce identical hashes.
    """
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON file atomically using a same-directory temp file.

    Creates parent directories as needed.  Writes to a temp file in the same
    directory as *path*, then renames with os.replace() to avoid cross-filesystem
    EXDEV errors on NFS or mounted repos.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    data_bytes = data.encode("utf-8")

    tmp = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(data_bytes)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()

    os.replace(tmp.name, str(path))


def append_jsonl_durable(path: Path, payload: Mapping[str, Any]) -> None:
    """Append one canonical JSON line to a JSONL file.

    Creates parent directories as needed.  Calls fsync on the file handle
    after writing.  This is a durable append, not a general multi-writer
    atomicity guarantee — concurrent writers to the same file may interleave.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    with open(path, "ab") as f:
        f.write(data.encode("utf-8"))
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())


def read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file."""
    with open(path, "rb") as f:
        raw = f.read()
    return json.loads(raw.decode("utf-8"))