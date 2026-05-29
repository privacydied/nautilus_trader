"""Tests for the HIP-3 Cross-DEX No-Arb-Band Phase -1 runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


class TestNoSubprocess:
    def test_no_subprocess_import(self):
        mod_path = Path(__file__).resolve().parents[1] / "run_hip3_cross_dex_noarb_band_phase_minus1_v0.py"
        src = mod_path.read_text(encoding="utf-8")
        assert "import subprocess" not in src
        assert "subprocess." not in src
        assert "os.system(" not in src
        assert "eval(" not in src


class TestBuildArgParser:
    def test_defaults(self):
        from examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.dry_run is False
        assert args.sample_days == 30
        assert args.max_files_per_leg == 200
        assert args.primary_align_tolerance_seconds == 5.0
        assert args.fallback_one_way_hip3_taker_fee_bps == 12.5
        assert args.allow_flx_diagnostic_only is False

    def test_dry_run_flag(self):
        from examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_pairs_override(self):
        from examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        args = parser.parse_args(["--pairs", "cash:NVDA|km:NVDA"])
        assert args.pairs == "cash:NVDA|km:NVDA"
