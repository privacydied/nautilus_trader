from __future__ import annotations

import json
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import (
    compute_data_corpus_hash,
    sha256_file,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    RESOLUTION_BAR,
    WINDOW_MODE_CAUSAL,
    WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
    OfflineBarRecord,
    OfflinePreparedDataset,
    OfflinePrepareManifest,
    OfflineSourceFile,
    OfflineTradeRecord,
    can_promote_from_window_mode,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
    STATUS_DATA_CORPUS_HASH_MISMATCH,
    STATUS_INSUFFICIENT_HISTORICAL_COVERAGE,
    STATUS_NO_STRESS_WINDOWS,
    STATUS_OFFLINE_STRESS_INDEX_READY,
    STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE,
    StressRuleConfig,
    build_stress_window_index,
    build_stress_window_manifest_payload,
    compute_stress_rule_config_hash,
    index_prepared_manifest_reload,
    write_stress_window_outputs,
)


NS = 1_000_000_000


def _trade(ts_s: int, price: float, source_file: str = "stream") -> OfflineTradeRecord:
    return OfflineTradeRecord(
        venue="binance",
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        timestamp_ns=ts_s * NS,
        price=price,
        size=1.0,
        side="buy",
        trade_id=f"t-{ts_s}",
        source_file=source_file,
        source_kind="binance_spot_agg_trades",
        resolution_type=RESOLUTION_AGG_TRADE,
    )



def _bar(ts_s: int, open_: float, high: float, low: float, close: float, source_file: str = "stream") -> OfflineBarRecord:
    return OfflineBarRecord(
        venue="binance",
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        timestamp_ns=ts_s * NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        trade_count=1,
        source_file=source_file,
        source_kind="binance_spot_klines",
        resolution_type=RESOLUTION_BAR,
    )



def _source_file(tmp_path: Path, *, logical_source_id: str = "stream", resolution_type: str = RESOLUTION_AGG_TRADE, source_kind: str = "binance_spot_agg_trades") -> OfflineSourceFile:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_path / f"{logical_source_id}.csv"
    payload_path.write_text("fixture\n", encoding="utf-8")
    return OfflineSourceFile(
        path=str(payload_path),
        logical_source_id=logical_source_id,
        venue="binance",
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        source_kind=source_kind,
        stream_type="agg_trades" if resolution_type != RESOLUTION_BAR else "bars",
        resolution_type=resolution_type,
        timestamp_unit="ms" if resolution_type != RESOLUTION_BAR else "s",
        expected_start_ns=0,
        expected_end_ns=10_000 * NS,
        file_size_bytes=payload_path.stat().st_size,
        mtime_ns=payload_path.stat().st_mtime_ns,
        file_sha256=sha256_file(payload_path),
        row_count=1,
        data_start_ns=0,
        data_end_ns=10_000 * NS,
    )



def _dataset(tmp_path: Path, *, trades: list[OfflineTradeRecord] | None = None, bars: list[OfflineBarRecord] | None = None, selection_mode: str = WINDOW_MODE_CAUSAL) -> OfflinePreparedDataset:
    source = _source_file(
        tmp_path,
        resolution_type=RESOLUTION_BAR if bars is not None else RESOLUTION_AGG_TRADE,
        source_kind="binance_spot_klines" if bars is not None else "binance_spot_agg_trades",
    )
    source_files = [source]
    return OfflinePreparedDataset(
        source_files=source_files,
        trades_by_stream={source.logical_source_id: trades or []} if trades is not None else {},
        bars_by_stream={source.logical_source_id: bars or []} if bars is not None else {},
        data_corpus_hash=compute_data_corpus_hash(source_files, OFFLINE_DATA_SCHEMA_VERSION),
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        created_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
    )



def _manifest_for_dataset(dataset: OfflinePreparedDataset, source: OfflineSourceFile) -> OfflinePrepareManifest:
    return OfflinePrepareManifest(
        run_id="prepare_run",
        phase="offline_historical_prepare",
        generated_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        precommitment_hash=None,
        data_corpus_hash=dataset.data_corpus_hash,
        source_files=[asdict(source)],
        timestamp_validation={},
        hash_cache_used=True,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1,
        normalized_time_range={},
        stream_counts={},
        resolution_summary={},
        quote_currency_summary={},
        safety="public_data_observer_only",
        forbidden_capabilities_present=False,
        next_phase_allowed=True,
    )



def _range_rule(**overrides) -> StressRuleConfig:
    return StressRuleConfig(
        rule_name="rolling_range_bps",
        rule_version="v1",
        lookback_seconds=600,
        threshold_bps=150.0,
        cooldown_seconds=900,
        pre_window_seconds=60,
        post_window_seconds=300,
        min_required_points=2,
        supported_resolutions=(RESOLUTION_AGG_TRADE, RESOLUTION_BAR),
        trigger_metric="range_bps",
    ).replace(**overrides)



def _return_rule(**overrides) -> StressRuleConfig:
    return StressRuleConfig(
        rule_name="rolling_absolute_return_bps",
        rule_version="v1",
        lookback_seconds=600,
        threshold_bps=100.0,
        cooldown_seconds=900,
        pre_window_seconds=60,
        post_window_seconds=300,
        min_required_points=2,
        supported_resolutions=(RESOLUTION_AGG_TRADE, RESOLUTION_BAR),
        trigger_metric="absolute_return_bps",
    ).replace(**overrides)



def _tick_only_rule(**overrides) -> StressRuleConfig:
    return StressRuleConfig(
        rule_name="tick_only_burst_placeholder",
        rule_version="v1",
        lookback_seconds=60,
        threshold_bps=1.0,
        cooldown_seconds=120,
        pre_window_seconds=30,
        post_window_seconds=60,
        min_required_points=2,
        supported_resolutions=(RESOLUTION_AGG_TRADE,),
        trigger_metric="tick_only_placeholder",
    ).replace(**overrides)



def test_trade_range_rule_detects_causal_trigger(tmp_path: Path):
    dataset = _dataset(
        tmp_path,
        trades=[_trade(0, 100.0), _trade(300, 100.5), _trade(600, 102.0)],
    )
    result = build_stress_window_index(dataset, [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_OFFLINE_STRESS_INDEX_READY
    assert len(result.windows) == 1
    assert result.windows[0].trigger_timestamp_ns == 600 * NS



def test_bar_range_rule_detects_causal_trigger(tmp_path: Path):
    dataset = _dataset(
        tmp_path,
        bars=[
            _bar(0, 100.0, 100.5, 99.8, 100.2),
            _bar(300, 100.2, 100.6, 100.0, 100.3),
            _bar(600, 100.3, 102.5, 99.9, 102.0),
        ],
    )
    result = build_stress_window_index(dataset, [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_OFFLINE_STRESS_INDEX_READY
    assert len(result.windows) == 1
    assert result.windows[0].trigger_timestamp_ns == 600 * NS



def test_absolute_return_rule_detects_causal_trigger(tmp_path: Path):
    dataset = _dataset(
        tmp_path,
        trades=[_trade(0, 100.0), _trade(300, 100.2), _trade(600, 101.2)],
    )
    result = build_stress_window_index(dataset, [_return_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_OFFLINE_STRESS_INDEX_READY
    assert len(result.windows) == 1
    assert result.windows[0].trigger_timestamp_ns == 600 * NS



def test_prefix_invariance_trigger_reproducible_at_t(tmp_path: Path):
    full = [_trade(0, 100.0), _trade(300, 100.5), _trade(600, 102.0), _trade(900, 110.0)]
    prefix = [_trade(0, 100.0), _trade(300, 100.5), _trade(600, 102.0)]
    full_result = build_stress_window_index(_dataset(tmp_path / "full", trades=full), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    prefix_result = build_stress_window_index(_dataset(tmp_path / "prefix", trades=prefix), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert full_result.windows[0].trigger_timestamp_ns == 600 * NS
    assert prefix_result.windows[0].trigger_timestamp_ns == 600 * NS
    assert full_result.windows[0].trigger_value == prefix_result.windows[0].trigger_value



def test_future_only_movement_does_not_create_earlier_trigger(tmp_path: Path):
    full = [_trade(0, 100.0), _trade(300, 100.1), _trade(600, 100.2), _trade(900, 110.0)]
    prefix = [_trade(0, 100.0), _trade(300, 100.1), _trade(600, 100.2)]
    full_result = build_stress_window_index(_dataset(tmp_path / "full", trades=full), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    prefix_result = build_stress_window_index(_dataset(tmp_path / "prefix", trades=prefix), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert full_result.windows[0].trigger_timestamp_ns == 900 * NS
    assert prefix_result.status in {STATUS_NO_STRESS_WINDOWS, STATUS_INSUFFICIENT_HISTORICAL_COVERAGE}
    assert all(window.trigger_timestamp_ns >= 900 * NS for window in full_result.windows)



def test_causal_trigger_uses_only_data_at_or_before_trigger_timestamp(tmp_path: Path):
    early = [_trade(0, 100.0), _trade(300, 100.5), _trade(600, 102.0)]
    late = early + [_trade(1200, 80.0), _trade(1500, 150.0)]
    early_result = build_stress_window_index(_dataset(tmp_path / "early", trades=early), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    late_result = build_stress_window_index(_dataset(tmp_path / "late", trades=late), [_range_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert early_result.windows[0].trigger_timestamp_ns == 600 * NS
    assert late_result.windows[0].trigger_timestamp_ns == 600 * NS
    assert early_result.windows[0].trigger_value == late_result.windows[0].trigger_value



def test_overlapping_triggers_deduped_by_cooldown(tmp_path: Path):
    trades = [_trade(0, 100.0), _trade(300, 102.0), _trade(600, 102.5), _trade(900, 103.0)]
    result = build_stress_window_index(_dataset(tmp_path, trades=trades), [_range_rule(cooldown_seconds=1200)], selection_mode=WINDOW_MODE_CAUSAL)
    assert len(result.windows) == 1
    assert result.manifest_metadata["suppressed_trigger_count"] >= 1



def test_suppressed_trigger_count_recorded(tmp_path: Path):
    trades = [_trade(0, 100.0), _trade(300, 102.0), _trade(600, 102.5)]
    result = build_stress_window_index(_dataset(tmp_path, trades=trades), [_range_rule(cooldown_seconds=1000)], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.manifest_metadata["suppressed_trigger_count"] == 1



def test_deduplication_is_deterministic(tmp_path: Path):
    trades = [_trade(0, 100.0), _trade(300, 102.0), _trade(600, 102.5)]
    ds1 = _dataset(tmp_path / "a", trades=trades)
    ds2 = _dataset(tmp_path / "b", trades=trades)
    r1 = build_stress_window_index(ds1, [_range_rule(cooldown_seconds=1000)], selection_mode=WINDOW_MODE_CAUSAL)
    r2 = build_stress_window_index(ds2, [_range_rule(cooldown_seconds=1000)], selection_mode=WINDOW_MODE_CAUSAL)
    assert [w.window_id for w in r1.windows] == [w.window_id for w in r2.windows]
    assert r1.manifest_metadata["suppressed_trigger_count"] == r2.manifest_metadata["suppressed_trigger_count"]



def test_retrospective_mode_sets_promotion_allowed_false(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC)
    assert result.status == STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY
    assert result.windows[0].promotion_allowed is False



def test_causal_mode_sets_promotion_allowed_true(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.windows[0].promotion_allowed is True



def test_retrospective_windows_cannot_be_promoted():
    assert can_promote_from_window_mode(WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC) is False



def test_bar_only_tick_only_rule_rejected(tmp_path: Path):
    dataset = _dataset(tmp_path, bars=[_bar(0, 100.0, 100.1, 99.9, 100.0), _bar(60, 100.0, 100.2, 99.8, 100.1)])
    result = build_stress_window_index(dataset, [_tick_only_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE
    assert any(item["status"] == STATUS_UNSUPPORTED_RESOLUTION_FOR_STRESS_RULE for item in result.rejected_rules)



def test_unsupported_resolution_does_not_emit_fake_window(tmp_path: Path):
    dataset = _dataset(tmp_path, bars=[_bar(0, 100.0, 100.1, 99.9, 100.0), _bar(60, 100.0, 100.2, 99.8, 100.1)])
    result = build_stress_window_index(dataset, [_tick_only_rule()], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.windows == []



def test_stress_rule_config_hash_changes_when_threshold_changes():
    assert compute_stress_rule_config_hash([_range_rule(threshold_bps=150.0)], WINDOW_MODE_CAUSAL) != compute_stress_rule_config_hash([_range_rule(threshold_bps=200.0)], WINDOW_MODE_CAUSAL)



def test_stress_rule_config_hash_changes_when_lookback_changes():
    assert compute_stress_rule_config_hash([_range_rule(lookback_seconds=600)], WINDOW_MODE_CAUSAL) != compute_stress_rule_config_hash([_range_rule(lookback_seconds=1200)], WINDOW_MODE_CAUSAL)



def test_stress_rule_config_hash_changes_when_cooldown_changes():
    assert compute_stress_rule_config_hash([_range_rule(cooldown_seconds=900)], WINDOW_MODE_CAUSAL) != compute_stress_rule_config_hash([_range_rule(cooldown_seconds=1200)], WINDOW_MODE_CAUSAL)



def test_stress_rule_config_hash_changes_when_selection_mode_changes():
    assert compute_stress_rule_config_hash([_range_rule()], WINDOW_MODE_CAUSAL) != compute_stress_rule_config_hash([_range_rule()], WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC)



def test_window_index_hash_identical_across_identical_runs(tmp_path: Path):
    dataset = _dataset(tmp_path / "ds", trades=[_trade(0, 100.0), _trade(300, 102.0)])
    r1 = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    r2 = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    assert r1.manifest_metadata["window_index_hash"] == r2.manifest_metadata["window_index_hash"]



def test_stress_windows_json_identical_across_identical_runs(tmp_path: Path):
    dataset = _dataset(tmp_path / "ds", trades=[_trade(0, 100.0), _trade(300, 102.0)])
    r1 = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    r2 = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    out1 = write_stress_window_outputs(r1, tmp_path / "out1", overwrite=False)
    out2 = write_stress_window_outputs(r2, tmp_path / "out2", overwrite=False)
    assert json.loads(out1["stress_windows_path"].read_text(encoding="utf-8")) == json.loads(out2["stress_windows_path"].read_text(encoding="utf-8"))



def test_window_index_hash_changes_when_windows_change(tmp_path: Path):
    dataset1 = _dataset(tmp_path / "a", trades=[_trade(0, 100.0), _trade(300, 102.0)])
    dataset2 = _dataset(tmp_path / "b", trades=[_trade(0, 100.0), _trade(300, 103.0)])
    r1 = build_stress_window_index(dataset1, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    r2 = build_stress_window_index(dataset2, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    assert r1.manifest_metadata["window_index_hash"] != r2.manifest_metadata["window_index_hash"]



def test_manifest_carries_data_corpus_hash(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    manifest = build_stress_window_manifest_payload(result)
    assert manifest["data_corpus_hash"] == dataset.data_corpus_hash



def test_manifest_carries_nullable_precommitment_hash(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    manifest = build_stress_window_manifest_payload(result)
    assert "precommitment_hash" in manifest
    assert manifest["precommitment_hash"] is None



def test_manifest_carries_input_prepare_manifest_hash(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    manifest = build_stress_window_manifest_payload(result)
    assert manifest["input_prepare_manifest_hash"] is not None



def test_reload_path_rejects_changed_source_data(tmp_path: Path):
    csv_path = tmp_path / "source.csv"
    csv_path.write_text("0,100.0,1,0,0,1704067200000,False\n1,102.0,1,0,0,1704067500000,False\n", encoding="utf-8")
    source_config = {
        "sources": [{
            "path": str(csv_path),
            "logical_source_id": "stream",
            "venue": "binance",
            "symbol": "BTC/USDT",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "source_kind": "binance_spot_agg_trades",
            "stream_type": "agg_trades",
            "resolution_type": RESOLUTION_AGG_TRADE,
            "timestamp_unit": "ms",
            "expected_start": "2024-01-01T00:00:00Z",
            "expected_end": "2024-01-02T00:00:00Z",
        }]
    }
    source_config_path = tmp_path / "offline_sources.json"
    source_config_path.write_text(json.dumps(source_config), encoding="utf-8")

    source = _source_file(tmp_path, logical_source_id="stream")
    source = OfflineSourceFile(**{**asdict(source), "path": str(csv_path), "file_size_bytes": csv_path.stat().st_size, "mtime_ns": csv_path.stat().st_mtime_ns, "file_sha256": sha256_file(csv_path)})
    dataset = OfflinePreparedDataset(
        source_files=[source],
        trades_by_stream={"stream": [_trade(0, 100.0), _trade(300, 102.0)]},
        bars_by_stream={},
        data_corpus_hash=compute_data_corpus_hash([source], OFFLINE_DATA_SCHEMA_VERSION),
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        created_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
    )
    manifest = _manifest_for_dataset(dataset, source)
    manifest_path = tmp_path / "offline_prepare_manifest.json"
    manifest_path.write_text(json.dumps({
        "run_id": manifest.run_id,
        "phase": manifest.phase,
        "generated_at_utc": manifest.generated_at_utc,
        "git_sha": manifest.git_sha,
        "schema_version": manifest.schema_version,
        "precommitment_hash": manifest.precommitment_hash,
        "data_corpus_hash": manifest.data_corpus_hash,
        "source_files": manifest.source_files,
        "timestamp_validation": manifest.timestamp_validation,
        "hash_cache_used": manifest.hash_cache_used,
        "hash_cache_entries_reused": manifest.hash_cache_entries_reused,
        "hash_cache_entries_recomputed": manifest.hash_cache_entries_recomputed,
        "normalized_time_range": manifest.normalized_time_range,
        "stream_counts": manifest.stream_counts,
        "resolution_summary": manifest.resolution_summary,
        "quote_currency_summary": manifest.quote_currency_summary,
        "safety": manifest.safety,
        "forbidden_capabilities_present": manifest.forbidden_capabilities_present,
        "next_phase_allowed": manifest.next_phase_allowed,
    }, sort_keys=True), encoding="utf-8")

    csv_path.write_text("0,100.0,1,0,0,1704067200000,False\n1,104.0,1,0,0,1704067500000,False\n", encoding="utf-8")
    result = index_prepared_manifest_reload(
        prepared_manifest_path=manifest_path,
        source_config_path=source_config_path,
        stress_rules=[_range_rule(lookback_seconds=300)],
        selection_mode=WINDOW_MODE_CAUSAL,
    )
    assert result.status == STATUS_DATA_CORPUS_HASH_MISMATCH



def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    dataset = _dataset(tmp_path / "ds", trades=[_trade(0, 100.0), _trade(300, 102.0)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300)], selection_mode=WINDOW_MODE_CAUSAL)
    out = tmp_path / "out"
    write_stress_window_outputs(result, out, overwrite=False)
    with pytest.raises(FileExistsError):
        write_stress_window_outputs(result, out, overwrite=False)



def test_no_windows_returns_no_stress_windows(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0), _trade(300, 100.1)])
    result = build_stress_window_index(dataset, [_range_rule(lookback_seconds=300, threshold_bps=500.0)], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_NO_STRESS_WINDOWS



def test_insufficient_points_returns_insufficient_coverage(tmp_path: Path):
    dataset = _dataset(tmp_path, trades=[_trade(0, 100.0)])
    result = build_stress_window_index(dataset, [_range_rule(min_required_points=2)], selection_mode=WINDOW_MODE_CAUSAL)
    assert result.status == STATUS_INSUFFICIENT_HISTORICAL_COVERAGE



def test_safety_no_forbidden_trading_capability_imports():
    root = Path("examples/strategies/venue_agnostic_signal_observer")
    targets = [
        root / "offline_stress_windows.py",
        root / "run_offline_stress_window_index.py",
    ]
    forbidden = [
        "OrderFactory",
        "submit_order",
        "TradingNode",
        "LiveNode",
        "private_key",
        "wallet",
        "signing",
        "exchange account",
        "bot authorization",
    ]
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source
