"""
Cross-phase integration tests.

Covers:
- Phase 3 → Phase 1: synthetic no-signal tripwire — Miner sweep on null data
  flows into Validator and must produce DIAGNOSTIC_FAIL (not PASS).
- Validator summary carries fdr_result and mcpt_result fields per plan spec.
- End-to-end: Miner sweep → Validator → Phase 2 ledger event emission →
  Phase 6 bot gate (full pipeline integration).
"""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime

from venue_agnostic_signal_observer.bot import BotGate
from venue_agnostic_signal_observer.governance import EvidenceLedger
from venue_agnostic_signal_observer.governance import make_approval_event
from venue_agnostic_signal_observer.governance import make_candidate_lock_event
from venue_agnostic_signal_observer.governance import make_estimator_evidence_event
from venue_agnostic_signal_observer.governance import make_grid_lock_event
from venue_agnostic_signal_observer.miner import GridCell
from venue_agnostic_signal_observer.miner import GridSpec
from venue_agnostic_signal_observer.miner import build_grid
from venue_agnostic_signal_observer.miner import run_sweep
from venue_agnostic_signal_observer.validator import compute_dsr
from venue_agnostic_signal_observer.validator import make_planted_signal_population
from venue_agnostic_signal_observer.validator import run_validator


def _make_cell(cell_id):
    return GridCell(
        cell_id=cell_id,
        source_venue="binance",
        source_instrument="BTC-USDT-PERP",
        target_venue="kraken",
        target_instrument="BTC-USD",
        entry_delay_seconds=5.0,
        forward_horizon_seconds=180.0,
        signal_type="novel_cross_asset_beta",
    )


class TestSyntheticNoSignalTripwire:
    """Phase 3 invariant: Miner sweep on synthetic null data must be rejected by Validator."""

    def test_null_sweep_fails_validator(self):
        # Build a grid
        spec = GridSpec(name="tripwire", version="1.0", cells=[_make_cell(f"c{i}") for i in range(5)])
        grid = build_grid(spec)

        # Observation function returns null synthetic returns
        import random
        rng = random.Random(0)

        def null_obs(cell):
            return [rng.gauss(0.0, 0.001) for _ in range(200)]

        sweep_result = run_sweep(grid, null_obs)
        assert sweep_result.n_cells_active == 5

        # Pick the cell with the best (lucky) noise mean — what overfit selection would pick
        best_cell = max(sweep_result.cell_results, key=lambda r: r.mean_return or -1)
        assert best_cell.return_series

        # Feed it into Validator
        dsr = compute_dsr(
            returns=best_cell.return_series,
            raw_trial_count=5,
            effective_trial_count=5,  # not clustered → harsh deflation
            dsr_threshold=0.95,
        )
        summary = run_validator(dsr_result=dsr, cost_floor=0.0005)

        # Validator must NOT pass null data
        assert summary.final_diagnostic_status != "DIAGNOSTIC_PASS"

    def test_planted_signal_can_pass_validator(self):
        """Sanity check the opposite: planted signal should be able to pass."""
        obs = make_planted_signal_population(n=300, signal_mean=0.005, noise_scale=0.0008)
        returns = [o.value for o in obs]
        dsr = compute_dsr(
            returns=returns,
            raw_trial_count=5,
            effective_trial_count=2,
            dsr_threshold=0.5,
        )
        assert dsr.diagnostic_status.value == "PASS"


class TestValidatorSummaryFDRandMCPT:
    """ValidatorSummary must accept fdr_result and mcpt_result per plan spec."""

    def test_fdr_result_field_present(self):
        fdr_payload = {
            "primary_fdr_method": "benjamini_hochberg",
            "primary_fdr_q": 0.10,
            "rejected_bh_count": 3,
            "accepted_bh_count": 47,
        }
        summary = run_validator(fdr_result=fdr_payload)
        assert summary.fdr_result == fdr_payload
        assert "fdr" in summary.estimator_versions

    def test_mcpt_result_field_present(self):
        mcpt_payload = {
            "mcpt_version": "1.0.0",
            "n_permutations": 1000,
            "p_value": 0.03,
        }
        summary = run_validator(mcpt_result=mcpt_payload)
        assert summary.mcpt_result == mcpt_payload
        assert "mcpt" in summary.estimator_versions

    def test_summary_to_dict_includes_fdr_and_mcpt(self):
        summary = run_validator(
            fdr_result={"primary_fdr_method": "benjamini_hochberg"},
            mcpt_result={"p_value": 0.05},
        )
        d = summary.to_dict()
        assert "fdr_result" in d
        assert "mcpt_result" in d
        json.dumps(d)  # must be serializable

    def test_a_null_fdr_and_mcpt_still_works(self):
        # Backward compatible: omitting both fields still works
        summary = run_validator()
        assert summary.fdr_result is None
        assert summary.mcpt_result is None


class TestEndToEndPipeline:
    """Full pipeline: Miner → Validator → Phase 2 ledger → Phase 6 bot."""

    def test_miner_validator_ledger_bot_authorizes(self, tmp_path):
        # 1. Miner sweep on planted-signal data
        spec = GridSpec(name="e2e", version="1.0", cells=[_make_cell("c1")])
        grid = build_grid(spec)

        obs_pop = make_planted_signal_population(n=300, signal_mean=0.005, noise_scale=0.0008)

        def planted_obs(cell):
            return [o.value for o in obs_pop]

        sweep = run_sweep(grid, planted_obs)
        cell = sweep.cell_results[0]
        candidate_hash = "candidate_e2e_1"

        # 2. Validator
        dsr = compute_dsr(
            returns=cell.return_series,
            raw_trial_count=1,
            effective_trial_count=1,
            dsr_threshold=0.5,
        )
        summary = run_validator(dsr_result=dsr, cost_floor=0.0005,
                                candidate_hash=candidate_hash,
                                parent_grid_hash=grid.grid_hash)

        # 3. Write to Phase 2 ledger
        ledger_path = tmp_path / "ledger.jsonl"
        ledger = EvidenceLedger(ledger_path)
        ledger.append(make_grid_lock_event(0, grid.grid_hash))
        ledger.append(make_candidate_lock_event(1, candidate_hash, grid.grid_hash))
        ledger.append(make_estimator_evidence_event(
            2, candidate_hash, grid.grid_hash,
            "dsr", "1.0.0", dsr.estimator_metadata.estimator_config_hash,
            {"final_status": summary.final_diagnostic_status},
        ))
        # Approval only if validator passed
        if summary.final_diagnostic_status == "DIAGNOSTIC_PASS":
            ledger.append(make_approval_event(3, candidate_hash, grid.grid_hash))

        # 4. Bot gate check
        from venue_agnostic_signal_observer.bot.manifest import _compute_manifest_hash
        manifest_record = {
            "candidate_hash": candidate_hash,
            "grid_hash": grid.grid_hash,
            "rule_description": "e2e test rule",
            "conditions": {},
            "limits": {"max_position_usd": 1000},
            "manifest_hash": "",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "expires_at_utc": None,
        }
        manifest_record["manifest_hash"] = _compute_manifest_hash([manifest_record])
        manifest_path = tmp_path / "manifest.json"
        with manifest_path.open("w") as f:
            json.dump([manifest_record], f)

        gate = BotGate(ledger_path, manifest_path)
        result = gate.authorize(candidate_hash)

        # Authorization should match validator verdict
        if summary.final_diagnostic_status == "DIAGNOSTIC_PASS":
            assert result.authorized
        else:
            assert not result.authorized
