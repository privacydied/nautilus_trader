# Hyperliquid OI Velocity Compression Breakout v0 Phase 0 Precommitment

Status: frozen before any Phase 0A data availability run.
Study key: hyperliquid_oi_velocity_compression_breakout_v0
Safety mode: archive/backfill-only observer feasibility.

## Frozen universe

Explicit frozen symbol list:

- BTC
- ETH
- SOL
- HYPE
- XRP
- DOGE
- BNB
- ADA
- LINK
- AVAX
- SUI
- TRX
- LTC
- BCH
- TON
- DOT
- AAVE
- UNI
- APT
- ARB
- OP
- SEI
- INJ
- NEAR
- TIA
- WIF
- PEPE
- FET
- ENA
- ONDO
- MKR
- JUP
- PENDLE
- WLD
- ATOM

Symbol list hash: `345d21a57bf99bc28202fca317ac446bcf89a8f0172f66ec16faf7a7337c4f8b`

Runtime may only evaluate this frozen list. There is no runtime discovery from “whatever has data today.” If Phase 0A finds fewer than 30 usable symbols from the frozen list, the run fails closed with `PHASE0A_INSUFFICIENT_OI_PRICE_COVERAGE`. Dropped symbols and reasons must be reported. The runtime must not auto-expand the universe.

## Locked-gate distinction

This hypothesis is distinct from locked gates 9, 10, 11, 12, 13, and Hyperliquid funding carry Phase 0 because the primary signal is OI velocity, not funding level, funding spread, funding extreme, or funding-conditioned OI. Funding is excluded from the feature space and this exclusion must be verified by AST import/path scans.

## In scope

- Historical Hyperliquid open interest snapshots.
- Historical Hyperliquid perp price series using frozen price field: mark price.
- Second independent direction proxy: perp basis, mark minus index, used only when mark and index data are present.
- Deterministic historical public backfill or existing local historical archives.
- Observer-only feasibility diagnostics.

## Explicit out of scope

- No funding feature.
- No funding threshold.
- No funding-conditioned subset.
- No funding values loaded into feature space.
- No cross-venue OI.
- No tick-level data.
- No order-book features.
- No basis feature unless used strictly as the second direction proxy and explicitly frozen.
- No live capture.
- No execution/shadow/bot path.
- Nothing outside the in-scope list may be added without a new precommitment.

## Frozen parameters

- `oi_velocity_lookback_h = 6`
- `realized_vol_lookback_h = 24`
- realized vol uses trailing hourly returns.
- percentile lookback is 30 calendar days.
- first 30 days per symbol are warmup and excluded from event counting and coverage denominators.
- event cooldown is 6h per symbol.
- cooldown tie-break is first-wins, not rank-wins.
- OI velocity event threshold: `oi_velocity_percentile >= 0.90`.
- Realized volatility compression threshold: `realized_vol_percentile <= 0.30`.
- Forward horizons: 1h, 4h, 12h.
- Primary direction proxy: sign of price drift over `oi_velocity_lookback_h`.
- Second direction proxy: sign of perp basis, mark minus index. This is diagnostic and stabilizing, not a funding proxy.

## Phase 0A data availability and funding quarantine

Required data: historical open interest snapshots and historical perp mark price. Funding values must not be loaded into feature space. If a manifest needs funding availability, it may log only `funding_history_available: true/false` per symbol. Funding values are never read.

Coverage requirement: at least 30 symbols from the frozen universe must have at least 12 months usable OI and price coverage after excluding the first 30 days as warmup. Max single data gap must be less than 48h. Total missing gap duration must be less than 5% of the evaluated window.

## Phase 0B compressed OI build events

OI velocity:

`oi_velocity_bps = 10000 * (oi_t - oi_t_minus_6h) / oi_t_minus_6h`

Realized volatility: trailing 24h hourly returns, strictly past-only.

Percentile ranks: OI velocity and realized vol each use trailing 30 calendar days, strictly past-only. First 30 days per symbol produce no events.

Event definition:

- `oi_velocity_percentile >= 0.90`
- `realized_vol_percentile <= 0.30`
- Per-symbol event cooldown: 6h
- First candidate wins within a cooldown window.

Phase 0B pass gates:

- At least 200 events across the frozen usable universe.
- At least 10 symbols contributing at least 5 events each.
- No single symbol may contribute more than 30% of total events.

## Phase 0C mechanism sanity diagnostic

This is not a v1 evaluator. It only checks whether the event family has enough directional structure to justify a separate v1 precommitment.

Forward horizons: 1h, 4h, 12h.

Primary direction proxy: sign of price drift over `oi_velocity_lookback_h`. If drift is zero, exclude from directional asymmetry counts and record `n_drift_zero_excluded`.

Second independent direction proxy: sign of perp basis, mark minus index. This is not a funding proxy.

Direction stability gate: drift-sign vs second-proxy-sign disagreement above 40% of non-zero comparable events emits `PHASE0C_DIRECTION_PROXY_UNSTABLE`.

Horizon sample-size gate: a horizon can satisfy hit-rate or median gates only if it has at least 100 valid events across the universe. Underpowered horizons are marked `PHASE0C_HORIZON_UNDERPOWERED` and cannot satisfy the pass condition alone.

Directional gates for at least one powered horizon:

- Direction-adjusted hit rate >= 0.60.
- Median direction-adjusted signed forward return >= 20 bps.

Vol-only detector: if absolute movement is more than 3x signed movement and hit rate is near 0.5, emit `PHASE0C_MECHANISM_MISMATCH_VOL_ONLY`.

Momentum masquerade guard: median direction-adjusted forward magnitude must exceed 1.5x median direction-adjusted lookback drift magnitude. Otherwise emit `PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION`.

Move magnitude failure: if direction is stable and not vol-only and not simple drift-continuation, but no powered horizon has median direction-adjusted signed forward return >= 20 bps, emit `PHASE0C_MOVE_MAGNITUDE_TOO_SMALL`.

## Failure-mode mapping

- `PRECOMMITMENT_HASH_MISMATCH`: current precommitment hash differs from `precommitment_hash.txt`; fail before loading data.
- `PHASE0A_INSUFFICIENT_OI_PRICE_COVERAGE`: fewer than 30 frozen symbols have usable 12-month OI and price coverage.
- `PHASE0A_FUNDING_QUARANTINE_VIOLATION`: funding archive imports, banned funding paths, or funding values enter feature-space code.
- `PHASE0B_INSUFFICIENT_COMPRESSED_OI_BUILD_EVENTS`: event count or symbol participation fails.
- `PHASE0B_UNIVERSE_CONCENTRATION_FAILURE`: one symbol contributes more than 30% of total events.
- `PHASE0C_HORIZON_UNDERPOWERED`: all horizons are underpowered.
- `PHASE0C_DIRECTION_PROXY_UNSTABLE`: second proxy disagreement exceeds 40%.
- `PHASE0C_MECHANISM_MISMATCH_VOL_ONLY`: volatility expansion appears without directional edge.
- `PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION`: result is consistent with drift continuation masquerading as OI signal.
- `PHASE0C_MOVE_MAGNITUDE_TOO_SMALL`: direction is stable but forward magnitude fails the 20 bps gate.
- `PHASE0_READY_FOR_V1_PRECOMMITMENT`: all Phase 0 diagnostic gates clear, allowing only a separate v1 precommitment.

## Verdict ladder

Phase 0C terminal verdicts are mutually exclusive and resolved in this exact order:

1. underpowered
2. direction proxy unstable
3. vol-only mismatch
4. drift-continuation/momentum masquerade
5. move magnitude too small
6. ready for v1 precommitment

## Forbidden runtime paths

This Phase 0 scaffold must not use live capture, WebSocket capture campaigns, stage2 watcher triggers, order-book live replay, execution, shadow, bot, private keys, authenticated APIs, paper trading, or any trading path. It must not update `REJECTED_RESEARCH.md`, run a v1 evaluator, run null/FDR/holdout, promote strategy PnL, tune thresholds after seeing data, or auto-expand the universe at runtime.
