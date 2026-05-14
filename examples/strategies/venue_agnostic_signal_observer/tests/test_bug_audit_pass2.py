"""Regression tests for bug audit pass 2 numerical robustness fixes."""

import json
import math

import pytest


def _signal():
    from ..models import SignalEvent

    return SignalEvent(
        signal_id="s",
        timestamp=0.0,
        source_venue="A",
        source_instrument="BTC/USDT",
        target_venue="B",
        target_instrument="BTC/USD",
        signal_type="test",
        direction="long",
        strength=1.0,
    )


def test_forward_returns_reject_zero_entry_price():
    from ..config import FeeModel, Horizon
    from ..forward_returns import evaluate_signal

    results = evaluate_signal(_signal(), [0.0, 10.0], [0.0, 100.0], [Horizon("10s", 10.0)], FeeModel())

    assert len(results) == 1
    assert results[0].valid is False
    assert results[0].rejection_reason == "zero_entry_price"


def test_forward_returns_reject_non_finite_prices():
    from ..config import FeeModel, Horizon
    from ..forward_returns import evaluate_signal

    results = evaluate_signal(_signal(), [0.0, 10.0], [100.0, float("nan")], [Horizon("10s", 10.0)], FeeModel())

    assert len(results) == 1
    assert results[0].valid is False
    assert results[0].rejection_reason == "non_finite_forward_price"


def test_observer_summary_filters_nan_and_interpolates_percentiles():
    from ..config import FeeModel, Horizon, ObserverConfig
    from ..models import ForwardReturnResult
    from ..observer import _build_summary

    cfg = ObserverConfig(horizons=[Horizon("h", 1.0)], fee_model=FeeModel())
    sig = _signal()
    values = [0.0, 10.0, 20.0]
    results = [
        ForwardReturnResult(
            signal_id=f"r{i}", signal_timestamp=0.0, source_venue="A", source_instrument="S",
            target_venue="B", target_instrument="T", signal_type="x", direction="long", strength=1.0,
            horizon="h", raw_return_bps=v, net_return_bps=v, valid=True,
        )
        for i, v in enumerate(values)
    ]
    results.append(ForwardReturnResult(
        signal_id="nan", signal_timestamp=0.0, source_venue="A", source_instrument="S",
        target_venue="B", target_instrument="T", signal_type="x", direction="long", strength=1.0,
        horizon="h", raw_return_bps=float("nan"), net_return_bps=float("nan"), valid=True,
    ))

    summary = _build_summary([sig], results, cfg, 0.0, 1.0, False)
    hs = summary.results_by_horizon[0]

    assert hs["valid_count"] == 4
    assert hs["mean_net_return_bps"] == pytest.approx(10.0)
    assert hs["p25"] == pytest.approx(5.0)
    assert hs["p50"] == pytest.approx(10.0)
    assert hs["p75"] == pytest.approx(15.0)
    assert hs["p90"] == pytest.approx(18.0)
    assert math.isfinite(hs["mean_net_return_bps"])


def test_load_signals_handles_bad_strength_and_empty_metadata_column(tmp_path):
    from ..signals import load_signals_from_csv

    path = tmp_path / "signals.csv"
    path.write_text(
        "timestamp,source_venue,source_instrument,target_venue,target_instrument,direction,signal_type,strength,metadata\n"
        "1,A,S,B,T,long,manual,N/A,\n"
    )

    events = load_signals_from_csv(str(path))

    assert len(events) == 1
    assert events[0].strength == 0.0
    assert events[0].metadata is not None
    assert "raw" in events[0].metadata


def test_align_venues_does_not_backfill_before_first_data():
    from ..data_adapters import align_venues

    ts, src, tgt = align_venues([10.0], [100.0], [0.0, 10.0], [50.0, 55.0])

    assert ts == [0.0, 10.0]
    assert src[0] is None
    assert src[1] == 100.0
    assert tgt == [50.0, 55.0]


def test_stream_health_zero_min_does_not_use_falsy_check():
    from ..cross_asset_impulse import StreamHealth

    health = StreamHealth("V", "S", price_min=0.0, price_max=100.0)

    assert health.range_bps == 0.0
    health.price_min = 1.0
    assert health.range_bps == pytest.approx(990000.0)


def test_cross_asset_verdict_handles_none_gate_mean():
    from ..cross_asset_impulse import PairResult, _determine_verdict

    pair = PairResult(
        pair_key="A:BTC->B:ETH",
        overlap={"overlap_duration_ms": 1000, "source_range_bps": 10.0, "target_range_bps": 10.0},
        signal_count=1,
        long_executable_count=1,
        diagnostic_only_count=0,
        valid_returns=[],
        valid_long_executable_returns=[],
        valid_diagnostic_returns=[],
        candidate_gate={"candidate": False, "mean_net_return_bps": None},
    )

    verdict, _ = _determine_verdict(
        pairs_with_overlap=1,
        pairs_with_sufficient=1,
        pairs_with_events=1,
        total_long=1,
        total_diag=0,
        total_signals=1,
        has_failed_subscription=False,
        has_zero_tick=False,
        any_data_issue=False,
        candidate_pairs=[],
        pair_results=[pair],
        min_events=1,
        min_source_range_bps=1.0,
        min_target_range_bps=1.0,
    )

    assert verdict == "REJECTED"


def test_cross_asset_report_formats_none_values(tmp_path):
    from ..cross_asset_impulse import CrossAssetVerdict, generate_markdown_report

    v = CrossAssetVerdict(
        verdict="NEEDS_MORE_DATA",
        reasons=[],
        total_signal_events=0,
        total_long_executable=0,
        total_diagnostic_only=0,
        pairs_evaluated=1,
        pairs_with_overlap=1,
        pairs_with_sufficient_data=1,
        pairs_with_events=1,
        source_venues=["A"],
        source_symbols=["BTC/USD"],
        target_venues=["B"],
        target_symbols=["ETH/USD"],
        best_pair="A:BTC->B:ETH",
        best_pair_mean_net=None,
        worst_pair="A:BTC->B:ETH",
        worst_pair_mean_net=None,
        best_long_executable_group="A:BTC->B:ETH",
        best_long_executable_mean_net=None,
        best_diagnostic_group="A:BTC->B:ETH",
        best_diagnostic_mean_net=None,
    )

    path = generate_markdown_report(v, 1.0, 0.0, 0.0, 0.0, ["x"], [1000], [1000], 0, 1000, str(tmp_path), 1)

    content = (tmp_path / "cross_asset_report.md").read_text()
    assert "N/A bps" in content
    assert path.startswith("# Cross-asset Spot Impulse")


def test_data_fetcher_empty_csv_has_header(tmp_path):
    from ..data_fetcher import write_ohlc_csv

    path = tmp_path / "empty.csv"
    write_ohlc_csv([], str(path))

    assert path.read_text().strip() == "timestamp,open,high,low,close,volume"



def test_run_derivatives_spot_report_top_groups_filters_non_finite_group_means(tmp_path):
    from ..run_derivatives_spot_lead_lag import EvalSummary, _write_md

    summary = EvalSummary(
        capture_dir="capture",
        capture_mode="FULL_ACTIVE",
        verdict="NEEDS_MORE_DATA",
        all_in_cost_bps=50.0,
        results_by_group=[
            {"signal_type": "poison", "lookback_ms": 1, "horizon_ms": 1, "mean_net_bps": float("nan"), "valid_count": 99},
            {"signal_type": "good", "lookback_ms": 1, "horizon_ms": 1, "mean_net_bps": 5.0, "valid_count": 1},
        ],
        best_group={"signal_type": "good", "lookback_ms": 1, "horizon_ms": 1, "mean_net_bps": 5.0, "valid_count": 1},
    )

    _write_md(summary, tmp_path)
    report = (tmp_path / "report.md").read_text()

    assert "poison" not in report
    assert "good" in report
    assert "5.0" in report


def test_run_mcpt_export_skip_reason_ignores_nan_group_means(tmp_path, capsys):
    from ..run_mcpt_export import main

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "summary.json").write_text(json.dumps({
        "results_by_group": [
            {"mean_net_bps": float("nan"), "valid_count": 100, "candidate": False},
            {"mean_net_bps": -50.0, "valid_count": 100, "candidate": False},
        ]
    }))

    old_argv = __import__("sys").argv
    try:
        __import__("sys").argv = ["run_mcpt_export", "--report-dir", str(report_dir)]
        main()
    finally:
        __import__("sys").argv = old_argv

    out = capsys.readouterr().out
    assert "nan" not in out.lower()
    assert "-50.0" in out
