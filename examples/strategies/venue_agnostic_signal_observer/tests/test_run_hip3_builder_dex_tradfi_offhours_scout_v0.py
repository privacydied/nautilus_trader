#!/usr/bin/env python3
"""Tests for HIP-3 Builder-DEX TradFi Off-Hours Scout CLI runner."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class TestRunnerCLI(unittest.TestCase):
    """Runner CLI argument parsing and dry-run behavior."""

    def test_help_works(self):
        result = subprocess.run(
            [sys.executable, "-m",
             "examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_offhours_scout_v0",
             "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        self.assertEqual(result.returncode, 0)
        self.assertIn("--out-root", result.stdout)
        self.assertIn("--seed-tickers", result.stdout)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--allow-network-public", result.stdout)
        self.assertIn("--require-sanity-seeds", result.stdout)

    def test_dry_run_no_write_registry_correction_flag(self):
        """Runner must not have --write-registry-correction flag."""
        result = subprocess.run(
            [sys.executable, "-m",
             "examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_offhours_scout_v0",
             "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        self.assertNotIn("--write-registry-correction", result.stdout)


class TestRunnerSafety(unittest.TestCase):
    """Runner safety checks."""

    def test_runner_no_registry_write_import(self):
        """Runner must not import the registry writer."""
        runner_path = Path(__file__).resolve().parent.parent / "run_hip3_builder_dex_tradfi_offhours_scout_v0.py"
        content = runner_path.read_text()
        self.assertNotIn("write_hip3_builder_dex_registry_correction", content)


class TestRunnerArgumentParsing(unittest.TestCase):
    """Parser accepts all flags."""

    def test_parser_accepts_all_flags(self):
        result = subprocess.run(
            [sys.executable, "-m",
             "examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_offhours_scout_v0",
             "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        for flag in ["--out-root", "--seed-tickers", "--start-date", "--end-date",
                      "--max-symbols", "--download-budget-bytes", "--l2-budget-bytes",
                      "--require-sanity-seeds", "--no-require-sanity-seeds",
                      "--allow-network-public", "--allow-s3-archive-read", "--dry-run"]:
            self.assertIn(flag, result.stdout, f"Flag {flag} not found in help output")


if __name__ == "__main__":
    unittest.main()
