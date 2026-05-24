# ruff: noqa: S101
from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_supertrend_v0_residual_diagnostic as diag


def _write_entries(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "symbol", "timeframe", "entry_ts", "exit_ts", "direction", "entry_price", "exit_price",
        "holding_period_bars", "exit_reason", "gross_return_bps", "funding_accrual_bps",
        "net_return_bps_primary",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def test_artifact_inventory_creation(tmp_path: Path) -> None:
    report = tmp_path / "v0"
    _write_entries(report / "entries_preview.csv", [{"symbol": "AAA", "timeframe": "1d", "entry_ts": "2025-01-01T00:00:00Z", "exit_ts": "2025-01-02T00:00:00Z", "direction": "long", "entry_price": 100, "exit_price": 101, "gross_return_bps": 100, "net_return_bps_primary": 50}])
    inv = diag.build_artifact_inventory(report)
    assert inv["files"][0]["sha256"]
    assert "gross_return_bps" in inv["files"][0]["columns"]
    assert inv["files"][0]["inferred_role"] == "entry_records"


def test_missing_per_entry_artifacts_returns_status_and_stops(tmp_path: Path) -> None:
    report = tmp_path / "v0"; report.mkdir()
    (report / "summary.json").write_text("{}")
    out = tmp_path / "out"
    summary = diag.run_diagnostic(report, out, price_archive_path=None, git_sha="s", git_branch="b")
    assert diag.STATUS_MISSING_V0_ENTRY_ARTIFACTS in summary["statuses"]
    assert not (out / "per_symbol.csv").exists()
    assert (out / "missing_artifacts.md").exists()


def test_v0_report_directory_sha_pinning_and_mismatch(tmp_path: Path) -> None:
    d = tmp_path / "d"; d.mkdir(); (d / "a.txt").write_text("a")
    digest = diag.sha256_directory(d)
    assert len(digest) == 64
    diag.assert_pinned_sha(d, digest)
    with pytest.raises(ValueError, match="V0 report directory SHA mismatch"):
        diag.assert_pinned_sha(d, "0" * 64)


def test_percentile_stat_calculation_and_sign_warning() -> None:
    rows = [diag.EntryRecord("A", "1d", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "long", 1, 1, 0, "", 50, 0, 40), diag.EntryRecord("B", "1d", "2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z", "long", 1, 1, 0, "", 50, 0, 40), diag.EntryRecord("C", "1d", "2025-01-03T00:00:00Z", "2025-01-04T00:00:00Z", "long", 1, 1, 0, "", -160, 0, -170)]
    q1 = diag.compute_q1(rows)
    assert q1["overall"]["gross_return_bps_p50"] == pytest.approx(50)
    assert q1["overall"]["gross_return_bps_mean"] == pytest.approx(-20)
    assert diag.STATUS_DISTRIBUTION_SIGN_INCONSISTENCY_WARNING in q1["statuses"]


def test_top_decile_cumulative_gross_contribution() -> None:
    values = [1000] + [1] * 19
    assert diag.top_decile_contribution(values) > 0.5


def test_by_symbol_concentration_warning() -> None:
    rows = []
    for sym, gross in [("A", 1000), ("B", 900), ("C", 800), ("D", 10), ("E", 10)]:
        rows.append(diag.EntryRecord(sym, "1d", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "long", 1, 1, 0, "", gross, 0, gross-10))
    q1 = diag.compute_q1(rows)
    assert q1["symbol_concentration"]["gross_contribution_hhi"] > 0.20
    assert diag.STATUS_CONCENTRATION_WARNING_DIAGNOSTIC in q1["statuses"]


def test_by_month_quarter_and_trailing_3m() -> None:
    rows = [diag.EntryRecord("A", "1d", f"2025-{m:02d}-01T00:00:00Z", f"2025-{m:02d}-02T00:00:00Z", "long", 1, 1, 0, "", m, 0, m) for m in range(1, 7)]
    q1 = diag.compute_q1(rows)
    assert len(q1["monthly"]) == 6
    assert len(q1["quarterly"]) == 2
    assert q1["rolling_3m"][0]["rolling_3m_gross_median"] == pytest.approx(2)


def test_breakeven_and_residual_cost_decomposition() -> None:
    rows = [diag.EntryRecord("A", "1d", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "long", 1, 1, 0, "", 50, -5, 0), diag.EntryRecord("A", "1d", "2025-01-02T00:00:00Z", "2025-01-03T00:00:00Z", "long", 1, 1, 0, "", 50, -5, 0)]
    q2 = diag.compute_q2(rows, explicit_fee_bps=10)
    assert q2["median_realized_total_cost_bps"] == pytest.approx(50)
    assert q2["cumulative_sum_breakeven_total_cost_bps"] == pytest.approx(50)
    assert q2["median_residual_cost_bps"] == pytest.approx(45)  # 50 - fee(10) - funding(-5)
    at50 = [r for r in q2["cost_sensitivity"] if r["cost_bps"] == 50][0]
    at25 = [r for r in q2["cost_sensitivity"] if r["cost_bps"] == 25][0]
    assert at50["median_net_after_cost_bps"] == pytest.approx(0)
    assert at25["median_net_after_cost_bps"] > 0
    assert q2["per_symbol"]["A"]["median_gross_breakeven_bps"] == pytest.approx(50)


def test_giveback_long_short_and_mfe_timing() -> None:
    rows = [
        diag.EntryRecord("A", "1d", "2025-01-01T00:00:00Z", "2025-01-01T02:00:00Z", "long", 100, 101, 2, "signal_flip", 100, 0, 90),
        diag.EntryRecord("B", "1d", "2025-01-01T00:00:00Z", "2025-01-01T02:00:00Z", "short", 100, 99, 2, "max_hold", 100, 0, 90),
    ]
    prices = {
        "A": [diag.PriceBar("2025-01-01T00:00:00Z", "A", 100, 110, 99, 105), diag.PriceBar("2025-01-01T01:00:00Z", "A", 105, 108, 98, 101)],
        "B": [diag.PriceBar("2025-01-01T00:00:00Z", "B", 100, 101, 90, 95), diag.PriceBar("2025-01-01T01:00:00Z", "B", 95, 102, 91, 99)],
    }
    gb = diag.compute_giveback(rows, prices)
    assert gb["trades"][0]["mfe_bps"] == pytest.approx(1000)
    assert gb["trades"][0]["peak_capture_ratio"] == pytest.approx(0.10)
    assert gb["trades"][0]["mfe_bars_from_entry"] == 0
    assert gb["trades"][1]["mfe_bps"] == pytest.approx(1111.111111, rel=1e-5)
    assert gb["summary"]["giveback_signature"] == "strong"
    assert "signal_flip" in gb["by_exit_reason"] and "max_hold" in gb["by_exit_reason"]


def test_missing_price_path_returns_schema_insufficient() -> None:
    rows = [diag.EntryRecord("A", "1d", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "long", 1, 1, 0, "", 10, 0, 0)]
    gb = diag.compute_giveback(rows, {})
    assert diag.STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK in gb["statuses"]


def test_reconstruction_from_price_archive(tmp_path: Path) -> None:
    p = tmp_path / "hourly_prices.csv"
    p.write_text("timestamp_utc,symbol,open,high,low,close,price_source\n2025-01-01T00:00:00Z,A,100,110,99,105,mark\n")
    prices = diag.load_price_archive(p)
    assert prices["A"][0].high == 110


def test_forbidden_verdict_strings_cannot_be_emitted() -> None:
    with pytest.raises(ValueError):
        diag.validate_statuses(["REJECTED"])


def test_forbidden_conclusion_regex_blocks_strategy_advice() -> None:
    with pytest.raises(ValueError):
        diag.validate_markdown_firewall("maker would work")
    with pytest.raises(ValueError):
        diag.validate_markdown_firewall("use atr stop")


def test_firewall_integrity_ast_scan_confirms_no_v0_source_imports() -> None:
    path = Path(diag.__file__)
    tree = ast.parse(path.read_text())
    assert diag.scan_ast_for_forbidden_v0_imports(tree) == []


def test_non_conclusions_constant_appears_verbatim_and_summary_safety_flags(tmp_path: Path) -> None:
    report = tmp_path / "v0"
    _write_entries(report / "entries_preview.csv", [{"symbol": "A", "timeframe": "1d", "entry_ts": "2025-01-01T00:00:00Z", "exit_ts": "2025-01-02T00:00:00Z", "direction": "long", "entry_price": 100, "exit_price": 101, "gross_return_bps": 100, "funding_accrual_bps": 0, "net_return_bps_primary": 0}])
    out = tmp_path / "out"
    summary = diag.run_diagnostic(report, out, price_archive_path=None, git_sha="s", git_branch="b")
    md = (out / "diagnostic_report.md").read_text()
    assert diag.NON_CONCLUSIONS in md
    assert summary["no_orders"] is True
    assert summary["registry_updated"] is False
    fw = json.loads((out / "firewall_compliance.json").read_text())
    assert set(diag.REQUIRED_FIREWALL_FIELDS).issubset(fw)
    assert all(fw.values())
