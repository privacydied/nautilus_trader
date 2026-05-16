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
| `shadow_execution.py` | Observer-only paired-fill shadow validation harness |
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

## Shadow Validation Harness

The V1 shadow harness answers the real execution question: not whether complement edges appeared, but whether `paired_fill_realized_net_edge_per_share` stayed positive after executable prices, Polymarket fees, leg-risk buffer, stale-book rejection, resolution danger window, depth constraints, queue position, paired-fill timing, timeout risk, and unwind losses.

Executable complement edge means:

1. YES and NO are from the same `condition_id`, with no negRisk or cross-market pairing.
2. The edge is computed from executable top-of-book ask/bid data only; midpoint pricing is invalid.
3. `YES ask + NO ask + fee_per_share + leg_risk_buffer < 1.00` after all configured buffers.
4. Max safe paired size is non-dust and limited by the weaker leg, max order USDC, exposure caps, and depth.
5. Both legs fill within `one_leg_timeout_ms` and the net edge remains positive across the joint fill window.

Pessimistic queue-fill rule:

- A resting BUY quote fills only after observed sell-side traded volume at-or-through the quote price strictly exceeds visible depth ahead at quote time.
- Full fill requires cumulative compatible volume to exceed `depth_ahead + our_quote_size`.
- Partial fill is recorded only when cumulative compatible volume exceeds `depth_ahead` but not `depth_ahead + our_quote_size`.
- Ambiguous trade direction, missing size, ambiguous event order, missing book/trade evidence, or insufficient size defaults to no fill.
- SELL quotes mirror the rule on the ask side.

Joint two-leg edge-survival window:

- YES fill at `t1` and NO fill at `t2` are one joint event, not independent booleans.
- The harness records `first_leg_fill_ts`, `second_leg_fill_ts`, `joint_fill_latency_ms`, `edge_at_detection`, `edge_at_first_fill`, `edge_at_second_fill`, `min_edge_during_joint_window`, and `edge_survived_until_second_leg`.
- Both legs eventually becoming fillable is rejected if they do not become fillable close enough together for the edge to survive.

Timeout/unwind arithmetic is precommitted:

```text
paired_gain = paired_fill_count * avg_paired_realized_net_edge_per_share * avg_paired_size
unwind_loss = one_leg_fill_count * avg_one_leg_unwind_loss_per_share * avg_one_leg_size
net_shadow_harvest = paired_gain - unwind_loss
expected_edge_per_detected_opportunity = net_shadow_harvest / detected_opportunity_count
```

If `unwind_loss >= paired_gain`, `net_shadow_harvest <= 0`, or `expected_edge_per_detected_opportunity <= 0`, the run is rejected once data sufficiency gates are met.

Shadow reports are deterministic and write:

- `shadow_opportunities.jsonl`
- `shadow_summary.json`
- `shadow_report.md`

All summary rates include raw numerators and denominators, not percentages alone. Every rejected opportunity must carry a reason code plus `git_sha` and `config_hash` provenance.

Data sufficiency gates are split config values, not one arbitrary opportunity count:

- `min_observer_windows = 5`
- `min_detected_opportunities = 50`
- `min_pessimistic_paired_fills = 20`
- `min_same_condition_valid_opportunities = 30`
- `min_non_dust_opportunities = 30`

Allowed run verdicts only:

- `NEEDS_MORE_DATA`
- `REJECTED_FOR_CURRENT_LIVE_CONDITIONS`
- `CANDIDATE_FOR_LONGER_OBSERVATION`

`CANDIDATE_FOR_LONGER_OBSERVATION` requires pessimistic paired fills with positive realized shadow net edge, positive unwind-adjusted expected edge, repeated non-dust evidence across multiple windows, and no weakened safety guard. Optimistic-only or neutral-only fills cannot promote a candidate.

## Adapter

Python `nautilus_trader.adapters.polymarket` only (no Rust adapter in this checkout).

## Limitations

See `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` for full details.

- **Depth mode**: `top_of_book_only` (V1)
- **Passive fill source**: `book_movement_only` (V1)
- **Final resolution**: `unavailable` (V1)
- **Signing latency**: Python py_clob_client_v2 ~1s
- **Backtest**: Not maker-fill proof
