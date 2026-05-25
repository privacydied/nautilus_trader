"""Venue-age-aware Phase 0A audit for liquidation flush aftershock reversal v0.

This module is a separate diagnostic wrapper around the original calendar-year-gated
Phase 0A. It keeps the frozen event definition and cooldown, excludes BTC/ETH from
altcoin gates, and replaces the unsatisfiable calendar-year gate with venue-age-aware
quarter/month diversification gates.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    COOLDOWN_HOURS,
    MAX_SYMBOL_EVENT_SHARE,
    MIN_ACCEPTED_EVENTS,
    MIN_ACCEPTED_SYMBOLS,
    MIN_SYMBOLS_WITH_3_EVENTS,
    OI_CHANGE_THRESHOLD_PCT,
    PRICE_RETURN_THRESHOLD_PCT,
    SAFETY_MODE,
    STATUS_INSUFFICIENT_COVERAGE,
    STATUS_INVALID_INPUT,
    STATUS_INVALID_PRECOMMITMENT,
    STATUS_READY as ORIGINAL_READY,
    STATUS_SYMBOL_CONCENTRATION,
    STATUS_UNDERPOWERED,
    ArchiveRow,
    EventRecord,
    LoadDiagnostics,
    Phase0AResult,
    SymbolAudit,
    WindowPoint,
    apply_cooldown,
    compute_distribution_diagnostics,
    compute_past_8h_points,
    compute_precommitment_hash,
    compute_threshold_diagnostics,
    dedupe_symbol_rows,
    determine_status,
    discover_archive_paths as original_discover_archive_paths,
    generate_candidate_events,
    git_metadata,
    load_archive_rows,
    utc_iso,
    validate_per_symbol_coverage,
    write_report_artifacts as original_write_report_artifacts,
)

STUDY_ID = "liquidation_flush_aftershock_reversal_venue_age_aware_phase0a"
STAGE = "venue_age_aware_phase0a_event_population_audit"
VENUE = "hyperliquid"
ALTCOIN_EXCLUDED_SYMBOLS = frozenset({"BTC", "ETH"})
ACTIVE_ARCHIVE_PATH = Path("data/hyperliquid_oi_velocity_compression_phase0")

STATUS_QUARTER_CONCENTRATION = "PHASE0A_QUARTER_CONCENTRATION_FAILED"
STATUS_MONTH_CONCENTRATION = "PHASE0A_MONTH_CONCENTRATION_FAILED"
STATUS_TEMPORAL_COVERAGE = "PHASE0A_TEMPORAL_COVERAGE_BLOCKED"
STATUS_INVALID_SINGLE_SYMBOL_FILE = "PHASE0A_ERROR_INVALID_ARCHIVE_PATH_SINGLE_SYMBOL_FILE"
STATUS_INTERNAL_CONSISTENCY = "PHASE0A_ERROR_INTERNAL_CONSISTENCY"

MAX_QUARTER_EVENT_SHARE = 0.45
MAX_MONTH_EVENT_SHARE = 0.20
MIN_DISTINCT_QUARTERS = 4
MIN_DISTINCT_MONTHS = 9

ALLOWED_STATUSES = {
    ORIGINAL_READY,
    STATUS_UNDERPOWERED,
    STATUS_INSUFFICIENT_COVERAGE,
    STATUS_SYMBOL_CONCENTRATION,
    STATUS_QUARTER_CONCENTRATION,
    STATUS_MONTH_CONCENTRATION,
    STATUS_TEMPORAL_COVERAGE,
    STATUS_INVALID_INPUT,
    STATUS_INVALID_PRECOMMITMENT,
    STATUS_INVALID_SINGLE_SYMBOL_FILE,
    STATUS_INTERNAL_CONSISTENCY,
}


def is_altcoin_symbol(symbol: str) -> bool:
    return symbol.strip().upper() not in ALTCOIN_EXCLUDED_SYMBOLS


def _filter_altcoin_rows(rows: Sequence[ArchiveRow]) -> list[ArchiveRow]:
    return [row for row in rows if is_altcoin_symbol(row.symbol)]


def _quarter_key(ts: datetime) -> str:
    return f"{ts.year}Q{((ts.month - 1) // 3) + 1}"


def _month_key(ts: datetime) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def _event_timestamp(event: EventRecord) -> datetime:
    return datetime.fromisoformat(event.event_timestamp_utc.replace("Z", "+00:00"))


def compute_quarter_month_distributions(events: Sequence[EventRecord]) -> tuple[dict[str, int], dict[str, int]]:
    quarters: Counter[str] = Counter()
    months: Counter[str] = Counter()
    for event in events:
        ts = _event_timestamp(event)
        quarters[_quarter_key(ts)] += 1
        months[_month_key(ts)] += 1
    return dict(sorted(quarters.items())), dict(sorted(months.items()))


def _max_distribution_share(distribution: dict[str, int], total: int) -> tuple[float, str | None]:
    if not distribution or total <= 0:
        return 0.0, None
    bucket, count = max(distribution.items(), key=lambda kv: (kv[1], kv[0]))
    return count / total, bucket


def validate_distribution_consistency(summary: dict[str, Any]) -> tuple[str | None, str]:
    accepted = summary.get("accepted_event_count_after_cooldown")
    quarter_distribution = summary.get("quarter_distribution")
    month_distribution = summary.get("month_distribution")
    if accepted is None:
        return None, ""
    if not isinstance(quarter_distribution, dict):
        return STATUS_INTERNAL_CONSISTENCY, "quarter_distribution missing or not a dict"
    if not isinstance(month_distribution, dict):
        return STATUS_INTERNAL_CONSISTENCY, "month_distribution missing or not a dict"
    try:
        q_sum = sum(int(v) for v in quarter_distribution.values())
        m_sum = sum(int(v) for v in month_distribution.values())
    except Exception:
        return STATUS_INTERNAL_CONSISTENCY, "distribution contains non-integer counts"
    if q_sum != accepted:
        return STATUS_INTERNAL_CONSISTENCY, f"quarter_distribution sum {q_sum} != accepted_event_count_after_cooldown {accepted}"
    if m_sum != accepted:
        return STATUS_INTERNAL_CONSISTENCY, f"month_distribution sum {m_sum} != accepted_event_count_after_cooldown {accepted}"
    q_nonzero = sum(1 for v in quarter_distribution.values() if int(v) > 0)
    m_nonzero = sum(1 for v in month_distribution.values() if int(v) > 0)
    if summary.get("distinct_quarters_with_events") != q_nonzero:
        return STATUS_INTERNAL_CONSISTENCY, "distinct_quarters_with_events does not match nonzero quarter buckets"
    if summary.get("distinct_months_with_events") != m_nonzero:
        return STATUS_INTERNAL_CONSISTENCY, "distinct_months_with_events does not match nonzero month buckets"
    return None, ""


def validate_archive_paths(archive_paths: Sequence[Path]) -> tuple[str | None, str]:
    if not archive_paths:
        return STATUS_INSUFFICIENT_COVERAGE, "no archive paths supplied"
    for path in archive_paths:
        if path.is_file():
            return STATUS_INVALID_SINGLE_SYMBOL_FILE, f"single-symbol archive files are invalid for venue-age-aware Phase 0A: {path}"
        if path.suffix.lower() in {".jsonl", ".json", ".csv", ".parquet"}:
            return STATUS_INVALID_SINGLE_SYMBOL_FILE, f"single-symbol archive files are invalid for venue-age-aware Phase 0A: {path}"
    return None, ""


def discover_archive_paths(repo_root: Path) -> list[Path]:
    active = repo_root / ACTIVE_ARCHIVE_PATH
    if active.is_dir():
        return [active]
    return original_discover_archive_paths(repo_root)


def _empty_result(
    status: str,
    locked_reason: str,
    precommitment_path: Path,
    generated_at: datetime,
    repo_root: Path,
    archive_source_path: str,
    archive_backfill_invoked: bool,
) -> Phase0AResult:
    try:
        pre_hash = compute_precommitment_hash(precommitment_path)
    except Exception:
        pre_hash = ""
    git_sha, git_dirty = git_metadata(repo_root)
    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": status,
        "unlocks_phase0b": False,
        "phase0b_locked_reason": locked_reason,
        "event_window_hours": 8,
        "cooldown_hours": COOLDOWN_HOURS,
        "price_return_threshold_pct": PRICE_RETURN_THRESHOLD_PCT,
        "oi_change_threshold_pct": OI_CHANGE_THRESHOLD_PCT,
        "total_symbols_requested": 0,
        "total_symbols_accepted": 0,
        "total_symbols_rejected": 0,
        "accepted_event_count_before_cooldown": 0,
        "accepted_event_count_after_cooldown": 0,
        "symbols_with_at_least_3_events": 0,
        "max_symbol_event_share": 0.0,
        "max_symbol_event_share_symbol": "",
        "max_calendar_quarter_event_share": 0.0,
        "max_calendar_quarter": None,
        "max_calendar_month_event_share": 0.0,
        "max_calendar_month": None,
        "distinct_months_with_events": 0,
        "distinct_quarters_with_events": 0,
        "quarter_distribution": {},
        "month_distribution": {},
        "archive_start_utc": None,
        "archive_end_utc": None,
        "archive_end_age_days": None,
        "generated_at_utc": utc_iso(generated_at),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "precommitment_sha256": pre_hash,
        "archive_source_path": archive_source_path,
        "archive_backfill_invoked": archive_backfill_invoked,
        "data_source_summary": {},
        "safety_mode": SAFETY_MODE,
        "venue_age_aware_precommitment_scope": "separate diagnostic precommitment, not a retroactive replacement for original Phase 0A",
    }
    return Phase0AResult(summary, [], [], [], [], [], [locked_reason])


def _event_stats(events: Sequence[EventRecord]) -> tuple[int, float, str, int]:
    count = len(events)
    if count == 0:
        return 0, 0.0, "", 0
    by_symbol: dict[str, int] = {}
    for event in events:
        by_symbol[event.symbol] = by_symbol.get(event.symbol, 0) + 1
    max_symbol, max_symbol_count = max(by_symbol.items(), key=lambda kv: (kv[1], kv[0]))
    symbols_with_3 = sum(1 for n in by_symbol.values() if n >= 3)
    return count, max_symbol_count / count, max_symbol, symbols_with_3


def _determine_venue_age_status(
    clean_symbol_count: int,
    event_count: int,
    symbols_with_3: int,
    max_symbol_share: float,
    max_quarter_share: float,
    max_month_share: float,
    distinct_quarters: int,
    distinct_months: int,
    invalid_input: bool,
    invalid_precommitment: bool,
    missing_required_fields: bool,
) -> tuple[str, str]:
    base_status = determine_status(
        clean_symbol_count,
        event_count,
        symbols_with_3,
        max_symbol_share,
        0.0,
        invalid_input,
        invalid_precommitment,
        missing_required_fields,
    )
    if base_status == STATUS_INVALID_PRECOMMITMENT:
        return base_status, "invalid precommitment self-check or frozen constants"
    if base_status == STATUS_INVALID_INPUT:
        return base_status, "invalid input archive"
    if base_status == STATUS_INSUFFICIENT_COVERAGE:
        return base_status, "fewer than 8 altcoin symbols with clean usable coverage or missing required archive fields"
    if base_status == STATUS_UNDERPOWERED:
        return base_status, "accepted altcoin event population below 100 after cooldown or fewer than 8 altcoin symbols with at least 3 events"
    if base_status == STATUS_SYMBOL_CONCENTRATION:
        return base_status, "single altcoin symbol contributed more than 30% of accepted events"
    if max_quarter_share > MAX_QUARTER_EVENT_SHARE:
        return STATUS_QUARTER_CONCENTRATION, f"max_calendar_quarter_event_share {max_quarter_share:.3f} exceeds threshold {MAX_QUARTER_EVENT_SHARE:.2f}"
    if max_month_share > MAX_MONTH_EVENT_SHARE:
        return STATUS_MONTH_CONCENTRATION, f"max_calendar_month_event_share {max_month_share:.3f} exceeds threshold {MAX_MONTH_EVENT_SHARE:.2f}"
    if distinct_quarters < MIN_DISTINCT_QUARTERS or distinct_months < MIN_DISTINCT_MONTHS:
        return STATUS_TEMPORAL_COVERAGE, f"insufficient distinct months ({distinct_months}) or quarters ({distinct_quarters}) with accepted altcoin events"
    return ORIGINAL_READY, ""


def run_phase0a_audit(
    rows: Sequence[ArchiveRow],
    diagnostics: LoadDiagnostics,
    precommitment_path: Path,
    generated_at: datetime | None = None,
    repo_root: Path | None = None,
    archive_source_path: str = "",
    archive_backfill_invoked: bool = False,
) -> Phase0AResult:
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import validate_precommitment
    from datetime import UTC

    generated_at = generated_at or datetime.now(UTC)
    repo_root = repo_root or Path.cwd()
    pre_ok, pre_hash, pre_warnings = validate_precommitment(precommitment_path)
    invalid_precommitment = not pre_ok
    invalid_input = False
    missing_required_fields = False
    if diagnostics.total_raw_rows == 0 and len(rows) == 0:
        invalid_input = True
    if diagnostics.total_raw_rows > 0 and diagnostics.field_validation_failures / diagnostics.total_raw_rows > 0.50:
        invalid_input = True
    if diagnostics.total_raw_rows > 0 and diagnostics.timestamp_parse_failures == diagnostics.total_raw_rows:
        invalid_input = True
    if diagnostics.missing_required_field_rows and not invalid_input:
        missing_required_fields = True

    alt_rows = _filter_altcoin_rows(rows)
    rows_by_symbol: dict[str, list[ArchiveRow]] = {}
    for row in alt_rows:
        rows_by_symbol.setdefault(row.symbol, []).append(row)

    coverage: list[SymbolAudit] = []
    points_by_symbol: dict[str, list[WindowPoint]] = {}
    accepted_before_all: list[WindowPoint] = []
    accepted_after_all: list[EventRecord] = []
    warnings = list(pre_warnings)
    excluded_counts = {sym: sum(1 for row in rows if row.symbol == sym) for sym in sorted(ALTCOIN_EXCLUDED_SYMBOLS)}
    archive_times = [r.timestamp for r in rows]

    for symbol in sorted(rows_by_symbol):
        deduped, audit = dedupe_symbol_rows(rows_by_symbol[symbol])
        audit = validate_per_symbol_coverage(deduped, audit)
        warnings.extend(audit.warnings)
        if audit.status == "accepted":
            points, stale = compute_past_8h_points(deduped)
            audit.candidate_points = len(points)
            if stale:
                audit.warnings.append(f"{symbol} stale 8h alignments excluded: {stale}")
            candidates = generate_candidate_events(points, PRICE_RETURN_THRESHOLD_PCT, OI_CHANGE_THRESHOLD_PCT)
            events = apply_cooldown(candidates)
            audit.accepted_events_before_cooldown = len(candidates)
            audit.accepted_events_after_cooldown = len(events)
            points_by_symbol[symbol] = points
            accepted_before_all.extend(candidates)
            accepted_after_all.extend(events)
        coverage.append(audit)

    clean_symbols = sum(1 for a in coverage if a.status == "accepted")
    event_count, max_symbol_share, max_symbol_symbol, symbols_with_3 = _event_stats(accepted_after_all)
    quarter_distribution, month_distribution = compute_quarter_month_distributions(accepted_after_all)
    max_quarter_share, max_quarter = _max_distribution_share(quarter_distribution, event_count)
    max_month_share, max_month = _max_distribution_share(month_distribution, event_count)
    distinct_quarters = sum(1 for v in quarter_distribution.values() if v > 0)
    distinct_months = sum(1 for v in month_distribution.values() if v > 0)
    status, locked_reason = _determine_venue_age_status(
        clean_symbols,
        event_count,
        symbols_with_3,
        max_symbol_share,
        max_quarter_share,
        max_month_share,
        distinct_quarters,
        distinct_months,
        invalid_input,
        invalid_precommitment,
        missing_required_fields,
    )
    unlocks = status == ORIGINAL_READY
    archive_start = min(archive_times) if archive_times else None
    archive_end = max(archive_times) if archive_times else None
    archive_end_age_days = (generated_at - archive_end).days if archive_end else None
    git_sha, git_dirty = git_metadata(repo_root)
    dist = compute_distribution_diagnostics(points_by_symbol)
    data_source_summary = {
        "total_raw_rows": diagnostics.total_raw_rows,
        "loaded_rows": diagnostics.loaded_rows,
        "field_validation_failures": diagnostics.field_validation_failures,
        "timestamp_parse_failures": diagnostics.timestamp_parse_failures,
        "distribution_diagnostics": dist,
        "excluded_symbol_raw_rows": excluded_counts,
        "altcoin_rows_loaded_for_gates": len(alt_rows),
    }
    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": status,
        "unlocks_phase0b": unlocks,
        "phase0b_locked_reason": locked_reason,
        "event_window_hours": 8,
        "cooldown_hours": COOLDOWN_HOURS,
        "price_return_threshold_pct": PRICE_RETURN_THRESHOLD_PCT,
        "oi_change_threshold_pct": OI_CHANGE_THRESHOLD_PCT,
        "total_symbols_requested": len(rows_by_symbol),
        "total_symbols_accepted": clean_symbols,
        "total_symbols_rejected": len(coverage) - clean_symbols,
        "accepted_event_count_before_cooldown": len(accepted_before_all),
        "accepted_event_count_after_cooldown": event_count,
        "symbols_with_at_least_3_events": symbols_with_3,
        "max_symbol_event_share": max_symbol_share,
        "max_symbol_event_share_symbol": max_symbol_symbol,
        "max_calendar_quarter_event_share": max_quarter_share,
        "max_calendar_quarter": max_quarter,
        "max_calendar_month_event_share": max_month_share,
        "max_calendar_month": max_month,
        "distinct_months_with_events": distinct_months,
        "distinct_quarters_with_events": distinct_quarters,
        "quarter_distribution": quarter_distribution,
        "month_distribution": month_distribution,
        "archive_start_utc": utc_iso(archive_start) if archive_start else None,
        "archive_end_utc": utc_iso(archive_end) if archive_end else None,
        "archive_end_age_days": archive_end_age_days,
        "generated_at_utc": utc_iso(generated_at),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "precommitment_sha256": pre_hash,
        "archive_source_path": archive_source_path,
        "archive_backfill_invoked": archive_backfill_invoked,
        "data_source_summary": data_source_summary,
        "safety_mode": SAFETY_MODE,
        "venue_age_aware_precommitment_scope": "separate diagnostic precommitment, not a retroactive replacement for original Phase 0A",
    }
    consistency_status, consistency_reason = validate_distribution_consistency(summary)
    if consistency_status is not None:
        summary["status"] = consistency_status
        summary["unlocks_phase0b"] = False
        summary["phase0b_locked_reason"] = consistency_reason
    if summary.get("max_symbol_event_share_symbol") in ALTCOIN_EXCLUDED_SYMBOLS:
        summary["status"] = STATUS_INTERNAL_CONSISTENCY
        summary["unlocks_phase0b"] = False
        summary["phase0b_locked_reason"] = "BTC/ETH appeared in altcoin symbol concentration output"
    rejected = [
        {
            "symbol": a.symbol,
            "rejection_reason": a.rejection_reason,
            "first_timestamp_utc": a.first_timestamp_utc,
            "last_timestamp_utc": a.last_timestamp_utc,
            "usable_months": a.usable_months,
            "row_count": a.row_count,
        }
        for a in coverage
        if a.status != "accepted"
    ]
    return Phase0AResult(summary, accepted_after_all, coverage, rejected, [], compute_threshold_diagnostics(points_by_symbol), warnings)


def run_from_archive_paths(
    archive_paths: Sequence[Path],
    precommitment_path: Path,
    repo_root: Path,
    out_dir: Path | None = None,
    archive_backfill_invoked: bool = False,
) -> Phase0AResult:
    from datetime import UTC

    path_status, path_reason = validate_archive_paths(archive_paths)
    archive_source_path = ":".join(str(p) for p in archive_paths)
    if path_status is not None:
        result = _empty_result(path_status, path_reason, precommitment_path, datetime.now(UTC), repo_root, archive_source_path, archive_backfill_invoked)
    else:
        rows, diagnostics = load_archive_rows(archive_paths)
        result = run_phase0a_audit(
            rows,
            diagnostics,
            precommitment_path,
            repo_root=repo_root,
            archive_source_path=archive_source_path,
            archive_backfill_invoked=archive_backfill_invoked,
        )
    if out_dir is not None:
        result = write_report_artifacts(result, out_dir)
    return result


def summary_markdown(result: Phase0AResult) -> str:
    s = result.summary
    warnings = "\n".join(f"- {w}" for w in result.warnings) if result.warnings else "- none"
    return f"""# Liquidation flush aftershock reversal venue-age-aware Phase 0A summary

Status: `{s['status']}`

Unlocks Phase 0B: `{s['unlocks_phase0b']}`

Locked reason: {s['phase0b_locked_reason'] or 'none'}

Precommitment SHA-256: `{s['precommitment_sha256']}`

Archive source path: `{s['archive_source_path']}`

Accepted altcoin events after cooldown: {s['accepted_event_count_after_cooldown']}

Max symbol event share: {s['max_symbol_event_share']} ({s['max_symbol_event_share_symbol']})

Max calendar quarter event share: {s['max_calendar_quarter_event_share']} ({s['max_calendar_quarter']})

Max calendar month event share: {s['max_calendar_month_event_share']} ({s['max_calendar_month']})

## Scope

This is venue-age-aware Phase 0A under a separate diagnostic precommitment. It is not a retroactive replacement for the original calendar-year Phase 0A. If ready, it can only unlock Phase 0B in principle under the venue-age-aware diagnostic precommitment. No returns, PnL, null tests, FDR, live execution, paper trading, shadow execution, orders, private keys, or trading auth were used.

## Distribution consistency

Quarter distribution: `{s['quarter_distribution']}`

Month distribution: `{s['month_distribution']}`

## Diagnostic warnings

{warnings}
"""


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def write_report_artifacts(result: Phase0AResult, report_dir: Path) -> Phase0AResult:
    from examples.strategies.venue_agnostic_signal_observer.run_artifacts import atomic_write_json, atomic_write_text

    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", result.summary)
    atomic_write_text(report_dir / "summary.md", summary_markdown(result))
    _write_csv(report_dir / "accepted_events.csv", ["event_id", "symbol", "event_timestamp_utc", "price_t", "price_t_minus_8h", "oi_t", "oi_t_minus_8h", "price_return_8h_pct", "oi_change_8h_pct", "cooldown_group_index", "calendar_year"], [asdict(e) for e in result.accepted_events])
    _write_csv(report_dir / "symbol_coverage.csv", ["symbol", "status", "first_timestamp_utc", "last_timestamp_utc", "usable_months", "row_count", "invalid_price_rows", "invalid_oi_rows", "duplicate_timestamp_rows", "non_monotonic_detected", "candidate_points", "accepted_events_before_cooldown", "accepted_events_after_cooldown", "rejection_reason"], [asdict(a) for a in result.symbol_coverage])
    _write_csv(report_dir / "rejected_symbols.csv", ["symbol", "rejection_reason", "first_timestamp_utc", "last_timestamp_utc", "usable_months", "row_count"], result.rejected_symbols)
    _write_csv(report_dir / "threshold_diagnostics.csv", ["threshold_label", "price_threshold_pct", "oi_threshold_pct", "threshold_type", "accepted_events_before_cooldown", "accepted_events_after_cooldown", "symbols_with_at_least_3_events", "max_symbol_event_share", "max_calendar_year_event_share", "unlocks_phase0b_candidate"], result.threshold_diagnostics)
    result.report_dir = str(report_dir)
    return result


__all__ = [
    "ACTIVE_ARCHIVE_PATH",
    "ALTCOIN_EXCLUDED_SYMBOLS",
    "ArchiveRow",
    "LoadDiagnostics",
    "MAX_MONTH_EVENT_SHARE",
    "MAX_QUARTER_EVENT_SHARE",
    "ORIGINAL_READY",
    "Phase0AResult",
    "STATUS_INTERNAL_CONSISTENCY",
    "STATUS_INVALID_INPUT",
    "STATUS_INVALID_PRECOMMITMENT",
    "STATUS_INVALID_SINGLE_SYMBOL_FILE",
    "STATUS_MONTH_CONCENTRATION",
    "STATUS_QUARTER_CONCENTRATION",
    "STATUS_TEMPORAL_COVERAGE",
    "compute_quarter_month_distributions",
    "discover_archive_paths",
    "load_archive_rows",
    "run_from_archive_paths",
    "run_phase0a_audit",
    "validate_archive_paths",
    "validate_distribution_consistency",
    "write_report_artifacts",
]
