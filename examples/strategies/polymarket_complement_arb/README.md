# Polymarket Complement Arb Strategy

**Base 100-share status: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE**
**Size ladder status: SIZE_LADDER_INCONCLUSIVE**

> **This is not a rejection of complement arbitrage as a mathematical concept.**
> **This is not a candidate for live trading.**

This strategy explores same-condition complement arbitrage on Polymarket — buying the YES/UP token and NO/DOWN token of the same binary condition as maker quotes.

The falsification campaign across 7 separated crypto Up/Down windows found abundant theoretical edges (1565 detected opportunities, 1155 non-dust) but **zero pessimistic paired fills at base 100-share quote size**. The trade evidence pipeline is fully operational (READY, 0 missing events) but the queue does not clear within the observed windows at the base size.

A size-ladder diagnostic was attempted but remains inconclusive:
- Replay over original campaign artifacts was blocked — `cumulative_fillable_volume` was not stored
- A fallback fresh capture found only 6 opportunities on a single remaining market (below sufficiency gates)
- The distinction between no fills at any size and edge only at dust size remains formally open

## Failure Mode

Structural pincer at base 100-share size:
- **5m**: turnover exists but no economic depth (0 non-dust)
- **15m**: edges detected with non-dust sizes, but zero queue turnover
- **4h/daily/1h**: abundant edges with zero fill evidence

Size ladder at smaller sizes: inconclusive.

## Campaign Phases

1. **Base closeout** (6 windows): Precommitted repeated-window campaign on BTC/ETH Up/Down. Result: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE at 100 shares.
2. **Hourly addendum** (1 window): Resolved the 1h slug discovery gap. Confirmatory only.
3. **Schema check**: REPLAY_SCHEMA_INSUFFICIENT — cumulative_fillable_volume missing from artifacts.
4. **Size-ladder stub**: Fallback capture on expired-universe market — BELOW_SUFFICIENCY_GATES. Does not settle the ladder question.

## Files

- `run_shadow_observe.py` — observer-only shadow validation runner
- `run_size_ladder_diagnostic.py` — post-campaign size-ladder diagnostic runner
- `shadow_execution.py` — shadow fill/execution model (stores cumulative_fillable_volume for future replay)
- `FINAL_RESEARCH_STATUS.json` — machine-readable status with separate base/size-ladder fields
- `FINAL_RESEARCH_STATUS.md` — detailed research summary
- `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` — precommitment assumptions and gates
- `size_ladder_schema_check.json` — schema replay capability assessment
- `size_ladder_schema_check.md` — schema check explanation
- `config.py` — strategy config with diagnostic thresholds

## Safety

This module contains **no execution client imports, no credential handling, no order submission, no signing paths**. All code uses public REST endpoints only (Gamma API, CLOB API, Data API).
