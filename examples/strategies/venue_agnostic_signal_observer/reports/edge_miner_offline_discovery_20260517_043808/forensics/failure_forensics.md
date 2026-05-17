# Edge Miner Failure Forensics

## 1. Executive verdict

- verdict: DATA_INSUFFICIENT_REJECTION
- contributing_reasons: ['DATA_INSUFFICIENT_REJECTION', 'TARGET_COVERAGE_LIMITED_REJECTION', 'COST_FLOOR_DOMINANT_FAILURE', 'TIMING_OR_MULTIPLE_TEST_FAILURE', 'SHADOW_NOT_REACHED']
- interpretation: The run produced zero survivors. With 9 stress windows this is useful diagnostic evidence, not a final global no-edge rejection.
- shadow_status: SHADOW_NOT_REACHED

## 2. Corpus adequacy

- stress_window_count: 9
- usable_window_count: 9
- stress_corpus_hash: 738bd12891e3ff9dce47701a
- label_version: stress_label.v1
- source_assets: ['BTC', 'ETH']
- target_assets: ['SOL', 'LINK', 'DOGE', 'AVAX']
- per_target_window_coverage: {'AVAX': 0, 'DOGE': 3, 'LINK': 3, 'SOL': 9}
- missing_target_assets: ['AVAX']
- enough_for_decisive_validation: False

## 3. Rejection funnel

- raw_cells: 768
- cells_tested: 768
- candidate_cards: 232
- rejected_on_cost_floor: 197
- rejected_by_null_mcpt: 26
- rejected_by_fdr: 8
- rejected_by_dsr: 1
- rejected_by_cpcv_nonstationarity: 0
- validator_survivors: 0
- sent_to_shadow: 0
- passing_shadow: 0
- final_candidates: 0

## 4. Cost-floor diagnostics

- rejected_on_cost_floor: 197
- gross_expected_bps_distribution: {'count': 197, 'min_bps': 0.0006, 'mean_bps': 1.887694, 'max_bps': 9.8414}
- distance_to_cost_floor_bps_distribution: {'count': 197, 'min_bps': -49.9994, 'mean_bps': -48.112306, 'max_bps': -40.1586}
- top_near_miss_cost_rejections: [{'candidate_hash': '8b2f7034e1e6c526e23a9a86', 'cell_id': 'BTC_to_DOGE_large_trade_lb60s_h300s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'large_trade', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 30, 'gross_expected_bps': 9.8414, 'cost_floor_bps': 50.0, 'distance_to_cost_floor_bps': -40.1586}, {'candidate_hash': 'd748a3f6352e245fc93b3246', 'cell_id': 'BTC_to_DOGE_large_trade_lb60s_h300s_d15s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'large_trade', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 15, 'gross_expected_bps': 8.9023, 'cost_floor_bps': 50.0, 'distance_to_cost_floor_bps': -41.0977}, {'candidate_hash': '0e4e6f0e30ac5e095fa1f841', 'cell_id': 'BTC_to_DOGE_signed_imbalance_lb30s_h300s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'signed_imbalance', 'horizon_seconds': 300, 'lookback_seconds': 30, 'entry_delay_seconds': 30, 'gross_expected_bps': 8.5815, 'cost_floor_bps': 50.0, 'distance_to_cost_floor_bps': -41.4185}]

## 5. Null / MCPT diagnostics

- rejected_by_null_mcpt: 26
- by_source_asset: {'BTC': 18, 'ETH': 8}
- by_target_asset: {'DOGE': 15, 'LINK': 5, 'SOL': 6}
- by_feature_family: {'notional_burst': 1, 'price_impulse': 19, 'signed_imbalance': 6}
- by_horizon_seconds: {'180': 9, '300': 17}
- top_near_miss_by_p_value: [{'candidate_hash': '4d4020501c50d75be972fd5d', 'cell_id': 'BTC_to_DOGE_price_impulse_lb60s_h180s_d15s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'price_impulse', 'horizon_seconds': 180, 'lookback_seconds': 60, 'entry_delay_seconds': 15, 'p_value': 0.06610854, 'alpha': 0.05}, {'candidate_hash': '392cfd31ea4e570e92e05b46', 'cell_id': 'BTC_to_DOGE_price_impulse_lb60s_h180s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'price_impulse', 'horizon_seconds': 180, 'lookback_seconds': 60, 'entry_delay_seconds': 30, 'p_value': 0.10274446, 'alpha': 0.05}, {'candidate_hash': 'f5aea4ae063aee4954213a27', 'cell_id': 'ETH_to_LINK_signed_imbalance_lb60s_h300s_d5s', 'source_asset': 'ETH', 'target_asset': 'LINK', 'feature_family': 'signed_imbalance', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 5, 'p_value': 0.14049036, 'alpha': 0.05}]

## 6. FDR diagnostics

- rejected_by_fdr: 8
- family_size: FIELD_UNAVAILABLE
- q_value_threshold: 0.1
- top_near_miss_by_p_value: [{'candidate_hash': 'c6e43b07b6b08ce08676858d', 'cell_id': 'BTC_to_DOGE_price_impulse_lb30s_h180s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'price_impulse', 'horizon_seconds': 180, 'lookback_seconds': 30, 'entry_delay_seconds': 30, 'p_value': 0.01926665, 'primary_fdr_q': 0.1, 'primary_fdr_method': 'benjamini_hochberg_offline_v1', 'family_size': 'FIELD_UNAVAILABLE'}, {'candidate_hash': '006e7ea37dc8c8b5086daaaf', 'cell_id': 'BTC_to_DOGE_signed_imbalance_lb60s_h300s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'signed_imbalance', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 30, 'p_value': 0.01971376, 'primary_fdr_q': 0.1, 'primary_fdr_method': 'benjamini_hochberg_offline_v1', 'family_size': 'FIELD_UNAVAILABLE'}, {'candidate_hash': '40430ee76a482e515f8f975a', 'cell_id': 'BTC_to_LINK_price_impulse_lb60s_h180s_d5s', 'source_asset': 'BTC', 'target_asset': 'LINK', 'feature_family': 'price_impulse', 'horizon_seconds': 180, 'lookback_seconds': 60, 'entry_delay_seconds': 5, 'p_value': 0.02000285, 'primary_fdr_q': 0.1, 'primary_fdr_method': 'benjamini_hochberg_offline_v1', 'family_size': 'FIELD_UNAVAILABLE'}]

## 7. DSR diagnostics

- rejected_by_dsr: 1
- dsr_rejections: [{'candidate_hash': '416be7c601df3e3f69989396', 'cell_id': 'BTC_to_LINK_price_impulse_lb60s_h180s_d0s', 'source_asset': 'BTC', 'target_asset': 'LINK', 'feature_family': 'price_impulse', 'horizon_seconds': 180, 'lookback_seconds': 60, 'entry_delay_seconds': 0, 'effective_trial_count': 768, 'raw_trial_count': 768, 'dsr_score': None, 'dsr_threshold': 0.95, 'volatility_adjustment_method': 'none', 'diagnostic_status': 'INSUFFICIENT_DATA'}]

## 8. Target coverage diagnostics

- SOL: covered across 9 windows
- LINK: partial coverage at 3 windows
- DOGE: partial coverage at 3 windows
- AVAX: not covered at 0 windows; no conclusion should be drawn

## 9. Next evidence required

- collect or assemble at least 20 independent BTC/ETH stress windows
- require target coverage for SOL/LINK/DOGE/AVAX
- do not change grid or thresholds until the next corpus is assembled
- rerun the same frozen stress-only grid
- only compare like-for-like grid/corpus hashes

## 10. Safety statement

- no orders placed
- no exchange connection
- no private-key flow
- no live capture
- observer-only offline analysis

## Top rejected candidates by diagnostic value

- {'candidate_hash': '8b2f7034e1e6c526e23a9a86', 'cell_id': 'BTC_to_DOGE_large_trade_lb60s_h300s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'large_trade', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 30, 'rejection_reason': 'BELOW_COST_FLOOR', 'mean_return_bps': 9.8414, 'hit_rate': 0.9, 'sharpe_like': 1.556651, 'n_observations': 10}
- {'candidate_hash': 'd748a3f6352e245fc93b3246', 'cell_id': 'BTC_to_DOGE_large_trade_lb60s_h300s_d15s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'large_trade', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 15, 'rejection_reason': 'BELOW_COST_FLOOR', 'mean_return_bps': 8.9023, 'hit_rate': 0.9, 'sharpe_like': 1.085306, 'n_observations': 10}
- {'candidate_hash': '0e4e6f0e30ac5e095fa1f841', 'cell_id': 'BTC_to_DOGE_signed_imbalance_lb30s_h300s_d30s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'signed_imbalance', 'horizon_seconds': 300, 'lookback_seconds': 30, 'entry_delay_seconds': 30, 'rejection_reason': 'BELOW_COST_FLOOR', 'mean_return_bps': 8.5815, 'hit_rate': 0.888889, 'sharpe_like': 1.21717, 'n_observations': 18}
- {'candidate_hash': '0d47929ce514aa889d226dca', 'cell_id': 'BTC_to_DOGE_large_trade_lb60s_h300s_d5s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'large_trade', 'horizon_seconds': 300, 'lookback_seconds': 60, 'entry_delay_seconds': 5, 'rejection_reason': 'BELOW_COST_FLOOR', 'mean_return_bps': 8.3561, 'hit_rate': 0.9, 'sharpe_like': 0.946759, 'n_observations': 10}
- {'candidate_hash': '68c6b7f382b5b0869bbf8ad6', 'cell_id': 'BTC_to_DOGE_signed_imbalance_lb30s_h300s_d5s', 'source_asset': 'BTC', 'target_asset': 'DOGE', 'feature_family': 'signed_imbalance', 'horizon_seconds': 300, 'lookback_seconds': 30, 'entry_delay_seconds': 5, 'rejection_reason': 'BELOW_COST_FLOOR', 'mean_return_bps': 8.2866, 'hit_rate': 0.842105, 'sharpe_like': 0.984959, 'n_observations': 19}
