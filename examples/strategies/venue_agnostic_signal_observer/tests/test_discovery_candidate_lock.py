"""
Tests for capture fingerprint and candidate lock.

Covers tests 54-94 from the discovery freeze specification.
"""

import json

import pytest

from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    DiscoveryCandidateLock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    candidate_sha256,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    canonicalize_selected_cells,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    create_candidate_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    load_candidate_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    save_candidate_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    validate_candidate_lock,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock import (
    validate_selected_cells,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint import (
    build_capture_manifest_ref,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint import (
    sha256_file,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    CandidateHashMismatchError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    CandidateLockValidationError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    CaptureFingerprintError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    GridCellCountMismatchError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    GridHashMismatchError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
    GridLockValidationError,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import GRID_LOCK_TYPE
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import DiscoveryGridLock
from examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock import create_grid_lock
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
    """A single valid selected cell from the golden grid."""
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
    """Create a temporary capture manifest JSON file."""
    manifest = {
        "run_id": "capture_run_001",
        "data_window_start_utc": "2026-01-01T00:00:00Z",
        "data_window_end_utc": "2026-01-07T00:00:00Z",
        "stream_count": 3,
    }
    path = tmp_path / "manifest.json"
    with open(path, "w") as f:
        json.dump(manifest, f)
    return path


# ===================================================================
# CAPTURE FINGERPRINT (54-62)
# ===================================================================

class TestCaptureFingerprint:
    """Tests 54-62: Capture manifest fingerprint."""

    def test_54_sha256_deterministic(self, tmp_path):
        """Capture manifest file SHA-256 is deterministic."""
        path = tmp_path / "manifest.json"
        path.write_text('{"run_id": "test"}')
        h1 = sha256_file(path)
        h2 = sha256_file(path)
        assert h1 == h2

    def test_55_manifest_ref_includes_sha256(self, temp_manifest):
        """CaptureManifestRef includes manifest_sha256."""
        ref = build_capture_manifest_ref(temp_manifest)
        assert ref.manifest_sha256
        assert len(ref.manifest_sha256) == 64

    def test_56_extracts_run_id(self, temp_manifest):
        """CaptureManifestRef extracts run_id when present."""
        ref = build_capture_manifest_ref(temp_manifest)
        assert ref.capture_id == "capture_run_001"

    def test_57_extracts_capture_id_when_no_run_id(self, tmp_path):
        """CaptureManifestRef extracts capture_id when run_id absent."""
        manifest = {"capture_id": "cap456"}
        path = tmp_path / "manifest.json"
        with open(path, "w") as f:
            json.dump(manifest, f)
        ref = build_capture_manifest_ref(path)
        assert ref.capture_id == "cap456"

    def test_58_derives_sha256_capture_id(self, tmp_path):
        """CaptureManifestRef derives sha256-prefixed capture_id when both absent."""
        manifest = {"other": "data"}
        path = tmp_path / "manifest.json"
        with open(path, "w") as f:
            json.dump(manifest, f)
        ref = build_capture_manifest_ref(path)
        assert ref.capture_id.startswith("sha256:")

    def test_59_tolerates_missing_optionals(self, tmp_path):
        """CaptureManifestRef tolerates missing optional fields as None."""
        manifest = {"run_id": "test"}
        path = tmp_path / "manifest.json"
        with open(path, "w") as f:
            json.dump(manifest, f)
        ref = build_capture_manifest_ref(path)
        assert ref.data_window_start_utc is None
        assert ref.data_window_end_utc is None
        assert ref.stream_count is None

    def test_60_missing_manifest_is_hard_error(self):
        """Missing manifest file raises CaptureFingerprintError."""
        with pytest.raises(CaptureFingerprintError):
            build_capture_manifest_ref("/nonexistent/manifest.json")

    def test_61_truncated_json_is_hard_error(self, tmp_path):
        """Truncated/invalid JSON raises CaptureFingerprintError."""
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        with pytest.raises(CaptureFingerprintError):
            build_capture_manifest_ref(path)

    def test_62_byte_changes_change_hash(self, tmp_path):
        """Raw manifest byte changes change manifest_sha256."""
        path = tmp_path / "manifest.json"
        path.write_text('{"run_id": "v1"}')
        h1 = sha256_file(path)
        path.write_text('{"run_id": "v2"}')
        h2 = sha256_file(path)
        assert h1 != h2


# ===================================================================
# CANDIDATE SELECTED CELLS (63-70)
# ===================================================================

class TestSelectedCells:
    """Tests 63-70: Selected cell validation and canonicalization."""

    def test_63_selected_cells_must_be_array(self, golden_spec):
        """selected_cells JSON must be an array (non-empty tuple expected)."""
        with pytest.raises(CandidateLockValidationError, match="must be non-empty"):
            validate_selected_cells((), golden_spec)

    def test_64_cell_has_required_keys(self, golden_spec, valid_cell):
        """Selected cell must contain exactly the primary counted axis keys."""
        # Should not raise
        canonicalize_selected_cells((valid_cell,), golden_spec)

    def test_65_extra_key_rejected(self, golden_spec, valid_cell):
        """Selected cell with extra key is rejected."""
        bad_cell = dict(valid_cell)
        bad_cell["extra_key"] = "value"
        with pytest.raises(CandidateLockValidationError, match="unexpected keys"):
            validate_selected_cells((bad_cell,), golden_spec)

    def test_66_missing_key_rejected(self, golden_spec, valid_cell):
        """Selected cell with missing key is rejected."""
        bad_cell = dict(valid_cell)
        del bad_cell["target_symbols"]
        with pytest.raises(CandidateLockValidationError, match="missing keys"):
            validate_selected_cells((bad_cell,), golden_spec)

    def test_67_value_not_in_axis_rejected(self, golden_spec, valid_cell):
        """Selected cell value outside parent grid axis is rejected."""
        bad_cell = dict(valid_cell)
        bad_cell["source_symbols"] = "NONEXISTENT"
        with pytest.raises(CandidateLockValidationError, match="not in the parent grid axis"):
            validate_selected_cells((bad_cell,), golden_spec)

    def test_68_duplicate_cells_rejected(self, golden_spec, valid_cell):
        """Duplicate selected cells are rejected."""
        with pytest.raises(CandidateLockValidationError, match="duplicate cell"):
            validate_selected_cells((valid_cell, valid_cell), golden_spec)

    def test_69_ordering_does_not_affect_hash(self, golden_spec, golden_lock, temp_manifest):
        """Selected cell ordering does not affect candidate hash."""
        ref = build_capture_manifest_ref(temp_manifest)
        cell_a = {
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
        cell_b = {
            "source_venues": "binance_perp",
            "source_symbols": "ETH/USDT",
            "target_venues": "coinbase",
            "target_symbols": "LINK/USD",
            "feature_types": "signed_imbalance",
            "lookbacks_ms": 5000,
            "thresholds_centibps": 2000,
            "horizons_ms": 60000,
            "entry_delays_ms": 0,
            "regime_filters": "market_active",
        }
        lock_ab = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(cell_a, cell_b),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        lock_ba = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(cell_b, cell_a),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        assert lock_ab.candidate_hash == lock_ba.candidate_hash

    def test_70_canonical_sort_deterministic(self, golden_spec, valid_cell):
        """Selected cell canonical sort is deterministic."""
        cell_b = {
            "source_venues": "binance_perp",
            "source_symbols": "ETH/USDT",
            "target_venues": "coinbase",
            "target_symbols": "LINK/USD",
            "feature_types": "signed_imbalance",
            "lookbacks_ms": 5000,
            "thresholds_centibps": 2000,
            "horizons_ms": 60000,
            "entry_delays_ms": 0,
            "regime_filters": "market_active",
        }
        sorted_1 = canonicalize_selected_cells((valid_cell, cell_b), golden_spec)
        sorted_2 = canonicalize_selected_cells((valid_cell, cell_b), golden_spec)
        assert sorted_1 == sorted_2

    def test_70b_canonical_sort_order(self, golden_spec, valid_cell):
        """Canonical sort produces same ordering regardless of input order."""
        cell_b = {
            "source_venues": "binance_perp",
            "source_symbols": "ETH/USDT",
            "target_venues": "coinbase",
            "target_symbols": "LINK/USD",
            "feature_types": "signed_imbalance",
            "lookbacks_ms": 5000,
            "thresholds_centibps": 2000,
            "horizons_ms": 60000,
            "entry_delays_ms": 0,
            "regime_filters": "market_active",
        }
        sorted_ab = canonicalize_selected_cells((valid_cell, cell_b), golden_spec)
        sorted_ba = canonicalize_selected_cells((cell_b, valid_cell), golden_spec)
        assert sorted_ab == sorted_ba


# ===================================================================
# CANDIDATE LOCK (71-94)
# ===================================================================

class TestCandidateLock:
    """Tests 71-94: Candidate lock creation and validation."""

    def test_71_requires_parent_grid_id(self, golden_lock):
        """Candidate lock requires parent_grid_id."""
        fields = DiscoveryCandidateLock.__dataclass_fields__
        assert "parent_grid_id" in fields

    def test_72_requires_parent_grid_hash(self, golden_lock):
        """Candidate lock requires parent_grid_hash."""
        fields = DiscoveryCandidateLock.__dataclass_fields__
        assert "parent_grid_hash" in fields

    def test_73_requires_parent_grid_schema_version(self, golden_lock):
        """Candidate lock requires parent_grid_schema_version."""
        fields = DiscoveryCandidateLock.__dataclass_fields__
        assert "parent_grid_schema_version" in fields

    def test_74_requires_parent_primary_cell_count(self, golden_lock):
        """Candidate lock requires parent_grid_primary_cell_count."""
        fields = DiscoveryCandidateLock.__dataclass_fields__
        assert "parent_grid_primary_cell_count" in fields

    def test_75_requires_parent_cost_sensitivity(self, golden_lock):
        """Candidate lock requires parent_grid_cost_sensitivity_cell_count."""
        fields = DiscoveryCandidateLock.__dataclass_fields__
        assert "parent_grid_cost_sensitivity_cell_count" in fields

    def test_76_requires_at_least_one_capture(self, golden_spec, golden_lock, valid_cell):
        """Candidate lock requires at least one discovery capture manifest ref."""
        with pytest.raises(CandidateLockValidationError, match="At least one"):
            create_candidate_lock(
                grid_spec=golden_spec,
                grid_lock=golden_lock,
                candidate_id="test",
                selected_cells=(valid_cell,),
                cluster_summary={},
                selection_reason="test",
                discovery_capture_refs=(),
            )

    def test_77_rejects_empty_selected_cells(self, golden_spec, golden_lock, temp_manifest):
        """Candidate lock rejects empty selected_cells."""
        ref = build_capture_manifest_ref(temp_manifest)
        with pytest.raises(CandidateLockValidationError, match="must be non-empty"):
            create_candidate_lock(
                grid_spec=golden_spec,
                grid_lock=golden_lock,
                candidate_id="test",
                selected_cells=(),
                cluster_summary={},
                selection_reason="test",
                discovery_capture_refs=(ref,),
            )

    def test_78_hash_changes_when_cells_change(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash changes when selected_cells change."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        other_cell = dict(valid_cell)
        other_cell["feature_types"] = "signed_imbalance"
        lock_b = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(other_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        assert lock_a.candidate_hash != lock_b.candidate_hash

    def test_79_hash_changes_when_grid_hash_changes(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash changes when parent_grid_hash changes."""
        ref = build_capture_manifest_ref(temp_manifest)
        different_spec = DiscoveryGridSpec(
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
        different_lock = create_grid_lock(different_spec)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        lock_b = create_candidate_lock(
            grid_spec=different_spec,
            grid_lock=different_lock,
            candidate_id="test",
            selected_cells=({
                "source_venues": "binance_perp",
                "source_symbols": "BTC/USDT",
                "target_venues": "kraken",
                "target_symbols": "SOL/USD",
                "feature_types": "price_impulse",
                "lookbacks_ms": 1000,
                "thresholds_centibps": 1000,
                "horizons_ms": 10000,
                "entry_delays_ms": 0,
                "regime_filters": "market_active",
            },),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        assert lock_a.candidate_hash != lock_b.candidate_hash

    def test_80_hash_changes_when_capture_hash_changes(self, golden_spec, golden_lock, tmp_path, valid_cell):
        """Candidate hash changes when capture manifest hash changes."""
        path_a = tmp_path / "manifest_a.json"
        with open(path_a, "w") as f:
            json.dump({"run_id": "capture_a"}, f)
        path_b = tmp_path / "manifest_b.json"
        with open(path_b, "w") as f:
            json.dump({"run_id": "capture_b"}, f)

        ref_a = build_capture_manifest_ref(path_a)
        ref_b = build_capture_manifest_ref(path_b)

        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref_a,),
        )
        lock_b = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref_b,),
        )
        assert lock_a.candidate_hash != lock_b.candidate_hash

    def test_81_hash_unchanged_by_frozen_at_utc(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash does not change when frozen_at_utc changes."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        lock_b = DiscoveryCandidateLock(
            lock_type=lock_a.lock_type,
            candidate_id=lock_a.candidate_id,
            candidate_hash=lock_a.candidate_hash,
            parent_grid_id=lock_a.parent_grid_id,
            parent_grid_hash=lock_a.parent_grid_hash,
            parent_grid_schema_version=lock_a.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock_a.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock_a.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock_a.schema_version,
            selected_cells=lock_a.selected_cells,
            cluster_summary=lock_a.cluster_summary,
            selection_reason=lock_a.selection_reason,
            discovery_captures=lock_a.discovery_captures,
            frozen_at_utc="2099-01-01T00:00:00Z",
        )
        # Recompute hash should be the same
        assert candidate_sha256(lock_b) == lock_a.candidate_hash

    def test_82_hash_unchanged_by_cluster_summary(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash does not change when cluster_summary changes."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        lock_b = DiscoveryCandidateLock(
            lock_type=lock_a.lock_type,
            candidate_id=lock_a.candidate_id,
            candidate_hash=lock_a.candidate_hash,
            parent_grid_id=lock_a.parent_grid_id,
            parent_grid_hash=lock_a.parent_grid_hash,
            parent_grid_schema_version=lock_a.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock_a.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock_a.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock_a.schema_version,
            selected_cells=lock_a.selected_cells,
            cluster_summary={"new": "data"},
            selection_reason=lock_a.selection_reason,
            discovery_captures=lock_a.discovery_captures,
            frozen_at_utc=lock_a.frozen_at_utc,
        )
        assert candidate_sha256(lock_b) == lock_a.candidate_hash

    def test_83_hash_unchanged_by_selection_reason(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash does not change when selection_reason changes."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="reason_a",
            discovery_capture_refs=(ref,),
        )
        lock_b = DiscoveryCandidateLock(
            lock_type=lock_a.lock_type,
            candidate_id=lock_a.candidate_id,
            candidate_hash=lock_a.candidate_hash,
            parent_grid_id=lock_a.parent_grid_id,
            parent_grid_hash=lock_a.parent_grid_hash,
            parent_grid_schema_version=lock_a.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock_a.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock_a.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock_a.schema_version,
            selected_cells=lock_a.selected_cells,
            cluster_summary=lock_a.cluster_summary,
            selection_reason="different reason",
            discovery_captures=lock_a.discovery_captures,
            frozen_at_utc=lock_a.frozen_at_utc,
        )
        assert candidate_sha256(lock_b) == lock_a.candidate_hash

    def test_84_hash_unchanged_by_candidate_id(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate hash does not change when candidate_id changes."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="id_a",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        lock_b = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="id_b",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        assert lock_a.candidate_hash == lock_b.candidate_hash

    def test_85_hash_unchanged_by_manifest_path(self, golden_spec, golden_lock, tmp_path, valid_cell):
        """Candidate hash does not change when manifest_path changes but sha256 doesn't."""
        manifest_content = {"run_id": "capture_test"}
        path_a = tmp_path / "manifest_a.json"
        with open(path_a, "w") as f:
            json.dump(manifest_content, f)
        # Copy same content to different path
        path_b = tmp_path / "manifest_b.json"
        with open(path_b, "w") as f:
            json.dump(manifest_content, f)

        ref_a = build_capture_manifest_ref(path_a)
        ref_b = build_capture_manifest_ref(path_b)
        assert ref_a.manifest_path != ref_b.manifest_path
        assert ref_a.manifest_sha256 == ref_b.manifest_sha256

        lock_a = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref_a,),
        )
        lock_b = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref_b,),
        )
        assert lock_a.candidate_hash == lock_b.candidate_hash

    def test_86_non_json_serializable_cluster_rejected(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Non-JSON-serializable cluster_summary is rejected at construction."""
        ref = build_capture_manifest_ref(temp_manifest)

        class NonSerializable:
            pass

        bad_summary = {"obj": NonSerializable()}
        with pytest.raises(CandidateLockValidationError, match="not JSON-serializable"):
            create_candidate_lock(
                grid_spec=golden_spec,
                grid_lock=golden_lock,
                candidate_id="test",
                selected_cells=(valid_cell,),
                cluster_summary=bad_summary,
                selection_reason="test",
                discovery_capture_refs=(ref,),
            )

    def test_87_rejects_wrong_lock_type(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects wrong lock_type."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        bad_lock = DiscoveryCandidateLock(
            lock_type="WRONG_TYPE",
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash=lock.parent_grid_hash,
            parent_grid_schema_version=lock.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock.schema_version,
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises(CandidateLockValidationError, match="lock_type"):
            validate_candidate_lock(golden_spec, golden_lock, bad_lock)

    def test_88_rejects_grid_schema_incompatibility(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects parent grid schema incompatibility."""
        ref = build_capture_manifest_ref(temp_manifest)
        bad_grid_lock = DiscoveryGridLock(
            lock_type=GRID_LOCK_TYPE,
            grid_id=golden_lock.grid_id,
            grid_hash=golden_lock.grid_hash,
            schema_version="discovery-grid-v999",
            primary_cell_count=golden_lock.primary_cell_count,
            cost_sensitivity_cell_count=golden_lock.cost_sensitivity_cell_count,
            git_sha=golden_lock.git_sha,
            locked_at_utc=golden_lock.locked_at_utc,
        )
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        # Create lock referencing the bad grid
        bad_lock = DiscoveryCandidateLock(
            lock_type=lock.lock_type,
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash=lock.parent_grid_hash,
            parent_grid_schema_version="discovery-grid-v999",
            parent_grid_primary_cell_count=lock.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock.schema_version,
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises((GridLockValidationError, CandidateLockValidationError), match="schema_version mismatch"):
            validate_candidate_lock(golden_spec, bad_grid_lock, bad_lock)

    def test_89_own_schema_version_must_be_candidate_v1(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock's own schema_version must be discovery-candidate-v1."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        bad_lock = DiscoveryCandidateLock(
            lock_type=lock.lock_type,
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash=lock.parent_grid_hash,
            parent_grid_schema_version=lock.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock.parent_grid_cost_sensitivity_cell_count,
            schema_version="discovery-candidate-v999",
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises(CandidateLockValidationError, match="schema_version"):
            validate_candidate_lock(golden_spec, golden_lock, bad_lock)

    def test_90_rejects_primary_count_mismatch_vs_lock(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects parent_grid_primary_cell_count mismatch vs grid lock."""
        ref = build_capture_manifest_ref(temp_manifest)
        DiscoveryGridLock(
            lock_type=GRID_LOCK_TYPE,
            grid_id=golden_lock.grid_id,
            grid_hash=golden_lock.grid_hash,
            schema_version=golden_lock.schema_version,
            primary_cell_count=99999,
            cost_sensitivity_cell_count=golden_lock.cost_sensitivity_cell_count,
            git_sha=golden_lock.git_sha,
            locked_at_utc=golden_lock.locked_at_utc,
        )
        # Create candidate referencing the bad lock's values
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        bad_candidate = DiscoveryCandidateLock(
            lock_type=lock.lock_type,
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash=lock.parent_grid_hash,
            parent_grid_schema_version=lock.parent_grid_schema_version,
            parent_grid_primary_cell_count=99999,
            parent_grid_cost_sensitivity_cell_count=lock.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock.schema_version,
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises(GridCellCountMismatchError, match="primary_cell_count"):
            validate_candidate_lock(golden_spec, golden_lock, bad_candidate)

    def test_91_rejects_cost_sensitivity_mismatch_vs_lock(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects parent_grid_cost_sensitivity_cell_count mismatch vs grid lock."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        bad_candidate = DiscoveryCandidateLock(
            lock_type=lock.lock_type,
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash=lock.parent_grid_hash,
            parent_grid_schema_version=lock.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=99999,
            schema_version=lock.schema_version,
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises(GridCellCountMismatchError, match="cost_sensitivity"):
            validate_candidate_lock(golden_spec, golden_lock, bad_candidate)

    def test_92_rejects_grid_hash_mismatch_vs_lock(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects parent_grid_hash mismatch vs grid lock."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        bad_candidate = DiscoveryCandidateLock(
            lock_type=lock.lock_type,
            candidate_id=lock.candidate_id,
            candidate_hash=lock.candidate_hash,
            parent_grid_id=lock.parent_grid_id,
            parent_grid_hash="0" * 64,
            parent_grid_schema_version=lock.parent_grid_schema_version,
            parent_grid_primary_cell_count=lock.parent_grid_primary_cell_count,
            parent_grid_cost_sensitivity_cell_count=lock.parent_grid_cost_sensitivity_cell_count,
            schema_version=lock.schema_version,
            selected_cells=lock.selected_cells,
            cluster_summary=lock.cluster_summary,
            selection_reason=lock.selection_reason,
            discovery_captures=lock.discovery_captures,
            frozen_at_utc=lock.frozen_at_utc,
        )
        with pytest.raises(CandidateLockValidationError, match="parent_grid_hash"):
            validate_candidate_lock(golden_spec, golden_lock, bad_candidate)

    def test_93_rejects_primary_count_mismatch_vs_spec(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock rejects parent_grid_primary_cell_count mismatch vs recomputed spec."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        # Use a DIFFERENT spec with different primary count
        diff_spec = DiscoveryGridSpec(
            grid_id=golden_spec.grid_id,
            schema_version=golden_spec.schema_version,
            signal_family=golden_spec.signal_family,
            source_venues=golden_spec.source_venues,
            source_symbols=("BTC/USDT",),  # Fewer symbols -> different count
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
        with pytest.raises((GridHashMismatchError, CandidateHashMismatchError), match="parent_grid_hash|grid_hash mismatch"):
            validate_candidate_lock(diff_spec, golden_lock, lock)

    def test_94_validation_requires_grid_spec(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Candidate lock validation requires grid spec, not only grid lock."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        # Validating with wrong spec should fail
        wrong_spec = DiscoveryGridSpec(
            grid_id="wrong",
            schema_version=GRID_SCHEMA_VERSION,
            signal_family="wrong",
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
        with pytest.raises((CandidateLockValidationError, GridLockValidationError, GridHashMismatchError)):
            validate_candidate_lock(wrong_spec, golden_lock, lock)

    def test_94b_validation_passes_happy_path(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """Happy path: validate_candidate_lock passes for matching inputs."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        # Should not raise
        validate_candidate_lock(golden_spec, golden_lock, lock)

    def test_94c_candidate_sha256_is_deterministic(self, golden_spec, golden_lock, temp_manifest, valid_cell):
        """candidate_sha256 produces same result for same lock."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        h1 = candidate_sha256(lock)
        h2 = candidate_sha256(lock)
        assert h1 == h2

    def test_94d_candidate_lock_round_trip(self, golden_spec, golden_lock, temp_manifest, valid_cell, tmp_path):
        """Candidate lock survives JSON round-trip."""
        ref = build_capture_manifest_ref(temp_manifest)
        lock = create_candidate_lock(
            grid_spec=golden_spec,
            grid_lock=golden_lock,
            candidate_id="test",
            selected_cells=(valid_cell,),
            cluster_summary={},
            selection_reason="test",
            discovery_capture_refs=(ref,),
        )
        path = tmp_path / "candidate.json"
        save_candidate_lock(lock, path)
        loaded = load_candidate_lock(path)
        assert loaded.candidate_hash == lock.candidate_hash
        assert loaded.parent_grid_id == lock.parent_grid_id
        assert len(loaded.discovery_captures) == 1
