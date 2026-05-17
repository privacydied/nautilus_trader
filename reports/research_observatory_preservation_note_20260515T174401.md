# Research Observatory Preservation Note

## Timestamp
UTC: Fri 15 May 17:44:02 UTC 2026

## Current State
- **PWD:** /mnt/nasirjones/py/nautilus_trader
- **Git SHA:** 9caae790ccdf9325f1fa3f8638dcdf6ae532c98d
- **Branch:** feat-dex-cex-spot-dislocation-v1
- **Dirty:**  M reports/research_run_burned.jsonl
 M reports/research_run_index.jsonl
 M reports/stage2_readiness/readiness_report.md
 M reports/stage2_readiness/readiness_summary.json
?? examples/strategies/indicators/
?? examples/strategies/venue_agnostic_signal_observer/run_stage2_gate_watcher.py
?? examples/strategies/venue_agnostic_signal_observer/systemd/
?? examples/strategies/venue_agnostic_signal_observer/tests/test_stage2_gate_watcher.py
?? reports/cross_capture_consistency_20260514_20260515/
?? reports/research_observatory_preservation_note_20260515T174346.md
?? reports/stage2_gate_watcher_logs/
- **Python:** Python 3.14.5

## Existing V1 / Stage 2 Files Found
- STAGE2_PRECOMMITMENT.md (derivatives_source_spot_target_lead_lag_v2)
- stage2_precommitment.json (derivatives_source_spot_target_lead_lag_v2)
- STAGE2_THRESHOLDS_RATIONALE.md (derivatives_source_spot_target_lead_lag_v2)
- stage2_collection_lock.json: NOT FOUND (no lock exists)
- reports/research_run_index.jsonl: EXISTS
- reports/research_run_quarantine.jsonl: EXISTS
- reports/research_run_burned.jsonl: EXISTS
- reports/stage2_readiness/readiness_summary.json: EXISTS
- reports/stage2_readiness/readiness_report.md: EXISTS
- examples/strategies/venue_agnostic_signal_observer/run_artifacts.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/run_index.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/quarantine.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/burn.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/run_derivatives_capture_campaign.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/validate_capture.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/stage2_precommitment_utils.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/stage2_split_corpus.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/stage2_fdr.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/stage2_readiness_check.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/cross_asset_impulse.py: EXISTS
- examples/strategies/venue_agnostic_signal_observer/run_cross_asset_impulse.py: EXISTS
- examples/strategies/volatility_gate.py: EXISTS
- reports/cross_asset_impulse_v1/: EXISTS (with diagnostic results)

## Hermes Job
- Job ID 85a4d56145fe: Not found in jobs.json (has already executed and been cleaned up)
- Gateway status: ACTIVE (running)

## Preservation Statement
No old data was deleted. No old reports were deleted. No old rejected research entries or previous run artifacts were deleted.

## Safety Statement
No execution logic, no private keys, no order paths, and no broker adapter code were added. Public data observer only.

## Purpose
This note documents the state of the Research Observatory before the cross_asset_beta_lag_v1 precommitment artifacts replace the existing derivative_source_spot_target_lead_lag_v2 precommitment for a new hypothesis cycle.
