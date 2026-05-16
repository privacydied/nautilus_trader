# Size Ladder Schema Check — Polymarket Complement Arb

**Verdict: REPLAY_SCHEMA_INSUFFICIENT**

Replay of the size ladder from existing artifacts is arithmetically impossible.
A fresh bounded diagnostic capture is required.

---

## What was checked

All `shadow_opportunities.jsonl` from the existing campaign were inspected:

| Run | Pessimistic rows | Pre-rejected | Post-fill-evaluated |
|---|---|---|---|
| crypto_updown_shadow_run2 | 241 | 241 (NO_NET_EDGE_AFTER_COSTS) | 0 |
| falsification_run1 (4h+daily) | 491 | 491 (NO_NET_EDGE_AFTER_COSTS) | 0 |
| btc15m_run1 | 9 | 9 (RESOLUTION_DANGER_WINDOW) | 0 |
| btc15m_closeout | 35 | 35 (RESOLUTION_DANGER_WINDOW) | 0 |
| hourly_addendum | 24 | 24 (RESOLUTION_DANGER_WINDOW) | 0 |
| **Total** | **800** | **800 (100%)** | **0** |

## Why replay is impossible

### 1. Missing cumulative_fillable_volume

The pessimistic fill rule is:

```
cumulative_fillable_volume > depth_ahead + quote_size → FULL fill
cumulative_fillable_volume > depth_ahead             → PARTIAL fill
```

The `shadow_opportunities.jsonl` stores:
- `yes_depth_ahead`, `no_depth_ahead` ✓
- `yes_fill_status`, `no_fill_status` (already-computed outcome) ✓

But it does NOT store:
- `cumulative_fillable_volume` ✗
This quantity is computed inside `evaluate_pessimistic_fill()` from raw trade events and
is written to `LegFillResult.cumulative_fillable_volume`, but `shadow_result_to_row()`
only emits the `status` field (no_fill/partial/full), not the cumulative volume.

Without cumulative_fillable_volume, the threshold comparison `cumulative > depth + size`
cannot be recomputed at an arbitrary ladder size.

### 2. All rows pre-rejected by size-invariant gates

Every single pessimistic row was pre-rejected by:

- **NO_NET_EDGE_AFTER_COSTS**: `sum_asks >= 1.0` or `net_edge_per_share <= 0`
  --- Neither depends on quote size. These opportunities would be rejected at 5, 10, 25,
  50, or 100 shares identically.

- **RESOLUTION_DANGER_WINDOW**: Market too close to resolution.
  --- Also invariant to quote size.

### 3. No raw trade data persisted

The `_market_trades()` output (list of `MarketTrade` objects) used by
`evaluate_pessimistic_fill` is ephemeral --- trades are accumulated in-memory
during the observation window and not written to any persistent artifact.

### Fields present vs. missing

| Field | Present? | Notes |
|---|---|---|
| depth_ahead per leg | YES | In JSONL as yes_depth_ahead / no_depth_ahead |
| fill status | YES | As yes_fill_status / no_fill_status (full/partial/no_fill) |
| cumulative_fillable_volume | **NO** | **Critical missing field** |
| raw trade events (time-series) | **NO** | Ephemeral in-memory only |
| quote_side | **NO** | Implicitly BUY for complement arb |
| quote_size (max_safe_shares) | YES | The actual size used in the LegQuote |
| quote_price (best_bid) | YES | Inferable from best_bid for BUY quotes |
| net_edge_per_share | YES | In JSONL |
| sum_asks | YES | In JSONL |

## What this means

Replay is not a viable path. A fresh bounded diagnostic capture is required.

The fresh capture must:
1. Observe live CLOB books and trades for a bounded time window
2. For each opportunity, store `cumulative_fillable_volume` per leg alongside `depth_ahead`
3. Compute fill outcomes at each ladder size [5, 10, 25, 50, 100] using the stored evidence
4. Apply dust/economic threshold classification before result interpretation

This is a diagnostic closeout, not a new strategy search or live trading promotion.
