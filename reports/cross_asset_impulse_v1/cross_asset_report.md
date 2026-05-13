# Cross-asset Spot Impulse Lead-Lag — Research Report

## Hypothesis
Large-cap spot trade-flow impulses in BTC/USD and ETH/USD may lead higher-beta spot altcoin movement on Kraken, Coinbase, and optionally Binance spot by 5s to 300s during volatile windows.
When BTC or ETH spot reprices violently, thinner high-beta spot alts may lag briefly before repricing in the same direction (positive-beta propagation).

## Safety Statement
- **No orders** submitted
- **No keys** or credentials required
- **No private endpoints** accessed
- **No derivatives execution**
- **Public spot data only**
- **Spot-only, no leverage, no margin, no perps, no futures, no CFDs, no options, no short execution**

## Source Venues / Symbols
- Venues: binance, coinbase, kraken
- Symbols: BTC/USDT, ETH/USDT, BTC/USD, ETH/USD

## Target Venues / Symbols
- Venues: kraken, coinbase
- Symbols: SOL/USD, LINK/USD, DOGE/USD

## Source→Target Matrix
| Source | Target | Pairs |
|--------|--------|-------|
| binance BTC/USDT | kraken SOL/USD | evaluated |
| binance BTC/USDT | kraken LINK/USD | evaluated |
| binance BTC/USDT | kraken DOGE/USD | evaluated |
| binance BTC/USDT | coinbase SOL/USD | evaluated |
| binance BTC/USDT | coinbase LINK/USD | evaluated |
| binance BTC/USDT | coinbase DOGE/USD | evaluated |
| binance ETH/USDT | kraken SOL/USD | evaluated |
| binance ETH/USDT | kraken LINK/USD | evaluated |
| binance ETH/USDT | kraken DOGE/USD | evaluated |
| binance ETH/USDT | coinbase SOL/USD | evaluated |
| binance ETH/USDT | coinbase LINK/USD | evaluated |
| binance ETH/USDT | coinbase DOGE/USD | evaluated |
| binance BTC/USD | kraken SOL/USD | evaluated |
| binance BTC/USD | kraken LINK/USD | evaluated |
| binance BTC/USD | kraken DOGE/USD | evaluated |
| binance BTC/USD | coinbase SOL/USD | evaluated |
| binance BTC/USD | coinbase LINK/USD | evaluated |
| binance BTC/USD | coinbase DOGE/USD | evaluated |
| binance ETH/USD | kraken SOL/USD | evaluated |
| binance ETH/USD | kraken LINK/USD | evaluated |
| binance ETH/USD | kraken DOGE/USD | evaluated |
| binance ETH/USD | coinbase SOL/USD | evaluated |
| binance ETH/USD | coinbase LINK/USD | evaluated |
| binance ETH/USD | coinbase DOGE/USD | evaluated |
| coinbase BTC/USDT | kraken SOL/USD | evaluated |
| coinbase BTC/USDT | kraken LINK/USD | evaluated |
| coinbase BTC/USDT | kraken DOGE/USD | evaluated |
| coinbase BTC/USDT | coinbase SOL/USD | evaluated |
| coinbase BTC/USDT | coinbase LINK/USD | evaluated |
| coinbase BTC/USDT | coinbase DOGE/USD | evaluated |
| coinbase ETH/USDT | kraken SOL/USD | evaluated |
| coinbase ETH/USDT | kraken LINK/USD | evaluated |
| coinbase ETH/USDT | kraken DOGE/USD | evaluated |
| coinbase ETH/USDT | coinbase SOL/USD | evaluated |
| coinbase ETH/USDT | coinbase LINK/USD | evaluated |
| coinbase ETH/USDT | coinbase DOGE/USD | evaluated |
| coinbase BTC/USD | kraken SOL/USD | evaluated |
| coinbase BTC/USD | kraken LINK/USD | evaluated |
| coinbase BTC/USD | kraken DOGE/USD | evaluated |
| coinbase BTC/USD | coinbase SOL/USD | evaluated |
| coinbase BTC/USD | coinbase LINK/USD | evaluated |
| coinbase BTC/USD | coinbase DOGE/USD | evaluated |
| coinbase ETH/USD | kraken SOL/USD | evaluated |
| coinbase ETH/USD | kraken LINK/USD | evaluated |
| coinbase ETH/USD | kraken DOGE/USD | evaluated |
| coinbase ETH/USD | coinbase SOL/USD | evaluated |
| coinbase ETH/USD | coinbase LINK/USD | evaluated |
| coinbase ETH/USD | coinbase DOGE/USD | evaluated |
| kraken BTC/USDT | kraken SOL/USD | evaluated |
| kraken BTC/USDT | kraken LINK/USD | evaluated |
| kraken BTC/USDT | kraken DOGE/USD | evaluated |
| kraken BTC/USDT | coinbase SOL/USD | evaluated |
| kraken BTC/USDT | coinbase LINK/USD | evaluated |
| kraken BTC/USDT | coinbase DOGE/USD | evaluated |
| kraken ETH/USDT | kraken SOL/USD | evaluated |
| kraken ETH/USDT | kraken LINK/USD | evaluated |
| kraken ETH/USDT | kraken DOGE/USD | evaluated |
| kraken ETH/USDT | coinbase SOL/USD | evaluated |
| kraken ETH/USDT | coinbase LINK/USD | evaluated |
| kraken ETH/USDT | coinbase DOGE/USD | evaluated |
| kraken BTC/USD | kraken SOL/USD | evaluated |
| kraken BTC/USD | kraken LINK/USD | evaluated |
| kraken BTC/USD | kraken DOGE/USD | evaluated |
| kraken BTC/USD | coinbase SOL/USD | evaluated |
| kraken BTC/USD | coinbase LINK/USD | evaluated |
| kraken BTC/USD | coinbase DOGE/USD | evaluated |
| kraken ETH/USD | kraken SOL/USD | evaluated |
| kraken ETH/USD | kraken LINK/USD | evaluated |
| kraken ETH/USD | kraken DOGE/USD | evaluated |
| kraken ETH/USD | coinbase SOL/USD | evaluated |
| kraken ETH/USD | coinbase LINK/USD | evaluated |
| kraken ETH/USD | coinbase DOGE/USD | evaluated |

## Same-Symbol Skips
- None (all evaluated cross-asset)

## Stream Subscription Health
- ⚠️ FAILED subscription: binance|DOGE/USD (venue=binance, symbol=DOGE/USD)
- ⚠️ ZERO ticks: binance|DOGE/USD after capture window
- ⚠️ FAILED subscription: binance|ETH/USD (venue=binance, symbol=ETH/USD)
- ⚠️ ZERO ticks: binance|ETH/USD after capture window
- ⚠️ FAILED subscription: binance|LINK/USD (venue=binance, symbol=LINK/USD)
- ⚠️ ZERO ticks: binance|LINK/USD after capture window
- ⚠️ FAILED subscription: binance|SOL/USD (venue=binance, symbol=SOL/USD)
- ⚠️ ZERO ticks: binance|SOL/USD after capture window
- ⚠️ FAILED subscription: kraken|BTC/USDT (venue=kraken, symbol=BTC/USDT)
- ⚠️ ZERO ticks: kraken|BTC/USDT after capture window
- ⚠️ FAILED subscription: kraken|DOGE/USD (venue=kraken, symbol=DOGE/USD)
- ⚠️ ZERO ticks: kraken|DOGE/USD after capture window

## Zero-Tick Stream Warnings
- ⚠️ `binance|DOGE/USD`: 0 ticks
- ⚠️ `binance|ETH/USD`: 0 ticks
- ⚠️ `binance|LINK/USD`: 0 ticks
- ⚠️ `binance|SOL/USD`: 0 ticks
- ⚠️ `kraken|BTC/USDT`: 0 ticks
- ⚠️ `kraken|DOGE/USD`: 0 ticks

## Overlap Windows per Pair
- **Pairs evaluated:** 72
- **Pairs with true overlap:** 50
- **Pairs with sufficient range:** 50
- **Pairs with ≥ min_events signals:** 35

## Source & Target Range (bps)
- **Source range threshold:** min=['binance', 'coinbase', 'kraken']
- **Target range threshold:** min=['kraken', 'coinbase']

## Signal Parameters
- **Signal types:** notional_burst, large_trade, signed_imbalance
- **Lookbacks (ms):** [1000, 5000, 10000, 30000]
- **Horizons (ms):** [1000, 2000, 5000, 10000, 30000, 60000, 300000]
- **Cooldown (ms):** 10000
- **Baseline window (ms):** 60000

## All-in Cost Wall
- **Fee:** 40.0 bps
- **Slippage:** 5.0 bps
- **Quote mismatch buffer:** 5.0 bps
- **All-in:** 50.0 bps

## Event Counts
- **Total signal events:** 11890
- **Long-executable signals:** 5195
- **Downside diagnostic signals:** 6695

## Best Source-Target Pair
- `coinbase:ETH/USDT->coinbase:LINK/USD`: mean net return = -43.98 bps

## Worst Source-Target Pair
- `kraken:ETH/USDT->coinbase:DOGE/USD`: mean net return = -46.70 bps

## Best Long-Executable Group
- `coinbase:BTC/USDT->kraken:SOL/USD`: mean net return = -42.56 bps

## Best Downside Diagnostic Group
- `coinbase:BTC/USDT->kraken:LINK/USD`: mean net return = -43.61 bps

## Random Baseline Comparison
- No pairs exceed baseline.

## Verdict
**REJECTED**

- Sufficient overlap, source movement, target movement, and event count exist, but signal fails after costs across all evaluated pairs.
