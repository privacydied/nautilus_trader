# Stage 2 Readiness Report

- **Ready for collection:** YES
- **Checked at:** 2026-05-15T02:02:24.074Z
- **Workdir:** /mnt/nasirjones/py/nautilus_trader
- **Git SHA:** 4f28342aadb2-dirty

## Blockers

- None

## Warnings

- None

## Checks

| Check | OK | Reason |
|-------|----|--------|
| module_import:examples.strategies.venue_agnostic_signal_observer.run_artifacts | ✅ | imported examples.strategies.venue_agnostic_signal_observer.run_artifacts (has 25 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.run_derivatives_capture_campaign | ✅ | imported examples.strategies.venue_agnostic_signal_observer.run_derivatives_capture_campaign (has 44 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.validate_capture | ✅ | imported examples.strategies.venue_agnostic_signal_observer.validate_capture (has 46 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.run_report_corpus | ✅ | imported examples.strategies.venue_agnostic_signal_observer.run_report_corpus (has 34 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.run_index | ✅ | imported examples.strategies.venue_agnostic_signal_observer.run_index (has 25 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.quarantine | ✅ | imported examples.strategies.venue_agnostic_signal_observer.quarantine (has 19 attrs) |
| module_import:examples.strategies.venue_agnostic_signal_observer.burn | ✅ | imported examples.strategies.venue_agnostic_signal_observer.burn (has 24 attrs) |
| precommitment_file:stage2_precommitment.json | ✅ | File exists: /mnt/nasirjones/py/nautilus_trader/stage2_precommitment.json |
| precommitment_file:STAGE2_PRECOMMITMENT.md | ✅ | File exists: /mnt/nasirjones/py/nautilus_trader/STAGE2_PRECOMMITMENT.md |
| precommitment_file:STAGE2_THRESHOLDS_RATIONALE.md | ✅ | File exists: /mnt/nasirjones/py/nautilus_trader/STAGE2_THRESHOLDS_RATIONALE.md |
| markdown_json_match | ✅ | All precommit values match |
| pvalue_source | ✅ | p-value source: native_permutation |
| minimum_valid_events_pinned | ✅ | min_valid_events = 50 |
| collection_lock_exists | ✅ | Lock does not exist yet — will be created before first FULL_ACTIVE capture attempt |
| python_path | ✅ | Python: /mnt/nasirjones/py/nautilus_trader/.venv/bin/python |
| git_status | ✅ | SHA: 4f28342aadb2-dirty, dirty: True |
| hermes_gateway | ✅ | Gateway is running |
| hermes_scheduled_job | ✅ | Job 'derivatives-source-spot-target Stage 2 readiness-check-and-collect' (id=85a4d56145fe) enabled=True, state=scheduled |
| quarantine_convention | ✅ | Quarantine dir exists: /mnt/nasirjones/py/nautilus_trader/reports |
| burn_convention | ✅ | Burn dir exists: /mnt/nasirjones/py/nautilus_trader/reports |
