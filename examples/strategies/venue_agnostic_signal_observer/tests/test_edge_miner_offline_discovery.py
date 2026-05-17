"""Tests for the offline Edge Miner discovery runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.run_edge_miner_offline_discovery import (
    OfflineDiscoveryConfig,
    build_mvp_grid,
    run_offline_discovery,
)
from examples.strategies.venue_agnostic_signal_observer.stress_corpus import build_stress_corpus
from examples.strategies.venue_agnostic_signal_observer.stress_labels import build_stress_labels
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite

_NS = 1_000_000_000

def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class TestOfflineEdgeMinerRunner:
    def test_refuses_live_or_network_mode(self, tmp_path: Path):
        config = OfflineDiscoveryConfig(output_root=tmp_path, run_mode="live")
        with pytest.raises(ValueError, match="offline"):
            run_offline_discovery(config)

    def test_records_grid_hash_and_writes_expected_artifacts(self, tmp_path: Path):
        config = OfflineDiscoveryConfig(
            output_root=tmp_path,
            data_dirs=(tmp_path / "missing",),
            force_synthetic=True,
            timestamp="20260101_000000",
        )
        result = run_offline_discovery(config)
        report_dir = result.report_dir
        expected = {
            "grid_spec.json",
            "grid_manifest.json",
            "discovery_results.jsonl",
            "candidate_cards.jsonl",
            "validator_evidence.jsonl",
            "ledger_events.jsonl",
            "corpus_summary.json",
            "shadow_summary.json",
            "final_report.md",
            "stress_labels.jsonl",
            "stress_label_summary.json",
        }
        assert expected.issubset({p.name for p in report_dir.iterdir()})
        manifest = _read_json(report_dir / "grid_manifest.json")
        assert manifest["grid_hash"] == result.grid_hash
        assert manifest["raw_cell_count"] == result.raw_cell_count
        assert len(result.grid_hash) == 24

    def test_handles_synthetic_fallback(self, tmp_path: Path):
        config = OfflineDiscoveryConfig(
            output_root=tmp_path,
            data_dirs=(tmp_path / "no_data_here",),
            timestamp="20260101_000001",
        )
        result = run_offline_discovery(config)
        assert result.used_synthetic is True
        assert result.corpus_status == "NO_REAL_CORPUS_AVAILABLE"
        assert result.stress_label_status == "NO_STRESS_LABELS_DIAGNOSTIC"
        assert result.stress_label_count == 0
        stress_summary = _read_json(result.report_dir / "stress_label_summary.json")
        assert stress_summary["label_count"] == 0
        assert (result.report_dir / "stress_labels.jsonl").read_text() == ""
        smoke = _read_json(result.report_dir / "synthetic_smoke_summary.json")
        assert smoke["assertions"]["null_does_not_promote"] is True
        assert smoke["assertions"]["planted_scores_better_than_null"] is True
        assert smoke["assertions"]["untradeable_detectable_but_economically_rejected"] is True
        assert smoke["assertions"]["decaying_caught_by_nonstationarity"] is True

    def test_handles_zero_candidates(self, tmp_path: Path):
        config = OfflineDiscoveryConfig(
            output_root=tmp_path,
            data_dirs=(tmp_path / "missing",),
            force_synthetic=True,
            initial_candidate_mean_floor=999.0,
            timestamp="20260101_000002",
        )
        result = run_offline_discovery(config)
        assert result.candidate_counts["candidate_cards"] == 0
        assert result.candidate_counts["validator_survivors"] == 0
        assert result.candidate_counts["final_candidates"] == 0
        assert (result.report_dir / "final_report.md").read_text().find("final_candidate_count: 0") >= 0

    def test_does_not_emit_trade_ready(self, tmp_path: Path):
        config = OfflineDiscoveryConfig(
            output_root=tmp_path,
            data_dirs=(tmp_path / "missing",),
            force_synthetic=True,
            timestamp="20260101_000003",
        )
        result = run_offline_discovery(config)
        for artifact in result.report_dir.iterdir():
            if artifact.suffix in {".json", ".jsonl", ".md"}:
                assert "TRADE_READY" not in artifact.read_text()

    def test_mvp_grid_shape_is_frozen(self):
        grid = build_mvp_grid(cost_floor=0.005, regime_status="NO_STRESS_LABELS_DIAGNOSTIC")
        assert len(grid.cells) == 768
        assert grid.grid_hash == build_mvp_grid(
            cost_floor=0.005,
            regime_status="NO_STRESS_LABELS_DIAGNOSTIC",
        ).grid_hash

    def test_stress_labels_are_deterministic_from_local_ticks(self):
        ticks = [
            TradeTickLite(ts_event=0, venue="local", symbol="BTC-USD", price=100.0, size=1.0, side="buy"),
            TradeTickLite(ts_event=30 * _NS, venue="local", symbol="BTC-USD", price=100.4, size=1.0, side="buy"),
            TradeTickLite(ts_event=60 * _NS, venue="local", symbol="BTC-USD", price=100.8, size=1.0, side="buy"),
            TradeTickLite(ts_event=90 * _NS, venue="local", symbol="BTC-USD", price=100.9, size=1.0, side="buy"),
        ]
        first = build_stress_labels({"BTC": ticks})
        second = build_stress_labels({"BTC": list(reversed(ticks))})

        assert first.status == "STRESS_LABELS_AVAILABLE"
        assert first.label_count >= 1
        assert [label.to_dict() for label in first.labels] == [label.to_dict() for label in second.labels]
        label = first.labels[0]
        assert label.source_asset == "BTC"
        assert label.trigger_reason in {"ABS_30S_MOVE", "ABS_60S_MOVE", "ROLLING_RANGE_60S"}
        assert abs(label.impulse_bps) >= 30.0
        assert label.label_version == "stress_label.v1"

    def test_stress_labels_report_insufficient_data_without_faking_labels(self):
        ticks = [
            TradeTickLite(ts_event=0, venue="local", symbol="ETH-USD", price=100.0, size=1.0, side="buy"),
            TradeTickLite(ts_event=10 * _NS, venue="local", symbol="ETH-USD", price=110.0, size=1.0, side="buy"),
        ]
        result = build_stress_labels({"ETH": ticks})

        assert result.status == "INSUFFICIENT_STRESS_DATA"
        assert result.label_count == 0
        assert result.labels == ()

    def test_runner_writes_stress_labels_when_local_corpus_has_stress(self, tmp_path: Path):
        data_dir = tmp_path / "captures" / "stress_capture"
        data_dir.mkdir(parents=True)
        rows = [
            TradeTickLite(ts_event=0, venue="local", symbol="BTC-USD", price=100.0, size=1.0, side="buy"),
            TradeTickLite(ts_event=30 * _NS, venue="local", symbol="BTC-USD", price=100.4, size=1.0, side="buy"),
            TradeTickLite(ts_event=60 * _NS, venue="local", symbol="BTC-USD", price=100.8, size=1.0, side="buy"),
            TradeTickLite(ts_event=390 * _NS, venue="local", symbol="BTC-USD", price=100.9, size=1.0, side="buy"),
        ]
        (data_dir / "trades_local_BTC-USD_1.jsonl").write_text(
            "".join(tick.to_json() + "\n" for tick in rows)
        )

        config = OfflineDiscoveryConfig(
            output_root=tmp_path / "reports",
            data_dirs=(tmp_path / "captures",),
            timestamp="20260101_000004",
        )
        result = run_offline_discovery(config)

        assert result.stress_label_status == "STRESS_LABELS_AVAILABLE"
        assert result.stress_label_count >= 1
        assert (result.report_dir / "stress_labels.jsonl").read_text().strip()
        run_summary = _read_json(result.report_dir / "run_summary.json")
        assert run_summary["stress_label_status"] == "STRESS_LABELS_AVAILABLE"
        assert run_summary["stress_label_count"] == result.stress_label_count
        assert "stress_label_count:" in (result.report_dir / "final_report.md").read_text()

    def test_runner_consumes_valid_stress_corpus_manifest(self, tmp_path: Path):
        data_dir = tmp_path / "captures" / "stress_capture"
        data_dir.mkdir(parents=True)
        source_rows = [
            TradeTickLite(ts_event=0, venue="local", symbol="BTC-USD", price=100.0, size=1.0, side="buy"),
            TradeTickLite(ts_event=30 * _NS, venue="local", symbol="BTC-USD", price=100.4, size=1.0, side="buy"),
            TradeTickLite(ts_event=60 * _NS, venue="local", symbol="BTC-USD", price=100.8, size=1.0, side="buy"),
            TradeTickLite(ts_event=390 * _NS, venue="local", symbol="BTC-USD", price=100.9, size=1.0, side="buy"),
        ]
        target_rows = [
            TradeTickLite(ts_event=0, venue="local", symbol="SOL-USD", price=50.0, size=1.0, side="buy"),
            TradeTickLite(ts_event=390 * _NS, venue="local", symbol="SOL-USD", price=50.1, size=1.0, side="sell"),
        ]
        (data_dir / "trades_local_BTC-USD_1.jsonl").write_text("".join(t.to_json() + "\n" for t in source_rows))
        (data_dir / "trades_local_SOL-USD_1.jsonl").write_text("".join(t.to_json() + "\n" for t in target_rows))
        corpus_result = build_stress_corpus(
            input_dirs=(tmp_path / "captures",),
            output_dir=tmp_path / "corpus",
            created_at_utc="2026-01-01T00:00:00+00:00",
        )

        config = OfflineDiscoveryConfig(
            output_root=tmp_path / "reports",
            data_dirs=(tmp_path / "captures",),
            timestamp="20260101_000005",
            stress_corpus_manifest=corpus_result.manifest_path,
        )
        result = run_offline_discovery(config)

        assert result.stress_label_status == "STRESS_LABELS_AVAILABLE"
        assert result.stress_corpus_hash == corpus_result.corpus_hash
        summary = _read_json(result.report_dir / "run_summary.json")
        assert summary["stress_corpus_hash"] == corpus_result.corpus_hash
        manifest = _read_json(result.report_dir / "grid_manifest.json")
        assert manifest["regime_filter"] == "stress_only"

    def test_runner_refuses_invalid_stress_corpus_manifest(self, tmp_path: Path):
        bad_manifest = tmp_path / "bad_manifest.json"
        bad_manifest.write_text('{"status":"NO_STRESS_WINDOWS_FOUND"}\n')
        config = OfflineDiscoveryConfig(
            output_root=tmp_path / "reports",
            data_dirs=(tmp_path / "missing",),
            force_synthetic=True,
            timestamp="20260101_000006",
            stress_corpus_manifest=bad_manifest,
        )

        with pytest.raises(ValueError):
            run_offline_discovery(config)

    def test_runner_allows_diagnostic_underpowered_stress_corpus_explicitly(self, tmp_path: Path):
        manifest = tmp_path / "accumulated_manifest.json"
        manifest.write_text(json.dumps({
            "corpus_id": "stress_beta_lag_v1_accumulated",
            "corpus_version": "1.0.0",
            "accumulator_version": "stress_corpus_accumulator.v1",
            "label_version": "stress_label.v1",
            "source_assets": ["BTC", "ETH"],
            "target_assets": ["SOL", "LINK", "DOGE", "AVAX"],
            "stress_window_count": 1,
            "usable_window_count": 1,
            "corpus_hash": "underpowered123",
            "status": "ACCUMULATING",
            "ready_for_rerun": False,
            "stress_windows": [{"usable_for_edge_miner": True}],
        }) + "\n")
        config = OfflineDiscoveryConfig(
            output_root=tmp_path / "reports",
            data_dirs=(tmp_path / "missing",),
            force_synthetic=True,
            timestamp="20260101_000007",
            stress_corpus_manifest=manifest,
            allow_diagnostic_stress_corpus=True,
        )

        result = run_offline_discovery(config)

        assert result.stress_corpus_hash == "underpowered123"
        assert result.stress_window_count == 1
        summary = _read_json(result.report_dir / "run_summary.json")
        assert summary["stress_corpus_status"] == "ACCUMULATING"
