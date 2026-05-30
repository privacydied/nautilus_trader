# HLP Backstop Archive Availability Probe — 20260524

**Run ID:** 20260526_194643_1aeaecc3
**Timestamp (UTC):** 2026-05-26T19:46:43Z
**Git SHA:** 30c5693e9bc058c80198e5af068ff3c70107df87
**Branch:** feat/hlp-backstop-absorption-phase0a-star-coverage

## S3 Bucket Summary

- **Bucket:** `hl-mainnet-node-data`
- **Prefixes available:** 6

## node_fills_by_block/hourly Archive

- **Prefix:** `s3://hl-mainnet-node-data/node_fills_by_block/hourly/`
- **Dates available:** from `20250727` to `20260526`
- **Total dates:** 304

## Probe Date Details

- **Date probed:** `20260524`
- **Files in date:** 24
- **Total compressed bytes:** 602.5 MiB
- **Over 5 GB threshold:** False

### Hourly Files

| File | Size | Last Modified |
|------|------|---------------|
| `0.lz4` | 26.7 MiB | 2026-05-24 03:02:16 |
| `1.lz4` | 27.5 MiB | 2026-05-24 04:02:52 |
| `10.lz4` | 26.0 MiB | 2026-05-24 13:02:16 |
| `11.lz4` | 21.3 MiB | 2026-05-24 14:02:54 |
| `12.lz4` | 22.2 MiB | 2026-05-24 15:03:05 |
| `13.lz4` | 26.8 MiB | 2026-05-24 16:02:42 |
| `14.lz4` | 49.9 MiB | 2026-05-24 17:02:40 |
| `15.lz4` | 26.1 MiB | 2026-05-24 18:02:18 |
| `16.lz4` | 24.7 MiB | 2026-05-24 19:02:27 |
| `17.lz4` | 21.0 MiB | 2026-05-24 20:02:30 |
| `18.lz4` | 18.6 MiB | 2026-05-24 21:02:59 |
| `19.lz4` | 19.6 MiB | 2026-05-24 22:02:31 |
| `2.lz4` | 22.3 MiB | 2026-05-24 05:02:58 |
| `20.lz4` | 20.2 MiB | 2026-05-24 23:03:01 |
| `21.lz4` | 31.6 MiB | 2026-05-25 00:02:59 |
| `22.lz4` | 38.6 MiB | 2026-05-26 01:02:15 |
| `23.lz4` | 25.9 MiB | 2026-05-26 01:02:20 |
| `3.lz4` | 27.0 MiB | 2026-05-24 06:02:25 |
| `4.lz4` | 17.6 MiB | 2026-05-24 07:03:03 |
| `5.lz4` | 18.3 MiB | 2026-05-24 08:02:36 |
| `6.lz4` | 19.4 MiB | 2026-05-24 09:03:02 |
| `7.lz4` | 21.2 MiB | 2026-05-24 10:02:55 |
| `8.lz4` | 23.7 MiB | 2026-05-24 11:02:52 |
| `9.lz4` | 26.3 MiB | 2026-05-24 12:03:03 |

## Sample Download & Schema

- **Sample file:** `s3://hl-mainnet-node-data/node_fills_by_block/hourly/20260524/0.lz4`
- **Compressed:** 26.7 MiB
- **Decompressed:** 132.1 MiB
- **Total rows in file:** 51946
- **Download time:** 5.99s
- **Decompress time:** 0.59s

## Schema Assessment

### Top-level block keys
  `block_number, block_time, events, local_time`

### Event fill detail keys
  `builder, builderFee, cloid, closedPnl, coin, crossed, deployerFee, dir, fee, feeToken, hash, oid, priorityGas, px, side, startPosition, sz, tid, time, twapId`

### Required Fields for HLP Reconstruction

- **✓** `timestamp_or_block_timestamp`
- **✓** `block_number_or_ordering_key`
- **✓** `symbol_or_coin`
- **✓** `buyer_address`
- **✓** `seller_address`
- **✓** `size`
- **✓** `side`
- **✗** `liquidation_or_backstop_marker`

- No liquidation/backstop marker found in fill data.

## Verdict

**Archive Verdict:** `NODE_FILLS_BY_BLOCK_SOURCE_AVAILABLE`
**Schema Verdict:** `NODE_FILLS_BY_BLOCK_SCHEMA_READY`

## Notes

- No liquidation/backstop flag exists in node_fills_by_block fill records.
- HLP backstop inference would require cross-referencing with
  liquidation events (misc_events_by_block) or heuristic position-change analysis.
- The fills data itself is parseable and rich (address, coin, side, size, price, direction, PnL).
- No orders, auth, trading, live, paper, shadow, bot, systemd, or registry mutation was used.

