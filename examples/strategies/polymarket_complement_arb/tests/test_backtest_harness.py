"""
Tests for the backtest harness.
"""

import pytest

from examples.strategies.polymarket_complement_arb.backtest_harness import (
    compute_config_hash,
    generate_run_id,
)
from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig


def test_backtest_harness_imports():
    """Just verify imports work."""
    from examples.strategies.polymarket_complement_arb.backtest_harness import (
        run_backtest_diagnostics,
    )
    assert callable(run_backtest_diagnostics)


def test_backtest_diagnostics_config_hash():
    config = ComplementArbConfig(mode="backtest")
    h = compute_config_hash(config)
    assert len(h) == 16
