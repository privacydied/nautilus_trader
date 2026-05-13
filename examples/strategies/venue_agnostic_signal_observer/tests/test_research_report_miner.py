"""Tests for research_report_miner.py"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest
import sys

# Ensure the miner is importable from the tests package.
REPO_ROOT = Path(__file__).resolve().parents[3]
MINER_PATH = REPO_ROOT / "examples" / "strategies" / "research_report_miner.py"
sys.path.insert(0, str(MINER_PATH.parent))

from research_report_miner import (  # noqa: E402
    StudyResult,
    _infer_project,
    _normalize_verdict,
    _parse_val,
    _make_result,
    _process_json_report,
    _process_jsonl_file,
    mine_reports,
    format_bps_gate_table,
    format_rejected_md,
)


# -- unit helpers --


def test_parse_val():
    assert _parse_val(1.5) == 1.5
    assert _parse_val("3.14") == 3.14
    assert _parse_val("42", "int") == 42
    assert _parse_val("not_a_number") is None
    assert _parse_val(None) is None


def test_normalize_verdict():
    assert _normalize_verdict("REJECTED") == "REJECTED"
    assert _normalize_verdict("was rejected for reasons") == "REJECTED"
    assert _normalize_verdict("CANDIDATE_FOR_LONGER_OBSERVATION") == "CANDIDATE"
    assert _normalize_verdict("NEEDS_MORE_DATA") == "NEEDS_MORE_DATA"
    assert _normalize_verdict("") == "UNKNOWN"
    assert _normalize_verdict(None) == "UNKNOWN"


def test_infer_project():
    assert _infer_project("reports/signal_observer_tick_lead_lag_v3/x.json") == "tick_lead_lag"
    assert _infer_project("reports/trade_flow_impulse_v1/y.json") == "trade_flow_impulse"
    assert _infer_project("reports/v6_market_structure/z.json") == "v6_funding_basis"
    assert _infer_project("reports/foobar/a.json") == "unknown"


def test_make_result_inference_candidate():
    r = StudyResult(study="x", signal_type="t", symbol="BTC/USD",
                    source_venue="a", target_venue="b", horizon_ms="1000",
                    candidate=True, verdict="UNKNOWN")
    _make_result(r)
    assert r.verdict == "CANDIDATE"


def test_make_result_inference_rejected_net():
    r = StudyResult(study="x", signal_type="t", symbol="BTC/USD",
                    source_venue="a", target_venue="b", horizon_ms="1000",
                    net_bps=-13.5, verdict="UNKNOWN")
    _make_result(r)
    assert r.verdict == "REJECTED"


def test_make_result_inference_rejected_reasons():
    r = StudyResult(study="x", signal_type="t", symbol="BTC/USD",
                    source_venue="a", target_venue="b", horizon_ms="1000",
                    rejection_reasons=["insufficient_events: 5 < 50"],
                    verdict="UNKNOWN")
    _make_result(r)
    assert r.verdict == "REJECTED"


def test_make_result_inference_needs_data():
    r = StudyResult(study="x", signal_type="t", symbol="BTC/USD",
                    source_venue="a", target_venue="b", horizon_ms="1000",
                    total_signals=0, verdict="UNKNOWN")
    _make_result(r)
    assert r.verdict == "NEEDS_MORE_DATA"


# -- integration with temp fixtures --


@pytest.fixture
def fixture_reports(tmp_path: Path) -> Path:
    """Create a temporary reports/ dir with small fixtures."""
    r = tmp_path / "reports" / "tick_lead_lag_sample"
    r.mkdir(parents=True)

    # JSON summary in the style produced by run_tick_lead_lag.py
    summary = {
        "summary": {
            "total_signals": 5,
            "valid_evaluations": 25,
            "fee_bps": 12.0,
            "slippage_bps": 2.0,
            "quote_mismatch_buffer_bps": 5.0,
            "results_by_group": [
                {
                    "source_venue": "coinbase",
                    "target_venue": "kraken",
                    "symbol": "BTC-USD",
                    "lookback_ms": 1000,
                    "total_signals": 5,
                    "valid_events": 25,
                    "rejected_events": 0,
                    "mean_net_return_bps": -13.1,
                    "median_net_return_bps": -14.0,
                    "win_rate": 0.0,
                }
            ],
            "candidate_groups": [],
            "baseline_results": {"mean_net_return_bps": -13.8},
        },
    }
    (r / "tick_summary.json").write_text(json.dumps(summary))

    # JSONL observation stream
    obs_lines = []
    for i in range(10):
        obs_lines.append(json.dumps({"net_edge_bps": -25.0 if i < 8 else 5.0}))
    (r / "observations.jsonl").write_text("\n".join(obs_lines) + "\n")

    # A malformed JSON file to test warning tolerance
    (r / "bad.json").write_text("{this is not valid json}")

    return r.parent  # return the directory above "reports/"


def test_mine_reports_json(fixture_reports: Path):
    results = mine_reports(str(fixture_reports))
    assert len(results) > 0
    # Ensure the group record was found
    tick_recs = [r for r in results if "tick_lead_lag_sample" in r.study and r.signal_type == "tick_lead_lag" and r.net_bps is not None]
    assert len(tick_recs) >= 1
    # The record from the JSON summary has source/target venue
    json_recs = [r for r in tick_recs if r.source_venue]
    assert len(json_recs) >= 1
    rec = json_recs[0]
    assert rec.source_venue == "coinbase"
    assert rec.target_venue == "kraken"
    assert abs(rec.net_bps - (-13.1)) < 0.01
    assert rec.total_signals == 5
    assert rec.valid_events == 25
    assert rec.verdict == "REJECTED"


def test_mine_reports_jsonl(fixture_reports: Path):
    results = mine_reports(str(fixture_reports))
    jsonl_recs = [r for r in results if "jsonl" in r.source_file]
    assert len(jsonl_recs) > 0
    rec = jsonl_recs[0]
    assert rec.valid_events == 10
    # mean of 8x(-25) + 2x(5) = -200+10 = -190 / 10 = -19.0
    assert abs(rec.net_bps - (-19.0)) < 0.01


def test_mine_reports_missing_dir(tmp_path: Path):
    results = mine_reports(str(tmp_path / "nonexistent"))
    assert results == []


def test_format_bps_gate_table():
    recs = [
        StudyResult(study="a", signal_type="t", symbol="BTC/USD",
                    source_venue="c", target_venue="k", horizon_ms="1000",
                    gross_bps=1.0, fee_bps=12.0, slippage_bps=2.0,
                    net_bps=-13.0, win_rate=0.0, valid_events=25,
                    candidate=False),
    ]
    table = format_bps_gate_table(recs)
    assert "a" in table
    assert "-13.00" in table
    assert "0.0%" in table
    assert "NO" in table


def test_format_rejected_md():
    recs = [
        StudyResult(study="a", signal_type="t", symbol="BTC/USD",
                    source_venue="c", target_venue="k", horizon_ms="1000",
                    net_bps=-13.0, win_rate=0.0, verdict="REJECTED",
                    rejection_reasons=["insufficient_events: 5 < 50"]),
        StudyResult(study="b", signal_type="t", symbol="ETH/USD",
                    source_venue="c", target_venue="k", horizon_ms="1000",
                    net_bps=-14.0, win_rate=0.0, verdict="CANDIDATE", candidate=True),
    ]
    md = format_rejected_md(recs)
    assert "REJECTED" in md
    assert "CANDIDATE" in md
    assert "insufficient_events" in md


# -- end-to-end run --


def test_full_mine_and_output(fixture_reports: Path, tmp_path: Path):
    results = mine_reports(str(fixture_reports))
    assert len(results) >= 2  # at least JSON + JSONL

    md = format_rejected_md(results)
    bps = format_bps_gate_table(results)
    assert len(md) > 100
    assert len(bps) > 100

    # Write CSV to a temp path
    csv_path = tmp_path / "test_table.csv"
    lines = md.splitlines()
    csv_rows = []
    csv_lines = bps.splitlines()
    assert isinstance(csv_lines, list)
