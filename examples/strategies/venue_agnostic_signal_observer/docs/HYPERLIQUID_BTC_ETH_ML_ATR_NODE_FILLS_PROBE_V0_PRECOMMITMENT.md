# HYPERLIQUID_BTC_ETH_ML_ATR_NODE_FILLS_PROBE_V0_PRECOMMITMENT

**study_id:** `hyperliquid_btc_eth_ml_atr_node_fills_probe_v0`

**Created:** 2026-05-28

**Branch:** `feat/hyperliquid-btc-eth-ml-atr-node-fills-probe-v0`

**Starting SHA:** `bdcfc23134cdfb81014a3d60dd08952d06d57e92`

---

## Purpose

Empirically determine whether official Hyperliquid `node_fills_by_block` or `node_fills` S3 archives contain BTC/ETH fills covering the original late-2023 train period (2023-09 through 2024-12-31).

**Do not assume prefix coverage from one shallow listing.** Probe explicit old dates first.

---

## Source Priority

1. `s3://hl-mainnet-node-data/node_fills_by_block` (current fills stream, block-batched)
2. `s3://hl-mainnet-node-data/node_fills` (older fills, API-format-like)
3. `s3://hl-mainnet-node-data/node_trades` (older trades, different format) — **only for overlap validation, not as sufficient source**

**Explicitly excluded:**
- `s3://hl-mainnet-node-data/hyperliquid-archive/market_data` — this is L2/asset data, not trade/fill OHLCV
- SonarX — this task does not use SonarX
- Binance/Kraken/Coinbase proxy bars
- Third-party paid data

---

## Probe-First Rule

List exact 2023/2024/2025 date prefixes before any bulk download:

- Probe dates: `20230916`, `20231001`, `20240101`, `20240701`, `20250101`, `20250322`, `20250525`
- Test layouts:
  - `{prefix}/{YYYYMMDD}/`
  - `{prefix}/hourly/{YYYYMMDD}/`
  - `{prefix}/{YYYYMMDD}/{hour}/`
  - `{prefix}/hourly/{YYYYMMDD}/{hour}/`

---

## One-File Schema Rule

Download at most one small sample object per candidate layout before implementing parser.

---

## Overlap Validation Rule

Any fills-derived bars must be compared against existing node_trades-derived bars over the overlapping window:

- **Overlap start:** 2025-03-22 10:00 UTC
- **Overlap end:** 2025-05-25 14:00 UTC

Promotion is **blocked** if overlap OHLC does not match within frozen tolerance (1e-6).

---

## Fills/Trades Semantic Rule

Fills are not necessarily one-to-one with trades. Dedup/taker-only logic must be explicit and validated.

---

## Cost Guard

- No blind sync
- List/probe/sample first
- Default sample cap: 1 GiB
- Default execute cap: 25 GiB unless explicitly overridden in command and manifest

---

## Promotion Path

1. Successful probe → permits bar adapter build
2. Successful overlap cross-check → permits full bars build
3. Successful full bars build → permits original v0 eligibility run
4. **No live/order/bot/systemd unlock**

---

## Probe Statuses

- `NODE_FILLS_PROBE_V0_READY`
- `NODE_FILLS_PROBE_V0_FOUND_ORIGINAL_V0_COVERAGE`
- `NODE_FILLS_PROBE_V0_FOUND_PARTIAL_COVERAGE`
- `NODE_FILLS_PROBE_V0_BLOCKED_AWS_CLI_MISSING`
- `NODE_FILLS_PROBE_V0_BLOCKED_REQUESTER_PAYS`
- `NODE_FILLS_PROBE_V0_BLOCKED_S3_LIST_FAILED`
- `NODE_FILLS_PROBE_V0_BLOCKED_NO_OLD_DATE_OBJECTS`
- `NODE_FILLS_PROBE_V0_BLOCKED_SCHEMA_UNRECOGNIZED`
- `NODE_FILLS_PROBE_V0_BLOCKED_SAMPLE_DOWNLOAD_FAILED`
- `NODE_FILLS_PROBE_V0_BLOCKED_COST_OR_SIZE_CAP`
- `NODE_FILLS_PROBE_V0_ERROR_INVALID_OUTPUT`

---

## Adapter Statuses (only if implemented)

- `NODE_FILLS_BARS_V0_READY_FOR_ORIGINAL_V0`
- `NODE_FILLS_BARS_V0_READY_FOR_ALL_AVAILABLE_DIAGNOSTIC`
- `NODE_FILLS_BARS_V0_BLOCKED_OVERLAP_MISMATCH`
- `NODE_FILLS_BARS_V0_BLOCKED_DEDUP_AMBIGUOUS`
- `NODE_FILLS_BARS_V0_BLOCKED_INSUFFICIENT_COVERAGE`
- `NODE_FILLS_BARS_V0_BLOCKED_PARSE_FAILED`
- `NODE_FILLS_BARS_V0_ERROR_INVALID_OUTPUT`

---

## Manual Probe Results (2026-05-28)

**AWS CLI:** `aws-cli/2.34.32 Python/3.14.5 Linux/7.0.10-arch1-1`

**Requester-pays access:** Working

**Earliest dates discovered:**

| Prefix | Earliest Date | 2023 Coverage | 2024 Coverage |
|--------|---------------|---------------|---------------|
| `node_fills/hourly/` | 2025-05-25 | NO | NO |
| `node_fills_by_block/hourly/` | 2025-07-27 | NO | NO |
| `node_trades/hourly/` | 2025-03-22 | NO | NO |

**Verdict:** `BLOCKED_NODE_FILLS_NO_ORIGINAL_V0_COVERAGE`

**Reason:** No official Hyperliquid S3 prefix contains data prior to 2025-03-22. The original v0 train period (late-2023 through 2024-12-31) cannot be covered.

---

## Forbidden Actions

- No live trading
- No exchange orders
- No private keys
- No exchange auth
- No signing
- No Nautilus live adapter
- No systemd changes
- No bot path changes
- No strategy/model/feature/threshold/exit tuning

---

## Output Directory

`reports/hyperliquid_btc_eth_ml_atr_node_fills_probe_v0/<run_id>/`

**Required outputs:**
- `probe_results.json`
- `candidate_coverage.json`
- `sampled_schema_report.json` (if sampled)
- `summary.json`
- `summary.md`
- `manifest.json`
- `INPUTS.md`

---

## Safety Notice

**DO NOT USE FOR LIVE TRADING**

This probe is for data-prep research only. Success does not unlock:
- Live execution
- Exchange-paper trading
- Bot routing
- Order submission