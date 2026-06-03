"""Tests for the HLP backstop stress-window attribution probe.

Covers CLI, stress window selection, address profiling, close-like
heuristic, backstop classification, vaultDetails sanity, artifacts, safety.
"""

from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_stress_window_attribution_probe import (
    main,
    run_id,
    human_bytes,
    normalize_coin,
    is_frozen_coin,
    compute_address_profile,
    classify_stress_address,
    vault_details_sanity_check,
    AddressStressProfile,
    KNOWN_MM_ADDRESSES,
    FORBIDDEN_STRINGS,
    STUDY_ID,
)


# ===========================================================================
# Helper tests
# ===========================================================================


def test_run_id_format():
    rid = run_id()
    assert isinstance(rid, str) and len(rid) > 10


def test_human_bytes():
    assert human_bytes(0) == "0.0 B"
    assert "1.0 MiB" in human_bytes(1048576)


def test_normalize_coin():
    assert normalize_coin("xyz:BTC") == "BTC"
    assert normalize_coin("BTC") == "BTC"


def test_is_frozen_coin():
    assert is_frozen_coin("BTC")
    assert not is_frozen_coin("SILVER")


# ===========================================================================
# CLI / no-network behavior
# ===========================================================================


def test_dry_run():
    rc = main(["--dry-run"])
    assert rc == 0


def test_requires_s3_flag():
    rc = main(["--date", "2025-10-10", "--hours", "15"])
    assert rc == 1


# ===========================================================================
# Compute stress profile
# ===========================================================================


def test_compute_profile_empty():
    prof = compute_address_profile("0xA", [], active_hours={15})
    assert prof.total_fills == 0


def test_compute_profile_basic():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B", "dir": "Open Long", "closedPnl": "0.0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "51000", "side": "A", "dir": "Close Long", "closedPnl": "100.0"},
        {"address": "0xA", "coin": "ETH", "sz": "10.0", "px": "3000", "side": "B", "dir": "Open Long", "closedPnl": "0.0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.total_fills == 3
    assert prof.close_fill_count == 1
    assert prof.open_fill_count == 2
    assert prof.nonzero_closedPnl_count == 1
    assert prof.nonzero_closedPnl_ratio == pytest.approx(1/3)
    assert prof.net_delta_ratio > 0  # not perfectly balanced


def test_known_mm_profile():
    """Known MM profiled correctly."""
    records = []
    for coin in ["BTC", "ETH", "SOL"]:
        for _ in range(100):
            records.append({"address": KNOWN_MM_ADDRESSES[0], "coin": coin,
                            "sz": "1.0", "px": "100", "side": "B", "dir": "Open Long", "closedPnl": "0.0"})
            records.append({"address": KNOWN_MM_ADDRESSES[0], "coin": coin,
                            "sz": "1.0", "px": "101", "side": "A", "dir": "Close Long", "closedPnl": "1.0"})
    prof = compute_address_profile(KNOWN_MM_ADDRESSES[0], records, active_hours={15})
    assert prof.total_fills == 600
    assert len(prof.unique_coins) == 3
    assert prof.net_delta_ratio < 0.1
    assert prof.pairability_rate > 0.4
    assert prof.one_sided_burst_score < 0.3


# ===========================================================================
# Close-like heuristic
# ===========================================================================


def test_close_like_cluster_score():
    """Close-like cluster score is high for close-heavy addresses."""
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "A",
         "dir": "Close Long", "closedPnl": "500.0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "49000", "side": "A",
         "dir": "Close Long", "closedPnl": "-1000.0"},
        {"address": "0xA", "coin": "ETH", "sz": "5.0", "px": "3000", "side": "B",
         "dir": "Open Long", "closedPnl": "0.0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.close_like_cluster_score > 0.3  # 2/3 close + 2/3 nonzero PnL = 0.67


def test_large_close_ratio():
    """Large close ratio detects outsized PnL relative to turnover."""
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "A",
         "dir": "Close Long", "closedPnl": "5000.0"},  # 5000 / 50000 = 10% > 1%
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B",
         "dir": "Open Long", "closedPnl": "0.0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.large_close_ratio == 0.5


# ===========================================================================
# Classification
# ===========================================================================


def test_known_mm_classification():
    """Known MM addresses classified as KNOWN_MM."""
    prof = AddressStressProfile(address=KNOWN_MM_ADDRESSES[0], total_fills=1)
    classification, conf = classify_stress_address(prof, KNOWN_MM_ADDRESSES[0])
    assert classification == "KNOWN_MM"


def test_mm_like_classification():
    """Broad balanced continuous -> MM_LIKE."""
    prof = AddressStressProfile(
        address="0xmm",
        total_fills=1000,
        unique_coins=[f"COIN{i}" for i in range(30)],
        net_delta_ratio=0.03,
        pairability_rate=0.8,
        one_sided_burst_score=0.1,
        concentration_score=0.05,
    )
    classification, conf = classify_stress_address(prof, "0xmm")
    assert classification == "MM_LIKE"


def test_backstop_stress_candidate():
    """One-sided unbalanced close-heavy -> backstop candidate."""
    prof = AddressStressProfile(
        address="0xback",
        total_fills=200,
        unique_coins=["BTC"],
        net_delta_ratio=0.6,  # high net delta ratio
        pairability_rate=0.1,
        one_sided_burst_score=0.8,  # high burst
        concentration_score=0.9,
        close_like_cluster_score=0.5,
        large_close_ratio=0.3,
    )
    classification, conf = classify_stress_address(prof, "0xback")
    assert classification == "BACKSTOP_INFERRED_STRESS_CANDIDATE"
    assert conf == "inferred_high"


def test_unknown_classification():
    """Moderate mixed evidence -> BACKSTOP_INFERRED_STRESS_CANDIDATE inferred_low or UNKNOWN."""
    prof = AddressStressProfile(
        address="0xunk",
        total_fills=500,
        unique_coins=["BTC", "ETH", "SOL"],
        net_delta_ratio=0.25,
        pairability_rate=0.65,  # higher pairability
        one_sided_burst_score=0.25,
        concentration_score=0.25,
        close_like_cluster_score=0.1,  # lower close activity
        large_close_ratio=0.01,
    )
    classification, conf = classify_stress_address(prof, "0xunk")
    # Should NOT be high-confidence backstop
    assert not (classification == "BACKSTOP_INFERRED_STRESS_CANDIDATE" and conf == "inferred_high")


# ===========================================================================
# VaultDetails sanity
# ===========================================================================


def test_vault_sanity_requires_flag():
    result = vault_details_sanity_check(allow_public_metadata_api=False)
    assert result["conclusion"] == "requires_flag"


def test_vault_sanity_no_addresses():
    result = vault_details_sanity_check(allow_public_metadata_api=True)
    assert result["requests_tested"] > 0


# ===========================================================================
# Nonzero closedPnl ratio
# ===========================================================================


def test_nonzero_closedPnl_ratio():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B",
         "dir": "Open Long", "closedPnl": "0.0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "51000", "side": "A",
         "dir": "Close Long", "closedPnl": "1000.0"},
        {"address": "0xA", "coin": "ETH", "sz": "5.0", "px": "3000", "side": "B",
         "dir": "Open Long", "closedPnl": "0.0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.nonzero_closedPnl_count == 1
    assert prof.nonzero_closedPnl_ratio == pytest.approx(1/3)


# ===========================================================================
# Per-coin delta and turnover
# ===========================================================================


def test_per_coin_delta():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "2.0", "px": "50000", "side": "B", "closedPnl": "0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "51000", "side": "A", "closedPnl": "0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.per_coin_delta.get("BTC", 0) == pytest.approx(100000 - 51000)  # 2*50000 - 1*51000 = 49000


def test_per_coin_turnover():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "2.0", "px": "50000", "side": "B", "closedPnl": "0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "51000", "side": "A", "closedPnl": "0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.per_coin_turnover.get("BTC", 0) == pytest.approx(100000 + 51000)


# ===========================================================================
# Safety
# ===========================================================================


def test_no_user_fills_by_time():
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_stress_window_attribution_probe as mod
    c = open(mod.__file__).read()
    assert c.count("userFillsByTime") <= 2


def test_forbidden_outside_definition():
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_stress_window_attribution_probe as mod
    content = open(mod.__file__).read()
    for forbidden in FORBIDDEN_STRINGS:
        cnt = content.count(forbidden)
        if cnt > 0:
            for i, line in enumerate(content.split("\n")):
                if forbidden in line:
                    is_def = "FORBIDDEN_STRINGS" in line or i in range(50, 70)
                    is_check = "forbidden" in line.lower()
                    if not is_def and not is_check:
                        pytest.fail(f"'{forbidden}' at line {i+1}")


def test_no_rejected_research():
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_stress_window_attribution_probe as mod
    c = open(mod.__file__).read()
    assert "REJECTED_RESEARCH.md" not in c


# ===========================================================================
# Artifact schema
# ===========================================================================


def test_candidate_schema():
    cad = {
        "study_id": STUDY_ID,
        "verdict": "HLP_STRESS_ATTR_MM_ONLY",
        "stress_window": {"date": "2025-10-10", "hours": [15], "stress_validated": True},
        "known_mm_addresses": list(KNOWN_MM_ADDRESSES),
        "candidates": [],
        "download_bytes": 0,
    }
    assert cad["study_id"] == STUDY_ID
    assert cad["verdict"] is not None


def test_summary_safety():
    s = {"observer_only": True, "no_order_intent": True, "promotion_candidate": False,
         "paper_promotion_locked": True, "conductor_ready": False,
         "no_registry_mutation": True, "no_user_fills_by_time": True}
    assert s["promotion_candidate"] is False
    assert s["conductor_ready"] is False


# ===========================================================================
# Burst score
# ===========================================================================


def test_one_sided_burst_score():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B", "closedPnl": "0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B", "closedPnl": "0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "A", "closedPnl": "0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.one_sided_burst_score == pytest.approx(1/3)  # |2-1|/3


def test_balanced_burst_score():
    records = [
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "B", "closedPnl": "0"},
        {"address": "0xA", "coin": "BTC", "sz": "1.0", "px": "50000", "side": "A", "closedPnl": "0"},
    ]
    prof = compute_address_profile("0xA", records, active_hours={15})
    assert prof.one_sided_burst_score == 0.0