"""Tests for scripts/fetch_kraken_trades.py and scripts/build_kraken_replay_source_config.py.

Tests are offline (no API calls) — they validate precommitment-parsing logic
and source-config generation only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Find the repository root by walking up from this test file."""
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (parent / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json").exists():
            return parent
    raise FileNotFoundError("Could not find repo root")


@pytest.fixture(scope="session")
def precommitment(repo_root: Path) -> dict:
    path = repo_root / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestBuildSourceConfig:
    """Tests for build_kraken_replay_source_config.py"""

    def test_script_imports_and_reads_precommitment(self, repo_root: Path) -> None:
        """The build script can resolve and parse the precommitment."""
        sys.path.insert(0, str(repo_root))
        try:
            import scripts.build_kraken_replay_source_config as mod  # type: ignore
            # Use the script's own resolve logic
            p = mod.resolve_from_repo_root(
                "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"
            )
            assert p.exists()
            data = json.loads(p.read_text(encoding="utf-8"))
            assert "multi_date_windows" in data
        finally:
            sys.path.pop(0)

    def test_generated_source_config_has_expected_entries(
        self, repo_root: Path, tmp_path: Path
    ) -> None:
        """Running the build script produces the correct number of source entries."""
        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "build_kraken_replay_source_config.py"),
                "--precommitment",
                str(repo_root / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"),
                "--out",
                str(tmp_path / "offline_sources.json"),
                "--data-dir",
                "data/kraken_trades",
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0, f"Script failed:\n{result.stderr}"

        config = json.loads((tmp_path / "offline_sources.json").read_text())
        sources = config["sources"]
        assert len(sources) > 0, "Zero source entries generated"

    def test_each_entry_has_required_fields(
        self, repo_root: Path, tmp_path: Path
    ) -> None:
        """Every source entry has the required fields."""
        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "build_kraken_replay_source_config.py"),
                "--precommitment",
                str(repo_root / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"),
                "--out",
                str(tmp_path / "offline_sources.json"),
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0

        config = json.loads((tmp_path / "offline_sources.json").read_text())
        required = {
            "path", "logical_source_id", "venue", "symbol",
            "base_asset", "quote_asset", "source_kind", "stream_type",
            "resolution_type", "timestamp_unit", "expected_start", "expected_end",
        }
        for i, entry in enumerate(config["sources"]):
            missing = required - set(entry.keys())
            assert not missing, f"Entry {i} missing fields: {missing}"

    def test_each_entry_references_precommitment_label(
        self, repo_root: Path, tmp_path: Path
    ) -> None:
        """Each source entry path includes a label from multi_date_windows."""
        precommit = json.loads(
            (repo_root / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json").read_text()
        )
        expected_labels = {w["label"] for w in precommit["multi_date_windows"]}

        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "build_kraken_replay_source_config.py"),
                "--precommitment",
                str(repo_root / "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"),
                "--out",
                str(tmp_path / "offline_sources.json"),
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        assert result.returncode == 0

        config = json.loads((tmp_path / "offline_sources.json").read_text())
        found_labels = set()
        for entry in config["sources"]:
            for label in expected_labels:
                if label in entry["path"]:
                    found_labels.add(label)
        assert found_labels == expected_labels, (
            f"Missing labels: {expected_labels - found_labels}"
        )


class TestFetchKrakenTrades:
    """Tests for fetch_kraken_trades.py — parsing/precommitment logic only, no API."""

    def test_resolve_precommitment(self, repo_root: Path) -> None:
        """The fetch script can resolve the precommitment file from the repo root."""
        sys.path.insert(0, str(repo_root))
        try:
            import scripts.fetch_kraken_trades as mod  # type: ignore
            path = mod.resolve_precommitment()
            assert path.exists()
            assert path.name == "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"
        finally:
            sys.path.pop(0)

    def test_precommitment_has_multi_date_windows(
        self, precommitment: dict
    ) -> None:
        """The precommitment has a non-empty multi_date_windows field."""
        windows = precommitment.get("multi_date_windows")
        assert windows, "multi_date_windows is missing or empty"
        assert len(windows) >= 1

    def test_each_window_has_required_fields(self, precommitment: dict) -> None:
        """Each window entry has the required fields for data fetching."""
        windows = precommitment.get("multi_date_windows", [])
        required = {"label", "date", "window_start_utc", "window_end_utc"}
        for i, w in enumerate(windows):
            missing = required - set(w.keys())
            assert not missing, f"Window {i} ({w.get('label', '?')}) missing: {missing}"

    def test_precommitment_has_kraken_trades_stream(self, precommitment: dict) -> None:
        """The precommitment has at least one kraken_trades stream."""
        streams = precommitment.get("data_config", {}).get("streams", [])
        kraken = [s for s in streams if s.get("source_kind") == "kraken_trades"]
        assert len(kraken) >= 1, "No kraken_trades stream in precommitment"

    def test_date_to_unix(self, repo_root: Path) -> None:
        """The date_to_unix helper produces correct Unix timestamps."""
        sys.path.insert(0, str(repo_root))
        try:
            import scripts.fetch_kraken_trades as mod  # type: ignore
            ts_20250203_1300 = mod.date_to_unix("2025-02-03", "13:00:00")
            ts_20250203_1600 = mod.date_to_unix("2025-02-03", "16:00:00")
            assert ts_20250203_1600 - ts_20250203_1300 == 10800  # 3 hours
        finally:
            sys.path.pop(0)
