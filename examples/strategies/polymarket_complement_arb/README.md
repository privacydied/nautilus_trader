# Polymarket Complement Arb Strategy

**Status: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE** (applies to base 100-share pessimistic maker execution)

> **This is not a rejection of complement arbitrage as a mathematical concept.**
> **This is not a candidate for live trading.**

This strategy explores same-condition complement arbitrage on Polymarket — buying the YES/UP token and NO/DOWN token of the same binary condition as maker quotes.

The falsification campaign across 7 separated crypto Up/Down windows found abundant theoretical edges (1565 detected opportunities, 1155 non-dust) but **zero pessimistic paired fills at base 100-share quote size**. A size-ladder diagnostic closeout confirmed zero fills at all tested sizes [5, 10, 25, 50, 100] with all opportunities classified as dust. The trade evidence pipeline is fully operational (READY, 0 missing events) but the queue does not clear within the observed windows.

## Failure Mode

Structural pincer:
- **5m**: turnover exists but no economic depth (0 non-dust)
- **15m**: edges detected with non-dust sizes, but zero queue turnover
- **4h/daily/1h**: abundant edges with zero fill evidence
- **All sizes [5-100]**: zero fills at any quote size

## Campaign Timeline

1. **Original closeout** (6 windows): Precommitted repeated-window campaign on BTC/ETH Up/Down. Result: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE at 100 shares.
2. **Hourly addendum** (1 window): Resolved the 1h slug discovery gap. Confirmed same zero-fill pattern.
3. **Size-ladder diagnostic closeout** (post-campaign): Schema check confirmed replay impossible (REPLAY_SCHEMA_INSUFFICIENT — cumulative_fillable_volume not stored in artifacts). Fresh bounded capture with ladder [5/10/25/50/100] confirmed zero pessimistic paired fills at all sizes.

## Files

- `run_shadow_observe.py` — observer-only shadow validation runner
- `run_size_ladder_diagnostic.py` — post-campaign size-ladder diagnostic runner
- `shadow_execution.py` — shadow fill/execution model
- `FINAL_RESEARCH_STATUS.json` — machine-readable status with campaign phases
- `FINAL_RESEARCH_STATUS.md` — detailed research summary
- `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` — precommitment assumptions and gates
- `size_ladder_schema_check.json` — schema replay capability assessment
- `size_ladder_schema_check.md` — schema check explanation
- `config.py` — strategy config with diagnostic thresholds

## Safety

This module contains **no execution client imports, no credential handling, no order submission, no signing paths**. All code uses public REST endpoints only (Gamma API, CLOB API, Data API).
