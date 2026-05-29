# HIP-3 Cross-DEX No-Arb-Band Phase -1 Experiment

## Overview

Anchor-free comparison of two Hyperliquid builder DEXes directly using SonarX L2
summary snapshots. This is a Phase -1 data-plane feasibility study only.

## Hypothesis

Same-symbol HIP-3 builder DEX markets (e.g., cash:NVDA vs km:NVDA) may
temporarily diverge from each other by more than a conservative no-arb band
implied by visible spread, four-leg fee burden, funding differential, and
margin friction.

## Safety

- Public/archive data only
- No orders, no private keys, no exchange auth
- No PnL, no returns, no signals
- No registry mutation
- No live/paper trading

## Pair Definitions

Primary: cash:NVDA|km:NVDA, cash:TSLA|km:TSLA
Excluded: all flx:* pairs (stale/sparse oracle evidence)

## No-Arb Band Formula

conservative_noarb_band_bps =
  combined_visible_spread_bps
  + conservative_four_fill_fee_band_bps
  + funding_differential_band_bps
  + margin_friction_bps
  + extra_uncertainty_band_bps

## Gate Statuses

See ALLOWED_STATUSES in the core module for all valid final states.
