#!/usr/bin/env python3
"""Tests for HIP-3 Builder-DEX registry correction writer."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class TestRegistryWriter(unittest.TestCase):
    """Registry writer behavior."""

    def test_help_works(self):
        result = subprocess.run(
            [sys.executable, "-m",
             "examples.strategies.venue_agnostic_signal_observer.write_hip3_builder_dex_registry_correction_v0",
             "--help"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        self.assertEqual(result.returncode, 0)
        self.assertIn("--from-report", result.stdout)
        self.assertIn("--authorize", result.stdout)

    def test_writer_does_not_import_scout(self):
        """Writer must not import the scout module."""
        writer_path = Path(__file__).resolve().parent.parent / "write_hip3_builder_dex_registry_correction_v0.py"
        content = writer_path.read_text()
        self.assertNotIn("from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0",
                          content)
        self.assertNotIn("import hip3_builder_dex_tradfi_offhours_scout", content)

    def test_writer_no_discovery_logic(self):
        """Writer must not contain API/archive discovery code."""
        writer_path = Path(__file__).resolve().parent.parent / "write_hip3_builder_dex_registry_correction_v0.py"
        content = writer_path.read_text()
        self.assertNotIn("hyperliquid.xyz/info", content)
        self.assertNotIn("perpDexs", content)
        self.assertNotIn("metaAndAssetCtxs", content)

    def test_missing_report_dir_fails(self):
        result = subprocess.run(
            [sys.executable, "-m",
             "examples.strategies.venue_agnostic_signal_observer.write_hip3_builder_dex_registry_correction_v0",
             "--from-report", "/nonexistent/path"],
            capture_output=True, text=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
        self.assertNotEqual(result.returncode, 0)


class TestRegistryWriterDryRun(unittest.TestCase):
    """Without --authorize, writer previews only."""

    def test_no_authorize_previews_only(self):
        # Create a fake report dir
        import tempfile, shutil
        tmpdir = Path(tempfile.mkdtemp())
        try:
            (tmpdir / "corrective_registry_note_preview.md").write_text("# Test preview")
            (tmpdir / "gate_decisions.json").write_text(json.dumps({"final_status": "TEST"}))
            result = subprocess.run(
                [sys.executable, "-m",
                 "examples.strategies.venue_agnostic_signal_observer.write_hip3_builder_dex_registry_correction_v0",
                 "--from-report", str(tmpdir)],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent))
            self.assertEqual(result.returncode, 0)
            self.assertIn("DRY RUN", result.stdout)
            self.assertIn("--authorize not set", result.stdout)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
