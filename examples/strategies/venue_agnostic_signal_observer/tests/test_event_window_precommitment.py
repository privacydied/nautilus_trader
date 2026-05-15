"""
Tests for the cross_asset_event_window_differential_v1 precommitment.

Covers:
1. Markdown/JSON consistency for the event-window precommitment
2. Signal family separation from cross_asset_beta_lag_v1
3. Beta-lag files are not modified
4. FDR dimensions match the event-window JSON
5. Baseline-window rule is present and not the old 60s default
6. Burn/quarantine namespace separation

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Find the repo root from git or cwd heuristic."""
    candidates = [
        Path.cwd().resolve(),
        Path(__file__).resolve().parent.parent.parent.parent.parent,
    ]
    for c in candidates:
        git_dir = c / ".git"
        if git_dir.exists() or git_dir.is_dir():
            return c
    # Fallback: try the known path
    return Path("/mnt/nasirjones/py/nautilus_trader")


ROOT = _repo_root()


def _load_ew_json() -> dict:
    """Load the event-window differential precommitment JSON."""
    path = ROOT / "event_window_differential_precommitment.json"
    assert path.exists(), f"File not found: {path}"
    with open(path, encoding="utf-8") as f:
        data: dict = json.load(f)
    return data


def _load_beta_json() -> dict:
    """Load the cross-asset beta lag precommitment JSON."""
    path = ROOT / "stage2_precommitment.json"
    assert path.exists(), f"File not found: {path}"
    with open(path, encoding="utf-8") as f:
        data: dict = json.load(f)
    return data


def _load_ew_md() -> str:
    """Load the event-window differential precommitment markdown."""
    path = ROOT / "EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md"
    assert path.exists(), f"File not found: {path}"
    return path.read_text(encoding="utf-8")


# ===========================================================================
# 1. Event-window precommitment file existence and structure
# ===========================================================================


class TestEventWindowPrecommitmentFilesExist:
    def test_ew_precommitment_files_exist(self):
        """EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md,
        event_window_differential_precommitment.json, and
        EVENT_WINDOW_THRESHOLDS_RATIONALE.md all exist."""
        assert (ROOT / "EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md").exists()
        assert (ROOT / "event_window_differential_precommitment.json").exists()
        assert (ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md").exists()

    def test_ew_json_has_required_values(self):
        """event_window_differential_precommitment.json has required values."""
        data = _load_ew_json()

        # Signal family
        assert data.get("signal_family") == "cross_asset_event_window_differential_v1"

        # Schema
        assert data.get("schema_version") == 1
        assert data.get("stage") == "stage2"

        # Timestamp
        created = data.get("created_utc", "")
        assert "T" in created and created.endswith("Z")

        # Source assets
        hc = data.get("hypothesis_config", {})
        assert hc.get("source_assets") == ["BTC", "ETH"]
        assert hc.get("target_assets") == ["SOL", "LINK", "DOGE", "AVAX"]
        assert hc.get("window_types") == ["event", "baseline"]

        # Signal type
        assert hc.get("primary_signal_type") == "signed_imbalance"
        assert hc.get("primary_lookback_ms") == 30000
        assert hc.get("allowed_signal_types") == ["signed_imbalance"]
        assert hc.get("allowed_lookbacks_ms") == [30000]

        # Allowed horizons
        horizons = hc.get("allowed_horizons_ms", [])
        assert set(horizons) == {10000, 30000, 60000, 300000}
        assert len(horizons) == 4

    def test_ew_fdr_values(self):
        """Event-window JSON has correct FDR values."""
        data = _load_ew_json()

        primary = data.get("primary_fdr", {})
        assert primary.get("method") == "benjamini_hochberg"
        assert primary.get("q") == 0.10
        assert primary.get("pvalue_source") == "native_permutation"

        dims = primary.get("test_family_dimensions", [])
        assert len(dims) == 6
        expected_dims = {
            "source_asset", "target_asset", "signal_type",
            "lookback_ms", "horizon_ms", "window_type",
        }
        assert set(dims) == expected_dims

        sensitivity = data.get("sensitivity_fdr", {})
        assert sensitivity.get("method") == "benjamini_yekutieli"
        assert sensitivity.get("q") == 0.10

    def test_ew_holdout_values(self):
        """Event-window JSON has correct holdout values."""
        data = _load_ew_json()

        holdout = data.get("holdout", {})
        assert holdout.get("primary_split") == "temporal"
        assert holdout.get("discovery_fraction") == 0.70
        assert holdout.get("test_fraction") == 0.30
        assert holdout.get("minimum_validated_captures") == 10
        assert holdout.get("test_set_sealed") is True

    def test_ew_discovery_acceptance(self):
        """Event-window JSON has correct discovery acceptance criteria."""
        data = _load_ew_json()

        discovery = data.get("discovery_acceptance", {})
        assert discovery.get("minimum_aggregate_mean_net_bps_per_event") == 2.0
        assert discovery.get("minimum_same_sign_capture_fraction") == 0.70
        assert discovery.get("same_sign_rounding") == "ceil"
        assert discovery.get("minimum_valid_events_per_config") == 50
        assert discovery.get("worst_capture_mean_net_bps_floor") == -5.0
        assert discovery.get("requires_primary_bh_survival") is True

    def test_ew_holdout_acceptance(self):
        """Event-window JSON has correct holdout acceptance criteria."""
        data = _load_ew_json()

        holdout_acc = data.get("holdout_acceptance", {})
        assert holdout_acc.get("minimum_aggregate_mean_net_bps_per_event") == 2.0
        assert holdout_acc.get("requires_same_sign_as_discovery") is True
        assert holdout_acc.get("minimum_valid_events_per_config") == 50
        assert holdout_acc.get("worst_capture_mean_net_bps_floor") == -5.0
        assert holdout_acc.get("frozen_config_only") is True
        assert holdout_acc.get("no_threshold_or_parameter_changes") is True

    def test_ew_burn_rules(self):
        """Event-window JSON has burn rules."""
        data = _load_ew_json()

        burn = data.get("burn_rules", {})
        assert burn.get("failed_discovery_burns_entire_validated_corpus_for_signal_family") is True

    def test_ew_cost_model(self):
        """Event-window JSON has correct cost model."""
        data = _load_ew_json()

        cost = data.get("cost_model", {})
        assert cost.get("taker_fee_bps") == 40.0
        assert cost.get("slippage_bps") == 5.0
        assert cost.get("quote_mismatch_buffer_bps") == 5.0
        assert cost.get("all_in_cost_per_event_bps") == 50.0

    def test_ew_event_window_config(self):
        """Event-window JSON has correct window boundaries."""
        data = _load_ew_json()

        ew = data.get("event_window", {})
        assert ew.get("event_start_offset_minutes") == -5
        assert ew.get("event_duration_minutes") == 20
        assert ew.get("baseline_window_offset_minutes_from_event") == 90
        assert ew.get("baseline_window_duration_seconds") == 1200
        assert ew.get("minimum_capture_duration_seconds") == 1200
        assert ew.get("minimum_global_overlap_seconds") == 30
        assert ew.get("paired_window_type") is True


# ===========================================================================
# 2. Signal family separation from cross_asset_beta_lag_v1
# ===========================================================================


class TestSignalFamilySeparation:
    def test_different_signal_families(self):
        """The event-window and beta-lag precommitments have different
        signal_family values."""
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_family = ew.get("signal_family", "")
        beta_family = beta.get("signal_family", "")

        assert ew_family != beta_family
        assert ew_family == "cross_asset_event_window_differential_v1"
        assert beta_family == "cross_asset_beta_lag_v1"

    def test_different_fdr_dimensions(self):
        """The two precommitments have different test_family_dimensions
        (event-window adds window_type)."""
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_dims = set(ew.get("primary_fdr", {}).get("test_family_dimensions", []))
        beta_dims = set(beta.get("primary_fdr", {}).get("test_family_dimensions", []))

        # They share the first 5 dimensions
        shared = {"source_asset", "target_asset", "signal_type",
                  "lookback_ms", "horizon_ms"}
        assert shared.issubset(ew_dims)
        assert shared.issubset(beta_dims)

        # Event-window adds window_type
        assert "window_type" in ew_dims
        assert "window_type" not in beta_dims

    def test_different_hypothesis_configs(self):
        """The event-window has window_types and different horizons
        from beta-lag."""
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_hc = ew.get("hypothesis_config", {})
        beta_hc = beta.get("hypothesis_config", {})

        # Different horizon sets
        ew_horizons = set(ew_hc.get("allowed_horizons_ms", []))
        beta_horizons = set(beta_hc.get("allowed_horizons_ms", []))
        assert ew_horizons != beta_horizons
        assert len(ew_horizons) == 4
        assert len(beta_horizons) == 1
        assert beta_horizons == {300000}

        # Event-window has window_types key
        assert "window_types" in ew_hc
        assert "window_types" not in beta_hc

    def test_event_window_section_only_in_ew(self):
        """The event_window config section only exists in the EW JSON."""
        ew = _load_ew_json()
        beta = _load_beta_json()

        assert "event_window" in ew
        assert "event_window" not in beta


# ===========================================================================
# 3. Beta-lag files are not modified
# ===========================================================================


class TestBetaLagFilesUntouched:
    def test_beta_lag_json_signal_family_unchanged(self):
        """The beta-lag precommitment signal family has not changed."""
        beta = _load_beta_json()
        assert beta.get("signal_family") == "cross_asset_beta_lag_v1"

    def test_beta_lag_json_dimensions_unchanged(self):
        """The beta-lag precommitment dimensions have not changed."""
        beta = _load_beta_json()
        dims = beta.get("primary_fdr", {}).get("test_family_dimensions", [])
        assert len(dims) == 5
        assert "window_type" not in dims

    def test_beta_lag_horizons_unchanged(self):
        """The beta-lag allowed horizons have not changed."""
        beta = _load_beta_json()
        hc = beta.get("hypothesis_config", {})
        assert hc.get("allowed_horizons_ms") == [300000]
        assert hc.get("primary_horizon_ms") == 300000

    def test_beta_lag_stress_gate_unchanged(self):
        """The beta-lag event gate has not changed."""
        beta = _load_beta_json()
        gate = beta.get("event_gate", {})
        assert gate.get("minimum_btc_1h_move_bps") == 150.0
        assert gate.get("requires_acceleration") is True

    def test_beta_lag_files_not_touched_by_test(self):
        """The beta-lag markdown file exists and has correct preamble."""
        md = ROOT / "STAGE2_PRECOMMITMENT.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "cross_asset_beta_lag_v1" in text
        assert "cross_asset_event_window_differential_v1" not in text


# ===========================================================================
# 4. Baseline-window rule is present and not the old 60s default
# ===========================================================================


class TestBaselineWindowRule:
    def test_baseline_window_section_exists(self):
        """The event-window JSON has a dedicated event_window section
        containing baseline parameters."""
        data = _load_ew_json()
        ew = data.get("event_window", {})
        assert ew, "event_window section must exist"

    def test_baseline_offset_not_zero(self):
        """The baseline window offset is explicitly set and non-zero."""
        data = _load_ew_json()
        offset = data.get("event_window", {}).get("baseline_window_offset_minutes_from_event")
        assert offset is not None
        assert offset > 0
        assert offset == 90

    def test_baseline_duration_not_60s(self):
        """The baseline window duration is NOT the old 60-second default."""
        data = _load_ew_json()
        dur = data.get("event_window", {}).get("baseline_window_duration_seconds")
        assert dur is not None
        assert dur != 60, (
            "baseline_window_duration_seconds must not be the old 60s default; "
            "must be explicitly parameterised for event-window captures"
        )
        assert dur == 1200, (
            "Expected 1200 seconds (20 min) to match event window duration"
        )

    def test_baseline_matches_event_duration(self):
        """Baseline duration equals event duration (fair comparison)."""
        data = _load_ew_json()
        ew = data.get("event_window", {})
        event_dur = ew.get("event_duration_minutes", 0) * 60
        baseline_dur = ew.get("baseline_window_duration_seconds", 0)
        assert event_dur > 0
        assert baseline_dur > 0
        assert event_dur == baseline_dur, (
            f"Baseline duration ({baseline_dur}s) must equal "
            f"event duration ({event_dur}s)"
        )

    def test_markdown_mentions_baseline_rule(self):
        """The markdown precommitment explicitly lists the baseline rule
        and mentions the 60s override."""
        md = _load_ew_md()
        assert "baseline_window_offset_minutes_from_event" in md
        assert "60" in md, "Must mention why 60s default is not used"
        assert "Never" in md or "never" in md

    def test_no_60s_default_reuse(self):
        """The precommitment JSON has no field that would silently inherit
        a 60s default from an older schema."""
        data = _load_ew_json()
        # Check that there is no field named something that could be confused
        # with the old baseline_window default
        for key in data.get("event_window", {}):
            assert "60" not in str(data["event_window"].get(key, ""))


# ===========================================================================
# 5. Burn/quarantine namespace separation
# ===========================================================================


class TestBurnQuarantineNamespace:
    def test_burn_file_supports_signal_family(self):
        """burn.py stores signal_family per row — separation is built in."""
        from venue_agnostic_signal_observer.burn import burn_corpus, read_burned

        # The burn interface accepts signal_family as a required first arg
        # (verified by inspection of the module signature).
        import inspect
        sig = inspect.signature(burn_corpus)
        params = list(sig.parameters.keys())
        assert "signal_family" in params
        # signal_family is the first positional parameter
        assert params[0] == "signal_family"

    def test_burn_signal_family_separation_conceptual(self):
        """The burn function stores signal_family, enabling per-family
        filtering."""
        from venue_agnostic_signal_observer.burn import get_burned_signal_families

        families = get_burned_signal_families()
        # This test just verifies the function exists and is callable.
        # Actual burn entries will have signal_family set.
        assert isinstance(families, set)

    def test_burn_separates_by_family(self, tmp_path):
        """Burn records for different signal families are distinguishable."""
        from venue_agnostic_signal_observer.burn import (
            burn_corpus,
            read_burned,
            get_burned_signal_families,
        )

        bpath = tmp_path / "burned.jsonl"

        # Write a beta-lag burn
        burn_corpus(
            signal_family="cross_asset_beta_lag_v1",
            reason="Test burn beta",
            precommitment_git_sha="abc123",
            path=bpath,
        )
        # Write an event-window burn
        burn_corpus(
            signal_family="cross_asset_event_window_differential_v1",
            reason="Test burn event",
            precommitment_git_sha="abc123",
            path=bpath,
        )

        rows = read_burned(path=bpath)
        families = {r.get("signal_family") for r in rows}

        assert "cross_asset_beta_lag_v1" in families
        assert "cross_asset_event_window_differential_v1" in families
        assert len(families) == 2

    def test_quarantine_is_run_scoped(self):
        """Quarantine is run_id-based (no signal_family field added).
        This is by design: data-quality quarantine is cross-family."""
        from venue_agnostic_signal_observer.quarantine import (
            quarantine_run,
            read_quarantine,
        )

        # Quarantine has no signal_family field by inspection of the module.
        # This test verifies the field is absent from the schema.
        with open(ROOT / "examples" / "strategies" / "venue_agnostic_signal_observer" / "quarantine.py",
                  encoding="utf-8") as f:
            source = f.read()
        assert "signal_family" not in source, (
            "quarantine.py should not have signal_family field — "
            "quarantine is run-scoped, not family-scoped"
        )


# ===========================================================================
# 6. Utility importability verification
# ===========================================================================


class TestStage2UtilitiesImportable:
    """Verify the core Stage 2 utilities remain importable (not broken by
    any changes)."""

    def test_precommitment_utils_importable(self):
        from venue_agnostic_signal_observer.stage2_precommitment_utils import (
            CollectionLock,
            load_precommitment,
            get_test_family_dimensions,
            get_signal_family,
            validate_markdown_json_match,
            compute_file_sha256,
        )
        # Smoke: load_precommitment works with explicit path
        data = load_precommitment(ROOT / "event_window_differential_precommitment.json")
        assert data.get("signal_family") == "cross_asset_event_window_differential_v1"

    def test_ew_loaded_via_explicit_path(self):
        """load_precommitment with explicit path loads the event-window
        precommitment, not the beta-lag one."""
        from venue_agnostic_signal_observer.stage2_precommitment_utils import (
            load_precommitment, get_signal_family,
        )

        data = load_precommitment(ROOT / "event_window_differential_precommitment.json")
        assert get_signal_family(data) == "cross_asset_event_window_differential_v1"

    def test_beta_lag_still_loads_via_default(self):
        """load_precommitment without path still loads the beta-lag
        precommitment."""
        from venue_agnostic_signal_observer.stage2_precommitment_utils import (
            load_precommitment, get_signal_family,
        )

        data = load_precommitment()
        assert get_signal_family(data) == "cross_asset_beta_lag_v1"

    def test_fdr_importable(self):
        from venue_agnostic_signal_observer.stage2_fdr import run_fdr, build_parser

    def test_split_corpus_importable(self):
        from venue_agnostic_signal_observer.stage2_split_corpus import build_split, build_parser

    def test_check_criteria_importable(self):
        from venue_agnostic_signal_observer.stage2_check_criteria import (
            run_discovery_check,
            run_holdout_check,
            build_parser,
        )

    def test_quarantine_importable(self):
        from venue_agnostic_signal_observer.quarantine import (
            quarantine_run,
            read_quarantine,
            is_quarantined,
            get_quarantined_run_ids,
        )

    def test_burn_importable(self):
        from venue_agnostic_signal_observer.burn import (
            burn_corpus,
            read_burned,
            get_burned_run_ids,
            is_burned,
            get_burned_signal_families,
        )

    def test_validate_capture_importable(self):
        from venue_agnostic_signal_observer.validate_capture import (
            validate_capture,
            build_parser,
        )

    def test_run_report_corpus_importable(self):
        from venue_agnostic_signal_observer.run_report_corpus import main as rc_main


# ===========================================================================
# 7. No post-hoc event window selection
# ===========================================================================


class TestNoPostHocWindowSelection:
    def test_markdown_bans_post_hoc_selection(self):
        """The markdown explicitly bans post-hoc event window selection."""
        md = _load_ew_md()
        assert "post-hoc" in md.lower() or "post hoc" in md.lower()
        assert "No Post-Hoc" in md or "no post-hoc" in md.lower()

    def test_json_no_flexible_window(self):
        """The JSON has no field that would allow post-hoc window shifting."""
        data = _load_ew_json()
        ew = data.get("event_window", {})
        # No allowed_widening or allowed_shifting fields
        for key in ew:
            assert "shift" not in key.lower()
            assert "widen" not in key.lower()
            assert "flexible" not in key.lower()


# ===========================================================================
# 8. No collection started
# ===========================================================================


class TestNoCollectionStarted:
    def test_no_capture_directories_exist(self):
        """No event-window capture directories have been created."""
        reports_root = ROOT / "reports"
        if reports_root.exists():
            for d in reports_root.iterdir():
                if d.is_dir() and "event_window" in d.name.lower():
                    pytest.fail(f"Event-window capture directory already exists: {d}")
        # Acceptable if the directory doesn't exist at all

    def test_no_systemd_service_added(self):
        """No new systemd service file for event-window capture."""
        svc_dir = (
            ROOT / "examples" / "strategies" / "venue_agnostic_signal_observer" / "systemd"
        )
        if svc_dir.exists():
            for f in svc_dir.iterdir():
                if "event_window" in f.name.lower():
                    pytest.fail(f"Unexpected systemd service: {f}")
