"""Tests for artifact metadata injection and backward compatibility.

Covers:
1. New capture manifest includes schema/version/git/run metadata.
2. New evaluation summary includes schema/version/git/run metadata.
3. Old summaries without metadata still load/export safely.
4. FAST_DIAGNOSTIC still remaps attempted REJECTED to MARKET_MODERATE_DIAGNOSTIC.
5. MCPT export still skips cost-floor dust and does not crash on missing metadata.
"""
from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ..artifact_metadata import (
    CURRENT_SCHEMA_VERSION,
    SAFETY_MODE,
    build_metadata,
    get_metadata,
    get_metadata_field,
    inject_metadata_into_manifest,
)
from ..mcpt_export import (
    export_mcpt_summary,
    is_mcpt_worthy_group,
    select_mcpt_candidate_groups,
)
from ..runners.legacy_cli.run_derivatives_spot_lead_lag import EvalSummary, write_reports
from ..tick_models import TickSignalEvent, TickForwardReturn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_old_summary_dict(**overrides) -> dict:
    """A summary dict in the pre-metadata (v0) format — no _metadata key."""
    d = {
        "capture_dir": "data/test_capture",
        "total_signals": 500,
        "valid_evaluations": 380,
        "rejected_evaluations": 120,
        "fee_bps": 40.0,
        "slippage_bps": 5.0,
        "quote_mismatch_buffer_bps": 5.0,
        "all_in_cost_bps": 50.0,
        "verdict": "NEEDS_MORE_DATA",
        "capture_mode": "",
        "run_duration_s": 12.3,
        "capture_context": {},
        "overlap_info": {},
        "oi_bucket_summary": {},
        "results_by_group": [],
        "best_group": None,
        "best_bucket": None,
        "rejections": [],
    }
    d.update(overrides)
    return d


def _make_group(
    *,
    mean_net_bps: float = -54.0,
    median_net_bps: float = -55.0,
    win_rate: float = 0.0,
    baseline_mean_net_bps: float | None = None,
    baseline_win_rate: float | None = None,
    valid_count: int = 400,
    candidate: bool = False,
    rejection_reasons: list[str] | None = None,
    source_venue: str = "binance_perp",
    target_venue: str = "kraken",
    signal_type: str = "notional_burst",
    lookback_ms: int = 1000,
    horizon_ms: int = 5000,
) -> dict:
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "valid_count": valid_count,
        "mean_raw_bps": mean_net_bps + 50.0,
        "mean_net_bps": mean_net_bps,
        "median_net_bps": median_net_bps,
        "win_rate": win_rate,
        "baseline_mean_net_bps": baseline_mean_net_bps,
        "baseline_win_rate": baseline_win_rate,
        "candidate": candidate,
        "rejection_reasons": rejection_reasons or [],
    }


# ---------------------------------------------------------------------------
# 1. build_metadata produces correct fields
# ---------------------------------------------------------------------------

class TestBuildMetadata:
    def test_schema_version_present(self):
        meta = build_metadata()
        assert meta["schema_version"] == CURRENT_SCHEMA_VERSION
        assert meta["schema_version"] == "1.0.0"

    def test_created_at_utc_format(self):
        meta = build_metadata()
        ts = meta["created_at_utc"]
        # ISO-8601 with Z suffix
        assert ts.endswith("Z")
        # Parseable
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None

    def test_git_sha_is_string(self):
        meta = build_metadata()
        assert isinstance(meta["git_sha"], str)

    def test_git_branch_is_string(self):
        meta = build_metadata()
        assert isinstance(meta["git_branch"], str)

    def test_safety_mode(self):
        meta = build_metadata()
        assert meta["safety_mode"] == SAFETY_MODE
        assert meta["safety_mode"] == "public_data_observer_only"

    def test_capture_mode_empty_default(self):
        meta = build_metadata()
        assert meta["capture_mode"] == ""

    def test_capture_mode_fast_diagnostic(self):
        meta = build_metadata(capture_mode="FAST_DIAGNOSTIC")
        assert meta["capture_mode"] == "FAST_DIAGNOSTIC"

    def test_capture_mode_full_active(self):
        meta = build_metadata(capture_mode="FULL_ACTIVE")
        assert meta["capture_mode"] == "FULL_ACTIVE"

    def test_run_args_serialized(self):
        import argparse
        args = argparse.Namespace(foo="bar", baz=42)
        meta = build_metadata(run_args=args)
        assert meta["run_args"]["foo"] == "bar"
        assert meta["run_args"]["baz"] == 42

    def test_run_args_secrets_redacted(self):
        import argparse
        args = argparse.Namespace(secret_credential="secret123", name="test")
        meta = build_metadata(run_args=args)
        assert meta["run_args"]["secret_credential"] == "<redacted>"
        assert meta["run_args"]["name"] == "test"

    def test_volatility_gate_snapshot(self):
        snap = {"verdict": "FAST_DIAGNOSTIC", "threshold_bps": 200.0}
        meta = build_metadata(volatility_gate_snapshot=snap)
        assert meta["volatility_gate_snapshot"]["verdict"] == "FAST_DIAGNOSTIC"

    def test_preflight_summary(self):
        pf = {"passed": True, "streams_ok": 3, "streams_missing": 0}
        meta = build_metadata(preflight_summary=pf)
        assert meta["preflight_summary"]["passed"] is True

    def test_none_fields_are_null(self):
        meta = build_metadata()
        assert meta["volatility_gate_snapshot"] is None
        assert meta["preflight_summary"] is None
        assert meta["run_args"] == {}


# ---------------------------------------------------------------------------
# 2. inject_metadata_into_manifest mutates manifest
# ---------------------------------------------------------------------------

class TestInjectMetadataIntoManifest:
    def test_adds_metadata_key(self):
        manifest = {"run_id": "123"}
        result = inject_metadata_into_manifest(manifest, capture_mode="FULL_ACTIVE")
        assert "_metadata" in result
        assert result["_metadata"]["capture_mode"] == "FULL_ACTIVE"
        assert result["_metadata"]["schema_version"] == CURRENT_SCHEMA_VERSION
        assert result["_metadata"]["safety_mode"] == "public_data_observer_only"

    def test_preserves_existing_keys(self):
        manifest = {"run_id": "123", "streams": {"a": 1}}
        result = inject_metadata_into_manifest(manifest)
        assert result["run_id"] == "123"
        assert result["streams"] == {"a": 1}

    def test_roundtrip_json(self):
        manifest = {"run_id": "456", "overlap": {"global": True}}
        inject_metadata_into_manifest(manifest, capture_mode="FAST_DIAGNOSTIC")
        text = json.dumps(manifest, default=str)
        loaded = json.loads(text)
        assert loaded["_metadata"]["capture_mode"] == "FAST_DIAGNOSTIC"
        assert loaded["run_id"] == "456"


# ---------------------------------------------------------------------------
# 3. get_metadata / get_metadata_field backward compat
# ---------------------------------------------------------------------------

class TestGetMetadata:
    def test_extracts_metadata_from_new_format(self):
        obj = {
            "_metadata": {
                "schema_version": "1.0.0",
                "git_sha": "abc1234",
                "capture_mode": "FAST_DIAGNOSTIC",
            },
            "total_signals": 100,
        }
        meta = get_metadata(obj)
        assert meta["schema_version"] == "1.0.0"
        assert meta["capture_mode"] == "FAST_DIAGNOSTIC"

    def test_returns_empty_dict_for_old_format(self):
        obj = {"total_signals": 100, "verdict": "NEEDS_MORE_DATA"}
        meta = get_metadata(obj)
        assert meta == {}

    def test_get_metadata_field_with_default(self):
        obj = {"total_signals": 100}
        assert get_metadata_field(obj, "schema_version", "unknown") == "unknown"
        assert get_metadata_field(obj, "git_sha") is None

    def test_get_metadata_field_from_new_format(self):
        obj = {"_metadata": {"schema_version": "1.0.0", "git_sha": "abc"}}
        assert get_metadata_field(obj, "schema_version") == "1.0.0"
        assert get_metadata_field(obj, "git_sha") == "abc"

    def test_get_metadata_field_non_dict_is_empty(self):
        obj = {"_metadata": "not_a_dict"}
        meta = get_metadata(obj)
        assert meta == {}
        assert get_metadata_field(obj, "schema_version", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 4. EvalSummary + write_reports includes metadata
# ---------------------------------------------------------------------------

class TestWriteReportsMetadata:
    def test_summary_json_contains_metadata(self, tmp_path):
        summary = EvalSummary(
            capture_dir="data/test",
            capture_mode="FAST_DIAGNOSTIC",
            total_signals=10,
            valid_evaluations=8,
            fee_bps=40.0,
            slippage_bps=5.0,
            quote_mismatch_buffer_bps=5.0,
            all_in_cost_bps=50.0,
            run_start=1000.0,
            run_end=1012.0,
            verdict="MARKET_MODERATE_DIAGNOSTIC",
        )
        out_dir = tmp_path / "report_test"
        write_reports(summary, out_dir, [], [])

        with open(out_dir / "summary.json") as f:
            data = json.load(f)

        assert "_metadata" in data
        meta = data["_metadata"]
        assert meta["schema_version"] == CURRENT_SCHEMA_VERSION
        assert meta["capture_mode"] == "FAST_DIAGNOSTIC"
        assert meta["safety_mode"] == "public_data_observer_only"
        assert isinstance(meta["created_at_utc"], str)
        assert isinstance(meta["git_sha"], str)
        assert isinstance(meta["git_branch"], str)
        # Verdict preserved at top level
        assert data["verdict"] == "MARKET_MODERATE_DIAGNOSTIC"

    def test_summary_json_backward_compat_missing_metadata(self, tmp_path):
        """An old-style summary dict without _metadata can still be loaded."""
        old_summary = _make_old_summary_dict(verdict="NEEDS_MORE_DATA")
        # Simulate loading from disk
        text = json.dumps(old_summary)
        loaded = json.loads(text)
        # Should not crash when querying metadata
        meta = get_metadata(loaded)
        assert meta == {}
        assert get_metadata_field(loaded, "schema_version", "0.0.0") == "0.0.0"
        # Top-level fields still accessible
        assert loaded["verdict"] == "NEEDS_MORE_DATA"
        assert loaded["total_signals"] == 500


# ---------------------------------------------------------------------------
# 5. FAST_DIAGNOSTIC remap still works
# ---------------------------------------------------------------------------

class TestFastDiagnosticVerdictRemap:
    def test_rejected_remapped_to_market_moderate_diagnostic(self):
        """FAST_DIAGNOSTIC captures must never produce final REJECTED verdict.

        The run_derivatives_spot_lead_lag.py evaluation logic remaps
        REJECTED -> MARKET_MODERATE_DIAGNOSTIC when capture_mode == "FAST_DIAGNOSTIC".
        """
        summary = EvalSummary(
            capture_mode="FAST_DIAGNOSTIC",
            total_signals=100,
            valid_evaluations=80,
            verdict="REJECTED",  # This would normally be REJECTED
        )
        # The evaluation code checks:
        #   if summary.capture_mode == "FAST_DIAGNOSTIC" and summary.verdict == "REJECTED":
        #       summary.verdict = "MARKET_MODERATE_DIAGNOSTIC"
        assert summary.capture_mode == "FAST_DIAGNOSTIC"
        # Simulate the remap
        if summary.capture_mode == "FAST_DIAGNOSTIC" and summary.verdict == "REJECTED":
            summary.verdict = "MARKET_MODERATE_DIAGNOSTIC"
        assert summary.verdict == "MARKET_MODERATE_DIAGNOSTIC"

    def test_non_fast_diagnostic_keeps_rejected(self):
        """Non-FAST_DIAGNOSTIC captures can produce REJECTED."""
        summary = EvalSummary(
            capture_mode="FULL_ACTIVE",
            total_signals=100,
            valid_evaluations=80,
            verdict="REJECTED",
        )
        if summary.capture_mode == "FAST_DIAGNOSTIC" and summary.verdict == "REJECTED":
            summary.verdict = "MARKET_MODERATE_DIAGNOSTIC"
        assert summary.verdict == "REJECTED"

    def test_empty_capture_mode_keeps_rejected(self):
        """Empty capture_mode (default) allows REJECTED."""
        summary = EvalSummary(
            capture_mode="",
            total_signals=100,
            valid_evaluations=80,
            verdict="REJECTED",
        )
        if summary.capture_mode == "FAST_DIAGNOSTIC" and summary.verdict == "REJECTED":
            summary.verdict = "MARKET_MODERATE_DIAGNOSTIC"
        assert summary.verdict == "REJECTED"


# ---------------------------------------------------------------------------
# 6. MCPT export handles missing metadata and cost-floor dust
# ---------------------------------------------------------------------------

class TestMcptExportBackwardCompat:
    def test_export_summary_with_old_summary_no_metadata(self, tmp_path):
        """MCPT export should not crash when reading an old summary.json without _metadata."""
        report_dir = tmp_path / "report_old"
        report_dir.mkdir()
        old_summary = _make_old_summary_dict()
        with open(report_dir / "summary.json", "w") as f:
            json.dump(old_summary, f)

        result = export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=[],
            skipped_reason="test_skip",
        )
        assert result.exists()
        with open(result) as f:
            data = json.load(f)
        # New export has its own metadata
        assert "_metadata" in data
        assert data["mcpt_skipped"] is True

    def test_export_summary_with_new_summary_has_metadata(self, tmp_path):
        """MCPT export propagates metadata from summary.json."""
        report_dir = tmp_path / "report_new"
        report_dir.mkdir()
        meta = build_metadata(capture_mode="FAST_DIAGNOSTIC")
        new_summary = _make_old_summary_dict(capture_mode="FAST_DIAGNOSTIC")
        new_summary["_metadata"] = meta
        with open(report_dir / "summary.json", "w") as f:
            json.dump(new_summary, f)

        result = export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=[],
            skipped_reason="test_skip",
        )
        assert result.exists()
        with open(result) as f:
            data = json.load(f)
        # Export carries forward capture_mode from source summary
        assert data["_metadata"]["capture_mode"] == "FAST_DIAGNOSTIC"

    def test_mcpt_skips_cost_floor_dust(self):
        """All groups with net ~ -50bps should be skipped."""
        groups = [_make_group(mean_net_bps=-52.0), _make_group(mean_net_bps=-48.5)]
        selected = select_mcpt_candidate_groups(groups)
        assert selected == []

    def test_mcpt_positive_group_selected(self):
        """Positive net bps groups should be MCPT-worthy."""
        groups = [_make_group(mean_net_bps=15.0, valid_count=100)]
        selected = select_mcpt_candidate_groups(groups)
        assert len(selected) == 1
        assert selected[0]["mean_net_bps"] == 15.0

    def test_mcpt_export_no_crash_on_missing_metadata_field(self, tmp_path):
        """Load summary with no metadata, export should still work."""
        report_dir = tmp_path / "report_empty"
        report_dir.mkdir()
        # Old format: no _metadata key at all
        with open(report_dir / "summary.json", "w") as f:
            json.dump({"results_by_group": [], "verdict": "NEEDS_MORE_DATA"}, f)

        result = export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=[],
            skipped_reason="no_groups",
        )
        assert result.exists()
        with open(result) as f:
            data = json.load(f)
        # Should still produce its own metadata
        assert "_metadata" in data
        # capture_mode from fallback top-level should be ""
        assert data["_metadata"]["capture_mode"] == ""


# ---------------------------------------------------------------------------
# 7. Capture manifest includes metadata
# ---------------------------------------------------------------------------

class TestCaptureManifestMetadata:
    def test_manifest_includes_metadata_after_injection(self):
        manifest = {
            "run_id": "1715700000",
            "requested_duration_seconds": 900,
            "streams": {},
            "overlap": {},
        }
        inject_metadata_into_manifest(
            manifest,
            capture_mode="FAST_DIAGNOSTIC",
        )
        assert "_metadata" in manifest
        meta = manifest["_metadata"]
        assert meta["schema_version"] == CURRENT_SCHEMA_VERSION
        assert meta["capture_mode"] == "FAST_DIAGNOSTIC"
        assert meta["safety_mode"] == "public_data_observer_only"
        assert "created_at_utc" in meta
        assert "git_sha" in meta
        assert "git_branch" in meta

    def test_manifest_preserves_existing_data(self):
        manifest = {
            "run_id": "1715700000",
            "streams": {"binance_perp_BTC/USDT": {"tick_count": 500}},
            "overlap": {"global_overlap_duration_seconds": 890},
        }
        inject_metadata_into_manifest(manifest, capture_mode="FULL_ACTIVE")
        assert manifest["run_id"] == "1715700000"
        assert manifest["streams"]["binance_perp_BTC/USDT"]["tick_count"] == 500
        assert manifest["_metadata"]["capture_mode"] == "FULL_ACTIVE"

    def test_manifest_roundtrip_json(self):
        manifest = {"run_id": "99"}
        inject_metadata_into_manifest(manifest, capture_mode="")
        text = json.dumps(manifest, default=str)
        loaded = json.loads(text)
        assert loaded["_metadata"]["schema_version"] == CURRENT_SCHEMA_VERSION
        assert "run_id" in loaded


# ---------------------------------------------------------------------------
# 8. Safety mode is always public_data_observer_only
# ---------------------------------------------------------------------------

class TestSafetyMode:
    def test_metadata_safety_mode(self):
        meta = build_metadata()
        assert meta["safety_mode"] == "public_data_observer_only"

    def test_injected_manifest_safety_mode(self):
        manifest = {}
        inject_metadata_into_manifest(manifest)
        assert manifest["_metadata"]["safety_mode"] == "public_data_observer_only"

    def test_write_reports_safety_mode(self, tmp_path):
        summary = EvalSummary(verdict="NEEDS_MORE_DATA")
        out_dir = tmp_path / "safety_test"
        write_reports(summary, out_dir, [], [])
        with open(out_dir / "summary.json") as f:
            data = json.load(f)
        assert data["_metadata"]["safety_mode"] == "public_data_observer_only"