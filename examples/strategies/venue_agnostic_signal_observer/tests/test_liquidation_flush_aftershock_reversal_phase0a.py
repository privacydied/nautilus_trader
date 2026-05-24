from __future__ import annotations

import ast
import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    COOLDOWN_HOURS,
    EVENT_WINDOW_HOURS,
    MAX_CALENDAR_YEAR_EVENT_SHARE,
    MAX_SYMBOL_EVENT_SHARE,
    MIN_ACCEPTED_EVENTS,
    MIN_ACCEPTED_SYMBOLS,
    OI_CHANGE_THRESHOLD_PCT,
    PRICE_RETURN_THRESHOLD_PCT,
    ArchiveRow,
    LoadDiagnostics,
    apply_cooldown,
    compute_past_8h_points,
    compute_precommitment_hash,
    compute_threshold_diagnostics,
    dedupe_symbol_rows,
    discover_archive_paths,
    load_archive_rows,
    run_phase0a_audit,
    write_report_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_V0_PHASE0A_PRECOMMITMENT.md"
PY_FILES = [
    REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/liquidation_flush_aftershock_reversal_phase0a.py",
    REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/run_liquidation_flush_aftershock_reversal_phase0a.py",
]
MD_FILES = [PRECOMMITMENT]


def row(symbol: str, ts: datetime, price: float, oi: float, order: int = 0) -> ArchiveRow:
    return ArchiveRow(ts, symbol, price, oi, "fixture", order)


def hourly_symbol(symbol: str, start: datetime, months: int = 12, event_offsets: list[int] | None = None) -> list[ArchiveRow]:
    rows = []
    event_offsets = event_offsets or []
    days = int(months * 31)
    price = 100.0
    oi = 1000.0
    events = set(event_offsets)
    order = 0
    for hour in range(days * 24 + 1):
        ts = start + timedelta(hours=hour)
        if hour in events:
            price = 90.0
            oi = 900.0
        elif hour - 8 in events:
            price = 100.0
            oi = 1000.0
        rows.append(row(symbol, ts, price, oi, order))
        order += 1
    return rows


def run_rows(rows: list[ArchiveRow], generated_at: datetime | None = None):
    return run_phase0a_audit(
        rows,
        LoadDiagnostics(total_raw_rows=len(rows), loaded_rows=len(rows), source_paths=["fixture"]),
        PRECOMMITMENT,
        generated_at=generated_at or datetime(2026, 1, 1, tzinfo=UTC),
        repo_root=REPO_ROOT,
        archive_source_path="fixture",
        archive_backfill_invoked=False,
    )


def test_clean_synthetic_population_passes():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(10):
        offsets = [240 + s * 24 + i * 24 * 95 for i in range(10)]
        rows.extend(hourly_symbol(f"ALT{s}", start, months=36, event_offsets=offsets))
    result = run_rows(rows)
    assert result.summary["status"] == "PHASE0A_EVENT_POPULATION_READY"
    assert result.summary["unlocks_phase0b"] is True


def test_insufficient_coverage_fewer_than_8_symbols():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(7):
        rows.extend(hourly_symbol(f"ALT{s}", start, event_offsets=[240 + i * 24 * 45 for i in range(20)]))
    result = run_rows(rows)
    assert result.summary["status"] == "PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE"


def test_underpowered_event_population():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(8):
        rows.extend(hourly_symbol(f"ALT{s}", start, event_offsets=[240 + s * 24 + i * 24 * 90 for i in range(3)]))
    result = run_rows(rows)
    assert result.summary["status"] == "NEEDS_MORE_DATA_UNDERPOWERED_EVENT_POPULATION"


def test_symbol_concentration_failure():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    rows.extend(hourly_symbol("BIG", start, months=24, event_offsets=[240 + i * 24 * 10 for i in range(70)]))
    for s in range(7):
        rows.extend(hourly_symbol(f"ALT{s}", start, months=24, event_offsets=[480 + s * 24 + i * 24 * 90 for i in range(5)]))
    result = run_rows(rows)
    assert result.summary["accepted_event_count_after_cooldown"] >= 100
    assert result.summary["status"] == "PHASE0A_SYMBOL_CONCENTRATION_FAILED"


def test_year_concentration_failure():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    # 8 symbols, 13 events each, all inside one year after cooldown.
    for s in range(8):
        offsets = [240 + s * 10 + i * 24 * 20 for i in range(13)]
        rows.extend(hourly_symbol(f"ALT{s}", start, months=12, event_offsets=offsets))
    result = run_rows(rows)
    assert result.summary["accepted_event_count_after_cooldown"] >= 100
    assert result.summary["max_symbol_event_share"] <= 0.30
    assert result.summary["status"] == "PHASE0A_YEAR_CONCENTRATION_FAILED"


def test_cooldown_enforcement_greedy_from_earliest():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = hourly_symbol("ALT", start, event_offsets=[100, 101, 172, 173, 245])
    deduped, _ = dedupe_symbol_rows(rows)
    points, _ = compute_past_8h_points(deduped)
    candidates = [p for p in points if p.price_return_8h_pct <= -8 and p.oi_change_8h_pct <= -8]
    events = apply_cooldown(candidates)
    assert [e.event_timestamp_utc for e in events] == [
        "2022-01-05T04:00:00Z",
        "2022-01-08T05:00:00Z",
        "2022-01-11T06:00:00Z",
    ]


def test_duplicate_timestamp_handling_counts_and_warns():
    ts = datetime(2022, 1, 1, tzinfo=UTC)
    rows = [row("ALT", ts, 100, 1000, 0), row("ALT", ts, 101, 1020, 1)]
    deduped, audit = dedupe_symbol_rows(rows)
    assert len(deduped) == 1
    assert deduped[0].price == 101
    assert audit.duplicate_timestamp_rows == 1
    assert audit.non_monotonic_detected is True
    assert any("price drift" in w for w in audit.warnings)
    assert any("OI drift" in w for w in audit.warnings)


def test_non_positive_price_oi_rejection(tmp_path: Path):
    p = tmp_path / "bad.csv"
    p.write_text("timestamp,symbol,price,open_interest\n2022-01-01T00:00:00Z,ALT,0,100\n2022-01-01T01:00:00Z,ALT,100,-1\n", encoding="utf-8")
    rows, diag = load_archive_rows([p])
    result = run_phase0a_audit(rows, diag, PRECOMMITMENT, repo_root=REPO_ROOT, archive_source_path=str(p))
    assert rows == []
    assert result.summary["status"] == "PHASE0A_ERROR_INVALID_INPUT"


def test_past_only_8h_computation_no_future_rows():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = [row("ALT", start + timedelta(hours=h), 100, 1000, h) for h in range(20)]
    base_points, _ = compute_past_8h_points(rows)
    rows_with_future = list(rows) + [row("ALT", start + timedelta(hours=30), 1, 1, 30)]
    future_points, _ = compute_past_8h_points(rows_with_future)
    assert [(p.timestamp, p.price_return_8h_pct) for p in base_points] == [(p.timestamp, p.price_return_8h_pct) for p in future_points if p.timestamp <= rows[-1].timestamp]


def test_precommitment_constants_match_doc():
    text = PRECOMMITMENT.read_text(encoding="utf-8")
    assert EVENT_WINDOW_HOURS == 8 and "Event window: 8h" in text
    assert PRICE_RETURN_THRESHOLD_PCT == -8.0 and "<= -8.0%" in text
    assert OI_CHANGE_THRESHOLD_PCT == -8.0 and "<= -8.0%" in text
    assert COOLDOWN_HOURS == 72 and "Cooldown: 72h" in text
    assert MIN_ACCEPTED_EVENTS == 100 and "at least 100" in text
    assert MIN_ACCEPTED_SYMBOLS == 8 and "at least 8" in text
    assert MAX_SYMBOL_EVENT_SHARE == 0.30 and "30%" in text
    assert MAX_CALENDAR_YEAR_EVENT_SHARE == 0.45 and "45%" in text


def test_diagnostic_thresholds_cannot_unlock_phase0b():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(8):
        # -6% events satisfy diagnostic -5/-5 but not primary -8/-8.
        sym_rows = hourly_symbol(f"ALT{s}", start, months=18, event_offsets=[])
        event_hours = [240 + s * 24 + i * 24 * 45 for i in range(13)]
        for idx, r in enumerate(sym_rows):
            if idx in event_hours:
                sym_rows[idx] = row(r.symbol, r.timestamp, 94.0, 940.0, r.file_order)
        rows.extend(sym_rows)
    result = run_rows(rows)
    assert result.summary["status"] == "NEEDS_MORE_DATA_UNDERPOWERED_EVENT_POPULATION"
    diags = {d["threshold_label"]: d for d in result.threshold_diagnostics}
    assert diags["diagnostic_-5_price_-5_oi"]["accepted_events_after_cooldown"] >= 100
    assert diags["diagnostic_-5_price_-5_oi"]["unlocks_phase0b_candidate"] is False
    assert result.summary["unlocks_phase0b"] is False


def test_precommitment_sha_self_consistency_and_summary(tmp_path: Path):
    data = PRECOMMITMENT.read_bytes()
    kept = []
    removed = False
    recorded = None
    for line in data.splitlines(keepends=True):
        if line.startswith(b"Precommitment SHA-256 (self):") and not removed:
            recorded = line.decode().split(":", 1)[1].strip()
            removed = True
            continue
        kept.append(line)
    actual = hashlib.sha256(b"".join(kept)).hexdigest()
    assert actual == recorded == compute_precommitment_hash(PRECOMMITMENT)
    result = run_rows(hourly_symbol("ALT", datetime(2021, 1, 1, tzinfo=UTC)))
    report = write_report_artifacts(result, tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["precommitment_sha256"] == actual
    assert report.report_dir == str(tmp_path)


def test_invalid_input_statuses(tmp_path: Path):
    zero = run_phase0a_audit([], LoadDiagnostics(), PRECOMMITMENT, repo_root=REPO_ROOT)
    assert zero.summary["status"] == "PHASE0A_ERROR_INVALID_INPUT"
    p = tmp_path / "bad.csv"
    p.write_text("timestamp,symbol,price,open_interest\nnot-a-date,A,1,1\nnot-a-date,B,1,1\n2022-01-01T00:00:00Z,C,1,1\n", encoding="utf-8")
    rows, diag = load_archive_rows([p])
    bad = run_phase0a_audit(rows, diag, PRECOMMITMENT, repo_root=REPO_ROOT, archive_source_path=str(p))
    assert bad.summary["status"] == "PHASE0A_ERROR_INVALID_INPUT"
    p2 = tmp_path / "dates.csv"
    p2.write_text("timestamp,symbol,price,open_interest\nbad,A,1,1\nworse,B,1,1\n", encoding="utf-8")
    rows2, diag2 = load_archive_rows([p2])
    all_bad_ts = run_phase0a_audit(rows2, diag2, PRECOMMITMENT, repo_root=REPO_ROOT, archive_source_path=str(p2))
    assert all_bad_ts.summary["status"] == "PHASE0A_ERROR_INVALID_INPUT"


def test_archive_discovery_bound(tmp_path: Path):
    repo = tmp_path
    allowed = repo / "data/hyperliquid_archive"
    allowed.mkdir(parents=True)
    (allowed / "rows.csv").write_text("timestamp,symbol,price,open_interest\n2022-01-01T00:00:00Z,A,1,1\n", encoding="utf-8")
    outside = repo / "outside"
    outside.mkdir()
    (outside / "rows.csv").write_text("timestamp,symbol,price,open_interest\n2022-01-01T00:00:00Z,B,1,1\n", encoding="utf-8")
    found = discover_archive_paths(repo)
    assert found == [allowed]
    assert outside not in found


def test_freshness_diagnostic_not_gate():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(10):
        rows.extend(hourly_symbol(f"ALT{s}", start, months=36, event_offsets=[240 + s * 24 + i * 24 * 95 for i in range(10)]))
    result = run_rows(rows, generated_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert result.summary["archive_end_age_days"] > 1000
    assert result.summary["status"] == "PHASE0A_EVENT_POPULATION_READY"


def _strip_md_code_and_allowed_sections(text: str) -> list[tuple[int, str]]:
    out = []
    in_code = False
    skip_section = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if line.startswith("## "):
            title = line[3:].strip()
            skip_section = title in {"Structurally distinct from", "Relationship to rejected research", "Relationship to prior rejected research", "Archive-only safety", "Forbidden filters"}
        if not in_code and not skip_section:
            out.append((i, line))
    return out


def run_safety_scan() -> tuple[str, list[str]]:
    forbidden = {
        "create_order", "submit_order", "cancel_order", "OrderFactory", "LiveExecutionClient", "TradingNode",
        "private_key", "api_key", "secret", "wallet", "sign", "paper trading", "shadow",
        "bot approval", "execution client", "Supertrend", "RSI", "ATR", "funding_carry", "direction_proxy",
    }
    allowed_substrings = {"strategies", "design", "diagnostic", "authorization", "calendar_year_event_share", "archive-only safety", "forbidden filters"}
    hits = []
    for path in PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = path.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                values.append(node.name)
            elif isinstance(node, ast.Import):
                values.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                values.append(node.module or "")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
            for val in values:
                low = str(val).lower()
                if any(allowed in low for allowed in allowed_substrings):
                    continue
                for token in forbidden:
                    if token.lower() in low:
                        hits.append(f"{path}:{getattr(node, 'lineno', 0)}:{token}:{lines[getattr(node, 'lineno', 1)-1].strip()}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                import re
                if re.search(r"family[0-9]+", node.value, re.I):
                    hits.append(f"{path}:{node.lineno}:family[0-9]+")
    import re
    for path in MD_FILES:
        for lineno, line in _strip_md_code_and_allowed_sections(path.read_text(encoding="utf-8")):
            low = line.lower()
            if any(allowed in low for allowed in allowed_substrings):
                continue
            for token in forbidden:
                if token.lower() in low:
                    hits.append(f"{path}:{lineno}:{token}:{line.strip()}")
            if re.search(r"family[0-9]+", line, re.I):
                hits.append(f"{path}:{lineno}:family[0-9]+:{line.strip()}")
    return ("SAFETY_SCAN_CLEAN" if not hits else "SAFETY_SCAN_HITS", hits)


def test_focused_safety_scan_clean():
    status, hits = run_safety_scan()
    assert status == "SAFETY_SCAN_CLEAN", "\n".join(hits)
