"""Phase 0C falsification/null validation for liquidation flush aftershock reversal.

This module is research-only. It verifies the frozen Phase 0A/0B artifacts, builds
archive-backed placebo candidates once, and either emits a non-validating profile
report or executes the precommitted null diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import random
import statistics
import time
import csv
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    load_archive_rows,
    parse_timestamp,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import (
    PRIMARY_COST_BPS,
    PRIMARY_HORIZON_HOURS,
    build_price_series,
    evaluate_events,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import atomic_write_json, atomic_write_text

EXPECTED_PHASE0A_EVENT_HASH = None
EXPECTED_PHASE0C_PRECOMMITMENT_HASH = "9e4e9a030b1e6d658a175800332a494627c0abb2cf97f23e43073fb8f476663d"
ALTCOIN_EXCLUDED_SYMBOLS = {"BTC", "ETH"}

STATUS_PROFILE_RUN = "NON_VALIDATING_PROFILE_RUN"
STATUS_NULL_VALIDATED_PASS = "PHASE0C_FALSIFICATION_PASSED_DIAGNOSTIC"
STATUS_NULL_REJECTED_PLACEBO_MATCH = "PLACEBO_EXPLAINED"
STATUS_NULL_REJECTED_MONTH = "CALENDAR_REGIME_EXPLAINED"
STATUS_NULL_REJECTED_CLUSTERING = "CLUSTERING_EXPLAINED"
STATUS_NULL_REJECTED_CLUSTERING_WITH_SURVIVORSHIP_AMBIGUITY = "PHASE0C_CLUSTERING_EXPLAINED_WITH_SURVIVORSHIP_AMBIGUITY"
STATUS_COST_FRAGILE = "COST_FRAGILE"
STATUS_SURVIVORSHIP_AMBIGUITY = "SURVIVORSHIP_AMBIGUITY"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"
STATUS_ERROR = "PHASE0C_ERROR"
STATUS_INSUFFICIENT_NULL_COVERAGE = "PHASE0C_INSUFFICIENT_NULL_COVERAGE"
STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES = "PHASE0C_PRIMARY_NULL_INSUFFICIENT_CANDIDATES"
STATUS_MONTH_NULL_INSUFFICIENT_CANDIDATES = "PHASE0C_MONTH_NULL_INSUFFICIENT_CANDIDATES"
STATUS_MONTH_NULL_NOT_APPLICABLE = "MONTH_NULL_NOT_APPLICABLE_INSUFFICIENT_CANDIDATES"


class SourceArtifactMismatch(RuntimeError):
    """Raised when frozen source artifacts do not match their declared hashes."""


class Phase0BReproductionFailed(RuntimeError):
    """Raised when Phase 0B 24h metrics cannot be reproduced from disk."""


class Phase0CIncomplete(RuntimeError):
    """Raised when a partial report must not be treated as complete."""


class Phase0CCacheInvalid(RuntimeError):
    """Raised when a Phase 0C cache is missing, corrupt, or lacks metadata."""


CACHE_SCHEMA_VERSION = "phase0c-cache-v2"


@dataclass(frozen=True)
class RealPrimaryMetrics:
    gross_mean_bps: float
    net_mean_bps: float
    net_median_bps: float
    win_rate: float
    event_count: int
    missing_forward_count: int = 0


@dataclass(frozen=True)
class NullSummary:
    iterations: int
    coverage: float
    mean: float
    median: float
    win_rate: float
    lower_confidence_bound: float
    p_value: float
    mean_95th: float = 0.0
    win_rate_p95: float = 0.0
    median_distribution_median: float = 0.0
    status: str = "OK"
    missing_candidate_details: list[dict[str, Any]] | None = None

    @property
    def missing_candidate_count(self) -> int:
        return len(self.missing_candidate_details or [])


@dataclass(frozen=True)
class Phase0CCache:
    rows: list[ArchiveRow]
    price_series: dict[str, list[tuple[datetime, float]]]
    symbol_rows: dict[str, list[ArchiveRow]]
    build_elapsed_seconds: float
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class Phase0CLightCache:
    price_series: dict[str, list[tuple[datetime, float]]]
    symbol_rows: dict[str, list[ArchiveRow]]
    build_elapsed_seconds: float
    diagnostics: dict[str, Any]


def parse_ts(ts_str: str) -> datetime:
    return parse_timestamp(ts_str)


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def read_precommitment_hash(precommitment_path: Path) -> str:
    for line in precommitment_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Precommitment SHA-256 (self):"):
            return line.split(":", 1)[1].strip()
    raise ValueError("precommitment hash line missing")


def compute_precommitment_hash(precommitment_path: Path) -> str:
    raw = precommitment_path.read_bytes()
    kept: list[bytes] = []
    removed = False
    for line in raw.splitlines(keepends=True):
        if not removed and line.startswith(b"Precommitment SHA-256 (self):"):
            removed = True
            continue
        kept.append(line)
    if not removed:
        raise ValueError("precommitment self-hash line missing")
    return hashlib.sha256(b"".join(kept)).hexdigest()


def precommitment_recorded_and_computed(path: Path) -> tuple[str, str]:
    return read_precommitment_hash(path), compute_precommitment_hash(path)


def _jsonl_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_phase0a_source(phase0a_dir: Path, expected_event_hash: str | None = None) -> None:
    summary = json.loads((phase0a_dir / "summary.json").read_text(encoding="utf-8"))
    declared = summary.get("accepted_events_jsonl_sha256")
    if not declared:
        raise SourceArtifactMismatch("Phase0A summary missing accepted_events_jsonl_sha256")
    artifact = phase0a_dir / str(summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl")
    actual = _jsonl_sha256(artifact)
    if declared != actual:
        raise SourceArtifactMismatch(
            f"Phase0A artifact hash mismatch: declared={declared} actual={actual}"
        )
    if expected_event_hash is not None and declared != expected_event_hash:
        raise SourceArtifactMismatch(
            f"Phase0A artifact hash mismatch: expected={expected_event_hash} declared={declared}"
        )


def validate_event_universe(events: Sequence[dict[str, Any]]) -> None:
    bad = sorted({str(ev.get("symbol", "")).upper() for ev in events if str(ev.get("symbol", "")).upper() in ALTCOIN_EXCLUDED_SYMBOLS})
    if bad:
        raise SourceArtifactMismatch(f"BTC/ETH events are not allowed in Phase 0C: {bad}")


def _verify_precommitment_hash(precommitment_path: Path, expected_hash: str = EXPECTED_PHASE0C_PRECOMMITMENT_HASH) -> str:
    recorded, computed = precommitment_recorded_and_computed(precommitment_path)
    if recorded != computed or computed != expected_hash:
        raise SourceArtifactMismatch(f"Phase0C precommitment hash mismatch: recorded={recorded} computed={computed} expected={expected_hash}")
    return computed


def _verify_artifact_hashes(phase0a_report_path: Path, phase0b_report_path: Path, expected_phase0a_hash: str | None = None) -> dict[str, Any]:
    verify_phase0a_source(phase0a_report_path, expected_phase0a_hash)
    phase0a_summary = json.loads((phase0a_report_path / "summary.json").read_text(encoding="utf-8"))
    phase0b_summary = json.loads((phase0b_report_path / "summary.json").read_text(encoding="utf-8"))
    phase0b_hash = phase0b_summary.get("phase0a_event_artifact_sha256")
    if phase0b_hash != expected_phase0a_hash:
        raise SourceArtifactMismatch(f"Phase0B source hash mismatch: expected={expected_phase0a_hash} phase0b={phase0b_hash}")
    artifact_path = phase0a_report_path / str(phase0a_summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl")
    return {
        "phase0a_event_artifact_hash": expected_phase0a_hash,
        "phase0a_event_artifact_path": str(artifact_path),
        "phase0a_artifact_hash_verified": True,
        "phase0b_report_verified": True,
    }


def _compute_effective_n(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return 1.0
    mean_abs = statistics.mean(abs(v) for v in vals)
    if mean_abs == 0:
        return float(len(vals))
    variance = statistics.variance(vals)
    dispersion_penalty = 1.0 + variance / (mean_abs * mean_abs)
    return max(1.0, len(vals) / dispersion_penalty)


def _compute_cost_stress(gross_mean_bps: float, costs: Sequence[float] = (50.0, 75.0, 100.0)) -> dict[str, float]:
    gross = float(gross_mean_bps)
    return {f"net_mean_bps_{int(cost)}": gross - float(cost) for cost in costs}


def _compute_survivorship_audit_from_symbols(archive_symbols_input: Sequence[str], real_events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    archive_symbols = {str(symbol).upper() for symbol in archive_symbols_input if str(symbol).upper() not in ALTCOIN_EXCLUDED_SYMBOLS}
    event_symbols = {str(ev.get("symbol", "")).upper() for ev in real_events}
    missing = sorted(event_symbols - archive_symbols)
    status = STATUS_SURVIVORSHIP_AMBIGUITY
    return {
        "universe_description": "archive_symbols_observed_through_time_without_independent_listing_master",
        "total_symbols_in_archive": len(archive_symbols),
        "symbols_in_research_universe": len(event_symbols),
        "missing_symbols": missing,
        "survivorship_status": status,
    }


def _compute_survivorship_audit(archive_rows: Sequence[ArchiveRow], real_events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return _compute_survivorship_audit_from_symbols([row.symbol for row in archive_rows], real_events)


def perform_survivorship_audit(archive_path: Path, real_events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows, _ = load_archive_rows([archive_path])
    return _compute_survivorship_audit(rows, real_events)


def _build_cache(archive_path: Path) -> Phase0CLightCache:
    start = time.perf_counter()
    print(f"PHASE0C_CACHE_BUILD_START archive_path={archive_path}", flush=True)
    rows, diagnostics = load_archive_rows([archive_path])
    print(f"PHASE0C_CACHE_ARCHIVE_LOADED rows={len(rows)} elapsed_seconds={time.perf_counter() - start:.3f}", flush=True)
    symbol_rows: dict[str, list[ArchiveRow]] = {}
    for row in rows:
        if row.symbol.upper() in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        symbol_rows.setdefault(row.symbol.upper(), []).append(row)
    for rows_for_symbol in symbol_rows.values():
        rows_for_symbol.sort(key=lambda row: row.timestamp)
    elapsed = time.perf_counter() - start
    diag = diagnostics if isinstance(diagnostics, dict) else asdict(diagnostics)
    diag["loaded_rows"] = len(rows)
    diag["cache_symbol_count"] = len(symbol_rows)
    print(f"PHASE0C_CACHE_BUILD_READY symbols={len(symbol_rows)} elapsed_seconds={elapsed:.3f}", flush=True)
    return Phase0CLightCache(price_series={}, symbol_rows=symbol_rows, build_elapsed_seconds=elapsed, diagnostics=diag)


def _build_profile_cache(phase0a_report_path: Path, archive_path: Path) -> Phase0CLightCache:
    start = time.perf_counter()
    phase0a_summary = json.loads((phase0a_report_path / "summary.json").read_text(encoding="utf-8"))
    events = _load_jsonl(phase0a_report_path / str(phase0a_summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl"))
    symbols = sorted({str(event["symbol"]).upper() for event in events})
    paths = [archive_path / f"{symbol}.jsonl" for symbol in symbols if (archive_path / f"{symbol}.jsonl").exists()]
    rows, diagnostics = load_archive_rows(paths)
    price_series = build_price_series(rows)
    symbol_rows: dict[str, list[ArchiveRow]] = {}
    for row in rows:
        symbol_rows.setdefault(row.symbol.upper(), []).append(row)
    for rows_for_symbol in symbol_rows.values():
        rows_for_symbol.sort(key=lambda row: row.timestamp)
    elapsed = time.perf_counter() - start
    diag = diagnostics if isinstance(diagnostics, dict) else asdict(diagnostics)
    diag["profile_symbol_file_count"] = len(paths)
    return Phase0CLightCache(price_series=price_series, symbol_rows=symbol_rows, build_elapsed_seconds=elapsed, diagnostics=diag)


def _cache_metadata_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(cache_path.suffix + ".meta.json")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _archive_manifest_hash(archive_path: Path) -> str:
    h = hashlib.sha256()
    paths = [archive_path] if archive_path.is_file() else sorted(p for p in archive_path.rglob("*") if p.is_file())
    for path in paths:
        if path.name.endswith("manifest.json") or path.suffix.lower() not in {".csv", ".jsonl", ".json", ".parquet"}:
            continue
        rel = str(path.relative_to(archive_path) if archive_path.is_dir() else path.name)
        stat = path.stat()
        h.update(rel.encode())
        h.update(str(stat.st_size).encode())
        h.update(str(stat.st_mtime_ns).encode())
    return h.hexdigest()


def _cache_row_bounds(cache: Phase0CLightCache) -> tuple[int, int, str | None, str | None]:
    row_count = 0
    first: datetime | None = None
    last: datetime | None = None
    for rows in cache.symbol_rows.values():
        row_count += len(rows)
        if not rows:
            continue
        local_first = rows[0].timestamp
        local_last = rows[-1].timestamp
        first = local_first if first is None or local_first < first else first
        last = local_last if last is None or local_last > last else last
    return row_count, len(cache.symbol_rows), utc_iso(first) if first else None, utc_iso(last) if last else None


def _build_cache_metadata(cache_path: Path, cache: Phase0CLightCache, *, archive_path: Path, precommitment_sha256: str, build_started_utc: str, build_finished_utc: str) -> dict[str, Any]:
    row_count, symbol_count, first_ts, last_ts = _cache_row_bounds(cache)
    cache_sha = _sha256_file(cache_path)
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "source_archive_path": str(archive_path),
        "archive_manifest_hash": _archive_manifest_hash(archive_path),
        "row_count": row_count,
        "symbol_count": symbol_count,
        "first_timestamp_utc": first_ts,
        "last_timestamp_utc": last_ts,
        "build_started_utc": build_started_utc,
        "build_finished_utc": build_finished_utc,
        "precommitment_sha256": precommitment_sha256,
        "cache_sha256": cache_sha,
        "cache_size_bytes": cache_path.stat().st_size,
        "cache_path": str(cache_path),
    }


def _load_validated_cache(cache_path: Path) -> Phase0CLightCache:
    metadata_path = _cache_metadata_path(cache_path)
    if not cache_path.exists():
        raise Phase0CCacheInvalid(f"cache missing: {cache_path}")
    if cache_path.with_suffix(cache_path.suffix + ".tmp").exists():
        raise Phase0CCacheInvalid(f"incomplete temp cache present: {cache_path.with_suffix(cache_path.suffix + '.tmp')}")
    if not metadata_path.exists():
        raise Phase0CCacheInvalid(f"cache metadata sidecar missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise Phase0CCacheInvalid(f"cache metadata unreadable: {metadata_path}: {exc}") from exc
    if metadata.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        raise Phase0CCacheInvalid(f"cache schema mismatch: {metadata.get('cache_schema_version')}")
    if not metadata.get("precommitment_sha256"):
        raise Phase0CCacheInvalid("cache metadata missing precommitment_sha256")
    if metadata.get("cache_path") != str(cache_path):
        raise Phase0CCacheInvalid(f"cache path mismatch: metadata={metadata.get('cache_path')} actual={cache_path}")
    if metadata.get("cache_size_bytes") != cache_path.stat().st_size:
        raise Phase0CCacheInvalid("cache size mismatch between metadata and payload")
    actual_sha = _sha256_file(cache_path)
    if metadata.get("cache_sha256") != actual_sha:
        raise Phase0CCacheInvalid(f"cache hash mismatch: metadata={metadata.get('cache_sha256')} actual={actual_sha}")
    try:
        with cache_path.open("rb") as f:
            cached = pickle.load(f)
    except Exception as exc:
        raise Phase0CCacheInvalid(f"cache pickle unreadable: {cache_path}: {exc}") from exc
    if not isinstance(cached, Phase0CLightCache):
        raise Phase0CCacheInvalid(f"cache has invalid type: {type(cached).__name__}")
    row_count, symbol_count, _, _ = _cache_row_bounds(cached)
    if metadata.get("row_count") != row_count or metadata.get("symbol_count") != symbol_count:
        raise Phase0CCacheInvalid("cache metadata row/symbol counts do not match payload")
    return cached


def _write_cache_atomic(cache_path: Path, cache: Phase0CLightCache, *, archive_path: Path, precommitment_sha256: str, build_started_utc: str, build_finished_utc: str) -> dict[str, Any]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_meta_path = _cache_metadata_path(cache_path).with_suffix(_cache_metadata_path(cache_path).suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)
    tmp_meta_path.unlink(missing_ok=True)
    with tmp_path.open("wb") as f:
        pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(cache_path)
    metadata = _build_cache_metadata(
        cache_path,
        cache,
        archive_path=archive_path,
        precommitment_sha256=precommitment_sha256,
        build_started_utc=build_started_utc,
        build_finished_utc=build_finished_utc,
    )
    tmp_meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tmp_meta_path.open("rb") as f:
        os.fsync(f.fileno())
    tmp_meta_path.replace(_cache_metadata_path(cache_path))
    _load_validated_cache(cache_path)
    return metadata


def _load_or_build_cache(cache_path: Path, create: Callable[[], Phase0CLightCache], archive_path: Path | None = None, precommitment_sha256: str = "") -> Phase0CLightCache:
    if not precommitment_sha256:
        raise Phase0CCacheInvalid("precommitment_sha256 is required to build or load Phase 0C cache")
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    if tmp_path.exists():
        print(f"PHASE0C_CACHE_TMP_IGNORED path={tmp_path} size={tmp_path.stat().st_size}", flush=True)
        tmp_path.unlink(missing_ok=True)
    if cache_path.exists():
        return _load_validated_cache(cache_path)
    if archive_path is None:
        archive_path = cache_path.parent
    build_started = utc_iso(datetime.now(UTC))
    result = create()
    build_finished = utc_iso(datetime.now(UTC))
    _write_cache_atomic(cache_path, result, archive_path=archive_path, precommitment_sha256=precommitment_sha256, build_started_utc=build_started, build_finished_utc=build_finished)
    return result


def build_phase0c_cache_only(archive_path: Path, cache_path: Path, precommitment_sha256: str) -> dict[str, Any]:
    build_started = utc_iso(datetime.now(UTC))
    start = time.perf_counter()
    cache = _build_cache(archive_path)
    build_finished = utc_iso(datetime.now(UTC))
    metadata = _write_cache_atomic(
        cache_path,
        cache,
        archive_path=archive_path,
        precommitment_sha256=precommitment_sha256,
        build_started_utc=build_started,
        build_finished_utc=build_finished,
    )
    elapsed = time.perf_counter() - start
    print(
        f"PHASE0C_CACHE_READY path={cache_path} size={cache_path.stat().st_size} rows={metadata['row_count']} symbols={metadata['symbol_count']} elapsed_seconds={elapsed:.3f}",
        flush=True,
    )
    return {
        "status": "PHASE0C_CACHE_READY",
        "cache_path": str(cache_path),
        "cache_metadata_path": str(_cache_metadata_path(cache_path)),
        "cache_size_bytes": cache_path.stat().st_size,
        "cache_build_elapsed_seconds": elapsed,
        "cache_metadata": metadata,
    }

def build_symbol_month_candidates(rows: Sequence[ArchiveRow]) -> dict[tuple[str, int, int], list[ArchiveRow]]:
    out: dict[tuple[str, int, int], list[ArchiveRow]] = {}
    for row in rows:
        if row.symbol.upper() in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        out.setdefault((row.symbol.upper(), row.timestamp.year, row.timestamp.month), []).append(row)
    for pool in out.values():
        pool.sort(key=lambda row: row.timestamp)
    return out


def build_eligible_placebo_candidates(rows: Sequence[ArchiveRow], real_events: Sequence[dict[str, Any]], min_hours_exclude: float = 48.0) -> dict[tuple[str, int, int], list[ArchiveRow]]:
    by_month = build_symbol_month_candidates(rows)
    real_ts_by_symbol: dict[str, list[datetime]] = {}
    for event in real_events:
        real_ts_by_symbol.setdefault(str(event["symbol"]).upper(), []).append(parse_ts(event["event_timestamp_utc"]))
    eligible: dict[tuple[str, int, int], list[ArchiveRow]] = {}
    delta = timedelta(hours=min_hours_exclude)
    for key, pool in by_month.items():
        symbol, _, _ = key
        blocked = [(ts - delta, ts + delta) for ts in real_ts_by_symbol.get(symbol, [])]
        filtered = [row for row in pool if not any(start <= row.timestamp <= end for start, end in blocked)]
        if filtered:
            eligible[key] = filtered
    return eligible


def _future_price(symbol_rows: Sequence[ArchiveRow], event_ts: datetime, horizon_hours: int) -> float | None:
    target = event_ts + timedelta(hours=horizon_hours)
    lo, hi = 0, len(symbol_rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if symbol_rows[mid].timestamp < target:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(symbol_rows):
        return None
    row = symbol_rows[lo]
    if abs((row.timestamp - target).total_seconds()) > 65 * 60:
        return None
    return row.price


def _directional_return_bps(direction: str, event_price: float, future_price: float) -> float:
    if direction in {"downside_liquidation_flush", "long_wipe"}:
        return (future_price / event_price - 1.0) * 10000.0
    if direction in {"upside_liquidation_flush", "short_squeeze"}:
        return (event_price / future_price - 1.0) * 10000.0
    raise ValueError(f"ambiguous event direction: {direction}")


def compute_forward_return_24h(price_t: float, price_t_plus_24h: float, cost_bps: float) -> float:
    return (price_t_plus_24h / price_t - 1.0) * 10000.0 - cost_bps


def compute_phase0b_metrics(events: Sequence[dict[str, Any]], archive_rows: Sequence[ArchiveRow]) -> RealPrimaryMetrics:
    price_series = build_price_series(archive_rows)
    evaluations, metrics, _ = evaluate_events(events, price_series)
    primary = next(m for m in metrics if m.horizon_hours == PRIMARY_HORIZON_HOURS)
    if primary.gross_mean_bps is None or primary.net_mean_bps_50bps is None or primary.net_median_bps_50bps is None or primary.win_rate_50bps is None:
        raise Phase0BReproductionFailed("No reproducible 24h Phase0B metrics")
    return RealPrimaryMetrics(
        gross_mean_bps=float(primary.gross_mean_bps),
        net_mean_bps=float(primary.net_mean_bps_50bps),
        net_median_bps=float(primary.net_median_bps_50bps),
        win_rate=float(primary.win_rate_50bps),
        event_count=int(primary.evaluated_event_count),
        missing_forward_count=int(primary.missing_forward_count),
    )


def _reproduce_phase0b_from_evaluation_csv(phase0b_report_path: Path, phase0b_summary: dict[str, Any]) -> RealPrimaryMetrics:
    csv_path = phase0b_report_path / "event_horizon_evaluations.csv"
    vals: list[dict[str, float]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["horizon_hours"]) != PRIMARY_HORIZON_HOURS:
                continue
            if str(row.get("missing_forward", "")).lower() == "true" or not row.get("gross_return_bps"):
                continue
            vals.append({
                "gross": float(row["gross_return_bps"]),
                "net": float(row["net_return_bps_50bps"]),
            })
    if not vals:
        raise Phase0BReproductionFailed("Phase0B evaluation CSV has no 24h rows")
    primary = next((m for m in phase0b_summary.get("horizon_metrics", []) if m.get("horizon_hours") == PRIMARY_HORIZON_HOURS), {})
    missing = int(primary.get("missing_forward_count", 0))
    return RealPrimaryMetrics(
        gross_mean_bps=statistics.mean(v["gross"] for v in vals),
        net_mean_bps=statistics.mean(v["net"] for v in vals),
        net_median_bps=statistics.median(v["net"] for v in vals),
        win_rate=sum(1 for v in vals if v["net"] > 0) / len(vals),
        event_count=len(vals),
        missing_forward_count=missing,
    )


def _reproduce_phase0b_or_raise(real: RealPrimaryMetrics, phase0b_summary: dict[str, Any], tolerance: float = 1e-6) -> None:
    primary = next((m for m in phase0b_summary.get("horizon_metrics", []) if m.get("horizon_hours") == PRIMARY_HORIZON_HOURS), None)
    if not primary:
        raise Phase0BReproductionFailed("Phase0B summary missing 24h metrics")
    checks = {
        "gross_mean_bps": (real.gross_mean_bps, primary.get("gross_mean_bps")),
        "net_mean_bps_50bps": (real.net_mean_bps, primary.get("net_mean_bps_50bps")),
        "net_median_bps_50bps": (real.net_median_bps, primary.get("net_median_bps_50bps")),
        "win_rate_50bps": (real.win_rate, primary.get("win_rate_50bps")),
    }
    for name, (actual, expected) in checks.items():
        if expected is None or abs(float(actual) - float(expected)) > tolerance:
            raise Phase0BReproductionFailed(f"Phase0B {name} reproduction mismatch: actual={actual} expected={expected}")
    if real.event_count != int(primary.get("evaluated_event_count", -1)):
        raise Phase0BReproductionFailed(f"Phase0B evaluated event count mismatch: actual={real.event_count} expected={primary.get('evaluated_event_count')}")


def _candidate_coverage(real_events: Sequence[dict[str, Any]], eligible_candidates: dict[Any, list[ArchiveRow]]) -> dict[str, Any]:
    missing: list[dict[str, Any]] = []
    with_candidate = 0
    for event in real_events:
        symbol = str(event["symbol"]).upper()
        event_ts = parse_ts(event["event_timestamp_utc"])
        key = (symbol, event_ts.year, event_ts.month)
        pool = eligible_candidates.get(key) or eligible_candidates.get(symbol) or []
        if pool:
            with_candidate += 1
            continue
        missing.append({"symbol": symbol, "month": f"{event_ts:%Y-%m}", "event_timestamp_utc": utc_iso(event_ts)})
    required = len(real_events)
    return {
        "required_events": required,
        "events_with_candidate": with_candidate,
        "missing_candidate_count": len(missing),
        "missing_candidate_examples": missing[:10],
        "coverage_ratio": with_candidate / required if required else 1.0,
    }


def sample_matched_placebo_events(real_events: Sequence[dict[str, Any]], eligible_candidates: dict[Any, list[ArchiveRow]], *, seed: int, horizon_hours: int, min_hours_exclude: float = 48.0) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for event in real_events:
        symbol = str(event["symbol"]).upper()
        event_ts = parse_ts(event["event_timestamp_utc"])
        key = (symbol, event_ts.year, event_ts.month)
        pool = eligible_candidates.get(key) or eligible_candidates.get(symbol) or []
        if not pool:
            raise RuntimeError(f"No placebo candidate found for {symbol} {event_ts:%Y-%m}")
        choice = rng.choice(pool)
        placebo = dict(event)
        placebo["event_timestamp_utc"] = utc_iso(choice.timestamp)
        placebo["price_t"] = choice.price
        placebo["event_direction"] = event.get("event_direction", "downside_liquidation_flush")
        out.append(placebo)
    return out


def shuffle_event_directions(events: Sequence[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        if rng.random() < 0.5:
            item["event_direction"] = "upside_liquidation_flush" if event.get("event_direction") == "downside_liquidation_flush" else "downside_liquidation_flush"
        out.append(item)
    return out


def circular_shift_events(events: Sequence[dict[str, Any]], days_offset: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        item["event_timestamp_utc"] = utc_iso(parse_ts(event["event_timestamp_utc"]) + timedelta(days=days_offset))
        out.append(item)
    return out


def _event_returns(events: Sequence[dict[str, Any]], symbol_rows: dict[str, list[ArchiveRow]], horizon_hours: int = PRIMARY_HORIZON_HOURS, cost_bps: float = PRIMARY_COST_BPS) -> list[float]:
    returns: list[float] = []
    for event in events:
        symbol = str(event["symbol"]).upper()
        future = _future_price(symbol_rows.get(symbol, []), parse_ts(event["event_timestamp_utc"]), horizon_hours)
        if future is None:
            continue
        gross = _directional_return_bps(str(event.get("event_direction") or event.get("flush_side") or ""), float(event["price_t"]), future)
        returns.append(gross - cost_bps)
    return returns


def _summarize_distribution(iteration_returns: Sequence[Sequence[float]], real_value: float) -> NullSummary:
    means = [statistics.mean(vals) for vals in iteration_returns if vals]
    medians = [statistics.median(vals) for vals in iteration_returns if vals]
    win_rates = [sum(1 for v in vals if v > 0) / len(vals) for vals in iteration_returns if vals]
    if not means:
        return NullSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, status="NO_COVERAGE")
    sorted_means = sorted(means)
    p95 = sorted_means[min(len(sorted_means) - 1, math.ceil(0.95 * len(sorted_means)) - 1)]
    p_value = empirical_p_value(real_value, means, higher_is_better=True)
    return NullSummary(
        iterations=len(means),
        coverage=len(means) / max(1, len(iteration_returns)),
        mean=statistics.mean(means),
        median=statistics.median(medians),
        win_rate=statistics.mean(win_rates),
        lower_confidence_bound=compute_confidence_bound(means),
        p_value=p_value,
        mean_95th=p95,
        win_rate_p95=sorted(win_rates)[min(len(win_rates) - 1, math.ceil(0.95 * len(win_rates)) - 1)],
        median_distribution_median=statistics.median(medians),
    )


def empirical_p_value(real: float, null_values: Sequence[float], higher_is_better: bool) -> float:
    if not null_values:
        return 1.0
    if higher_is_better:
        count = sum(1 for value in null_values if value >= real)
    else:
        count = sum(1 for value in null_values if value <= real)
    return (count + 1) / (len(null_values) + 1)


def compute_confidence_bound(values: Sequence[float], confidence: float = 0.95) -> float:
    vals = [float(v) for v in values]
    if not vals:
        return 0.0
    if len(vals) < 2:
        return vals[0]
    mean = statistics.mean(vals)
    return mean - 1.96 * statistics.stdev(vals) / math.sqrt(len(vals))


def classify_phase0c(real: RealPrimaryMetrics, survivorship_status: str, primary: NullSummary, month: NullSummary, circular_shift: NullSummary, direction_shuffle_p_value: float = 1.0, all_events_same_direction: bool = True) -> str:
    if primary.status == STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES:
        return STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES
    if primary.coverage < 0.8:
        return STATUS_INSUFFICIENT_NULL_COVERAGE
    if real.net_mean_bps <= primary.mean_95th:
        return STATUS_NULL_REJECTED_PLACEBO_MATCH
    if month.status != STATUS_MONTH_NULL_NOT_APPLICABLE and real.net_mean_bps <= month.mean_95th:
        return STATUS_NULL_REJECTED_MONTH

    circular_shift_failed = real.net_mean_bps <= circular_shift.mean_95th
    survivorship_ambiguous = survivorship_status == STATUS_SURVIVORSHIP_AMBIGUITY
    if circular_shift_failed and survivorship_ambiguous:
        return STATUS_NULL_REJECTED_CLUSTERING_WITH_SURVIVORSHIP_AMBIGUITY
    if circular_shift_failed:
        return STATUS_NULL_REJECTED_CLUSTERING
    if real.net_mean_bps - 25.0 <= 0 or real.net_mean_bps - 50.0 <= 0:
        return STATUS_COST_FRAGILE
    if survivorship_ambiguous:
        return STATUS_SURVIVORSHIP_AMBIGUITY
    return STATUS_NULL_VALIDATED_PASS


def build_summary(status: str, precommitment_sha256: str, phase0a_report_path: Path, phase0b_report_path: Path, phase0a_artifact_hash_verified: bool, phase0b_report_verified: bool, real: RealPrimaryMetrics, primary: NullSummary, month: NullSummary, circular_shift: NullSummary, direction_shuffle_p_value: float, all_events_same_direction: bool, survivorship_audit: dict[str, Any], *, profile_only: bool = False, elapsed_seconds: float | None = None, cache_build_elapsed_seconds: float | None = None, estimated_full_run_elapsed_seconds: float | None = None, phase0a_event_artifact_hash: str = EXPECTED_PHASE0A_EVENT_HASH) -> dict[str, Any]:
    cost = _compute_cost_stress(real.gross_mean_bps)
    final_verdict = status if not profile_only else STATUS_PROFILE_RUN
    return {
        "status": status,
        "final_verdict": final_verdict,
        "profile_only": profile_only,
        "precommitment_sha256": precommitment_sha256,
        "phase0a_report_path": str(phase0a_report_path),
        "phase0a_event_artifact_hash": phase0a_event_artifact_hash,
        "phase0b_report_path": str(phase0b_report_path),
        "phase0a_artifact_hash_verified": phase0a_artifact_hash_verified,
        "phase0b_report_verified": phase0b_report_verified,
        "phase0b_24h_metrics_reproduced": True,
        "real_24h_gross_mean_bps": real.gross_mean_bps,
        "real_24h_net_mean_bps": real.net_mean_bps,
        "real_24h_net_median_bps": real.net_median_bps,
        "real_24h_win_rate": real.win_rate,
        "real_event_count": real.event_count,
        "missing_forward_count_24h": real.missing_forward_count,
        "net_mean_bps_50": cost["net_mean_bps_50"],
        "net_mean_bps_75": cost["net_mean_bps_75"],
        "net_mean_bps_100": cost["net_mean_bps_100"],
        "cost_stress_75bps": cost["net_mean_bps_75"],
        "cost_stress_100bps": cost["net_mean_bps_100"],
        "primary_placebo_iterations": primary.iterations,
        "primary_placebo_status": primary.status,
        "primary_placebo_matched_coverage": primary.coverage,
        "primary_placebo_required_events": real.event_count,
        "primary_placebo_events_with_candidate": real.event_count - primary.missing_candidate_count,
        "primary_placebo_missing_candidate_count": primary.missing_candidate_count,
        "primary_placebo_missing_candidate_examples": primary.missing_candidate_details or [],
        "primary_placebo_mean": primary.mean,
        "primary_placebo_median": primary.median,
        "primary_placebo_mean_95th": primary.mean_95th,
        "primary_empirical_p_value": primary.p_value,
        "primary_null_p_value": primary.p_value,
        "secondary_placebo_iterations": month.iterations,
        "secondary_placebo_matched_coverage": month.coverage,
        "secondary_placebo_required_events": real.event_count,
        "secondary_placebo_events_with_candidate": real.event_count - month.missing_candidate_count,
        "secondary_placebo_missing_candidate_count": month.missing_candidate_count,
        "secondary_placebo_missing_candidate_examples": month.missing_candidate_details or [],
        "secondary_placebo_mean": month.mean,
        "secondary_placebo_month": month.mean_95th,
        "secondary_placebo_status": month.status,
        "month_null_p_value": month.p_value,
        "circular_shift_iterations": circular_shift.iterations,
        "circular_shift_mean": circular_shift.mean,
        "circular_shift_mean_95th": circular_shift.mean_95th,
        "circular_shift_null_p_value": circular_shift.p_value,
        "circular_shift_status": circular_shift.status,
        "direction_shuffle_p_value": direction_shuffle_p_value,
        "direction_shuffle_status": "DIRECTION_SHUFFLE_NOT_APPLICABLE" if all_events_same_direction else "DIRECTION_SHUFFLE_DIAGNOSTIC",
        "survivorship_audit": survivorship_audit,
        "survivorship_audit_status": survivorship_audit.get("survivorship_status"),
        "elapsed_seconds": elapsed_seconds,
        "cache_build_elapsed_seconds": cache_build_elapsed_seconds,
        "cold_cache_estimate_available": (not profile_only) and estimated_full_run_elapsed_seconds is not None,
        "warm_cache_estimated_validation_seconds": estimated_full_run_elapsed_seconds if not profile_only else None,
        "estimated_full_run_elapsed_seconds": estimated_full_run_elapsed_seconds if not profile_only else None,
        "v1_unlock": False,
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    return f"""# Liquidation flush aftershock reversal venue-age-aware Phase 0C

Status: `{summary.get('status')}`

Final verdict: `{summary.get('final_verdict')}`

Profile-only: `{summary.get('profile_only')}`

Precommitment SHA-256: `{summary.get('precommitment_sha256')}`

Phase 0A artifact hash verified: `{summary.get('phase0a_artifact_hash_verified')}`

Phase 0B report verified: `{summary.get('phase0b_report_verified')}`

Phase 0B 24h metrics reproduced: `{summary.get('phase0b_24h_metrics_reproduced')}`

Real 24h gross mean bps: `{summary.get('real_24h_gross_mean_bps')}`

Real 24h net mean bps at 50 bps: `{summary.get('net_mean_bps_50')}`

Net mean bps at 75 bps: `{summary.get('net_mean_bps_75')}`

Net mean bps at 100 bps: `{summary.get('net_mean_bps_100')}`

Survivorship audit status: `{summary.get('survivorship_audit_status')}`

Direction shuffle status: `{summary.get('direction_shuffle_status')}`

No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.
"""


def _write_report(summary: dict[str, Any], report_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("liquidation_flush_aftershock_reversal_venue_age_aware_phase0c_%Y%m%dT%H%M%S_%f")
    report_dir = report_root / stamp
    report_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(report_dir / "summary.json", summary)
    atomic_write_text(report_dir / "summary.md", _summary_markdown(summary))
    return report_dir


def _load_phase0_inputs(phase0a_report_path: Path, phase0b_report_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    phase0a_summary = json.loads((phase0a_report_path / "summary.json").read_text(encoding="utf-8"))
    phase0b_summary = json.loads((phase0b_report_path / "summary.json").read_text(encoding="utf-8"))
    events_path = phase0a_report_path / str(phase0a_summary.get("accepted_events_jsonl_path") or "accepted_events.jsonl")
    events = _load_jsonl(events_path)
    validate_event_universe(events)
    return phase0a_summary, phase0b_summary, events


def _run_nulls(real_events: Sequence[dict[str, Any]], cache: Phase0CCache | Phase0CLightCache, real_metrics: RealPrimaryMetrics, iterations: int, cluster_iterations: int) -> tuple[NullSummary, NullSummary, NullSummary, float, bool]:
    symbol_candidates: dict[str, list[ArchiveRow]] = {}
    rows_for_candidates = getattr(cache, "rows", None)
    eligible_month = build_eligible_placebo_candidates(rows_for_candidates, real_events, min_hours_exclude=48.0) if rows_for_candidates is not None else {}
    if rows_for_candidates is None:
        delta = timedelta(hours=48)
        blocked_by_symbol: dict[str, list[tuple[datetime, datetime]]] = {}
        for event in real_events:
            ts = parse_ts(event["event_timestamp_utc"])
            blocked_by_symbol.setdefault(str(event["symbol"]).upper(), []).append((ts - delta, ts + delta))
        for symbol, rows in cache.symbol_rows.items():
            filtered = [row for row in rows if not any(start <= row.timestamp <= end for start, end in blocked_by_symbol.get(symbol, []))]
            if filtered:
                symbol_candidates[symbol] = filtered
                for row in filtered:
                    eligible_month.setdefault((symbol, row.timestamp.year, row.timestamp.month), []).append(row)
    else:
        for (symbol, _, _), pool in eligible_month.items():
            symbol_candidates.setdefault(symbol, []).extend(pool)

    primary_coverage = _candidate_coverage(real_events, symbol_candidates)
    if primary_coverage["missing_candidate_count"]:
        primary_summary = NullSummary(
            iterations=0,
            coverage=primary_coverage["coverage_ratio"],
            mean=0.0,
            median=0.0,
            win_rate=0.0,
            lower_confidence_bound=0.0,
            p_value=1.0,
            status=STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES,
            missing_candidate_details=primary_coverage["missing_candidate_examples"],
        )
    else:
        primary_iteration_returns: list[list[float]] = []
        for i in range(iterations):
            placebo = sample_matched_placebo_events(real_events, symbol_candidates, seed=20260525 + i, horizon_hours=24)
            primary_iteration_returns.append(_event_returns(placebo, cache.symbol_rows))
        primary_summary = _summarize_distribution(primary_iteration_returns, real_metrics.net_mean_bps)

    month_coverage = _candidate_coverage(real_events, eligible_month)
    if month_coverage["missing_candidate_count"]:
        month_summary = NullSummary(
            iterations=0,
            coverage=month_coverage["coverage_ratio"],
            mean=0.0,
            median=0.0,
            win_rate=0.0,
            lower_confidence_bound=0.0,
            p_value=1.0,
            status=STATUS_MONTH_NULL_NOT_APPLICABLE,
            missing_candidate_details=month_coverage["missing_candidate_examples"],
        )
        print(
            f"PHASE0C_MONTH_NULL_INSUFFICIENT_CANDIDATES missing={month_coverage['missing_candidate_count']} required={month_coverage['required_events']} coverage={month_coverage['coverage_ratio']:.6f}",
            flush=True,
        )
    else:
        month_iteration_returns: list[list[float]] = []
        for i in range(iterations):
            placebo = sample_matched_placebo_events(real_events, eligible_month, seed=20260526 + i, horizon_hours=24)
            month_iteration_returns.append(_event_returns(placebo, cache.symbol_rows))
        month_summary = _summarize_distribution(month_iteration_returns, real_metrics.net_mean_bps)

    circular_iteration_returns: list[list[float]] = []
    rng = random.Random(20260527)
    for _ in range(cluster_iterations):
        shifted = circular_shift_events(real_events, rng.choice([d for d in range(-30, 31) if d != 0]))
        circular_iteration_returns.append(_event_returns(shifted, cache.symbol_rows))

    directions = {str(ev.get("event_direction") or ev.get("flush_side") or "") for ev in real_events}
    all_same_direction = len(directions) == 1
    direction_shuffle_p_value = 1.0
    if not all_same_direction:
        shuffled = shuffle_event_directions(real_events, seed=20260526)
        shuffled_returns = _event_returns(shuffled, cache.symbol_rows)
        direction_shuffle_p_value = empirical_p_value(real_metrics.net_mean_bps, [statistics.mean(shuffled_returns)] if shuffled_returns else [], True)

    return (
        primary_summary,
        month_summary,
        _summarize_distribution(circular_iteration_returns, real_metrics.net_mean_bps),
        direction_shuffle_p_value,
        all_same_direction,
    )


def run_phase0c(phase0a_report_path: Path, phase0b_report_path: Path, archive_path: Path, precommitment_path: Path, iterations: int = 1000, cluster_iterations: int = 1000, profile_only: bool = False, report_root: Path | None = None, cache_path: Path | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    precommitment_sha = _verify_precommitment_hash(precommitment_path)
    artifact_info = _verify_artifact_hashes(phase0a_report_path, phase0b_report_path)
    _, phase0b_summary, real_events = _load_phase0_inputs(phase0a_report_path, phase0b_report_path)

    if cache_path is None:
        cache_key = hashlib.sha256((str(archive_path.resolve()) + (":profile" if profile_only else ":full")).encode()).hexdigest()[:16]
        cache_path = Path(".cache") / "phase0c" / f"{cache_key}.pkl"
    if profile_only:
        event_symbols = sorted({str(event["symbol"]).upper() for event in real_events})
        existing = [symbol for symbol in event_symbols if (archive_path / f"{symbol}.jsonl").exists()]
        cache = Phase0CLightCache(price_series={}, symbol_rows={symbol: [] for symbol in existing}, build_elapsed_seconds=0.0, diagnostics={"profile_symbol_file_count": len(existing), "profile_archive_scan": "symbol_file_existence_only"})
    else:
        cache = _load_or_build_cache(cache_path, lambda: _build_cache(archive_path), archive_path=archive_path, precommitment_sha256=precommitment_sha)

    if profile_only:
        real_metrics = _reproduce_phase0b_from_evaluation_csv(phase0b_report_path, phase0b_summary)
    else:
        real_metrics = compute_phase0b_metrics(real_events, [row for rows in cache.symbol_rows.values() for row in rows])
    _reproduce_phase0b_or_raise(real_metrics, phase0b_summary)
    survivorship = _compute_survivorship_audit_from_symbols(list(cache.symbol_rows), real_events)
    directions = {str(ev.get("event_direction") or ev.get("flush_side") or "") for ev in real_events}
    all_same_direction = len(directions) == 1

    if profile_only:
        elapsed = time.perf_counter() - start
        empty = NullSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, status="PROFILE_ONLY_NOT_RUN")
        summary = build_summary(
            STATUS_PROFILE_RUN,
            precommitment_sha,
            phase0a_report_path,
            phase0b_report_path,
            artifact_info["phase0a_artifact_hash_verified"],
            artifact_info["phase0b_report_verified"],
            real_metrics,
            empty,
            empty,
            empty,
            1.0,
            all_same_direction,
            survivorship,
            profile_only=True,
            elapsed_seconds=elapsed,
            cache_build_elapsed_seconds=cache.build_elapsed_seconds,
            estimated_full_run_elapsed_seconds=None,
            phase0a_event_artifact_hash=artifact_info["phase0a_event_artifact_hash"],
        )
    else:
        primary, month, circular, direction_p, all_same_direction = _run_nulls(real_events, cache, real_metrics, iterations, cluster_iterations)
        verdict = classify_phase0c(real_metrics, survivorship["survivorship_status"], primary, month, circular, direction_p, all_same_direction)
        elapsed = time.perf_counter() - start
        summary = build_summary(
            verdict,
            precommitment_sha,
            phase0a_report_path,
            phase0b_report_path,
            artifact_info["phase0a_artifact_hash_verified"],
            artifact_info["phase0b_report_verified"],
            real_metrics,
            primary,
            month,
            circular,
            direction_p,
            all_same_direction,
            survivorship,
            profile_only=False,
            elapsed_seconds=elapsed,
            cache_build_elapsed_seconds=cache.build_elapsed_seconds,
            estimated_full_run_elapsed_seconds=elapsed,
            phase0a_event_artifact_hash=artifact_info["phase0a_event_artifact_hash"],
        )

    if report_root is not None:
        report_dir = _write_report(summary, report_root)
        summary["report_dir"] = str(report_dir)
        atomic_write_json(report_dir / "summary.json", summary)
    return summary


# Compatibility helpers historically imported by the test module.
def _get_first(row: dict[str, Any], names: Sequence[str]) -> Any:
    lower = {str(k).lower(): k for k in row}
    for name in names:
        if name in row:
            return row[name]
        key = lower.get(name.lower())
        if key is not None:
            return row[key]
    return None


def _finite_float(value: Any, name: str) -> float:
    if value is None or value == "":
        raise ValueError(f"missing {name}")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"non-finite {name}")
    return out


def normalize_raw_row(raw: dict[str, Any], source_path: Path, file_order: int):
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import normalize_raw_row as _normalize
    return _normalize(raw, source_path, file_order)


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return [dict(row) for row in json.loads(text)]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    import csv
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _candidate_archive_files(path: Path):
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import _candidate_archive_files as _candidate
    return _candidate(path)
