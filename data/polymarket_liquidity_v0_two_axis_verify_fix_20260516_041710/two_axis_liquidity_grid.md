# Two-Axis Liquidity Grid

Rows: time-to-expiry. Columns: distance-to-strike.

| TTE \ Distance | lte_5_bps | 5_to_10_bps | 10_to_25_bps | 25_to_50_bps | gt_50_bps | unknown |
|---|---|---|---|---|---|---|
| tte_gt_15m | - | - | - | - | - | **36** |
| tte_5m_to_15m | - | - | - | - | - | **0** |
| tte_2m_to_5m | - | - | - | - | - | **0** |
| tte_1m_to_2m | - | - | - | - | - | **0** |
| tte_30s_to_1m | - | - | - | - | - | **0** |
| tte_0s_to_30s | - | - | - | - | - | **0** |
| tte_expired | - | - | - | - | - | **0** |
| tte_unknown | - | - | - | - | - | **0** |

## Convex Danger Zone Cell

**Definition:** tte <= 120s AND distance <= 10 bps

| Metric | Value |
|--------|-------|
| Sample count | 0 |
| Median spread (cents) | N/A |
| Classification | no_data |

---
*Two-axis liquidity grid — observer-only, no trading signal.*
