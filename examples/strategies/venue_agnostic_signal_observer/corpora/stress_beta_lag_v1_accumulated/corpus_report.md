# Edge Miner Stress Corpus Accumulator v1

- mode: local_only
- status: TARGET_COVERAGE_LIMITED
- ready_for_rerun: False
- corpus_id: stress_beta_lag_v1_accumulated
- corpus_hash: 02a4e332c99febbd0c7443d7
- stress_label_count: 9
- independent_stress_window_count: 6
- usable_window_count: 0
- rejected_window_count: 6
- target_coverage: {'SOL': 6, 'LINK': 2, 'DOGE': 2, 'AVAX': 0}
- independence_rule: merge same-source labels that overlap or occur within 30 minutes
- target_coverage_rule: require all targets from 60s before stress start through 360s after stress end
- target_returns_used_for_selection: false
- grid_or_thresholds_changed: false
- no_orders_were_placed: true
- no_private_key_flow: true
- public_capture_mode_implemented: false
