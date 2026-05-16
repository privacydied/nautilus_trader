# Polymarket BTC Up/Down CLOB Liquidity Probe Summary

**Run ID:** polymarket_liquidity_probe_1778904641
**Started:** 2026-05-16T04:10:41.627140+00:00
**Duration:** 300.3s (requested 300s)

---

## Markets

- Markets discovered: 3
- Markets with token IDs: 3
- Market slugs: btc-updown-4h-1778990400, btc-updown-15m-1778990400, btc-updown-5m-1778990400

## Samples

- Samples collected: 360
- Valid samples: 360

## Spread Statistics

- Median spread (cents): 1.0000
- P75 spread (cents): 2.0000
- P95 spread (cents): 2.0000
- Spread sample count: 360

## Depth Statistics

- Median top bid depth (USD): 95.0900
- Median top ask depth (USD): 95.0900

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
