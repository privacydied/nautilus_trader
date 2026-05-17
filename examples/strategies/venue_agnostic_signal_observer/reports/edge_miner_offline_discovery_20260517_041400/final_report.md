# Edge Miner Offline Discovery Report

- run_status: OFFLINE_DISCOVERY_COMPLETE
- observer_only: true
- no_orders_were_placed: true
- no_exchange_connections_were_made: true
- no_live_capture_was_run: true
- no_private_key_flow_was_used: true
- corpus_used: real_capture
- corpus_status: REAL_CORPUS_AVAILABLE
- corpus_hash: a73372c55f79a56993d61d2b
- manifest_hash: a949176fc55e938edf0ceb33
- grid_hash: 2b5452f71c364bd471a7c093
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
- regime_filter: NO_STRESS_LABELS_DIAGNOSTIC

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

## Top Rejected Candidates By Diagnostic Value

- 3d7a64eb04c3fda74677fad7 BTC->DOGE large_trade mean=0.00098414 n=10 reason=BELOW_COST_FLOOR
- b9d40ad995352d76f9ac05b6 BTC->DOGE large_trade mean=0.00089023 n=10 reason=BELOW_COST_FLOOR
- 20639705786c6c27869aeaee BTC->DOGE signed_imbalance mean=0.00085815 n=18 reason=BELOW_COST_FLOOR
- 5a00e8b4c2bc54d3ca4b2630 BTC->DOGE large_trade mean=0.00083561 n=10 reason=BELOW_COST_FLOOR
- f9f35c813268a91b96de00a6 BTC->DOGE signed_imbalance mean=0.00082866 n=19 reason=BELOW_COST_FLOOR
- d5cca20b130fe6ddf1b49b1a BTC->DOGE large_trade mean=0.0008102 n=10 reason=BELOW_COST_FLOOR
- d9b4db00152f2aeac6ea5140 BTC->DOGE signed_imbalance mean=0.00078337 n=19 reason=BELOW_COST_FLOOR
- 1ebeddd85d6914b5a8496175 BTC->DOGE signed_imbalance mean=0.00077423 n=19 reason=BELOW_COST_FLOOR
- a32690f51893bd4be67c4ba3 BTC->DOGE signed_imbalance mean=0.00067075 n=9 reason=FDR_FAIL
- 2e4f6024be4d57da9fab43e5 BTC->DOGE price_impulse mean=0.00066506 n=7 reason=FDR_FAIL

## Survivor Candidate Cards

- none

## Safety Statement

This was an observer-only offline run. No orders were placed. No exchange connections were made. No live capture was run. No private-key flow was used.
