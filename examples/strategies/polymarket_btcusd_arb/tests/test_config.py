from examples.strategies.polymarket_btcusd_arb.config import PolymarketArbConfig,parse_utc_ns
def test_defaults():
 c=PolymarketArbConfig(); assert c.sanitize_info and c.maker_rebates_enabled and c.fail_on_lookahead; assert c.lookback_ns_grid==(1_000_000_000,5_000_000_000,15_000_000_000,30_000_000_000,60_000_000_000); assert 'nanoseconds' in c.config_docs['lookback_ns_grid']
def test_invalid_thresholds_rejected():
 import pytest
 with pytest.raises(ValueError): PolymarketArbConfig(threshold_bps_grid=(0,))
def test_cli_date_utc(): assert parse_utc_ns('2026-04-01')==1775001600000000000
