# Polymarket Complement Arb V1

Same-condition binary YES+NO arbitrage strategy for Polymarket Global.

## Hypothesis

YES+NO pairs from the same binary condition on Polymarket Global occasionally trade at a combined price below $1.00. A patient maker-default hybrid strategy may be able to harvest this gap after all costs.

## Architecture

```
market_discovery (Gamma API) → market_filter → eligible pairs
    ↓
detector (book state + edge model) → opportunity diagnostic
    ↓
passive_fill_estimator → touch/cross analysis
    ↓
state_machine (per-condition) → hybrid maker/taker lifecycle
```

### Key Files

| File | Purpose |
|------|---------|
| `config.py` | Configuration dataclass |
| `models.py` | Data models (market, diagnostic, state, ledger) |
| `market_filter.py` | Filter Gamma markets to complementary binary pairs |
| `edge_model.py` | Gross gap, cost, net edge computation |
| `detector.py` | Opportunity detection from book state |
| `passive_fill_estimator.py` | Would-be quote tracking and touch analysis |
| `sizing.py` | Position sizing with constraints |
| `state_machine.py` | Per-condition lifecycle management |
| `strategy.py` | Nautilus Strategy class |
| `ledger.py` | Append-only JSONL ledger |
| `reports.py` | Report generation |
| `backtest_harness.py` | Thin trade-history diagnostics |
| `run_observe.py` | Observe-mode CLI runner |
| `run_backtest.py` | Backtest CLI runner |
| `run_live_guarded.py` | Live guarded runner (stub) |

## Commands

### Observe Mode

```bash
# Default 5-minute poll of up to 10 markets
uv run -m examples.strategies.polymarket_complement_arb.run_observe

# Custom duration and market limit
uv run -m examples.strategies.polymarket_complement_arb.run_observe \
    --duration 600 --max-markets 20

# Single event slug
uv run -m examples.strategies.polymarket_complement_arb.run_observe \
    --event-slug will-bitcoin-reach-200k-by-june-2026 --duration 600
```

### Backtest Mode

```bash
# Discover-only (list eligible markets)
uv run -m examples.strategies.polymarket_complement_arb.run_backtest \
    --discover-only --max-markets 5

# Full backtest diagnostics for a specific market
uv run -m examples.strategies.polymarket_complement_arb.run_backtest \
    --market-slug will-bitcoin-reach-200k-by-june-2026
```

### Tests

```bash
uv run -m pytest examples/strategies/polymarket_complement_arb/tests -v
```

## Adapter

Python `nautilus_trader.adapters.polymarket` only (no Rust adapter in this checkout).

## Limitations

See `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` for full details.

- **Depth mode**: `top_of_book_only` (V1)
- **Passive fill source**: `book_movement_only` (V1)
- **Final resolution**: `unavailable` (V1)
- **Signing latency**: Python py_clob_client_v2 ~1s
- **Backtest**: Not maker-fill proof
