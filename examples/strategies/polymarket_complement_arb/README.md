# Polymarket Complement Arb Strategy

**Status: FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE**

> **This is not a rejection of complement arbitrage as a mathematical concept.**
> **This is not a candidate for live trading.**

This strategy explores same-condition complement arbitrage on Polymarket — buying the YES/UP token and NO/DOWN token of the same binary condition as maker quotes.

The falsification campaign across 6 separated crypto Up/Down windows found abundant theoretical edges (1541 detected opportunities, 1098 non-dust) but **zero pessimistic paired fills at base 100-share quote size**. The trade evidence pipeline is fully operational (READY, 0 missing events) but the queue does not clear within the observed windows.

## Failure Mode

Structural pincer:
- **5m**: turnover exists but no economic depth (0 non-dust)
- **15m**: edges detected with non-dust sizes, but zero queue turnover
- **4h/daily**: abundant edges with zero fill evidence

## Files

- `run_shadow_observe.py` — observer-only shadow validation runner
- `shadow_execution.py` — shadow fill/execution model
- `FINAL_RESEARCH_STATUS.json` — machine-readable status
- `FINAL_RESEARCH_STATUS.md` — detailed research summary
- `POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md` — precommitment assumptions and gates

## Safety

This module contains **no execution client imports, no credential handling, no order submission, no signing paths**. All code uses public REST endpoints only (Gamma API, CLOB API, Data API).
