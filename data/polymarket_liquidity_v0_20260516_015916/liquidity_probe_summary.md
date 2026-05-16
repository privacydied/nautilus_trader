# Polymarket BTC Up/Down CLOB Liquidity Probe Summary

**Run ID:** polymarket_liquidity_probe_1778896757
**Started:** 2026-05-16T01:59:17.006074+00:00
**Duration:** 600.6s (requested 600s)

---

## Markets

- Markets discovered: 6
- Markets with token IDs: 6
- Market slugs: btc-updown-15m-1778982300, btc-updown-5m-1778982300, btc-updown-5m-1778982000, btc-updown-5m-1778981700, btc-updown-15m-1778981400, btc-updown-5m-1778981400

## Samples

- Samples collected: 1440
- Valid samples: 1440

## Spread Statistics

- Median spread (cents): 98.0000
- P75 spread (cents): 98.0000
- P95 spread (cents): 98.0000
- Spread sample count: 1440

## Depth Statistics

- Median top bid depth (USD): 141.54
- Median top ask depth (USD): 14012.39

## Book Quality

- Percent two-sided: 100.0%
- Percent stale: 0.0%
- Percent crossed: 0.0%
- Percent missing: 0.0%

## Diagnostic Classification

**RED_LIQUIDITY_DIAGNOSTIC**

> Median spread > $0.06, or p95 spread > $0.10, or top-of-book depth is consistently below $100. Stop unless a human reviews and overrides.

## Verdict

> No trade candidate emitted. This is a liquidity/actionability diagnostic only. It cannot produce CANDIDATE, REJECTED, EXECUTION_READY, or TRADE_READY verdicts.

---

*This summary was produced by a liquidity/actionability probe.*
*It is not a trading signal or strategy recommendation.*
