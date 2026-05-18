"""Phase 2A offline historical causal stress-window indexer.

Consumes Phase 1 prepared datasets or reloads a Phase 1 prepare manifest plus the
same source-config path used during prepare. Produces deterministic stress-window
artifacts only; no candidate evaluation, no execution, no auth, no live paths.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .offline_corpus_hash import HashCache, compute_data_corpus_hash, sha256_file
from .offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
    WINDOW_MODE_CAUSAL,
    WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
    OfflineBarRecord,
    OfflinePreparedDataset,
    OfflinePrepareManifest,
    OfflineSourceFile,
    OfflineTradeRecord,
    can_promote_from_window_mode,
)
from .run_offline_historical_prepare import _dispatch_parse


OFFLINE_STRESS_WINDOW_SCHEMA_VERSION = "offline_stress_window_index_v1"

STATUS_OFFLINE_STRESS_INDEX_READY = "OFFLINE_STRESS_INDEX_READY"
STATUS_NO_STRESS_WINDOWS = "NO_STRESS_WINDOWS"
STATUS_INSUFFICIENT_HISTORICAL_COVERAGE = "INSUFFICIENT_HISTORICAL_COVERAGE"
STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE = "UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE"
STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY = "RETROSPECTIVE_DIAGNOSTIC_ONLY"
STATUS_STRESS_INDEX_UNUSABLE = "STRESS_INDEX_UNUSABLE"
STATUS_DATA_CORPUS_HASH_MISMATCH = "DATA_CORPUS_HASH_MISMATCH"

_SUPPORTED_STATUSES = {
    STATUS_OFFLINE_STRESS_INDEX_READY,
    STATUS_NO_STRESS_WINDOWS,
    STATUS_INSUFFICIENT_HISTORICAL_COVERAGE,
    STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE,
    STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    STATUS_STRESS_INDEX_UNUSABLE,
    STATUS_DATA_CORPUS_HASH_MISMATCH,
}


@dataclass(frozen=True)
class StressRuleConfig:
    rule_name: str
    rule_version: str
    lookback_seconds: int
    threshold_bps: float
    cooldown_seconds: int
    pre_window_seconds: int
    post_window_seconds: int
    min_required_points: int
    supported_resolutions: tuple[str, ...]
    trigger_metric: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def replace(self, **changes: Any) -> "StressRuleConfig":
        return replace(self, **changes)


@dataclass(frozen=True)
class OfflineStressWindow:
    window_id: str
    selection_mode: str
    promotion_allowed: bool
    source_venue: str
    source_symbol: str
    base_asset: str
    quote_asset: str
    trigger_timestamp_ns: int
    window_start_ns: int
    window_end_ns: int
    pre_window_start_ns: int
    post_window_end_ns: int
    rule_name: str
    rule_version: str
    trigger_metric: str
    trigger_value: float
    trigger_threshold: float
    lookback_ns: int
    cooldown_ns: int
    resolution_type: str
    data_corpus_hash: str
    precommitment_hash: Optional[str]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StressWindowIndexResult:
    status: str
    windows: list[OfflineStressWindow]
    rejected_rules: list[dict[str, Any]]
    source_coverage: dict[str, Any]
    manifest_metadata: dict[str, Any]


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _get_git_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)


def _status_priority(status: str) -> int:
    order = {
        STATUS_DATA_CORPUS_HASH_MISMATCH: 0,
        STATUS_STRESS_INDEX_UNUSABLE: 1,
        STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE: 2,
        STATUS_INSUFFICIENT_HISTORICAL_COVERAGE: 3,
        STATUS_NO_STRESS_WINDOWS: 4,
        STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY: 5,
        STATUS_OFFLINE_STRESS_INDEX_READY: 6,
    }
    return order[status]


def _select_final_status(
    *,
    selection_mode: str,
    windows: Sequence[OfflineStressWindow],
    rejected_rules: Sequence[dict[str, Any]],
) -> str:
    if windows:
        if selection_mode == WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC:
            return STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY
        return STATUS_OFFLINE_STRESS_INDEX_READY
    if rejected_rules:
        statuses = [item["status"] for item in rejected_rules]
        statuses.sort(key=_status_priority)
        return statuses[0]
    return STATUS_NO_STRESS_WINDOWS


def _serialize_rule(rule: StressRuleConfig) -> dict[str, Any]:
    return {
        "rule_name": rule.rule_name,
        "rule_version": rule.rule_version,
        "lookback_seconds": rule.lookback_seconds,
        "threshold_bps": rule.threshold_bps,
        "cooldown_seconds": rule.cooldown_seconds,
        "pre_window_seconds": rule.pre_window_seconds,
        "post_window_seconds": rule.post_window_seconds,
        "min_required_points": rule.min_required_points,
        "supported_resolutions": list(rule.supported_resolutions),
        "trigger_metric": rule.trigger_metric,
        "metadata": rule.metadata,
    }


def compute_stress_rule_config_hash(
    stress_rules: Sequence[StressRuleConfig],
    selection_mode: str,
    source_streams_used: Sequence[str] | None = None,
) -> str:
    payload = {
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "selection_mode": selection_mode,
        "source_streams_used": sorted(source_streams_used or []),
        "rules": sorted([_serialize_rule(rule) for rule in stress_rules], key=lambda item: (item["rule_name"], item["rule_version"], item["lookback_seconds"], item["threshold_bps"], item["cooldown_seconds"])),
    }
    return _sha256_json(payload)


def _window_to_payload(window: OfflineStressWindow) -> dict[str, Any]:
    payload = asdict(window)
    payload["metadata"] = json.loads(_canonical_json(payload["metadata"]))
    return payload


def _compute_window_id(window_payload: dict[str, Any]) -> str:
    return _sha256_json(window_payload)


def _build_window(
    *,
    source: OfflineSourceFile,
    dataset: OfflinePreparedDataset,
    selection_mode: str,
    rule: StressRuleConfig,
    trigger_timestamp_ns: int,
    trigger_value: float,
    precommitment_hash: Optional[str],
    metadata: dict[str, Any],
) -> OfflineStressWindow:
    promotion_allowed = can_promote_from_window_mode(selection_mode)
    payload = {
        "selection_mode": selection_mode,
        "promotion_allowed": promotion_allowed,
        "source_venue": source.venue,
        "source_symbol": source.symbol,
        "base_asset": source.base_asset,
        "quote_asset": source.quote_asset,
        "trigger_timestamp_ns": trigger_timestamp_ns,
        "window_start_ns": trigger_timestamp_ns,
        "window_end_ns": trigger_timestamp_ns,
        "pre_window_start_ns": max(0, trigger_timestamp_ns - rule.pre_window_seconds * 1_000_000_000),
        "post_window_end_ns": trigger_timestamp_ns + rule.post_window_seconds * 1_000_000_000,
        "rule_name": rule.rule_name,
        "rule_version": rule.rule_version,
        "trigger_metric": rule.trigger_metric,
        "trigger_value": round(trigger_value, 12),
        "trigger_threshold": rule.threshold_bps,
        "lookback_ns": rule.lookback_seconds * 1_000_000_000,
        "cooldown_ns": rule.cooldown_seconds * 1_000_000_000,
        "resolution_type": source.resolution_type,
        "data_corpus_hash": dataset.data_corpus_hash,
        "precommitment_hash": precommitment_hash,
        "metadata": metadata,
    }
    window_id = _compute_window_id(payload)
    return OfflineStressWindow(window_id=window_id, **payload)


def _points_for_stream(
    source: OfflineSourceFile,
    dataset: OfflinePreparedDataset,
) -> list[dict[str, Any]]:
    if source.resolution_type in {RESOLUTION_TRADE, RESOLUTION_AGG_TRADE}:
        trades = sorted(dataset.trades_by_stream.get(source.logical_source_id, []), key=lambda item: item.timestamp_ns)
        return [
            {
                "timestamp_ns": item.timestamp_ns,
                "price": item.price,
                "high": item.price,
                "low": item.price,
            }
            for item in trades
        ]
    bars = sorted(dataset.bars_by_stream.get(source.logical_source_id, []), key=lambda item: item.timestamp_ns)
    return [
        {
            "timestamp_ns": item.timestamp_ns,
            "price": item.close,
            "high": item.high,
            "low": item.low,
            "open": item.open,
            "close": item.close,
        }
        for item in bars
    ]


def _eligible_slice(
    points: Sequence[dict[str, Any]], index: int, lookback_ns: int, left: int = 0
) -> tuple[int, int]:
    """Return (start, end) indices of points within lookback_ns of points[index].

    Uses a sliding-window left bound to avoid O(n²) list creation.
    Since points are sorted by timestamp_ns, advancing from *left* is correct.
    """
    current_ts = points[index]["timestamp_ns"]
    start_ts = current_ts - lookback_ns
    start_idx = left
    while start_idx <= index and points[start_idx]["timestamp_ns"] < start_ts:
        start_idx += 1
    return start_idx, index + 1


def _range_bps(points: Sequence[dict[str, Any]], start: int = 0, end: int | None = None) -> float:
    end = end or len(points)
    if end - start < 2:
        return 0.0
    min_low = min(point["low"] for point in points[start:end])
    max_high = max(point["high"] for point in points[start:end])
    if min_low <= 0:
        return 0.0
    return ((max_high - min_low) / min_low) * 10_000.0


def _absolute_return_bps(points: Sequence[dict[str, Any]], start: int = 0, end: int | None = None) -> float:
    end = end or len(points)
    if end - start < 2:
        return 0.0
    start_price = points[start]["price"]
    end_price = points[end - 1]["price"]
    if start_price <= 0:
        return 0.0
    return abs((end_price - start_price) / start_price) * 10_000.0


def _evaluate_rule_on_points(
    *,
    source: OfflineSourceFile,
    dataset: OfflinePreparedDataset,
    points: Sequence[dict[str, Any]],
    rule: StressRuleConfig,
    selection_mode: str,
    precommitment_hash: Optional[str],
) -> tuple[list[OfflineStressWindow], list[dict[str, Any]], int]:
    if source.resolution_type not in rule.supported_resolutions:
        return [], [{
            "logical_source_id": source.logical_source_id,
            "rule_name": rule.rule_name,
            "rule_version": rule.rule_version,
            "status": STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE,
            "resolution_type": source.resolution_type,
        }], 0

    if len(points) < rule.min_required_points:
        return [], [{
            "logical_source_id": source.logical_source_id,
            "rule_name": rule.rule_name,
            "rule_version": rule.rule_version,
            "status": STATUS_INSUFFICIENT_HISTORICAL_COVERAGE,
            "resolution_type": source.resolution_type,
            "available_points": len(points),
            "min_required_points": rule.min_required_points,
        }], 0

    cooldown_ns = rule.cooldown_seconds * 1_000_000_000
    lookback_ns = rule.lookback_seconds * 1_000_000_000
    last_emitted_ts: Optional[int] = None
    windows: list[OfflineStressWindow] = []
    suppressed = 0
    window_left = 0

    for index in range(len(points)):
        start_idx, end_idx = _eligible_slice(points, index, lookback_ns, window_left)
        window_left = start_idx
        eligible_count = end_idx - start_idx
        if eligible_count < rule.min_required_points:
            continue

        if rule.rule_name == "rolling_range_bps":
            trigger_value = _range_bps(points, start_idx, end_idx)
        elif rule.rule_name == "rolling_absolute_return_bps":
            trigger_value = _absolute_return_bps(points, start_idx, end_idx)
        elif rule.rule_name == "tick_only_burst_placeholder":
            trigger_value = 0.0
        else:
            return [], [{
                "logical_source_id": source.logical_source_id,
                "rule_name": rule.rule_name,
                "rule_version": rule.rule_version,
                "status": STATUS_STRESS_INDEX_UNUSABLE,
                "reason": "UNKNOWN_STRESS_RULE",
            }], suppressed

        if trigger_value < rule.threshold_bps:
            continue

        trigger_ts = points[index]["timestamp_ns"]
        if last_emitted_ts is not None and trigger_ts - last_emitted_ts < cooldown_ns:
            suppressed += 1
            continue

        metadata = {
            "logical_source_id": source.logical_source_id,
            "point_count": eligible_count,
            "current_timestamp_ns": trigger_ts,
        }
        window = _build_window(
            source=source,
            dataset=dataset,
            selection_mode=selection_mode,
            rule=rule,
            trigger_timestamp_ns=trigger_ts,
            trigger_value=trigger_value,
            precommitment_hash=precommitment_hash,
            metadata=metadata,
        )
        windows.append(window)
        last_emitted_ts = trigger_ts

    rejected: list[dict[str, Any]] = []
    if not windows and suppressed == 0:
        rejected.append({
            "logical_source_id": source.logical_source_id,
            "rule_name": rule.rule_name,
            "rule_version": rule.rule_version,
            "status": STATUS_NO_STRESS_WINDOWS,
            "resolution_type": source.resolution_type,
        })
    return windows, rejected, suppressed


def _source_coverage(dataset: OfflinePreparedDataset) -> dict[str, Any]:
    coverage = {}
    for source in dataset.source_files:
        point_count = len(dataset.trades_by_stream.get(source.logical_source_id, [])) + len(dataset.bars_by_stream.get(source.logical_source_id, []))
        coverage[source.logical_source_id] = {
            "venue": source.venue,
            "symbol": source.symbol,
            "resolution_type": source.resolution_type,
            "point_count": point_count,
            "data_start_ns": source.data_start_ns,
            "data_end_ns": source.data_end_ns,
        }
    return coverage


def build_stress_window_index(
    dataset: OfflinePreparedDataset,
    stress_rules: Sequence[StressRuleConfig],
    *,
    selection_mode: str,
    precommitment_hash: Optional[str] = None,
    run_id: Optional[str] = None,
    input_prepare_manifest_path: Optional[str] = None,
    input_prepare_manifest_hash: Optional[str] = None,
    git_sha: Optional[str] = None,
) -> StressWindowIndexResult:
    if selection_mode not in {WINDOW_MODE_CAUSAL, WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC}:
        raise ValueError(f"Unknown selection_mode: {selection_mode!r}")

    source_streams_used = [source.logical_source_id for source in dataset.source_files]
    stress_rule_config_hash = compute_stress_rule_config_hash(stress_rules, selection_mode, source_streams_used)

    all_windows: list[OfflineStressWindow] = []
    rejected_rules: list[dict[str, Any]] = []
    suppressed_trigger_count = 0

    for source in sorted(dataset.source_files, key=lambda item: item.logical_source_id):
        points = _points_for_stream(source, dataset)
        for rule in sorted(stress_rules, key=lambda item: (item.rule_name, item.rule_version, item.lookback_seconds, item.threshold_bps)):
            windows, rejected, suppressed = _evaluate_rule_on_points(
                source=source,
                dataset=dataset,
                points=points,
                rule=rule,
                selection_mode=selection_mode,
                precommitment_hash=precommitment_hash,
            )
            all_windows.extend(windows)
            rejected_rules.extend(rejected)
            suppressed_trigger_count += suppressed

    all_windows = sorted(all_windows, key=lambda item: (item.trigger_timestamp_ns, item.source_venue, item.source_symbol, item.rule_name, item.window_id))
    deduped_window_count = len(all_windows)
    rejected_rules = sorted(rejected_rules, key=lambda item: (item["logical_source_id"], item["rule_name"], item["status"]))
    source_coverage = _source_coverage(dataset)

    window_index_hash = _sha256_json({
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "data_corpus_hash": dataset.data_corpus_hash,
        "precommitment_hash": precommitment_hash,
        "stress_rule_config_hash": stress_rule_config_hash,
        "windows": [_window_to_payload(window) for window in all_windows],
        "rejected_rules": rejected_rules,
        "suppressed_trigger_count": suppressed_trigger_count,
    })

    status = _select_final_status(selection_mode=selection_mode, windows=all_windows, rejected_rules=rejected_rules)
    if status not in _SUPPORTED_STATUSES:
        raise ValueError(f"Unexpected status {status!r}")

    manifest_metadata = {
        "run_id": run_id or f"offline_stress_window_index_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "phase": "offline_stress_window_index",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": git_sha or dataset.git_sha or _get_git_sha(),
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "input_prepare_manifest_path": input_prepare_manifest_path,
        "input_prepare_manifest_hash": input_prepare_manifest_hash or _sha256_json({"data_corpus_hash": dataset.data_corpus_hash, "schema_version": dataset.schema_version}),
        "data_corpus_hash": dataset.data_corpus_hash,
        "precommitment_hash": precommitment_hash,
        "stress_rule_config_hash": stress_rule_config_hash,
        "window_index_hash": window_index_hash,
        "selection_mode": selection_mode,
        "promotion_allowed": can_promote_from_window_mode(selection_mode),
        "window_count": len(all_windows),
        "deduped_window_count": deduped_window_count,
        "suppressed_trigger_count": suppressed_trigger_count,
        "source_streams_used": sorted(source_streams_used),
        "resolution_summary": dataset_resolution_summary(dataset),
        "status": status,
        "safety": "public_data_observer_only",
        "rejected_rules": rejected_rules,
    }
    return StressWindowIndexResult(status=status, windows=all_windows, rejected_rules=rejected_rules, source_coverage=source_coverage, manifest_metadata=manifest_metadata)


def dataset_resolution_summary(dataset: OfflinePreparedDataset) -> dict[str, int]:
    summary: dict[str, int] = {}
    for source in dataset.source_files:
        summary[source.resolution_type] = summary.get(source.resolution_type, 0) + 1
    return summary


def _load_prepare_manifest(path: Path) -> OfflinePrepareManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OfflinePrepareManifest(
        run_id=payload["run_id"],
        phase=payload["phase"],
        generated_at_utc=payload["generated_at_utc"],
        git_sha=payload["git_sha"],
        schema_version=payload["schema_version"],
        precommitment_hash=payload.get("precommitment_hash"),
        data_corpus_hash=payload["data_corpus_hash"],
        source_files=payload["source_files"],
        timestamp_validation=payload.get("timestamp_validation", {}),
        hash_cache_used=payload.get("hash_cache_used", False),
        hash_cache_entries_reused=payload.get("hash_cache_entries_reused", 0),
        hash_cache_entries_recomputed=payload.get("hash_cache_entries_recomputed", 0),
        normalized_time_range=payload.get("normalized_time_range", {}),
        stream_counts=payload.get("stream_counts", {}),
        resolution_summary=payload.get("resolution_summary", {}),
        quote_currency_summary=payload.get("quote_currency_summary", {}),
        safety=payload.get("safety", "public_data_observer_only"),
        forbidden_capabilities_present=payload.get("forbidden_capabilities_present", False),
        next_phase_allowed=payload.get("next_phase_allowed", False),
    )


def reload_prepared_dataset(
    *,
    prepared_manifest_path: Path,
    source_config_path: Path,
    force_rehash: bool = False,
) -> tuple[OfflinePreparedDataset, OfflinePrepareManifest, str]:
    manifest = _load_prepare_manifest(prepared_manifest_path)
    source_config = json.loads(source_config_path.read_text(encoding="utf-8"))
    sources = source_config.get("sources", [])
    hash_cache = HashCache()

    source_files: list[OfflineSourceFile] = []
    trades_by_stream: dict[str, list[OfflineTradeRecord]] = {}
    bars_by_stream: dict[str, list[OfflineBarRecord]] = {}

    for src in sources:
        source_file, parse_result = _dispatch_parse(src, hash_cache, force_rehash)
        source_files.append(source_file)
        if parse_result.trades:
            trades_by_stream[source_file.logical_source_id] = list(parse_result.trades)
        if parse_result.bars:
            bars_by_stream[source_file.logical_source_id] = list(parse_result.bars)

    recomputed_hash = compute_data_corpus_hash(source_files, manifest.schema_version)
    dataset = OfflinePreparedDataset(
        source_files=source_files,
        trades_by_stream=trades_by_stream,
        bars_by_stream=bars_by_stream,
        data_corpus_hash=recomputed_hash,
        schema_version=manifest.schema_version,
        created_at_utc=_now_utc_iso(),
        git_sha=_get_git_sha(),
    )
    return dataset, manifest, recomputed_hash


def _corpus_hash_mismatch_result(
    *,
    manifest: OfflinePrepareManifest,
    prepared_manifest_path: Path,
    source_config_path: Path,
    recomputed_hash: str,
) -> StressWindowIndexResult:
    metadata = {
        "run_id": f"offline_stress_window_index_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "phase": "offline_stress_window_index",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": _get_git_sha(),
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "input_prepare_manifest_path": str(prepared_manifest_path),
        "input_prepare_manifest_hash": sha256_file(prepared_manifest_path),
        "data_corpus_hash": manifest.data_corpus_hash,
        "precommitment_hash": manifest.precommitment_hash,
        "stress_rule_config_hash": None,
        "window_index_hash": None,
        "selection_mode": None,
        "promotion_allowed": False,
        "window_count": 0,
        "deduped_window_count": 0,
        "suppressed_trigger_count": 0,
        "source_streams_used": [],
        "resolution_summary": {},
        "status": STATUS_DATA_CORPUS_HASH_MISMATCH,
        "safety": "public_data_observer_only",
        "diagnostic": {
            "prepared_manifest_path": str(prepared_manifest_path),
            "source_config_path": str(source_config_path),
            "manifest_data_corpus_hash": manifest.data_corpus_hash,
            "recomputed_data_corpus_hash": recomputed_hash,
        },
    }
    return StressWindowIndexResult(
        status=STATUS_DATA_CORPUS_HASH_MISMATCH,
        windows=[],
        rejected_rules=[metadata["diagnostic"]],
        source_coverage={},
        manifest_metadata=metadata,
    )


def index_prepared_manifest_reload(
    *,
    prepared_manifest_path: Path,
    source_config_path: Path,
    stress_rules: Sequence[StressRuleConfig],
    selection_mode: str,
    force_rehash: bool = False,
) -> StressWindowIndexResult:
    dataset, manifest, recomputed_hash = reload_prepared_dataset(
        prepared_manifest_path=prepared_manifest_path,
        source_config_path=source_config_path,
        force_rehash=force_rehash,
    )
    if recomputed_hash != manifest.data_corpus_hash:
        return _corpus_hash_mismatch_result(
            manifest=manifest,
            prepared_manifest_path=prepared_manifest_path,
            source_config_path=source_config_path,
            recomputed_hash=recomputed_hash,
        )
    return build_stress_window_index(
        dataset,
        stress_rules,
        selection_mode=selection_mode,
        precommitment_hash=manifest.precommitment_hash,
        input_prepare_manifest_path=str(prepared_manifest_path),
        input_prepare_manifest_hash=sha256_file(prepared_manifest_path),
    )


def build_stress_window_manifest_payload(result: StressWindowIndexResult) -> dict[str, Any]:
    payload = dict(result.manifest_metadata)
    payload["rejected_rules"] = result.rejected_rules
    return payload


def write_stress_window_outputs(
    result: StressWindowIndexResult,
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Path]:
    run_dir = Path(out_dir)
    if run_dir.exists() and not overwrite:
        raise FileExistsError(f"Output directory {run_dir} already exists. Pass overwrite=True or --overwrite to force.")
    run_dir.mkdir(parents=True, exist_ok=True)

    windows_payload = {
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "status": result.status,
        "windows": [_window_to_payload(window) for window in result.windows],
        "window_index_hash": result.manifest_metadata.get("window_index_hash"),
    }
    stress_windows_path = run_dir / "stress_windows.json"
    stress_windows_path.write_text(json.dumps(windows_payload, indent=2, sort_keys=True), encoding="utf-8")

    manifest_path = run_dir / "stress_window_manifest.json"
    manifest_path.write_text(json.dumps(build_stress_window_manifest_payload(result), indent=2, sort_keys=True), encoding="utf-8")
    return {"stress_windows_path": stress_windows_path, "manifest_path": manifest_path}
