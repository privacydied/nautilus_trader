"""Phase 0A* coverage/mechanism diagnostic for HLP backstop absorption reversal.

This module implements a coverage/mechanism-only diagnostic that determines
whether historical HLP/liquidator-vault per-symbol backstop inventory changes
can be reconstructed from public archive data with the backstop component
separable from MM/Earn vault activity.

It is NOT an economic signal precommitment.
It CANNOT produce a conductor/paper promotion candidate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY_ID = "hlp_backstop_absorption_phase0a_star_coverage"
SIGNAL_FAMILY = "hlp_backstop_absorption_reversal"
PHASE = "0A_star_coverage_diagnostic"

FROZEN_SYMBOLS: tuple[str, ...] = (
    "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "BTC",
    "DOGE", "DOT", "ENA", "ETH", "FET", "HYPE", "INJ", "JUP", "LINK",
    "LTC", "MKR", "NEAR", "ONDO", "OP", "PENDLE", "SEI", "SOL", "SUI",
    "TIA", "TON", "TRX", "UNI", "WIF", "WLD", "XRP",
)

FORBIDDEN_STRINGS: tuple[str, ...] = (
    "submit_order", "place_order", "cancel_order", "private_key",
    "api_key", "live_execute", "paper_broker", "broker_connect",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "testnet",
    "alpaca", "ibkr", "userFillsByTime",
)

DEFAULT_START_DATE = "2025-08-17"
DEFAULT_WORKERS = 4

# ---------------------------------------------------------------------------
# Verdict enumeration
# ---------------------------------------------------------------------------


class HlpBackstopCoverageVerdict(str, Enum):
    """Exactly one terminal verdict, frozen. No threshold softening."""

    HOURLY_RECONSTRUCTABLE = "HLP_BACKSTOP_COVERAGE_HOURLY_RECONSTRUCTABLE"
    DAILY_ONLY = "HLP_BACKSTOP_COVERAGE_DAILY_ONLY"
    BACKSTOP_INSEPARABLE = "HLP_BACKSTOP_COVERAGE_BACKSTOP_INSEPARABLE"
    ARCHIVE_INFEASIBLE = "HLP_BACKSTOP_COVERAGE_ARCHIVE_INFEASIBLE"
    EXTERNAL_HEDGING_DOMINATES = "HLP_BACKSTOP_COVERAGE_EXTERNAL_HEDGING_DOMINATES"
    DIAGNOSTIC_ERROR = "HLP_BACKSTOP_COVERAGE_DIAGNOSTIC_ERROR"

    def is_terminal(self) -> bool:
        return True

    @classmethod
    def from_str(cls, s: str) -> HlpBackstopCoverageVerdict:
        for v in cls:
            if v.value == s:
                return v
        raise ValueError(f"Unknown verdict: {s}")


# ---------------------------------------------------------------------------
# Downstream unlock enum
# ---------------------------------------------------------------------------


class DownstreamUnlock(str, Enum):
    NONE = "none"
    HOURLY_SIGNAL_PHASE0_AUTHORABLE = "hourly_signal_phase0_authorable"
    DAILY_SIGNAL_PHASE0_AUTHORABLE = "daily_signal_phase0_authorable"


# ---------------------------------------------------------------------------
# Frozen data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackstopVaultRecord:
    """Resolved child vault with classification evidence."""

    address: str
    role_label: str  # BACKSTOP, BACKSTOP_INFERRED, MM_PARENT, MM_PER_SYMBOL_<symbol>, EARN, UNKNOWN
    resolution_source: str  # fixture, vaultDetails, docs, heuristic
    confidence: str  # documented, inferred_high, inferred_low, unknown
    symbol_coverage: tuple[str, ...] = ()
    fraction_fills_liquidation_flagged: float = 0.0
    evidence_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AddressResolutionResult:
    """Result of vault address resolution."""

    parent_address: str | None
    parent_resolved: bool
    children: tuple[BackstopVaultRecord, ...] = ()
    resolution_source: str = ""
    error: str | None = None


@dataclass(frozen=True)
class HourlyBar:
    """One hourly bucket of backstop-attributed inventory delta."""

    symbol: str
    hour_start_utc_ns: int  # nanosecond timestamp for hour boundary
    hour_start_iso: str
    net_delta: float  # signed size change in this hour
    fill_count: int
    blocks_referenced: tuple[str, ...] = ()
    missing: bool = False  # True if no fills in this hour window


@dataclass(frozen=True)
class DailyBar:
    """One daily bucket of backstop-attributed inventory delta."""

    symbol: str
    date_iso: str
    net_delta: float
    fill_count: int
    source: str  # path_a_aggregated, path_b_daily_snapshot
    missing: bool = False


@dataclass(frozen=True)
class CoverageSymbolResult:
    """Per-symbol coverage gate results."""

    symbol: str
    hourly_hours: int
    hourly_consecutive_days: float
    hourly_max_gap_hours: float
    daily_days: int
    daily_max_gap_days: float
    hourly_pass: bool
    daily_pass: bool
    total_backstop_fills: int
    total_backstop_daily_deltas: int
    lookahead_violations: int
    half_life_hours: float | None = None


@dataclass(frozen=True)
class ReconstructionResult:
    """Per-vault, per-symbol reconstruction outcome."""

    vault_address: str
    vault_role: str
    symbol: str
    hourly_bars: tuple[HourlyBar, ...] = ()
    daily_bars: tuple[DailyBar, ...] = ()
    cumulative_hourly_inventory: float = 0.0
    cumulative_daily_inventory: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class CrossSourceDay:
    """One day of cross-source comparison."""

    date_iso: str
    symbol: str
    path_b_delta: float
    path_a_aggregated_delta: float
    diff: float
    tolerance: float
    passed: bool


@dataclass(frozen=True)
class HalfLifeResult:
    """Per-symbol half-life estimate for backstop inventory shocks."""

    symbol: str
    shock_count: int
    median_half_life_hours: float | None
    half_lives_hours: tuple[float, ...] = ()
    qualifying: bool = True
    error: str | None = None


@dataclass(frozen=True)
class ControlsResult:
    """Sanity control outputs."""

    mm_backstop_correlation_warning: bool = False
    mm_backstop_median_abs_correlation: float | None = None
    long_run_mean_net_inventory: dict[str, float] = field(default_factory=dict)
    long_run_median_net_inventory: dict[str, float] = field(default_factory=dict)
    block_ordering: str = "SKIPPED_NO_REPLICA_CMDS"  # SKIPPED_*, PASSED, FAILED
    address_resolution_stable: bool = True
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Gate results dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResults:
    """All gate outcomes for the diagnostic run."""

    hourly_pass: bool = False
    hourly_reason: str = ""
    daily_pass: bool = False
    daily_reason: str = ""
    backstop_inseparable: bool = False
    backstop_inseparable_reason: str = ""
    archive_infeasible: bool = False
    archive_infeasible_reason: str = ""
    external_hedging_dominates: bool = False
    external_hedging_reason: str = ""
    diagnostic_error: bool = False
    diagnostic_error_reason: str = ""

    # Gate detail fields
    backstop_child_resolved: bool = False
    backstop_confidence: str = ""
    qualifying_symbol_count: int = 0
    qualifying_symbols: tuple[str, ...] = ()
    min_consecutive_days: float = 0.0
    max_gap_hours: float = 0.0
    max_gap_days: float = 0.0
    total_backstop_fills: int = 0
    total_backstop_daily_deltas: int = 0
    cross_source_consistency: str = ""  # PASSED, FAILED, SKIPPED_NO_DAILY_SOURCE
    cross_source_fail_pct: float = 0.0
    lookahead_violations: int = 0
    median_half_life_hours: float | None = None


# ---------------------------------------------------------------------------
# Run metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunMetadata:
    """Metadata for one diagnostic run."""

    run_id: str
    start_timestamp_utc: str
    git_sha: str
    data_root: str
    reports_root: str
    precommitment_path: str
    precommitment_hash: str
    symbol_universe: tuple[str, ...]
    window_start: str
    window_end: str
    allow_public_metadata_api: bool
    dry_run: bool


# ---------------------------------------------------------------------------
# Sanity control names
# ---------------------------------------------------------------------------


class BlockOrderingStatus(str, Enum):
    SKIPPED_NO_REPLICA_CMDS = "SKIPPED_NO_REPLICA_CMDS"
    PASSED = "PASSED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Precommitment hash
# ---------------------------------------------------------------------------


def compute_precommitment_hash(path: str | Path) -> str:
    """Compute SHA-256 of the precommitment doc, excluding the line containing
    'Precommitment SHA-256:' so the hash is not self-referential."""
    path = Path(path)
    raw = path.read_bytes()
    lines = raw.split(b"\n")
    filtered = b"\n".join(
        line for line in lines if b"Precommitment SHA-256:" not in line
    )
    return hashlib.sha256(filtered).hexdigest()


def verify_precommitment_hash(path: str | Path, expected_hash: str) -> None:
    """Verify precommitment hash. Raises ValueError on mismatch.

    The expected hash is read from the doc line containing 'Precommitment SHA-256:'.
    The runtime hash excludes that same line (no self-referential trap).
    """
    path = Path(path)
    raw = path.read_bytes()
    lines = raw.split(b"\n")

    # Extract expected hash from the doc
    expected_from_doc: str | None = None
    for line in lines:
        decoded = line.decode("utf-8", errors="replace").strip()
        if "Precommitment SHA-256:" in decoded:
            parts = decoded.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if all(c in "0123456789abcdef" for c in candidate) and len(candidate) == 64:
                    expected_from_doc = candidate
                    break

    if expected_from_doc is not None:
        # Verify against the explicit expected
        actual = compute_precommitment_hash(path)
        if actual != expected_from_doc:
            raise ValueError(
                f"Precommitment hash mismatch: doc says {expected_from_doc}, "
                f"computed {actual}"
            )

    # Verify against the caller's expected
    if expected_hash and expected_from_doc is not None:
        if expected_hash != expected_from_doc:
            raise ValueError(
                f"Precommitment hash mismatch: expected {expected_hash}, "
                f"doc says {expected_from_doc}"
            )
    elif expected_hash:
        actual = compute_precommitment_hash(path)
        if actual != expected_hash:
            raise ValueError(
                f"Precommitment hash mismatch: expected {expected_hash}, "
                f"computed {actual}"
            )


# ---------------------------------------------------------------------------
# Source inventory struct
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceInventoryEntry:
    """One source's availability metadata."""

    source_type: str  # node_fills_by_block, artemis_perp_balances, vault_metadata
    available: bool
    path: str | None
    schema_version: str | None
    row_count: int = 0
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceInventory:
    """All source availability checks."""

    entries: tuple[SourceInventoryEntry, ...] = ()


# ---------------------------------------------------------------------------
# Verdict resolution
# ---------------------------------------------------------------------------


def resolve_verdict(
    gates: GateResults,
) -> tuple[HlpBackstopCoverageVerdict, DownstreamUnlock]:
    """Resolve exactly one terminal verdict from gate results.

    Priority: DIAGNOSTIC_ERROR > ARCHIVE_INFEASIBLE > EXTERNAL_HEDGING > BACKSTOP_INSEPARABLE > HOURLY > DAILY
    """
    if gates.diagnostic_error:
        return HlpBackstopCoverageVerdict.DIAGNOSTIC_ERROR, DownstreamUnlock.NONE

    if gates.archive_infeasible:
        return HlpBackstopCoverageVerdict.ARCHIVE_INFEASIBLE, DownstreamUnlock.NONE

    if gates.hourly_pass:
        if gates.external_hedging_dominates:
            return HlpBackstopCoverageVerdict.EXTERNAL_HEDGING_DOMINATES, DownstreamUnlock.NONE
        return HlpBackstopCoverageVerdict.HOURLY_RECONSTRUCTABLE, DownstreamUnlock.HOURLY_SIGNAL_PHASE0_AUTHORABLE

    if gates.external_hedging_dominates:
        return HlpBackstopCoverageVerdict.EXTERNAL_HEDGING_DOMINATES, DownstreamUnlock.NONE

    if gates.backstop_inseparable:
        return HlpBackstopCoverageVerdict.BACKSTOP_INSEPARABLE, DownstreamUnlock.NONE

    if gates.daily_pass:
        return HlpBackstopCoverageVerdict.DAILY_ONLY, DownstreamUnlock.DAILY_SIGNAL_PHASE0_AUTHORABLE

    # Fallthrough: archive seems present but gates didn't pass
    return HlpBackstopCoverageVerdict.ARCHIVE_INFEASIBLE, DownstreamUnlock.NONE


# ---------------------------------------------------------------------------
# Summary schema
# ---------------------------------------------------------------------------


def build_summary(
    *,
    precommitment_hash: str,
    verdict: HlpBackstopCoverageVerdict,
    downstream_unlock: DownstreamUnlock,
    artifact_paths: Mapping[str, str],
    gates: GateResults,
    warnings: list[str],
) -> dict[str, Any]:
    """Build the locked-format summary.json payload."""
    blocked_reason: str | None = None
    if verdict not in (
        HlpBackstopCoverageVerdict.HOURLY_RECONSTRUCTABLE,
        HlpBackstopCoverageVerdict.DAILY_ONLY,
    ):
        blocked_reason = verdict.value

    return {
        "study_id": STUDY_ID,
        "signal_family": SIGNAL_FAMILY,
        "phase": PHASE,
        "precommitment_hash": precommitment_hash,
        "verdict": verdict.value,
        "promotion_candidate": False,
        "paper_promotion_locked": True,
        "observer_only": True,
        "no_order_intent": True,
        "conductor_ready": False,
        "downstream_unlock": downstream_unlock.value,
        "blocked_reason": blocked_reason,
        "artifact_paths": dict(artifact_paths),
        "gate_results": {
            "hourly_pass": gates.hourly_pass,
            "daily_pass": gates.daily_pass,
            "backstop_inseparable": gates.backstop_inseparable,
            "archive_infeasible": gates.archive_infeasible,
            "external_hedging_dominates": gates.external_hedging_dominates,
            "diagnostic_error": gates.diagnostic_error,
            "backstop_child_resolved": gates.backstop_child_resolved,
            "backstop_confidence": gates.backstop_confidence,
            "qualifying_symbol_count": gates.qualifying_symbol_count,
            "min_consecutive_days": gates.min_consecutive_days,
            "max_gap_hours": gates.max_gap_hours,
            "max_gap_days": gates.max_gap_days,
            "total_backstop_fills": gates.total_backstop_fills,
            "total_backstop_daily_deltas": gates.total_backstop_daily_deltas,
            "cross_source_consistency": gates.cross_source_consistency,
            "cross_source_fail_pct": gates.cross_source_fail_pct,
            "lookahead_violations": gates.lookahead_violations,
            "median_half_life_hours": gates.median_half_life_hours,
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------


def _ensure_dir(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a deterministic JSON artifact."""
    _ensure_dir(path)
    tmp = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        tmp.write(data.encode("utf-8"))
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def write_csv_artifact(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Write a deterministic CSV artifact."""
    _ensure_dir(path)
    tmp = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with open(tmp.name, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(fieldnames))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def write_text_artifact(path: Path, content: str) -> None:
    """Write a text artifact."""
    _ensure_dir(path)
    tmp = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(content.encode("utf-8"))
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    """Write human-readable summary.md."""
    lines = [
        f"# HLP backstop absorption Phase 0A* coverage diagnostic",
        f"",
        f"**Verdict:** {summary['verdict']}",
        f"**Downstream unlock:** {summary['downstream_unlock']}",
        f"**Precommitment hash:** {summary['precommitment_hash']}",
        f"**Promotion candidate:** No (locked false)",
        f"**Conductor ready:** No (locked false)",
        f"**Observer only:** Yes (locked true)",
        f"**No order intent:** Yes (locked true)",
        f"",
        f"## Gate results",
        f"",
    ]
    for k, v in summary.get("gate_results", {}).items():
        lines.append(f"- **{k}:** {v}")
    lines.append("")
    lines.append("## Warnings")
    for w in summary.get("warnings", []):
        lines.append(f"- {w}")
    lines.append("")
    lines.append("## Artifacts")
    for name, apath in summary.get("artifact_paths", {}).items():
        lines.append(f"- {name}: {apath}")
    lines.append("")

    write_text_artifact(path, "\n".join(lines))


def write_manifest(
    path: Path,
    run_meta: RunMetadata,
    verdict: HlpBackstopCoverageVerdict,
    artifacts: Mapping[str, str],
) -> None:
    """Write manifest.json."""
    write_json_artifact(
        path,
        {
            "run_id": run_meta.run_id,
            "start_timestamp_utc": run_meta.start_timestamp_utc,
            "git_sha": run_meta.git_sha,
            "data_root": run_meta.data_root,
            "reports_root": run_meta.reports_root,
            "precommitment_path": run_meta.precommitment_path,
            "precommitment_hash": run_meta.precommitment_hash,
            "symbol_count": len(run_meta.symbol_universe),
            "window_start": run_meta.window_start,
            "window_end": run_meta.window_end,
            "verdict": verdict.value,
            "artifact_count": len(artifacts),
        },
    )


def write_address_resolution_artifact(
    path: Path,
    result: AddressResolutionResult,
) -> None:
    """Write address_resolution.json."""
    write_json_artifact(
        path,
        {
            "parent_address": result.parent_address,
            "parent_resolved": result.parent_resolved,
            "resolution_source": result.resolution_source,
            "error": result.error,
            "children": [
                {
                    "address": c.address,
                    "role_label": c.role_label,
                    "resolution_source": c.resolution_source,
                    "confidence": c.confidence,
                    "symbol_count": len(c.symbol_coverage),
                    "symbol_coverage": list(c.symbol_coverage),
                    "fraction_fills_liquidation_flagged": c.fraction_fills_liquidation_flagged,
                    "evidence_fields": dict(c.evidence_fields),
                }
                for c in result.children
            ],
        },
    )


def write_coverage_csv(
    path: Path,
    results: Sequence[CoverageSymbolResult],
) -> None:
    """Write coverage_per_symbol.csv."""
    fieldnames = [
        "symbol", "hourly_hours", "hourly_consecutive_days", "hourly_max_gap_hours",
        "daily_days", "daily_max_gap_days", "hourly_pass", "daily_pass",
        "total_backstop_fills", "total_backstop_daily_deltas",
        "lookahead_violations", "half_life_hours",
    ]
    rows = [asdict(r) for r in results]
    write_csv_artifact(path, fieldnames, rows)


def write_hourly_parquet_fallback(
    path: Path,
    bars: Sequence[HourlyBar],
) -> None:
    """Write hourly backstop inventory as JSONL (parquet substitute when pyarrow unavailable)."""
    _ensure_dir(path)
    with open(path, "w") as f:
        for bar in bars:
            f.write(
                json.dumps(asdict(bar), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )


def write_daily_parquet_fallback(
    path: Path,
    bars: Sequence[DailyBar],
) -> None:
    """Write daily backstop inventory as JSONL."""
    _ensure_dir(path)
    with open(path, "w") as f:
        for bar in bars:
            f.write(
                json.dumps(asdict(bar), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )


def write_cross_source_csv(
    path: Path,
    days: Sequence[CrossSourceDay],
) -> None:
    """Write cross_source_check.csv."""
    fieldnames = [
        "date_iso", "symbol", "path_b_delta", "path_a_aggregated_delta",
        "diff", "tolerance", "passed",
    ]
    rows = [asdict(d) for d in days]
    write_csv_artifact(path, fieldnames, rows)


def write_half_life_csv(
    path: Path,
    results: Sequence[HalfLifeResult],
) -> None:
    """Write external_hedging_halflife.csv."""
    fieldnames = [
        "symbol", "shock_count", "median_half_life_hours", "qualifying", "error",
    ]
    rows = []
    for r in results:
        d = asdict(r)
        d.pop("half_lives_hours", None)
        rows.append(d)
    write_csv_artifact(path, fieldnames, rows)


def write_controls_artifact(path: Path, controls: ControlsResult) -> None:
    """Write controls.json."""
    write_json_artifact(
        path,
        {
            "mm_backstop_correlation_warning": controls.mm_backstop_correlation_warning,
            "mm_backstop_median_abs_correlation": controls.mm_backstop_median_abs_correlation,
            "long_run_mean_net_inventory": dict(controls.long_run_mean_net_inventory),
            "long_run_median_net_inventory": dict(controls.long_run_median_net_inventory),
            "block_ordering": controls.block_ordering,
            "address_resolution_stable": controls.address_resolution_stable,
            "warnings": list(controls.warnings),
        },
    )


def write_lookahead_audit(
    path: Path,
    violations: Sequence[Mapping[str, Any]],
) -> None:
    """Write lookahead_audit.json."""
    write_json_artifact(
        path,
        {
            "violation_count": len(violations),
            "violations": [dict(v) for v in violations],
        },
    )


def write_source_inventory(path: Path, inventory: SourceInventory) -> None:
    """Write source_inventory.json."""
    write_json_artifact(
        path,
        {
            "entries": [
                {
                    "source_type": e.source_type,
                    "available": e.available,
                    "path": e.path,
                    "schema_version": e.schema_version,
                    "row_count": e.row_count,
                    "error": e.error,
                    "metadata": dict(e.metadata),
                }
                for e in inventory.entries
            ],
        },
    )


def write_suggested_registry_snippet(path: Path, verdict: HlpBackstopCoverageVerdict, reason: str) -> None:
    """Write suggested_registry_snippet.md. Always non-promotable."""
    lines = [
        "> This is a suggested registry snippet only — not a live registry edit.",
        "> It was generated by a Phase 0A* coverage diagnostic and must not be",
        "> auto-promoted to paper or conductor.",
        "",
        f"**Status:** BLOCKED — {verdict.value}",
        f"**Reason:** {reason}",
        "",
        "```json",
        json.dumps(
            {
                "study_id": STUDY_ID,
                "verdict": verdict.value,
                "promotion_candidate": False,
                "paper_promotion_locked": True,
                "conductor_ready": False,
            },
            indent=2,
        ),
        "```",
        "",
        "_This diagnostic never writes to the registry._",
    ]
    write_text_artifact(path, "\n".join(lines))


# ---------------------------------------------------------------------------
# Coverage gate evaluation
# ---------------------------------------------------------------------------


def evaluate_hourly_gate(
    symbol_results: Sequence[CoverageSymbolResult],
    backstop_resolved: bool,
    backstop_confidence: str,
    cross_source_consistency: str,
    lookahead_violations: int,
    qualifying_cross_source_fail_pct: float = 0.0,
) -> GateResults:
    """Evaluate hourly coverage gate."""
    gates = GateResults()

    if not backstop_resolved:
        gates = _replace_gate(gates, archive_infeasible=True,
                              archive_infeasible_reason="Backstop child vault not resolved")
        return gates

    if backstop_confidence not in ("documented", "inferred_high"):
        gates = _replace_gate(gates, backstop_inseparable=True,
                              backstop_inseparable_reason=f"Backstop confidence too low: {backstop_confidence}")
        return gates

    qualifying = [r for r in symbol_results if r.hourly_pass]
    if len(qualifying) < 12:
        gates = _replace_gate(gates, hourly_pass=False,
                              hourly_reason=f"Only {len(qualifying)} / 12 qualifying symbols",
                              qualifying_symbol_count=len(qualifying),
                              qualifying_symbols=tuple(r.symbol for r in qualifying))
        return gates

    min_days = min(r.hourly_consecutive_days for r in qualifying)
    if min_days < 180:
        gates = _replace_gate(gates, hourly_pass=False,
                              hourly_reason=f"Min consecutive days {min_days:.1f} < 180",
                              min_consecutive_days=min_days)
        return gates

    max_gap = max(r.hourly_max_gap_hours for r in qualifying)
    if max_gap > 72:
        gates = _replace_gate(gates, hourly_pass=False,
                              hourly_reason=f"Max gap {max_gap:.1f}h > 72h",
                              max_gap_hours=max_gap)
        return gates

    total_fills = sum(r.total_backstop_fills for r in qualifying)
    if total_fills < 500:
        gates = _replace_gate(gates, hourly_pass=False,
                              hourly_reason=f"Total fills {total_fills} < 500",
                              total_backstop_fills=total_fills)
        return gates

    if lookahead_violations > 0:
        gates = _replace_gate(gates, diagnostic_error=True,
                              diagnostic_error_reason=f"Lookahead violations: {lookahead_violations}",
                              lookahead_violations=lookahead_violations)
        return gates

    if cross_source_consistency == "FAILED":
        gates = _replace_gate(gates, archive_infeasible=True,
                              archive_infeasible_reason=f"Cross-source consistency failed: {qualifying_cross_source_fail_pct:.1f}% days failed",
                              cross_source_consistency=cross_source_consistency,
                              cross_source_fail_pct=qualifying_cross_source_fail_pct)
        return gates

    gates = _replace_gate(
        gates,
        hourly_pass=True,
        hourly_reason=f"All gates pass: {len(qualifying)} symbols, min {min_days:.0f}d, max gap {max_gap:.0f}h",
        backstop_child_resolved=backstop_resolved,
        backstop_confidence=backstop_confidence,
        qualifying_symbol_count=len(qualifying),
        qualifying_symbols=tuple(r.symbol for r in qualifying),
        min_consecutive_days=min_days,
        max_gap_hours=max_gap,
        total_backstop_fills=total_fills,
        lookahead_violations=lookahead_violations,
        cross_source_consistency=cross_source_consistency,
        cross_source_fail_pct=qualifying_cross_source_fail_pct,
    )
    return gates


def evaluate_daily_gate(
    symbol_results: Sequence[CoverageSymbolResult],
    backstop_resolved: bool,
    backstop_confidence: str,
) -> GateResults:
    """Evaluate daily coverage gate."""
    gates = GateResults()

    if not backstop_resolved:
        gates = _replace_gate(gates, archive_infeasible=True,
                              archive_infeasible_reason="Backstop child vault not resolved")
        return gates

    if backstop_confidence not in ("documented", "inferred_high"):
        gates = _replace_gate(gates, backstop_inseparable=True,
                              backstop_inseparable_reason=f"Backstop confidence too low: {backstop_confidence}")
        return gates

    qualifying = [r for r in symbol_results if r.daily_pass]
    if len(qualifying) < 12:
        gates = _replace_gate(gates, daily_pass=False,
                              daily_reason=f"Only {len(qualifying)} / 12 symbols qualify",
                              qualifying_symbol_count=len(qualifying),
                              qualifying_symbols=tuple(r.symbol for r in qualifying))
        return gates

    min_days = min(r.daily_days for r in qualifying)
    if min_days < 180:
        gates = _replace_gate(gates, daily_pass=False,
                              daily_reason=f"Min days {min_days} < 180",
                              min_consecutive_days=float(min_days))
        return gates

    max_gap = max(r.daily_max_gap_days for r in qualifying)
    if max_gap > 5:
        gates = _replace_gate(gates, daily_pass=False,
                              daily_reason=f"Max daily gap {max_gap} > 5 days",
                              max_gap_days=max_gap)
        return gates

    total_deltas = sum(r.total_backstop_daily_deltas for r in qualifying)
    if total_deltas < 200:
        gates = _replace_gate(gates, daily_pass=False,
                              daily_reason=f"Total daily delta events {total_deltas} < 200",
                              total_backstop_daily_deltas=total_deltas)
        return gates

    gates = _replace_gate(
        gates,
        daily_pass=True,
        daily_reason=f"All daily gates pass: {len(qualifying)} symbols, min {min_days}d",
        backstop_child_resolved=backstop_resolved,
        backstop_confidence=backstop_confidence,
        qualifying_symbol_count=len(qualifying),
        qualifying_symbols=tuple(r.symbol for r in qualifying),
        min_consecutive_days=float(min_days),
        max_gap_days=max_gap,
        total_backstop_daily_deltas=total_deltas,
    )
    return gates


def evaluate_external_hedging_gate(
    half_life_results: Sequence[HalfLifeResult],
    qualifying_symbols: tuple[str, ...],
) -> tuple[bool, str, float | None]:
    """Evaluate external hedging half-life gate.

    Returns (dominates, reason, median_half_life).
    """
    qual_results = [r for r in half_life_results if r.symbol in qualifying_symbols and r.qualifying]
    half_lives = []
    for r in qual_results:
        if r.median_half_life_hours is not None:
            half_lives.append(r.median_half_life_hours)

    if not half_lives:
        return False, "No half-life data available for qualifying symbols", None

    median = statistics.median(half_lives)
    if median < 1.0:
        return (
            True,
            f"Median half-life {median:.3f}h < 1h across {len(half_lives)} qualifying symbols",
            median,
        )
    return False, f"Median half-life {median:.3f}h >= 1h", median


# ---------------------------------------------------------------------------
# Controls evaluation helpers
# ---------------------------------------------------------------------------


def check_mm_correlation(
    backstop_deltas: dict[str, list[float]],
    mm_deltas: dict[str, list[float]],
    threshold: float = 0.40,
) -> tuple[bool, float | None]:
    """Check pairwise symbol-level correlation between backstop and MM deltas.

    Returns (warning_triggered, median_abs_correlation).
    """
    correlations: list[float] = []
    for sym in backstop_deltas:
        b = backstop_deltas[sym]
        m = mm_deltas.get(sym)
        if m is None or len(b) != len(m) or len(b) < 3:
            continue
        r = _pearson(b, m)
        if r is not None:
            correlations.append(abs(r))

    if not correlations:
        return False, None

    med = statistics.median(correlations)
    return med > threshold, med


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = sum((xi - mx) ** 2 for xi in x)
    dy = sum((yi - my) ** 2 for yi in y)
    if dx == 0 or dy == 0:
        return None
    r = num / (math.sqrt(dx) * math.sqrt(dy))
    return max(-1.0, min(1.0, r))


def _replace_gate(gates: GateResults, **kwargs: Any) -> GateResults:
    """Create a new GateResults with specified fields replaced."""
    current = {
        "hourly_pass": gates.hourly_pass,
        "hourly_reason": gates.hourly_reason,
        "daily_pass": gates.daily_pass,
        "daily_reason": gates.daily_reason,
        "backstop_inseparable": gates.backstop_inseparable,
        "backstop_inseparable_reason": gates.backstop_inseparable_reason,
        "archive_infeasible": gates.archive_infeasible,
        "archive_infeasible_reason": gates.archive_infeasible_reason,
        "external_hedging_dominates": gates.external_hedging_dominates,
        "external_hedging_reason": gates.external_hedging_reason,
        "diagnostic_error": gates.diagnostic_error,
        "diagnostic_error_reason": gates.diagnostic_error_reason,
        "backstop_child_resolved": gates.backstop_child_resolved,
        "backstop_confidence": gates.backstop_confidence,
        "qualifying_symbol_count": gates.qualifying_symbol_count,
        "qualifying_symbols": gates.qualifying_symbols,
        "min_consecutive_days": gates.min_consecutive_days,
        "max_gap_hours": gates.max_gap_hours,
        "max_gap_days": gates.max_gap_days,
        "total_backstop_fills": gates.total_backstop_fills,
        "total_backstop_daily_deltas": gates.total_backstop_daily_deltas,
        "cross_source_consistency": gates.cross_source_consistency,
        "cross_source_fail_pct": gates.cross_source_fail_pct,
        "lookahead_violations": gates.lookahead_violations,
        "median_half_life_hours": gates.median_half_life_hours,
    }
    current.update(kwargs)
    return GateResults(**current)


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]


def datetime_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_git_sha() -> str:
    """Get current git SHA from git directly."""
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def hour_bucket_ns(ts_ns: int) -> int:
    """Truncate nanosecond timestamp to hour boundary."""
    ns_per_hour = 3_600_000_000_000
    return (ts_ns // ns_per_hour) * ns_per_hour


def ns_to_iso(ts_ns: int) -> str:
    """Convert nanosecond timestamp to ISO string."""
    sec = ts_ns // 1_000_000_000
    return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()


def iso_to_ns(iso_str: str) -> int:
    """Convert ISO date string to nanosecond timestamp."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def compute_from_iso(iso_date: str) -> int:
    """Start of day in ns from ISO date string."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)