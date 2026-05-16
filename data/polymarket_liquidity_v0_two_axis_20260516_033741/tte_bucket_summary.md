# Time-to-Expiry Bucket Summary

Liquidity breakdown by time-to-expiry for BTC Up/Down binary markets.

---

## Bucket Definitions

| Bucket | TTE Range |
|--------|-----------|
| tte_gt_15m | > 15 minutes |
| tte_5m_to_15m | 5 to 15 minutes |
| tte_2m_to_5m | 2 to 5 minutes |
| tte_1m_to_2m | 1 to 2 minutes |
| tte_30s_to_1m | 30 seconds to 1 minute |
| tte_0s_to_30s | 0 to 30 seconds |
| tte_expired | Expired |
| tte_unknown | Unknown/no expiry |

---

## tte_gt_15m

| Metric | Value |
|--------|-------|
| Sample count | 3600 |
| Valid samples | 3600 |
| Two-sided rate | 100.0% |
| Median spread (cents) | 1.0000 |
| P75 spread (cents) | 1.0000 |
| P95 spread (cents) | 2.0000 |
| Median bid depth (USD) | 134.15 |
| Median ask depth (USD) | 139.63 |
| Combined top depth (USD) | 134.15 |
| Stale rate | 0.0% |
| Crossed rate | 0.0% |
| Missing rate | 0.0% |
| Bucket classification | GREEN_LIQUIDITY_DIAGNOSTIC |

---

## Near-Expiry Rollup

**Definition:** tte <= 120s

| Metric | Value |
|--------|-------|
| Sample count | 0 |
| Valid samples | 0 |
| Median spread (cents) | N/A |
| P95 spread (cents) | N/A |
| Median bid depth (USD) | N/A |
| Median ask depth (USD) | N/A |
| Near-expiry classification | NEEDS_MORE_DATA |

---

*TTE bucket analysis — observer-only, no trading signal.*
