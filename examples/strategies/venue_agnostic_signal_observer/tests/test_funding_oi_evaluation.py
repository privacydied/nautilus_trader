"""
Phase 1 evaluation tests for Family 3.
Tests verify evaluation gates, null test, FDR, holdout, and safety constraints.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_funding_oi_evaluation import (
    PRECOMMITMENT_SHA256,
    PRECOMMITMENT_GIT_SHA,
    SEED,
    HORIZONS,
    FUNDING_DIRECTIONS,
    OI_REGIMES,
    PRIMARY_COST_BPS,
    DIAGNOSTIC_COST_BPS,
    MIN_EVENTS,
    HOLDOUT_MIN_EVENTS,
    N_SHUFFLES,
    FDR_ALPHA,
    NULL_ALPHA,
    evaluate_cell,
    benjamini_yekutieli,
)


def test_precommitment_sha_matches():
    """Precommitment SHA-256 must match the frozen value."""
    expected = "5f911c3f910f60abd3572474a0581c70042ef998150da0bbd5f0a0f688ae4673"
    assert PRECOMMITMENT_SHA256 == expected


def test_seed_fixed():
    """Seed must be fixed (42)."""
    assert SEED == 42


def test_primary_cells_exactly_6():
    """2 directions × 1 OI regime × 3 horizons = 6 primary cells."""
    count = 0
    for d in FUNDING_DIRECTIONS:
        for h in HORIZONS:
            count += 1
    assert count == 6


def test_horizons():
    """Horizons must be 8, 24, 48."""
    assert HORIZONS == [8, 24, 48]


def test_primary_cost():
    """Primary cost must be 100 bps (50 entry + 50 exit)."""
    assert PRIMARY_COST_BPS == 100


def test_diagnostic_cost():
    """Diagnostic cost must be 12 bps (6 entry + 6 exit)."""
    assert DIAGNOSTIC_COST_BPS == 12


def test_min_events():
    """Minimum events per cell must be 50."""
    assert MIN_EVENTS == 50


def test_holdout_min_events():
    """Holdout minimum events must be 50."""
    assert HOLDOUT_MIN_EVENTS == 50


def test_null_shuffles():
    """Null test must use 1000 shuffles."""
    assert N_SHUFFLES == 1000


def test_fdr_alpha():
    """FDR alpha must be 0.05."""
    assert FDR_ALPHA == 0.05


def test_null_alpha():
    """Null alpha must be 0.05."""
    assert NULL_ALPHA == 0.05


def test_by_fdr_rejects_all_when_all_p_large():
    """Benjamini-Yekutieli with all large p-values rejects none."""
    p_vals = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    rejected = benjamini_yekutieli(p_vals, 0.05)
    assert sum(rejected) == 0


def test_by_fdr_rejects_some_when_some_p_small():
    """BY should reject at least the smallest p-value when it's very small."""
    p_vals = [0.0001, 0.5, 0.6, 0.7, 0.8, 0.9]
    rejected = benjamini_yekutieli(p_vals, 0.05)
    assert sum(rejected) >= 1


def test_by_fdr_empty():
    """Empty p-value list returns empty rejection list."""
    assert benjamini_yekutieli([], 0.05) == []


def test_underpowered_cell_verdict():
    """Cell with < 50 events must be NEEDS_MORE_DATA."""
    events = []
    for i in range(30):
        events.append({
            "is_positive": True,
            "is_negative": False,
            "oi_regime": "rising_oi",
            f"fwd_available_8h": True,
            f"net_return_8h": 0.0,
            f"diagnostic_return_8h": 0.0,
        })
    result = evaluate_cell(events, "positive", "rising_oi", 8, PRIMARY_COST_BPS)
    assert result["verdict"] == "NEEDS_MORE_DATA"


def test_cell_with_no_fwd_price():
    """Events without forward price should be excluded."""
    events = [
        {"is_positive": True, "is_negative": False, "oi_regime": "rising_oi",
         "fwd_available_8h": False, "net_return_8h": None},
        {"is_positive": True, "is_negative": False, "oi_regime": "rising_oi",
         "fwd_available_8h": True, "net_return_8h": -50.0},
    ]
    result = evaluate_cell(events, "positive", "rising_oi", 8, PRIMARY_COST_BPS)
    assert result["n_events"] == 1


def test_no_private_auth_imports():
    """The evaluation script must not import private/auth/order/execution modules."""
    path = os.path.join(os.path.dirname(__file__), "..", "run_funding_oi_evaluation.py")
    with open(path) as f:
        content = f.read()
    forbidden = [
        "import nautilus", "from nautilus", "import ccxt", "from ccxt",
        "api_key", "api_secret", "private_key", "execution_client",
        "bot_path", "TradeOrder", "OrderSubmit",
    ]
    for pattern in forbidden:
        assert pattern not in content, f"Forbidden: {pattern}"


def test_run_output_exists():
    """Check that the run output directory and summary.json exist."""
    base = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_oi_crowding_regime_v0"
    )
    # Find most recent run directory
    if not os.path.exists(base):
        pytest.skip("No output directory found")
    dirs = sorted([d for d in os.listdir(base) if d.startswith("funding_oi_crowding_regime_v0_")])
    if not dirs:
        pytest.skip("No run directories found")
    latest = os.path.join(base, dirs[-1])
    assert os.path.exists(os.path.join(latest, "summary.json"))
    assert os.path.exists(os.path.join(latest, "cells.json"))
    assert os.path.exists(os.path.join(latest, "null_results.json"))
    assert os.path.exists(os.path.join(latest, "fdr_table.json"))
    assert os.path.exists(os.path.join(latest, "holdout_results.json"))
    assert os.path.exists(os.path.join(latest, "_metadata.json"))
    assert os.path.exists(os.path.join(latest, "summary.md"))


def test_run_no_candidates():
    """Study verdict should not contain CANDIDATE (since all gates failed)."""
    base = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..",
        "reports", "funding_oi_crowding_regime_v0"
    )
    if not os.path.exists(base):
        pytest.skip("No output directory found")
    dirs = sorted([d for d in os.listdir(base) if d.startswith("funding_oi_crowding_regime_v0_")])
    if not dirs:
        pytest.skip("No run directories found")
    latest = os.path.join(base, dirs[-1])
    summary_path = os.path.join(latest, "summary.json")
    if not os.path.exists(summary_path):
        pytest.skip("summary.json not found")
    with open(summary_path) as f:
        data = json.load(f)
    # Check no cell has CANDIDATE_FOR_LONGER_OBSERVATION
    for cell in data.get("primary_cells", []):
        assert cell.get("final_verdict") != "CANDIDATE_FOR_LONGER_OBSERVATION", \
            f"Unexpected candidate: {cell['cell_key']}"
