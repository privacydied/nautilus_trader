"""Archive-only Phase 0A event-population audit for liquidation flush aftershock reversal v0."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # optional project dependency; tests do not require it
    import pyarrow.parquet as pq  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - depends on local environment
    pq = None

from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
)

STUDY_ID = "liquidation_flush_aftershock_reversal_v0"
STAGE = "phase0a_event_population_audit"
VENUE = "hyperliquid"
SAFETY_MODE = "public_data_observer_only"

EVENT_WINDOW_HOURS = 8
PRICE_RETURN_THRESHOLD_PCT = -8.0
OI_CHANGE_THRESHOLD_PCT = -8.0
COOLDOWN_HOURS = 72
MIN_COVERAGE_MONTHS = 9.0
MIN_ACCEPTED_SYMBOLS = 8
MIN_ACCEPTED_EVENTS = 100
MIN_SYMBOLS_WITH_3_EVENTS = 8
MAX_SYMBOL_EVENT_SHARE = 0.30
MAX_CALENDAR_YEAR_EVENT_SHARE = 0.45
MAX_ALIGNMENT_TOLERANCE_MINUTES = 65

STATUS_READY = "PHASE0A_EVENT_POPULATION_READY"
STATUS_INSUFFICIENT_COVERAGE = "PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE"
STATUS_UNDERPOWERED = "NEEDS_MORE_DATA_UNDERPOWERED_EVENT_POPULATION"
STATUS_SYMBOL_CONCENTRATION = "PHASE0A_SYMBOL_CONCENTRATION_FAILED"
STATUS_YEAR_CONCENTRATION = "PHASE0A_YEAR_CONCENTRATION_FAILED"
STATUS_INVALID_INPUT = "PHASE0A_ERROR_INVALID_INPUT"
STATUS_INVALID_PRECOMMITMENT = "PHASE0A_ERROR_INVALID_PRECOMMITMENT"

ALLOWED_STATUSES = {
    STATUS_READY,
    STATUS_INSUFFICIENT_COVERAGE,
    STATUS_UNDERPOWERED,
    STATUS_SYMBOL_CONCENTRATION,
    STATUS_YEAR_CONCENTRATION,
    STATUS_INVALID_INPUT,
    STATUS_INVALID_PRECOMMITMENT,
}

DOC_EXPECTED_VALUES = {
    "event_window_hours": EVENT_WINDOW_HOURS,
    "price_return_threshold_pct": PRICE_RETURN_THRESHOLD_PCT,
    "oi_change_threshold_pct": OI_CHANGE_THRESHOLD_PCT,
    "cooldown_hours": COOLDOWN_HOURS,
    "minimum_events": MIN_ACCEPTED_EVENTS,
    "minimum_symbols": MIN_ACCEPTED_SYMBOLS,
    "max_symbol_share": MAX_SYMBOL_EVENT_SHARE,
    "max_year_share": MAX_CALENDAR_YEAR_EVENT_SHARE,
}

TIMESTAMP_FIELDS = ("timestamp", "timestamp_utc", "ts_event", "ts", "time", "datetime", "date")
SYMBOL_FIELDS = ("symbol", "coin", "asset")
PRICE_FIELDS = ("mark_price", "markPx", "mark_price", "markPrice", "mark", "price", "midPx", "lastPrice", "close")
OI_FIELDS = ("open_interest", "openInterest", "oi", "open_interest_usd")

DISCOVERY_PATHS = (
    Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid"),
    Path("data/hyperliquid_archive"),
    Path("reports/hyperliquid_oi_velocity_compression_phase0"),
)


@dataclass(frozen=True)
class ArchiveRow:
    timestamp: datetime
    symbol: str
    price: float
    open_interest: float
    source_path: str
    file_order: int


@dataclass(frozen=True)
class WindowPoint:
    timestamp: datetime
    symbol: str
    price_t: float
    price_t_minus_8h: float
    oi_t: float
    oi_t_minus_8h: float
    price_return_8h_pct: float
    oi_change_8h_pct: float


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    symbol: str
    event_timestamp_utc: str
    price_t: float
    price_t_minus_8h: float
    oi_t: float
    oi_t_minus_8h: float
    price_return_8h_pct: float
    oi_change_8h_pct: float
    cooldown_group_index: int
    calendar_year: int


@dataclass
class SymbolAudit:
    symbol: str
    status: str = "rejected"
    first_timestamp_utc: str = ""
    last_timestamp_utc: str = ""
    usable_months: float = 0.0
    row_count: int = 0
    invalid_price_rows: int = 0
    invalid_oi_rows: int = 0
    duplicate_timestamp_rows: int = 0
    non_monotonic_detected: bool = False
    candidate_points: int = 0
    accepted_events_before_cooldown: int = 0
    accepted_events_after_cooldown: int = 0
    rejection_reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class LoadDiagnostics:
    total_raw_rows: int = 0
    loaded_rows: int = 0
    field_validation_failures: int = 0
    timestamp_parse_failures: int = 0
    missing_required_field_rows: int = 0
    source_paths: list[str] = field(default_factory=list)


@dataclass
class Phase0AResult:
    summary: dict[str, Any]
    accepted_events: list[EventRecord]
    symbol_coverage: list[SymbolAudit]
    rejected_symbols: list[dict[str, Any]]
    year_distribution: list[dict[str, Any]]
    threshold_diagnostics: list[dict[str, Any]]
    warnings: list[str]
    report_dir: str | None = None


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime:
    if value is None or value == "":
        raise ValueError("missing timestamp")
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite timestamp")
        if number > 1e18:
            return datetime.fromtimestamp(number / 1e9, tz=UTC)
        if number > 1e15:
            return datetime.fromtimestamp(number / 1e6, tz=UTC)
        if number > 1e12:
            return datetime.fromtimestamp(number / 1e3, tz=UTC)
        return datetime.fromtimestamp(number, tz=UTC)
    text = str(value).strip()
    if text.isdigit():
        return parse_timestamp(int(text))
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


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


def normalize_raw_row(raw: dict[str, Any], source_path: Path, file_order: int) -> tuple[ArchiveRow | None, str | None]:
    try:
        timestamp = parse_timestamp(_get_first(raw, TIMESTAMP_FIELDS))
    except Exception:
        return None, "timestamp"
    symbol = _get_first(raw, SYMBOL_FIELDS)
    if symbol is None or str(symbol).strip() == "":
        return None, "field"
    try:
        price = _finite_float(_get_first(raw, PRICE_FIELDS), "price")
        oi = _finite_float(_get_first(raw, OI_FIELDS), "open_interest")
    except Exception:
        return None, "field"
    if price <= 0:
        return None, "invalid_price"
    if oi < 0:
        return None, "invalid_oi"
    return ArchiveRow(timestamp, str(symbol).strip().upper(), price, oi, str(source_path), file_order), None


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
        return [dict(row) for row in payload]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    if pq is None:
        raise RuntimeError("pyarrow not available for parquet input")
    return pq.read_table(path).to_pylist()


def _candidate_archive_files(path: Path) -> list[Path]:
    suffixes = {".csv", ".jsonl", ".json", ".parquet"}
    def usable_file(p: Path) -> bool:
        lower_parts = {part.lower() for part in p.parts}
        lower_name = p.name.lower()
        if "l2book" in lower_parts:
            return False
        if p.suffix.lower() not in suffixes:
            return False
        if p.name.lower().endswith("manifest.json"):
            return False
        if p.suffix.lower() == ".parquet" and not any(token in lower_name or token in str(p.parent).lower() for token in ("asset", "ctx", "oi", "open_interest", "mark")):
            return False
        return True
    if path.is_file():
        return [path] if usable_file(path) else []
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if usable_file(p))


def load_archive_rows(paths: Sequence[Path]) -> tuple[list[ArchiveRow], LoadDiagnostics]:
    diagnostics = LoadDiagnostics(source_paths=[str(p) for p in paths])
    rows: list[ArchiveRow] = []
    order = 0
    files: list[Path] = []
    for path in paths:
        files.extend(_candidate_archive_files(path))
    for file_path in files:
        try:
            if file_path.suffix.lower() == ".csv":
                raw_rows = _read_csv_rows(file_path)
            elif file_path.suffix.lower() in {".jsonl", ".json"}:
                raw_rows = _read_json_rows(file_path)
            elif file_path.suffix.lower() == ".parquet":
                raw_rows = _read_parquet_rows(file_path)
            else:
                continue
        except Exception:
            continue
        for raw in raw_rows:
            diagnostics.total_raw_rows += 1
            row, reason = normalize_raw_row(dict(raw), file_path, order)
            order += 1
            if row is None:
                diagnostics.field_validation_failures += 1
                if reason == "timestamp":
                    diagnostics.timestamp_parse_failures += 1
                if reason == "field":
                    diagnostics.missing_required_field_rows += 1
                continue
            diagnostics.loaded_rows += 1
            rows.append(row)
    return rows, diagnostics


def precommitment_hash_bytes(data: bytes) -> str:
    lines = data.splitlines(keepends=True)
    removed = False
    kept: list[bytes] = []
    for line in lines:
        if not removed and line.startswith(b"Precommitment SHA-256 (self):"):
            removed = True
            continue
        kept.append(line)
    if not removed:
        raise ValueError("precommitment self-hash line missing")
    return hashlib.sha256(b"".join(kept)).hexdigest()


def read_precommitment_hash(precommitment_path: Path) -> str:
    for line in precommitment_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Precommitment SHA-256 (self):"):
            return line.split(":", 1)[1].strip()
    raise ValueError("precommitment hash line missing")


def compute_precommitment_hash(precommitment_path: Path) -> str:
    return precommitment_hash_bytes(precommitment_path.read_bytes())


def validate_precommitment(precommitment_path: Path) -> tuple[bool, str, list[str]]:
    warnings: list[str] = []
    try:
        actual = compute_precommitment_hash(precommitment_path)
        recorded = read_precommitment_hash(precommitment_path)
    except Exception as exc:
        return False, "", [f"precommitment hash read failed: {exc}"]
    if actual != recorded:
        return False, actual, [f"precommitment hash mismatch actual={actual} recorded={recorded}"]
    text = precommitment_path.read_text(encoding="utf-8")
    required_snippets = [
        "Event window: 8h",
        "Primary price threshold: 8h price return <= -8.0%",
        "Primary OI threshold: 8h open-interest change <= -8.0%",
        "Cooldown: 72h",
        "Minimum accepted event population: at least 100",
        "Minimum accepted symbols: at least 8",
        "more than 30%",
        "more than 45%",
    ]
    missing = [s for s in required_snippets if s not in text]
    if missing:
        return False, actual, [f"precommitment missing frozen value: {s}" for s in missing]
    return True, actual, warnings


def dedupe_symbol_rows(rows: Sequence[ArchiveRow]) -> tuple[list[ArchiveRow], SymbolAudit]:
    if not rows:
        return [], SymbolAudit(symbol="")
    symbol = rows[0].symbol
    audit = SymbolAudit(symbol=symbol)
    by_ts: dict[datetime, ArchiveRow] = {}
    seen_values: dict[datetime, list[ArchiveRow]] = {}
    last_ts_by_file: datetime | None = None
    for row in rows:
        if last_ts_by_file is not None and row.timestamp < last_ts_by_file:
            audit.non_monotonic_detected = True
        last_ts_by_file = row.timestamp
        if row.timestamp in by_ts:
            audit.duplicate_timestamp_rows += 1
        seen_values.setdefault(row.timestamp, []).append(row)
        by_ts[row.timestamp] = row
    for ts, dupes in seen_values.items():
        if len(dupes) < 2:
            continue
        prices = [d.price for d in dupes]
        ois = [d.open_interest for d in dupes]
        if min(prices) > 0 and (max(prices) / min(prices) - 1.0) * 10000.0 > 5.0:
            audit.non_monotonic_detected = True
            audit.warnings.append(f"{symbol} duplicate price drift >5bps at {utc_iso(ts)}")
        if min(ois) > 0 and (max(ois) / min(ois) - 1.0) > 0.01:
            audit.non_monotonic_detected = True
            audit.warnings.append(f"{symbol} duplicate OI drift >1pct at {utc_iso(ts)}")
    out = sorted(by_ts.values(), key=lambda r: r.timestamp)
    audit.row_count = len(out)
    if out:
        audit.first_timestamp_utc = utc_iso(out[0].timestamp)
        audit.last_timestamp_utc = utc_iso(out[-1].timestamp)
        audit.usable_months = max(0.0, (out[-1].timestamp - out[0].timestamp).total_seconds() / (86400.0 * 30.4375))
    return out, audit


def validate_per_symbol_coverage(rows: Sequence[ArchiveRow], audit: SymbolAudit) -> SymbolAudit:
    audit.row_count = len(rows)
    invalid_price = sum(1 for r in rows if r.price <= 0 or not math.isfinite(r.price))
    invalid_oi = sum(1 for r in rows if r.open_interest < 0 or not math.isfinite(r.open_interest))
    audit.invalid_price_rows += invalid_price
    audit.invalid_oi_rows += invalid_oi
    if not rows:
        audit.status = "rejected"
        audit.rejection_reason = "no_usable_rows"
    elif audit.usable_months < MIN_COVERAGE_MONTHS:
        audit.status = "rejected"
        audit.rejection_reason = "coverage_less_than_9_months"
    elif invalid_price or invalid_oi:
        audit.status = "rejected"
        audit.rejection_reason = "invalid_price_or_oi"
    else:
        audit.status = "accepted"
        audit.rejection_reason = ""
    return audit


def compute_past_8h_points(rows: Sequence[ArchiveRow], tolerance: timedelta = timedelta(minutes=MAX_ALIGNMENT_TOLERANCE_MINUTES)) -> tuple[list[WindowPoint], int]:
    points: list[WindowPoint] = []
    stale_alignment_count = 0
    j = 0
    window = timedelta(hours=EVENT_WINDOW_HOURS)
    for i, row in enumerate(rows):
        target = row.timestamp - window
        while j + 1 < i and rows[j + 1].timestamp <= target:
            j += 1
        if j < i and rows[j].timestamp <= target:
            prior = rows[j]
            if target - prior.timestamp <= tolerance:
                if prior.price > 0 and prior.open_interest >= 0:
                    price_ret = 100.0 * (row.price / prior.price - 1.0)
                    oi_ret = math.inf if prior.open_interest == 0 else 100.0 * (row.open_interest / prior.open_interest - 1.0)
                    if math.isfinite(price_ret) and math.isfinite(oi_ret):
                        points.append(WindowPoint(row.timestamp, row.symbol, row.price, prior.price, row.open_interest, prior.open_interest, price_ret, oi_ret))
            else:
                stale_alignment_count += 1
    return points, stale_alignment_count


def generate_candidate_events(points: Sequence[WindowPoint], price_threshold: float, oi_threshold: float) -> list[WindowPoint]:
    return [p for p in points if p.price_return_8h_pct <= price_threshold and p.oi_change_8h_pct <= oi_threshold]


def apply_cooldown(candidates: Sequence[WindowPoint]) -> list[EventRecord]:
    accepted: list[EventRecord] = []
    cooldown_until: datetime | None = None
    group = 0
    for point in sorted(candidates, key=lambda p: p.timestamp):
        if cooldown_until is not None and point.timestamp <= cooldown_until:
            continue
        group += 1
        event_id = f"{point.symbol}_{point.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        accepted.append(EventRecord(event_id, point.symbol, utc_iso(point.timestamp), point.price_t, point.price_t_minus_8h, point.oi_t, point.oi_t_minus_8h, point.price_return_8h_pct, point.oi_change_8h_pct, group, point.timestamp.year))
        cooldown_until = point.timestamp + timedelta(hours=COOLDOWN_HOURS)
    return accepted


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * pct / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[int(rank)]
    return vals[lo] * (hi - rank) + vals[hi] * (rank - lo)


def compute_distribution_diagnostics(points_by_symbol: dict[str, list[WindowPoint]]) -> dict[str, Any]:
    out: dict[str, Any] = {"by_symbol": {}, "pooled": {}}
    pooled_returns: list[float] = []
    pooled_oi: list[float] = []
    for symbol, points in points_by_symbol.items():
        returns = [p.price_return_8h_pct for p in points]
        oi_changes = [p.oi_change_8h_pct for p in points]
        pooled_returns.extend(returns)
        pooled_oi.extend(oi_changes)
        out["by_symbol"][symbol] = {
            "return_pctiles": {str(p): _percentile(returns, p) for p in (1, 2.5, 5, 10, 50)},
            "oi_pctiles": {str(p): _percentile(oi_changes, p) for p in (1, 2.5, 5, 10, 50)},
        }
    out["pooled"] = {
        "return_pctiles": {str(p): _percentile(pooled_returns, p) for p in (1, 2.5, 5, 10, 50)},
        "oi_pctiles": {str(p): _percentile(pooled_oi, p) for p in (1, 2.5, 5, 10, 50)},
    }
    return out


def _event_stats(events: Sequence[EventRecord]) -> tuple[int, float, str, float, int | None, int]:
    count = len(events)
    if count == 0:
        return 0, 0.0, "", 0.0, None, 0
    by_symbol: dict[str, int] = {}
    by_year: dict[int, int] = {}
    for event in events:
        by_symbol[event.symbol] = by_symbol.get(event.symbol, 0) + 1
        by_year[event.calendar_year] = by_year.get(event.calendar_year, 0) + 1
    max_symbol, max_symbol_count = max(by_symbol.items(), key=lambda kv: kv[1])
    max_year, max_year_count = max(by_year.items(), key=lambda kv: kv[1])
    symbols_with_3 = sum(1 for n in by_symbol.values() if n >= 3)
    return count, max_symbol_count / count, max_symbol, max_year_count / count, max_year, symbols_with_3


def _threshold_row(label: str, threshold_type: str, points_by_symbol: dict[str, list[WindowPoint]], price_threshold: float | None, oi_threshold: float | None, pctile: float | None = None) -> dict[str, Any]:
    before: list[WindowPoint] = []
    after: list[EventRecord] = []
    for symbol, points in points_by_symbol.items():
        if pctile is not None:
            pthr = _percentile([p.price_return_8h_pct for p in points], pctile)
            othr = _percentile([p.oi_change_8h_pct for p in points], pctile)
        else:
            pthr = price_threshold
            othr = oi_threshold
        if pthr is None or othr is None:
            candidates: list[WindowPoint] = []
        else:
            candidates = generate_candidate_events(points, float(pthr), float(othr))
        before.extend(candidates)
        after.extend(apply_cooldown(candidates))
    _, max_sym_share, _, max_year_share, _, symbols_with_3 = _event_stats(after)
    is_primary = label == "primary_-8_price_-8_oi"
    return {
        "threshold_label": label,
        "price_threshold_pct": price_threshold if price_threshold is not None else f"bottom_{pctile}",
        "oi_threshold_pct": oi_threshold if oi_threshold is not None else f"bottom_{pctile}",
        "threshold_type": threshold_type,
        "accepted_events_before_cooldown": len(before),
        "accepted_events_after_cooldown": len(after),
        "symbols_with_at_least_3_events": symbols_with_3,
        "max_symbol_event_share": max_sym_share,
        "max_calendar_year_event_share": max_year_share,
        "unlocks_phase0b_candidate": bool(is_primary and len(after) >= MIN_ACCEPTED_EVENTS and symbols_with_3 >= MIN_SYMBOLS_WITH_3_EVENTS and max_sym_share <= MAX_SYMBOL_EVENT_SHARE and max_year_share <= MAX_CALENDAR_YEAR_EVENT_SHARE),
    }


def compute_threshold_diagnostics(points_by_symbol: dict[str, list[WindowPoint]]) -> list[dict[str, Any]]:
    return [
        _threshold_row("diagnostic_-5_price_-5_oi", "absolute", points_by_symbol, -5.0, -5.0),
        _threshold_row("primary_-8_price_-8_oi", "absolute", points_by_symbol, -8.0, -8.0),
        _threshold_row("diagnostic_-10_price_-10_oi", "absolute", points_by_symbol, -10.0, -10.0),
        _threshold_row("diagnostic_bottom_5pct_price_and_oi_by_symbol", "per_symbol_percentile", points_by_symbol, None, None, 5.0),
        _threshold_row("diagnostic_bottom_2_5pct_price_and_oi_by_symbol", "per_symbol_percentile", points_by_symbol, None, None, 2.5),
    ]


def compute_year_distribution(events: Sequence[EventRecord]) -> list[dict[str, Any]]:
    counts: dict[int, int] = {}
    for event in events:
        counts[event.calendar_year] = counts.get(event.calendar_year, 0) + 1
    total = len(events)
    return [{"calendar_year": year, "accepted_events_after_cooldown": count, "share": count / total if total else 0.0} for year, count in sorted(counts.items())]


def determine_status(clean_symbol_count: int, event_count: int, symbols_with_3: int, max_symbol_share: float, max_year_share: float, invalid_input: bool, invalid_precommitment: bool, missing_required_fields: bool = False) -> str:
    if invalid_precommitment:
        return STATUS_INVALID_PRECOMMITMENT
    if invalid_input:
        return STATUS_INVALID_INPUT
    if clean_symbol_count < MIN_ACCEPTED_SYMBOLS or missing_required_fields:
        return STATUS_INSUFFICIENT_COVERAGE
    if event_count < MIN_ACCEPTED_EVENTS or symbols_with_3 < MIN_SYMBOLS_WITH_3_EVENTS:
        return STATUS_UNDERPOWERED
    if max_symbol_share > MAX_SYMBOL_EVENT_SHARE:
        return STATUS_SYMBOL_CONCENTRATION
    if max_year_share > MAX_CALENDAR_YEAR_EVENT_SHARE:
        return STATUS_YEAR_CONCENTRATION
    return STATUS_READY


def git_metadata(repo_root: Path) -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_root, text=True)
        return sha, bool(status.strip())
    except Exception:
        return "", False


def discover_archive_paths(repo_root: Path) -> list[Path]:
    checked: list[Path] = []
    for rel in DISCOVERY_PATHS:
        root = repo_root / rel
        checked.append(root)
        if not root.exists():
            continue
        if rel.parts[:1] == ("reports",):
            children = sorted((p for p in root.glob("*") if p.is_dir()), key=lambda p: p.name)
            for child in children:
                if _candidate_archive_files(child):
                    return [child]
        elif _candidate_archive_files(root):
            return [root]
    return []


def run_phase0a_audit(rows: Sequence[ArchiveRow], diagnostics: LoadDiagnostics, precommitment_path: Path, generated_at: datetime | None = None, repo_root: Path | None = None, archive_source_path: str = "", archive_backfill_invoked: bool = False) -> Phase0AResult:
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

    rows_by_symbol: dict[str, list[ArchiveRow]] = {}
    for row in rows:
        rows_by_symbol.setdefault(row.symbol, []).append(row)

    coverage: list[SymbolAudit] = []
    points_by_symbol: dict[str, list[WindowPoint]] = {}
    accepted_before_all: list[WindowPoint] = []
    accepted_after_all: list[EventRecord] = []
    warnings = list(pre_warnings)
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
    event_count, max_symbol_share, max_symbol_symbol, max_year_share, max_year, symbols_with_3 = _event_stats(accepted_after_all)
    status = determine_status(clean_symbols, event_count, symbols_with_3, max_symbol_share, max_year_share, invalid_input, invalid_precommitment, missing_required_fields)
    unlocks = status == STATUS_READY
    if unlocks:
        locked_reason = ""
    elif status == STATUS_INVALID_INPUT:
        locked_reason = "invalid input archive"
    elif status == STATUS_INVALID_PRECOMMITMENT:
        locked_reason = "invalid precommitment self-check or frozen constants"
    elif status == STATUS_INSUFFICIENT_COVERAGE:
        locked_reason = "fewer than 8 symbols with clean usable coverage or missing required archive fields"
    elif status == STATUS_UNDERPOWERED:
        locked_reason = "accepted event population below 100 after cooldown or fewer than 8 symbols with at least 3 events"
    elif status == STATUS_SYMBOL_CONCENTRATION:
        locked_reason = "single symbol contributed more than 30% of accepted events"
    else:
        locked_reason = "single calendar year contributed more than 45% of accepted events"

    archive_start = min(archive_times) if archive_times else None
    archive_end = max(archive_times) if archive_times else None
    archive_end_age_days = (generated_at - archive_end).days if archive_end else None
    git_sha, git_dirty = git_metadata(repo_root)
    threshold_diags = compute_threshold_diagnostics(points_by_symbol)
    dist = compute_distribution_diagnostics(points_by_symbol)
    data_source_summary = {
        "total_raw_rows": diagnostics.total_raw_rows,
        "loaded_rows": diagnostics.loaded_rows,
        "field_validation_failures": diagnostics.field_validation_failures,
        "timestamp_parse_failures": diagnostics.timestamp_parse_failures,
        "distribution_diagnostics": dist,
    }
    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": status,
        "unlocks_phase0b": unlocks,
        "phase0b_locked_reason": locked_reason,
        "event_window_hours": EVENT_WINDOW_HOURS,
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
        "max_calendar_year_event_share": max_year_share,
        "max_calendar_year": max_year,
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
    }
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
    return Phase0AResult(summary, accepted_after_all, coverage, rejected, compute_year_distribution(accepted_after_all), threshold_diags, warnings)


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def summary_markdown(result: Phase0AResult) -> str:
    s = result.summary
    warnings = "\n".join(f"- {w}" for w in result.warnings) if result.warnings else "- none"
    return f"""# Liquidation flush aftershock reversal v0 Phase 0A summary

Status: `{s['status']}`

Unlocks Phase 0B: `{s['unlocks_phase0b']}`

Locked reason: {s['phase0b_locked_reason'] or 'none'}

Precommitment SHA-256: `{s['precommitment_sha256']}`

Archive source path: `{s['archive_source_path']}`

Archive backfill invoked: `{s['archive_backfill_invoked']}`

Archive start UTC: `{s['archive_start_utc']}`

Archive end UTC: `{s['archive_end_utc']}`

Archive end age days: `{s['archive_end_age_days']}`

Accepted symbols: {s['total_symbols_accepted']}

Accepted events before cooldown: {s['accepted_event_count_before_cooldown']}

Accepted events after cooldown: {s['accepted_event_count_after_cooldown']}

Symbols with at least 3 accepted events: {s['symbols_with_at_least_3_events']}

Max symbol event share: {s['max_symbol_event_share']} ({s['max_symbol_event_share_symbol']})

Max calendar year event share: {s['max_calendar_year_event_share']} ({s['max_calendar_year']})

## Scope

Archive-only Phase 0A event-population audit. No profitability, forward return, PnL, null, FDR, v1 authorization, live execution, paper trading, or trading authorization was performed.

## Freshness diagnostic

`archive_end_age_days` is diagnostic only and is not a Phase 0A gate. A later Phase 0B precommitment, if unlocked, must decide whether this age warrants a fresh public archive backfill before return evaluation.

## Diagnostic warnings

{warnings}
"""


def write_report_artifacts(result: Phase0AResult, report_dir: Path) -> Phase0AResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", result.summary)
    atomic_write_text(report_dir / "summary.md", summary_markdown(result))
    _write_csv(report_dir / "accepted_events.csv", ["event_id", "symbol", "event_timestamp_utc", "price_t", "price_t_minus_8h", "oi_t", "oi_t_minus_8h", "price_return_8h_pct", "oi_change_8h_pct", "cooldown_group_index", "calendar_year"], [asdict(e) for e in result.accepted_events])
    _write_csv(report_dir / "symbol_coverage.csv", ["symbol", "status", "first_timestamp_utc", "last_timestamp_utc", "usable_months", "row_count", "invalid_price_rows", "invalid_oi_rows", "duplicate_timestamp_rows", "non_monotonic_detected", "candidate_points", "accepted_events_before_cooldown", "accepted_events_after_cooldown", "rejection_reason"], [asdict(a) for a in result.symbol_coverage])
    _write_csv(report_dir / "rejected_symbols.csv", ["symbol", "rejection_reason", "first_timestamp_utc", "last_timestamp_utc", "usable_months", "row_count"], result.rejected_symbols)
    _write_csv(report_dir / "year_distribution.csv", ["calendar_year", "accepted_events_after_cooldown", "share"], result.year_distribution)
    _write_csv(report_dir / "threshold_diagnostics.csv", ["threshold_label", "price_threshold_pct", "oi_threshold_pct", "threshold_type", "accepted_events_before_cooldown", "accepted_events_after_cooldown", "symbols_with_at_least_3_events", "max_symbol_event_share", "max_calendar_year_event_share", "unlocks_phase0b_candidate"], result.threshold_diagnostics)
    result.report_dir = str(report_dir)
    return result


def run_from_archive_paths(archive_paths: Sequence[Path], precommitment_path: Path, repo_root: Path, out_dir: Path | None = None, archive_backfill_invoked: bool = False) -> Phase0AResult:
    rows, diagnostics = load_archive_rows(archive_paths)
    result = run_phase0a_audit(rows, diagnostics, precommitment_path, repo_root=repo_root, archive_source_path=":".join(str(p) for p in archive_paths), archive_backfill_invoked=archive_backfill_invoked)
    if out_dir is not None:
        result = write_report_artifacts(result, out_dir)
        if result.summary.get("precommitment_sha256") != compute_precommitment_hash(precommitment_path):
            result.summary["status"] = STATUS_INVALID_PRECOMMITMENT
            result.summary["unlocks_phase0b"] = False
            result.summary["phase0b_locked_reason"] = "summary precommitment hash does not match document hash"
            write_report_artifacts(result, out_dir)
    return result
