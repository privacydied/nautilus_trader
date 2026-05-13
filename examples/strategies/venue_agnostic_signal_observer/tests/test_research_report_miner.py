"""Tests for the research report miner.

Uses temporary directories with tiny fixture files — no real data, no
network calls, no private keys.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.research_report_miner import (
    _detect_project,
    _normalize_verdict,
    _record_from_dict,
    mine_reports,
    _write_json,
    _write_csv,
    _write_md,
)


# -- Unit helpers --------------------------------------------------------

def test_detect_project_signal_observer():
    assert "signal_observer" in _detect_project("reports/signal_observer_v3/x.json")

def test_detect_project_trade_flow():
    assert "signal_observer" in _detect_project("reports/trade_flow_impulse_v1/x.json")

def test_detect_project_l2_maker():
    assert _detect_project("reports/v7_l2_maker_paper/x.json") == "kraken_l2_maker_paper"

def test_detect_project_v6():
    assert _detect_project("reports/v6_market_structure/x.jsonl") == "kraken_market_structure_scanner"

def test_detect_project_kraken_btcusd():
    assert _detect_project("reports/kraken_btcusd/backtest.json") == "kraken_btcusd_research"

def test_detect_project_unknown():
    assert _detect_project("reports/foobar/result.json") == "unknown"


def test_normalize_verdict_rejected():
    assert _normalize_verdict("REJECTED") == "REJECTED"
    assert _normalize_verdict("This was REJECTED for reasons.") == "REJECTED"

def test_normalize_verdict_candidate():
    assert _normalize_verdict("CANDIDATE_FOR_LONGER_OBSERVATION") == "CANDIDATE_FOR_LONGER_OBSERVATION"

def test_normalize_verdict_needs_data():
    assert _normalize_verdict("NEEDS_MORE_DATA") == "NEEDS_MORE_DATA"

def test_normalize_verdict_empty():
    assert _normalize_verdict("") == "UNKNOWN"

def test_normalize_verdict_none():
    assert _normalize_verdict(None) == "UNKNOWN"

def test_normalize_verdict_casual():
    assert _normalize_verdict("CANDIDATE for longer observation") == "CANDIDATE_FOR_LONGER_OBSERVATION"


# -- Dict extraction -----------------------------------------------------

def test_record_from_dict_basic():
    row = {
        "source_venue": "coinbase",
        "target_venue": "kraken",
        "symbol": "BTC-USD",
        "mean_net_return_bps": -13.5,
        "fee_bps": 12.0,
        "slippage_bps": 2.0,
        "win_rate": 0.0,
        "valid_events": 639,
        "candidate": False,
    }
    rec = _record_from_dict(row, "test.json", "test_proj", "test_study")
    assert rec.source_venue == "coinbase"
    assert rec.target_venue == "kraken"
    assert rec.symbol == "BTC-USD"
    # mean_net_return_bps is picked as gross_bps (it's a BPS key match)
    # net_bps is not set because the row has no net_* key
    assert rec.gross_bps == -13.5
    assert rec.fee_bps == 12.0
    assert rec.slippage_bps == 2.0
    assert rec.win_rate == 0.0
    assert rec.events_count == 639

def test_record_from_dict_verdict_in_row():
    row = {"verdict": "CANDIDATE_FOR_LONGER_OBSERVATION"}
    rec = _record_from_dict(row, "test.json", "p", "s")
    assert rec.verdict == "CANDIDATE_FOR_LONGER_OBSERVATION"

def test_record_from_dict_rejection_reason_list():
    row = {"rejection_reason": ["reason_a", "reason_b"]}
    rec = _record_from_dict(row, "test.json", "p", "s")
    assert "reason_a" in rec.rejection_reason
    assert "reason_b" in rec.rejection_reason

def test_record_from_dict_win_rate_percent():
    row = {"win_rate_percent": 25.0}
    rec = _record_from_dict(row, "test.json", "p", "s")
    assert rec.win_rate == 0.25


# -- Integration: mine_reports with fixtures ------------------------------

@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """Create a temporary reports directory with known fixture files."""
    reports = tmp_path / "reports"
    reports.mkdir()

    # JSON summary with a verdict
    (reports / "summary.json").write_text(json.dumps({
        "summary": {
            "total_signals": 131,
            "valid_evaluations": 639,
            "mean_net_return_bps": -13.5,
            "fee_bps": 12.0,
            "verdict": "REJECTED",
        },
        "pair_reports": [
            {
                "source_venue": "coinbase",
                "target_venue": "kraken",
                "symbol": "BTC-USD",
                "groups": [
                    {"total_signals": 6, "valid_events": 30, "mean_net_return_bps": -13.1, "win_rate": 0.0}
                ]
            }
        ],
        "baseline_results": {"mean_net_return_bps": -13.8},
    }))

    # JSONL observations
    obs_file = reports / "observations.jsonl"
    obs_file.write_text("\n".join([
        json.dumps({"timestamp_ms": 1000, "net_edge_bps": -25.0, "funding_apr": 15.0}),
        json.dumps({"timestamp_ms": 2000, "net_edge_bps": 5.0, "funding_apr": 45.0}),
        ""  # trailing newline
    ]))

    # CSV
    csv_file = reports / "tick_summary.csv"
    with open(csv_file, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source_venue", "target_venue", "mean_net_return_bps", "valid_events", "signal_type"])
        w.writeheader()
        w.writerow({"source_venue": "kraken", "target_venue": "coinbase", "mean_net_return_bps": -12.5, "valid_events": 200, "signal_type": "count_burst"})

    # Markdown with verdict
    md_file = reports / "report.md"
    md_file.write_text("## Final Verdict\n\n**REJECTED**\n\nSome text about -15 bps.\n")

    # Malformed JSON
    bad = reports / "bad.json"
    bad.write_text("{this is not valid json}")

    return reports


def test_mine_reports_json(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    assert len(records) > 0
    src_files = {r.source_file for r in records}
    assert any("summary.json" in sf for sf in src_files)

def test_mine_reports_jsonl(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    jsonl_recs = [r for r in records if "jsonl" in r.source_file]
    assert len(jsonl_recs) >= 2

def test_mine_reports_csv(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    csv_recs = [r for r in records if ".csv" in r.source_file]
    assert len(csv_recs) >= 1
    assert any(r.source_venue == "kraken" for r in csv_recs)

def test_mine_reports_md(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    md_recs = [r for r in records if r.source_file.endswith(".md")]
    assert len(md_recs) >= 1
    assert any(r.verdict == "REJECTED" for r in md_recs)

def test_mine_reports_bad_json_warning(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    assert any("bad.json" in w for w in warnings)

def test_mine_reports_missing_dir(tmp_path: Path):
    records, warnings = mine_reports(str(tmp_path / "nonexistent"))
    assert len(records) == 0
    assert len(warnings) >= 1

def test_output_files_created(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    _write_json(records, warnings, fixture_dir)
    _write_csv(records, fixture_dir)
    _write_md(records, warnings, fixture_dir)

    assert (fixture_dir / "research_status_summary.json").exists()
    assert (fixture_dir / "research_status_summary.csv").exists()
    assert (fixture_dir / "research_status_summary.md").exists()

    d = json.loads((fixture_dir / "research_status_summary.json").read_text())
    assert "records" in d
    assert "warnings" in d

    with open(fixture_dir / "research_status_summary.csv") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) > 0

def test_verdict_normalization_in_output(fixture_dir: Path):
    records, warnings = mine_reports(str(fixture_dir))
    _write_md(records, warnings, fixture_dir)
    md_text = (fixture_dir / "research_status_summary.md").read_text()
    assert "REJECTED" in md_text

def test_no_summary_files_self_consumed(fixture_dir: Path):
    _write_json([], [], fixture_dir)
    _write_csv([], fixture_dir)
    _write_md([], [], fixture_dir)
    records, _ = mine_reports(str(fixture_dir))
    assert all("research_status_summary" not in r.source_file for r in records)
