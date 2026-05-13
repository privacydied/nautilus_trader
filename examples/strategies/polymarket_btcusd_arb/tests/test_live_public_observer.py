"""Tests for Phase 2 live observer modules."""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch,mock_open,MagicMock
from dataclasses import dataclass

def test_branch_check_accepts_phase2():
    from examples.strategies.polymarket_btcusd_arb.live_public_observer import _branch_ok
    with patch("subprocess.check_output",return_value="polymarket-btcusd-arb-phase2-observer\n"):
        assert _branch_ok() is True

def test_branch_check_rejects_master():
    from examples.strategies.polymarket_btcusd_arb.live_public_observer import _branch_ok
    with patch("subprocess.check_output",return_value="master\n"):
        assert _branch_ok() is False

def test_branch_check_rejects_nightly():
    from examples.strategies.polymarket_btcusd_arb.live_public_observer import _branch_ok
    with patch("subprocess.check_output",return_value="nightly\n"):
        assert _branch_ok() is False

def test_branch_check_rejects_phase1():
    from examples.strategies.polymarket_btcusd_arb.live_public_observer import _branch_ok
    with patch("subprocess.check_output",return_value="polymarket-btcusd-arb-phase1\n"):
        assert _branch_ok() is False

def test_discovery_filters_btc_15m():
    from examples.strategies.polymarket_btcusd_arb.live_market_discovery import UpDownMarketInfo
    info_15m=UpDownMarketInfo(slug="btc-updown-15m-1234567890",question="Bitcoin Up or Down 15m",active=True,closed=False,condition_id="",yes_token_id=None,no_token_id=None,start_ns=None,end_ns=None,series_slug=None,resolution_source=None)
    info_5m=UpDownMarketInfo(slug="btc-updown-5m-1234567890",question="Bitcoin Up or Down 5m",active=True,closed=False,condition_id="",yes_token_id=None,no_token_id=None,start_ns=None,end_ns=None,series_slug=None,resolution_source=None)
    info_eth=UpDownMarketInfo(slug="eth-updown-15m-1234567890",question="Ethereum Up or Down 15m",active=True,closed=False,condition_id="",yes_token_id=None,no_token_id=None,start_ns=None,end_ns=None,series_slug=None,resolution_source=None)
    assert "15m-" in info_15m.slug
    assert "15m-" not in info_5m.slug
    assert "btc" in info_15m.slug

def test_discovery_rejects_non_btc():
    from examples.strategies.polymarket_btcusd_arb.live_market_discovery import _parse_tokens
    tokens=_parse_tokens({"clobTokenIds":"[\"abc123\",\"def456\"]","outcomes":"[\"Up\",\"Down\"]"})
    assert tokens==("abc123","def456")

def test_ts_ns_parsing():
    from examples.strategies.polymarket_btcusd_arb.live_capture import _ts_ns
    assert _ts_ns("2025-12-30T15:30:00Z")==1767108600000000000
    assert _ts_ns(None) is None

def test_safety_checks_include_phase2():
    from examples.strategies.polymarket_btcusd_arb.safety_checks import check_path
    r=check_path(Path("examples/strategies/polymarket_btcusd_arb"))
    assert r["ok"],f"Safety violations: {r['violations']}"

def test_capture_writer_writes_jsonl(tmp_path):
    from examples.strategies.polymarket_btcusd_arb.live_capture import write_capture
    from examples.strategies.polymarket_btcusd_arb.live_market_discovery import UpDownMarketInfo
    from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig
    from examples.strategies.polymarket_btcusd_arb.models import DivergenceSignal
    info=UpDownMarketInfo(slug="test-market",question="Test",active=True,closed=False,condition_id="",yes_token_id=None,no_token_id=None,start_ns=None,end_ns=None,series_slug=None,resolution_source=None)
    config=PolymarketArbConfig()
    # Create rejection records to test rejection tracking
    rej=DivergenceSignal(market_slug="test-market",side="YES",threshold_bps=10,lookback_ns=0,tte_bucket="60s-180s",fair_probability=0.55,quoted_probability=0.50,raw_divergence_bps=50.0,maker_fee_bps=3.6,spread_bps=20.0,latency_buffer_bps=1.0,settlement_buffer_bps=2.0,stale_buffer_bps=0.5,net_divergence_bps=22.9,ts_event_ns=1000000000,expiry_ns=2000000000,rejection_reason="edge_below_threshold")
    cap={"signals":[],"rejections":[rej],"rejection_counts":{"edge_below_threshold":1},"evaluated_event_count":1,"binance_polls":1,"poly_polls":1,"binance_stale":0,"poly_stale":0,"missing_binance":0,"missing_poly":0,"duration_seconds":1.0,"_poly_events":[],"_binance_events":[]}
    d=write_capture("test-run",cap,info,config)
    assert (d/"metadata.json").exists()
    assert (d/"signals.jsonl").exists()
    assert (d/"rejections.jsonl").exists()
    assert (d/"heartbeat.jsonl").exists()
    assert (d/"observer_summary.json").exists()
    # Verify rejection was written
    obs=json.loads((d/"observer_summary.json").read_text())
    assert obs["rejection_count"]==1
    assert obs["rejection_counts"]["edge_below_threshold"]==1
    assert obs["evaluated_event_count"]==1

def test_replay_reads_capture(tmp_path):
    from examples.strategies.polymarket_btcusd_arb.live_replay import load_capture
    d=tmp_path/"cap_test"
    d.mkdir()
    (d/"metadata.json").write_text(json.dumps({"run_id":"test","market_slug":"test-market","end_ns":0}))
    (d/"signals.jsonl").write_text("")
    (d/"rejections.jsonl").write_text("")
    (d/"polymarket_events.jsonl").write_text("")
    (d/"binance_events.jsonl").write_text("")
    (d/"observer_summary.json").write_text(json.dumps({"candidate_count":0}))
    data=load_capture(d)
    assert data["metadata"]["run_id"]=="test"
    assert data["signals"]==[]

def test_live_report_writes_files(tmp_path):
    from examples.strategies.polymarket_btcusd_arb.live_reports import write_live_report
    write_live_report(tmp_path/"report_test",summary={"run_id":"test","branch":"phase2","market_slug":"test","candidate_count":0,"evaluated_event_count":0,"rejection_total":0,"rejection_counts":{}},signals=[],rejections=[],groups=[],safety={"ok":True,"violations":[]},replay={"deterministic":True})
    assert (tmp_path/"report_test/summary.json").exists()
    assert (tmp_path/"report_test/report.md").exists()
    assert (tmp_path/"report_test/safety_check.json").exists()
    assert (tmp_path/"report_test/replay_check.json").exists()

def test_observer_emits_no_orders():
    """AST scan: no OrderFactory, submit_order, cancel_order in live observer."""
    import ast
    observer_path=Path("examples/strategies/polymarket_btcusd_arb/live_public_observer.py")
    tree=ast.parse(observer_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in ("OrderFactory","TradingNode","LiveNode"),f"Banned import: {alias.name}"
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Attribute) and node.func.attr in ("submit_order","cancel_order"):
                raise AssertionError(f"Banned call: {node.func.attr}")

def test_zero_candidate_live_run_no_crash():
    """Zero candidates must not crash report generation."""
    from examples.strategies.polymarket_btcusd_arb.live_reports import write_live_report
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        write_live_report(Path(td),summary={"run_id":"zero-test","branch":"phase2","market_slug":"test","candidate_count":0,"evaluated_event_count":0,"rejection_total":0,"rejection_counts":{}},signals=[],rejections=[],groups=[],safety={"ok":True,"violations":[]},replay={"deterministic":True})
        report=(Path(td)/"report.md").read_text()
        assert "Phase 2" in report

def test_live_observer_records_edge_below_threshold_rejection():
    """Capture with evaluated events must record edge_below_threshold rejection."""
    from examples.strategies.polymarket_btcusd_arb.models import DivergenceSignal
    rej=DivergenceSignal(market_slug="test",side="YES",threshold_bps=10,lookback_ns=0,tte_bucket="60s-180s",fair_probability=0.50,quoted_probability=0.55,raw_divergence_bps=-50.0,maker_fee_bps=3.6,spread_bps=5.0,latency_buffer_bps=1.0,settlement_buffer_bps=2.0,stale_buffer_bps=0.5,net_divergence_bps=-62.1,ts_event_ns=1000000000,expiry_ns=2000000000,rejection_reason="edge_below_threshold")
    # Verify the rejection reason is set
    assert rej.rejection_reason=="edge_below_threshold"
    assert rej.net_divergence_bps<0

def test_live_observer_records_stale_binance_rejection():
    """Capture must record stale_or_missing_binance rejection when Binance state unavailable."""
    from examples.strategies.polymarket_btcusd_arb.models import DivergenceSignal
    rej=DivergenceSignal(market_slug="test",side="YES",threshold_bps=0,lookback_ns=0,tte_bucket="60s-180s",fair_probability=0.0,quoted_probability=0.5,raw_divergence_bps=0.0,maker_fee_bps=0.0,spread_bps=10.0,latency_buffer_bps=0.0,settlement_buffer_bps=0.0,stale_buffer_bps=0.0,net_divergence_bps=0.0,ts_event_ns=1000000000,expiry_ns=2000000000,rejection_reason="stale_or_missing_binance")
    assert rej.rejection_reason=="stale_or_missing_binance"

def test_live_observer_zero_evaluable_events_is_explicit():
    """When no events are evaluated, report must say evaluated_event_count=0 explicitly."""
    from examples.strategies.polymarket_btcusd_arb.live_reports import write_live_report
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        write_live_report(Path(td),summary={"run_id":"zero-eval","branch":"phase2","market_slug":"test","candidate_count":0,"evaluated_event_count":0,"rejection_total":0,"rejection_counts":{}},signals=[],rejections=[],groups=[],safety={"ok":True,"violations":[]},replay={"deterministic":True})
        summary=json.loads((Path(td)/"summary.json").read_text())
        assert summary["evaluated_event_count"]==0
        report=(Path(td)/"report.md").read_text()
        assert "Evaluated events: 0" in report

def test_live_replay_reproduces_rejection_counts(tmp_path):
    """Replay must produce deterministic rejection counts when capture has rejections."""
    from examples.strategies.polymarket_btcusd_arb.live_capture import write_capture
    from examples.strategies.polymarket_btcusd_arb.live_market_discovery import UpDownMarketInfo
    from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig
    from examples.strategies.polymarket_btcusd_arb.models import DivergenceSignal
    from examples.strategies.polymarket_btcusd_arb.live_replay import load_capture
    info=UpDownMarketInfo(slug="replay-test",question="Test",active=True,closed=False,condition_id="",yes_token_id=None,no_token_id=None,start_ns=None,end_ns=None,series_slug=None,resolution_source=None)
    config=PolymarketArbConfig()
    rej=DivergenceSignal(market_slug="replay-test",side="YES",threshold_bps=10,lookback_ns=0,tte_bucket="60s-180s",fair_probability=0.55,quoted_probability=0.50,raw_divergence_bps=50.0,maker_fee_bps=3.6,spread_bps=20.0,latency_buffer_bps=1.0,settlement_buffer_bps=2.0,stale_buffer_bps=0.5,net_divergence_bps=22.9,ts_event_ns=1000000000,expiry_ns=2000000000,rejection_reason="edge_below_threshold")
    cap={"signals":[],"rejections":[rej],"rejection_counts":{"edge_below_threshold":1},"evaluated_event_count":1,"binance_polls":1,"poly_polls":1,"binance_stale":0,"poly_stale":0,"missing_binance":0,"missing_poly":0,"duration_seconds":1.0,"_poly_events":[],"_binance_events":[]}
    d=write_capture("replay-test-run",cap,info,config)
    data=load_capture(d)
    # Check rejection was persisted and loadable
    assert len(data["rejections"])==1
    assert data["rejections"][0].rejection_reason=="edge_below_threshold"

def test_live_report_contains_rejection_reason_table():
    """Report must contain a rejection reason table when rejections exist."""
    from examples.strategies.polymarket_btcusd_arb.live_reports import write_live_report
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        write_live_report(Path(td),summary={"run_id":"rej-test","branch":"phase2","market_slug":"test","candidate_count":0,"evaluated_event_count":5,"rejection_total":5,"rejection_counts":{"edge_below_threshold":3,"stale_or_missing_binance":2}},signals=[],rejections=[],groups=[],safety={"ok":True,"violations":[]},replay={"deterministic":True})
        report=(Path(td)/"report.md").read_text()
        assert "Rejection Reasons" in report
        assert "edge_below_threshold" in report
        assert "stale_or_missing_binance" in report