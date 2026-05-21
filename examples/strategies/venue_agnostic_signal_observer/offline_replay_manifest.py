"""
Manifest writer and corpus compatibility guard for offline historical Phase 1.

Writes per-run manifests to:
  <out_dir>/<run_id>/offline_prepare_manifest.json

Never overwrites an existing output directory unless --overwrite is explicitly
requested.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Dict
from typing import List
from typing import Sequence

from .offline_corpus_hash import compute_data_corpus_hash
from .offline_historical_models import OFFLINE_DATA_SCHEMA_VERSION
from .offline_historical_models import OfflineBarRecord
from .offline_historical_models import OfflinePrepareManifest
from .offline_historical_models import OfflineSourceFile
from .offline_historical_models import OfflineTradeRecord


# ---------------------------------------------------------------------------
# Precommitment hash
# ---------------------------------------------------------------------------


def compute_precommitment_hash(precommitment_path: Path) -> str:
    """SHA-256 of the raw precommitment file bytes."""
    return hashlib.sha256(precommitment_path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    run_id: str,
    git_sha: str,
    source_files: List[OfflineSourceFile],
    trades_by_stream: Dict[str, List[OfflineTradeRecord]],
    bars_by_stream: Dict[str, List[OfflineBarRecord]],
    hash_cache_used: bool,
    hash_cache_entries_reused: int,
    hash_cache_entries_recomputed: int,
    precommitment_hash: str | None,
    timestamp_validation_meta: dict | None = None,
    schema_version: str = OFFLINE_DATA_SCHEMA_VERSION,
) -> OfflinePrepareManifest:

    data_corpus_hash = compute_data_corpus_hash(source_files, schema_version)

    all_start = [sf.data_start_ns for sf in source_files if sf.data_start_ns]
    all_end = [sf.data_end_ns for sf in source_files if sf.data_end_ns]
    normalized_time_range = {
        "global_start_ns": min(all_start) if all_start else None,
        "global_end_ns": max(all_end) if all_end else None,
    }

    stream_counts: Dict[str, int] = {}
    for lsid, trades in trades_by_stream.items():
        stream_counts[lsid] = len(trades)
    for lsid, bars in bars_by_stream.items():
        stream_counts[lsid] = stream_counts.get(lsid, 0) + len(bars)

    resolution_summary: Dict[str, int] = {}
    for trades in trades_by_stream.values():
        for t in trades:
            resolution_summary[t.resolution_type] = (
                resolution_summary.get(t.resolution_type, 0) + 1
            )
    for bars_list in bars_by_stream.values():
        for b in bars_list:
            resolution_summary[b.resolution_type] = (
                resolution_summary.get(b.resolution_type, 0) + 1
            )

    quote_currency_summary: Dict[str, int] = {}
    for sf in source_files:
        q = sf.quote_asset
        quote_currency_summary[q] = quote_currency_summary.get(q, 0) + 1

    all_ok = all(
        sf.file_sha256 and sf.row_count >= 0 and sf.data_start_ns and sf.data_end_ns
        for sf in source_files
    )

    return OfflinePrepareManifest(
        run_id=run_id,
        phase="offline_historical_prepare",
        generated_at_utc=datetime.now(UTC).isoformat(),
        git_sha=git_sha,
        schema_version=schema_version,
        precommitment_hash=precommitment_hash,
        data_corpus_hash=data_corpus_hash,
        source_files=[_source_file_to_dict(sf) for sf in source_files],
        timestamp_validation=timestamp_validation_meta or {},
        hash_cache_used=hash_cache_used,
        hash_cache_entries_reused=hash_cache_entries_reused,
        hash_cache_entries_recomputed=hash_cache_entries_recomputed,
        normalized_time_range=normalized_time_range,
        stream_counts=stream_counts,
        resolution_summary=resolution_summary,
        quote_currency_summary=quote_currency_summary,
        safety="public_data_observer_only",
        forbidden_capabilities_present=False,
        next_phase_allowed=all_ok and len(source_files) > 0,
    )


def _source_file_to_dict(sf: OfflineSourceFile) -> dict:
    return {
        "path": sf.path,
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
        "file_size_bytes": sf.file_size_bytes,
        "mtime_ns": sf.mtime_ns,
        "file_sha256": sf.file_sha256,
        "row_count": sf.row_count,
        "data_start_ns": sf.data_start_ns,
        "data_end_ns": sf.data_end_ns,
    }


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------


def write_manifest(
    manifest: OfflinePrepareManifest,
    out_dir: Path,
    overwrite: bool = False,
) -> Path:
    """
    Write manifest JSON to <out_dir>/<run_id>/offline_prepare_manifest.json.

    Raises FileExistsError if the run directory already exists and
    ``overwrite`` is False.
    """
    run_dir = out_dir / manifest.run_id
    if run_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Output directory {run_dir} already exists. "
            f"Pass overwrite=True or --overwrite to force."
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "offline_prepare_manifest.json"

    payload: dict = {
        "run_id": manifest.run_id,
        "phase": manifest.phase,
        "generated_at_utc": manifest.generated_at_utc,
        "git_sha": manifest.git_sha,
        "schema_version": manifest.schema_version,
        "precommitment_hash": manifest.precommitment_hash,
        "data_corpus_hash": manifest.data_corpus_hash,
        "source_files": manifest.source_files,
        "timestamp_validation": manifest.timestamp_validation,
        "hash_cache_used": manifest.hash_cache_used,
        "hash_cache_entries_reused": manifest.hash_cache_entries_reused,
        "hash_cache_entries_recomputed": manifest.hash_cache_entries_recomputed,
        "normalized_time_range": manifest.normalized_time_range,
        "stream_counts": manifest.stream_counts,
        "resolution_summary": manifest.resolution_summary,
        "quote_currency_summary": manifest.quote_currency_summary,
        "safety": manifest.safety,
        "forbidden_capabilities_present": manifest.forbidden_capabilities_present,
        "next_phase_allowed": manifest.next_phase_allowed,
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


# ---------------------------------------------------------------------------
# Corpus compatibility guard
# ---------------------------------------------------------------------------


def assert_compatible_offline_corpus(manifests: Sequence[OfflinePrepareManifest]) -> None:
    """
    Raise ValueError if manifests cannot be safely mixed.

    Checks:
    - data_corpus_hash must all match
    - schema_version must all match
    - non-null precommitment_hash values must all match
    """
    if len(manifests) < 2:
        return

    first = manifests[0]

    for i, m in enumerate(manifests[1:], start=1):
        if m.data_corpus_hash != first.data_corpus_hash:
            raise ValueError(
                f"Corpus mixing rejected: manifest[{i}].data_corpus_hash "
                f"{m.data_corpus_hash!r} != manifest[0].data_corpus_hash "
                f"{first.data_corpus_hash!r}"
            )
        if m.schema_version != first.schema_version:
            raise ValueError(
                f"Corpus mixing rejected: manifest[{i}].schema_version "
                f"{m.schema_version!r} != manifest[0].schema_version "
                f"{first.schema_version!r}"
            )
        if (
            m.precommitment_hash is not None
            and first.precommitment_hash is not None
            and m.precommitment_hash != first.precommitment_hash
        ):
            raise ValueError(
                f"Corpus mixing rejected: manifest[{i}].precommitment_hash "
                f"{m.precommitment_hash!r} != manifest[0].precommitment_hash "
                f"{first.precommitment_hash!r}"
            )
