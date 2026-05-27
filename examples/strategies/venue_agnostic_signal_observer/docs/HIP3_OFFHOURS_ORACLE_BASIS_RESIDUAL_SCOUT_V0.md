# HIP-3 Off-Hours Oracle Basis Residual Scout v0

**Study ID:** `hip3_offhours_oracle_basis_residual_scout_v0`

**Phase:** -1 (Feasibility Scout)

**Version:** 0

---

## What This Is NOT

- This is **not** a strategy.
- This is **not** a precommitment.
- This is **not** a PnL evaluator.
- This **cannot** emit candidate/promotion/live/paper verdicts.
- This **cannot** mutate `REJECTED_RESEARCH.md`.
- This **does not** authorize Phase 0 execution.
- This **only** decides whether a later Phase 0 precommitment is worth drafting.

## What This Is

A Phase -1 feasibility scout for HIP-3 builder-deployed equity/index-like perps
on Hyperliquid. It answers whether HIP-3 perps are sufficiently discoverable,
archived, fee-measurable, oracle-classifiable, liquid, and basis-dislocated
during U.S. cash-market closed hours to justify a later frozen Phase 0
precommitment.

**This scout does not answer whether the idea is profitable.**

## Core Question

> Are HIP-3 builder-deployed equity/index-like perps on Hyperliquid
> sufficiently discoverable, archived, fee-measurable, oracle-classifiable,
> liquid, and basis-dislocated during U.S. cash-market closed hours to justify
> a later frozen Phase 0 precommitment?

## Hypothesis Family Under Inspection

HIP-3 equity/index-like perps may exhibit off-hours residual basis dislocations
after adjusting for a fair-value anchor. These residuals might later justify a
mean-reversion study, but only if:

1. HIP-3 symbols are discoverable.
2. Archive coverage exists.
3. Oracle behavior is not already tightly fair-value tracking.
4. Fees are not too high relative to the residual tail.
5. L2 executable liquidity exists.
6. Off-hours residual basis tails exist.

## Six-Gate Kill-Chain

Canonical ordering enforced in code:

1. **Symbol discovery** — public Hyperliquid info endpoint
2. **Archive coverage** — S3 archive namespace/coverage
3. **Oracle / fair-value classification** — anchor alignment (cheap killer)
4. **Fee discovery** — public metadata / conservative estimates
5. **L2 liquidity / depth / spread** — bounded archive L2 samples
6. **Off-hours residual basis-tail** — executable mid vs anchor

### Short-Circuit Rule

Each gate failure stops the run immediately and writes only:
- Artifacts produced up to that point
- `summary.json`
- `summary.md`
- `run_manifest.json`

For example, if `HIP3_ORACLE_ALREADY_FAIR_VALUE_TRACKING` fires, the run must
not proceed to fee discovery, liquidity diagnostics, or tail-existence work.
In that case, fee discovery, liquidity diagnostics, and basis tail diagnostics
must be absent from the output, and byte counts for skipped downstream steps
must be zero.

### Oracle Reverse-Engineering (Cheap Killer)

The oracle reverse-engineering step runs immediately after archive coverage.
If the oracle already tracks ES/NQ/SPX-style fair value tightly during closed
cash hours, the family is killed cheaply before building anything else.

## Anchor Sources

| Source | Description | Default | Silent Fallback |
|--------|-------------|---------|-----------------|
| `cme_futures_proxy` | CME ES/NQ continuous futures proxy (public, no auth) | Yes | No — fails with `HIP3_ANCHOR_DATA_UNAVAILABLE` |
| `cash_eod_only` | Public end-of-day cash index close carried forward | No | No — must be explicitly selected |
| `none` | No anchor; skip classification | No | No — emits `HIP3_ANCHOR_DATA_UNAVAILABLE` |

### Anchor Timestamp Alignment

When computing `residual_basis_bps = executable_mid_bps - selected_anchor_bps`:

- Uses the last observed anchor value **at or before** the executable-mid timestamp.
- Does not forward-fill across more than **4 hours**.
- If the most recent anchor value is older than 4 hours, the sample is dropped
  with reason `ANCHOR_STALE`.

### Kill Rule

If the selected 24/7 fair-value anchor explains off-hours mark/oracle tightly
enough that residual p90 is below 20 bps and p95 below 30 bps, emit:
`HIP3_ORACLE_ALREADY_FAIR_VALUE_TRACKING`

## US Cash Session Classification

Uses a static in-repo NYSE holiday sidecar. No external calendar dependency.

| Bucket | Description |
|--------|-------------|
| `cash_session` | 09:30–16:00 ET (or 09:30–13:00 ET on early-close days) |
| `extended_hours_us` | 16:00–20:00 ET and 04:00–09:30 ET weekdays |
| `overnight_us` | 20:00 ET–04:00 ET weekdays |
| `weekend` | Friday 16:00 ET through Monday 09:30 ET, plus holidays |

All three off-hours sub-buckets are reported separately.

## Fee Discovery

Fees are discovered from public metadata. If exact fees cannot be determined:

- Conservative assumptions: 25 bps, 50 bps, 100 bps round trip
- Scout pass cannot rely on sub-10 bps round-trip assumptions
- Fees are reported by deployer namespace

### Kill Rule

If conservative estimated round-trip cost >= p90 executable residual basis tail,
emit: `HIP3_FEES_TOO_HIGH_FOR_TAIL`

## L2 Liquidity

L2 sampling strategy: `uniform_first_snapshot_per_hour`

### Executable Depth Methodology

Walk the order book from best bid/ask outward, accumulating until the target
USD notional is filled. Report achieved price-impact bps. Do **not** use
mid-quote multiplied by top-of-book size as a depth proxy.

### Pass Gate

- Median executable spread <= 15 bps during off-hours
- p90 executable spread <= 30 bps during off-hours
- At least $1,000 executable notional in >= 80% of candidate off-hours samples
- No severe missing-book issue

## Residual Basis-Tail Existence

Uses executable L2 mid (not oracle/mark) for basis computation.

`executable_mid = (best_bid + best_ask) / 2`

`residual_basis_bps = executable_mid_bps - selected_anchor_bps`

### Pass Gate

- p90 absolute executable residual basis >= 30 bps
- p90 absolute executable residual basis >= conservative round-trip cost + 10 bps margin
- At least 50 off-hours residual-tail observations >= 30 bps for at least one eligible
  index-like symbol
- No single calendar week contributes > 30% of tail events
- No single month contributes > 50% of tail events

The +10 bps margin is not a profitability claim. It is only a Phase -1 "is
there enough above-cost residual to justify designing a proper study?" screen.

## Network Chokepoint

All network calls flow through a single chokepoint in the scout module. The
chokepoint is the only call site in new code that performs operations via
`urllib`, `requests`, `httpx`, `boto3`, or `aiohttp`.

### Rules

- Public Hyperliquid endpoint calls require `--allow-network-public`
- S3/archive reads require `--allow-s3-archive-read`
- Requester-pays acknowledgement is implied by `--allow-s3-archive-read`
- No `--confirm-requester-pays` CLI flag exists

### Download Budget

- Hard cap: 5 GB total per run
- Per-symbol L2 budget: 500 MB
- Exceeding either emits `HIP3_SCOUT_ERROR` with reason `DOWNLOAD_BUDGET_EXCEEDED`

## Allowed Statuses

| Status | Meaning |
|--------|---------|
| `HIP3_SCOUT_READY` | Gate passed, continuing |
| `HIP3_SYMBOL_DISCOVERY_FAILED` | No symbols discovered |
| `HIP3_NO_EQUITY_OR_INDEX_LIKE_SYMBOLS` | No index/equity symbols |
| `HIP3_ARCHIVE_NAMESPACE_MISSING` | Archive path does not exist |
| `HIP3_ARCHIVE_COVERAGE_INSUFFICIENT` | Insufficient coverage |
| `HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED` | Helper can't be reused |
| `HIP3_SCHEMA_UNUSABLE` | Schema cannot be parsed |
| `HIP3_ANCHOR_DATA_UNAVAILABLE` | Fair-value anchor unavailable |
| `HIP3_ORACLE_ALREADY_FAIR_VALUE_TRACKING` | Oracle already tracks anchor |
| `HIP3_ORACLE_CLASSIFICATION_INCONCLUSIVE` | Classification unclear |
| `HIP3_FEE_DISCOVERY_FAILED` | Cannot determine fees |
| `HIP3_FEES_TOO_HIGH_FOR_TAIL` | Fees exceed residual tail |
| `HIP3_L2_LIQUIDITY_TOO_THIN` | Insufficient liquidity |
| `HIP3_NO_OFFHOURS_RESIDUAL_BASIS_TAIL` | No tail exists |
| `HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED` | Scout passed; Phase 0 drafting permitted |
| `HIP3_SCOUT_ERROR` | Runtime error |

## Forbidden Statuses

`REJECTED`, `CANDIDATE`, `CANDIDATE_FOR_LONGER_OBSERVATION`,
`CANDIDATE_FOR_LIVE`, `EXECUTION_READY`, `TRADE_READY`, `LIVE_READY`,
`PAPER_STRATEGY_PROMOTED`, `PROMOTION_AUTHORIZED`, `EDGE_CONFIRMED`,
`PROFITABLE`, `ALPHA_FOUND`, `READY_FOR_PHASE_0`

## Usage

```bash
# Full run (requires network + S3 access)
uv run python run_hip3_offhours_oracle_basis_residual_scout_v0.py \
    --allow-network-public \
    --allow-s3-archive-read

# Dry-run (no network)
uv run python run_hip3_offhours_oracle_basis_residual_scout_v0.py \
    --dry-run

# With specific dates and anchor
uv run python run_hip3_offhours_oracle_basis_residual_scout_v0.py \
    --start-date 2025-10-13 \
    --end-date 2026-05-27 \
    --anchor-source cme_futures_proxy \
    --allow-network-public

# Skip L2 download (oracle classification only)
uv run python run_hip3_offhours_oracle_basis_residual_scout_v0.py \
    --skip-l2-download \
    --allow-network-public --allow-s3-archive-read
```

## CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--out-root` | `reports/hip3_offhours_oracle_basis_residual_scout_v0` | Output root |
| `--start-date` | `2025-10-13` | Start date |
| `--end-date` | None | End date (today UTC) |
| `--max-symbols` | 5 | Max symbols for deep analysis |
| `--prefer-index-like` | True | Prefer index-like symbols |
| `--sample-l2-days` | 30 | Days to sample L2 |
| `--max-l2-hours-per-symbol` | 200 | Max L2 hours per symbol |
| `--skip-l2-download` | False | Skip L2 download |
| `--dry-run` | False | No-network dry run |
| `--allow-network-public` | False | Allow public Hyperliquid calls |
| `--allow-s3-archive-read` | False | Allow S3/archive reads |
| `--anchor-source` | `cme_futures_proxy` | `cme_futures_proxy`, `cash_eod_only`, `none` |
| `--min-coverage-days` | 90 | Min archive coverage days |
| `--min-tail-events` | 50 | Min tail events for pass |
| `--download-budget-bytes` | 5 GB | Total download budget |
| `--per-symbol-l2-budget-bytes` | 500 MB | Per-symbol L2 budget |

## Non-Goals

- No private keys or API keys
- No order placement
- No exchange auth
- No account access
- No execution clients
- No live trading
- No paper trading
- No shadow execution
- No bot path changes
- No systemd watcher changes
- No auto-paper promotion
- No conductor promotion
- No `REJECTED_RESEARCH.md` mutation

## Symbol Classification

HIP-3 symbols are classified using longest-match ticker patterns plus
deployer-namespace context:

- `index_like`: SPX, S&P 500, US500, NDX, NASDAQ, NAS100, QQQ, DOW, DJI, DJIA
- `single_stock_like`: AAPL, MSFT, NVDA, TSLA, AMZN, GOOG, META
- `commodity_like`: GOLD, XAU, SILVER, XAG, OIL, WTI, BRENT, NATGAS
- `crypto_like`: BTC, ETH, SOL, DOGE, XRP, ADA, etc.
- `unknown`: No pattern matched

**Do not** hardcode a fixed universe as truth. These are discovery hints only.

## Idempotence

Two consecutive runs with identical args and same archive content must produce
identical artifact content hashes except for:
- `run_id`
- `created_at_utc`
- `git_dirty` if it varies

Deterministic config hashing uses stable key ordering, stable symbol ordering,
stable date ordering, and stable anchor selection.

## Helper Provenance

Every imported function or module from outside the scout files is recorded in
`run_manifest.json` under `helpers_reused` with:
- Full dotted path
- Source file path
- Content SHA256 at run time
- Parent repo git SHA (implicit from run context)

No `subprocess` is used for provenance collection — pure Python file reads only.

## Output Artifacts

### Full Run (by gate reached)
- `summary.json`
- `summary.md`
- `run_manifest.json`
- `symbol_discovery.json`
- `archive_coverage.json`
- `oracle_classification.json`
- `fee_discovery.json`
- `liquidity_diagnostics.json`
- `basis_tail_diagnostics.json`

### Dry Run
- `dry_run_preview.json` only

Every JSON artifact includes: `study_id`, `run_id`, `created_at_utc`, `git_sha`,
`git_dirty`, `repo_root`, `command_args`, `status`, `safety_mode`,
`schema_version`, `bytes_downloaded_total`, `bytes_downloaded_per_symbol`,
`config_hash`.

## runs_required_before_phase0_drafting

If the scout passes once: one scout pass is not enough to authorize immediate
Phase 0 execution. A second independent run on a later date is recommended
before drafting Phase 0 to reduce single-snapshot artifact risk.

If the scout fails: no Phase 0 drafting; the gate failure must be resolved or
reopened separately.

## closes_what

A pass here closes nothing. A fail here closes only the specific gate that
failed, not the HIP-3 family.

## does_not_close_what

- HIP-3 symbol family viability for later studies
- Alternative oracle/execution approaches
- Different anchor sources
- Different fee regimes
- Future HIP-3 symbol additions

## Dependencies

- Python stdlib only (json, datetime, hashlib, logging, urllib.request)
- `aws` CLI for S3 operations (runtime only, not import)
- No external data dependencies
- No authenticated APIs
- No paid data sources

## File List

- `hip3_offhours_oracle_basis_residual_scout_v0.py` — Scout module
- `run_hip3_offhours_oracle_basis_residual_scout_v0.py` — CLI runner
- `tests/test_hip3_offhours_oracle_basis_residual_scout_v0.py` — Logic/unit tests
- `tests/test_run_hip3_offhours_oracle_basis_residual_scout_v0.py` — CLI/runtime tests
- `data/nyse_holidays_2024_2026.json` — NYSE holiday sidecar (static, manual refresh)

## License

This document is part of the NautilusTrader research scaffold.
No warranties. No trading advice. Research purposes only.