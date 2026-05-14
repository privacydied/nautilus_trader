from examples.strategies.polymarket_btcusd_arb.run_backtest import validate_branch
def test_branch_helper(): assert validate_branch('polymarket-btcusd-arb-phase1') and not validate_branch('master') and not validate_branch('nightly') and not validate_branch('kraken-v7-l2-maker-paper')
