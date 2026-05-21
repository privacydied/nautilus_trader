"""
Tests for the promotion boundary choke point.

Covers tests 120-124 from the discovery freeze specification.
"""

import json

import pytest

from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    DiscoveryCandidateLock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    create_candidate_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint import (
    build_capture_manifest_ref,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    CandidateLockValidationError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    DiscoveryFreezeError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import create_grid_lock
from examples.strategies.venue_agnostic_signal_observer.discovery.promotion_boundary import (
    require_frozen_candidate_for_validation,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.search_space import (
    GRID_SCHEMA_VERSION,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.search_space import (
    DiscoveryGridSpec,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def golden_spec():
    return DiscoveryGridSpec(
        grid_id="edge_miner_cross_asset_beta_lag_v1",
        schema_version=GRID_SCHEMA_VERSION,
        signal_family="cross_asset_beta_lag",
        source_venues=("binance_perp",),
        source_symbols=("BTC/USDT", "ETH/USDT"),
        target_venues=("kraken", "coinbase"),
        target_symbols=("SOL/USD", "DOGE/USD", "LINK/USD", "AVAX/USD"),
        feature_types=("price_impulse", "signed_imbalance", "notional_burst", "large_trade"),
        lookbacks_ms=(1000, 5000, 10000, 30000, 60000),
        thresholds_centibps=(1000, 2000, 3000, 5000),
        horizons_ms=(10000, 30000, 60000, 180000, 300000),
        entry_delays_ms=(0, 5000, 15000),
        cooldown_ms=60000,
        regime_filters=("market_active", "market_stress", "btc_1m_vol_p95"),
        cost_models_centibps=(1000, 2500, 5000),
        min_events=30,
        clustering_keys=("feature_types", "lookbacks_ms", "horizons_ms", "target_symbols"),
        fdr_family_dimensions=(
            "source_symbols", "target_symbols", "feature_types",
            "lookbacks_ms", "thresholds_centibps", "horizons_ms",
            "entry_delays_ms", "regime_filters",
        ),
        created_at_utc="2026-05-17T00:00:00Z",
        notes="Golden example grid for deterministic freeze tests.",
    )


@pytest.fixture
def golden_lock(golden_spec):
    return create_grid_lock(golden_spec)


@pytest.fixture
def valid_cell():
    return {
        "source_venues": "binance_perp",
        "source_symbols": "BTC/USDT",
        "target_venues": "kraken",
        "target_symbols": "SOL/USD",
        "feature_types": "price_impulse",
        "lookbacks_ms": 30000,
        "thresholds_centibps": 3000,
        "horizons_ms": 300000,
        "entry_delays_ms": 5000,
        "regime_filters": "market_stress",
    }


@pytest.fixture
def temp_manifest(tmp_path):
    """Create a temporary capture manifest for testing."""
    manifest = {"run_id": "capture_test"}
    path = tmp_path / "manifest.json"
    with open(path, "w") as f:
        json.dump(manifest, f)
    return path


@pytest.fixture
def valid_candidate_lock(golden_spec, golden_lock, valid_cell, temp_manifest):
    """Create a valid candidate lock for testing."""
    ref = build_capture_manifest_ref(temp_manifest)
    return create_candidate_lock(
        grid_spec=golden_spec,
        grid_lock=golden_lock,
        candidate_id="test_candidate",
        selected_cells=(valid_cell,),
        cluster_summary={},
        selection_reason="Test cluster",
        discovery_capture_refs=(ref,),
    )


# ===================================================================
# PROMOTION BOUNDARY (120-124)
# ===================================================================

class TestPromotionBoundary:
    """Tests 120-124: Promotion boundary choke point."""

    def test_120_validates_matching_and_returns(self, golden_spec, golden_lock, valid_candidate_lock):
        """require_frozen_candidate_for_validation validates matching grid/grid-lock/candidate-lock and returns the candidate lock."""
        result = require_frozen_candidate_for_validation(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_lock=valid_candidate_lock,
        )
        assert result is valid_candidate_lock
        assert isinstance(result, DiscoveryCandidateLock)
        assert result.candidate_hash == valid_candidate_lock.candidate_hash

    def test_121_rejects_ad_hoc_non_candidate_lock(self, golden_spec, golden_lock):
        """require_frozen_candidate_for_validation rejects ad-hoc / non-DiscoveryCandidateLock data."""
        # A plain dict is not a DiscoveryCandidateLock
        with pytest.raises((CandidateLockValidationError, AttributeError, TypeError)):
            require_frozen_candidate_for_validation(
                grid_spec=golden_spec,
                grid_lock=golden_lock,
                candidate_lock={"not": "a candidate lock"},  # type: ignore
            )

    def test_122_rejects_mismatched_grid_lock(self, golden_spec, golden_lock, valid_cell, temp_manifest):
        """require_frozen_candidate_for_validation rejects a mismatched grid lock."""
        ref = build_capture_manifest_ref(temp_manifest)

        # Create a DIFFERENT grid and lock
        other_spec = DiscoveryGridSpec(
            grid_id="different_grid",
            schema_version=GRID_SCHEMA_VERSION,
            signal_family="different_family",
            source_venues=("binance_perp",),
            source_symbols=("BTC/USDT",),
            target_venues=("kraken",),
            target_symbols=("SOL/USD",),
            feature_types=("price_impulse",),
            lookbacks_ms=(1000,),
            thresholds_centibps=(1000,),
            horizons_ms=(10000,),
            entry_delays_ms=(0,),
            cooldown_ms=5000,
            regime_filters=("market_active",),
            cost_models_centibps=(1000,),
            min_events=10,
            clustering_keys=("feature_types",),
            fdr_family_dimensions=("feature_types",),
            created_at_utc="",
            notes="",
        )
        other_lock = create_grid_lock(other_spec)

        # Candidate lock referencing the original grid
        candidate = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )

        # Validation should fail because grid_spec vs other_lock mismatch
        with pytest.raises(DiscoveryFreezeError):
            require_frozen_candidate_for_validation(
                grid_spec=other_spec,
                grid_lock=other_lock,
                candidate_lock=candidate,
            )

    def test_123_rejects_mismatched_candidate_lock(self, golden_spec, golden_lock, valid_cell, temp_manifest):
        """require_frozen_candidate_for_validation rejects a mismatched candidate lock."""
        ref = build_capture_manifest_ref(temp_manifest)

        # Create a valid candidate
        candidate = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )

        # Tamper with the candidate's parent_grid_hash
        tampered_candidate = DiscoveryCandidateLock(
            lock_type=candidate.lock_type,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            parent_grid_id=candidate.parent_grid_id,
            parent_grid_hash="0" * 64,  # Tampered hash
            parent_grid_schema_version=candidate.parent_grid_schema_version,
            parent_grid_primary_cell_count=candidate.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=candidate.parent_grid_cost_sensitivity_cell_count,
            schema_version=candidate.schema_version,
            selected_cells=candidate.selected_cells,
            cluster_summary=candidate.cluster_summary,
            selection_reason=candidate.selection_reason,
            discovery_captures=candidate.discovery_captures,
            frozen_at_utc=candidate.frozen_at_utc,
        )

        with pytest.raises(DiscoveryFreezeError):
            require_frozen_candidate_for_validation(
                grid_spec=golden_spec,
                grid_lock=golden_lock,
                candidate_lock=tampered_candidate,
            )

    def test_124_signature_locked(self):
        """The function signature (parameter names, order, return type) is locked by an explicit test."""
        import inspect
        sig = inspect.signature(require_frozen_candidate_for_validation)
        param_names = list(sig.parameters.keys())

        assert param_names == ["grid_spec", "grid_lock", "candidate_lock"], (
            f"Parameter order changed: {param_names}"
        )

        # Annotations may be strings due to `from __future__ import annotations`
        def _resolve(name: str) -> str:
            """Get the annotation string regardless of whether it's a forward ref or type."""
            return name

        # Check parameter annotations by string value
        p_grid_spec = sig.parameters["grid_spec"].annotation
        p_grid_lock = sig.parameters["grid_lock"].annotation
        p_candidate_lock = sig.parameters["candidate_lock"].annotation
        ret = sig.return_annotation

        # All annotations should resolve to the expected names
        grid_spec_str = getattr(p_grid_spec, "__name__", str(p_grid_spec)).replace(
            "<class '", ""
        ).replace("'>", "").split(".")[-1] if not isinstance(p_grid_spec, str) else p_grid_spec
        assert "DiscoveryGridSpec" in str(grid_spec_str), (
            f"grid_spec annotation should mention DiscoveryGridSpec, got {p_grid_spec}"
        )
        assert "DiscoveryGridLock" in str(p_grid_lock), (
            f"grid_lock annotation should mention DiscoveryGridLock, got {p_grid_lock}"
        )
        assert "DiscoveryCandidateLock" in str(p_candidate_lock), (
            f"candidate_lock annotation should mention DiscoveryCandidateLock, got {p_candidate_lock}"
        )
        assert "DiscoveryCandidateLock" in str(ret), (
            f"return annotation should mention DiscoveryCandidateLock, got {ret}"
        )
