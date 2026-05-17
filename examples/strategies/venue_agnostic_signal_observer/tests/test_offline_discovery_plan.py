from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import compute_data_corpus_hash
from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
    WINDOW_MODE_CAUSAL,
    WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
    OfflinePrepareManifest,
    OfflineSourceFile,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
    STATUS_OFFLINE_STRESS_INDEX_READY,
    STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    OfflineStressWindow,
)
from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import (
    DISCOVERY_SCHEMA_VERSION,
    STATUS_INPUT_HASH_MISMATCH,
    STATUS_INVALID_DISCOVERY_CONFIG,
    STATUS_NO_PROMOTABLE_WINDOWS,
    STATUS_NO_SUPPORTED_HYPOTHESIS_FAMILIES,
    STATUS_OFFLINE_DISCOVERY_PLAN_READY,
    STATUS_UNUSABLE_STRESS_INDEX,
    STATUS_WINDOW_INDEX_HASH_MISMATCH,
    build_offline_discovery_plan,
    build_offline_discovery_plan_manifest_payload,
    compute_discovery_config_hash,
    load_discovery_config,
    write_offline_discovery_plan_outputs,
)

NS = 1_000_000_000


def _source_file(
    tmp_path: Path,
    *,
    logical_source_id: str = "kraken-btcusd-1m",
    venue: str = "kraken",
    symbol: str = "BTC/USD",
    base_asset: str = "BTC",
    quote_asset: str = "USD",
    resolution_type: str = RESOLUTION_BAR,
) -> OfflineSourceFile:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_path / f"{logical_source_id}.csv"
    payload_path.write_text("fixture\n", encoding="utf-8")
    return OfflineSourceFile(
        path=str(payload_path),
        logical_source_id=logical_source_id,
        venue=venue,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        source_kind="fixture",
        stream_type="bars" if resolution_type == RESOLUTION_BAR else "trades",
        resolution_type=resolution_type,
        timestamp_unit="ms",
        expected_start_ns=0,
        expected_end_ns=10_000 * NS,
        file_size_bytes=payload_path.stat().st_size,
        mtime_ns=payload_path.stat().st_mtime_ns,
        file_sha256="abc123",
        row_count=1,
        data_start_ns=0,
        data_end_ns=10_000 * NS,
    )


def _prepare_manifest(tmp_path: Path, *, data_corpus_hash: str | None = None, precommitment_hash: str | None = None) -> OfflinePrepareManifest:
    sources = [
        _source_file(tmp_path / "kraken", logical_source_id="kraken-btcusd-1m", venue="kraken", symbol="BTC/USD"),
        _source_file(tmp_path / "kraken", logical_source_id="kraken-btcusdt-1m", venue="kraken", symbol="BTC/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-btcusdt-1m", venue="binance", symbol="BTC/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-ethusdt-1m", venue="binance", symbol="ETH/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-solusdt-1m", venue="binance", symbol="SOL/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-linkusdt-1m", venue="binance", symbol="LINK/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-dogeusdt-1m", venue="binance", symbol="DOGE/USDT"),
        _source_file(tmp_path / "binance", logical_source_id="binance-avaxusdt-1m", venue="binance", symbol="AVAX/USDT"),
        _source_file(tmp_path / "coinbase", logical_source_id="coinbase-btcusd-1m", venue="coinbase", symbol="BTC/USD"),
    ]
    corpus_hash = data_corpus_hash or compute_data_corpus_hash(sources, OFFLINE_DATA_SCHEMA_VERSION)
    return OfflinePrepareManifest(
        run_id="prepare_run",
        phase="offline_historical_prepare",
        generated_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        precommitment_hash=precommitment_hash,
        data_corpus_hash=corpus_hash,
        source_files=[asdict(source) for source in sources],
        timestamp_validation={},
        hash_cache_used=True,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1,
        normalized_time_range={},
        stream_counts={},
        resolution_summary={RESOLUTION_BAR: len(sources)},
        quote_currency_summary={"USD": 3, "USDT": 6},
        safety="public_data_observer_only",
        forbidden_capabilities_present=False,
        next_phase_allowed=True,
    )


def _window(
    *,
    window_id: str,
    trigger_timestamp_ns: int,
    source_venue: str = "kraken",
    source_symbol: str = "BTC/USD",
    resolution_type: str = RESOLUTION_BAR,
    data_corpus_hash: str = "corpus_hash",
    precommitment_hash: str | None = None,
    selection_mode: str = WINDOW_MODE_CAUSAL,
    promotion_allowed: bool = True,
) -> OfflineStressWindow:
    return OfflineStressWindow(
        window_id=window_id,
        selection_mode=selection_mode,
        promotion_allowed=promotion_allowed,
        source_venue=source_venue,
        source_symbol=source_symbol,
        base_asset="BTC",
        quote_asset="USD" if "USD" in source_symbol and "USDT" not in source_symbol else "USDT",
        trigger_timestamp_ns=trigger_timestamp_ns,
        window_start_ns=trigger_timestamp_ns,
        window_end_ns=trigger_timestamp_ns,
        pre_window_start_ns=max(0, trigger_timestamp_ns - 60 * NS),
        post_window_end_ns=trigger_timestamp_ns + 300 * NS,
        rule_name="rolling_range_bps",
        rule_version="v1",
        trigger_metric="range_bps",
        trigger_value=200.0,
        trigger_threshold=150.0,
        lookback_ns=600 * NS,
        cooldown_ns=900 * NS,
        resolution_type=resolution_type,
        data_corpus_hash=data_corpus_hash,
        precommitment_hash=precommitment_hash,
        metadata={"logical_source_id": f"{source_venue}-{source_symbol}"},
    )


def _stress_payload(windows: list[OfflineStressWindow]) -> dict:
    return {
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "status": STATUS_OFFLINE_STRESS_INDEX_READY,
        "windows": [asdict(window) for window in windows],
    }


def _stress_manifest(
    windows: list[OfflineStressWindow],
    *,
    data_corpus_hash: str,
    precommitment_hash: str | None = None,
    status: str = STATUS_OFFLINE_STRESS_INDEX_READY,
    stress_rule_config_hash: str = "stress_rule_hash",
) -> dict:
    payload = _stress_payload(windows)
    from examples.strategies.venue_agnostic_signal_observer.offline_discovery_plan import compute_window_index_hash

    return {
        "run_id": "stress_run",
        "phase": "offline_stress_window_index",
        "generated_at_utc": "2024-01-01T00:00:00+00:00",
        "git_sha": "deadbeef",
        "schema_version": OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
        "data_corpus_hash": data_corpus_hash,
        "precommitment_hash": precommitment_hash,
        "stress_rule_config_hash": stress_rule_config_hash,
        "window_index_hash": compute_window_index_hash(payload),
        "status": status,
        "promotion_allowed": status == STATUS_OFFLINE_STRESS_INDEX_READY,
        "window_count": len(windows),
        "selection_mode": WINDOW_MODE_CAUSAL if status == STATUS_OFFLINE_STRESS_INDEX_READY else WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
        "safety": "public_data_observer_only",
    }


def _discovery_config_dict() -> dict:
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "train_holdout": {
            "train_fraction": 0.7,
            "min_train_windows": 1,
            "min_holdout_windows": 1,
        },
        "families": {
            "family_1_same_venue_quote_basis": {
                "enabled": True,
                "source_venue": "kraken",
                "source_symbols": ["BTC/USD", "BTC/USDT"],
                "horizons_ms": [60000, 300000],
                "lookbacks_ms": [60000, 300000],
                "required_resolution": RESOLUTION_BAR,
                "fee_slippage_mismatch_assumptions": {
                    "fees_bps": 40.0,
                    "slippage_bps": 5.0,
                    "quote_mismatch_bps": 5.0,
                },
                "min_events": 2,
            },
            "family_2_cross_asset_stress_beta_lag": {
                "enabled": True,
                "source_symbols": ["BTC/USDT", "ETH/USDT"],
                "target_symbols": ["SOL/USDT", "LINK/USDT", "DOGE/USDT", "AVAX/USDT"],
                "signal_variants": ["signed_imbalance", "notional_burst"],
                "horizons_ms": [60000],
                "lookbacks_ms": [30000],
                "required_resolution": RESOLUTION_BAR,
                "fee_slippage_mismatch_assumptions": {
                    "fees_bps": 50.0,
                    "slippage_bps": 10.0,
                    "quote_mismatch_bps": 5.0,
                },
                "min_events": 2,
            },
            "family_3_usd_reference_translation_lag": {
                "enabled": True,
                "usd_reference_venue_symbols": [
                    {"venue": "coinbase", "symbol": "BTC/USD"},
                    {"venue": "kraken", "symbol": "BTC/USD"},
                ],
                "usdt_venue_symbols": [
                    {"venue": "binance", "symbol": "BTC/USDT"},
                ],
                "horizons_ms": [120000],
                "lookbacks_ms": [60000],
                "required_resolution": RESOLUTION_BAR,
                "latency_gate_required": True,
                "fee_slippage_mismatch_assumptions": {
                    "fees_bps": 45.0,
                    "slippage_bps": 5.0,
                    "quote_mismatch_bps": 5.0,
                },
                "min_events": 2,
            },
            "family_4_stablecoin_quote_regime_conditioning": {
                "enabled": True,
                "conditioning_only": True,
                "basis_source": {
                    "venue": "kraken",
                    "symbols": ["BTC/USD", "BTC/USDT"],
                },
                "basis_threshold_bps": 10.0,
                "target_families": [
                    "family_1_same_venue_quote_basis",
                    "family_2_cross_asset_stress_beta_lag",
                    "family_3_usd_reference_translation_lag",
                ],
                "standalone": False,
            },
        },
    }


def _build_ready_plan(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [
        _window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash),
        _window(window_id="w2", trigger_timestamp_ns=200 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash),
        _window(window_id="w3", trigger_timestamp_ns=300 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash),
    ]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    return build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )


def test_rejects_prepare_stress_data_corpus_hash_mismatch(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path, data_corpus_hash="prepare_hash")
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash="prepare_hash")]
    stress_manifest = _stress_manifest(windows, data_corpus_hash="other_hash")
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert plan.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_prepare_stress_precommitment_hash_mismatch_when_both_non_null(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path, precommitment_hash="aaa")
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash, precommitment_hash="bbb")]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash, precommitment_hash="bbb")
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert plan.status == STATUS_INPUT_HASH_MISMATCH


def test_rejects_supplied_stress_windows_json_if_window_index_hash_does_not_match_manifest(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _stress_payload(windows)
    payload["windows"][0]["window_id"] = "tampered"
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=payload,
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert plan.status == STATUS_WINDOW_INDEX_HASH_MISMATCH


def test_rejects_unusable_stress_index_status(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash, status="STRESS_INDEX_UNUSABLE")
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert plan.status == STATUS_UNUSABLE_STRESS_INDEX


def test_accepts_usable_causal_stress_index_status(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    assert plan.status == STATUS_OFFLINE_DISCOVERY_PLAN_READY


def test_retrospective_only_windows_do_not_enter_promotable_edge_family_cells(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [
        _window(
            window_id="retro-1",
            trigger_timestamp_ns=100 * NS,
            data_corpus_hash=prepare_manifest.data_corpus_hash,
            selection_mode=WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
            promotion_allowed=False,
        )
    ]
    stress_manifest = _stress_manifest(
        windows,
        data_corpus_hash=prepare_manifest.data_corpus_hash,
        status=STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    )
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert all(not cell.promotion_allowed for cell in plan.plan_cells if cell.is_edge_family)
    assert all("promotion_allowed=false" in cell.exclusion_reasons for cell in plan.plan_cells if cell.is_edge_family)


def test_only_retrospective_windows_produces_no_promotable_windows(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [
        _window(
            window_id="retro-1",
            trigger_timestamp_ns=100 * NS,
            data_corpus_hash=prepare_manifest.data_corpus_hash,
            selection_mode=WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
            promotion_allowed=False,
        )
    ]
    stress_manifest = _stress_manifest(
        windows,
        data_corpus_hash=prepare_manifest.data_corpus_hash,
        status=STATUS_RETROSPECTIVE_DIAGNOSTIC_ONLY,
    )
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(_discovery_config_dict()),
    )
    assert plan.status == STATUS_NO_PROMOTABLE_WINDOWS


def test_family1_config_creates_edge_family_plan_cells_without_evaluation(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    family1_cells = [cell for cell in plan.plan_cells if cell.family_id == "family_1_same_venue_quote_basis"]
    assert family1_cells
    assert all(cell.is_edge_family for cell in family1_cells)


def test_family2_config_creates_edge_family_plan_cells_with_explicit_signal_variants(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    family2_cells = [cell for cell in plan.plan_cells if cell.family_id == "family_2_cross_asset_stress_beta_lag"]
    assert family2_cells
    assert {cell.signal_variant for cell in family2_cells} == {"signed_imbalance", "notional_burst"}


@pytest.mark.parametrize("bad_variant", ["all", "auto", "default", "existing_implemented_families"])
def test_family2_rejects_soft_values(bad_variant: str):
    payload = _discovery_config_dict()
    payload["families"]["family_2_cross_asset_stress_beta_lag"]["signal_variants"] = [bad_variant]
    with pytest.raises(ValueError):
        load_discovery_config(payload)


def test_family2_rejects_unknown_signal_variants():
    payload = _discovery_config_dict()
    payload["families"]["family_2_cross_asset_stress_beta_lag"]["signal_variants"] = ["mystery_variant"]
    with pytest.raises(ValueError):
        load_discovery_config(payload)


def test_family3_plan_cells_carry_latency_gate_required_true(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    family3_cells = [cell for cell in plan.plan_cells if cell.family_id == "family_3_usd_reference_translation_lag"]
    assert family3_cells
    assert all(cell.latency_gate_required is True for cell in family3_cells)


def test_family4_creates_conditioning_cells_only(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    family4_cells = [cell for cell in plan.plan_cells if cell.family_id == "family_4_stablecoin_quote_regime_conditioning"]
    assert family4_cells
    assert all(cell.is_conditioning_family for cell in family4_cells)
    assert all(not cell.is_edge_family for cell in family4_cells)


def test_family4_is_excluded_from_edge_family_cell_count_by_default(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    family4_cells = [cell for cell in plan.plan_cells if cell.family_id == "family_4_stablecoin_quote_regime_conditioning"]
    assert plan.conditioning_cell_count == len(family4_cells)
    assert plan.edge_family_cell_count == len([cell for cell in plan.plan_cells if cell.is_edge_family])


def test_standalone_family4_edge_mode_is_rejected_as_out_of_scope():
    payload = _discovery_config_dict()
    payload["families"]["family_4_stablecoin_quote_regime_conditioning"]["standalone"] = True
    with pytest.raises(ValueError):
        load_discovery_config(payload)


def test_train_holdout_split_is_deterministic_by_trigger_timestamp(tmp_path: Path):
    plan1 = _build_ready_plan(tmp_path / "a")
    plan2 = _build_ready_plan(tmp_path / "b")
    assert plan1.train_window_ids == plan2.train_window_ids
    assert plan1.holdout_window_ids == plan2.holdout_window_ids


def test_train_holdout_split_records_boundary_and_counts(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    assert plan.split_timestamp_boundary_ns == 200 * NS
    assert plan.family_summary["train_window_count"] == len(plan.train_window_ids)
    assert plan.family_summary["holdout_window_count"] == len(plan.holdout_window_ids)


def test_insufficient_train_windows_prevents_evaluation_ready_status(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _discovery_config_dict()
    payload["train_holdout"]["min_train_windows"] = 2
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert plan.status != STATUS_OFFLINE_DISCOVERY_PLAN_READY


def test_insufficient_holdout_windows_prevents_evaluation_ready_status(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _discovery_config_dict()
    payload["train_holdout"]["min_holdout_windows"] = 2
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert plan.status != STATUS_OFFLINE_DISCOVERY_PLAN_READY


def test_discovery_config_hash_changes_when_horizons_change():
    payload = _discovery_config_dict()
    changed = _discovery_config_dict()
    changed["families"]["family_1_same_venue_quote_basis"]["horizons_ms"] = [60000]
    assert compute_discovery_config_hash(load_discovery_config(payload)) != compute_discovery_config_hash(load_discovery_config(changed))


def test_discovery_config_hash_changes_when_symbols_change():
    payload = _discovery_config_dict()
    changed = _discovery_config_dict()
    changed["families"]["family_1_same_venue_quote_basis"]["source_symbols"] = ["BTC/USD"]
    assert compute_discovery_config_hash(load_discovery_config(payload)) != compute_discovery_config_hash(load_discovery_config(changed))


def test_discovery_config_hash_changes_when_cost_assumptions_change():
    payload = _discovery_config_dict()
    changed = _discovery_config_dict()
    changed["families"]["family_2_cross_asset_stress_beta_lag"]["fee_slippage_mismatch_assumptions"]["fees_bps"] = 55.0
    assert compute_discovery_config_hash(load_discovery_config(payload)) != compute_discovery_config_hash(load_discovery_config(changed))


def test_plan_hash_is_identical_across_two_identical_runs(tmp_path: Path):
    plan1 = _build_ready_plan(tmp_path / "a")
    plan2 = _build_ready_plan(tmp_path / "b")
    assert plan1.plan_hash == plan2.plan_hash


def test_parsed_output_json_is_identical_across_two_identical_runs(tmp_path: Path):
    plan1 = _build_ready_plan(tmp_path / "a")
    plan2 = _build_ready_plan(tmp_path / "b")
    out1 = write_offline_discovery_plan_outputs(plan1, tmp_path / "out1", prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", overwrite=False)
    out2 = write_offline_discovery_plan_outputs(plan2, tmp_path / "out2", prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", overwrite=False)
    assert json.loads(out1["plan_path"].read_text(encoding="utf-8")) == json.loads(out2["plan_path"].read_text(encoding="utf-8"))


def test_plan_hash_changes_when_window_ids_change(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows_a = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    windows_b = [_window(window_id="w2", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    config = load_discovery_config(_discovery_config_dict())
    plan_a = build_offline_discovery_plan(prepare_manifest=prepare_manifest, stress_window_manifest=_stress_manifest(windows_a, data_corpus_hash=prepare_manifest.data_corpus_hash), stress_windows_payload=_stress_payload(windows_a), discovery_config=config)
    plan_b = build_offline_discovery_plan(prepare_manifest=prepare_manifest, stress_window_manifest=_stress_manifest(windows_b, data_corpus_hash=prepare_manifest.data_corpus_hash), stress_windows_payload=_stress_payload(windows_b), discovery_config=config)
    assert plan_a.plan_hash != plan_b.plan_hash


def test_output_directory_does_not_overwrite_by_default(tmp_path: Path):
    plan = _build_ready_plan(tmp_path / "build")
    out_dir = tmp_path / "out"
    write_offline_discovery_plan_outputs(plan, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", overwrite=False)
    with pytest.raises(FileExistsError):
        write_offline_discovery_plan_outputs(plan, out_dir, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json", overwrite=False)


def test_manifest_includes_hashes(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    manifest = build_offline_discovery_plan_manifest_payload(plan, prepare_manifest_path="prepare.json", stress_window_manifest_path="stress_manifest.json")
    assert manifest["data_corpus_hash"]
    assert manifest["window_index_hash"]
    assert manifest["discovery_config_hash"]
    assert manifest["plan_hash"]


def test_survivor_freeze_placeholders_are_present_and_not_run_phase_2b1(tmp_path: Path):
    plan = _build_ready_plan(tmp_path)
    assert plan.train_survivor_cell_ids == []
    assert plan.holdout_evaluation_cell_ids == []
    assert plan.survivor_freeze_status == "NOT_RUN_PHASE_2B1"


def test_safety_no_forbidden_capability_imports_or_calls():
    path = Path(__file__).resolve().parent.parent / "offline_discovery_plan.py"
    source = path.read_text(encoding="utf-8")
    blocked = (
        "OrderFactory",
        "submit_order",
        "TradingNode",
        "LiveNode",
        "private_key",
        "wallet",
        "signing",
        "CANDIDATE_FOR_LIVE",
        "TRADE_READY",
        "EXECUTION_READY",
        "POLYMARKET_PK",
        "KRAKEN_API_KEY",
        "run_permutation_null",
        "cost_sensitivity",
        "ShadowExecutor",
        "shadow_executor",
        "bot authorization",
    )
    for term in blocked:
        assert term not in source


def test_disabled_all_families_reports_no_supported_hypothesis_families(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "train_holdout": {
            "train_fraction": 0.7,
            "min_train_windows": 1,
            "min_holdout_windows": 1,
        },
        "families": {
            "family_1_same_venue_quote_basis": {"enabled": False},
            "family_2_cross_asset_stress_beta_lag": {"enabled": False},
            "family_3_usd_reference_translation_lag": {"enabled": False},
            "family_4_stablecoin_quote_regime_conditioning": {"enabled": False},
        },
    }
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert plan.status == STATUS_NO_SUPPORTED_HYPOTHESIS_FAMILIES


def test_family2_cells_require_prepare_manifest_symbol_coverage(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _discovery_config_dict()
    payload["families"]["family_2_cross_asset_stress_beta_lag"]["target_symbols"] = ["MISSING/USDT"]
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert not [cell for cell in plan.plan_cells if cell.family_id == "family_2_cross_asset_stress_beta_lag"]


def test_family3_cells_require_prepare_manifest_symbol_coverage(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _discovery_config_dict()
    payload["families"]["family_3_usd_reference_translation_lag"]["usd_reference_venue_symbols"] = [{"venue": "coinbase", "symbol": "MISSING/USD"}]
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert not [cell for cell in plan.plan_cells if cell.family_id == "family_3_usd_reference_translation_lag"]


def test_family4_target_families_must_be_supported(tmp_path: Path):
    prepare_manifest = _prepare_manifest(tmp_path)
    windows = [_window(window_id="w1", trigger_timestamp_ns=100 * NS, data_corpus_hash=prepare_manifest.data_corpus_hash)]
    stress_manifest = _stress_manifest(windows, data_corpus_hash=prepare_manifest.data_corpus_hash)
    payload = _discovery_config_dict()
    payload["families"]["family_4_stablecoin_quote_regime_conditioning"]["target_families"] = ["made_up_family"]
    plan = build_offline_discovery_plan(
        prepare_manifest=prepare_manifest,
        stress_window_manifest=stress_manifest,
        stress_windows_payload=_stress_payload(windows),
        discovery_config=load_discovery_config(payload),
    )
    assert not [cell for cell in plan.plan_cells if cell.family_id == "family_4_stablecoin_quote_regime_conditioning"]
