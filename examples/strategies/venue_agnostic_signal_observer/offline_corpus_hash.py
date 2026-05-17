"""File hashing, hash cache, and deterministic data_corpus_hash.

Hash cache is keyed by (absolute path, file size bytes, mtime ns).
Cache lives at reports/venue_agnostic_signal_observer/offline_historical_hash_cache.json.

data_corpus_hash is SHA-256 over a canonical sorted JSON payload of all
source-file identities plus the schema version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .offline_historical_models import OfflineSourceFile

# ---------------------------------------------------------------------------
# Default cache path
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_PATH = Path(
    "reports/venue_agnostic_signal_observer/offline_historical_hash_cache.json"
)


# ---------------------------------------------------------------------------
# File SHA-256
# ---------------------------------------------------------------------------


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Compute SHA-256 of a file, reading in chunks for large files."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Hash cache
# ---------------------------------------------------------------------------


class HashCache:
    """Reusable per-session file hash cache backed by a JSON file on disk."""

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        self._path: Path = Path(cache_path) if cache_path else _DEFAULT_CACHE_PATH
        self._data: Dict[str, Any] = {}
        self._reused: int = 0
        self._recomputed: int = 0
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(self._data, dict):
                    self._data = {}
            except (json.JSONDecodeError, OSError):
                # Corrupt cache — ignore and start fresh.
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
        )

    @staticmethod
    def _cache_key(abs_path: str, size: int, mtime_ns: int) -> str:
        return f"{abs_path}|{size}|{mtime_ns}"

    def get_or_compute(
        self, path: Path, force_rehash: bool = False
    ) -> tuple[str, bool]:
        """Return (sha256_hex, was_reused).

        If ``force_rehash`` is True, always recomputes even if the cache entry
        is valid.
        """
        stat = path.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        abs_path = str(path.resolve())

        key = self._cache_key(abs_path, size, mtime_ns)

        if not force_rehash and key in self._data:
            entry = self._data[key]
            if (
                entry.get("file_size_bytes") == size
                and entry.get("mtime_ns") == mtime_ns
            ):
                self._reused += 1
                return entry["file_sha256"], True

        # Recompute
        sha = sha256_file(path)
        self._data[key] = {
            "file_sha256": sha,
            "computed_at_utc": datetime.now(timezone.utc).isoformat(),
            "file_size_bytes": size,
            "mtime_ns": mtime_ns,
        }
        self._save()
        self._recomputed += 1
        return sha, False

    @property
    def entries_reused(self) -> int:
        return self._reused

    @property
    def entries_recomputed(self) -> int:
        return self._recomputed


# ---------------------------------------------------------------------------
# data_corpus_hash
# ---------------------------------------------------------------------------


def _source_file_identity(sf: OfflineSourceFile) -> dict:
    """Canonical dict for one source file used in corpus hash computation."""
    return {
        "logical_source_id": sf.logical_source_id,
        "venue": sf.venue,
        "symbol": sf.symbol,
        "base_asset": sf.base_asset,
        "quote_asset": sf.quote_asset,
        "source_kind": sf.source_kind,
        "stream_type": sf.stream_type,
        "resolution_type": sf.resolution_type,
        "timestamp_unit": sf.timestamp_unit,
        "expected_start_ns": sf.expected_start_ns,
        "expected_end_ns": sf.expected_end_ns,
        "data_start_ns": sf.data_start_ns,
        "data_end_ns": sf.data_end_ns,
        "row_count": sf.row_count,
        "file_size_bytes": sf.file_size_bytes,
        "file_sha256": sf.file_sha256,
    }


def compute_data_corpus_hash(
    source_files: Sequence[OfflineSourceFile],
    schema_version: str,
) -> str:
    """Compute a deterministic SHA-256 over sorted source-file identities.

    Any change to a source file's SHA-256, row count, timestamp unit,
    or schema version changes the corpus hash.
    """
    identities = sorted(
        [_source_file_identity(sf) for sf in source_files],
        key=lambda d: d["logical_source_id"],
    )
    payload: dict = {
        "offline_data_schema_version": schema_version,
        "source_files": identities,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
