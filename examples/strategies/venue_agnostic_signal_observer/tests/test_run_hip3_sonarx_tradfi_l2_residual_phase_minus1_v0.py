"""Tests for the runner module of the HIP-3 SonarX TradFi L2 Residual Phase -1 scout."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.run_hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import main


class TestRunner:
    def test_runner_imports_module(self):
        """The runner module correctly imports and delegates to the probe module."""
        # The runner just imports and calls main from the probe module
        # Verify the import path works
        import importlib
        probe_mod = importlib.import_module(
            "examples.strategies.venue_agnostic_signal_observer"
            ".hip3_sonarx_tradfi_l2_residual_phase_minus1_v0"
        )
        assert hasattr(probe_mod, "build_arg_parser")
        assert hasattr(probe_mod, "run_phase_minus1")
        assert hasattr(probe_mod, "main")

    def test_arg_parser_has_new_anchor_args(self):
        """The arg parser includes the new anchor fallback arguments."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        args = parser.parse_args([
            "--anchor-source", "yahoo,stooq",
            "--anchor-cache-dir", "/tmp/test_cache",
            "--anchor-max-retries", "3",
            "--max-anchor-staleness-minutes", "30",
            "--allow-daily-stale-anchor-diagnostic",
        ])
        assert args.anchor_source == "yahoo,stooq"
        assert args.anchor_cache_dir == "/tmp/test_cache"
        assert args.anchor_max_retries == 3
        assert args.max_anchor_staleness_minutes == 30
        assert args.allow_daily_stale_anchor_diagnostic is True

    def test_arg_parser_defaults(self):
        """Default anchor arguments are correct."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.anchor_source == "yahoo"
        assert args.anchor_cache_dir is None
        assert args.anchor_max_retries == 2
        assert args.max_anchor_staleness_minutes == 15
        assert args.allow_daily_stale_anchor_diagnostic is False

    def test_dry_run_returns_status(self):
        """Dry run returns SONARX_PHASE_MINUS1_READY status."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
            build_arg_parser, run_phase_minus1,
        )
        parser = build_arg_parser()
        args = parser.parse_args(["--dry-run"])
        result = run_phase_minus1(args)
        assert result["status"] == "SONARX_PHASE_MINUS1_READY"
        assert "SONARX_PHASE_MINUS1_READY" in result["statuses"]
