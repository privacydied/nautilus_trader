# Edge Miner Offline Discovery Report

- run_status: OFFLINE_DISCOVERY_COMPLETE
- observer_only: true
- no_orders_were_placed: true
- no_exchange_connections_were_made: true
- no_live_capture_was_run: true
- no_private_key_flow_was_used: true
- corpus_used: real_capture
- corpus_status: REAL_CORPUS_AVAILABLE
- stress_label_status: STRESS_LABELS_AVAILABLE
- stress_label_count: 9
- stress_corpus_id: stress_beta_lag_v1
- stress_corpus_hash: 738bd12891e3ff9dce47701a
- corpus_hash: a73372c55f79a56993d61d2b
- manifest_hash: a949176fc55e938edf0ceb33
- grid_hash: 2c5d4a9918050869004fa921
- raw_cell_count: 768
- effective_trial_count: 768
- cells_tested: 768
- blocked_by_locked_rejection_guard: 0
- rejected_on_cost_floor: 197
- rejected_by_null_mcpt: 26
- rejected_by_fdr: 8
- rejected_by_dsr: 1
- rejected_by_cpcv_nonstationarity: 0
- sent_to_shadow: 0
- passing_shadow: 0
- final_candidate_count: 0
- cost_floor: 0.005
- regime_filter: stress_only

## Frozen Grid

- feature_families: price_impulse, signed_imbalance, notional_burst, large_trade
- source_assets: BTC, ETH
- target_assets: SOL, LINK, DOGE, AVAX
- lookbacks_seconds: [30, 60]
- horizons_seconds: [60, 180, 300]
- entry_delays_seconds: [0, 5, 15, 30]

## Corpus

- capture_ids: ['data/cross_asset_impulse_v1']
- files_loaded: 17

## Stress Labels

- status: STRESS_LABELS_AVAILABLE
- label_count: 9
- source_assets_evaluated: ['BTC', 'ETH']
- reason: Validated stress corpus manifest supplied to offline runner.
- stress_corpus_id: stress_beta_lag_v1
- stress_corpus_hash: 738bd12891e3ff9dce47701a
- usable_stress_windows: 9

## Top Rejected Candidates By Diagnostic Value

- 8b2f7034e1e6c526e23a9a86 BTC->DOGE large_trade mean=0.00098414 n=10 reason=BELOW_COST_FLOOR
- d748a3f6352e245fc93b3246 BTC->DOGE large_trade mean=0.00089023 n=10 reason=BELOW_COST_FLOOR
- 0e4e6f0e30ac5e095fa1f841 BTC->DOGE signed_imbalance mean=0.00085815 n=18 reason=BELOW_COST_FLOOR
- 0d47929ce514aa889d226dca BTC->DOGE large_trade mean=0.00083561 n=10 reason=BELOW_COST_FLOOR
- 68c6b7f382b5b0869bbf8ad6 BTC->DOGE signed_imbalance mean=0.00082866 n=19 reason=BELOW_COST_FLOOR
- 4748015d59cc23cd4bc85c36 BTC->DOGE large_trade mean=0.0008102 n=10 reason=BELOW_COST_FLOOR
- ff925cc68c404605dc0b6be5 BTC->DOGE signed_imbalance mean=0.00078337 n=19 reason=BELOW_COST_FLOOR
- 6f186a3c23eb38fa938ea746 BTC->DOGE signed_imbalance mean=0.00077423 n=19 reason=BELOW_COST_FLOOR
- 006e7ea37dc8c8b5086daaaf BTC->DOGE signed_imbalance mean=0.00067075 n=9 reason=FDR_FAIL
- c2fa43d92c0f0d8d68e7b51b BTC->DOGE price_impulse mean=0.00066506 n=7 reason=FDR_FAIL

## Survivor Candidate Cards

- none

## Safety Statement

This was an observer-only offline run. No orders were placed. No exchange connections were made. No live capture was run. No private-key flow was used.
