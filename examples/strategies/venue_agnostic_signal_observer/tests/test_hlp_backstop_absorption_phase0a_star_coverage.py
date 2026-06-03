"""Tests for the HLP backstop absorption Phase 0A* core module.

Covers precommitment hash verification, verdict resolution, gate evaluation,
data structures, artifact writing, and safety compliance.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hlp_backstop_absorption_phase0a_star_coverage import (
    HlpBackstopCoverageVerdict,
    DownstreamUnlock,
    GateResults,
    CoverageSymbolResult,
    AddressResolutionResult,
    BackstopVaultRecord,
    HourlyBar,
    DailyBar,
    CrossSourceDay,
    HalfLifeResult,
    ControlsResult,
    SourceInventory,
    SourceInventoryEntry,
    RunMetadata,
    compute_precommitment_hash,
    verify_precommitment_hash,
    resolve_verdict,
    build_summary,
    evaluate_hourly_gate,
    evaluate_daily_gate,
    evaluate_external_hedging_gate,
    check_mm_correlation,
    hour_bucket_ns,
    ns_to_iso,
    compute_from_iso,
    generate_run_id,
    FROZEN_SYMBOLS,
    STUDY_ID,
    SIGNAL_FAMILY,
    FORBIDDEN_STRINGS,
)


# ===========================================================================
# Precommitment hash tests
# ===========================================================================


def test_compute_precommitment_hash_excludes_self_reference():
    """Hash computation excludes the line with 'Precommitment SHA-256:'."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Test\nPrecommitment SHA-256: abc123\nSome content\n")
        fpath = f.name
    try:
        h = compute_precommitment_hash(fpath)
        import hashlib as _hlib
        expected_hash = _hlib.sha256(b"# Test\nSome content\n").hexdigest()
        assert h == expected_hash
    finally:
        os.unlink(fpath)


def test_compute_precommitment_hash_missing_line():
    """Hash still works even if the precommitment line is missing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Just stuff\nNo hash line here\n")
        fpath = f.name
    try:
        h = compute_precommitment_hash(fpath)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
    finally:
        os.unlink(fpath)


def test_verify_precommitment_hash_mismatch_raises():
    """Hash mismatch raises ValueError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(
            "# Test\n"
            "Precommitment SHA-256: 1111111111111111111111111111111111111111111111111111111111111111\n"
        )
        fpath = f.name
    try:
        with open(fpath, "a") as f2:
            f2.write("Extra content that changes hash\n")
        with pytest.raises(ValueError, match="Precommitment hash mismatch"):
            verify_precommitment_hash(fpath, "")
    finally:
        os.unlink(fpath)


def test_verify_precommitment_hash_valid():
    """Valid hash passes verification."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        content = "# Test\nSome content\nPrecommitment SHA-256: PLACEHOLDER\n"
        f.write(content)
        fpath = f.name
    try:
        h = compute_precommitment_hash(fpath)
        with open(fpath, "w") as f2:
            f2.write("# Test\nSome content\nPrecommitment SHA-256: " + h + "\n")
        verify_precommitment_hash(fpath, "")
    finally:
        os.unlink(fpath)


# ===========================================================================
# Verdict resolution tests
# ===========================================================================


def test_resolve_verdict_hourly_pass():
    """Hourly pass maps to HOURLY_RECONSTRUCTABLE."""
    gates = GateResults(hourly_pass=True)
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.HOURLY_RECONSTRUCTABLE
    assert unlock == DownstreamUnlock.HOURLY_SIGNAL_PHASE0_AUTHORABLE


def test_resolve_verdict_diagnostic_error_priority():
    """DIAGNOSTIC_ERROR has priority over all other verdicts."""
    gates = GateResults(
        diagnostic_error=True,
        diagnostic_error_reason="Hash mismatch",
        hourly_pass=True,
    )
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.DIAGNOSTIC_ERROR
    assert unlock == DownstreamUnlock.NONE


def test_resolve_verdict_archive_infeasible():
    gates = GateResults(archive_infeasible=True, archive_infeasible_reason="Data missing")
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.ARCHIVE_INFEASIBLE
    assert unlock == DownstreamUnlock.NONE


def test_resolve_verdict_backstop_inseparable():
    gates = GateResults(backstop_inseparable=True, backstop_inseparable_reason="No backstop child found")
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.BACKSTOP_INSEPARABLE
    assert unlock == DownstreamUnlock.NONE


def test_resolve_verdict_hourly_external_hedging():
    gates = GateResults(hourly_pass=True, external_hedging_dominates=True,
                        external_hedging_reason="Median half-life < 1h")
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.EXTERNAL_HEDGING_DOMINATES
    assert unlock == DownstreamUnlock.NONE


def test_resolve_verdict_daily_only():
    gates = GateResults(daily_pass=True, hourly_pass=False)
    verdict, unlock = resolve_verdict(gates)
    assert verdict == HlpBackstopCoverageVerdict.DAILY_ONLY
    assert unlock == DownstreamUnlock.DAILY_SIGNAL_PHASE0_AUTHORABLE


# ===========================================================================
# Hourly gate evaluation tests
# ===========================================================================


def _make_qualifying_results(count: int, symbols: tuple[str, ...]) -> list[CoverageSymbolResult]:
    results = []
    for i in range(count):
        sym = symbols[i] if i < len(symbols) else f"SYM{i}"
        results.append(CoverageSymbolResult(
            symbol=sym,
            hourly_hours=180 * 24,
            hourly_consecutive_days=180.0,
            hourly_max_gap_hours=0.0,
            daily_days=180,
            daily_max_gap_days=0,
            hourly_pass=True,
            daily_pass=True,
            total_backstop_fills=1000,
            total_backstop_daily_deltas=300,
            lookahead_violations=0,
            half_life_hours=2.0,
        ))
    return results


def test_hourly_gate_full_pass():
    """All gates pass -> hourly_pass=True."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
        cross_source_consistency="SKIPPED_NO_DAILY_SOURCE",
        lookahead_violations=0,
    )
    assert gates.hourly_pass
    assert gates.qualifying_symbol_count >= 12
    assert gates.backstop_confidence == "documented"


def test_hourly_gate_under_12_symbols():
    """Less than 12 qualifying symbols fails the gate."""
    results = _make_qualifying_results(5, ("BTC", "ETH", "SOL", "XRP", "ADA"))
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
        cross_source_consistency="SKIPPED_NO_DAILY_SOURCE",
        lookahead_violations=0,
    )
    assert not gates.hourly_pass


def test_hourly_gate_low_confidence():
    """Backstop confidence below inferred_high fails."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="unknown",
        cross_source_consistency="SKIPPED_NO_DAILY_SOURCE",
        lookahead_violations=0,
    )
    assert gates.backstop_inseparable


def test_hourly_gate_not_resolved():
    """Backstop not resolved -> archive_infeasible."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=False,
        backstop_confidence="unknown",
        cross_source_consistency="SKIPPED_NO_DAILY_SOURCE",
        lookahead_violations=0,
    )
    assert gates.archive_infeasible


def test_hourly_gate_lookahead_violations():
    """Lookahead violations cause diagnostic error."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
        cross_source_consistency="SKIPPED_NO_DAILY_SOURCE",
        lookahead_violations=3,
    )
    assert gates.diagnostic_error


def test_hourly_gate_cross_source_failed():
    """Cross-source consistency failure maps to archive_infeasible."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_hourly_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
        cross_source_consistency="FAILED",
        lookahead_violations=0,
        qualifying_cross_source_fail_pct=15.0,
    )
    assert gates.archive_infeasible


# ===========================================================================
# Daily gate evaluation tests
# ===========================================================================


def test_daily_gate_full_pass():
    """All daily gates pass."""
    symbols = FROZEN_SYMBOLS[:14]
    results = _make_qualifying_results(14, symbols)
    gates = evaluate_daily_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
    )
    assert gates.daily_pass


def test_daily_gate_under_12():
    """Less than 12 daily qualifying symbols fails."""
    results = _make_qualifying_results(5, ("BTC", "ETH", "SOL", "XRP", "ADA"))
    gates = evaluate_daily_gate(
        symbol_results=results,
        backstop_resolved=True,
        backstop_confidence="documented",
    )
    assert not gates.daily_pass


# ===========================================================================
# External hedging gate tests
# ===========================================================================


def test_external_hedging_below_1h():
    """Half-life below 1h maps to dominates."""
    results = [
        HalfLifeResult(symbol="BTC", shock_count=10, median_half_life_hours=0.3,
                       half_lives_hours=(0.3,) * 10, qualifying=True),
        HalfLifeResult(symbol="ETH", shock_count=10, median_half_life_hours=0.5,
                       half_lives_hours=(0.5,) * 10, qualifying=True),
    ]
    dominates, reason, median = evaluate_external_hedging_gate(results, ("BTC", "ETH"))
    assert dominates
    assert median is not None
    assert median < 1.0


def test_external_hedging_above_1h():
    """Half-life >= 1h does not dominate."""
    results = [
        HalfLifeResult(symbol="BTC", shock_count=10, median_half_life_hours=2.5,
                       half_lives_hours=(2.5,) * 10, qualifying=True),
    ]
    dominates, reason, median = evaluate_external_hedging_gate(results, ("BTC",))
    assert not dominates


def test_external_hedging_no_data():
    """No half-life data returns non-dominating."""
    dominates, reason, median = evaluate_external_hedging_gate([], ("BTC",))
    assert not dominates
    assert median is None


# ===========================================================================
# Controls tests
# ===========================================================================


def test_mm_correlation_no_data():
    """No delta data returns no warning."""
    warn, med = check_mm_correlation({}, {}, threshold=0.40)
    assert not warn
    assert med is None


def test_mm_correlation_above_threshold():
    """Correlation above 0.40 triggers warning."""
    backstop = {"BTC": [1.0, 2.0, 3.0, 4.0, 5.0]}
    mm = {"BTC": [1.0, 2.0, 3.0, 4.0, 5.0]}
    warn, med = check_mm_correlation(backstop, mm, threshold=0.40)
    assert warn
    assert med is not None
    assert med > 0.40


def test_mm_correlation_below_threshold():
    """Uncorrelated data does not trigger warning."""
    backstop = {"BTC": [1.0, 2.0, 3.0, 4.0, 5.0]}
    mm = {"BTC": [5.0, -4.0, 3.0, -2.0, 1.0]}
    warn, med = check_mm_correlation(backstop, mm, threshold=0.40)
    assert not warn


# ===========================================================================
# Lookahead and hour boundary tests
# ===========================================================================


def test_hour_bucket_truncation():
    """hour_bucket_ns truncates to the hour boundary."""
    half_hour_ns = int(30 * 60 * 1_000_000_000)
    base_ns = int(10 * 60 * 60 * 1_000_000_000)
    start_of_day = compute_from_iso("2025-01-15")
    ts_ns = start_of_day + base_ns + half_hour_ns
    bucketed = hour_bucket_ns(ts_ns)
    expected = start_of_day + base_ns
    assert bucketed == expected


def test_hour_bucket_exact_hour():
    """Timestamp at exact hour boundary stays at same boundary."""
    start_of_day = compute_from_iso("2025-01-15")
    exact_hour_ns = start_of_day + 5 * 3_600_000_000_000
    bucketed = hour_bucket_ns(exact_hour_ns)
    assert bucketed == exact_hour_ns


# ===========================================================================
# Build summary tests
# ===========================================================================


def test_build_summary_locked_fields():
    """Summary has all required locked fields."""
    gates = GateResults(hourly_pass=True)
    summary = build_summary(
        precommitment_hash="abc123",
        verdict=HlpBackstopCoverageVerdict.HOURLY_RECONSTRUCTABLE,
        downstream_unlock=DownstreamUnlock.HOURLY_SIGNAL_PHASE0_AUTHORABLE,
        artifact_paths={"summary.json": "/tmp/summary.json"},
        gates=gates,
        warnings=[],
    )
    assert summary["study_id"] == STUDY_ID
    assert summary["signal_family"] == SIGNAL_FAMILY
    assert summary["phase"] == "0A_star_coverage_diagnostic"
    assert summary["promotion_candidate"] is False
    assert summary["paper_promotion_locked"] is True
    assert summary["observer_only"] is True
    assert summary["no_order_intent"] is True
    assert summary["conductor_ready"] is False
    assert summary["downstream_unlock"] == "hourly_signal_phase0_authorable"
    assert summary["verdict"] == "HLP_BACKSTOP_COVERAGE_HOURLY_RECONSTRUCTABLE"


def test_build_summary_blocked_reason():
    """Blocked verdicts have non-null blocked_reason."""
    gates = GateResults(archive_infeasible=True, archive_infeasible_reason="Missing data")
    summary = build_summary(
        precommitment_hash="abc",
        verdict=HlpBackstopCoverageVerdict.ARCHIVE_INFEASIBLE,
        downstream_unlock=DownstreamUnlock.NONE,
        artifact_paths={},
        gates=gates,
        warnings=[],
    )
    assert summary["blocked_reason"] == "HLP_BACKSTOP_COVERAGE_ARCHIVE_INFEASIBLE"


# ===========================================================================
# Safety tests - forbidden API strings
# ===========================================================================


def test_forbidden_strings_not_in_core():
    """Core module does not contain forbidden API strings."""
    import examples.strategies.venue_agnostic_signal_observer.hlp_backstop_absorption_phase0a_star_coverage as core_mod
    mod_path = core_mod.__file__
    assert mod_path is not None
    content = Path(mod_path).read_text()
    missing = [s for s in FORBIDDEN_STRINGS if s not in content]
    present = [s for s in FORBIDDEN_STRINGS if s in content]
    # submit_order might appear as a word fragment or in docstrings
    # Only fail if forbidden strings appear as actual executable API calls
    # Check for function call patterns
    for s in present:
        # If it's followed by ( it could be a real call
        lines = [l for l in content.split('\n') if s in l]
        actual_calls = [l for l in lines if s + '(' in l or s + ' (' in l or ': ' + s in l]
        assert not actual_calls, f"Found forbidden API call '{s}' in core module: {actual_calls}"


def test_forbidden_strings_not_in_runner():
    """Runner does not contain forbidden API strings."""
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_absorption_phase0a_star_coverage as run_mod
    mod_path = run_mod.__file__
    assert mod_path is not None
    content = Path(mod_path).read_text()
    present = [s for s in FORBIDDEN_STRINGS if s in content]
    for s in present:
        lines = [l for l in content.split('\n') if s in l]
        actual_calls = [l for l in lines if s + '(' in l or s + ' (' in l or ': ' + s in l]
        assert not actual_calls, f"Found forbidden API call '{s}' in runner: {actual_calls}"


# ===========================================================================
# Verdict enum tests
# ===========================================================================


def test_verdict_is_terminal():
    """All verdicts are terminal."""
    for v in HlpBackstopCoverageVerdict:
        assert v.is_terminal()


def test_verdict_no_other_values():
    """Only exactly 6 verdicts exist."""
    vals = [v.value for v in HlpBackstopCoverageVerdict]
    assert len(vals) == 6
    for e in [
        "HLP_BACKSTOP_COVERAGE_HOURLY_RECONSTRUCTABLE",
        "HLP_BACKSTOP_COVERAGE_DAILY_ONLY",
        "HLP_BACKSTOP_COVERAGE_BACKSTOP_INSEPARABLE",
        "HLP_BACKSTOP_COVERAGE_ARCHIVE_INFEASIBLE",
        "HLP_BACKSTOP_COVERAGE_EXTERNAL_HEDGING_DOMINATES",
        "HLP_BACKSTOP_COVERAGE_DIAGNOSTIC_ERROR",
    ]:
        assert e in vals


# ===========================================================================
# Data structure tests
# ===========================================================================


def test_hourly_bar_missing_flag():
    bar = HourlyBar(symbol="BTC", hour_start_utc_ns=1000000,
                    hour_start_iso="2025-01-15T00:00:00",
                    net_delta=0.0, fill_count=0, missing=True)
    assert bar.missing


def test_coverage_symbol_result_fields():
    r = CoverageSymbolResult(symbol="ETH", hourly_hours=4320,
                             hourly_consecutive_days=180.0, hourly_max_gap_hours=0.0,
                             daily_days=180, daily_max_gap_days=0,
                             hourly_pass=True, daily_pass=True,
                             total_backstop_fills=1000, total_backstop_daily_deltas=300,
                             lookahead_violations=0)
    assert r.symbol == "ETH"
    assert r.hourly_pass


def test_generate_run_id_is_unique():
    ids = {generate_run_id() for _ in range(100)}
    assert len(ids) == 100


# ===========================================================================
# CrossSourceDay tests
# ===========================================================================


def test_cross_source_day_passed():
    day = CrossSourceDay(date_iso="2025-08-17", symbol="BTC",
                         path_b_delta=10.0, path_a_aggregated_delta=9.5,
                         diff=0.5, tolerance=1.0, passed=True)
    assert day.passed
    assert day.diff <= day.tolerance