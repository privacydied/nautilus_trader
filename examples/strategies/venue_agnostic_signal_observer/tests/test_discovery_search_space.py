"""Tests for DiscoveryGridSpec — hashing, validation, and cell-count enumeration."""

from __future__ import annotations

import pytest

from venue_agnostic_signal_observer.discovery.exceptions import GridSpecValidationError
from venue_agnostic_signal_observer.discovery.search_space import GRID_SCHEMA_VERSION
from venue_agnostic_signal_observer.discovery.search_space import DiscoveryGridSpec
from venue_agnostic_signal_observer.discovery.search_space import canonical_grid_json
from venue_agnostic_signal_observer.discovery.search_space import (
    enumerate_cost_sensitivity_cell_count,
)
from venue_agnostic_signal_observer.discovery.search_space import enumerate_primary_cell_count
from venue_agnostic_signal_observer.discovery.search_space import grid_sha256
from venue_agnostic_signal_observer.discovery.search_space import validate_grid_spec


# ===================================================================
# Golden hash constant — DO NOT CHANGE
# ===================================================================
GOLDEN_HASH = "52a34e07c10312b6a49657c49bef541a3b2b55e969773412f6bc9a11f00d8bbb"

# ===================================================================
# Golden canonical JSON string — DO NOT CHANGE
# ===================================================================
GOLDEN_CANONICAL_JSON = (
    '{"clustering_keys":["feature_types","lookbacks_ms","horizons_ms","target_symbols"],'
    '"cooldown_ms":60000,'
    '"cost_models_centibps":[1000,2500,5000],'
    '"entry_delays_ms":[0,5000,15000],'
    '"fdr_family_dimensions":["source_symbols","target_symbols","feature_types","lookbacks_ms",'
    '"thresholds_centibps","horizons_ms","entry_delays_ms","regime_filters"],'
    '"feature_types":["price_impulse","signed_imbalance","notional_burst","large_trade"],'
    '"grid_id":"edge_miner_cross_asset_beta_lag_v1",'
    '"horizons_ms":[10000,30000,60000,180000,300000],'
    '"lookbacks_ms":[1000,5000,10000,30000,60000],'
    '"min_events":30,'
    '"regime_filters":["market_active","market_stress","btc_1m_vol_p95"],'
    '"schema_version":"discovery-grid-v1",'
    '"signal_family":"cross_asset_beta_lag",'
    '"source_symbols":["BTC/USDT","ETH/USDT"],'
    '"source_venues":["binance_perp"],'
    '"target_symbols":["SOL/USD","DOGE/USD","LINK/USD","AVAX/USD"],'
    '"target_venues":["kraken","coinbase"],'
    '"thresholds_centibps":[1000,2000,3000,5000]}'
)

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def golden_spec():
    """Canonical golden example grid spec."""
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
            "source_symbols", "target_symbols", "feature_types", "lookbacks_ms",
            "thresholds_centibps", "horizons_ms", "entry_delays_ms", "regime_filters",
        ),
        created_at_utc="2026-05-17T00:00:00Z",
        notes="Golden example grid for deterministic freeze tests.",
    )


@pytest.fixture
def base_valid_spec():
    """Minimal valid grid spec for mutation-based tests."""
    return DiscoveryGridSpec(
        grid_id="test_grid",
        schema_version=GRID_SCHEMA_VERSION,
        signal_family="test_family",
        source_venues=("venue_a",),
        source_symbols=("SYM1/USD",),
        target_venues=("venue_b",),
        target_symbols=("SYM2/USD",),
        feature_types=("ma_cross",),
        lookbacks_ms=(1000,),
        thresholds_centibps=(100,),
        horizons_ms=(5000,),
        entry_delays_ms=(0,),
        cooldown_ms=1000,
        regime_filters=("all",),
        cost_models_centibps=(500,),
        min_events=1,
        clustering_keys=("feature_types",),
        fdr_family_dimensions=("source_symbols", "feature_types"),
        created_at_utc="",
        notes="",
    )


# ===================================================================
# CLASS 1: Grid hashing (tests 1–12)
# ===================================================================


class TestGridHashing:
    """Deterministic SHA-256 hashing over the canonical JSON payload."""

    # --- 1. Same grid produces same hash ---
    def test_same_grid_same_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        hash2 = grid_sha256(golden_spec)
        assert hash1 == hash2

    # --- 2. Reordered dict keys produce same hash ---
    def test_reordered_dict_keys_same_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        # Construct an identical spec (same field values, just a new instance)
        spec2 = DiscoveryGridSpec(
            grid_id=golden_spec.grid_id,
            schema_version=golden_spec.schema_version,
            signal_family=golden_spec.signal_family,
            source_venues=golden_spec.source_venues,
            source_symbols=golden_spec.source_symbols,
            target_venues=golden_spec.target_venues,
            target_symbols=golden_spec.target_symbols,
            feature_types=golden_spec.feature_types,
            lookbacks_ms=golden_spec.lookbacks_ms,
            thresholds_centibps=golden_spec.thresholds_centibps,
            horizons_ms=golden_spec.horizons_ms,
            entry_delays_ms=golden_spec.entry_delays_ms,
            cooldown_ms=golden_spec.cooldown_ms,
            regime_filters=golden_spec.regime_filters,
            cost_models_centibps=golden_spec.cost_models_centibps,
            min_events=golden_spec.min_events,
            clustering_keys=golden_spec.clustering_keys,
            fdr_family_dimensions=golden_spec.fdr_family_dimensions,
            created_at_utc=golden_spec.created_at_utc,
            notes=golden_spec.notes,
        )
        hash2 = grid_sha256(spec2)
        assert hash1 == hash2

    # --- 3. Changing a feature type changes the hash ---
    def test_changing_feature_type_changes_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc", "notes"]},
               "feature_types": ("price_impulse", "signed_imbalance", "notional_burst", "custom_signal"),
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 != hash2

    # --- 4. Changing a lookback changes the hash ---
    def test_changing_lookback_changes_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "thresholds_centibps", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc", "notes"]},
               "lookbacks_ms": (1000, 5000, 10000, 30000, 120000),
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 != hash2

    # --- 5. Changing a horizon changes the hash ---
    def test_changing_horizon_changes_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "lookbacks_ms", "thresholds_centibps",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc", "notes"]},
               "horizons_ms": (10000, 30000, 60000, 180000, 600000),
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 != hash2

    # --- 6. Changing a threshold changes the hash ---
    def test_changing_threshold_changes_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "lookbacks_ms", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc", "notes"]},
               "thresholds_centibps": (1000, 2000, 3000, 7500),
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 != hash2

    # --- 7. Changing cost model changes the hash ---
    def test_changing_cost_model_changes_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc", "notes"]},
               "cost_models_centibps": (1000, 2500, 5000, 10000),
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 != hash2

    # --- 8. Changing created_at_utc does NOT change the hash ---
    def test_changing_created_at_utc_does_not_change_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "notes"]},
               "created_at_utc": "2099-01-01T00:00:00Z",
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 == hash2

    # --- 9. Changing notes does NOT change the hash ---
    def test_changing_notes_does_not_change_hash(self, golden_spec):
        hash1 = grid_sha256(golden_spec)
        modified = DiscoveryGridSpec(
            **{**{k: getattr(golden_spec, k) for k in
                  ["grid_id", "schema_version", "signal_family", "source_venues",
                   "source_symbols", "target_venues", "target_symbols",
                   "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                   "entry_delays_ms", "cooldown_ms", "regime_filters",
                   "cost_models_centibps", "min_events", "clustering_keys",
                   "fdr_family_dimensions", "created_at_utc"]},
               "notes": "Completely different notes that should not affect the hash.",
            }
        )
        hash2 = grid_sha256(modified)
        assert hash1 == hash2

    # --- 10. Structurally different grid hashes differently ---
    def test_structurally_different_grid_different_hash(self, golden_spec):
        hash_golden = grid_sha256(golden_spec)
        # Different signal_family and different axis lengths
        different = DiscoveryGridSpec(
            grid_id="other_grid_v2",
            schema_version=GRID_SCHEMA_VERSION,
            signal_family="momentum_mean_reversion",
            source_venues=("venue_a", "venue_b"),
            source_symbols=("BTC/USDT",),
            target_venues=("venue_c",),
            target_symbols=("SOL/USD", "DOGE/USD"),
            feature_types=("rsi_divergence",),
            lookbacks_ms=(5000, 10000),
            thresholds_centibps=(500,),
            horizons_ms=(30000,),
            entry_delays_ms=(0, 1000),
            cooldown_ms=30000,
            regime_filters=("all",),
            cost_models_centibps=(1000,),
            min_events=10,
            clustering_keys=("feature_types", "horizons_ms"),
            fdr_family_dimensions=("source_symbols", "target_symbols", "feature_types"),
            created_at_utc="",
            notes="",
        )
        hash_diff = grid_sha256(different)
        assert hash_golden != hash_diff

    # --- 11. Golden example grid hash matches the hardcoded expected hash ---
    def test_golden_grid_hash(self, golden_spec):
        actual_hash = grid_sha256(golden_spec)
        assert actual_hash == GOLDEN_HASH, (
            f"Golden grid hash changed! Expected {GOLDEN_HASH}, got {actual_hash}. "
            "Do NOT change the constant -- fix the implementation or update the golden spec."
        )

    # --- 12. Canonical JSON matches the exact expected string ---
    def test_canonical_json_matches_expected_string(self, golden_spec):
        actual_json = canonical_grid_json(golden_spec)
        assert actual_json == GOLDEN_CANONICAL_JSON, (
            f"Canonical JSON mismatch.\n"
            f"Expected:\n  {GOLDEN_CANONICAL_JSON}\n"
            f"Got:\n  {actual_json}"
        )


# ===================================================================
# CLASS 2: Grid validation (tests 13–33)
# ===================================================================


class TestGridValidation:
    """Validation rules for DiscoveryGridSpec."""

    # --- Helper to copy a spec with one field changed ---
    @staticmethod
    def _mutate(spec: DiscoveryGridSpec, **overrides) -> DiscoveryGridSpec:
        fields = [
            "grid_id", "schema_version", "signal_family",
            "source_venues", "source_symbols", "target_venues", "target_symbols",
            "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
            "entry_delays_ms", "cooldown_ms", "regime_filters",
            "cost_models_centibps", "min_events",
            "clustering_keys", "fdr_family_dimensions",
            "created_at_utc", "notes",
        ]
        d = {k: getattr(spec, k) for k in fields}
        d.update(overrides)
        return DiscoveryGridSpec(**d)

    # --- 13. Empty source_venues rejected ---
    def test_empty_source_venues_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, source_venues=())
        with pytest.raises(GridSpecValidationError, match="source_venues must be non-empty"):
            validate_grid_spec(bad)

    # --- 14. Empty source_symbols rejected ---
    def test_empty_source_symbols_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, source_symbols=())
        with pytest.raises(GridSpecValidationError, match="source_symbols must be non-empty"):
            validate_grid_spec(bad)

    # --- 15. Empty target_venues rejected ---
    def test_empty_target_venues_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, target_venues=())
        with pytest.raises(GridSpecValidationError, match="target_venues must be non-empty"):
            validate_grid_spec(bad)

    # --- 16. Empty target_symbols rejected ---
    def test_empty_target_symbols_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, target_symbols=())
        with pytest.raises(GridSpecValidationError, match="target_symbols must be non-empty"):
            validate_grid_spec(bad)

    # --- 17. Empty feature_types rejected ---
    def test_empty_feature_types_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, feature_types=())
        with pytest.raises(GridSpecValidationError, match="feature_types must be non-empty"):
            validate_grid_spec(bad)

    # --- 18. Negative lookback rejected ---
    def test_negative_lookback_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, lookbacks_ms=(-100, 1000))
        with pytest.raises(GridSpecValidationError, match=r"lookbacks_ms\[0\]: must be positive"):
            validate_grid_spec(bad)

    # --- 19. Zero horizon rejected ---
    def test_zero_horizon_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, horizons_ms=(0, 5000))
        with pytest.raises(GridSpecValidationError, match=r"horizons_ms\[0\]: must be positive"):
            validate_grid_spec(bad)

    # --- 20. Negative entry delay rejected ---
    def test_negative_entry_delay_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, entry_delays_ms=(-1,))
        with pytest.raises(GridSpecValidationError, match=r"entry_delays_ms\[0\]: must be non-negative"):
            validate_grid_spec(bad)

    # --- 21. Zero cooldown rejected ---
    def test_zero_cooldown_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, cooldown_ms=0)
        with pytest.raises(GridSpecValidationError, match="cooldown_ms: must be positive"):
            validate_grid_spec(bad)

    # --- 22. Empty thresholds_centibps rejected ---
    def test_empty_thresholds_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, thresholds_centibps=())
        with pytest.raises(GridSpecValidationError, match="thresholds_centibps must be non-empty"):
            validate_grid_spec(bad)

    # --- 23. Negative threshold rejected ---
    def test_negative_threshold_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, thresholds_centibps=(-500,))
        with pytest.raises(GridSpecValidationError, match=r"thresholds_centibps\[0\]: must be positive"):
            validate_grid_spec(bad)

    # --- 24. Negative cost model rejected ---
    def test_negative_cost_model_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, cost_models_centibps=(-100,))
        with pytest.raises(GridSpecValidationError, match=r"cost_models_centibps\[0\]: must be non-negative"):
            validate_grid_spec(bad)

    # --- 25. Zero min_events rejected ---
    def test_zero_min_events_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, min_events=0)
        with pytest.raises(GridSpecValidationError, match="min_events: must be positive"):
            validate_grid_spec(bad)

    # --- 26. Invalid clustering key rejected ---
    def test_invalid_clustering_key_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, clustering_keys=("not_a_real_axis",))
        with pytest.raises(GridSpecValidationError, match="is not a valid grid axis name"):
            validate_grid_spec(bad)

    # --- 27. Invalid FDR family dimension rejected ---
    def test_invalid_fdr_family_dimension_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, fdr_family_dimensions=("cost_models_centibps",))
        with pytest.raises(GridSpecValidationError, match="is not a valid primary counted axis name"):
            validate_grid_spec(bad)

    # --- 28. schema_version mismatch rejected ---
    def test_schema_version_mismatch_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, schema_version="discovery-grid-v0")
        with pytest.raises(GridSpecValidationError, match="schema_version must be"):
            validate_grid_spec(bad)

    # --- 29. Bool numeric axis element rejected ---
    @pytest.mark.parametrize("axis", [
        "lookbacks_ms",
        "thresholds_centibps",
        "horizons_ms",
        "entry_delays_ms",
        "cost_models_centibps",
    ])
    def test_bool_in_numeric_axis_rejected(self, base_valid_spec, axis):
        original = getattr(base_valid_spec, axis)
        bad = self._mutate(base_valid_spec, **{axis: (True, *original[1:])})
        with pytest.raises(GridSpecValidationError, match="bool is not a valid int value"):
            validate_grid_spec(bad)

    # --- 30. Whitespace-only string axis element rejected ---
    def test_whitespace_string_axis_element_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, source_venues=("  ",))
        with pytest.raises(GridSpecValidationError, match="whitespace-only"):
            validate_grid_spec(bad)

    # --- 31. Duplicate string axis element rejected ---
    def test_duplicate_string_axis_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, target_symbols=("SYM2/USD", "SYM2/USD"))
        with pytest.raises(GridSpecValidationError, match="duplicate value"):
            validate_grid_spec(bad)

    # --- 32. Duplicate int axis element rejected ---
    def test_duplicate_int_axis_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, lookbacks_ms=(1000, 1000))
        with pytest.raises(GridSpecValidationError, match="duplicate value"):
            validate_grid_spec(bad)

    # --- 33. fdr_family_dimensions as strict subset of counted axes is accepted ---
    def test_fdr_family_dimensions_strict_subset_accepted(self, base_valid_spec):
        validate_grid_spec(base_valid_spec)  # should not raise

    # Edge case: empty fdr_family_dimensions rejected
    def test_empty_fdr_family_dimensions_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, fdr_family_dimensions=())
        with pytest.raises(GridSpecValidationError, match="fdr_family_dimensions must be non-empty"):
            validate_grid_spec(bad)

    # Edge case: empty clustering_keys rejected
    def test_empty_clustering_keys_rejected(self, base_valid_spec):
        bad = self._mutate(base_valid_spec, clustering_keys=())
        with pytest.raises(GridSpecValidationError, match="clustering_keys must be non-empty"):
            validate_grid_spec(bad)

    # Edge case: valid golden spec passes validation
    def test_golden_spec_passes_validation(self, golden_spec):
        validate_grid_spec(golden_spec)  # should not raise


# ===================================================================
# CLASS 3: Cell count (tests 34–40)
# ===================================================================


class TestCellCount:
    """Cell-count enumeration determinism and correctness."""

    # --- 34. primary_cell_count is deterministic ---
    def test_primary_cell_count_deterministic(self, golden_spec):
        c1 = enumerate_primary_cell_count(golden_spec)
        c2 = enumerate_primary_cell_count(golden_spec)
        assert c1 == c2

    # --- 35. primary_cell_count = 57600 for golden grid ---
    def test_primary_cell_count_golden(self, golden_spec):
        assert enumerate_primary_cell_count(golden_spec) == 57600

    # --- 36. cost_sensitivity_cell_count = 172800 for golden grid ---
    def test_cost_sensitivity_cell_count_golden(self, golden_spec):
        assert enumerate_cost_sensitivity_cell_count(golden_spec) == 172800

    # --- 37. primary_cell_count changes when counted dimension changes ---
    def test_primary_cell_count_changes_when_counted_dimension_changes(self, golden_spec):
        base_count = enumerate_primary_cell_count(golden_spec)
        # Add one more feature type
        modified = DiscoveryGridSpec(
            **{k: getattr(golden_spec, k) for k in
               ["grid_id", "schema_version", "signal_family", "source_venues",
                "source_symbols", "target_venues", "target_symbols",
                "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "cooldown_ms", "regime_filters",
                "cost_models_centibps", "min_events", "clustering_keys",
                "fdr_family_dimensions", "created_at_utc", "notes"]},
            feature_types=("price_impulse", "signed_imbalance", "notional_burst", "large_trade", "extra"),
        )
        new_count = enumerate_primary_cell_count(modified)
        assert new_count != base_count
        # 57600 * 5/4 = 72000
        assert new_count == 72000

    # --- 38. primary_cell_count does NOT change when cost_models_centibps changes ---
    def test_primary_cell_count_unchanged_when_cost_models_change(self, golden_spec):
        base_count = enumerate_primary_cell_count(golden_spec)
        modified = DiscoveryGridSpec(
            **{k: getattr(golden_spec, k) for k in
               ["grid_id", "schema_version", "signal_family", "source_venues",
                "source_symbols", "target_venues", "target_symbols",
                "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "cooldown_ms", "regime_filters",
                "min_events", "clustering_keys",
                "fdr_family_dimensions", "created_at_utc", "notes"]},
            cost_models_centibps=(1000, 2500, 5000, 10000),
        )
        new_count = enumerate_primary_cell_count(modified)
        assert new_count == base_count

    # --- 39. primary_cell_count does NOT change when cooldown_ms or min_events changes ---
    def test_primary_cell_count_unchanged_when_non_multiplicative_changes(self, golden_spec):
        base_count = enumerate_primary_cell_count(golden_spec)
        modified = DiscoveryGridSpec(
            **{k: getattr(golden_spec, k) for k in
               ["grid_id", "schema_version", "signal_family", "source_venues",
                "source_symbols", "target_venues", "target_symbols",
                "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "regime_filters",
                "cost_models_centibps", "clustering_keys",
                "fdr_family_dimensions", "created_at_utc", "notes"]},
            cooldown_ms=99999,
            min_events=99,
        )
        new_count = enumerate_primary_cell_count(modified)
        assert new_count == base_count

    # --- 40. cost_sensitivity_cell_count changes when cost_models_centibps changes ---
    def test_cost_sensitivity_cell_count_changes_when_cost_models_change(self, golden_spec):
        base_count = enumerate_cost_sensitivity_cell_count(golden_spec)
        modified = DiscoveryGridSpec(
            **{k: getattr(golden_spec, k) for k in
               ["grid_id", "schema_version", "signal_family", "source_venues",
                "source_symbols", "target_venues", "target_symbols",
                "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "cooldown_ms", "regime_filters",
                "min_events", "clustering_keys",
                "fdr_family_dimensions", "created_at_utc", "notes"]},
            cost_models_centibps=(1000, 2500, 5000, 10000),
        )
        new_count = enumerate_cost_sensitivity_cell_count(modified)
        assert new_count != base_count
        # 57600 * 4 = 230400
        assert new_count == 230400
