"""Tests for the L2 summary coverage probe runner."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


class TestRunL2CoverageProbe:
    def test_probe_module_exists(self):
        """The probe module is importable."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
            build_arg_parser,
        )
        parser = build_arg_parser()
        assert parser is not None
