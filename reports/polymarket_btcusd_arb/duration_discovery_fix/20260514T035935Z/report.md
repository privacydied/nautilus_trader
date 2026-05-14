# Polymarket BTC UpDown Duration Discovery Fix

## Phase Statement

This run corrects a duration discovery bug in the prior probe.

## Why This Run Exists

The previous duration probe (`polymarket-btc-updown-duration-spread-v1`)

incorrectly concluded that 1h and 4h BTC UpDown markets were not offered

on Polymarket. This run supersedes that conclusion with corrected

discovery logic, known slug validation, and proper separation of

product existence from active quote availability.

## Superseded Prior Conclusion

The prior claim that 1h and 4h BTC UpDown markets were not offered is

**superseded** by this run. Known user-supplied Polymarket URLs validate

that 1h/hourly and 4h BTC UpDown products exist. Product existence is

now separated from active quote availability.

## Known Slug Validation

| Slug | Product Exists | Duration | Active | Reference Source |
|------|---------------|----------|--------|-----------------|
| bitcoin-up-or-down-may-13-2026-11pm-et | True | 1h | True | BINANCE_BTCUSDT |
| btc-updown-4h-1778716800 | True | 4h | True | CHAINLINK_BTCUSD |

## Duration Discovery

| Duration | Products Found | Active Markets | Reference Sources |
|----------|---------------|----------------|------------------|
| 5m | 4 | 4 | UNKNOWN |
| 15m | 4 | 4 | UNKNOWN |
| 1h | 2 | 2 | BINANCE_BTCUSDT |
| 4h | 1 | 1 | CHAINLINK_BTCUSD |

## Active Market Availability

- **5m**: Product exists (4 found) with 4 active market(s).
- **15m**: Product exists (4 found) with 4 active market(s).
- **1h**: Product exists (2 found) with 2 active market(s).
- **4h**: Product exists (1 found) with 1 active market(s).

## Reference Sources

| Duration | Reference Source | Implication |
|----------|-----------------|-------------|
| 5m | UNKNOWN | Unknown — verify before testing |
| 5m | UNKNOWN | Unknown — verify before testing |
| 5m | UNKNOWN | Unknown — verify before testing |
| 5m | UNKNOWN | Unknown — verify before testing |
| 15m | UNKNOWN | Unknown — verify before testing |
| 15m | UNKNOWN | Unknown — verify before testing |
| 15m | UNKNOWN | Unknown — verify before testing |
| 15m | UNKNOWN | Unknown — verify before testing |
| 1h | BINANCE_BTCUSDT | Binance fair-prob model applicable |
| 1h | BINANCE_BTCUSDT | Binance fair-prob model applicable |
| 4h | CHAINLINK_BTCUSD | Needs separate reference model |

## Quote Quality

## Actionable Two-Sided Books

## Reference Source Warning

**4h product(s) use Chainlink BTC/USD as resolution source.**

The existing Binance-reference fair-probability strategy is not automatically valid for this duration without a separate Chainlink-reference Phase 1 hypothesis.

## Corrected Verdict

**Overall: DURATION_PROBE_SUPERSEDES_PRIOR_NONEXISTENCE_FINDING**

Reason: known_slugs_validate_product_existence_but_no_active_markets_observed

- **15m**: DURATION_NO_ACTIVE_MARKET_NOW
- **1h**: DURATION_NO_ACTIVE_MARKET_NOW
- **4h**: DURATION_NO_ACTIVE_MARKET_NOW
- **5m**: DURATION_NO_ACTIVE_MARKET_NOW

## What This Does and Does Not Prove

**Does prove:**

- 1h/hourly BTC UpDown products exist on Polymarket (validated via known slug).
- 4h BTC UpDown products exist on Polymarket (validated via known slug).
- Product existence and active market availability are separate concepts.
- 4h products use Chainlink BTC/USD, not Binance BTC/USDT.

**Does NOT prove:**

- That active 1h/4h markets are available at any given time.
- That 1h/4h books are actionable (requires live quote observation).
- That a Binance fair-probability strategy works for Chainlink-based products.
- That any trade should be executed.

## Next Recommendation

Insufficient data. Re-run the probe when 1h/4h markets are active.

Do not execute. Do not start Phase 3.


## Limitations

- Observer-only. No orders. No keys.
- Public API data only.
- Product existence confirmed via known slugs; active market availability is separate.
- 4h markets may use Chainlink BTC/USD, not Binance BTC/USDT.
- Single-point-in-time snapshots, not continuous book.

## Safety

- No orders: PASS
- No keys: PASS
- No execution client imports: PASS
- No on-chain calls: PASS
- Branch: polymarket-btc-updown-duration-discovery-fix-v2
