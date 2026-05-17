# Edge Miner Offline Discovery Report

- run_status: NO_REAL_CORPUS_AVAILABLE
- observer_only: true
- no_orders_were_placed: true
- no_exchange_connections_were_made: true
- no_live_capture_was_run: true
- no_private_key_flow_was_used: true
- corpus_used: synthetic_smoke
- corpus_status: NO_REAL_CORPUS_AVAILABLE
- corpus_hash: synthetic-smoke-populations-v1
- manifest_hash: synthetic-smoke-populations-v1
- grid_hash: 2c5d4a9918050869004fa921
- raw_cell_count: 768
- effective_trial_count: 768
- cells_tested: 768
- blocked_by_locked_rejection_guard: 0
- rejected_on_cost_floor: 382
- rejected_by_null_mcpt: 0
- rejected_by_fdr: 0
- rejected_by_dsr: 84
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

- capture_ids: ['synthetic_null', 'synthetic_planted', 'synthetic_untradeable', 'synthetic_decaying']
- files_loaded: 0
- warnings: ['No usable real capture corpus loaded; using synthetic smoke populations.']

## Top Rejected Candidates By Diagnostic Value

- 9f7f847a69a6eb4f8267e787 ETH->LINK price_impulse mean=0.00732006 n=120 reason=DSR_FAIL
- 17f6f66a9fc206af8760e104 BTC->LINK price_impulse mean=0.00729133 n=120 reason=DSR_FAIL
- 6e3749f09eb6d24c52912eb6 BTC->AVAX price_impulse mean=0.00721516 n=120 reason=DSR_FAIL
- ea355ebc2313ad6360f2a2ff ETH->DOGE price_impulse mean=0.00718503 n=120 reason=DSR_FAIL
- 4813d9aa381fbea39b7aefb3 ETH->DOGE price_impulse mean=0.00715917 n=120 reason=DSR_FAIL
- 40430ee76a482e515f8f975a BTC->LINK price_impulse mean=0.00713034 n=120 reason=DSR_FAIL
- ecc0cc35985d620f435a908e BTC->SOL price_impulse mean=0.00711923 n=120 reason=DSR_FAIL
- 0f019aefd31e5214bf0b422e BTC->SOL price_impulse mean=0.00710734 n=120 reason=DSR_FAIL
- d0371d925e658899a3cb211e ETH->AVAX price_impulse mean=0.00710072 n=120 reason=DSR_FAIL
- f8ebe9a7afa683c974d69f28 BTC->AVAX price_impulse mean=0.0070914 n=120 reason=DSR_FAIL

## Survivor Candidate Cards

- none

## Synthetic Smoke Assertions

- null_does_not_promote: True
- planted_scores_better_than_null: True
- untradeable_detectable_but_economically_rejected: True
- decaying_caught_by_nonstationarity: True

## Safety Statement

This was an observer-only offline run. No orders were placed. No exchange connections were made. No live capture was run. No private-key flow was used.
