# Generic altcoin stress regime ablation — Phase 0 precommitment

## Study

Attribute the prior liquidation/OI flush aftershock reversal result to generic price-only altcoin stress regimes.

Replace the liquidation/OI detector with a dumb price-only stress detector, then reuse the same Phase 0B/0C return/null evaluation stack.

## Question

Is the previous result specific to liquidation/OI flush events, or is it mostly generic altcoin stress-regime mean reversion?

## Detector

Price-only. No OI, no liquidation, no funding inputs.

### Inputs

- `symbol`
- `ts_event` (timestamp)
- `price` (mark/mid/oracle price field)
- past-only price returns
- past-only realized volatility

### Feature computation (at timestamp t, using data strictly before t)

- `trailing_1h_return_bps`: (price_t - price_{t-1h}) / price_{t-1h} * 10000
- `trailing_6h_realized_vol_bps`: sum of absolute 1h returns over past 6 hours * 10000
- `trailing_6h_realized_vol_percentile`: rolling percentile rank of `trailing_6h_realized_vol_bps` over a 30-day lookback window (minimum 14 days of history required)

### Primary stress event fires when

- `trailing_1h_return_bps` <= -300 bps (i.e., price dropped >= 3% in the last hour)
- `trailing_6h_realized_vol_percentile` >= 0.80 (volatility in top 20%)
- timestamp has sufficient lookback (at least 14 days of history for percentile)
- forward 24h price coverage exists
- event is not within cooldown window

### Direction

- Long only (after negative stress).
- No shorts.
- BTC and ETH excluded from all event gates.

### Cooldown

- 48h per symbol (global cooldown, greedy from earliest).

### Thresholds (frozen)

- 1h return threshold: <= -300 bps
- 6h realized vol percentile threshold: >= 0.80
- Cooldown: 48h
- Minimum accepted event population: at least 300
- Minimum accepted symbols: at least 8
- Minimum symbols with at least 3 events: at least 8
- Max symbol event share: no more than 20%
- Max month event share: no more than 25%
- Max quarter event share: no more than 45%

## Phase 0B

- Same return horizons: 6h, 12h, 24h (primary), 48h.
- Same cost convention: 50 bps primary diagnostic cost, also report 75 bps and 100 bps stress.
- Compare to prior liquidation benchmark:
  - benchmark 24h net mean after 50 bps: +125.95 bps
  - benchmark 24h net median after 50 bps: +97.65 bps
  - benchmark 24h win rate after 50 bps: 0.5668

## Phase 0C

- Primary symbol-preserving timestamp placebo.
- Month-matched timestamp placebo.
- Circular-shift clustering null.
- Survivorship audit.
- 1000 iterations for full run.

## Safety

- Archive-only public data.
- No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, or bot path.
- No REJECTED_RESEARCH.md update until completed.

Precommitment SHA-256 (self): 9faf9a8e9111bf5f9a564a69980b8c26d6a0084d229546f67195c51e95b8c448
