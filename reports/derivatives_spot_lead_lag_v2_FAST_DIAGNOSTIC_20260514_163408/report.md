# Derivatives-Source Spot Lead-Lag v2 Report

## Hypothesis

Derivatives/perp flow as SOURCE predicts spot forward returns as TARGET.
Source: Binance USD-M perpetuals. Target: Kraken/Coinbase spot.

## Safety

**Public data only. No auth. No API keys. No orders. No derivatives execution.**

## Capture Context

- Capture method: combined async single-event-loop runner
- All-in cost: 50.0 bps (40.0 fee + 5.0 slippage + 5.0 quote mismatch)

## Overlap Windows

### binance_perp->kraken@BTC
- source_range: 2026-05-14T16:18:40.466Z - 2026-05-14T16:33:39.110Z
- target_range: 2026-05-14T16:18:42.520Z - 2026-05-14T16:33:39.337Z
- source_ticks: 23581
- target_ticks: 759
- source_price_range_bps: 21.68
- target_price_range_bps: 18.68
- overlap_start: 2026-05-14T16:18:42.520Z
- overlap_end: 2026-05-14T16:33:39.110Z
- overlap_duration_s: 896.59
- source_in_overlap: 23565
- target_in_overlap: 757
- overlap_source_price_range_bps: 21.68
- overlap_target_price_range_bps: 18.68

### binance_perp->kraken@ETH
- source_range: 2026-05-14T16:18:40.292Z - 2026-05-14T16:33:39.194Z
- target_range: 2026-05-14T16:18:58.424Z - 2026-05-14T16:33:16.666Z
- source_ticks: 17498
- target_ticks: 292
- source_price_range_bps: 28.37
- target_price_range_bps: 25.23
- overlap_start: 2026-05-14T16:18:58.424Z
- overlap_end: 2026-05-14T16:33:16.666Z
- overlap_duration_s: 858.24
- source_in_overlap: 16786
- target_in_overlap: 292
- overlap_source_price_range_bps: 28.37
- overlap_target_price_range_bps: 25.23

### binance_perp->kraken@SOL
- source_range: 2026-05-14T16:18:40.283Z - 2026-05-14T16:33:39.058Z
- target_range: 2026-05-14T16:18:48.622Z - 2026-05-14T16:33:38.880Z
- source_ticks: 5961
- target_ticks: 316
- source_price_range_bps: 30.09
- target_price_range_bps: 25.78
- overlap_start: 2026-05-14T16:18:48.622Z
- overlap_end: 2026-05-14T16:33:38.880Z
- overlap_duration_s: 890.26
- source_in_overlap: 5923
- target_in_overlap: 316
- overlap_source_price_range_bps: 30.09
- overlap_target_price_range_bps: 25.78

### binance_perp->coinbase@BTC
- source_range: 2026-05-14T16:18:40.466Z - 2026-05-14T16:33:39.110Z
- target_range: 2026-05-14T16:18:40.451Z - 2026-05-14T16:33:38.969Z
- source_ticks: 23581
- target_ticks: 15961
- source_price_range_bps: 21.68
- target_price_range_bps: 23.81
- overlap_start: 2026-05-14T16:18:40.466Z
- overlap_end: 2026-05-14T16:33:38.969Z
- overlap_duration_s: 898.5
- source_in_overlap: 23579
- target_in_overlap: 15960
- overlap_source_price_range_bps: 21.68
- overlap_target_price_range_bps: 23.81

### binance_perp->coinbase@ETH
- source_range: 2026-05-14T16:18:40.292Z - 2026-05-14T16:33:39.194Z
- target_range: 2026-05-14T16:18:41.508Z - 2026-05-14T16:33:39.431Z
- source_ticks: 17498
- target_ticks: 4552
- source_price_range_bps: 28.37
- target_price_range_bps: 25.65
- overlap_start: 2026-05-14T16:18:41.508Z
- overlap_end: 2026-05-14T16:33:39.194Z
- overlap_duration_s: 897.69
- source_in_overlap: 17488
- target_in_overlap: 4551
- overlap_source_price_range_bps: 28.37
- overlap_target_price_range_bps: 25.65

### binance_perp->coinbase@SOL
- source_range: 2026-05-14T16:18:40.283Z - 2026-05-14T16:33:39.058Z
- target_range: 2026-05-14T16:18:41.742Z - 2026-05-14T16:33:38.222Z
- source_ticks: 5961
- target_ticks: 2253
- source_price_range_bps: 30.09
- target_price_range_bps: 27.93
- overlap_start: 2026-05-14T16:18:41.742Z
- overlap_end: 2026-05-14T16:33:38.222Z
- overlap_duration_s: 896.48
- source_in_overlap: 5948
- target_in_overlap: 2253
- overlap_source_price_range_bps: 30.09
- overlap_target_price_range_bps: 27.93

## Price Ranges

- Max overlap price range: 30.09 bps
- All-in cost: 50.0 bps

## Signal Summary

- Total signals: 4118
- Valid forward returns: 26812

## Best Group by Mean Net Bps

- source_venue: binance_perp
- target_venue: kraken
- signal_type: notional_burst
- lookback_ms: 30000
- horizon_ms: 30000
- valid_count: 21
- mean_raw_bps: -1.0692
- mean_net_bps: -53.5467
- median_net_bps: -53.773
- win_rate: 0.0
- baseline_mean_net_bps: None
- baseline_win_rate: None
- candidate: False
- rejection_reasons: ['insufficient_events: 21 < 50', 'mean_net_return_not_positive: -53.55 bps', 'median_net_too_negative: -53.77 bps', 'no_valid_baseline_events', 'single_event_driven: mean_without_best=-53.75 bps', 'win_rate_fails: 0.0000 (no baseline to compare)']

## OI Bucket Summary

### price_up_oi_up
- event_count: 15722
- valid_count: 14719
- mean_net_bps: -54.8988
- median_net_bps: -55.0
- win_rate: 0.0

### price_down_oi_up
- event_count: 12292
- valid_count: 11281
- mean_net_bps: -55.2631
- median_net_bps: -55.0
- win_rate: 0.0

### flat_or_unknown
- event_count: 441
- valid_count: 441
- mean_net_bps: -55.1498
- median_net_bps: -55.0
- win_rate: 0.0

### price_down_oi_down
- event_count: 175
- valid_count: 175
- mean_net_bps: -53.8753
- median_net_bps: -53.9276
- win_rate: 0.0

### price_up_oi_down
- event_count: 196
- valid_count: 196
- mean_net_bps: -55.9218
- median_net_bps: -55.0
- win_rate: 0.0

**Best bucket**: price_down_oi_down (mean net = -53.8753 bps)

## Verdict

**REJECTED**

Sufficient overlap and movement observed; no signal group passed candidate gates.

