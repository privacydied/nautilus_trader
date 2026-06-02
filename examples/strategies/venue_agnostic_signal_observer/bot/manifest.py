"""
Approved candidate manifest.

A manifest records which candidate (by hash) the bot is allowed to execute,
under which exact conditions. The manifest alone is NOT sufficient to
authorize trading — current ledger state must be replayed and confirmed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_expiry_datetime(value: str) -> datetime:
    """Parse an ISO datetime string into a timezone-aware datetime.

    Normalizes trailing 'Z' to '+00:00' for fromisoformat compatibility.
    Naive datetimes (no offset) are assumed UTC.
    Raises ValueError or TypeError on malformed input.
    """
    normalized = value.rstrip("Z") if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ManifestRecord:
    candidate_hash: str
    grid_hash: str
    rule_description: str
    conditions: dict[str, Any]
    limits: dict[str, Any]
    manifest_hash: str
    created_at_utc: str
    expires_at_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_expired(self) -> bool:
        if self.expires_at_utc is None:
            return False
        now = datetime.now(timezone.utc)
        try:
            expires_dt = _parse_expiry_datetime(self.expires_at_utc)
        except (ValueError, TypeError):
            # Malformed expiry → fail closed: treat as expired
            return True
        return now >= expires_dt


@dataclass
class ApprovedManifest:
    records: list[ManifestRecord]
    manifest_file_hash: str

    def get(self, candidate_hash: str) -> ManifestRecord | None:
        for r in self.records:
            if r.candidate_hash == candidate_hash:
                return r
        return None


def _compute_manifest_hash(records_data: list[dict[str, Any]]) -> str:
    raw = json.dumps(records_data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def load_manifest(path: Path) -> ApprovedManifest:
    """Load and hash-verify a manifest file."""
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Manifest must be a JSON array of records")
    records = [ManifestRecord(**r) for r in data]
    file_hash = _compute_manifest_hash(data)
    return ApprovedManifest(records=records, manifest_file_hash=file_hash)
