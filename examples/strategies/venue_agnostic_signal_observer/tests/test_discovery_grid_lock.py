"""Tests for DiscoveryGridLock — creation, validation, and semantic comparison."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ..discovery.search_space import (
    DiscoveryGridSpec,
    validate_grid_spec,
    grid_sha256,
    enumerate_primary_cell_count,
    enumerate_cost_sensitivity_cell_count,
    GRID_SCHEMA_VERSION,
    save_grid_spec,
)
from ..discovery.grid_lock import (
    create_grid_lock,
    validate_grid_lock,
    load_grid_lock,
    save_grid_lock,
    locks_are_semantically_equal,
    DiscoveryGridLock,
    GRID_LOCK_TYPE,
)
from ..discovery.exceptions import (
    GridLockValidationError,
    GridHashMismatchError,
    GridCellCountMismatchError,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def golden_spec():
    """Canonical golden example grid spec (same as test_discovery_search_space.py)."""
    return DiscoveryGridSpec(
        grid_id='edge_miner_cross_asset_beta_lag_v1',
        schema_version=GRID_SCHEMA_VERSION,
        signal_family='cross_asset_beta_lag',
        source_venues=('binance_perp',),
        source_symbols=('BTC/USDT', 'ETH/USDT'),
        target_venues=('kraken', 'coinbase'),
        target_symbols=('SOL/USD', 'DOGE/USD', 'LINK/USD', 'AVAX/USD'),
        feature_types=('price_impulse', 'signed_imbalance', 'notional_burst', 'large_trade'),
        lookbacks_ms=(1000, 5000, 10000, 30000, 60000),
        thresholds_centibps=(1000, 2000, 3000, 5000),
        horizons_ms=(10000, 30000, 60000, 180000, 300000),
        entry_delays_ms=(0, 5000, 15000),
        cooldown_ms=60000,
        regime_filters=('market_active', 'market_stress', 'btc_1m_vol_p95'),
        cost_models_centibps=(1000, 2500, 5000),
        min_events=30,
        clustering_keys=('feature_types', 'lookbacks_ms', 'horizons_ms', 'target_symbols'),
        fdr_family_dimensions=(
            'source_symbols', 'target_symbols', 'feature_types', 'lookbacks_ms',
            'thresholds_centibps', 'horizons_ms', 'entry_delays_ms', 'regime_filters',
        ),
        created_at_utc='2026-05-17T00:00:00Z',
        notes='Golden example grid for deterministic freeze tests.',
    )


@pytest.fixture
def base_valid_spec():
    """Minimal valid grid spec for mutation-based tests."""
    return DiscoveryGridSpec(
        grid_id='test_grid',
        schema_version=GRID_SCHEMA_VERSION,
        signal_family='test_family',
        source_venues=('venue_a',),
        source_symbols=('SYM1/USD',),
        target_venues=('venue_b',),
        target_symbols=('SYM2/USD',),
        feature_types=('ma_cross',),
        lookbacks_ms=(1000,),
        thresholds_centibps=(100,),
        horizons_ms=(5000,),
        entry_delays_ms=(0,),
        cooldown_ms=1000,
        regime_filters=('all',),
        cost_models_centibps=(500,),
        min_events=1,
        clustering_keys=('feature_types',),
        fdr_family_dimensions=('source_symbols', 'feature_types'),
        created_at_utc='',
        notes='',
    )


# ===================================================================
# CLASS: Grid lock creation and validation (tests 41–53)
# ===================================================================


class TestGridLock:
    """Grid lock creation, validation, and semantic comparison."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mutate(spec: DiscoveryGridSpec, **overrides) -> DiscoveryGridSpec:
        """Return a copy of spec with selected fields overridden."""
        fields = [
            'grid_id', 'schema_version', 'signal_family',
            'source_venues', 'source_symbols', 'target_venues', 'target_symbols',
            'feature_types', 'lookbacks_ms', 'thresholds_centibps', 'horizons_ms',
            'entry_delays_ms', 'cooldown_ms', 'regime_filters',
            'cost_models_centibps', 'min_events',
            'clustering_keys', 'fdr_family_dimensions',
            'created_at_utc', 'notes',
        ]
        d = {k: getattr(spec, k) for k in fields}
        d.update(overrides)
        return DiscoveryGridSpec(**d)

    # ------------------------------------------------------------------
    # 41. Grid lock records primary_cell_count
    # ------------------------------------------------------------------

    def test_lock_records_primary_cell_count(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        expected = enumerate_primary_cell_count(golden_spec)
        assert lock.primary_cell_count == expected
        assert lock.primary_cell_count == 57600

    # ------------------------------------------------------------------
    # 42. Grid lock records cost_sensitivity_cell_count
    # ------------------------------------------------------------------

    def test_lock_records_cost_sensitivity_cell_count(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        expected = enumerate_cost_sensitivity_cell_count(golden_spec)
        assert lock.cost_sensitivity_cell_count == expected
        assert lock.cost_sensitivity_cell_count == 172800

    # ------------------------------------------------------------------
    # 43. Grid lock validation rejects primary_cell_count mismatch
    # ------------------------------------------------------------------

    def test_validation_rejects_primary_cell_count_mismatch(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        # Construct a lock with correct grid_hash but wrong primary_cell_count
        wrong_count_lock = DiscoveryGridLock(
            lock_type=lock.lock_type,
            grid_id=lock.grid_id,
            grid_hash=lock.grid_hash,
            schema_version=lock.schema_version,
            primary_cell_count=99999,  # Wrong count
            cost_sensitivity_cell_count=lock.cost_sensitivity_cell_count,
            git_sha=lock.git_sha,
            locked_at_utc=lock.locked_at_utc,
        )
        with pytest.raises(GridCellCountMismatchError, match='primary_cell_count'):
            validate_grid_lock(golden_spec, wrong_count_lock)

    # ------------------------------------------------------------------
    # 44. Grid lock validation rejects cost_sensitivity_cell_count mismatch
    # ------------------------------------------------------------------

    def test_validation_rejects_cost_sensitivity_cell_count_mismatch(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        # Construct a lock with correct grid_hash but wrong cost_sensitivity_cell_count
        wrong_count_lock = DiscoveryGridLock(
            lock_type=lock.lock_type,
            grid_id=lock.grid_id,
            grid_hash=lock.grid_hash,
            schema_version=lock.schema_version,
            primary_cell_count=lock.primary_cell_count,
            cost_sensitivity_cell_count=99999,  # Wrong count
            git_sha=lock.git_sha,
            locked_at_utc=lock.locked_at_utc,
        )
        with pytest.raises(GridCellCountMismatchError, match='cost_sensitivity_cell_count'):
            validate_grid_lock(golden_spec, wrong_count_lock)

    # ------------------------------------------------------------------
    # 45. Grid lock validates matching grid
    # ------------------------------------------------------------------

    def test_validation_passes_matching_grid(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        # Should not raise
        validate_grid_lock(golden_spec, lock)

    # ------------------------------------------------------------------
    # 46. Grid lock rejects hash mismatch (tampered grid_hash in lock)
    # ------------------------------------------------------------------

    def test_validation_rejects_tampered_hash(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        # Manually build a tampered lock with a different grid_hash
        tampered = DiscoveryGridLock(
            lock_type=lock.lock_type,
            grid_id=lock.grid_id,
            grid_hash='0' * 64,  # clearly wrong hash
            schema_version=lock.schema_version,
            primary_cell_count=lock.primary_cell_count,
            cost_sensitivity_cell_count=lock.cost_sensitivity_cell_count,
            git_sha=lock.git_sha,
            locked_at_utc=lock.locked_at_utc,
        )
        with pytest.raises(GridHashMismatchError, match='grid_hash mismatch'):
            validate_grid_lock(golden_spec, tampered)

    # ------------------------------------------------------------------
    # 47. Grid lock rejects grid_id mismatch
    # ------------------------------------------------------------------

    def test_validation_rejects_grid_id_mismatch(self, golden_spec):
        lock = create_grid_lock(golden_spec)
        modified = self._mutate(golden_spec, grid_id='different_grid_id')
        with pytest.raises(GridLockValidationError, match='grid_id mismatch'):
            validate_grid_lock(modified, lock)

    # ------------------------------------------------------------------
    # 48. Grid lock rejects schema_version mismatch
    # ------------------------------------------------------------------

    def test_validation_rejects_schema_version_mismatch(self, golden_spec):
        """Lock has different schema_version than spec. validate_grid_lock
        validates spec first (passes since spec is valid), then checks
        lock.schema_version == spec.schema_version and fails."""
        lock = create_grid_lock(golden_spec)
        # Create a manually-modified lock with different schema_version
        bad_lock = DiscoveryGridLock(
            lock_type=lock.lock_type,
            grid_id=lock.grid_id,
            grid_hash=lock.grid_hash,
            schema_version="wrong-version",
            primary_cell_count=lock.primary_cell_count,
            cost_sensitivity_cell_count=lock.cost_sensitivity_cell_count,
            git_sha=lock.git_sha,
            locked_at_utc=lock.locked_at_utc,
        )
        with pytest.raises(GridLockValidationError, match='schema_version mismatch'):
            validate_grid_lock(golden_spec, bad_lock)

    # ------------------------------------------------------------------
    # 49. Grid lock rejects wrong lock_type
    # ------------------------------------------------------------------

    def test_validation_rejects_wrong_lock_type(self, golden_spec):
        correct_lock = create_grid_lock(golden_spec)
        wrong_type_lock = DiscoveryGridLock(
            lock_type='WRONG_TYPE',
            grid_id=correct_lock.grid_id,
            grid_hash=correct_lock.grid_hash,
            schema_version=correct_lock.schema_version,
            primary_cell_count=correct_lock.primary_cell_count,
            cost_sensitivity_cell_count=correct_lock.cost_sensitivity_cell_count,
            git_sha=correct_lock.git_sha,
            locked_at_utc=correct_lock.locked_at_utc,
        )
        with pytest.raises(GridLockValidationError, match='lock_type'):
            validate_grid_lock(golden_spec, wrong_type_lock)

    # ------------------------------------------------------------------
    # 50. Existing identical lock with reordered keys and different
    #     whitespace but SAME semantic payload is accepted
    # ------------------------------------------------------------------

    def test_semantically_identical_locks_accepted(self, golden_spec):
        lock = create_grid_lock(golden_spec)

        # Build two dicts with the same payload but different key order
        common = {
            'lock_type': lock.lock_type,
            'grid_id': lock.grid_id,
            'grid_hash': lock.grid_hash,
            'schema_version': lock.schema_version,
            'primary_cell_count': lock.primary_cell_count,
            'cost_sensitivity_cell_count': lock.cost_sensitivity_cell_count,
            'git_sha': lock.git_sha,
            'locked_at_utc': lock.locked_at_utc,
        }

        # Dict A: keys in alphabetical order, pretty-printed
        dict_a = dict(sorted(common.items()))

        # Dict B: keys in reverse alphabetical order, compact
        dict_b = dict(reversed(sorted(common.items())))

        with tempfile.TemporaryDirectory() as tmpdir:
            path_a = Path(tmpdir) / 'lock_a.json'
            path_b = Path(tmpdir) / 'lock_b.json'

            # Write A with indent=2 (pretty, sorted keys)
            with open(path_a, 'w') as f:
                json.dump(dict_a, f, indent=2, sort_keys=True, ensure_ascii=False)

            # Write B as compact JSON with NO sort_keys and custom ordering
            # Use json.dumps with sort_keys=False to preserve the reversed order
            compact_json = json.dumps(dict_b, separators=(',', ':'), ensure_ascii=False)
            with open(path_b, 'w') as f:
                f.write(compact_json)
                f.write('\n')

            lock_a = load_grid_lock(path_a)
            lock_b = load_grid_lock(path_b)

            assert locks_are_semantically_equal(lock_a, lock_b)

    # ------------------------------------------------------------------
    # 51. Existing different lock is rejected by CLI (via raise from
    #     validate_grid_lock)
    # ------------------------------------------------------------------

    def test_different_lock_rejected(self, golden_spec, base_valid_spec):
        """Different lock content is rejected via validate_grid_lock."""
        golden_lock = create_grid_lock(golden_spec)

        # Create a lock from base_valid_spec — this will have different
        # grid_id, hash, cell counts, etc.
        base_lock = create_grid_lock(base_valid_spec)

        with pytest.raises(GridLockValidationError):
            validate_grid_lock(golden_spec, base_lock)

    # ------------------------------------------------------------------
    # 52. No --force option exists
    # ------------------------------------------------------------------

    def test_no_force_option_exists(self):
        """Verify the CLI script does not implement a --force argument.

        '--force' may appear in explanatory error messages (e.g., "No --force
        option exists") but must NOT appear as an argparse argument definition
        or a sys.argv-based check.
        """
        cli_path = Path(__file__).resolve().parent.parent / 'run_lock_discovery_grid.py'
        assert cli_path.exists(), f'CLI script not found: {cli_path}'

        cli_source = cli_path.read_text()

        # The only acceptable occurrence of '--force' is in a message
        # saying it does NOT exist.  Check that there is no argparse
        # argument definition or sys.argv check for --force.
        import_line = 'import argparse'
        assert import_line not in cli_source, 'CLI should not use argparse'

        # Check there is no sys.argv-based --force handling
        force_lines = [
            line.strip()
            for line in cli_source.splitlines()
            if '--force' in line
        ]

        # Every line containing '--force' must be an explanatory message
        # saying it does not exist (not an implementation of the flag).
        for line in force_lines:
            has_explanation = ('No --force option exists' in line) or ('no --force' in line.lower())
            assert has_explanation, (
                f'Unexpected --force usage (should only appear in explanatory message): '
                f'{line!r}'
            )

    # ------------------------------------------------------------------
    # 53. git_sha None is accepted by lock creation and validation
    # ------------------------------------------------------------------

    def test_git_sha_none_accepted(self, golden_spec):
        """Create a lock with git_sha=None, save/load it, and validate against it."""
        # Build a lock with explicit git_sha=None
        grid_hash = grid_sha256(golden_spec)
        primary = enumerate_primary_cell_count(golden_spec)
        cost_sens = enumerate_cost_sensitivity_cell_count(golden_spec)

        lock = DiscoveryGridLock(
            lock_type=GRID_LOCK_TYPE,
            grid_id=golden_spec.grid_id,
            grid_hash=grid_hash,
            schema_version=golden_spec.schema_version,
            primary_cell_count=primary,
            cost_sensitivity_cell_count=cost_sens,
            git_sha=None,
            locked_at_utc='2026-05-17T00:00:00Z',
        )

        assert lock.git_sha is None

        # Save to and load from a JSON file, verify git_sha remains None
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / 'lock_none_git.json'
            save_grid_lock(lock, lock_path)
            reloaded = load_grid_lock(lock_path)
            assert reloaded.git_sha is None

        # Validation should pass with this lock
        validate_grid_lock(golden_spec, lock)

    # ------------------------------------------------------------------
    # Edge case: lock with git_sha=None is not the same as missing key
    # ------------------------------------------------------------------

    def test_git_sha_none_survives_json_round_trip(self, golden_spec):
        """json serialization of git_sha=None must round-trip correctly."""
        lock = DiscoveryGridLock(
            lock_type=GRID_LOCK_TYPE,
            grid_id=golden_spec.grid_id,
            grid_hash=grid_sha256(golden_spec),
            schema_version=golden_spec.schema_version,
            primary_cell_count=enumerate_primary_cell_count(golden_spec),
            cost_sensitivity_cell_count=enumerate_cost_sensitivity_cell_count(golden_spec),
            git_sha=None,
            locked_at_utc='2026-05-17T00:00:00Z',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / 'lock_none_rt.json'
            save_grid_lock(lock, lock_path)

            # Read raw JSON to confirm git_sha is null, not missing
            raw = json.loads(lock_path.read_text())
            assert 'git_sha' in raw, 'git_sha key must be present in JSON'
            assert raw['git_sha'] is None, 'git_sha must serialize as JSON null'

            reloaded = load_grid_lock(lock_path)
            assert reloaded.git_sha is None
