# Raw CLOB Orderbook Payload Audit

Verifies that parser best-bid/ask selection and depth calculation match the raw CLOB API responses.

---

**Total payloads captured:** 36

### Tightest Spread Sample

- **Market:** `bitcoin-up-or-down-may-18-2026-12am-et`
- **Token:** `940806722378174490268490...` (yes)
- **Expiry:** 2026-05-18T05:00:00Z
- **Timestamp:** 1778905031.120885

#### Raw Bids

| Price | Size |
|-------|------|
| 0.01 | 6257 |
| 0.02 | 10 |
| 0.03 | 20000 |
| 0.1 | 3000 |
| 0.15 | 2000 |
| 0.2 | 1500 |
| 0.25 | 1240 |
| 0.3 | 1020 |
| 0.33 | 500 |
| 0.35 | 20 |
| 0.36 | 500 |
| 0.38 | 500 |
| 0.39 | 51 |
| 0.4 | 513 |
| 0.41 | 500 |
| 0.42 | 500 |
| 0.43 | 508 |
| 0.44 | 507 |
| 0.45 | 507 |
| 0.46 | 507 |
| 0.47 | 566 |
| 0.48 | 540 |
| 0.49 | 547 |

- max(bid prices) = 0.4900 ← **best bid**

#### Raw Asks

| Price | Size |
|-------|------|
| 0.99 | 6257 |
| 0.98 | 10 |
| 0.97 | 20000 |
| 0.9 | 3000 |
| 0.85 | 2000 |
| 0.8 | 1500 |
| 0.75 | 1240 |
| 0.7 | 1020 |
| 0.67 | 500 |
| 0.65 | 20 |
| 0.64 | 500 |
| 0.62 | 500 |
| 0.61 | 51 |
| 0.6 | 513 |
| 0.59 | 500 |
| 0.58 | 500 |
| 0.57 | 508 |
| 0.56 | 507 |
| 0.55 | 507 |
| 0.54 | 507 |
| 0.53 | 566 |
| 0.52 | 540 |
| 0.51 | 547 |

- min(ask prices) = 0.5100 ← **best ask**

#### Parser Selection

| Field | Value |
|-------|-------|
| best_bid | 0.4900 |
| best_ask | 0.5100 |
| spread_price_units | 0.0200 |
| spread_cents | 2.0000 |
| top_bid_size | 547.0000 |
| top_ask_size | 547.0000 |
| bid_depth_usd (best_bid * top_bid_size) | 268.03 |
| ask_depth_usd (best_ask * top_ask_size) | 278.97 |
| spread_calc (best_ask - best_bid) | 0.0200 |
| spread_self_check | PASS |

### Widest Spread Sample

- **Market:** `bitcoin-up-or-down-may-18-2026-12am-et`
- **Token:** `216780993513315885178661...` (no)
- **Expiry:** 2026-05-18T05:00:00Z
- **Timestamp:** 1778905201.0414824

#### Raw Bids

| Price | Size |
|-------|------|
| 0.01 | 6257 |
| 0.02 | 10 |
| 0.03 | 20000 |
| 0.1 | 3000 |
| 0.15 | 2000 |
| 0.2 | 1500 |
| 0.25 | 1240 |
| 0.3 | 1020 |
| 0.33 | 500 |
| 0.35 | 20 |
| 0.36 | 500 |
| 0.38 | 500 |
| 0.39 | 51 |
| 0.4 | 513 |
| 0.41 | 500 |
| 0.42 | 500 |
| 0.43 | 508 |
| 0.44 | 507 |
| 0.45 | 507 |
| 0.46 | 507 |
| 0.47 | 566 |
| 0.48 | 540 |
| 0.49 | 547 |

- max(bid prices) = 0.4900 ← **best bid**

#### Raw Asks

| Price | Size |
|-------|------|
| 0.99 | 6257 |
| 0.98 | 10 |
| 0.97 | 20000 |
| 0.9 | 3000 |
| 0.85 | 2000 |
| 0.8 | 1500 |
| 0.75 | 1240 |
| 0.7 | 1020 |
| 0.67 | 500 |
| 0.65 | 20 |
| 0.64 | 500 |
| 0.62 | 500 |
| 0.61 | 51 |
| 0.6 | 513 |
| 0.59 | 500 |
| 0.58 | 500 |
| 0.57 | 508 |
| 0.56 | 507 |
| 0.55 | 507 |
| 0.54 | 507 |
| 0.53 | 566 |
| 0.52 | 540 |
| 0.51 | 547 |

- min(ask prices) = 0.5100 ← **best ask**

#### Parser Selection

| Field | Value |
|-------|-------|
| best_bid | 0.4900 |
| best_ask | 0.5100 |
| spread_price_units | 0.0200 |
| spread_cents | 2.0000 |
| top_bid_size | 547.0000 |
| top_ask_size | 547.0000 |
| bid_depth_usd (best_bid * top_bid_size) | 268.03 |
| ask_depth_usd (best_ask * top_ask_size) | 278.97 |
| spread_calc (best_ask - best_bid) | 0.0200 |
| spread_self_check | PASS |

### Median Spread Sample

- **Market:** `bitcoin-up-or-down-may-18-2026-12am-et`
- **Token:** `940806722378174490268490...` (yes)
- **Expiry:** 2026-05-18T05:00:00Z
- **Timestamp:** 1778905121.201544

#### Raw Bids

| Price | Size |
|-------|------|
| 0.01 | 6257 |
| 0.02 | 10 |
| 0.03 | 20000 |
| 0.1 | 3000 |
| 0.15 | 2000 |
| 0.2 | 1500 |
| 0.25 | 1240 |
| 0.3 | 1020 |
| 0.33 | 500 |
| 0.35 | 20 |
| 0.36 | 500 |
| 0.38 | 500 |
| 0.39 | 51 |
| 0.4 | 513 |
| 0.41 | 500 |
| 0.42 | 500 |
| 0.43 | 508 |
| 0.44 | 507 |
| 0.45 | 507 |
| 0.46 | 507 |
| 0.47 | 566 |
| 0.48 | 540 |
| 0.49 | 547 |

- max(bid prices) = 0.4900 ← **best bid**

#### Raw Asks

| Price | Size |
|-------|------|
| 0.99 | 6257 |
| 0.98 | 10 |
| 0.97 | 20000 |
| 0.9 | 3000 |
| 0.85 | 2000 |
| 0.8 | 1500 |
| 0.75 | 1240 |
| 0.7 | 1020 |
| 0.67 | 500 |
| 0.65 | 20 |
| 0.64 | 500 |
| 0.62 | 500 |
| 0.61 | 51 |
| 0.6 | 513 |
| 0.59 | 500 |
| 0.58 | 500 |
| 0.57 | 508 |
| 0.56 | 507 |
| 0.55 | 507 |
| 0.54 | 507 |
| 0.53 | 566 |
| 0.52 | 540 |
| 0.51 | 547 |

- min(ask prices) = 0.5100 ← **best ask**

#### Parser Selection

| Field | Value |
|-------|-------|
| best_bid | 0.4900 |
| best_ask | 0.5100 |
| spread_price_units | 0.0200 |
| spread_cents | 2.0000 |
| top_bid_size | 547.0000 |
| top_ask_size | 547.0000 |
| bid_depth_usd (best_bid * top_bid_size) | 268.03 |
| ask_depth_usd (best_ask * top_ask_size) | 278.97 |
| spread_calc (best_ask - best_bid) | 0.0200 |
| spread_self_check | PASS |

### Early-Life Sample

- **Market:** `bitcoin-up-or-down-may-18-2026-12am-et`
- **Token:** `940806722378174490268490...` (yes)
- **Expiry:** 2026-05-18T05:00:00Z
- **Timestamp:** 1778905031.120885

#### Raw Bids

| Price | Size |
|-------|------|
| 0.01 | 6257 |
| 0.02 | 10 |
| 0.03 | 20000 |
| 0.1 | 3000 |
| 0.15 | 2000 |
| 0.2 | 1500 |
| 0.25 | 1240 |
| 0.3 | 1020 |
| 0.33 | 500 |
| 0.35 | 20 |
| 0.36 | 500 |
| 0.38 | 500 |
| 0.39 | 51 |
| 0.4 | 513 |
| 0.41 | 500 |
| 0.42 | 500 |
| 0.43 | 508 |
| 0.44 | 507 |
| 0.45 | 507 |
| 0.46 | 507 |
| 0.47 | 566 |
| 0.48 | 540 |
| 0.49 | 547 |

- max(bid prices) = 0.4900 ← **best bid**

#### Raw Asks

| Price | Size |
|-------|------|
| 0.99 | 6257 |
| 0.98 | 10 |
| 0.97 | 20000 |
| 0.9 | 3000 |
| 0.85 | 2000 |
| 0.8 | 1500 |
| 0.75 | 1240 |
| 0.7 | 1020 |
| 0.67 | 500 |
| 0.65 | 20 |
| 0.64 | 500 |
| 0.62 | 500 |
| 0.61 | 51 |
| 0.6 | 513 |
| 0.59 | 500 |
| 0.58 | 500 |
| 0.57 | 508 |
| 0.56 | 507 |
| 0.55 | 507 |
| 0.54 | 507 |
| 0.53 | 566 |
| 0.52 | 540 |
| 0.51 | 547 |

- min(ask prices) = 0.5100 ← **best ask**

#### Parser Selection

| Field | Value |
|-------|-------|
| best_bid | 0.4900 |
| best_ask | 0.5100 |
| spread_price_units | 0.0200 |
| spread_cents | 2.0000 |
| top_bid_size | 547.0000 |
| top_ask_size | 547.0000 |
| bid_depth_usd (best_bid * top_bid_size) | 268.03 |
| ask_depth_usd (best_ask * top_ask_size) | 278.97 |
| spread_calc (best_ask - best_bid) | 0.0200 |
| spread_self_check | PASS |

### Late-Life Sample

- **Market:** `bitcoin-up-or-down-may-18-2026-12am-et`
- **Token:** `216780993513315885178661...` (no)
- **Expiry:** 2026-05-18T05:00:00Z
- **Timestamp:** 1778905201.0414824

#### Raw Bids

| Price | Size |
|-------|------|
| 0.01 | 6257 |
| 0.02 | 10 |
| 0.03 | 20000 |
| 0.1 | 3000 |
| 0.15 | 2000 |
| 0.2 | 1500 |
| 0.25 | 1240 |
| 0.3 | 1020 |
| 0.33 | 500 |
| 0.35 | 20 |
| 0.36 | 500 |
| 0.38 | 500 |
| 0.39 | 51 |
| 0.4 | 513 |
| 0.41 | 500 |
| 0.42 | 500 |
| 0.43 | 508 |
| 0.44 | 507 |
| 0.45 | 507 |
| 0.46 | 507 |
| 0.47 | 566 |
| 0.48 | 540 |
| 0.49 | 547 |

- max(bid prices) = 0.4900 ← **best bid**

#### Raw Asks

| Price | Size |
|-------|------|
| 0.99 | 6257 |
| 0.98 | 10 |
| 0.97 | 20000 |
| 0.9 | 3000 |
| 0.85 | 2000 |
| 0.8 | 1500 |
| 0.75 | 1240 |
| 0.7 | 1020 |
| 0.67 | 500 |
| 0.65 | 20 |
| 0.64 | 500 |
| 0.62 | 500 |
| 0.61 | 51 |
| 0.6 | 513 |
| 0.59 | 500 |
| 0.58 | 500 |
| 0.57 | 508 |
| 0.56 | 507 |
| 0.55 | 507 |
| 0.54 | 507 |
| 0.53 | 566 |
| 0.52 | 540 |
| 0.51 | 547 |

- min(ask prices) = 0.5100 ← **best ask**

#### Parser Selection

| Field | Value |
|-------|-------|
| best_bid | 0.4900 |
| best_ask | 0.5100 |
| spread_price_units | 0.0200 |
| spread_cents | 2.0000 |
| top_bid_size | 547.0000 |
| top_ask_size | 547.0000 |
| bid_depth_usd (best_bid * top_bid_size) | 268.03 |
| ask_depth_usd (best_ask * top_ask_size) | 278.97 |
| spread_calc (best_ask - best_bid) | 0.0200 |
| spread_self_check | PASS |

---

*Raw payload audit — no trading signal or strategy recommendation.*
