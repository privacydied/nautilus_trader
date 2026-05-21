# Copyright (C) 2026. All rights reserved.
"""
CLI tests for discovery freeze entrypoints.

Tests test cases 95-102 from the discovery freeze specification.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from venue_agnostic_signal_observer.discovery.candidate_lock import load_candidate_lock
from venue_agnostic_signal_observer.discovery.grid_lock import GRID_LOCK_TYPE
from venue_agnostic_signal_observer.discovery.grid_lock import create_grid_lock
from venue_agnostic_signal_observer.discovery.grid_lock import load_grid_lock
from venue_agnostic_signal_observer.discovery.search_space import DiscoveryGridSpec


# ── Paths ──────────────────────────────────────────────────────────────

DISCOVERY_DIR = Path(__file__).resolve().parent.parent / "discovery"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

GRID_LOCK_MODULE = "run_lock_discovery_grid"
VALIDATE_MODULE = "run_validate_discovery_grid_lock"
CANDIDATE_MODULE = "run_lock_discovery_candidate"

GOLDEN_GRID_JSON = FIXTURES_DIR / "golden_grid.json"
MANIFEST_1 = FIXTURES_DIR / "test_manifest_capture_001.json"
MANIFEST_2 = FIXTURES_DIR / "test_manifest_capture_002.json"


# ── Helpers ────────────────────────────────────────────────────────────


def _golden_spec() -> DiscoveryGridSpec:
    return DiscoveryGridSpec(
        grid_id="edge_miner_cross_asset_beta_lag_v1",
        schema_version="discovery-grid-v1",
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
    )


def _sample_cells():
    return [{
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
    }]


DISCOVERY_MODULE = "examples.strategies.venue_agnostic_signal_observer.discovery"


def run_cli(module_suffix: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run a CLI module using -m invocation (needed for relative imports)."""
    return subprocess.run(
        [sys.executable, "-m", f"{DISCOVERY_MODULE}.{module_suffix}", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── Test 95: CLI lock command ──────────────────────────────────────────


class TestCliGridLock:
    """Tests 95, 50-51, 52 for grid lock CLI."""

    def test_95_writes_grid_lock_file(self):
        """Test 95: CLI lock command writes a grid lock file."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            lock_path = f.name

        try:
            result = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result.returncode == 0, (
                f"CLI exited {result.returncode}: {result.stderr}"
            )
            assert "Grid lock written" in result.stdout, (
                f"Expected success message in: {result.stdout}"
            )
            assert Path(lock_path).exists()
            lock = load_grid_lock(lock_path)
            assert lock.lock_type == GRID_LOCK_TYPE
            assert lock.grid_id == "edge_miner_cross_asset_beta_lag_v1"
        finally:
            Path(lock_path).unlink(missing_ok=True)

    def test_50_identical_lock_accepted(self):
        """Test 50: existing identical lock is accepted via semantic comparison."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            lock_path = f.name

        try:
            # First call — create the lock
            result1 = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result1.returncode == 0

            # Second call — identical spec, should say "already locked"
            result2 = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result2.returncode == 0, (
                f"Expected exit 0 for identical lock, got "
                f"{result2.returncode}: {result2.stderr}"
            )
            assert "already locked" in result2.stdout.lower() or \
                   "semantically identical" in result2.stdout.lower()
        finally:
            Path(lock_path).unlink(missing_ok=True)

    def test_51_different_lock_rejected(self):
        """Test 51: existing different lock is rejected by CLI."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            lock_path = f.name

        try:
            # Create initial lock
            spec = _golden_spec()
            create_grid_lock(spec)
            lock_data = {
                "lock_type": GRID_LOCK_TYPE,
                "grid_id": spec.grid_id,
                "grid_hash": "ffffff0000000000000000000000000000000000000000000000000000000000",
                "schema_version": spec.schema_version,
                "primary_cell_count": 0,
                "cost_sensitivity_cell_count": 0,
                "git_sha": None,
                "locked_at_utc": "2026-01-01T00:00:00",
            }
            with open(lock_path, "w") as f:
                json.dump(lock_data, f)

            # Second call — different lock, should fail
            result = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result.returncode != 0, (
                f"Expected non-zero exit for different lock, "
                f"got {result.returncode}: {result.stdout}"
            )
            assert "different content" in result.stderr.lower()
        finally:
            Path(lock_path).unlink(missing_ok=True)

    def test_52_no_force_option(self):
        """Test 52: no --force option exists in CLI."""
        result = run_cli(
            GRID_LOCK_MODULE,
            ["--help"],
        )
        assert "--force" not in result.stdout, "CLI should not have --force option"

    def test_print_grid_hash_and_counts(self):
        """Grid lock CLI prints hash and cell counts."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            lock_path = f.name

        try:
            result = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result.returncode == 0
            assert "grid_hash" in result.stdout
            assert "primary_cell_count" in result.stdout
            assert "cost_sensitivity_cell_count" in result.stdout
        finally:
            Path(lock_path).unlink(missing_ok=True)


# ── Tests 96-97: validate command ──────────────────────────────────────


class TestCliValidate:
    """Tests 96, 97 for validate CLI."""

    def test_96_validate_exits_zero_for_match(self):
        """Test 96: CLI validate command exits zero for matching grid/lock."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as lock_f:
            lock_path = lock_f.name

        try:
            # Create lock
            result_lock = run_cli(
                GRID_LOCK_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result_lock.returncode == 0

            # Validate
            result = run_cli(
                VALIDATE_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result.returncode == 0, (
                f"Validation failed: {result.stderr}"
            )
            assert "matches" in result.stdout.lower()
        finally:
            Path(lock_path).unlink(missing_ok=True)

    def test_97_validate_exits_nonzero_for_mismatch(self):
        """Test 97: CLI validate exits non-zero for mismatched grid/lock."""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as lock_f:
            lock_path = lock_f.name

        try:
            # Create a lock with a different grid spec
            diff_spec = DiscoveryGridSpec(
                grid_id="different_grid",
                schema_version="discovery-grid-v1",
                signal_family="test",
                source_venues=("test",),
                source_symbols=("BTC/USDT",),
                target_venues=("kraken",),
                target_symbols=("SOL/USD",),
                feature_types=("price_impulse",),
                lookbacks_ms=(1000,),
                thresholds_centibps=(1000,),
                horizons_ms=(10000,),
                entry_delays_ms=(0,),
                cooldown_ms=60000,
                regime_filters=("market_active",),
                cost_models_centibps=(1000,),
                min_events=30,
                clustering_keys=("feature_types",),
                fdr_family_dimensions=("feature_types",),
            )
            diff_lock = create_grid_lock(diff_spec)
            with open(lock_path, "w") as f:
                import dataclasses
                json.dump(dataclasses.asdict(diff_lock), f, indent=2)

            # Validate golden spec against different lock
            result = run_cli(
                VALIDATE_MODULE,
                [str(GOLDEN_GRID_JSON), lock_path],
            )
            assert result.returncode != 0, (
                f"Expected non-zero exit for mismatched grid/lock, "
                f"got {result.returncode}: {result.stdout}"
            )
        finally:
            Path(lock_path).unlink(missing_ok=True)


# ── Tests 98-102: candidate lock CLI ────────────────────────────────────


class TestCliCandidateLock:
    """Tests 98-102 for candidate lock CLI."""

    def _make_selected_cells_file(self) -> str:
        """Create a temporary selected cells JSON file."""
        f = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        )
        json.dump(_sample_cells(), f)
        f.close()
        return f.name

    def _make_grid_lock_file(self) -> str:
        """Create a temporary grid lock file from golden spec."""
        f = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        )
        f.close()
        result = run_cli(
            GRID_LOCK_MODULE,
            [str(GOLDEN_GRID_JSON), f.name],
        )
        assert result.returncode == 0
        return f.name

    def test_98_writes_candidate_lock_file(self):
        """Test 98: CLI candidate lock command writes a candidate lock file."""
        lock_path = self._make_grid_lock_file()
        cells_path = self._make_selected_cells_file()

        try:
            output_f = tempfile.NamedTemporaryFile(
                suffix=".json", mode="w", delete=False
            )
            output_path = output_f.name
            output_f.close()

            result = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", str(MANIFEST_1),
                    "--candidate-id", "test_cli_candidate",
                ],
            )
            assert result.returncode == 0, (
                f"Candidate lock CLI failed: {result.stderr}"
            )
            assert "Candidate lock written" in result.stdout

            # Verify the output file is valid
            candidate = load_candidate_lock(output_path)
            assert candidate.candidate_id == "test_cli_candidate"
            assert candidate.candidate_hash != ""

            Path(output_path).unlink(missing_ok=True)
        finally:
            Path(lock_path).unlink(missing_ok=True)
            Path(cells_path).unlink(missing_ok=True)

    def test_99_exits_nonzero_missing_manifest(self):
        """Test 99: CLI exits non-zero when a capture manifest path is missing."""
        lock_path = self._make_grid_lock_file()
        cells_path = self._make_selected_cells_file()

        try:
            output_f = tempfile.NamedTemporaryFile(
                suffix=".json", mode="w", delete=False
            )
            output_path = output_f.name
            output_f.close()

            result = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", "/tmp/nonexistent_manifest.json",
                ],
            )
            assert result.returncode != 0, (
                f"Expected non-zero exit for missing manifest, "
                f"got {result.returncode}: {result.stdout}"
            )

            Path(output_path).unlink(missing_ok=True)
        finally:
            Path(lock_path).unlink(missing_ok=True)
            Path(cells_path).unlink(missing_ok=True)

    def test_100_refuses_overwrite_different(self):
        """Test 100: CLI refuses to overwrite a different existing candidate lock."""
        lock_path = self._make_grid_lock_file()
        cells_path = self._make_selected_cells_file()

        try:
            output_f = tempfile.NamedTemporaryFile(
                suffix=".json", mode="w", delete=False
            )
            output_path = output_f.name
            output_f.close()

            # First write
            result1 = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", str(MANIFEST_1),
                    "--candidate-id", "test_cli_candidate",
                ],
            )
            assert result1.returncode == 0

            # Second write with different candidate ID should fail if content differs
            result2 = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", str(MANIFEST_2),  # different manifest
                    "--candidate-id", "test_cli_candidate_v2",
                ],
            )
            assert result2.returncode != 0, (
                f"Expected non-zero exit for overwrite attempt, "
                f"got {result2.returncode}"
            )

            Path(output_path).unlink(missing_ok=True)
        finally:
            Path(lock_path).unlink(missing_ok=True)
            Path(cells_path).unlink(missing_ok=True)

    def test_101_accepts_identical_existing(self):
        """Test 101: CLI accepts semantically identical existing candidate lock."""
        lock_path = self._make_grid_lock_file()
        cells_path = self._make_selected_cells_file()

        try:
            output_f = tempfile.NamedTemporaryFile(
                suffix=".json", mode="w", delete=False
            )
            output_path = output_f.name
            output_f.close()

            # First write
            result1 = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", str(MANIFEST_1),
                    "--candidate-id", "test_identical",
                ],
            )
            assert result1.returncode == 0

            # Second write with identical content
            result2 = run_cli(
                CANDIDATE_MODULE,
                [
                    str(GOLDEN_GRID_JSON),
                    lock_path,
                    cells_path,
                    output_path,
                    "--capture", str(MANIFEST_1),
                    "--candidate-id", "test_identical",
                ],
            )
            assert result2.returncode == 0, (
                f"Expected exit 0 for identical candidate, "
                f"got {result2.returncode}: {result2.stderr}"
            )
            assert "already locked" in result2.stdout.lower() or \
                   "semantically identical" in result2.stdout.lower()

            Path(output_path).unlink(missing_ok=True)
        finally:
            Path(lock_path).unlink(missing_ok=True)
            Path(cells_path).unlink(missing_ok=True)

    def test_102_requires_grid_spec_path(self):
        """Test 102: CLI requires a grid spec path argument."""
        result = run_cli(
            CANDIDATE_MODULE,
            ["--help"],
        )
        assert result.returncode == 0
        # --help should list the required positional arguments
        assert "grid_spec" in result.stdout or "positional arguments" in result.stdout
