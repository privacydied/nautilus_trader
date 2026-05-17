"""Phase 2B-1 fixed-window offline discovery scaffold.

Consumes a frozen Phase 1 prepare manifest plus a frozen Phase 2A stress-window
index and produces a deterministic discovery-plan scaffold only. No forward
return evaluation, no FDR, no null tests, no cost sensitivity sweeps, no
candidate falsification, no shadow execution, no auth, no live trading, and no
network access.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .offline_historical_models import (
    ALLOWED_FAMILY2_SIGNAL_VARIANTS,
    OFFLINE_DATA_SCHEMA_VERSION,
    REJECTED_FAMILY2_SOFT_VALUES,
    RESOLUTION_BAR,
    VALID_RESOLUTIONS,
    validate_family2_signal_variants,
)
from .offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
    STATUS_DATA_CORPUS_HASH_MISMATCH,
    STATUS_OFFLINE_STRESS_INDEX_READY,
    STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    STATUS_STRESS_INDEX_UNUSABLE,
    OfflineStressWindow,
)

DISCOVERY_SCHEMA_VERSION = "offline_discovery_plan_v1"

STATUS_OFFLINE_DISCOVERY_PLAN_READY = "OFFLINE_DISCOVERY_PLAN_READY"
STATUS_NO_PROMOTABLE_WINDOWS = "NO_PROMOTABLE_WINDOWS"
STATUS_INPUT_HASH_MISMATCH = "INPUT_HASH_MISMATCH"
STATUS_WINDOW_INDEX_HASH_MISMATCH = "WINDOW_INDEX_HASH_MISMATCH"
STATUS_UNUSABLE_STRESS_INDEX = "UNUSABLE_STRESS_INDEX"
STATUS_INVALID_DISCOVERY_CONFIG = "INVALID_DISCOVERY_CONFIG"
STATUS_NO_SUPPORTED_HYPOTHESIS_FAMILIES = "NO_SUPPORTED_HYPOTHESIS_FAMILIES"

_ALLOWED_STRESS_STATUSES = {
    STATUS_OFFLINE_STRESS_INDEX_READY,
    STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
}
_REJECTED_STRESS_STATUSES = {
    STATUS_DATA_CORPUS_HASH_MISMATCH,
    STATUS_STRESS_INDEX_UNUSABLE,
}


@dataclass(frozen=True)
class CostConfig:
    fees_bps: float
    slippage_bps: float
    quote_mismatch_bps: float


@dataclass(frozen=True)
class TrainHoldoutConfig:
    train_fraction: float
    min_train_windows: int
    min_holdout_windows: int


@dataclass(frozen=True)
class Family1Config:
    enabled: bool
    source_venue: str
    source_symbols: tuple[str, ...]
    horizons_ms: tuple[int, ...]
    lookbacks_ms: tuple[int, ...]
    required_resolution: str
    cost_config: CostConfig
    min_events: int


@dataclass(frozen=True)
class Family2Config:
    enabled: bool
    source_symbols: tuple[str, ...]
    target_symbols: tuple[str, ...]
    signal_variants: tuple[str, ...]
    horizons_ms: tuple[int, ...]
    lookbacks_ms: tuple[int, ...]
    required_resolution: str
    cost_config: CostConfig
    min_events: int


@dataclass(frozen=True)
class VenueSymbol:
    venue: str
    symbol: str


@dataclass(frozen=True)
class Family3Config:
    enabled: bool
    usd_reference_venue_symbols: tuple[VenueSymbol, ...]
    usdt_venue_symbols: tuple[VenueSymbol, ...]
    horizons_ms: tuple[int, ...]
    lookbacks_ms: tuple[int, ...]
    required_resolution: str
    latency_gate_required: bool
    cost_config: CostConfig
    min_events: int


@dataclass(frozen=True)
class BasisSourceConfig:
    venue: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class Family4Config:
    enabled: bool
    conditioning_only: bool
    basis_source: BasisSourceConfig
    basis_threshold_bps: float
    target_families: tuple[str, ...]
    standalone: bool = False


@dataclass(frozen=True)
class OfflineDiscoveryConfig:
    schema_version: str
    train_holdout: TrainHoldoutConfig
    family1: Family1Config
    family2: Family2Config
    family3: Family3Config
    family4: Family4Config


@dataclass(frozen=True)
class OfflineDiscoveryPlanCell:
    cell_id: str
    family_id: str
    family_name: str
    is_edge_family: bool
    is_conditioning_family: bool
    source_venues: tuple[str, ...]
    source_symbols: tuple[str, ...]
    target_venues: tuple[str, ...]
    target_symbols: tuple[str, ...]
    signal_variant: Optional[str]
    lookback_ms: int
    horizon_ms: int
    required_resolution: str
    latency_gate_required: bool
    cost_config: CostConfig
    window_ids: tuple[str, ...]
    promotion_allowed: bool
    exclusion_reasons: tuple[str, ...]
    data_corpus_hash: str
    window_index_hash: str
    discovery_config_hash: str


@dataclass(frozen=True)
class OfflineDiscoveryPlan:
    status: str
    plan_cells: list[OfflineDiscoveryPlanCell]
    excluded_windows: list[dict[str, Any]]
    family_summary: dict[str, Any]
    train_window_ids: list[str]
    holdout_window_ids: list[str]
    edge_family_cell_count: int
    conditioning_cell_count: int
    discovery_config_hash: str
    plan_hash: str
    data_corpus_hash: str
    window_index_hash: str
    split_timestamp_boundary_ns: Optional[int]
    train_survivor_cell_ids: list[str]
    holdout_evaluation_cell_ids: list[str]
    survivor_freeze_status: str
    run_id: str
    generated_at_utc: str
    git_sha: str
    stress_rule_config_hash: Optional[str]
    precommitment_hash: Optional[str]


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_git_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _require_resolution(value: str) -> str:
    if value not in VALID_RESOLUTIONS:
        raise ValueError(f"Unsupported resolution {value!r}; allowed {sorted(VALID_RESOLUTIONS)}")
    return value


def _require_positive_int_list(values: Sequence[Any], field_name: str) -> tuple[int, ...]:
    cleaned = tuple(int(value) for value in values)
    if not cleaned or any(value <= 0 for value in cleaned):
        raise ValueError(f"{field_name} must be a non-empty list of positive integers")
    return cleaned


def _require_non_empty_str_list(values: Sequence[Any], field_name: str) -> tuple[str, ...]:
    cleaned = tuple(str(value) for value in values)
    if not cleaned or any(not value for value in cleaned):
        raise ValueError(f"{field_name} must be a non-empty list of strings")
    return cleaned


def _parse_cost_config(payload: dict[str, Any]) -> CostConfig:
    required = {"fees_bps", "slippage_bps", "quote_mismatch_bps"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"Missing cost-config fields: {missing}")
    return CostConfig(
        fees_bps=float(payload["fees_bps"]),
        slippage_bps=float(payload["slippage_bps"]),
        quote_mismatch_bps=float(payload["quote_mismatch_bps"]),
    )


def load_discovery_config(payload: dict[str, Any]) -> OfflineDiscoveryConfig:
    schema_version = payload.get("schema_version")
    if schema_version != DISCOVERY_SCHEMA_VERSION:
        raise ValueError(
            f"discovery config schema_version must be {DISCOVERY_SCHEMA_VERSION!r}, got {schema_version!r}"
        )

    split = payload.get("train_holdout", {})
    train_holdout = TrainHoldoutConfig(
        train_fraction=float(split.get("train_fraction", 0.7)),
        min_train_windows=int(split.get("min_train_windows", 1)),
        min_holdout_windows=int(split.get("min_holdout_windows", 1)),
    )
    if not 0.0 < train_holdout.train_fraction < 1.0:
        raise ValueError("train_fraction must be strictly between 0 and 1")
    if train_holdout.min_train_windows < 1 or train_holdout.min_holdout_windows < 1:
        raise ValueError("min_train_windows and min_holdout_windows must be >= 1")

    families = payload.get("families", {})

    f1 = families.get("family_1_same_venue_quote_basis", {})
    family1_enabled = bool(f1.get("enabled", False))
    family1 = Family1Config(
        enabled=family1_enabled,
        source_venue=str(f1.get("source_venue", "")),
        source_symbols=_require_non_empty_str_list(f1.get("source_symbols", []), "family1.source_symbols") if family1_enabled else tuple(str(value) for value in f1.get("source_symbols", [])),
        horizons_ms=_require_positive_int_list(f1.get("horizons_ms", []), "family1.horizons_ms") if family1_enabled else tuple(int(value) for value in f1.get("horizons_ms", [])),
        lookbacks_ms=_require_positive_int_list(f1.get("lookbacks_ms", []), "family1.lookbacks_ms") if family1_enabled else tuple(int(value) for value in f1.get("lookbacks_ms", [])),
        required_resolution=_require_resolution(str(f1.get("required_resolution", ""))) if family1_enabled else str(f1.get("required_resolution", RESOLUTION_BAR)),
        cost_config=_parse_cost_config(f1.get("fee_slippage_mismatch_assumptions", {})) if family1_enabled else CostConfig(0.0, 0.0, 0.0),
        min_events=int(f1.get("min_events", 1)),
    )

    f2 = families.get("family_2_cross_asset_stress_beta_lag", {})
    family2_enabled = bool(f2.get("enabled", False))
    family2_variants = _require_non_empty_str_list(f2.get("signal_variants", []), "family2.signal_variants") if family2_enabled else tuple(str(value) for value in f2.get("signal_variants", []))
    if family2_enabled:
        validate_family2_signal_variants(family2_variants)
    family2 = Family2Config(
        enabled=family2_enabled,
        source_symbols=_require_non_empty_str_list(f2.get("source_symbols", []), "family2.source_symbols") if family2_enabled else tuple(str(value) for value in f2.get("source_symbols", [])),
        target_symbols=_require_non_empty_str_list(f2.get("target_symbols", []), "family2.target_symbols") if family2_enabled else tuple(str(value) for value in f2.get("target_symbols", [])),
        signal_variants=family2_variants,
        horizons_ms=_require_positive_int_list(f2.get("horizons_ms", []), "family2.horizons_ms") if family2_enabled else tuple(int(value) for value in f2.get("horizons_ms", [])),
        lookbacks_ms=_require_positive_int_list(f2.get("lookbacks_ms", []), "family2.lookbacks_ms") if family2_enabled else tuple(int(value) for value in f2.get("lookbacks_ms", [])),
        required_resolution=_require_resolution(str(f2.get("required_resolution", ""))) if family2_enabled else str(f2.get("required_resolution", RESOLUTION_BAR)),
        cost_config=_parse_cost_config(f2.get("fee_slippage_mismatch_assumptions", {})) if family2_enabled else CostConfig(0.0, 0.0, 0.0),
        min_events=int(f2.get("min_events", 1)),
    )

    f3 = families.get("family_3_usd_reference_translation_lag", {})
    family3_enabled = bool(f3.get("enabled", False))
    usd_reference_venue_symbols = tuple(
        VenueSymbol(venue=str(item["venue"]), symbol=str(item["symbol"]))
        for item in f3.get("usd_reference_venue_symbols", [])
    )
    usdt_venue_symbols = tuple(
        VenueSymbol(venue=str(item["venue"]), symbol=str(item["symbol"]))
        for item in f3.get("usdt_venue_symbols", [])
    )
    if family3_enabled and (not usd_reference_venue_symbols or not usdt_venue_symbols):
        raise ValueError("family3 must declare usd_reference_venue_symbols and usdt_venue_symbols")
    family3 = Family3Config(
        enabled=family3_enabled,
        usd_reference_venue_symbols=usd_reference_venue_symbols,
        usdt_venue_symbols=usdt_venue_symbols,
        horizons_ms=_require_positive_int_list(f3.get("horizons_ms", []), "family3.horizons_ms") if family3_enabled else tuple(int(value) for value in f3.get("horizons_ms", [])),
        lookbacks_ms=_require_positive_int_list(f3.get("lookbacks_ms", []), "family3.lookbacks_ms") if family3_enabled else tuple(int(value) for value in f3.get("lookbacks_ms", [])),
        required_resolution=_require_resolution(str(f3.get("required_resolution", ""))) if family3_enabled else str(f3.get("required_resolution", RESOLUTION_BAR)),
        latency_gate_required=bool(f3.get("latency_gate_required", False)),
        cost_config=_parse_cost_config(f3.get("fee_slippage_mismatch_assumptions", {})) if family3_enabled else CostConfig(0.0, 0.0, 0.0),
        min_events=int(f3.get("min_events", 1)),
    )
    if family3.enabled and family3.latency_gate_required is not True:
        raise ValueError("family3 requires latency_gate_required=true")

    f4 = families.get("family_4_stablecoin_quote_regime_conditioning", {})
    family4_enabled = bool(f4.get("enabled", False))
    basis_source_payload = f4.get("basis_source", {})
    family4 = Family4Config(
        enabled=family4_enabled,
        conditioning_only=bool(f4.get("conditioning_only", True)),
        basis_source=BasisSourceConfig(
            venue=str(basis_source_payload.get("venue", "")),
            symbols=_require_non_empty_str_list(basis_source_payload.get("symbols", []), "family4.basis_source.symbols") if family4_enabled else tuple(str(value) for value in basis_source_payload.get("symbols", [])),
        ),
        basis_threshold_bps=float(f4.get("basis_threshold_bps", 0.0)),
        target_families=_require_non_empty_str_list(f4.get("target_families", []), "family4.target_families") if family4_enabled else tuple(str(value) for value in f4.get("target_families", [])),
        standalone=bool(f4.get("standalone", False)),
    )
    if family4.standalone:
        raise ValueError("family4 standalone mode is out of scope for Phase 2B-1")
    if family4.conditioning_only is not True:
        raise ValueError("family4 must remain conditioning_only=true in Phase 2B-1")

    return OfflineDiscoveryConfig(
        schema_version=schema_version,
        train_holdout=train_holdout,
        family1=family1,
        family2=family2,
        family3=family3,
        family4=family4,
    )


def _serialize_discovery_config(config: OfflineDiscoveryConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "train_holdout": asdict(config.train_holdout),
        "families": {
            "family_1_same_venue_quote_basis": {
                "enabled": config.family1.enabled,
                "source_venue": config.family1.source_venue,
                "source_symbols": list(config.family1.source_symbols),
                "horizons_ms": list(config.family1.horizons_ms),
                "lookbacks_ms": list(config.family1.lookbacks_ms),
                "required_resolution": config.family1.required_resolution,
                "fee_slippage_mismatch_assumptions": asdict(config.family1.cost_config),
                "min_events": config.family1.min_events,
            },
            "family_2_cross_asset_stress_beta_lag": {
                "enabled": config.family2.enabled,
                "source_symbols": list(config.family2.source_symbols),
                "target_symbols": list(config.family2.target_symbols),
                "signal_variants": list(config.family2.signal_variants),
                "horizons_ms": list(config.family2.horizons_ms),
                "lookbacks_ms": list(config.family2.lookbacks_ms),
                "required_resolution": config.family2.required_resolution,
                "fee_slippage_mismatch_assumptions": asdict(config.family2.cost_config),
                "min_events": config.family2.min_events,
            },
            "family_3_usd_reference_translation_lag": {
                "enabled": config.family3.enabled,
                "usd_reference_venue_symbols": [asdict(item) for item in config.family3.usd_reference_venue_symbols],
                "usdt_venue_symbols": [asdict(item) for item in config.family3.usdt_venue_symbols],
                "horizons_ms": list(config.family3.horizons_ms),
                "lookbacks_ms": list(config.family3.lookbacks_ms),
                "required_resolution": config.family3.required_resolution,
                "latency_gate_required": config.family3.latency_gate_required,
                "fee_slippage_mismatch_assumptions": asdict(config.family3.cost_config),
                "min_events": config.family3.min_events,
            },
            "family_4_stablecoin_quote_regime_conditioning": {
                "enabled": config.family4.enabled,
                "conditioning_only": config.family4.conditioning_only,
                "basis_source": {
                    "venue": config.family4.basis_source.venue,
                    "symbols": list(config.family4.basis_source.symbols),
                },
                "basis_threshold_bps": config.family4.basis_threshold_bps,
                "target_families": list(config.family4.target_families),
                "standalone": config.family4.standalone,
            },
        },
    }


def compute_discovery_config_hash(config: OfflineDiscoveryConfig) -> str:
    return _sha256_json(
        {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "discovery_config": _serialize_discovery_config(config),
        }
    )


def compute_window_index_hash(stress_windows_payload: dict[str, Any]) -> str:
    return _sha256_json(stress_windows_payload)


def _load_stress_windows(stress_windows_payload: dict[str, Any]) -> list[OfflineStressWindow]:
    if stress_windows_payload.get("schema_version") != OFFLINE_STRESS_WINDOW_SCHEMA_VERSION:
        raise ValueError("stress windows payload schema_version mismatch")
    windows = []
    for item in stress_windows_payload.get("windows", []):
        windows.append(OfflineStressWindow(**item))
    return windows


def _window_to_payload(window: OfflineStressWindow) -> dict[str, Any]:
    return asdict(window)


def _plan_cell_to_payload(cell: OfflineDiscoveryPlanCell) -> dict[str, Any]:
    payload = asdict(cell)
    payload["cost_config"] = asdict(cell.cost_config)
    return payload


def _split_windows(
    windows: Sequence[OfflineStressWindow],
    train_fraction: float,
) -> tuple[list[OfflineStressWindow], list[OfflineStressWindow], Optional[int]]:
    promotable = sorted(
        [window for window in windows if window.promotion_allowed],
        key=lambda item: (item.trigger_timestamp_ns, item.window_id),
    )
    if not promotable:
        return [], [], None
    train_count = max(1, int(len(promotable) * train_fraction))
    if train_count >= len(promotable):
        train_count = len(promotable) - 1 if len(promotable) > 1 else len(promotable)
    train_windows = promotable[:train_count]
    holdout_windows = promotable[train_count:]
    boundary = train_windows[-1].trigger_timestamp_ns if train_windows else None
    return train_windows, holdout_windows, boundary


def _structural_exclusions(
    windows: Sequence[OfflineStressWindow],
    required_resolution: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    allowed: list[str] = []
    excluded: list[dict[str, Any]] = []
    for window in sorted(windows, key=lambda item: (item.trigger_timestamp_ns, item.window_id)):
        reasons: list[str] = []
        if window.promotion_allowed is False:
            reasons.append("promotion_allowed=false")
        if window.resolution_type != required_resolution:
            reasons.append(f"incompatible_resolution:{window.resolution_type}")
        if reasons:
            excluded.append({"window_id": window.window_id, "reasons": reasons})
            continue
        allowed.append(window.window_id)
    return allowed, excluded


def _available_symbols_by_venue(prepare_manifest: Any) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for source in prepare_manifest.source_files:
        if isinstance(source, dict):
            venue = source["venue"]
            symbol = source["symbol"]
        else:
            venue = source.venue
            symbol = source.symbol
        mapping.setdefault(venue, set()).add(symbol)
    return mapping


def _dedupe_excluded_windows(excluded_windows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, set[str]] = {}
    for item in excluded_windows:
        merged.setdefault(item["window_id"], set()).update(item["reasons"])
    return [
        {"window_id": window_id, "reasons": sorted(reasons)}
        for window_id, reasons in sorted(merged.items(), key=lambda kv: kv[0])
    ]


def _make_cell(
    *,
    family_id: str,
    family_name: str,
    is_edge_family: bool,
    is_conditioning_family: bool,
    source_venues: Sequence[str],
    source_symbols: Sequence[str],
    target_venues: Sequence[str],
    target_symbols: Sequence[str],
    signal_variant: Optional[str],
    lookback_ms: int,
    horizon_ms: int,
    required_resolution: str,
    latency_gate_required: bool,
    cost_config: CostConfig,
    window_ids: Sequence[str],
    promotion_allowed: bool,
    exclusion_reasons: Sequence[str],
    data_corpus_hash: str,
    window_index_hash: str,
    discovery_config_hash: str,
) -> OfflineDiscoveryPlanCell:
    payload = {
        "family_id": family_id,
        "family_name": family_name,
        "source_venues": list(source_venues),
        "source_symbols": list(source_symbols),
        "target_venues": list(target_venues),
        "target_symbols": list(target_symbols),
        "signal_variant": signal_variant,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "required_resolution": required_resolution,
        "latency_gate_required": latency_gate_required,
        "window_ids": list(window_ids),
        "promotion_allowed": promotion_allowed,
        "exclusion_reasons": list(exclusion_reasons),
        "data_corpus_hash": data_corpus_hash,
        "window_index_hash": window_index_hash,
        "discovery_config_hash": discovery_config_hash,
    }
    cell_id = _sha256_json(payload)
    return OfflineDiscoveryPlanCell(
        cell_id=cell_id,
        family_id=family_id,
        family_name=family_name,
        is_edge_family=is_edge_family,
        is_conditioning_family=is_conditioning_family,
        source_venues=tuple(source_venues),
        source_symbols=tuple(source_symbols),
        target_venues=tuple(target_venues),
        target_symbols=tuple(target_symbols),
        signal_variant=signal_variant,
        lookback_ms=lookback_ms,
        horizon_ms=horizon_ms,
        required_resolution=required_resolution,
        latency_gate_required=latency_gate_required,
        cost_config=cost_config,
        window_ids=tuple(window_ids),
        promotion_allowed=promotion_allowed,
        exclusion_reasons=tuple(exclusion_reasons),
        data_corpus_hash=data_corpus_hash,
        window_index_hash=window_index_hash,
        discovery_config_hash=discovery_config_hash,
    )


def build_offline_discovery_plan(
    *,
    prepare_manifest: Any,
    stress_window_manifest: dict[str, Any],
    stress_windows_payload: dict[str, Any],
    discovery_config: OfflineDiscoveryConfig,
) -> OfflineDiscoveryPlan:
    generated_at_utc = _now_utc_iso()
    git_sha = _get_git_sha()
    run_id = f"offline_discovery_plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    def empty_plan(status: str, *, family_summary: Optional[dict[str, Any]] = None) -> OfflineDiscoveryPlan:
        plan_hash = _sha256_json(
            {
                "schema_version": DISCOVERY_SCHEMA_VERSION,
                "status": status,
                "data_corpus_hash": stress_window_manifest.get("data_corpus_hash"),
                "window_index_hash": stress_window_manifest.get("window_index_hash"),
                "discovery_config_hash": compute_discovery_config_hash(discovery_config),
                "plan_cells": [],
                "excluded_windows": [],
                "family_summary": family_summary or {},
                "train_window_ids": [],
                "holdout_window_ids": [],
            }
        )
        return OfflineDiscoveryPlan(
            status=status,
            plan_cells=[],
            excluded_windows=[],
            family_summary=family_summary or {},
            train_window_ids=[],
            holdout_window_ids=[],
            edge_family_cell_count=0,
            conditioning_cell_count=0,
            discovery_config_hash=compute_discovery_config_hash(discovery_config),
            plan_hash=plan_hash,
            data_corpus_hash=stress_window_manifest.get("data_corpus_hash") or getattr(prepare_manifest, "data_corpus_hash", None),
            window_index_hash=stress_window_manifest.get("window_index_hash"),
            split_timestamp_boundary_ns=None,
            train_survivor_cell_ids=[],
            holdout_evaluation_cell_ids=[],
            survivor_freeze_status="NOT_RUN_PHASE_2B1",
            run_id=run_id,
            generated_at_utc=generated_at_utc,
            git_sha=git_sha,
            stress_rule_config_hash=stress_window_manifest.get("stress_rule_config_hash"),
            precommitment_hash=stress_window_manifest.get("precommitment_hash"),
        )

    try:
        discovery_config_hash = compute_discovery_config_hash(discovery_config)
    except Exception:
        return empty_plan(STATUS_INVALID_DISCOVERY_CONFIG)

    if stress_window_manifest.get("status") in _REJECTED_STRESS_STATUSES:
        return empty_plan(STATUS_UNUSABLE_STRESS_INDEX)
    if stress_window_manifest.get("status") not in _ALLOWED_STRESS_STATUSES:
        return empty_plan(STATUS_UNUSABLE_STRESS_INDEX)
    if not stress_window_manifest.get("stress_rule_config_hash"):
        return empty_plan(STATUS_UNUSABLE_STRESS_INDEX)

    if getattr(prepare_manifest, "data_corpus_hash", None) != stress_window_manifest.get("data_corpus_hash"):
        return empty_plan(STATUS_INPUT_HASH_MISMATCH)
    prepare_precommitment_hash = getattr(prepare_manifest, "precommitment_hash", None)
    stress_precommitment_hash = stress_window_manifest.get("precommitment_hash")
    if (
        prepare_precommitment_hash is not None
        and stress_precommitment_hash is not None
        and prepare_precommitment_hash != stress_precommitment_hash
    ):
        return empty_plan(STATUS_INPUT_HASH_MISMATCH)

    try:
        stress_windows = _load_stress_windows(stress_windows_payload)
    except Exception:
        return empty_plan(STATUS_WINDOW_INDEX_HASH_MISMATCH)
    computed_window_index_hash = compute_window_index_hash(stress_windows_payload)
    if computed_window_index_hash != stress_window_manifest.get("window_index_hash"):
        return empty_plan(STATUS_WINDOW_INDEX_HASH_MISMATCH)

    enabled_families = [
        config.enabled
        for config in (discovery_config.family1, discovery_config.family2, discovery_config.family3, discovery_config.family4)
    ]
    if not any(enabled_families):
        return empty_plan(STATUS_NO_SUPPORTED_HYPOTHESIS_FAMILIES)

    if discovery_config.family4.standalone:
        return empty_plan(STATUS_INVALID_DISCOVERY_CONFIG)

    train_windows, holdout_windows, boundary = _split_windows(
        stress_windows,
        discovery_config.train_holdout.train_fraction,
    )
    train_window_ids = [window.window_id for window in train_windows]
    holdout_window_ids = [window.window_id for window in holdout_windows]

    available_by_venue = _available_symbols_by_venue(prepare_manifest)
    all_excluded_windows: list[dict[str, Any]] = []
    plan_cells: list[OfflineDiscoveryPlanCell] = []

    def source_symbols_exist(venue: str, symbols: Sequence[str]) -> bool:
        return set(symbols).issubset(available_by_venue.get(venue, set()))

    def venue_symbol_pairs_exist(pairs: Sequence[VenueSymbol]) -> bool:
        return all(item.symbol in available_by_venue.get(item.venue, set()) for item in pairs)

    if discovery_config.family1.enabled:
        allowed_windows, excluded = _structural_exclusions(stress_windows, discovery_config.family1.required_resolution)
        all_excluded_windows.extend(excluded)
        if source_symbols_exist(discovery_config.family1.source_venue, discovery_config.family1.source_symbols):
            for lookback_ms in discovery_config.family1.lookbacks_ms:
                for horizon_ms in discovery_config.family1.horizons_ms:
                    plan_cells.append(
                        _make_cell(
                            family_id="family_1_same_venue_quote_basis",
                            family_name="Kraken same-venue USD/USDT quote-basis",
                            is_edge_family=True,
                            is_conditioning_family=False,
                            source_venues=(discovery_config.family1.source_venue,),
                            source_symbols=discovery_config.family1.source_symbols,
                            target_venues=(discovery_config.family1.source_venue,),
                            target_symbols=discovery_config.family1.source_symbols,
                            signal_variant=None,
                            lookback_ms=lookback_ms,
                            horizon_ms=horizon_ms,
                            required_resolution=discovery_config.family1.required_resolution,
                            latency_gate_required=False,
                            cost_config=discovery_config.family1.cost_config,
                            window_ids=allowed_windows,
                            promotion_allowed=bool(allowed_windows),
                            exclusion_reasons=("promotion_allowed=false",) if not allowed_windows else (),
                            data_corpus_hash=prepare_manifest.data_corpus_hash,
                            window_index_hash=computed_window_index_hash,
                            discovery_config_hash=discovery_config_hash,
                        )
                    )

    if discovery_config.family2.enabled:
        allowed_windows, excluded = _structural_exclusions(stress_windows, discovery_config.family2.required_resolution)
        all_excluded_windows.extend(excluded)
        if source_symbols_exist("binance", discovery_config.family2.source_symbols) and source_symbols_exist("binance", discovery_config.family2.target_symbols):
            for variant in discovery_config.family2.signal_variants:
                for lookback_ms in discovery_config.family2.lookbacks_ms:
                    for horizon_ms in discovery_config.family2.horizons_ms:
                        plan_cells.append(
                            _make_cell(
                                family_id="family_2_cross_asset_stress_beta_lag",
                                family_name="Binance cross-asset stress beta-lag",
                                is_edge_family=True,
                                is_conditioning_family=False,
                                source_venues=("binance",),
                                source_symbols=discovery_config.family2.source_symbols,
                                target_venues=("binance",),
                                target_symbols=discovery_config.family2.target_symbols,
                                signal_variant=variant,
                                lookback_ms=lookback_ms,
                                horizon_ms=horizon_ms,
                                required_resolution=discovery_config.family2.required_resolution,
                                latency_gate_required=False,
                                cost_config=discovery_config.family2.cost_config,
                                window_ids=allowed_windows,
                                promotion_allowed=bool(allowed_windows),
                                exclusion_reasons=("promotion_allowed=false",) if not allowed_windows else (),
                                data_corpus_hash=prepare_manifest.data_corpus_hash,
                                window_index_hash=computed_window_index_hash,
                                discovery_config_hash=discovery_config_hash,
                            )
                        )

    if discovery_config.family3.enabled:
        allowed_windows, excluded = _structural_exclusions(stress_windows, discovery_config.family3.required_resolution)
        all_excluded_windows.extend(excluded)
        source_venues = tuple(item.venue for item in discovery_config.family3.usd_reference_venue_symbols)
        source_symbols = tuple(item.symbol for item in discovery_config.family3.usd_reference_venue_symbols)
        target_venues = tuple(item.venue for item in discovery_config.family3.usdt_venue_symbols)
        target_symbols = tuple(item.symbol for item in discovery_config.family3.usdt_venue_symbols)
        if venue_symbol_pairs_exist(discovery_config.family3.usd_reference_venue_symbols) and venue_symbol_pairs_exist(discovery_config.family3.usdt_venue_symbols):
            for lookback_ms in discovery_config.family3.lookbacks_ms:
                for horizon_ms in discovery_config.family3.horizons_ms:
                    plan_cells.append(
                        _make_cell(
                            family_id="family_3_usd_reference_translation_lag",
                            family_name="USD-reference vs USDT-venue translation lag",
                            is_edge_family=True,
                            is_conditioning_family=False,
                            source_venues=source_venues,
                            source_symbols=source_symbols,
                            target_venues=target_venues,
                            target_symbols=target_symbols,
                            signal_variant=None,
                            lookback_ms=lookback_ms,
                            horizon_ms=horizon_ms,
                            required_resolution=discovery_config.family3.required_resolution,
                            latency_gate_required=True,
                            cost_config=discovery_config.family3.cost_config,
                            window_ids=allowed_windows,
                            promotion_allowed=bool(allowed_windows),
                            exclusion_reasons=("promotion_allowed=false",) if not allowed_windows else (),
                            data_corpus_hash=prepare_manifest.data_corpus_hash,
                            window_index_hash=computed_window_index_hash,
                            discovery_config_hash=discovery_config_hash,
                        )
                    )

    if discovery_config.family4.enabled:
        allowed_windows, excluded = _structural_exclusions(stress_windows, RESOLUTION_BAR)
        all_excluded_windows.extend(excluded)
        valid_target_families = {
            "family_1_same_venue_quote_basis",
            "family_2_cross_asset_stress_beta_lag",
            "family_3_usd_reference_translation_lag",
        }
        if set(discovery_config.family4.target_families).issubset(valid_target_families):
            plan_cells.append(
                _make_cell(
                    family_id="family_4_stablecoin_quote_regime_conditioning",
                    family_name="Stablecoin/quote-regime conditioning",
                    is_edge_family=False,
                    is_conditioning_family=True,
                    source_venues=(discovery_config.family4.basis_source.venue,),
                    source_symbols=discovery_config.family4.basis_source.symbols,
                    target_venues=(),
                    target_symbols=discovery_config.family4.target_families,
                    signal_variant=None,
                    lookback_ms=0,
                    horizon_ms=0,
                    required_resolution=RESOLUTION_BAR,
                    latency_gate_required=False,
                    cost_config=CostConfig(0.0, 0.0, 0.0),
                    window_ids=allowed_windows,
                    promotion_allowed=False,
                    exclusion_reasons=(),
                    data_corpus_hash=prepare_manifest.data_corpus_hash,
                    window_index_hash=computed_window_index_hash,
                    discovery_config_hash=discovery_config_hash,
                )
            )

    if not plan_cells:
        return empty_plan(STATUS_NO_SUPPORTED_HYPOTHESIS_FAMILIES)

    edge_family_cell_count = len([cell for cell in plan_cells if cell.is_edge_family])
    conditioning_cell_count = len([cell for cell in plan_cells if cell.is_conditioning_family])
    promotable_edge_cells = [cell for cell in plan_cells if cell.is_edge_family and cell.promotion_allowed]

    family_summary = {
        "train_window_count": len(train_window_ids),
        "holdout_window_count": len(holdout_window_ids),
        "split_timestamp_boundary_ns": boundary,
        "family_1_cell_count": len([cell for cell in plan_cells if cell.family_id == "family_1_same_venue_quote_basis"]),
        "family_2_cell_count": len([cell for cell in plan_cells if cell.family_id == "family_2_cross_asset_stress_beta_lag"]),
        "family_3_cell_count": len([cell for cell in plan_cells if cell.family_id == "family_3_usd_reference_translation_lag"]),
        "family_4_cell_count": len([cell for cell in plan_cells if cell.family_id == "family_4_stablecoin_quote_regime_conditioning"]),
    }

    if not promotable_edge_cells:
        status = STATUS_NO_PROMOTABLE_WINDOWS
    elif len(train_window_ids) < discovery_config.train_holdout.min_train_windows:
        status = STATUS_NO_PROMOTABLE_WINDOWS
    elif len(holdout_window_ids) < discovery_config.train_holdout.min_holdout_windows:
        status = STATUS_NO_PROMOTABLE_WINDOWS
    else:
        status = STATUS_OFFLINE_DISCOVERY_PLAN_READY

    plan_cells = sorted(plan_cells, key=lambda item: (item.family_id, item.signal_variant or "", item.lookback_ms, item.horizon_ms, item.cell_id))
    all_excluded_windows = _dedupe_excluded_windows(all_excluded_windows)

    plan_hash = _sha256_json(
        {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "data_corpus_hash": prepare_manifest.data_corpus_hash,
            "window_index_hash": computed_window_index_hash,
            "discovery_config_hash": discovery_config_hash,
            "train_window_ids": train_window_ids,
            "holdout_window_ids": holdout_window_ids,
            "plan_cells": [_plan_cell_to_payload(cell) for cell in plan_cells],
            "excluded_windows": all_excluded_windows,
            "family_summary": family_summary,
        }
    )

    return OfflineDiscoveryPlan(
        status=status,
        plan_cells=plan_cells,
        excluded_windows=all_excluded_windows,
        family_summary=family_summary,
        train_window_ids=train_window_ids,
        holdout_window_ids=holdout_window_ids,
        edge_family_cell_count=edge_family_cell_count,
        conditioning_cell_count=conditioning_cell_count,
        discovery_config_hash=discovery_config_hash,
        plan_hash=plan_hash,
        data_corpus_hash=prepare_manifest.data_corpus_hash,
        window_index_hash=computed_window_index_hash,
        split_timestamp_boundary_ns=boundary,
        train_survivor_cell_ids=[],
        holdout_evaluation_cell_ids=[],
        survivor_freeze_status="NOT_RUN_PHASE_2B1",
        run_id=run_id,
        generated_at_utc=generated_at_utc,
        git_sha=git_sha,
        stress_rule_config_hash=stress_window_manifest.get("stress_rule_config_hash"),
        precommitment_hash=stress_precommitment_hash,
    )


def build_offline_discovery_plan_manifest_payload(
    plan: OfflineDiscoveryPlan,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
) -> dict[str, Any]:
    return {
        "run_id": plan.run_id,
        "phase": "offline_discovery_plan",
        "generated_at_utc": plan.generated_at_utc,
        "git_sha": plan.git_sha,
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "prepare_manifest_path": prepare_manifest_path,
        "stress_window_manifest_path": stress_window_manifest_path,
        "data_corpus_hash": plan.data_corpus_hash,
        "precommitment_hash": plan.precommitment_hash,
        "stress_rule_config_hash": plan.stress_rule_config_hash,
        "window_index_hash": plan.window_index_hash,
        "discovery_config_hash": plan.discovery_config_hash,
        "plan_hash": plan.plan_hash,
        "status": plan.status,
        "edge_family_cell_count": plan.edge_family_cell_count,
        "conditioning_cell_count": plan.conditioning_cell_count,
        "train_window_count": len(plan.train_window_ids),
        "holdout_window_count": len(plan.holdout_window_ids),
        "safety": "public_data_observer_only",
    }


def write_offline_discovery_plan_outputs(
    plan: OfflineDiscoveryPlan,
    out_dir: Path,
    *,
    prepare_manifest_path: str,
    stress_window_manifest_path: str,
    overwrite: bool = False,
) -> dict[str, Path]:
    run_dir = Path(out_dir)
    if run_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Output directory {run_dir} already exists. Pass overwrite=True or --overwrite to force."
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    plan_payload = {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "status": plan.status,
        "plan_cells": [_plan_cell_to_payload(cell) for cell in plan.plan_cells],
        "excluded_windows": plan.excluded_windows,
        "family_summary": plan.family_summary,
        "train_window_ids": plan.train_window_ids,
        "holdout_window_ids": plan.holdout_window_ids,
        "edge_family_cell_count": plan.edge_family_cell_count,
        "conditioning_cell_count": plan.conditioning_cell_count,
        "discovery_config_hash": plan.discovery_config_hash,
        "plan_hash": plan.plan_hash,
        "split_timestamp_boundary_ns": plan.split_timestamp_boundary_ns,
        "train_survivor_cell_ids": plan.train_survivor_cell_ids,
        "holdout_evaluation_cell_ids": plan.holdout_evaluation_cell_ids,
        "survivor_freeze_status": plan.survivor_freeze_status,
    }
    plan_path = run_dir / "offline_discovery_plan.json"
    plan_path.write_text(json.dumps(plan_payload, indent=2, sort_keys=True), encoding="utf-8")

    manifest_path = run_dir / "offline_discovery_plan_manifest.json"
    manifest_path.write_text(
        json.dumps(
            build_offline_discovery_plan_manifest_payload(
                plan,
                prepare_manifest_path=prepare_manifest_path,
                stress_window_manifest_path=stress_window_manifest_path,
            ),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {"plan_path": plan_path, "manifest_path": manifest_path}
