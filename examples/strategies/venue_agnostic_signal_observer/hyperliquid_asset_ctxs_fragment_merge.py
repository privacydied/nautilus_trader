"""Deterministic merge helper for validated Hyperliquid asset_ctxs fragments."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class FragmentMergeError(RuntimeError):
    """Raised when staged fragments cannot be merged safely."""


@dataclass(frozen=True)
class FragmentMergeResult:
    output_dir: str
    manifest_path: str
    manifest_hash: str
    jsonl_file_count: int
    total_row_count: int
    duplicate_rows_deduped: int


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_manifest_hash(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_hash", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    symbol = str(row.get("symbol") or "").strip().upper()
    ts = str(row.get("ts_event") or row.get("timestamp") or row.get("time") or "").strip()
    if not symbol or not ts:
        raise FragmentMergeError("ROW_MISSING_SYMBOL_OR_TIMESTAMP")
    return symbol, ts


def _conflict_payload(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        row.get("price", row.get("mark_price", row.get("markPx"))),
        row.get("open_interest", row.get("openInterest", row.get("oi"))),
        row.get("index_price", row.get("indexPx")),
        row.get("price_source"),
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FragmentMergeError(f"FRAGMENT_MANIFEST_MISSING path={path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _source_caveats(fragment: Path, manifest: dict[str, Any]) -> list[str]:
    caveats = list(manifest.get("coverage_caveats") or [])
    if fragment.name == "2024_q3":
        caveat = "2024_q3 final day truncated at 2024-09-30T19:56:00Z"
        if caveat not in caveats:
            caveats.append(caveat)
    return caveats


def _unusable_notes(fragment: Path, manifest: dict[str, Any]) -> dict[str, str]:
    notes = {str(k).upper(): str(v) for k, v in (manifest.get("unusable_symbol_notes") or {}).items()}
    if fragment.name == "2025_q4" and (fragment / "MKR.jsonl").exists():
        notes.setdefault("MKR", "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER")
    return notes


def _iter_jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            count += 1
    os.replace(tmp, path)
    return count


def merge_asset_ctxs_fragments(fragment_dirs: list[Path], output_dir: Path) -> FragmentMergeResult:
    if not fragment_dirs:
        raise FragmentMergeError("NO_FRAGMENTS_SUPPLIED")
    output_dir = Path(output_dir)
    tmp_dir = output_dir.with_name(output_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    tmp_dir.mkdir(parents=True)

    manifests: dict[Path, dict[str, Any]] = {}
    source_fragments: list[str] = []
    coverage_caveats: list[str] = []
    unusable_symbol_notes: dict[str, str] = {}
    symbol_sources: dict[str, list[Path]] = {}
    duplicate_rows_deduped = 0

    for fragment in fragment_dirs:
        fragment = Path(fragment)
        manifest = _read_manifest(fragment)
        manifests[fragment] = manifest
        source_fragments.append(str(fragment))
        for caveat in _source_caveats(fragment, manifest):
            if caveat not in coverage_caveats:
                coverage_caveats.append(caveat)
        unusable_symbol_notes.update(_unusable_notes(fragment, manifest))
        for jsonl in sorted(fragment.glob("*.jsonl")):
            symbol_sources.setdefault(jsonl.stem.upper(), []).append(jsonl)

    content_hashes: dict[str, str] = {}
    per_symbol_counts: dict[str, int] = {}
    first_last: dict[str, dict[str, str | None]] = {}
    all_dates: set[str] = set()
    total_rows = 0

    for symbol, paths in sorted(symbol_sources.items()):
        rows_by_ts: dict[str, dict[str, Any]] = {}
        conflict_by_ts: dict[str, tuple[Any, Any, Any, Any]] = {}
        for path in paths:
            for row in _iter_jsonl_rows(path):
                row_symbol, ts = _row_key(row)
                if row_symbol != symbol:
                    raise FragmentMergeError(f"ROW_SYMBOL_MISMATCH file_symbol={symbol} row_symbol={row_symbol}")
                payload = _conflict_payload(row)
                if ts in rows_by_ts:
                    if conflict_by_ts[ts] != payload:
                        raise FragmentMergeError(f"DUPLICATE_TIMESTAMP_CONFLICT symbol={symbol} ts={ts}")
                    duplicate_rows_deduped += 1
                    continue
                rows_by_ts[ts] = row
                conflict_by_ts[ts] = payload
        ordered_ts = sorted(rows_by_ts)
        out_path = tmp_dir / f"{symbol}.jsonl"
        count = _atomic_write_jsonl(out_path, (rows_by_ts[ts] for ts in ordered_ts))
        per_symbol_counts[symbol] = count
        total_rows += count
        if ordered_ts:
            first_last[symbol] = {"first_timestamp": ordered_ts[0], "last_timestamp": ordered_ts[-1]}
            all_dates.update(ts[:10] for ts in ordered_ts)
            content_hashes[symbol] = _sha256_file(out_path)
        else:
            first_last[symbol] = {"first_timestamp": None, "last_timestamp": None}

    date_list = sorted(all_dates)
    manifest = {
        "safety_mode": "public_s3_archive_only_observer_no_funding_values_merged_fragments",
        "source_fragments": source_fragments,
        "date_list": date_list,
        "first_date": date_list[0] if date_list else None,
        "last_date": date_list[-1] if date_list else None,
        "coverage_caveats": coverage_caveats,
        "unusable_symbol_notes": unusable_symbol_notes,
        "per_symbol_row_counts": per_symbol_counts,
        "first_last_timestamp": first_last,
        "content_hashes": content_hashes,
        "duplicate_rows_deduped": duplicate_rows_deduped,
        "total_rows": total_rows,
        "fragment_manifests": {str(path): {"manifest_hash": manifest.get("manifest_hash"), "date_count": len(manifest.get("date_list") or [])} for path, manifest in manifests.items()},
    }
    manifest["manifest_hash"] = _stable_manifest_hash(manifest)
    tmp_manifest = tmp_dir / "manifest.json.tmp"
    tmp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_manifest, tmp_dir / "manifest.json")
    os.replace(tmp_dir, output_dir)
    return FragmentMergeResult(
        output_dir=str(output_dir),
        manifest_path=str(output_dir / "manifest.json"),
        manifest_hash=str(manifest["manifest_hash"]),
        jsonl_file_count=len(per_symbol_counts),
        total_row_count=total_rows,
        duplicate_rows_deduped=duplicate_rows_deduped,
    )


def result_as_dict(result: FragmentMergeResult) -> dict[str, Any]:
    return dataclasses.asdict(result)
