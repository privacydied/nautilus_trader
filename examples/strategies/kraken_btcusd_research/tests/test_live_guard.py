#!/usr/bin/env python3
"""
Test live guard functionality.
"""

import os
import sys
from pathlib import Path
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research.run_live_kraken_guarded import validate_live_guard


def test_guard_rejects_no_live_flag():
    """Without --live, guard returns safe result regardless of env."""
    args = Namespace(live=False)
    environ = {}
    result = validate_live_guard(args, environ)
    assert result.safe is True


def test_guard_rejects_missing_acknowledgment():
    """--live without I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes must fail."""
    args = Namespace(live=True)
    environ = {"KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": "s"}
    result = validate_live_guard(args, environ)
    assert result.safe is False
    assert "I_UNDERSTAND_THIS_CAN_LOSE_MONEY" in result.reason


def test_guard_rejects_missing_keys():
    """--live without API keys must fail."""
    args = Namespace(live=True)
    environ = {"I_UNDERSTAND_THIS_CAN_LOSE_MONEY": "yes"}
    result = validate_live_guard(args, environ)
    assert result.safe is False


def test_guard_accepts_when_all_gates_pass():
    """--live + acknowledgment + keys must succeed."""
    args = Namespace(live=True)
    environ = {
        "I_UNDERSTAND_THIS_CAN_LOSE_MONEY": "yes",
        "KRAKEN_API_KEY": "test-key",
        "KRAKEN_API_SECRET": "test-secret",
    }
    result = validate_live_guard(args, environ)
    assert result.safe is True
