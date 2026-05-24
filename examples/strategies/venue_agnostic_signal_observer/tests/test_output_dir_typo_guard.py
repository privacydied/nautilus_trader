import pytest
from examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_asset_ctxs_archive import DEFAULT_OUTPUT_DIR

def test_default_output_dir_spelling():
    """The default output directory must contain the correctly spelled 'hyperliquid'."""
    assert "hyperliquid" in str(DEFAULT_OUTPUT_DIR), f"Default output dir misspelled: {DEFAULT_OUTPUT_DIR}"
