# HIP-3 Builder-DEX TradFi Off-Hours Oracle-Basis Phase -1 Scout v0

## What this is NOT

- Not a strategy implementation
- Not a PnL evaluator
- Not a return backtest
- Not a Phase 0 precommitment
- Not a paper-trading unlock
- Not a live-trading unlock
- Not a conductor promotion
- Not a registry rejection
- Not a profitability claim

## Why this scout exists and what the prior scouts got wrong

Prior HIP-3 builder discovery work queried only the default validator-operated perp DEX
universe via `metaAndAssetCtxs` **without** the `dex` parameter. This concluded that no
confirmed builder-deployed equity/index/commodity symbols were publicly discoverable.

**That was incomplete.**

Hyperliquid builder-deployed HIP-3 perps live under separate perp DEX namespaces. These
are accessible via:

1. The `perpDexs` endpoint, which returns all DEX namespaces (default + builder).
2. The `meta` and `metaAndAssetCtxs` endpoints with the `dex` parameter for each builder DEX.

The frontend URL `https://app.hyperliquid.xyz/trade/TSLA/USDC` is the five-second sanity
check that should have happened first. Any future scout in this family must run the
frontend/API consistency check before any gate work.

> **"The previous default-dex-only discovery result is not evidence that HIP-3
> builder-deployed TradFi markets are absent."**

> **"Builder DEX enumeration via `perpDexs` is required before drawing any
> public-discovery conclusion."**

> **"Frontend trade URLs are used only as sanity seeds; the scout must verify
> symbols through public API/archive data."**

## Corrected public API discovery method

1. Query `{"type": "perpDexs"}` to discover all DEX namespaces.
2. Classify each entry: default (null name) vs builder (named).
3. For each builder DEX, query:
   - `{"type": "meta", "dex": "<dex_name>"}` for universe metadata
   - `{"type": "metaAndAssetCtxs", "dex": "<dex_name>"}` for universe + contexts
4. Query `{"type": "metaAndAssetCtxs"}` (no dex param) as baseline comparison only.

## Builder DEX namespace explanation

- Default validator DEX has `null` or empty name in `perpDexs`.
- Builder DEXs have explicit names (e.g., `xyz`).
- Each builder DEX has its own universe of perp markets.
- Builder asset ID formula: `100000 + perp_dex_index * 10000 + index_in_meta`

## Seed tickers

Primary mandatory: TSLA, AAPL, MSFT, NVDA
Extended: AMZN, GOOG, GOOGL, META
Index/ETF: SPX, NDX, NAS100, QQQ
Commodity: GOLD, XAU, SILVER, XAG, OIL, WTI, BRENT

## Frontend/API consistency check

For each mandatory seed (TSLA/AAPL/MSFT/NVDA):
- HEAD request to `https://app.hyperliquid.xyz/trade/<TICKER>/USDC`
- Check if ticker resolves under any builder DEX `meta`/`metaAndAssetCtxs`
- Record match status: consistent, frontend_only, api_only, neither
- Any `frontend_only` triggers `HIP3_FRONTEND_API_DESYNC`
- Zero resolved + frontend non-404 triggers `HIP3_SCOUT_ERROR`

## Gate DAG

| Phase | Name | Pass condition | Block condition |
|-------|------|---------------|-----------------|
| A | Corrected builder-DEX discovery | >=1 builder DEX found | perpDexs fails or no builder DEXs |
| A2 | Frontend/API consistency | Seeds resolve via API | frontend_only desync or zero resolution |
| B | Target symbol resolution | >=1 TradFi candidate | No TradFi symbols found |
| C | Archive/L2 visibility | Archive data found for >=1 | No archive paths found |
| D | Fee reality | Conservative cost bounded | Fee regime unknowable |
| E | Oracle/anchor classification | Anchor supports measurement | No anchor available |
| F | L2 depth/spread feasibility | Enough off-hours depth | Liquidity too thin |
| G | Off-hours residual basis-tail | >=200 samples + tail exists | <200 samples = INCONCLUSIVE |
| H | Corrective registry note preview | Preview generated | N/A |
| I | Final gate verdict | Composite of above | Composite of above |

## Status taxonomy

See the module's `DiscoveryStatus`, `ArchiveStatus`, `FeeLiqOracleStatus`,
`BasisTailStatus`, and `FinalGateStatus` enums for the complete taxonomy.

## Sample-size honesty rules

- Fewer than 100 aligned off-hours samples: status must be `HIP3_OFFHOURS_BASIS_TAIL_INCONCLUSIVE`
- Fewer than 200 aligned off-hours samples: cannot emit EXISTS or NO_TAIL
- >= 200 samples required for EXISTS/NO_TAIL verdicts
- INCONCLUSIVE is the strongest possible status under 200 samples

## Two-run Phase 0 warrant rule

`HIP3_PHASE0_PRECOMMITMENT_WARRANTED` requires:
- >= 200 aligned off-hours samples in this run
- AND a `prior_independent_run_evidence.json` showing a second independent scout pass
- Single-run warrant is forbidden
- Even with warrant, precommitment must be drafted in a separate explicitly authorized run

## Safety constraints

- Public data only
- No orders, no private keys, no exchange auth, no wallet/account endpoints
- No signing, no live execution, no paper broker
- No strategy evaluator, no position sizing, no PnL, no return backtest
- No phase promotion, no registry mutation during the scout run
- No `subprocess`, `os.system`, or `eval` in production code
- No AWS CLI shelling
- All S3/archive reads through scout-owned chokepoint
- All public HTTP reads through scout-owned chokepoint
- Hyperliquid info endpoint never used for account-state request types

## Closes / does not close

**Closes:**
- Prior default-dex-only discovery conclusion
- Prior `asset_ctxs` universe-dex route as sole public discovery source

**Does not close:**
- HIP-3 builder markets generally
- TradeXYZ or other deployer-specific markets
- Off-hours basis on confirmed builder-deployed TradFi perps
- Future investigation using official HIP-3 metadata or deployer docs

## Reopen conditions

- New builder DEX discovered via `perpDexs`
- Official HIP-3 deployer documentation becomes public
- Reliable no-auth archive path discovered for builder DEX symbols
- Extended-hours public equity anchor becomes available

## Next allowed step

After a successful Phase -1 scout:
1. Review the corrective registry note preview
2. If approved, run the registry correction writer separately
3. If TradFi symbols are confirmed, proceed to Phase 0 with archive/anchor resolution

## Required wording

- "A pass here does not prove profitability."
- "A fail here does not reject HIP-3 generally."
- "Phase 0 drafting is not authorized by this scout unless the final status is
  `HIP3_PHASE0_PRECOMMITMENT_WARRANTED`, and even then the precommitment must be
  drafted in a separate explicitly authorized run."
- "Single-run Phase 0 warrant is forbidden; two independent passing scouts are required."

## Corrective registry note preview requirements

- Prior entry heading
- Correction summary
- New API route explanation
- Frontend/API consistency result
- New final status
- Whether old note should be amended
- Exact suggested wording
- No automatic registry mutation by default

## CLI usage

### Dry run
```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_offhours_scout_v0 \
  --dry-run
```

### Full run
```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_offhours_scout_v0 \
  --out-root reports/hip3_builder_dex_tradfi_offhours_scout_v0 \
  --seed-tickers TSLA,AAPL,MSFT,NVDA,AMZN,GOOG,GOOGL,META,SPX,NDX,NAS100,QQQ,GOLD,XAU,SILVER,XAG,OIL,WTI,BRENT \
  --start-date 2025-10-13 \
  --end-date latest \
  --max-symbols 12 \
  --max-archive-days-per-symbol 7 \
  --max-l2-hours-per-symbol 72 \
  --download-budget-bytes 500000000 \
  --l2-budget-bytes 250000000 \
  --require-sanity-seeds true \
  --allow-network-public \
  --allow-s3-archive-read
```

### Registry correction (separate command)
```bash
python -m examples.strategies.venue_agnostic_signal_observer.write_hip3_builder_dex_registry_correction_v0 \
  --from-report reports/hip3_builder_dex_tradfi_offhours_scout_v0/<run_id> \
  --authorize
```
