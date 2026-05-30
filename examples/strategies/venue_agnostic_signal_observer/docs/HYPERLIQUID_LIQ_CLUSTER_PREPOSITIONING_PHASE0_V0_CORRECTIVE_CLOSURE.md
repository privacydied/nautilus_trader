# Corrective Closure — LIQ Cluster Phase 0 v0 Proxy Reconstruction

**Date**: 2026-05-30
**Branch**: `feat/hyperliquid-liq-cluster-prepositioning-phase0-v0`
**Corrective Branch**: `fix/liq-cluster-proxy-closure-v0`

## Original Run Details

| Field | Value |
|-------|-------|
| Commits | `f49b3d92aa`, `faf12a903b`, `a1452e9e40` |
| Tests | 77 passed, 0 failed |
| Safety scan | Passed |
| Dry run status | `PHASE_MINUS1_DRY_RUN_READY` |
| Real run status (original) | `PHASE0A_MECHANISM_RECONSTRUCTABLE` |
| Real run path | `reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_075233/` |
| Symbols reconstructed | ADA, AVAX, DOGE, LINK, SOL (5 symbols) |
| Signals generated | 1,079 across 2 symbols |
| Phase 0B discovery events | 755 |

## Corrected Classification

| Field | Value |
|-------|-------|
| Original emitted status | `PHASE0A_MECHANISM_RECONSTRUCTABLE` |
| Corrected status | `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_PROXY_ONLY_RECONSTRUCTION` |
| True hypothesis verdict | **NOT_TESTED** — archive reconstruction blocked |
| Proxy diagnostic verdict | Negative proxy, **not promotable**, not evidence against true mechanism |
| Phase 0B validity | Not valid for mechanism evaluation when Phase 0A is proxy-only |
| Registry posture | Do **not** mark `REJECTED` |

## Why Original Status Was Too Strong

The original run emitted `PHASE0A_MECHANISM_RECONSTRUCTABLE`, implying that the exact isolated-margin liquidation-price cluster map was reconstructable from available public archive data. This is incorrect for several reasons:

### 1. Proxy Reconstruction, Not Exact Reconstruction

The implementation uses **aggregate OI snapshots** with a 60/40 long/short split and hardcoded default leverage tiers. This produces an approximate liquidation-price distribution but not the per-address isolated-margin liquidation prices specified in the precommitment.

Key deficiencies:
- Aggregate OI cannot distinguish isolated vs cross margin positions.
- Cross-margin / undetermined positions dominate the altcoin universe.
- Hardcoded leverage defaults (e.g., SOL=25x) are applied uniformly, not derived from historical `meta` snapshots or node fills.
- Leverage-tier history is unavailable; no per-address leverage profiles exist.

### 2. What Evidence Was Actually Collected

- 5 symbols had usable aggregate OI data in the partial 2024 window.
- Liquidation price estimates were computed from aggregate notional, not per-position state.
- No node fills archive access was available for per-address reconstruction.
- No L2 executable book data for the effective altcoin universe; returns are mark-return only.

### 3. Why Proxy Reconstruction Is Insufficient

The precommitment specifies that isolated-margin liquidation prices require:
- Per-address position state (entry price, size, leverage, margin mode)
- Historical leverage tier changes
- Ability to distinguish isolated from cross margin

Aggregate OI provides none of these. It can estimate where liquidations *might* be concentrated, but it cannot produce the exact mechanism being tested.

### 4. Why Negative Proxy Returns Do Not Reject the True Hypothesis

The proxy run produced:
- Mean net50: -46.3 bps
- Median net50: -48.5 bps
- Win rate: 26.1%

These are **diagnostic context only**. They reflect the behavior of an approximate cluster map under mark-return, not the true liquidation-cluster mechanism. The true mechanism was never actually measured — the proxy is a biased fragment-map, and its negative result does not falsify the hypothesis that exact isolated-margin clusters would show continuation.

### 5. What This Does NOT Unlock

- No v1 study
- No paper trading
- No shadow execution
- No live trading
- No auto-promotion
- No registry rejection

The required future unblocker is **node fills archive access** (per-address position state) or a public liquidation-price/margin-mode source.

## Artifact Paths Observed

```
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_063119/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_063226/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_064240/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_064707/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_072043/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_075233/
reports/hyperliquid_liq_cluster_prepositioning_phase0_v0/20260530_080102/
```

These report directories are untracked and not committed.

## Safety Confirmation

- No orders, private keys, API keys, auth, signing, or live execution.
- No paper trading, shadow execution, or bot path changes.
- No systemd watcher changes.
- No auto-promotion or registry rejection.
- All changes are code/docs/tests only.

## Final Verdict

```
True mechanism:          NOT_TESTED_ARCHIVE_RECONSTRUCTION_BLOCKED
Proxy diagnostic:        NEGATIVE_PROXY_DIAGNOSTIC_NOT_PROMOTABLE
Promotion:               FORBIDDEN
Registry rejection:      FORBIDDEN / NOT REJECTED
Required unblocker:      Node fills archive access or public liquidation-price/margin-mode source
```

## Do Not Use the Proxy Run as Evidence of a Tradable Edge

The proxy run's negative result is not evidence that the liquidation-cluster mechanism does not exist. It is evidence that the aggregate-OI proxy map, under mark-return with 50 bps costs, produced negative returns. The true mechanism — per-address isolated-margin clusters with executable entries — was never tested.
