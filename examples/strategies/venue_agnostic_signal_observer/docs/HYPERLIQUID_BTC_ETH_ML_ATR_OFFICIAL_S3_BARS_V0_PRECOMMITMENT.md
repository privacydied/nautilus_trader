# HYPERLIQUID BTC/ETH ML+ATR Official S3 Bars v0 — Precommitment

> **Project context:** This study builds on the local data-prep module at
> `feat/hyperliquid-btc-eth-ml-atr-bars-from-trades-v0` and the audited v0 scaffold.

---

## Purpose

- Fetch official Hyperliquid historical fills/trades from S3.
- Aggregate BTC/ETH to 1h OHLCV bars for ML+ATR v0.
- Produce v0-compatible bars/funding artifacts.

## Source Priority

1. `s3://hl-mainnet-node-data/node_fills_by_block` (hourly, LZ4-compressed)
2. `s3://hl-mainnet-node-data/node_fills` (hourly, LZ4-compressed)
3. `s3://hl-mainnet-node-data/node_trades` (hourly, LZ4-compressed)

- Older prefixes may have different formats — must be schema-inspected before use.
- Historical candles are not assumed to exist; bars must be derived from fills.
- Requester-pays may require AWS credentials and may incur transfer cost.

## Strict Budget Guard

- Plan-only first, no blind recursive sync.
- Default download cap: 25 GiB.
- If estimated download exceeds cap, stop before downloading.

## Scope

- BTC and ETH only
- 1h bars only
- Local artifact generation only
- No live exchange connection, no strategy retuning, no paper/live unlock

## Invalidation

- S3 schema unrecognized
- Object layout cannot be discovered
- Coverage does not reach train window
- Parsed fills cannot be mapped to BTC/ETH
- Timestamp ambiguity
- Inconsistent volume/price units
- v0 preflight rejects bars
- Data cost/size cap exceeded

## Promotion Path

- Data prep success only permits running v0 eligibility.
- v0 pass only permits paper `--once`.
- No live/order-routing/systemd/bot unlock.
