from __future__ import annotations

import hashlib
import json
import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import ArchiveRow
from examples.strategies.venue_agnostic_signal_observer import liquidation_flush_aftershock_reversal_venue_age_aware_phase0c as phase0c

REPO_ROOT = Path(__file__).resolve().parents[4]
PHASE0C_PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0C_NULL_PRECOMMITMENT.md"


def archive_row(symbol: str = "ALT", ts: datetime | None = None, price: float = 100.0, oi: float = 1000.0, order: int = 0) -> ArchiveRow:
    return ArchiveRow(ts or datetime(2024, 1, 1, tzinfo=UTC), symbol, price, oi, "fixture", order)


def event(symbol: str = "ALT", ts: datetime | None = None, direction: str = "downside_liquidation_flush", price: float = 100.0) -> dict:
    ts = ts or datetime(2024, 1, 15, 12, tzinfo=UTC)
    return {
        "event_id": f"{symbol}_{ts:%Y%m%dT%H%M%SZ}",
        "symbol": symbol,
        "event_timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
        "event_direction": direction,
        "flush_side": "long_wipe" if direction == "downside_liquidation_flush" else "short_squeeze",
        "price_t": price,
        "oi_t": 1000.0,
    }


def null_summary(mean_95th: float = 100.0, coverage: float = 0.9, p_value: float = 0.01) -> phase0c.NullSummary:
    return phase0c.NullSummary(1000, coverage, 20.0, 20.0, 0.55, 10.0, p_value, mean_95th)


def test_precommitment_hash_non_empty_and_matches_doc():
    recorded, computed = phase0c.precommitment_recorded_and_computed(PHASE0C_PRECOMMITMENT)
    assert recorded == computed
    assert isinstance(recorded, str) and len(recorded) == 64


def test_source_artifact_hash_mismatch_hard_fails(tmp_path: Path):
    phase0a = tmp_path / "phase0a"
    phase0a.mkdir()
    (phase0a / "accepted_events.jsonl").write_text(json.dumps(event()) + "\n")
    (phase0a / "summary.json").write_text(json.dumps({"accepted_events_jsonl_path": "accepted_events.jsonl", "accepted_events_jsonl_sha256": "bad"}))
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c.verify_phase0a_source(phase0a, expected_event_hash="expected")


def test_verify_artifact_hashes_hard_fails_on_phase0b_mismatch(tmp_path: Path):
    phase0a = tmp_path / "phase0a"
    phase0b = tmp_path / "phase0b"
    phase0a.mkdir(); phase0b.mkdir()
    artifact = phase0a / "accepted_events.jsonl"
    artifact.write_text(json.dumps(event()) + "\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (phase0a / "summary.json").write_text(json.dumps({"accepted_events_jsonl_path": "accepted_events.jsonl", "accepted_events_jsonl_sha256": digest}))
    (phase0b / "summary.json").write_text(json.dumps({"phase0a_event_artifact_sha256": "wrong"}))
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c._verify_artifact_hashes(phase0a, phase0b, expected_phase0a_hash=digest)


def test_precommitment_hash_mismatch_hard_fails(tmp_path: Path):
    doc = tmp_path / "precommitment.md"
    doc.write_text("Precommitment SHA-256 (self): bad\nbody\n")
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c._verify_precommitment_hash(doc, expected_hash="expected")


def test_btc_eth_events_are_rejected():
    with pytest.raises(phase0c.SourceArtifactMismatch):
        phase0c.validate_event_universe([event("BTC"), event("ETH")])


def test_source_order_file_order_regression():
    row = archive_row(order=7)
    assert row.file_order == 7
    assert not hasattr(row, "source_order")
    candidates = phase0c.build_symbol_month_candidates([row])
    assert candidates[("ALT", 2024, 1)][0].file_order == 7


def test_matched_placebo_preserves_symbol_month_direction_and_uses_candidate_price():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    candidates = phase0c.build_symbol_month_candidates([
        archive_row("ALT", base - timedelta(days=5), 90),
        archive_row("ALT", base + timedelta(days=5), 95),
        archive_row("ALT", datetime(2024, 2, 1, tzinfo=UTC), 99),
    ])
    sampled = phase0c.sample_matched_placebo_events([event("ALT", base)], candidates, seed=7, horizon_hours=24)
    assert sampled[0]["symbol"] == "ALT"
    assert sampled[0]["event_timestamp_utc"].startswith("2024-01")
    assert sampled[0]["event_direction"] == "downside_liquidation_flush"
    assert sampled[0]["price_t"] in {90, 95}


def test_48h_exclusion_around_same_symbol_real_events():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    rows = [
        archive_row("ALT", base - timedelta(hours=49), 90),
        archive_row("ALT", base - timedelta(hours=48), 91),
        archive_row("ALT", base + timedelta(hours=48), 92),
        archive_row("ALT", base + timedelta(hours=49), 93),
        archive_row("OTHER", base, 50),
    ]
    eligible = phase0c.build_eligible_placebo_candidates(rows, [event("ALT", base)], min_hours_exclude=48)
    alt_prices = [r.price for r in eligible[("ALT", 2024, 1)]]
    assert alt_prices == [90, 93]
    assert ("OTHER", 2024, 1) in eligible


def test_price_series_nameerror_regression_direction_shuffle_non_all_long():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    rows = [
        archive_row("ALT", base - timedelta(days=7), 99),
        archive_row("ALT", base - timedelta(days=6), 101),
        archive_row("ALT", base, 100),
        archive_row("ALT", base + timedelta(hours=24), 102),
    ]
    cache = phase0c.Phase0CCache(rows, phase0c.build_price_series(rows), {"ALT": rows}, 0.0, {})
    real = phase0c.RealPrimaryMetrics(200.0, 150.0, 150.0, 1.0, 1)
    primary, month, circular, direction_p, all_same = phase0c._run_nulls(
        [event("ALT", base, "downside_liquidation_flush", 100), event("ALT", base, "upside_liquidation_flush", 100)],
        cache,
        real,
        iterations=1,
        cluster_iterations=1,
    )
    assert not all_same
    assert direction_p is not None


def test_month_null_without_candidate_is_reported_not_applicable_without_crashing():
    base = datetime(2024, 1, 15, 12, tzinfo=UTC)
    rows = [
        archive_row("ALT", base - timedelta(hours=49), 99),
        archive_row("ALT", base - timedelta(hours=25), 100),
        archive_row("ALT", base + timedelta(hours=49), 101),
        archive_row("ALT", base + timedelta(hours=73), 103),
        archive_row("ALT", base, 100),
        archive_row("ALT", base + timedelta(hours=24), 102),
        archive_row("ONDO", datetime(2024, 2, 15, tzinfo=UTC), 90),
        archive_row("ONDO", datetime(2024, 2, 16, tzinfo=UTC), 91),
    ]
    symbol_rows = {
        "ALT": sorted([row for row in rows if row.symbol == "ALT"], key=lambda row: row.timestamp),
        "ONDO": sorted([row for row in rows if row.symbol == "ONDO"], key=lambda row: row.timestamp),
    }
    cache = phase0c.Phase0CCache(rows, phase0c.build_price_series(rows), symbol_rows, 0.0, {})
    real = phase0c.RealPrimaryMetrics(200.0, 150.0, 150.0, 1.0, 1)
    primary, month, circular, direction_p, all_same = phase0c._run_nulls(
        [event("ALT", base, "downside_liquidation_flush", 100), event("ONDO", base, "downside_liquidation_flush", 100)],
        cache,
        real,
        iterations=1,
        cluster_iterations=1,
    )
    assert primary.status == "OK"
    assert month.status == phase0c.STATUS_MONTH_NULL_NOT_APPLICABLE
    assert month.iterations == 0
    assert month.coverage == pytest.approx(0.5)
    assert month.missing_candidate_count == 1
    assert month.missing_candidate_details == [{"symbol": "ONDO", "month": "2024-01", "event_timestamp_utc": "2024-01-15T12:00:00Z"}]
    assert circular.status in {"OK", "NO_COVERAGE"}
    assert direction_p == 1.0
    assert all_same is True


def test_cost_stress_arithmetic_from_gross_and_net50():
    cost = phase0c._compute_cost_stress(175.94987373067414)
    assert cost["net_mean_bps_50"] == pytest.approx(125.94987373067414)
    assert cost["net_mean_bps_75"] == pytest.approx(cost["net_mean_bps_50"] - 25.0)
    assert cost["net_mean_bps_100"] == pytest.approx(cost["net_mean_bps_50"] - 50.0)


def test_survivorship_ambiguity_prevents_clean_pass():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(200.0, 150.0, 100.0, 0.6, 100),
        survivorship_status="SURVIVORSHIP_AMBIGUITY",
        primary=null_summary(100),
        month=null_summary(100),
        circular_shift=null_summary(100),
    )
    assert verdict == phase0c.STATUS_SURVIVORSHIP_AMBIGUITY


def test_primary_null_pass_circular_shift_failure_is_not_clean_pass():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(175.0, 125.0, 97.0, 0.56, 951),
        survivorship_status="OK",
        primary=null_summary(mean_95th=13.0, p_value=0.000999),
        month=phase0c.NullSummary(
            0,
            948 / 951,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            status=phase0c.STATUS_MONTH_NULL_NOT_APPLICABLE,
            missing_candidate_details=[
                {"symbol": "ONDO", "month": "2024-01"},
                {"symbol": "ONDO", "month": "2024-01"},
                {"symbol": "ONDO", "month": "2024-01"},
            ],
        ),
        circular_shift=null_summary(mean_95th=860.0, p_value=0.974),
    )
    assert verdict == phase0c.STATUS_NULL_REJECTED_CLUSTERING
    assert verdict != phase0c.STATUS_NULL_VALIDATED_PASS


def test_circular_shift_failure_plus_survivorship_ambiguity_preserves_both_blockers():
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(175.0, 125.0, 97.0, 0.56, 951),
        survivorship_status=phase0c.STATUS_SURVIVORSHIP_AMBIGUITY,
        primary=null_summary(mean_95th=13.0, p_value=0.000999),
        month=null_summary(mean_95th=0.0),
        circular_shift=null_summary(mean_95th=860.0, p_value=0.974),
    )
    assert verdict == phase0c.STATUS_NULL_REJECTED_CLUSTERING_WITH_SURVIVORSHIP_AMBIGUITY


def test_v1_drafting_false_when_circular_shift_fails():
    summary = phase0c.build_summary(
        phase0c.STATUS_NULL_REJECTED_CLUSTERING, "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 97.0, 0.56, 951),
        null_summary(mean_95th=13.0, p_value=0.000999),
        null_summary(mean_95th=0.0),
        null_summary(mean_95th=860.0, p_value=0.974),
        1.0,
        True,
        {"survivorship_status": "OK"},
    )
    assert summary["final_verdict"] == phase0c.STATUS_NULL_REJECTED_CLUSTERING
    assert summary["v1_unlock"] is False


def test_v1_drafting_false_when_survivorship_ambiguity_exists():
    summary = phase0c.build_summary(
        phase0c.STATUS_SURVIVORSHIP_AMBIGUITY, "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 97.0, 0.56, 951),
        null_summary(mean_95th=13.0, p_value=0.000999),
        null_summary(mean_95th=0.0),
        null_summary(mean_95th=100.0, p_value=0.01),
        1.0,
        True,
        {"survivorship_status": phase0c.STATUS_SURVIVORSHIP_AMBIGUITY},
    )
    assert summary["final_verdict"] == phase0c.STATUS_SURVIVORSHIP_AMBIGUITY
    assert summary["v1_unlock"] is False


def test_primary_null_missing_candidate_is_hard_non_pass():
    primary = phase0c.NullSummary(
        0,
        0.5,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        status=phase0c.STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES,
        missing_candidate_details=[{"symbol": "ONDO", "month": "2024-01"}],
    )
    verdict = phase0c.classify_phase0c(
        real=phase0c.RealPrimaryMetrics(200.0, 150.0, 100.0, 0.6, 2),
        survivorship_status="OK",
        primary=primary,
        month=null_summary(100),
        circular_shift=null_summary(100),
    )
    assert verdict == phase0c.STATUS_PRIMARY_NULL_INSUFFICIENT_CANDIDATES


def test_month_null_missing_candidate_summary_records_details_without_pass():
    month = phase0c.NullSummary(
        0,
        0.5,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        status=phase0c.STATUS_MONTH_NULL_NOT_APPLICABLE,
        missing_candidate_details=[{"symbol": "ONDO", "month": "2024-01"}],
    )
    summary = phase0c.build_summary(
        "SURVIVORSHIP_AMBIGUITY", "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 90.0, 0.56, 2),
        null_summary(), month, null_summary(), 1.0, True,
        {"survivorship_status": "SURVIVORSHIP_AMBIGUITY"},
    )
    assert summary["secondary_placebo_status"] == phase0c.STATUS_MONTH_NULL_NOT_APPLICABLE
    assert summary["secondary_placebo_required_events"] == 2
    assert summary["secondary_placebo_events_with_candidate"] == 1
    assert summary["secondary_placebo_missing_candidate_count"] == 1
    assert summary["secondary_placebo_missing_candidate_examples"] == [{"symbol": "ONDO", "month": "2024-01"}]
    assert summary["final_verdict"] != phase0c.STATUS_NULL_VALIDATED_PASS


def test_direction_shuffle_all_long_emits_not_applicable():
    summary = phase0c.build_summary(
        "SURVIVORSHIP_AMBIGUITY", "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 90.0, 0.56, 10),
        null_summary(), null_summary(), null_summary(), 1.0, True,
        {"survivorship_status": "SURVIVORSHIP_AMBIGUITY"},
    )
    assert summary["direction_shuffle_status"] == "DIRECTION_SHUFFLE_NOT_APPLICABLE"


def test_summary_md_without_summary_json_is_incomplete(tmp_path: Path):
    report = tmp_path / "report"
    report.mkdir()
    (report / "summary.md").write_text("# partial\n")
    assert not (report / "summary.json").exists()
    with pytest.raises(phase0c.Phase0CIncomplete):
        if not (report / "summary.json").exists():
            raise phase0c.Phase0CIncomplete("summary.json missing")


def test_profile_only_cannot_emit_validation_pass():
    summary = phase0c.build_summary(
        phase0c.STATUS_PROFILE_RUN, "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 90.0, 0.56, 10),
        null_summary(), null_summary(), null_summary(), 1.0, True,
        {"survivorship_status": "SURVIVORSHIP_AMBIGUITY"}, profile_only=True,
    )
    assert summary["status"] == "NON_VALIDATING_PROFILE_RUN"
    assert summary["final_verdict"] != "PHASE0C_FALSIFICATION_PASSED_DIAGNOSTIC"
    assert summary["v1_unlock"] is False


def test_phase0b_metric_reproduction_failure_hard_fails():
    real = phase0c.RealPrimaryMetrics(10.0, 5.0, 1.0, 0.5, 1)
    with pytest.raises(phase0c.Phase0BReproductionFailed):
        phase0c._reproduce_phase0b_or_raise(real, {"horizon_metrics": [{"horizon_hours": 24, "gross_mean_bps": 11.0, "net_mean_bps_50bps": 5.0, "net_median_bps_50bps": 1.0, "win_rate_50bps": 0.5, "evaluated_event_count": 1}]})


def test_cache_builder_does_not_rescan_archive_inside_null_iteration_loop(monkeypatch: pytest.MonkeyPatch):
    calls = {"load": 0}
    def fake_load(paths):
        calls["load"] += 1
        base = datetime(2024, 1, 15, 12, tzinfo=UTC)
        return ([
            archive_row("ALT", base - timedelta(days=7), 99),
            archive_row("ALT", base - timedelta(days=6), 100),
            archive_row("ALT", base, 100),
            archive_row("ALT", base + timedelta(hours=24), 101),
        ], {"loaded_rows": 4})
    monkeypatch.setattr(phase0c, "load_archive_rows", fake_load)
    cache = phase0c._build_cache(Path("archive"))
    real = phase0c.RealPrimaryMetrics(100.0, 50.0, 50.0, 1.0, 1)
    phase0c._run_nulls([event("ALT", datetime(2024, 1, 15, 12, tzinfo=UTC), price=100)], cache, real, iterations=3, cluster_iterations=2)
    assert calls["load"] == 1


def test_load_or_build_cache_uses_disk_without_recreate(tmp_path: Path):
    calls = {"create": 0}
    cache_path = tmp_path / "cache.pkl"
    def create():
        calls["create"] += 1
        return phase0c.Phase0CLightCache({}, {}, 0.0, {})
    first = phase0c._load_or_build_cache(cache_path, create, precommitment_sha256="precommit")
    second = phase0c._load_or_build_cache(cache_path, create, precommitment_sha256="precommit")
    assert isinstance(first, phase0c.Phase0CLightCache)
    assert isinstance(second, phase0c.Phase0CLightCache)
    assert calls["create"] == 1


def test_incomplete_tmp_cache_is_ignored_and_not_consumed(tmp_path: Path):
    calls = {"create": 0}
    cache_path = tmp_path / "cache.pkl"
    tmp_cache = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_cache.write_bytes(b"not-a-complete-pickle")

    def create():
        calls["create"] += 1
        return phase0c.Phase0CLightCache({}, {}, 0.0, {"created": True})

    cache = phase0c._load_or_build_cache(cache_path, create, precommitment_sha256="precommit")

    assert isinstance(cache, phase0c.Phase0CLightCache)
    assert cache.diagnostics["created"] is True
    assert calls["create"] == 1
    assert cache_path.exists()
    assert tmp_cache.exists() is False


def test_complete_cache_requires_metadata_sidecar(tmp_path: Path):
    cache_path = tmp_path / "cache.pkl"
    with cache_path.open("wb") as f:
        pickle.dump(phase0c.Phase0CLightCache({}, {}, 0.0, {}), f)

    with pytest.raises(phase0c.Phase0CCacheInvalid):
        phase0c._load_validated_cache(cache_path)


def test_cache_atomic_write_creates_metadata_and_validates_readback(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "ALT.jsonl").write_text("{}\n")
    cache_path = tmp_path / "cache.pkl"
    cache = phase0c.Phase0CLightCache(
        {},
        {"ALT": [archive_row("ALT", datetime(2024, 1, 1, tzinfo=UTC)), archive_row("ALT", datetime(2024, 1, 2, tzinfo=UTC))]},
        1.25,
        {"loaded_rows": 2},
    )

    metadata = phase0c._write_cache_atomic(
        cache_path,
        cache,
        archive_path=archive,
        precommitment_sha256="precommit",
        build_started_utc="2024-01-01T00:00:00Z",
        build_finished_utc="2024-01-01T00:00:01Z",
    )

    assert cache_path.exists()
    assert not cache_path.with_suffix(cache_path.suffix + ".tmp").exists()
    assert phase0c._cache_metadata_path(cache_path).exists()
    assert metadata["row_count"] == 2
    assert metadata["symbol_count"] == 1
    assert metadata["cache_sha256"] == hashlib.sha256(cache_path.read_bytes()).hexdigest()
    assert metadata["precommitment_sha256"] == "precommit"
    assert phase0c._load_validated_cache(cache_path).diagnostics["loaded_rows"] == 2


def test_profile_summary_does_not_claim_cold_cache_estimate_when_cache_missing():
    summary = phase0c.build_summary(
        phase0c.STATUS_PROFILE_RUN, "hash", Path("p0a"), Path("p0b"), True, True,
        phase0c.RealPrimaryMetrics(175.0, 125.0, 90.0, 0.56, 10),
        null_summary(), null_summary(), null_summary(), 1.0, True,
        {"survivorship_status": "SURVIVORSHIP_AMBIGUITY"}, profile_only=True,
    )

    assert summary["cold_cache_estimate_available"] is False
    assert summary["estimated_full_run_elapsed_seconds"] is None


def test_build_cache_only_emits_usable_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def fake_build(archive_path: Path) -> phase0c.Phase0CLightCache:
        return phase0c.Phase0CLightCache({}, {"ALT": [archive_row("ALT")]}, 0.01, {"loaded_rows": 1})

    monkeypatch.setattr(phase0c, "_build_cache", fake_build)
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "ALT.jsonl").write_text("{}\n")
    cache_path = tmp_path / "cache.pkl"

    result = phase0c.build_phase0c_cache_only(archive, cache_path, precommitment_sha256="precommit")

    assert result["status"] == "PHASE0C_CACHE_READY"
    assert cache_path.exists()
    assert phase0c._load_validated_cache(cache_path).symbol_rows["ALT"]


def test_full_validation_refuses_corrupt_incomplete_cache(tmp_path: Path):
    cache_path = tmp_path / "cache.pkl"
    cache_path.write_bytes(b"corrupt")

    with pytest.raises(phase0c.Phase0CCacheInvalid):
        phase0c._load_validated_cache(cache_path)


def test_no_canned_stub_returns_validation_critical_values():
    assert phase0c._compute_cost_stress(150.0)["net_mean_bps_75"] == 75.0
    assert phase0c._compute_effective_n([1.0, 2.0, 3.0]) > 0
    assert phase0c._compute_survivorship_audit([archive_row("ALT")], [event("ALT")])["survivorship_status"] == "SURVIVORSHIP_AMBIGUITY"
    with pytest.raises(TypeError):
        phase0c._verify_artifact_hashes()


def test_no_prohibited_execution_constants_in_phase0c_module():
    text = Path(phase0c.__file__).read_text()
    prohibited = ["submit_order", "place_order", "api_key", "private_key", "paper_trading", "shadow_execution"]
    hits = [line for line in text.splitlines() for term in prohibited if term in line]
    assert hits == []
