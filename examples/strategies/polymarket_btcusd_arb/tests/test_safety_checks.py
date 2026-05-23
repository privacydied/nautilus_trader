from pathlib import Path
from examples.strategies.polymarket_btcusd_arb.safety_checks import check_path
def test_package_observer_only():
 r=check_path(Path('examples/strategies/polymarket_btcusd_arb')); assert r['ok'], r
