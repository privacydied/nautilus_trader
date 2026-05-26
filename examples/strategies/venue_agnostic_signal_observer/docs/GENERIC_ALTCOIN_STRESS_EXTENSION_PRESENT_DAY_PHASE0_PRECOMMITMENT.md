# Generic altcoin stress extension: present-day Phase 0 precommitment

## Purpose

Extend the prior generic altcoin stress regime ablation (Phase 0A/0B) from its original data window (2024-01 through 2025-12) through the latest evaluable present-day Hyperliquid asset_ctxs archive. Determine whether the generic price-only stress detector's positive 24h forward return signal persists in the extension-only window.

This is an archive-only diagnostic extension. It does not change any detector parameter, threshold, cost assumption, cooldown logic, horizon, symbol universe, negative control, or return metric definition inherited from the prior generic stress ablation.

## Inherited prior generic ablation artifacts

| Artifact | Path |
|---|---|
| Phase 0 precommitment | `examples/strategies/venue_agnostic_signal_observer/docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0_PRECOMMITMENT.md` |
| Phase 0A report | `reports/generic_altcoin_stress_regime_ablation_phase0a/generic_altcoin_stress_regime_ablation_phase0a_20260525T235410_666100_010d14/` |
| Phase 0B report | `reports/generic_altcoin_stress_regime_ablation_phase0b/generic_altcoin_stress_regime_ablation_phase0b_20260526T013814_632696_22db27/` |
| Phase 0A accepted events | `reports/generic_altcoin_stress_regime_ablation_phase0a/generic_altcoin_stress_regime_ablation_phase0a_20260525T235410_666100_010d14/accepted_events.jsonl` |
| Comparison audit | `reports/liquidation_vs_generic_stress_ablation_comparison/liquidation_vs_generic_stress_ablation_comparison_20260526T021044_585288_c3736f/summary.json` |

## Inherited precommitment hash

```
inherited_from_precommitment_sha256: 9faf9a8e9111bf5f9a564a69980b8c26d6a0084d229546f67195c51e95b8c448
```

## Frozen detector constants (inherited unchanged from prior generic stress ablation)

| Constant | Value |
|---|---|
| Trailing 1h return threshold | <= -300 bps |
| Trailing 6h realized vol percentile threshold | >= 0.80 |
| Vol lookback window | 30 days |
| Vol minimum history | 14 days |
| Cooldown | 48h per symbol |
| Min accepted events | 300 |
| Min accepted symbols | 8 |
| Min symbols with 3+ events | 8 |
| Max symbol event share | 20% |
| Max month event share | 25% |
| Max quarter event share | 45% |
| Min distinct months | 6 |
| Min distinct quarters | 2 |
| Direction | Long only (after negative stress) |
| BTC/ETH | Excluded from all event gates |
| Detector type | Price-only. No OI, liquidation, funding inputs |

## Frozen symbol universe

All 34 Hyperliquid frozen symbols. BTC and ETH excluded by detector gates. PEPE may be missing from some archive periods.

AAVE, ADA, APT, ARB, ATOM, AVAX, BCH, BNB, BTC, DOGE, DOT, ENA, ETH, FET, HYPE, INJ, JUP, LINK, LTC, MKR, NEAR, ONDO, OP, PENDLE, SEI, SOL, SUI, TIA, TON, TRX, UNI, WIF, WLD, XRP

## Frozen cost model (inherited)

- Primary diagnostic cost: 50 bps round-trip (maker + taker)
- Stress costs: 75 bps and 100 bps
- Cost arithmetic: net75 = net50 - 25.0, net100 = net50 - 50.0

## Frozen horizon model (inherited)

- Primary horizon: 24h
- Secondary horizons (diagnostic only): 6h, 12h, 48h

## Frozen cooldown logic (inherited)

- 48h per symbol, greedy from earliest

## Frozen negative controls (inherited)

1. Boring/non-stress control: same eligible universe/timestamps, stress detector condition not triggered, sample/cap to match extension-only event count, seed 20260526.
2. Random timestamp control: same symbols/eligible timestamp mask, sample/cap to match extension-only event count, seed 20260527.
3. Inverse-direction control: accepted stress events but inverted direction/return sign, deterministic if sampling needed, seed 20260528.
4. Control bootstrap CI: 200 bootstrap resamples, 95% CI for net50 mean, seed 20260529.

## Frozen temporal concentration gates (inherited)

- Extension-only evaluated_event_count >= 100 (GENERIC_STRESS_EXTENSION_UNDERPOWERED if below)
- Extension-only max calendar year share <= 0.50 (GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED if above)
- Extension-only max calendar week share <= 0.30 (EXTENSION_WEEKLY_CLUSTERING_WARNING if above)
- Full-extended-window 2024 share threshold: if > 0.50, emit FULL_WINDOW_TEMPORAL_CONCENTRATION_STILL_FAILED

## Frozen extension-only/full-window verdict rules

- Extension-only pass requires: sufficient events, positive net50 mean above inherited threshold (net50_mean > +50 bps), positive net75 and net100, negative controls pass, independent return reproduction passes, temporal concentration passes, lookahead audit passes, survivorship explicitly classified.
- Full-window 2024 share > 50 does NOT override extension-only pass unless full-window pass is a precommitment requirement (it is not — only extension-only matters).
- Final pass: GENERIC_STRESS_EXTENSION_AUDIT_PASSED_AWAITING_COOLING_PERIOD
- Final diagnostic fail: GENERIC_STRESS_EXTENSION_UNDERPOWERED, GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED, or GENERIC_STRESS_EXTENSION_NEGATIVE_CONTROL_FAILED

## Detector code hash

```
detector_code_sha256: 34ae07f3fff0d4d0bcf7639384e2673fed194f178ceffe3399438123a1b14900
```

## Latest evaluable complete timestamp rule

latest_evaluable_complete_timestamp = min(
    per-symbol last timestamp,
    archive end timestamp
) - 24h forward horizon

No event is evaluated without 24h forward price coverage.

## Performance refactor note

The detector event-selection logic has been refactored (vectorized rolling percentiles, numpy forward return lookup, mmap-based JSONL reads, per-symbol parquet cache) but all thresholds, costs, cooldown, and selection semantics are unchanged from the prior generic ablation. Detector code hash drift guard verifies this.

## Scope boundary: no liquidation comparison

This extension test was triggered by the prior generic stress ablation's temporal concentration gate and does not address the OI-conditioning question, which remains separately closed/unresolved by its own registry entry.

No liquidation fields, comparison, or machine-readable statuses appear in this task's summary.json.

## 72h cooling period rule

If and only if the final extension-only status is GENERIC_STRESS_EXTENSION_AUDIT_PASSED_AWAITING_COOLING_PERIOD, a 72-hour cooling period begins. During this period:
- Phase 0C precommitment drafting is unlocked after 72h.
- Phase 0C execution is NOT unlocked immediately.
- Phase 0D, v1, paper trading, shadow execution, and live trading remain locked.
- Any future Phase 0C must jointly consider prior generic ablation audit, this extension result, concentration warnings, and survivorship caveats.

## Safety statement

No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update are used in this task.

This is archive-only public S3 data. No exchange accounts, signing, authentication, or private keys are accessed.

This extension diagnostic does not unlock Phase 0C execution, Phase 0D, v1, paper trading, shadow execution, or live trading.

Precommitment SHA-256 (self): 1ca4f398ca284ca78c1000af0289699fb9c244f395db7dae7818fdf5ddb413c3
