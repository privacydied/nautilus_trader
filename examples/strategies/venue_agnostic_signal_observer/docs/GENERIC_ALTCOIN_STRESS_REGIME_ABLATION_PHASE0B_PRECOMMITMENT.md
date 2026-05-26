# Generic altcoin stress regime ablation Phase 0B precommitment

## Purpose

Diagnostic return evaluation for a generic price-only altcoin downside-stress detector.

This is an ablation study. It measures forward returns after price-only stress events
to compare against the liquidation/OI flush detector from the prior venue-age-aware study.

If generic stress produces similar return behavior, the specificity of liquidation/OI
as a signal may be low. If generic stress is materially weaker, the liquidation/OI detector
may contain incremental information.

Phase 0B is return diagnostic only. Specificity cannot be judged until Phase 0C
null/clustering falsification.

## Frozen parameters

### Detector (Phase 0A, declared in Phase 0A precommitment)
- Price only: no OI, liquidation, or funding inputs
- Trailing 1h return <= -300 bps
- Trailing 6h realized vol percentile >= 0.80 (over 30-day lookback, min 14 days)
- Cooldown: 48h per-symbol
- BTC and ETH excluded from all event gates

### Return evaluation (Phase 0B)
- Same return horizons: 6h, 12h, 24h (primary), 48h
- Primary diagnostic cost: 50 bps round trip (maker + taker)
- Also report stress costs: 75 bps and 100 bps
- Direction: long after downside price drop
- Min effective events: 200

### Classification
- GENERIC_STRESS_PHASE0B_RETURN_DIAGNOSTIC_PASS
- GENERIC_STRESS_PHASE0B_RETURN_DIAGNOSTIC_FAIL
- GENERIC_STRESS_PHASE0B_INSUFFICIENT_FORWARD_COVERAGE
- GENERIC_STRESS_PHASE0B_ERROR_INVALID_PHASE0A_ARTIFACT
- GENERIC_STRESS_PHASE0B_ERROR_PRECOMMITMENT_MISMATCH

### Comparison benchmark
Liquidation-flush Phase 0B (venue-age-aware):
- 24h net mean after 50 bps: +125.95 bps
- 24h net median after 50 bps: +97.65 bps
- 24h win rate after 50 bps: 0.567
- Evaluated events: ~897

This comparison is diagnostic only.

## Specification

### Inputs
- Phase 0A accepted-events artifact (JSONL)
- Hyperliquid asset_ctxs archive

### Outputs
- Phase 0B summary.json
- Phase 0B summary.md with comparison block
- Horizon evaluations CSV
- Horizon metrics CSV

### Constraints
- Observer-only. No orders. No private keys. No trading auth.
- No live execution. No paper trading. No shadow execution.
- No systemd watcher changes. No bot path changes.
- No REJECTED_RESEARCH.md update.
- No archive/cache/report data committed.
- Does NOT unlock v1, trading, paper, shadow, bot, or systemd.

### Safety
- No orders, private keys, trading auth, live execution, paper trading,
  shadow execution, systemd watcher, or bot path were used.

Precommitment SHA-256 (self): b65e4e0582b8ed3f5e553dd98e0eba68f9b6d1fa0c1ad0de33c6f2e18bafa6ca
