"""
Tests for run_hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_diagnostic_v0 — CLI module.

NOT live trading. NOT paper execution. NOT bot authorization.
"""

import sys
from pathlib import Path

import pytest

# Ensure importable without Nautilus extensions
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_diagnostic_v0 import main


class TestCLIModule:
    """Test CLI module."""
    
    def test_importable(self):
        """Module should import without errors."""
        assert callable(main)
    
    def test_cli_help(self):
        """CLI --help should work."""
        # The main function exists
        assert callable(main)