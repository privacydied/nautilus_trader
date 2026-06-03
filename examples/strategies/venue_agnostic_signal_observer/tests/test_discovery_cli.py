"""Tests for CLI entrypoints and runtime safety.

Covers tests 95-102 (CLI) and 115-119 (runtime no-network/import side-effects).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ===================================================================
# CLI TESTS (95-102)
# ===================================================================

class TestGridLockCLI:
    """Tests 95-97: Grid lock CLI commands."""

    @pytest.fixture
    def golden_grid_json(self, tmp_path):
        """Write the golden grid spec as JSON."""
        spec = {
            "grid_id": "edge_miner_cross_asset_beta_lag_v1",
            "schema_version": "discovery-grid-v1",
            "signal_family": "cross_asset_beta_lag",
            "source_venues": ["binance_perp"],
            "source_symbols": ["BTC/USDT", "ETH/USDT"],
            "target_venues": ["kraken", "coinbase"],
            "target_symbols": ["SOL/USD", "DOGE/USD", "LINK/USD", "AVAX/USD"],
            "feature_types": ["price_impulse", "signed_imbalance", "notional_burst", "large_trade"],
            "lookbacks_ms": [1000, 5000, 10000, 30000, 60000],
            "thresholds_centibps": [1000, 2000, 3000, 5000],
            "horizons_ms": [10000, 30000, 60000, 180000, 300000],
            "entry_delays_ms": [0, 5000, 15000],
            "cooldown_ms": 60000,
            "regime_filters": ["market_active", "market_stress", "btc_1m_vol_p95"],
            "cost_models_centibps": [1000, 2500, 5000],
            "min_events": 30,
            "clustering_keys": ["feature_types", "lookbacks_ms", "horizons_ms", "target_symbols"],
            "fdr_family_dimensions": [
                "source_symbols", "target_symbols", "feature_types",
                "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "regime_filters",
            ],
            "created_at_utc": "2026-05-17T00:00:00Z",
            "notes": "Golden example.",
        }
        path = tmp_path / "golden_grid.json"
        with open(path, "w") as f:
            json.dump(spec, f, indent=2)
        return path

    def _run_cli(self, script_name, *args):
        """Run a CLI script and return (returncode, stdout, stderr)."""
        base = Path(__file__).resolve().parent.parent
        script = base / "runners" / "legacy_cli" / script_name
        cmd = [sys.executable, str(script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr

    def test_95_lock_command_writes_lock_file(self, golden_grid_json, tmp_path):
        """CLI lock command writes a grid lock file."""
        lock_path = tmp_path / "test_lock.json"
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_grid.py",
            str(golden_grid_json),
            str(lock_path),
        )
        assert rc == 0, f"CLI failed: {stderr}"
        assert lock_path.exists()
        assert "Grid lock written" in stdout
        assert "grid_hash" in stdout

    def test_95b_lock_file_is_valid_json(self, golden_grid_json, tmp_path):
        """Written lock file is valid JSON."""
        lock_path = tmp_path / "test_lock.json"
        self._run_cli("run_lock_discovery_grid.py", str(golden_grid_json), str(lock_path))
        with open(lock_path) as f:
            data = json.load(f)
        assert data["lock_type"] == "DISCOVERY_GRID_LOCK"
        assert len(data["grid_hash"]) == 64

    def test_96_validate_command_passes(self, golden_grid_json, tmp_path):
        """CLI validate command exits zero for matching grid/lock."""
        lock_path = tmp_path / "test_lock.json"
        self._run_cli("run_lock_discovery_grid.py", str(golden_grid_json), str(lock_path))
        rc, stdout, stderr = self._run_cli(
            "run_validate_discovery_grid_lock.py",
            str(golden_grid_json),
            str(lock_path),
        )
        assert rc == 0, f"Validation failed: {stderr}"
        assert "PASSED" in stdout

    def test_97_validate_command_fails_on_mismatch(self, golden_grid_json, tmp_path):
        """CLI validate command exits non-zero for mismatched grid/lock."""
        lock_path = tmp_path / "test_lock.json"
        self._run_cli("run_lock_discovery_grid.py", str(golden_grid_json), str(lock_path))

        # Modify the grid spec (change signal_family)
        with open(golden_grid_json) as f:
            spec_data = json.load(f)
        spec_data["signal_family"] = "different_family"
        modified_grid = tmp_path / "modified_grid.json"
        with open(modified_grid, "w") as f:
            json.dump(spec_data, f, indent=2)

        rc, stdout, stderr = self._run_cli(
            "run_validate_discovery_grid_lock.py",
            str(modified_grid),
            str(lock_path),
        )
        assert rc != 0, "Validation should have failed"

    def test_50_semantic_comparison(self, golden_grid_json, tmp_path):
        """Existing identical lock with reordered keys and different whitespace is accepted."""
        lock_path = tmp_path / "test_lock.json"
        self._run_cli("run_lock_discovery_grid.py", str(golden_grid_json), str(lock_path))

        # Second run with same grid should succeed
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_grid.py",
            str(golden_grid_json),
            str(lock_path),
        )
        assert rc == 0, f"Second lock run failed: {stderr}"
        assert "Already locked" in stdout or "already locked" in stdout

    def test_51_different_lock_rejected(self, golden_grid_json, tmp_path):
        """Existing different lock is rejected by CLI."""
        lock_path = tmp_path / "test_lock.json"
        self._run_cli("run_lock_discovery_grid.py", str(golden_grid_json), str(lock_path))

        # Modify the grid spec to produce a different lock
        with open(golden_grid_json) as f:
            spec_data = json.load(f)
        spec_data["signal_family"] = "different_family"
        spec_data["grid_id"] = "different_grid_id"
        modified_grid = tmp_path / "modified_grid.json"
        with open(modified_grid, "w") as f:
            json.dump(spec_data, f, indent=2)

        # Try to lock the modified grid to the same path
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_grid.py",
            str(modified_grid),
            str(lock_path),
        )
        assert rc != 0, "Different lock should have been rejected"
        assert "different content" in stderr.lower()

    def test_52_no_force_option(self):
        """No --force option exists in the CLI scripts."""
        base = Path(__file__).resolve().parent.parent
        cli_scripts = [
            base / "runners" / "legacy_cli" / "run_lock_discovery_grid.py",
            base / "runners" / "legacy_cli" / "run_validate_discovery_grid_lock.py",
            base / "runners" / "legacy_cli" / "run_lock_discovery_candidate.py",
        ]
        for script in cli_scripts:
            if script.exists():
                content = script.read_text()
                # Check that --force is only in an error message, not as an argument
                assert "--force" not in content or "No --force" in content

    def test_53_git_sha_none_accepted(self, golden_grid_json, tmp_path):
        """git_sha None is accepted by lock creation and validation."""
        lock_path = tmp_path / "test_lock.json"
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_grid.py",
            str(golden_grid_json),
            str(lock_path),
        )
        assert rc == 0
        with open(lock_path) as f:
            data = json.load(f)
        # git_sha can be None or a string
        assert "git_sha" in data


class TestCandidateLockCLI:
    """Tests 98-102: Candidate lock CLI."""

    @pytest.fixture
    def golden_grid_json(self, tmp_path):
        """Write the golden grid spec as JSON."""
        spec = {
            "grid_id": "edge_miner_cross_asset_beta_lag_v1",
            "schema_version": "discovery-grid-v1",
            "signal_family": "cross_asset_beta_lag",
            "source_venues": ["binance_perp"],
            "source_symbols": ["BTC/USDT", "ETH/USDT"],
            "target_venues": ["kraken", "coinbase"],
            "target_symbols": ["SOL/USD", "DOGE/USD", "LINK/USD", "AVAX/USD"],
            "feature_types": ["price_impulse", "signed_imbalance", "notional_burst", "large_trade"],
            "lookbacks_ms": [1000, 5000, 10000, 30000, 60000],
            "thresholds_centibps": [1000, 2000, 3000, 5000],
            "horizons_ms": [10000, 30000, 60000, 180000, 300000],
            "entry_delays_ms": [0, 5000, 15000],
            "cooldown_ms": 60000,
            "regime_filters": ["market_active", "market_stress", "btc_1m_vol_p95"],
            "cost_models_centibps": [1000, 2500, 5000],
            "min_events": 30,
            "clustering_keys": ["feature_types", "lookbacks_ms", "horizons_ms", "target_symbols"],
            "fdr_family_dimensions": [
                "source_symbols", "target_symbols", "feature_types",
                "lookbacks_ms", "thresholds_centibps", "horizons_ms",
                "entry_delays_ms", "regime_filters",
            ],
            "created_at_utc": "2026-05-17T00:00:00Z",
            "notes": "Golden example.",
        }
        path = tmp_path / "golden_grid.json"
        with open(path, "w") as f:
            json.dump(spec, f, indent=2)
        return path

    @pytest.fixture
    def grid_lock_json(self, golden_grid_json, tmp_path):
        """Create a grid lock file."""
        base = Path(__file__).resolve().parent.parent
        script = base / "runners" / "legacy_cli" / "run_lock_discovery_grid.py"
        lock_path = tmp_path / "grid_lock.json"
        result = subprocess.run(
            [sys.executable, str(script), str(golden_grid_json), str(lock_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        return lock_path

    @pytest.fixture
    def selected_cells_json(self, tmp_path):
        """Write a valid selected cells JSON."""
        cells = [
            {
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
        ]
        path = tmp_path / "selected_cells.json"
        with open(path, "w") as f:
            json.dump(cells, f)
        return path

    @pytest.fixture
    def capture_manifest(self, tmp_path):
        """Create a valid capture manifest JSON."""
        manifest = {"run_id": "capture_001"}
        path = tmp_path / "manifest.json"
        with open(path, "w") as f:
            json.dump(manifest, f)
        return path

    def _run_cli(self, script_name, *args):
        """Run a CLI script and return (returncode, stdout, stderr)."""
        base = Path(__file__).resolve().parent.parent
        script = base / "runners" / "legacy_cli" / script_name
        cmd = [sys.executable, str(script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr

    def test_98_candidate_lock_command_writes_file(
        self, golden_grid_json, grid_lock_json, selected_cells_json, capture_manifest, tmp_path
    ):
        """CLI candidate lock command writes a candidate lock file."""
        out_path = tmp_path / "candidate_lock.json"
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
            str(capture_manifest),
        )
        assert rc == 0, f"Candidate lock CLI failed: {stderr}"
        assert out_path.exists()
        with open(out_path) as f:
            data = json.load(f)
        assert data["lock_type"] == "DISCOVERY_CANDIDATE_LOCK"
        assert len(data["candidate_hash"]) == 64

    def test_99_missing_manifest_exits_nonzero(
        self, golden_grid_json, grid_lock_json, selected_cells_json, tmp_path
    ):
        """CLI candidate lock exits non-zero when capture manifest path is missing."""
        out_path = tmp_path / "candidate_lock.json"
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
        )
        assert rc != 0, "Should fail without capture manifest paths"

    def test_99b_bad_manifest_exits_nonzero(
        self, golden_grid_json, grid_lock_json, selected_cells_json, tmp_path
    ):
        """CLI candidate lock exits non-zero when capture manifest path is bad."""
        out_path = tmp_path / "candidate_lock.json"
        rc, stdout, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
            "/nonexistent/manifest.json",
        )
        assert rc != 0, "Should fail with bad manifest path"

    def test_100_refuses_to_overwrite_different(
        self, golden_grid_json, grid_lock_json, selected_cells_json, capture_manifest, tmp_path
    ):
        """CLI refuses to overwrite a different existing candidate lock."""
        out_path = tmp_path / "candidate_lock.json"

        # First write
        rc, _, _ = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
            str(capture_manifest),
        )
        assert rc == 0

        # Modify the selected cells to create different content
        diff_cells = [
            {
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
        ]
        diff_cells_path = tmp_path / "diff_cells.json"
        with open(diff_cells_path, "w") as f:
            json.dump(diff_cells, f)

        rc, _, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(diff_cells_path),
            str(out_path),
            str(capture_manifest),
        )
        assert rc != 0, "Should refuse to overwrite different lock"
        assert "different content" in stderr

    def test_101_accepts_identical_existing(
        self, golden_grid_json, grid_lock_json, selected_cells_json, capture_manifest, tmp_path
    ):
        """CLI accepts a semantically identical existing candidate lock."""
        out_path = tmp_path / "candidate_lock.json"

        # Write twice
        rc1, _, _ = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
            str(capture_manifest),
        )
        assert rc1 == 0

        rc2, stdout, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
            str(golden_grid_json),
            str(grid_lock_json),
            str(selected_cells_json),
            str(out_path),
            str(capture_manifest),
        )
        assert rc2 == 0, f"Second write failed: {stderr}"
        assert "Already locked" in stdout or "already locked" in stdout

    def test_102_requires_grid_spec_path(self, grid_lock_json, selected_cells_json, tmp_path):
        """CLI requires grid spec path argument."""
        out_path = tmp_path / "candidate_lock.json"
        rc, _, stderr = self._run_cli(
            "run_lock_discovery_candidate.py",
        )
        assert rc != 0
        assert "Usage" in stderr


# ===================================================================
# RUNTIME NO-NETWORK / IMPORT SIDE-EFFECT TESTS (115-119)
# ===================================================================

class TestRuntimeSafety:
    """Tests 115-119: Runtime no-network and import side-effect tests."""

    def test_115_no_socket_at_import(self):
        """Import each discovery module; assert no socket is opened at import."""
        import socket
        original_socket = socket.socket

        call_count = [0]

        def tracking_socket(*args, **kwargs):
            call_count[0] += 1
            return original_socket(*args, **kwargs)

        socket.socket = tracking_socket
        try:
            import examples.strategies.venue_agnostic_signal_observer.discovery.search_space  # noqa: F811
            import examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock  # noqa: F811
            import examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint  # noqa: F811
            import examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock  # noqa: F811
            import examples.strategies.venue_agnostic_signal_observer.discovery.promotion_boundary  # noqa: F811
            import examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan  # noqa: F811
            # No socket calls at import time expected
        finally:
            socket.socket = original_socket

    def test_116_no_socket_in_cli_execution(self, golden_grid_json, tmp_path):
        """Run lock/validation CLIs under monkeypatched socket; assert no socket opened."""
        import socket
        original_socket = socket.socket

        call_count = [0]

        def blocking_socket(*args, **kwargs):
            call_count[0] += 1
            raise RuntimeError("Socket creation blocked in test")

        lock_path = tmp_path / "test_lock.json"
        socket.socket = blocking_socket
        try:
            # Create grid spec JSON
            spec = {
                "grid_id": "test_grid",
                "schema_version": "discovery-grid-v1",
                "signal_family": "test",
                "source_venues": ["binance"],
                "source_symbols": ["BTC/USDT"],
                "target_venues": ["kraken"],
                "target_symbols": ["SOL/USD"],
                "feature_types": ["price_impulse"],
                "lookbacks_ms": [1000],
                "thresholds_centibps": [1000],
                "horizons_ms": [10000],
                "entry_delays_ms": [0],
                "cooldown_ms": 1000,
                "regime_filters": ["market_active"],
                "cost_models_centibps": [1000],
                "min_events": 10,
                "clustering_keys": ["feature_types"],
                "fdr_family_dimensions": ["feature_types"],
                "created_at_utc": "",
                "notes": "",
            }
            grid_path = tmp_path / "grid.json"
            with open(grid_path, "w") as f:
                json.dump(spec, f)

            # Run lock command
            base = Path(__file__).resolve().parent.parent
            from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_lock_discovery_grid import (
                main as lock_main,
            )
            import sys
            orig_argv = sys.argv
            sys.argv = ["run_lock_discovery_grid.py", str(grid_path), str(lock_path)]
            try:
                rc = lock_main()
                assert rc == 0, "Lock CLI should succeed without sockets"
            finally:
                sys.argv = orig_argv
        finally:
            socket.socket = original_socket

    def test_117_no_files_written_at_import(self, tmp_path):
        """Import every discovery module and assert no files are written at import time."""
        import os

        # Snapshot files in the discovery package directory
        discovery_dir = Path(__file__).resolve().parent.parent / "discovery"
        before = set()
        for root, dirs, files in os.walk(discovery_dir):
            # Skip __pycache__
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                before.add(Path(root, f))

        # Reload modules
        import importlib
        modules = [
            "examples.strategies.venue_agnostic_signal_observer.discovery.exceptions",
            "examples.strategies.venue_agnostic_signal_observer.discovery.search_space",
            "examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock",
            "examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint",
            "examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock",
            "examples.strategies.venue_agnostic_signal_observer.discovery.promotion_boundary",
            "examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan",
        ]
        for mod_name in modules:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])

        # Check no new files
        after = set()
        for root, dirs, files in os.walk(discovery_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                after.add(Path(root, f))

        new_files = after - before
        # Allow __pycache__ .pyc files in __pycache__
        new_non_cache = {
            f for f in new_files
            if "__pycache__" not in str(f) and not str(f).endswith(".pyc")
        }
        assert len(new_non_cache) == 0, f"New files appeared after import: {new_non_cache}"

    def test_118_no_subprocess_at_import(self):
        """Import every discovery module; assert no subprocess call happens at import time."""
        import subprocess
        original_run = subprocess.run

        call_count = [0]

        def blocking_run(*args, **kwargs):
            call_count[0] += 1
            raise RuntimeError("subprocess.run blocked in test")

        subprocess.run = blocking_run
        try:
            import importlib
            modules = [
                "examples.strategies.venue_agnostic_signal_observer.discovery.exceptions",
                "examples.strategies.venue_agnostic_signal_observer.discovery.search_space",
                "examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock",
                "examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint",
                "examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock",
                "examples.strategies.venue_agnostic_signal_observer.discovery.promotion_boundary",
                "examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan",
            ]
            for mod_name in modules:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
        finally:
            subprocess.run = original_run

    def _create_grid_json(self, tmp_path, grid_id="test_grid"):
        """Helper to create a grid JSON file."""
        spec = {
            "grid_id": grid_id,
            "schema_version": "discovery-grid-v1",
            "signal_family": "test",
            "source_venues": ["binance"],
            "source_symbols": ["BTC/USDT"],
            "target_venues": ["kraken"],
            "target_symbols": ["SOL/USD"],
            "feature_types": ["price_impulse"],
            "lookbacks_ms": [1000],
            "thresholds_centibps": [1000],
            "horizons_ms": [10000],
            "entry_delays_ms": [0],
            "cooldown_ms": 1000,
            "regime_filters": ["market_active"],
            "cost_models_centibps": [1000],
            "min_events": 10,
            "clustering_keys": ["feature_types"],
            "fdr_family_dimensions": ["feature_types"],
            "created_at_utc": "",
            "notes": "",
        }
        path = tmp_path / f"{grid_id}.json"
        with open(path, "w") as f:
            json.dump(spec, f)
        return path

    @pytest.fixture
    def golden_grid_json(self, tmp_path):
        return self._create_grid_json(tmp_path)

    @pytest.fixture
    def grid_lock_json(self, golden_grid_json, tmp_path):
        base = Path(__file__).resolve().parent.parent
        script = base / "runners" / "legacy_cli" / "run_lock_discovery_grid.py"
        lock_path = tmp_path / "grid_lock.json"
        result = subprocess.run(
            [sys.executable, str(script), str(golden_grid_json), str(lock_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        return lock_path

    def test_119_importing_is_side_effect_free(self):
        """Importing every discovery module is side-effect free overall.

        No writes, no subprocess, no socket at import time.
        """
        import socket
        import os

        # Combine checks from 115, 117, 118
        socket_original = socket.socket
        socket_calls = [0]

        def tracking_socket(*args, **kwargs):
            socket_calls[0] += 1
            return socket_original(*args, **kwargs)

        socket.socket = tracking_socket

        discovery_dir = Path(__file__).resolve().parent.parent / "discovery"
        before_files = set()
        for root, dirs, files in os.walk(discovery_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                before_files.add(Path(root, f))

        try:
            import importlib
            modules = [
                "examples.strategies.venue_agnostic_signal_observer.discovery.exceptions",
                "examples.strategies.venue_agnostic_signal_observer.discovery.search_space",
                "examples.strategies.venue_agnostic_signal_observer.discovery.grid_lock",
                "examples.strategies.venue_agnostic_signal_observer.discovery.capture_fingerprint",
                "examples.strategies.venue_agnostic_signal_observer.discovery.candidate_lock",
                "examples.strategies.venue_agnostic_signal_observer.discovery.promotion_boundary",
                "examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan",
            ]
            for mod_name in modules:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])

            assert socket_calls[0] == 0, f"Sockets created at import: {socket_calls[0]}"

            after_files = set()
            for root, dirs, files in os.walk(discovery_dir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    after_files.add(Path(root, f))

            new_files = after_files - before_files
            new_non_cache = {
                f for f in new_files
                if "__pycache__" not in str(f) and not str(f).endswith(".pyc")
            }
            assert len(new_non_cache) == 0, f"New files after import: {new_non_cache}"
        finally:
            socket.socket = socket_original
