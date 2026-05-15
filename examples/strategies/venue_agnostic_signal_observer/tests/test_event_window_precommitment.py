"""
Tests for the cross_asset_event_window_differential_v1 precommitment.

Covers:
1. Precommitment file existence and structure
2. Signal family spelling guard (no typos)
3. Paired differential contrast semantics (JSON fields, p-value source)
4. Capture geometry and unambiguous duration fields
5. Frozen event-list template + schema existence
6. Signal family separation from cross_asset_beta_lag_v1
7. Beta-lag files are not modified
8. FDR dimensions match the event-window JSON
9. Baseline-window rule is present and not the old 60s default
10. Burn/quarantine namespace separation
11. No collection started

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
# 1. Precommitment file existence and structure
# ===========================================================================


class TestEventWindowPrecommitmentFilesExist:
    def test_ew_precommitment_files_exist(self):
        """All event-window precommitment files exist."""
        assert (ROOT / "EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md").exists()
        assert (ROOT / "event_window_differential_precommitment.json").exists()
        assert (ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md").exists()

    def test_event_list_files_exist(self):
        """Event-list template and schema files exist."""
        assert (ROOT / "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md").exists()
        assert (ROOT / "event_window_event_list.schema.json").exists()

    def test_ew_json_has_required_values(self):
        """event_window_differential_precommitment.json has required values."""
        data = _load_ew_json()

        assert data.get("signal_family") == "cross_asset_event_window_differential_v1"
        assert data.get("schema_version") == 1
        assert data.get("stage") == "stage2"

        created = data.get("created_utc", "")
        assert "T" in created and created.endswith("Z")

        hc = data.get("hypothesis_config", {})
        assert hc.get("source_assets") == ["BTC", "ETH"]
        assert hc.get("target_assets") == ["SOL", "LINK", "DOGE", "AVAX"]
        assert hc.get("window_types") == ["event", "baseline"]

        assert hc.get("primary_signal_type") == "signed_imbalance"
        assert hc.get("primary_lookback_ms") == 30000
        assert hc.get("allowed_signal_types") == ["signed_imbalance"]
        assert hc.get("allowed_lookbacks_ms") == [30000]

        horizons = hc.get("allowed_horizons_ms", [])
        assert set(horizons) == {10000, 30000, 60000, 300000}
        assert len(horizons) == 4

    def test_ew_fdr_values(self):
        """Event-window JSON has correct FDR values and native_paired_permutation."""
        data = _load_ew_json()

        primary = data.get("primary_fdr", {})
        assert primary.get("method") == "benjamini_hochberg"
        assert primary.get("q") == 0.10
        assert primary.get("pvalue_source") == "native_paired_permutation"

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
        """Event-window JSON has correct boundary config."""
        data = _load_ew_json()

        ew = data.get("event_window", {})
        assert ew.get("event_start_offset_minutes") == -5
        assert ew.get("event_duration_minutes") == 20
        assert ew.get("event_window_duration_seconds") == 1200
        assert ew.get("baseline_window_offset_minutes_from_event") == 90
        assert ew.get("baseline_window_duration_seconds") == 1200
        assert ew.get("minimum_capture_duration_seconds") == 1200
        assert ew.get("minimum_global_overlap_seconds") == 30
        assert ew.get("paired_window_type") is True


# ===========================================================================
# 2. Signal family spelling guard (no typos)
# ===========================================================================


class TestSignalFamilySpelling:
    """Search every event-window file and ensure the signal family is
    spelled exactly cross_asset_event_window_differential_v1
    with no comp_asset, compas_asset, or other typos."""

    def test_json_signal_family_spelling(self):
        data = _load_ew_json()
        sf = data.get("signal_family", "")
        assert sf == "cross_asset_event_window_differential_v1", (
            f"JSON signal_family has typo: '{sf}'"
        )

    def test_md_signal_family_spelling(self):
        md = _load_ew_md()
        assert "cross_asset_event_window_differential_v1" in md
        assert "comp_asset_event_window_differential_v1" not in md

    def test_no_comp_asset_typo_in_any_file(self):
        """Search the entire event-window file set for 'comp_asset' typo."""
        files_to_check = [
            ROOT / "EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md",
            ROOT / "event_window_differential_precommitment.json",
            ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md",
            ROOT / "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md",
            ROOT / "event_window_event_list.schema.json",
        ]
        for f in files_to_check:
            if f.exists():
                text = f.read_text(encoding="utf-8")
                assert "comp_asset" not in text, (
                    f"Typo 'comp_asset' found in {f.name}"
                )
                assert "cross_asset_event_window_differential_v1" in text, (
                    f"Expected signal_family not found in {f.name}"
                )

    def test_file_names_no_comp_asset(self):
        """No event-window file contains 'comp' in its name."""
        import glob
        root_str = str(ROOT)
        for f in glob.glob(f"{root_str}/EVENT_WINDOW_*"):
            assert "comp" not in f.lower()
        for f in glob.glob(f"{root_str}/event_window_*"):
            assert "comp" not in f.lower()


# ===========================================================================
# 3. Paired differential contrast semantics
# ===========================================================================


class TestPairedDifferentialContrast:
    """The event-window precommitment must be based on paired contrast,
    not independent window comparison."""

    def test_paired_contrast_section_exists(self):
        data = _load_ew_json()
        pc = data.get("paired_contrast", {})
        assert pc, "paired_contrast section must exist"

    def test_contrast_type_field(self):
        data = _load_ew_json()
        ct = data.get("paired_contrast", {}).get("contrast_type")
        assert ct == "paired_event_minus_baseline", (
            f"Expected paired_event_minus_baseline, got '{ct}'"
        )

    def test_paired_permutation_required(self):
        data = _load_ew_json()
        assert data.get("paired_contrast", {}).get("paired_permutation_required") is True

    def test_primary_pvalue_source_paired(self):
        """Both paired_contrast.primary_pvalue_source and
        primary_fdr.pvalue_source must be 'native_paired_permutation'."""
        data = _load_ew_json()
        pc_source = data.get("paired_contrast", {}).get("primary_pvalue_source")
        fdr_source = data.get("primary_fdr", {}).get("pvalue_source")
        assert pc_source == "native_paired_permutation", (
            f"paired_contrast.pvalue_source is '{pc_source}'"
        )
        assert fdr_source == "native_paired_permutation", (
            f"primary_fdr.pvalue_source is '{fdr_source}'"
        )
        assert pc_source == fdr_source

    def test_window_type_separation_insufficient(self):
        data = _load_ew_json()
        assert data.get("paired_contrast", {}).get("window_type_separation_insufficient") is True

    def test_delta_metric_description_present(self):
        data = _load_ew_json()
        desc = data.get("paired_contrast", {}).get("delta_metric_description", "")
        assert desc, "delta_metric_description must be non-empty"
        assert "delta" in desc.lower()
        assert "event" in desc.lower()
        assert "baseline" in desc.lower()

    def test_md_mentions_paired_contrast(self):
        md = _load_ew_md()
        assert "Paired Differential Contrast" in md
        assert "delta = event_window_mean_net_bps" in md or "delta_metric" in md

    def test_md_mentions_native_paired_permutation(self):
        md = _load_ew_md()
        assert "native_paired_permutation" in md

    def test_md_mentions_window_type_insufficient(self):
        md = _load_ew_md()
        assert "window_type separation is not sufficient" in md or \
               "window_type separation alone is not sufficient" in md

    def test_rationale_mentions_paired_contrast(self):
        rationale = ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md"
        text = rationale.read_text(encoding="utf-8")
        assert "Paired Contrast" in text or "paired differential" in text
        assert "native_paired_permutation" in text

    def test_pvalue_source_differs_from_beta(self):
        """The event-window pvalue_source must differ from the beta-lag
        precommitment (which uses native_permutation, not native_paired_permutation)."""
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_source = ew.get("paired_contrast", {}).get("primary_pvalue_source")
        beta_source = beta.get("primary_fdr", {}).get("pvalue_source")
        assert ew_source != beta_source, (
            f"Event-window and beta-lag must have different pvalue sources: "
            f"both are '{ew_source}'"
        )
        assert ew_source == "native_paired_permutation"
        assert beta_source == "native_permutation"


class TestPairedPermutationRowShape:
    """The paired permutation output row shape is defined once in JSON,
    markdown, and rationale.  All three must agree."""

    def test_json_has_primary_pvalue_row_shape(self):
        data = _load_ew_json()
        pc = data.get("paired_contrast", {})
        rshape = pc.get("primary_pvalue_row_shape", {})
        assert rshape, "paired_contrast.primary_pvalue_row_shape must exist"

    def test_json_window_type_value_for_primary(self):
        data = _load_ew_json()
        wt = (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("window_type_value_for_primary")
        )
        assert wt == "paired_delta", f"Expected 'paired_delta', got '{wt}'"

    def test_json_diagnostic_values(self):
        data = _load_ew_json()
        diag = (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("window_type_values_for_diagnostics", [])
        )
        assert set(diag) == {"event", "baseline"}
        assert len(diag) == 2

    def test_json_dimensions_match_fdr(self):
        """The row-shape dimensions must match primary_fdr.test_family_dimensions."""
        data = _load_ew_json()
        rshape_dims = (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("dimensions", [])
        )
        fdr_dims = data.get("primary_fdr", {}).get("test_family_dimensions", [])
        assert list(rshape_dims) == list(fdr_dims), (
            f"Row shape dims {rshape_dims} don't match FDR dims {fdr_dims}"
        )

    def test_json_has_is_diagnostic_tag(self):
        data = _load_ew_json()
        tag = (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("is_diagnostic_tag")
        )
        assert tag == "is_diagnostic", f"Expected 'is_diagnostic', got '{tag}'"
        assert (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("diagnostic_rows_not_in_primary_fdr_family")
        ) is True

    def test_md_mentions_paired_delta(self):
        md = _load_ew_md()
        assert "paired_delta" in md, (
            "Markdown must mention 'paired_delta' as the window_type for primary rows"
        )

    def test_md_mentions_is_diagnostic(self):
        md = _load_ew_md()
        assert "is_diagnostic" in md, (
            "Markdown must mention 'is_diagnostic' tag for optional diagnostic rows"
        )

    def test_md_says_diagnostic_not_primary_fdr(self):
        md = _load_ew_md()
        assert "**not** the primary FDR family" in md

    def test_rationale_mentions_paired_permutation(self):
        rationale = ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md"
        text = rationale.read_text(encoding="utf-8")
        assert "native_paired_permutation" in text

    def test_fdr_pvalue_source_matches_paired_contrast(self):
        """primary_fdr.pvalue_source must match
        paired_contrast.primary_pvalue_source."""
        data = _load_ew_json()
        fdr_source = data.get("primary_fdr", {}).get("pvalue_source")
        pc_source = data.get("paired_contrast", {}).get("primary_pvalue_source")
        assert fdr_source == pc_source, (
            f"FDR source '{fdr_source}' != contrast source '{pc_source}'"
        )

    def test_window_type_not_event_or_baseline_for_primary(self):
        """The primary FDR dimension window_type must NOT have value 'event'
        or 'baseline' — it must be 'paired_delta'."""
        data = _load_ew_json()
        wt = (
            data.get("paired_contrast", {})
            .get("primary_pvalue_row_shape", {})
            .get("window_type_value_for_primary")
        )
        assert wt == "paired_delta"
        assert wt not in ("event", "baseline")

    def test_md_window_type_section_does_not_list_event_baseline_first(self):
        """The 'window_type as a Dimension' section must NOT list
        '(values: event, baseline)' as the primary description."""
        md = _load_ew_md()
        # Find the "### window_type as a Dimension" section
        idx = md.find("### window_type as a Dimension")
        assert idx >= 0, "window_type as a Dimension section not found"
        section = md[idx:idx + 800]  # Read ~800 chars into the section
        # Must mention paired_delta
        assert '"paired_delta"' in section
        # Must NOT list (values: event, baseline) as the primary description
        assert "(values: `event`, `baseline`)" not in section
        assert "`event`, `baseline`" not in section.split("Primary FDR rows")[0] if "Primary FDR rows" in section else True

    def test_md_window_type_section_says_diagnostic_not_primary(self):
        md = _load_ew_md()
        assert "### window_type as a Dimension" in md
        # The document must state that diagnostic rows do not enter
        # the primary FDR family (stated with markdown bold formatting)
        assert ("**not** the primary FDR family" in md
                or "primary FDR family is defined on the" in md)

    def test_rationale_window_type_section_mentions_paired_delta(self):
        """The rationale 'Window Type as a Test Family Dimension' section
        must mention paired_delta."""
        rationale = ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md"
        text = rationale.read_text(encoding="utf-8")
        idx = text.find("## Window Type as a Test Family Dimension")
        assert idx >= 0
        section = text[idx:idx + 1000]
        assert '"paired_delta"' in section, \
            "Rationale Window Type section must mention paired_delta"

    def test_rationale_window_type_does_not_say_primary_separates_event_baseline(self):
        """The rationale must NOT say the primary FDR separates event and
        baseline rows — those are diagnostic-only."""
        rationale = ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md"
        text = rationale.read_text(encoding="utf-8")
        idx = text.find("## Window Type as a Test Family Dimension")
        assert idx >= 0
        section = text[idx:idx + 1000]
        # Must say diagnostic rows are not in the primary family
        assert ("not in the primary family" in section
                or "excluded from the primary FDR family" in section)
        # Must NOT contain the stale phrase that treated event/baseline as primary
        stale = "allows the FDR correction to treat event-window and baseline-window observations"
        assert stale not in section

    def test_md_no_stale_event_baseline_as_primary_fdr_prose(self):
        """The markdown must not contain prose implying event/baseline
        are separated by the primary FDR correction."""
        md = _load_ew_md()
        stale_phrases = [
            "the FDR correction can separate the two window types",
        ]
        for phrase in stale_phrases:
            assert phrase not in md, f"Stale prose found in markdown: '{phrase}'"


# ===========================================================================
# 4. Capture geometry and unambiguous duration fields
# ===========================================================================


class TestCaptureGeometry:
    """The capture geometry must be unambiguous and the duration fields
    must be explicit in seconds, no implied defaults."""

    def test_capture_geometry_section_exists(self):
        data = _load_ew_json()
        cg = data.get("capture_geometry", {})
        assert cg, "capture_geometry section must exist"

    def test_collection_mode_two_separate(self):
        data = _load_ew_json()
        mode = data.get("capture_geometry", {}).get("collection_mode")
        assert mode == "two_separate_captures_paired_by_event_id"

    def test_event_window_duration_seconds_explicit(self):
        data = _load_ew_json()
        cg = data.get("capture_geometry", {})
        ew = data.get("event_window", {})

        cg_dur = cg.get("event_window_duration_seconds")
        ew_dur = ew.get("event_window_duration_seconds")
        assert cg_dur is not None and cg_dur == 1200, (
            f"capture_geometry event_window_duration_seconds: '{cg_dur}'"
        )
        assert ew_dur is not None and ew_dur == 1200, (
            f"event_window event_window_duration_seconds: '{ew_dur}'"
        )
        assert cg_dur == ew_dur

    def test_baseline_window_duration_seconds_explicit(self):
        data = _load_ew_json()
        cg = data.get("capture_geometry", {})
        ew = data.get("event_window", {})

        cg_dur = cg.get("baseline_window_duration_seconds")
        ew_dur = ew.get("baseline_window_duration_seconds")
        assert cg_dur is not None and cg_dur == 1200
        assert ew_dur is not None and ew_dur == 1200
        assert cg_dur == ew_dur

    def test_paired_admitted_duration_seconds(self):
        data = _load_ew_json()
        pad = data.get("capture_geometry", {}).get("paired_admitted_duration_seconds")
        assert pad is not None, "paired_admitted_duration_seconds missing"
        assert pad == 2400, (
            f"paired_admitted_duration_seconds should be 2400 (sum of two 1200s), "
            f"got {pad}"
        )

    def test_durations_sum_to_paired(self):
        """Verify event + baseline durations sum to paired_admitted."""
        data = _load_ew_json()
        cg = data.get("capture_geometry", {})
        ev = cg.get("event_window_duration_seconds", 0)
        bl = cg.get("baseline_window_duration_seconds", 0)
        pa = cg.get("paired_admitted_duration_seconds", 0)
        assert ev + bl == pa, (
            f"Paired duration {pa} does not match sum of "
            f"event ({ev}) + baseline ({bl})"
        )

    def test_no_ambiguous_duration_wording_in_md(self):
        """The markdown must not use ambiguous relative terms like
        'up to', 'at least', or 'approximately' for window durations."""
        md = _load_ew_md()
        ambiguous_terms = ["up to", "approximately", "roughly", "about ", "~"]
        for term in ambiguous_terms:
            if term in md:
                # Only flag if near a duration context
                lines = [l for l in md.split("\n") if term.lower() in l.lower()
                         and ("duration" in l.lower() or "second" in l.lower()
                              or "minute" in l.lower())]
                assert not lines, (
                    f"Ambiguous duration wording '{term}' found in: {lines}"
                )

    def test_capture_geometry_rationale_present(self):
        data = _load_ew_json()
        rationale = data.get("capture_geometry", {}).get("rationale", "")
        assert rationale, "capture_geometry rationale must be non-empty"


# ===========================================================================
# 5. Frozen event-list template + schema
# ===========================================================================


class TestFrozenEventList:
    """Event-list template and schema must exist with correct fields."""

    def test_event_list_template_exists(self):
        path = ROOT / "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "event_id" in text
        assert "scheduled_event_utc" in text
        assert "event_window_start_utc" in text
        assert "baseline_window_start_utc" in text
        assert "inclusion_reason" in text
        assert "exclusion_reason" in text
        assert "calendar_source" in text

    def test_event_list_schema_exists(self):
        path = ROOT / "event_window_event_list.schema.json"
        assert path.exists()
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        assert schema.get("type") == "array"
        items = schema.get("items", {})
        props = items.get("properties", {})
        for field in ["event_id", "event_name", "scheduled_event_utc",
                      "calendar_source", "calendar_source_snapshot_utc",
                      "event_window_start_utc", "event_window_end_utc",
                      "baseline_window_start_utc", "baseline_window_end_utc",
                      "inclusion_reason", "exclusion_reason"]:
            assert field in props, f"Schema missing field: {field}"

    def test_event_list_schema_requires_inclusion_or_exclusion(self):
        """Schema must have oneOf with two branches for included/excluded."""
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        items = schema.get("items", {})
        one_of = items.get("oneOf", [])
        assert len(one_of) >= 2, "Schema must have oneOf for inclusion/exclusion"
        # Both inclusion_reason and exclusion_reason must be in required
        required = items.get("required", [])
        assert "inclusion_reason" in required
        assert "exclusion_reason" in required

    def test_schema_validates_included_row(self):
        """Row with inclusion_reason string + exclusion_reason null passes."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_001",
            "event_name": "Test Event",
            "scheduled_event_utc": "2026-06-18T18:00:00Z",
            "calendar_source": "Forex Factory",
            "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
            "event_window_start_utc": "2026-06-18T17:55:00Z",
            "event_window_end_utc": "2026-06-18T18:15:00Z",
            "baseline_window_start_utc": "2026-06-18T19:30:00Z",
            "baseline_window_end_utc": "2026-06-18T19:50:00Z",
            "inclusion_reason": "Test: high-impact macro event",
            "exclusion_reason": None,
        }
        jsonschema.validate([row], schema)

    def test_schema_validates_excluded_row(self):
        """Row with inclusion_reason null + exclusion_reason string passes."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_002",
            "event_name": "Test Excluded Event",
            "scheduled_event_utc": "2026-07-03T12:30:00Z",
            "calendar_source": "Investing.com",
            "calendar_source_snapshot_utc": "2026-07-02T10:00:00Z",
            "event_window_start_utc": "2026-07-03T12:25:00Z",
            "event_window_end_utc": "2026-07-03T12:45:00Z",
            "baseline_window_start_utc": "2026-07-03T14:00:00Z",
            "baseline_window_end_utc": "2026-07-03T14:20:00Z",
            "inclusion_reason": None,
            "exclusion_reason": "Baseline overlap with another event",
        }
        jsonschema.validate([row], schema)

    def test_schema_rejects_both_non_null(self):
        """Row with both inclusion_reason and exclusion_reason non-null fails."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_003",
            "event_name": "Test Bad Event",
            "scheduled_event_utc": "2026-06-18T18:00:00Z",
            "calendar_source": "Forex Factory",
            "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
            "event_window_start_utc": "2026-06-18T17:55:00Z",
            "event_window_end_utc": "2026-06-18T18:15:00Z",
            "baseline_window_start_utc": "2026-06-18T19:30:00Z",
            "baseline_window_end_utc": "2026-06-18T19:50:00Z",
            "inclusion_reason": "Included",
            "exclusion_reason": "Also excluded — contradiction",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([row], schema)

    def test_schema_rejects_both_null(self):
        """Row with both inclusion_reason and exclusion_reason null fails."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_004",
            "event_name": "Test Null Event",
            "scheduled_event_utc": "2026-06-18T18:00:00Z",
            "calendar_source": "Forex Factory",
            "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
            "event_window_start_utc": "2026-06-18T17:55:00Z",
            "event_window_end_utc": "2026-06-18T18:15:00Z",
            "baseline_window_start_utc": "2026-06-18T19:30:00Z",
            "baseline_window_end_utc": "2026-06-18T19:50:00Z",
            "inclusion_reason": None,
            "exclusion_reason": None,
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([row], schema)

    def test_schema_rejects_empty_inclusion_string(self):
        """Row with empty inclusion_reason string fails (minLength)."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_005",
            "event_name": "Test Empty Inc",
            "scheduled_event_utc": "2026-06-18T18:00:00Z",
            "calendar_source": "Forex Factory",
            "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
            "event_window_start_utc": "2026-06-18T17:55:00Z",
            "event_window_end_utc": "2026-06-18T18:15:00Z",
            "baseline_window_start_utc": "2026-06-18T19:30:00Z",
            "baseline_window_end_utc": "2026-06-18T19:50:00Z",
            "inclusion_reason": "",
            "exclusion_reason": None,
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([row], schema)

    def test_schema_rejects_empty_exclusion_string(self):
        """Row with empty exclusion_reason string fails (minLength)."""
        import jsonschema
        path = ROOT / "event_window_event_list.schema.json"
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        row = {
            "event_id": "ew_TEST_006",
            "event_name": "Test Empty Exc",
            "scheduled_event_utc": "2026-06-18T18:00:00Z",
            "calendar_source": "Forex Factory",
            "calendar_source_snapshot_utc": "2026-06-17T12:00:00Z",
            "event_window_start_utc": "2026-06-18T17:55:00Z",
            "event_window_end_utc": "2026-06-18T18:15:00Z",
            "baseline_window_start_utc": "2026-06-18T19:30:00Z",
            "baseline_window_end_utc": "2026-06-18T19:50:00Z",
            "inclusion_reason": None,
            "exclusion_reason": "",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([row], schema)

    def test_event_list_template_mentions_baseline_overlap(self):
        path = ROOT / "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md"
        text = path.read_text(encoding="utf-8")
        assert "baseline overlap" in text.lower() or "Baseline Overlap" in text

    def test_md_mentions_frozen_event_list(self):
        md = _load_ew_md()
        assert "Frozen Event List" in md or "frozen" in md.lower()
        assert "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md" in md


# ===========================================================================
# 6. Signal family separation from cross_asset_beta_lag_v1
# ===========================================================================


class TestSignalFamilySeparation:
    def test_different_signal_families(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_family = ew.get("signal_family", "")
        beta_family = beta.get("signal_family", "")

        assert ew_family != beta_family
        assert ew_family == "cross_asset_event_window_differential_v1"
        assert beta_family == "cross_asset_beta_lag_v1"

    def test_different_fdr_dimensions(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_dims = set(ew.get("primary_fdr", {}).get("test_family_dimensions", []))
        beta_dims = set(beta.get("primary_fdr", {}).get("test_family_dimensions", []))

        shared = {"source_asset", "target_asset", "signal_type",
                  "lookback_ms", "horizon_ms"}
        assert shared.issubset(ew_dims)
        assert shared.issubset(beta_dims)

        assert "window_type" in ew_dims
        assert "window_type" not in beta_dims

    def test_different_hypothesis_configs(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        ew_hc = ew.get("hypothesis_config", {})
        beta_hc = beta.get("hypothesis_config", {})

        ew_horizons = set(ew_hc.get("allowed_horizons_ms", []))
        beta_horizons = set(beta_hc.get("allowed_horizons_ms", []))
        assert ew_horizons != beta_horizons
        assert len(ew_horizons) == 4
        assert len(beta_horizons) == 1

        assert "window_types" in ew_hc
        assert "window_types" not in beta_hc

    def test_event_window_section_only_in_ew(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        assert "event_window" in ew
        assert "event_window" not in beta

    def test_paired_contrast_section_only_in_ew(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        assert "paired_contrast" in ew
        assert "paired_contrast" not in beta

    def test_capture_geometry_section_only_in_ew(self):
        ew = _load_ew_json()
        beta = _load_beta_json()

        assert "capture_geometry" in ew
        assert "capture_geometry" not in beta


# ===========================================================================
# 7. Beta-lag files are not modified
# ===========================================================================


class TestBetaLagFilesUntouched:
    def test_beta_lag_json_signal_family_unchanged(self):
        beta = _load_beta_json()
        assert beta.get("signal_family") == "cross_asset_beta_lag_v1"

    def test_beta_lag_json_dimensions_unchanged(self):
        beta = _load_beta_json()
        dims = beta.get("primary_fdr", {}).get("test_family_dimensions", [])
        assert len(dims) == 5
        assert "window_type" not in dims

    def test_beta_lag_horizons_unchanged(self):
        beta = _load_beta_json()
        hc = beta.get("hypothesis_config", {})
        assert hc.get("allowed_horizons_ms") == [300000]
        assert hc.get("primary_horizon_ms") == 300000

    def test_beta_lag_stress_gate_unchanged(self):
        beta = _load_beta_json()
        gate = beta.get("event_gate", {})
        assert gate.get("minimum_btc_1h_move_bps") == 150.0
        assert gate.get("requires_acceleration") is True

    def test_beta_lag_files_not_touched_by_test(self):
        md = ROOT / "STAGE2_PRECOMMITMENT.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "cross_asset_beta_lag_v1" in text
        assert "cross_asset_event_window_differential_v1" not in text

    def test_beta_lag_pvalue_source_unchanged(self):
        beta = _load_beta_json()
        source = beta.get("primary_fdr", {}).get("pvalue_source")
        assert source == "native_permutation", (
            f"Beta-lag pvalue_source changed to '{source}'"
        )


# ===========================================================================
# 8. Baseline-window rule is present and not the old 60s default
# ===========================================================================


class TestBaselineWindowRule:
    def test_baseline_window_section_exists(self):
        data = _load_ew_json()
        ew = data.get("event_window", {})
        assert ew, "event_window section must exist"

    def test_baseline_offset_not_zero(self):
        data = _load_ew_json()
        offset = data.get("event_window", {}).get("baseline_window_offset_minutes_from_event")
        assert offset is not None
        assert offset > 0
        assert offset == 90

    def test_baseline_duration_not_60s(self):
        data = _load_ew_json()
        dur = data.get("event_window", {}).get("baseline_window_duration_seconds")
        assert dur is not None
        assert dur != 60, (
            "baseline_window_duration_seconds must not be the old 60s default; "
            "must be explicitly parameterised"
        )
        assert dur == 1200

    def test_baseline_matches_event_duration(self):
        data = _load_ew_json()
        ew_sec = data.get("event_window", {}).get("event_window_duration_seconds", 0)
        bl_sec = data.get("event_window", {}).get("baseline_window_duration_seconds", 0)
        assert ew_sec > 0
        assert bl_sec > 0
        assert ew_sec == bl_sec, (
            f"Baseline ({bl_sec}s) must equal event ({ew_sec}s)"
        )

    def test_markdown_mentions_baseline_rule(self):
        md = _load_ew_md()
        assert "baseline_window_offset_minutes_from_event" in md
        assert "60" in md, "Must mention why 60s default is not used"

    def test_no_60s_default_reuse(self):
        data = _load_ew_json()
        for key in data.get("event_window", {}):
            val = data["event_window"].get(key)
            if isinstance(val, str):
                assert "60" not in val, (
                    f"event_window.{key} contains '60' — possible default leak"
                )

    def test_rationale_explains_baseline_60s_departure(self):
        rationale = ROOT / "EVENT_WINDOW_THRESHOLDS_RATIONALE.md"
        text = rationale.read_text(encoding="utf-8")
        assert "60-second default" in text or "60-second baseline" in text
        assert "1200-second" in text


# ===========================================================================
# 9. Burn/quarantine namespace separation
# ===========================================================================


class TestBurnQuarantineNamespace:
    def test_burn_file_supports_signal_family(self):
        """burn.py stores signal_family per row — separation is built in."""
        from venue_agnostic_signal_observer.burn import burn_corpus

        import inspect
        sig = inspect.signature(burn_corpus)
        params = list(sig.parameters.keys())
        assert "signal_family" in params
        assert params[0] == "signal_family"

    def test_burn_signal_family_separation(self):
        from venue_agnostic_signal_observer.burn import get_burned_signal_families
        families = get_burned_signal_families()
        assert isinstance(families, set)

    def test_burn_separates_by_family(self, tmp_path):
        from venue_agnostic_signal_observer.burn import (
            burn_corpus, read_burned,
        )

        bpath = tmp_path / "burned.jsonl"
        burn_corpus(
            signal_family="cross_asset_beta_lag_v1",
            reason="Test burn beta",
            precommitment_git_sha="abc123",
            path=bpath,
        )
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
        """Quarantine has no signal_family field — by design,
        data-quality quarantine is cross-family."""
        path = ROOT / "examples" / "strategies" / "venue_agnostic_signal_observer" / "quarantine.py"
        source = path.read_text(encoding="utf-8")
        assert "signal_family" not in source, (
            "quarantine.py should not have signal_family — quarantine is run-scoped"
        )

    def test_capture_admission_baseline_overlap_quarantine(self):
        """JSON must have baseline_overlap_invalidates_paired_capture field."""
        data = _load_ew_json()
        ca = data.get("capture_admission", {})
        assert ca.get("baseline_overlap_invalidates_paired_capture") is True


# ===========================================================================
# 10. Utility importability verification
# ===========================================================================


class TestStage2UtilitiesImportable:
    def test_precommitment_utils_importable(self):
        from venue_agnostic_signal_observer.stage2_precommitment_utils import (
            CollectionLock, load_precommitment, get_test_family_dimensions,
            get_signal_family, validate_markdown_json_match, compute_file_sha256,
        )
        data = load_precommitment(ROOT / "event_window_differential_precommitment.json")
        assert data.get("signal_family") == "cross_asset_event_window_differential_v1"

    def test_ew_loaded_via_explicit_path(self):
        from venue_agnostic_signal_observer.stage2_precommitment_utils import (
            load_precommitment, get_signal_family,
        )
        data = load_precommitment(ROOT / "event_window_differential_precommitment.json")
        assert get_signal_family(data) == "cross_asset_event_window_differential_v1"

    def test_beta_lag_still_loads_via_default(self):
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
            run_discovery_check, run_holdout_check, build_parser,
        )

    def test_quarantine_importable(self):
        from venue_agnostic_signal_observer.quarantine import (
            quarantine_run, read_quarantine, is_quarantined, get_quarantined_run_ids,
        )

    def test_burn_importable(self):
        from venue_agnostic_signal_observer.burn import (
            burn_corpus, read_burned, get_burned_run_ids, is_burned, get_burned_signal_families,
        )

    def test_validate_capture_importable(self):
        from venue_agnostic_signal_observer.validate_capture import (
            validate_capture, build_parser,
        )

    def test_run_report_corpus_importable(self):
        from venue_agnostic_signal_observer.run_report_corpus import main as rc_main


# ===========================================================================
# 11. No post-hoc event window selection
# ===========================================================================


class TestNoPostHocWindowSelection:
    def test_markdown_bans_post_hoc_selection(self):
        md = _load_ew_md()
        assert "post-hoc" in md.lower() or "post hoc" in md.lower()
        assert "No Post-Hoc" in md or "no post-hoc" in md.lower()

    def test_json_no_flexible_window(self):
        data = _load_ew_json()
        ew = data.get("event_window", {})
        for key in ew:
            assert "shift" not in key.lower()
            assert "widen" not in key.lower()
            assert "flexible" not in key.lower()

    def test_md_mentions_cancelled_event_rule(self):
        md = _load_ew_md()
        assert "cancelled" in md.lower() or "rescheduled" in md.lower()


# ===========================================================================
# 12. No collection started
# ===========================================================================


class TestNoCollectionStarted:
    def test_no_capture_directories_exist(self):
        reports_root = ROOT / "reports"
        if reports_root.exists():
            event_window_dirs = [
                d for d in reports_root.iterdir()
                if d.is_dir() and "event_window" in d.name.lower()
            ]
            assert not event_window_dirs, (
                f"Unexpected event-window capture dirs: {event_window_dirs}"
            )

    def test_no_systemd_service_added(self):
        svc_dir = (
            ROOT / "examples" / "strategies" / "venue_agnostic_signal_observer" / "systemd"
        )
        if svc_dir.exists():
            ew_services = [
                f for f in svc_dir.iterdir()
                if "event_window" in f.name.lower()
            ]
            assert not ew_services, (
                f"Unexpected event-window systemd services: {ew_services}"
            )

    def test_event_list_not_populated(self):
        """The event list template must not contain populated future events."""
        path = ROOT / "EVENT_WINDOW_EVENT_LIST_TEMPLATE.md"
        text = path.read_text(encoding="utf-8")
        assert "No events have been populated" in text
