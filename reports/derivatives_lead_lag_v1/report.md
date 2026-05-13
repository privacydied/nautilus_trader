# Derivatives Lead-Lag Signal Report

> **This is an observer-only research report.**
> No orders, no execution, no private keys, no live trading.
> **This is not a trading recommendation.**

## Study Parameters

- **Source venue:** coinbase
- **Target venue:** kraken
- **Symbol:** BTC/USD
- **Signal types:** notional_burst, price_shock, signed_imbalance
- **Lookbacks (ms):** 1000, 5000, 10000, 30000
- **Horizons (ms):** 1000, 2000, 5000, 10000, 30000, 60000
- **Total cost (bps):** 24.0

## Results by Group

| Source | Target | Symbol | Signal | LB(ms) | Signals | Valid | Net(bps) | Win% | Baseline | Candidate |
|--------|--------|--------|--------|--------|---------|-------|----------|------|----------|-----------|
| coinbase | kraken | BTC/USD | notional_burst | 1000 | 23 | 130 | -18.9748 | 0.0 | -18.7641 | NO |
| coinbase | kraken | BTC/USD | notional_burst | 5000 | 27 | 149 | -18.8884 | 0.0 | -18.781 | NO |
| coinbase | kraken | BTC/USD | notional_burst | 10000 | 28 | 153 | -18.8492 | 0.0 | -18.7749 | NO |
| coinbase | kraken | BTC/USD | notional_burst | 30000 | 26 | 145 | -18.8793 | 0.0 | -18.7808 | NO |
| coinbase | kraken | BTC/USD | price_shock | 1000 | 11 | 59 | -18.9168 | 0.0 | -18.6526 | NO |
| coinbase | kraken | BTC/USD | price_shock | 5000 | 12 | 65 | -18.7401 | 0.0 | -18.6859 | NO |
| coinbase | kraken | BTC/USD | price_shock | 10000 | 12 | 65 | -18.9204 | 0.0 | -18.6859 | NO |
| coinbase | kraken | BTC/USD | price_shock | 30000 | 17 | 90 | -18.8856 | 0.0 | -18.7342 | NO |
| coinbase | kraken | BTC/USD | signed_imbalance | 1000 | 24 | 134 | -19.0198 | 0.0 | -18.7777 | NO |
| coinbase | kraken | BTC/USD | signed_imbalance | 5000 | 26 | 146 | -18.9138 | 0.0 | -18.7808 | NO |
| coinbase | kraken | BTC/USD | signed_imbalance | 10000 | 25 | 135 | -19.0043 | 0.0 | -18.7808 | NO |
| coinbase | kraken | BTC/USD | signed_imbalance | 30000 | 16 | 90 | -19.0281 | 0.0 | -18.7514 | NO |

## Final Verdict

**REJECTED**
No signal group passed the candidate gate after net-cost evaluation.
