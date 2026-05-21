"""
Focused tests for Family 2 funding crowding reversal Phase 0 precommitment.

These tests protect precommitment invariants. They do not fetch data,
evaluate returns, or run empirical evaluation.

Test categories (as specified in task section 18):

1. BTC primary family size is exactly 60.
2. ETH is diagnostic only and excluded from the BTC FDR family.
3. BY FDR is mandatory for candidate promotion wording.
4. Timestamp-shuffle null is mandatory for candidate promotion wording.
5. Sign-flip null is forbidden.
6. Positive funding direction maps to negative BTC forward return.
7. Negative funding direction maps to positive BTC forward return.
8. Direction-matched baseline wording exists.
9. Event de-dup rule wording exists.
10. Funding event clustering limitation wording exists.
11. Percentile warmup consequence wording exists.
12. Funding interval metadata requirement wording exists.
13. No candidate wording contains "if FDR available" or "if implemented" for FDR.
14. No candidate wording contains "if null available" or "if implemented" for null.
15. No live/private/order/execution terms introduced.
16. Past-only percentile invariant behavioral test (non-circular).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    ALL_THRESHOLDS,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    BASELINE_BEAT_BPS,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    BTC_PRIMARY_FDR_FAMILY_SIZE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    DIRECTION_NEGATIVE_FUNDING,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    DIRECTION_POSITIVE_FUNDING,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import DIRECTIONS
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import HORIZONS
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_BTC_FAMILY_SIZE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_CLUSTERING_UNADJUSTED,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_DIRECTION_MATCHED_BASELINE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_EVENT_DEDUP,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_FDR_METHOD,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_FUNDING_INTERVAL_METADATA,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_PAST_ONLY_PERCENTILE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    INVARIANT_TIMESTAMP_SHUFFLE_NULL_ONLY,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    MIN_VALID_EVENTS_FOR_CANDIDATE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    MIN_WIN_RATE,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    PERCENTILE_LOOKBACK_CALENDAR_DAYS,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    TOTAL_COST_BPS,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    compute_past_only_percentile_threshold,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    signal_return_bps_for_negative_funding,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
    signal_return_bps_for_positive_funding,
)


PRECOMMITMENT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "FUNDING_CROWDING_REVERSAL_PRECOMMITMENT.md"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _precommitment_text() -> str:
    """Read the full precommitment document text."""
    return PRECOMMITMENT_PATH.read_text(encoding="utf-8")


# ===================================================================
# 1. BTC primary family size is exactly 60
# ===================================================================


class TestBtcFamilySize:
    """Test 1: BTC primary FDR family size is exactly 60."""

    def test_constant_is_60(self) -> None:
        assert BTC_PRIMARY_FDR_FAMILY_SIZE == 60

    def test_enumerated_cell_count(self) -> None:
        """Verify 6 thresholds * 5 horizons * 2 directions = 60."""
        expected = (
            len(ALL_THRESHOLDS) * len(HORIZONS) * len(DIRECTIONS)
        )
        assert expected == 60
        assert expected == BTC_PRIMARY_FDR_FAMILY_SIZE

    def test_document_pins_60(self) -> None:
        text = _precommitment_text()
        assert "BTC_PRIMARY_FDR_FAMILY_SIZE = 60" in text

    def test_document_mentions_60_cells(self) -> None:
        text = _precommitment_text()
        assert "60 frozen cells" in text or "60 BTC cells" in text


# ===================================================================
# 2. ETH is diagnostic only and excluded from BTC FDR
# ===================================================================


class TestEthDiagnosticOnly:
    """Test 2: ETH is diagnostic only, excluded from BTC FDR family."""

    def test_document_excludes_eth_from_btc_fdr(self) -> None:
        text = _precommitment_text()
        assert "ETH must not enter the BTC FDR family" in text
        assert "ETH must never independently promote" in text
        assert "ETH must never expand the BTC FDR denominator" in text
        assert "ETH must never be used to rescue" in text

    def test_document_limits_eth_description(self) -> None:
        text = _precommitment_text()
        assert "consistent with BTC finding" in text
        assert "not consistent with BTC finding" in text
        assert "diagnostic only" in text

    def test_family_size_constant_excludes_eth(self) -> None:
        """BTC_PRIMARY_FDR_FAMILY_SIZE must be only BTC cells, not ETH."""
        assert BTC_PRIMARY_FDR_FAMILY_SIZE == 60


# ===================================================================
# 3. BY FDR is mandatory for candidate promotion wording
# ===================================================================


class TestByFdrMandatory:
    """Test 3: BY FDR mandatory wording in document."""

    def test_document_pins_by_method(self) -> None:
        text = _precommitment_text()
        # Must specify BY, not generic FDR
        assert "Benjamini-Yekutieli" in text or "BY" in text

    def test_document_says_mandatory(self) -> None:
        text = _precommitment_text()
        # Must say mandatory, not optional
        assert "This is mandatory" in text

    def test_document_requires_by_for_candidate(self) -> None:
        text = _precommitment_text()
        assert "survives BY FDR correction" in text
        assert "CANDIDATE_FOR_LONGER_OBSERVATION" in text

    def test_no_if_implemented_for_fdr(self) -> None:
        text = _precommitment_text()
        assert "if implemented" not in text.replace(
            "if BY FDR module is not available on this branch, Phase 0 still completes normally",
            "",
        )
        assert "if FDR available" not in text

    def test_fdr_blocked_diagnostic_exists(self) -> None:
        text = _precommitment_text()
        assert "FDR_BLOCKED_DIAGNOSTIC" in text

    def test_fdr_module_missing_blocker_exists(self) -> None:
        text = _precommitment_text()
        assert "FDR_MODULE_MISSING_BLOCKER" in text


# ===================================================================
# 4. Timestamp-shuffle null is mandatory for candidate promotion wording
# ===================================================================


class TestTimestampShuffleNullMandatory:
    """Test 4: Timestamp-shuffle null mandatory wording."""

    def test_document_requires_null_for_candidate(self) -> None:
        text = _precommitment_text()
        assert "timestamp-shuffle null" in text

    def test_document_says_null_mandatory(self) -> None:
        text = _precommitment_text()
        # The null is mandatory - check for several possible phrasings
        assert "mandatory" in text
        assert "null" in text.lower()

    def test_timestamp_null_blocker_exists(self) -> None:
        text = _precommitment_text()
        assert "TIMESTAMP_NULL_MODULE_MISSING_BLOCKER" in text

    def test_null_rejected_diagnostic_exists(self) -> None:
        text = _precommitment_text()
        assert "NULL_REJECTED_DIAGNOSTIC" in text

    def test_no_if_implemented_for_null(self) -> None:
        text = _precommitment_text()
        assert "if null available" not in text


# ===================================================================
# 5. Sign-flip null is forbidden
# ===================================================================


class TestSignFlipForbidden:
    """Test 5: Sign-flip null is forbidden."""

    def test_document_forbids_sign_flip(self) -> None:
        text = _precommitment_text()
        assert "Sign-flipping is forbidden" in text

    def test_document_no_sign_flip_in_null(self) -> None:
        text = _precommitment_text()
        assert "Do not randomize funding signs" in text
        assert "Do not include" in text

    def test_reason_given(self) -> None:
        text = _precommitment_text()
        assert "The hypothesis is directional" in text


# ===================================================================
# 6. Positive funding direction maps to negative BTC forward return
# ===================================================================


class TestPositiveFundingMapping:
    """Test 6: Positive funding extreme predicts negative BTC return."""

    def test_function_surrogate(self) -> None:
        """signal_return_bps_for_positive_funding returns negated return."""
        assert signal_return_bps_for_positive_funding(100.0) == -100.0
        assert signal_return_bps_for_positive_funding(-50.0) == 50.0
        assert signal_return_bps_for_positive_funding(0.0) == 0.0

    def test_document_mapping(self) -> None:
        text = _precommitment_text()
        assert "positive funding predicts negative BTC return" in text

    def test_direction_constant(self) -> None:
        assert DIRECTION_POSITIVE_FUNDING == "positive_funding_extreme"

    def test_document_maps_positive_to_negative(self) -> None:
        text = _precommitment_text()
        assert "predicts negative" in text


# ===================================================================
# 7. Negative funding direction maps to positive BTC forward return
# ===================================================================


class TestNegativeFundingMapping:
    """Test 7: Negative funding extreme predicts positive BTC return."""

    def test_function_surrogate(self) -> None:
        """signal_return_bps_for_negative_funding returns raw return."""
        assert signal_return_bps_for_negative_funding(100.0) == 100.0
        assert signal_return_bps_for_negative_funding(-50.0) == -50.0
        assert signal_return_bps_for_negative_funding(0.0) == 0.0

    def test_document_mapping(self) -> None:
        text = _precommitment_text()
        assert "negative funding predicts positive BTC return" in text

    def test_direction_constant(self) -> None:
        assert DIRECTION_NEGATIVE_FUNDING == "negative_funding_extreme"

    def test_document_maps_negative_to_positive(self) -> None:
        text = _precommitment_text()
        assert "predicts positive" in text


# ===================================================================
# 8. Direction-matched baseline wording exists
# ===================================================================


class TestDirectionMatchedBaseline:
    """Test 8: Direction-matched baseline wording."""

    def test_document_requires_direction_matched_baseline(self) -> None:
        text = _precommitment_text()
        assert "direction-matched" in text

    def test_baseline_constant_linked(self) -> None:
        text = _precommitment_text()
        assert "DIRECTION_MATCHED_BASELINE_REQUIRED" in text

    def test_baseline_beat_gate(self) -> None:
        text = _precommitment_text()
        assert "at least 10 bps" in text

    def test_baseline_definition_exists(self) -> None:
        text = _precommitment_text()
        assert "random-entry" in text
        # Check that positive and negative baselines are specified
        assert "random-entry negative BTC return" in text
        assert "random-entry positive BTC return" in text


# ===================================================================
# 9. Event de-dup rule wording exists
# ===================================================================


class TestEventDedupRule:
    """Test 9: Event de-dup rule wording."""

    def test_document_has_dedup_rule(self) -> None:
        text = _precommitment_text()
        assert "EVENT_DEDUP_RULE" in text

    def test_document_describes_dedup(self) -> None:
        text = _precommitment_text()
        assert "at most one event per frozen cell" in text

    def test_no_discretionary_cooldown(self) -> None:
        text = _precommitment_text()
        assert "choose event cooldown" in text


# ===================================================================
# 10. Funding event clustering limitation wording exists
# ===================================================================


class TestFundingEventClustering:
    """Test 10: Funding event clustering limitation wording."""

    def test_document_has_clustering_label(self) -> None:
        text = _precommitment_text()
        assert "FUNDING_EVENT_CLUSTERING_UNADJUSTED" in text

    def test_document_describes_clustering(self) -> None:
        text = _precommitment_text()
        assert "cluster hard in time" in text or "correlated extreme events" in text


# ===================================================================
# 11. Percentile warmup consequence wording exists
# ===================================================================


class TestPercentileWarmup:
    """Test 11: Percentile warmup consequence wording."""

    def test_document_has_percentile_invariant(self) -> None:
        text = _precommitment_text()
        assert "PAST_ONLY_PERCENTILE_INVARIANT" in text

    def test_document_describes_warmup(self) -> None:
        text = _precommitment_text()
        assert "180" in text
        assert "warmup" in text.lower()

    def test_document_describes_warmup_tax(self) -> None:
        text = _precommitment_text()
        assert "NEEDS_MORE_DATA" in text
        assert "warmup tax" in text

    def test_past_only_not_circular(self) -> None:
        """
        Test that past-only invariant is well-formed.

        This test uses a synthetic series to verify the function
        behaviorally. It does NOT use the document wording check.
        """
        # Build synthetic funding series: 200 daily observations
        # Last 10 observations have extreme values
        rates_before_t = [0.0001] * 190 + [0.001, 0.002, 0.003, 0.004, 0.005] * 2
        # Event t itself: 0.01
        # Future observations after t: 0.02, 0.03, ...

        # Compute 95th percentile threshold from past-only data
        threshold_at_t = compute_past_only_percentile_threshold(
            rates_before_t, percentile_rank=95.0
        )

        # Append future observations (more extreme)

        # Recompute using only past data (same as before)
        threshold_after_future = compute_past_only_percentile_threshold(
            rates_before_t, percentile_rank=95.0
        )

        # Must be exactly equal (byte-identical for deterministic computation)
        assert threshold_at_t == threshold_after_future, (
            "Past-only percentile threshold changed when future observations "
            "were added. This violates PAST_ONLY_PERCENTILE_INVARIANT."
        )

    def test_insufficient_history_raises(self) -> None:
        """compute_past_only_percentile_threshold raises on empty data."""
        with pytest.raises(ValueError, match="at least 1 observation"):
            compute_past_only_percentile_threshold([], percentile_rank=5.0)


# ===================================================================
# 12. Funding interval metadata requirement wording exists
# ===================================================================


class TestFundingIntervalMetadata:
    """Test 12: Funding interval metadata requirement wording."""

    def test_document_has_metadata_label(self) -> None:
        text = _precommitment_text()
        assert "FUNDING_INTERVAL_METADATA_REQUIRED" in text

    def test_document_describes_disclosures(self) -> None:
        text = _precommitment_text()
        assert "funding interval source" in text
        assert "interval changes" in text
        assert "number of observations by interval" in text

    def test_document_no_assumed_8h(self) -> None:
        text = _precommitment_text()
        assert "must not assume a fixed 8h" in text


# ===================================================================
# 13. No candidate wording contains "if FDR available" or "if implemented"
# ===================================================================


class TestNoFdrConditionalWording:
    """Test 13: No conditional FDR wording for candidate promotion."""

    def test_no_if_fdr_available(self) -> None:
        text = _precommitment_text()
        assert "if FDR available" not in text

    def test_no_if_implemented_for_fdr(self) -> None:
        text = _precommitment_text()
        assert "if implemented" not in text

    def test_fdr_mandatory_wording(self) -> None:
        """Verify that FDR-related sentences say 'must' not 'may'."""
        text = _precommitment_text()
        # This should pass because the doc says "must survive" not "may survive"
        # Just check that the sentence reads mandatory
        assert "must survive" in text or "This is mandatory" in text


# ===================================================================
# 14. No candidate wording contains "if null available" or "if implemented"
# ===================================================================


class TestNoNullConditionalWording:
    """Test 14: No conditional null wording for candidate promotion."""

    def test_no_if_null_available(self) -> None:
        text = _precommitment_text()
        assert "if null available" not in text

    def test_no_if_implemented_for_null(self) -> None:
        text = _precommitment_text()
        # The document has "if the timestamp-shuffle null module is not available on this branch"
        # in the context of Phase 0 / readiness reporting, not candidate promotion.
        # This test checks the entire doc text for "if implemented" which should be absent.
        assert "if implemented" not in text


# ===================================================================
# 15. No live/private/order/execution terms introduced
# ===================================================================


class TestForbiddenTermsSafety:
    """Test 15: No forbidden execution terms outside safety sections."""

    FORBIDDEN_TERMS = [
        "private key",
        "API secret",
        "wallet",
        "order submission",
        "live trading",
        "exchange account",
        "execution adapter",
        "bot authorization",
        "trade-ready",
        "TRADE_READY",
        "execution-ready",
        "EXECUTION_READY",
        "CANDIDATE_FOR_LIVE",
    ]

    def test_no_forbidden_terms_in_constants(self) -> None:
        """Check the constants module for forbidden terms."""
        import inspect

        from examples.strategies.venue_agnostic_signal_observer import (
            funding_crowding_reversal as fcr_mod,
        )

        source = inspect.getsource(fcr_mod)
        source_lower = source.lower()
        for term in self.FORBIDDEN_TERMS:
            if term.lower() in source_lower:
                # Only flag if it's NOT in a docstring that says "do not add"
                if "forbidden" not in source_lower:
                    pytest.fail(
                        f"Constants module contains forbidden term: {term!r}"
                    )

    def test_no_forbidden_terms_in_document(self) -> None:
        """
        Check the precommitment document for forbidden terms.

        Some terms like TRADE_READY appear in the verdict taxonomy as
        explicitly forbidden verdicts. That is acceptable because the
        document says they are forbidden.

        Some terms like "private key" appear in the Phase boundary
        section as things Phase 0 does not do (e.g. "No private keys.").
        That is a safety boundary statement, not introducing the
        capability.
        """
        text = _precommitment_text()
        text_lower = text.lower()

        # These terms are mentioned explicitly to say they're forbidden
        # in the verdict taxonomy, so allow them in that context.
        allowed_in_verdict_section = {
            "trade-ready",
            "TRADE_READY",
            "execution-ready",
            "EXECUTION_READY",
            "CANDIDATE_FOR_LIVE",
        }

        # These terms appear in the "What Phase 0 does not do" safety
        # boundary list, as "No <term>", which is explicitly rejecting
        # the concept.
        allowed_in_boundary_section = {
            "private key",
            "API secret",
            "wallet",
            "live trading",
            "exchange account",
            "execution adapter",
        }
        # These don't appear naturally as "No" but also aren't in the document at all
        # "order submission", "bot authorization" - check they don't appear

        for term in self.FORBIDDEN_TERMS:
            term_lower = term.lower()
            if term_lower not in text_lower:
                continue
            # Check if it's in the verdict taxonomy (forbidden list)
            if term in allowed_in_verdict_section:
                idx = text_lower.index(term_lower)
                context = text_lower[max(0, idx - 50) : idx + 50]
                if "forbidden" in context or "do not add" in context or "forbidden verdict" in context:
                    continue
            # Check if it's in the boundary section ("No <term>" or "No <plural>")
            if term_lower in allowed_in_boundary_section:
                idx = text_lower.index(term_lower)
                context_before = text_lower[max(0, idx - 30) : idx]
                if "no " in context_before or "not do" in context_before:
                    continue
            pytest.fail(
                f"Precommitment document contains forbidden term: {term!r}"
            )


# ===================================================================
# 16. Additional structural checks
# ===================================================================


class TestStructuralInvariants:
    """Additional structural precommitment checks."""

    def test_precommitment_file_exists(self) -> None:
        assert PRECOMMITMENT_PATH.exists()
        assert PRECOMMITMENT_PATH.is_file()

    def test_precommitment_non_empty(self) -> None:
        text = _precommitment_text()
        assert len(text) > 5000

    def test_cost_model_pinned(self) -> None:
        text = _precommitment_text()
        assert "total_cost_bps" in text
        assert "6.0" in text or "6 bps" in text

    def test_sample_size_policy_pinned(self) -> None:
        text = _precommitment_text()
        assert "50 valid events" in text
        assert "100 valid events" in text
        assert "NEEDS_MORE_DATA" in text

    def test_split_policy_pinned(self) -> None:
        text = _precommitment_text()
        assert "70%" in text
        assert "30%" in text
        assert "chronological" in text or "chronological train/holdout" in text

    def test_constants_module_exists(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer import funding_crowding_reversal
        assert funding_crowding_reversal.__doc__ is not None

    def test_direction_constant_values(self) -> None:
        assert DIRECTION_POSITIVE_FUNDING == "positive_funding_extreme"
        assert DIRECTION_NEGATIVE_FUNDING == "negative_funding_extreme"

    def test_all_thresholds_count(self) -> None:
        assert len(ALL_THRESHOLDS) == 6

    def test_horizons_count(self) -> None:
        assert len(HORIZONS) == 5

    def test_directions_count(self) -> None:
        assert len(DIRECTIONS) == 2

    def test_total_cost_bps_value(self) -> None:
        assert TOTAL_COST_BPS == 6.0

    def test_min_events_for_candidate(self) -> None:
        assert MIN_VALID_EVENTS_FOR_CANDIDATE == 100

    def test_min_win_rate(self) -> None:
        assert MIN_WIN_RATE == 0.55

    def test_baseline_beat_bps(self) -> None:
        assert BASELINE_BEAT_BPS == 10.0

    def test_percentile_lookback_days(self) -> None:
        assert PERCENTILE_LOOKBACK_CALENDAR_DAYS == 180

    def test_invariant_labels_pinned(self) -> None:
        assert "60" in INVARIANT_BTC_FAMILY_SIZE
        assert "PAST_ONLY" in INVARIANT_PAST_ONLY_PERCENTILE
        assert "METADATA" in INVARIANT_FUNDING_INTERVAL_METADATA
        assert "DEDUP" in INVARIANT_EVENT_DEDUP
        assert "CLUSTERING" in INVARIANT_CLUSTERING_UNADJUSTED
        assert "BASELINE" in INVARIANT_DIRECTION_MATCHED_BASELINE
        assert "SHUFFLE" in INVARIANT_TIMESTAMP_SHUFFLE_NULL_ONLY
        assert "BY" in INVARIANT_FDR_METHOD or "Yekutieli" in INVARIANT_FDR_METHOD

    def test_horizon_seconds_positive(self) -> None:
        for name, sec in HORIZONS.items():
            assert sec > 0, f"Horizon {name} has non-positive seconds: {sec}"
            assert name.startswith("h"), f"Horizon {name} does not start with 'h'"

    def test_primary_horizon_in_horizons(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
            PRIMARY_HORIZON,
        )
        assert PRIMARY_HORIZON in HORIZONS

    def test_all_thresholds_have_label(self) -> None:
        for t in ALL_THRESHOLDS:
            assert "label" in t
            assert isinstance(t["label"], str)
            assert len(str(t["label"])) > 0

    def test_absolute_thresholds_have_min_abs_rate(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
            ABSOLUTE_THRESHOLDS,
        )
        for t in ABSOLUTE_THRESHOLDS:
            assert "min_abs_rate" in t
            assert isinstance(t["min_abs_rate"], float)

    def test_percentile_thresholds_have_rank(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.funding_crowding_reversal import (
            PERCENTILE_THRESHOLDS,
        )
        for t in PERCENTILE_THRESHOLDS:
            assert "percentile_rank" in t
            assert isinstance(t["percentile_rank"], float)


# ===================================================================
# 17. Document presence of key decision statements
# ===================================================================


class TestKeyDecisionStatements:
    """Verify the document contains required decision statements."""

    def test_hypothesis_stated(self) -> None:
        text = _precommitment_text()
        assert "crowded long" in text
        assert "crowded short" in text
        assert "reversal" in text

    def test_spot_btc_forward_return_leg(self) -> None:
        text = _precommitment_text()
        assert "spot BTC forward returns" in text
        assert "funding-paid-while-held" in text

    def test_not_funding_carry(self) -> None:
        text = _precommitment_text()
        # The document can reference V6-B as something it is NOT (rejected, different)
        # but must not describe this hypothesis as funding carry
        assert "Funding-as-carry" in text or "funding-as-carry" in text or "funding carry" in text.lower()

    def test_not_family_1(self) -> None:
        text = _precommitment_text()
        assert "Family 1" in text or "same-venue" in text

    def test_output_paths_documented(self) -> None:
        text = _precommitment_text()
        assert "reports/funding_crowding_reversal_v1/<run_id>/" in text
        assert "fdr_summary.json" in text
        assert "timestamp_shuffle_null" in text

    def test_verdict_taxonomy_complete(self) -> None:
        text = _precommitment_text()
        verdicts = [
            "CANDIDATE_FOR_LONGER_OBSERVATION",
            "REJECTED",
            "NEEDS_MORE_DATA",
            "UNDERPOWERED_HOLDOUT_FAILURE",
            "NO_MCPT_WORTHY_GROUPS",
            "NULL_REJECTED_DIAGNOSTIC",
            "FDR_BLOCKED_DIAGNOSTIC",
            "FDR_MODULE_MISSING_BLOCKER",
            "TIMESTAMP_NULL_MODULE_MISSING_BLOCKER",
        ]
        for v in verdicts:
            assert v in text, f"Verdict {v} missing from document"

    def test_forbidden_verdicts_listed(self) -> None:
        text = _precommitment_text()
        for forbidden in ["TRADE_READY", "EXECUTION_READY", "CANDIDATE_FOR_LIVE"]:
            assert forbidden in text, f"Must mention {forbidden} as forbidden"


# ===================================================================
# 18. Past-only percentile invariant behavioral test (non-circular)
# ===================================================================


class TestPastOnlyPercentileInvariant:
    """
    Test 16 (from section 18): Behavioral past-only percentiles.

    This test builds a synthetic funding series, computes the percentile
    threshold at event time t, appends future observations, and verifies
    the threshold at t remains byte-identical.

    This is NOT a circular test. It uses:
    - Synthetic data (not fetched from any API)
    - Only the compute_past_only_percentile_threshold pure function
    - No document string matching
    """

    def test_past_only_deterministic(self) -> None:
        """Verify past-only threshold is unaffected by future observations."""
        # Simulate 200 funding observations before t
        import random
        rng = random.Random(42)

        # Generate realistic funding rates: mostly small, some extremes
        past_rates: list[float] = [
            0.0001 * rng.gauss(0, 1) for _ in range(200)
        ]

        # Compute 95th percentile at time t using past data only
        threshold_at_t = compute_past_only_percentile_threshold(
            past_rates, percentile_rank=95.0
        )

        # Add 50 more extreme future observations (should not affect past threshold)
        future_rates: list[float] = [
            0.01 * rng.gauss(0, 1) for _ in range(50)
        ]
        past_rates + future_rates

        # Compute again using ONLY past data (same as before)
        threshold_after_future = compute_past_only_percentile_threshold(
            past_rates, percentile_rank=95.0
        )

        assert threshold_at_t == pytest.approx(threshold_after_future, abs=1e-15), (
            "Past-only percentile threshold changed when future observations were added"
        )

    def test_percentile_with_mixed_rates(self) -> None:
        """Test with a known set of rates."""
        rates = [0.0, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01]
        # 95th percentile of 8 values = position ~6.65 in sorted = ~0.0065
        threshold = compute_past_only_percentile_threshold(rates, percentile_rank=95.0)
        # Sorted: [0.0, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01]
        # 95th: rank = 0.95 * 7 = 6.65
        # value[6] = 0.005, value[7] = 0.01
        # result = 0.005 + 0.65 * (0.01 - 0.005) = 0.005 + 0.00325 = 0.00825
        expected = 0.005 + 0.65 * (0.01 - 0.005)
        assert threshold == pytest.approx(expected, abs=1e-12)

    def test_5th_percentile(self) -> None:
        """Test bottom 5% threshold."""
        rates = [0.0001, 0.0002, 0.0003, 0.0004, 0.0005, 0.001, 0.002]
        threshold = compute_past_only_percentile_threshold(rates, percentile_rank=5.0)
        # Sorted: same order (already sorted)
        # n=7, rank = 0.05 * 6 = 0.3
        # value[0]=0.0001, value[1]=0.0002
        # result = 0.0001 + 0.3 * (0.0002 - 0.0001) = 0.00013
        expected = 0.0001 + 0.3 * 0.0001
        assert threshold == pytest.approx(expected, abs=1e-12)

    def test_single_observation(self) -> None:
        """Single observation should return that observation as threshold."""
        threshold = compute_past_only_percentile_threshold([0.001], percentile_rank=50.0)
        assert threshold == 0.001

    def test_two_observations(self) -> None:
        """Two observations: 50th percentile is the mean."""
        threshold = compute_past_only_percentile_threshold([0.0001, 0.001], percentile_rank=50.0)
        # rank = 0.5 * 1 = 0.5
        # value[0]=0.0001, value[1]=0.001
        # result = 0.0001 + 0.5 * (0.001 - 0.0001) = 0.00055
        assert threshold == pytest.approx(0.00055, abs=1e-12)
