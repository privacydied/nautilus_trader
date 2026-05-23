# Hyperliquid funding divergence Phase 0 audit

## 1. Study identity
Study ID: hyperliquid_funding_divergence_phase0. Distribution-only funding-divergence audit.

## 2. Not a precommitment
This is not a precommitment, not an evaluator, not a return study, not a PnL study, not shadow execution, and not a bot path.

## 3. Kill criteria written before histogram
The runner records pre_data_kill_criteria before loading any data. Module section constants define: `KILL_CRITERION_MAX_ABS_BPS = 10.0` and `KILL_CRITERION_P99_ABS_BPS = 8.0`.

## 4. Data sources and coverage
[
  {
    "source_name": "hyperliquid_BTC",
    "venue": "hyperliquid",
    "asset": "BTC",
    "native_symbol": "BTC",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_BTC_2024-01-01_to_2025-05-30.jsonl",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00.151000Z",
    "source_last_row_timestamp_utc": "2025-05-30T23:00:00.180000Z",
    "source_row_count": 12383,
    "source_sha256": "531d7189a8e406fb9044f242a56e3855e9011ff572f2237d116ad4b01ff1eb1b",
    "source_native_funding_interval_hours": 1.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "jsonl timestamp_ms/time/timestamp field with epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours",
    "source_format": "jsonl"
  },
  {
    "source_name": "hyperliquid_ETH",
    "venue": "hyperliquid",
    "asset": "ETH",
    "native_symbol": "ETH",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_ETH_2024-01-01_to_2025-05-30.jsonl",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00.151000Z",
    "source_last_row_timestamp_utc": "2025-05-30T23:00:00.180000Z",
    "source_row_count": 12383,
    "source_sha256": "46663673457ad6567dd6f09ca36f18a09d024f101bc29515009b7b4f87460f84",
    "source_native_funding_interval_hours": 1.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "jsonl timestamp_ms/time/timestamp field with epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours",
    "source_format": "jsonl"
  },
  {
    "source_name": "binance_BTC",
    "venue": "binance",
    "asset": "BTC",
    "native_symbol": "BTC-PERP",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_btc_funding.csv",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00Z",
    "source_last_row_timestamp_utc": "2025-05-31T16:00:00Z",
    "source_row_count": 1551,
    "source_sha256": "3b3917e1a06e7e8207fa8fa9adcec10dfb60eacab2724b3475b20f9ebde47b24",
    "source_native_funding_interval_hours": 8.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "iso8601 marker or epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours"
  },
  {
    "source_name": "binance_ETH",
    "venue": "binance",
    "asset": "ETH",
    "native_symbol": "ETH-PERP",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_eth_funding.csv",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00Z",
    "source_last_row_timestamp_utc": "2025-05-31T16:00:00Z",
    "source_row_count": 1551,
    "source_sha256": "8f9516c49915dde7fb47f9a8241f6840d0b130419042bf7f24c4b3b398c2fc6d",
    "source_native_funding_interval_hours": 8.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "iso8601 marker or epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours"
  },
  {
    "source_name": "bybit_BTC",
    "venue": "bybit",
    "asset": "BTC",
    "native_symbol": "BTCUSDT",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_btc_funding.csv",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00Z",
    "source_last_row_timestamp_utc": "2025-06-01T00:00:00Z",
    "source_row_count": 1552,
    "source_sha256": "754f09debaa0b3b37a8331a2931223207bdddc4023bf876e0989199f006963e2",
    "source_native_funding_interval_hours": 8.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "iso8601 marker or epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours"
  },
  {
    "source_name": "bybit_ETH",
    "venue": "bybit",
    "asset": "ETH",
    "native_symbol": "ETHUSDT",
    "source_path_or_endpoint": "examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_eth_funding.csv",
    "source_first_row_timestamp_utc": "2024-01-01T00:00:00Z",
    "source_last_row_timestamp_utc": "2025-06-01T00:00:00Z",
    "source_row_count": 1552,
    "source_sha256": "969d02c10f5018a00ca2f03f9ea9f1862d4fb145148bb2fb75edc285484d998a",
    "source_native_funding_interval_hours": 8.0,
    "source_timestamp_unit_detected": "ms",
    "timestamp_unit_detection_method": "iso8601 marker or epoch digit length",
    "interval_detection_method": "median adjacent timestamp delta hours"
  }
]

## 5. Data-source provenance table
See manifest.json data_source_provenance for first row timestamp, last row timestamp, row count, source sha256, detected timestamp unit, and detected native funding interval.

## 6. Alignment method and no-lookahead proof
Each Hyperliquid timestamp aligns to the latest reference timestamp where reference_timestamp <= hyperliquid_timestamp. Future rows are never eligible; no interpolation or covering-interval logic is used.

## 7. Funding normalization method
hourly_funding_bps = native_funding_rate * 10000 / native_interval_hours. projected_8h_funding_bps = hourly_funding_bps * 8.

## 8. Distribution table
[
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.080275,
    "p75_absolute_divergence_bps_hourly": 0.1633405,
    "p90_absolute_divergence_bps_hourly": 0.40203339999999993,
    "p95_absolute_divergence_bps_hourly": 0.6093615999999997,
    "p99_absolute_divergence_bps_hourly": 1.094166210000001,
    "max_absolute_divergence_bps_hourly": 5.855828000000001,
    "mean_signed_divergence_bps_hourly": 0.11802975579423404,
    "median_signed_divergence_bps_hourly": 0.059175000000000005,
    "count_hl_funding_above_reference": 8406,
    "count_hl_funding_below_reference": 1608,
    "p99_positive_side_divergence_bps_hourly": 1.2773732500000001,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.611526505,
    "asset": "BTC",
    "reference_basis": "binance"
  },
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.08376249999999999,
    "p75_absolute_divergence_bps_hourly": 0.1742195,
    "p90_absolute_divergence_bps_hourly": 0.3954064999999997,
    "p95_absolute_divergence_bps_hourly": 0.59130675,
    "p99_absolute_divergence_bps_hourly": 1.081790370000001,
    "max_absolute_divergence_bps_hourly": 5.855828000000001,
    "mean_signed_divergence_bps_hourly": 0.11760246636517806,
    "median_signed_divergence_bps_hourly": 0.061024999999999996,
    "count_hl_funding_above_reference": 8158,
    "count_hl_funding_below_reference": 1479,
    "p99_positive_side_divergence_bps_hourly": 1.2691301250000016,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.6799184200000002,
    "asset": "BTC",
    "reference_basis": "bybit"
  },
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.0779,
    "p75_absolute_divergence_bps_hourly": 0.15763749999999999,
    "p90_absolute_divergence_bps_hourly": 0.3950600999999999,
    "p95_absolute_divergence_bps_hourly": 0.5949843,
    "p99_absolute_divergence_bps_hourly": 1.0823310000000017,
    "max_absolute_divergence_bps_hourly": 5.855828000000001,
    "mean_signed_divergence_bps_hourly": 0.11781611107970605,
    "median_signed_divergence_bps_hourly": 0.060125,
    "count_hl_funding_above_reference": 9171,
    "count_hl_funding_below_reference": 1687,
    "p99_positive_side_divergence_bps_hourly": 1.205904499999999,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.6247375,
    "asset": "BTC",
    "reference_basis": "median_reference"
  },
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.07345,
    "p75_absolute_divergence_bps_hourly": 0.1698625,
    "p90_absolute_divergence_bps_hourly": 0.3819243999999998,
    "p95_absolute_divergence_bps_hourly": 0.5427987999999998,
    "p99_absolute_divergence_bps_hourly": 0.9249959600000015,
    "max_absolute_divergence_bps_hourly": 2.2331860000000003,
    "mean_signed_divergence_bps_hourly": 0.07743431902608415,
    "median_signed_divergence_bps_hourly": 0.029768499999999996,
    "count_hl_funding_above_reference": 7292,
    "count_hl_funding_below_reference": 2639,
    "p99_positive_side_divergence_bps_hourly": 1.0707629850000002,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.6366555099999996,
    "asset": "ETH",
    "reference_basis": "binance"
  },
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.077271,
    "p75_absolute_divergence_bps_hourly": 0.1789550000000001,
    "p90_absolute_divergence_bps_hourly": 0.36626359999999974,
    "p95_absolute_divergence_bps_hourly": 0.5265239999999998,
    "p99_absolute_divergence_bps_hourly": 0.91112985,
    "max_absolute_divergence_bps_hourly": 2.2331860000000003,
    "mean_signed_divergence_bps_hourly": 0.07971511830735686,
    "median_signed_divergence_bps_hourly": 0.02726250000000001,
    "count_hl_funding_above_reference": 7073,
    "count_hl_funding_below_reference": 2510,
    "p99_positive_side_divergence_bps_hourly": 1.0689262799999988,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.6305871199999995,
    "asset": "ETH",
    "reference_basis": "bybit"
  },
  {
    "count": 12383,
    "p50_absolute_divergence_bps_hourly": 0.070767,
    "p75_absolute_divergence_bps_hourly": 0.16365000000000002,
    "p90_absolute_divergence_bps_hourly": 0.36961069999999996,
    "p95_absolute_divergence_bps_hourly": 0.5278331500000001,
    "p99_absolute_divergence_bps_hourly": 0.9238892650000007,
    "max_absolute_divergence_bps_hourly": 2.2331860000000003,
    "mean_signed_divergence_bps_hourly": 0.07857471866672051,
    "median_signed_divergence_bps_hourly": 0.03541875,
    "count_hl_funding_above_reference": 8045,
    "count_hl_funding_below_reference": 2669,
    "p99_positive_side_divergence_bps_hourly": 1.034922780000001,
    "p99_negative_side_absolute_divergence_bps_hourly": 0.6279781400000004,
    "asset": "ETH",
    "reference_basis": "median_reference"
  }
]

## 9. Kill decision table
BTC p99 vs 8 bps: 1.0823310000000017
BTC max vs 10 bps: 5.855828000000001
ETH p99 vs 8 bps: 0.9238892650000007
ETH max vs 10 bps: 2.2331860000000003

## 10. Single-asset distribution-ready interpretation
If exactly one asset clears, future v1 must predeclare one-asset vs two-asset scope and count family size accordingly. Do not silently drop the failing asset later.

## 11. Persistence / half-life
Reported in persistence_half_life.csv with n_observations, n_censored, and percentile suppression where n < 30.

## 12. Calendar stratification
Descriptive only. Calendar conditioning cannot create evaluation cells in Phase 0. If used later, it must be frozen in a separate v1 precommitment and counted in family size / FDR.

## 13. Non-coverage
This is separate from Family 2 funding crowding reversal, Family 3 funding x OI variants, and the Hyperliquid BTC->LINK fixed-cell paper replay.
The invalidated +39.47 bps BTC->LINK replay result is not evidence for this funding-divergence hypothesis.
The corrected +6.13 bps BTC->LINK replay result is not evidence against this funding-divergence hypothesis.
BTC->LINK replay involved cross-asset beta-lag. This audit involves same-asset funding divergence and venue-specific crowding pressure.

## 14. Self-test paragraph
If this hypothesis advances to v1 and the v1 return leg is BTC spot, the separation risks laundering Family 2 / Family 3 unless the load-bearing mechanism is explicitly the venue-specific crowding pressure on Hyperliquid identified via HL-vs-reference funding divergence, not BTC spot funding extremes alone. Prefer a Hyperliquid perp return leg, Hyperliquid venue crowding, explicit funding-paid-while-held treatment, or another clearly distinct mechanism.

## 15. Final Phase 0 status
PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL

## 16. Next-step rule
No v1 precommitment should be written.

## Appendix: branch handling
remote branch feat/hyperliquid-funding-divergence-phase0 absent at start; created from develop

Kill decision: Phase 0 — killed at distribution audit, no precommitment written.
