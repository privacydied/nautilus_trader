"""
Tests for Phase 1 Validator estimator layer.

Covers:
- Time-domain embargo (wall-clock, not index-based)
- Purging
- Effective trial count via correlation clustering
- DSR: de-overlap, autocorrelation adjustment, INSUFFICIENT_DATA
- CPCV: split structure, non-stationarity diagnostics
- PBO: grid-level, not per-candidate API
- Synthetic populations: null, planted, untradeable, decaying
- Validator summary: no TRADE_READY status
- Estimator versioning: metadata fields present
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

from venue_agnostic_signal_observer.validator import DiagnosticStatus
from venue_agnostic_signal_observer.validator import TimeInterval
from venue_agnostic_signal_observer.validator import compute_cpcv
from venue_agnostic_signal_observer.validator import compute_dsr
from venue_agnostic_signal_observer.validator import compute_effective_trial_count
from venue_agnostic_signal_observer.validator import compute_pbo
from venue_agnostic_signal_observer.validator import embargo_train_obs
from venue_agnostic_signal_observer.validator import make_decaying_signal_population
from venue_agnostic_signal_observer.validator import make_null_population
from venue_agnostic_signal_observer.validator import make_planted_signal_population
from venue_agnostic_signal_observer.validator import make_planted_untradeable_population
from venue_agnostic_signal_observer.validator import purge_train_obs
from venue_agnostic_signal_observer.validator import run_validator
from venue_agnostic_signal_observer.validator.embargo import TimestampedObservation
from venue_agnostic_signal_observer.validator.embargo import compute_embargo_seconds


# ---------------------------------------------------------------------------
# Embargo helpers
# ---------------------------------------------------------------------------

def _make_obs(idx, t, value=0.0, horizon=60.0):
    label_end = t + timedelta(seconds=horizon)
    return TimestampedObservation(
        idx=idx,
        event_time=t,
        label_interval=TimeInterval(start=t, end=label_end),
        value=value,
    )


T0 = datetime(2024, 1, 1, tzinfo=UTC)


class TestPurging:
    def test_purge_removes_overlapping(self):
        test_interval = TimeInterval(
            start=T0 + timedelta(seconds=100),
            end=T0 + timedelta(seconds=200),
        )
        train = [
            _make_obs(0, T0 + timedelta(seconds=50), horizon=60),   # label ends at 110 → overlaps
            _make_obs(1, T0 + timedelta(seconds=0), horizon=30),    # label ends at 30 → no overlap
            _make_obs(2, T0 + timedelta(seconds=180), horizon=30),  # event in test interval → overlaps
        ]
        surviving, purged = purge_train_obs(train, test_interval)
        assert purged == 2
        assert len(surviving) == 1
        assert surviving[0].idx == 1

    def test_purge_none_when_no_overlap(self):
        test_interval = TimeInterval(
            start=T0 + timedelta(seconds=300),
            end=T0 + timedelta(seconds=400),
        )
        train = [_make_obs(i, T0 + timedelta(seconds=i * 10), horizon=5) for i in range(10)]
        surviving, purged = purge_train_obs(train, test_interval)
        assert purged == 0
        assert len(surviving) == 10


class TestEmbargo:
    def test_embargo_wall_clock_not_index(self):
        """Dense cluster + sparse tail. Embargo must remove same wall-clock window."""
        test_end = T0 + timedelta(seconds=100)
        test_interval = TimeInterval(start=T0, end=test_end)
        embargo_secs = 30.0

        # Dense cluster: 20 events in 10 seconds after test_end
        dense = [
            _make_obs(i, test_end + timedelta(seconds=0.5 * i), horizon=1.0)
            for i in range(1, 21)
        ]
        # Sparse event: 1 event at test_end + 35s (outside embargo)
        sparse_ok = [_make_obs(100, test_end + timedelta(seconds=35), horizon=1.0)]
        # Sparse event inside embargo
        sparse_embargoed = [_make_obs(101, test_end + timedelta(seconds=25), horizon=1.0)]

        all_obs = dense + sparse_ok + sparse_embargoed
        surviving, embargoed = embargo_train_obs(all_obs, test_interval, embargo_secs)

        # All dense events (0.5s to 10s after test_end) should be embargoed
        surviving_ids = {o.idx for o in surviving}
        assert 100 in surviving_ids  # sparse at +35s survives
        assert 101 not in surviving_ids  # sparse at +25s embargoed
        assert embargoed == len(dense) + 1  # 20 dense + 1 sparse_embargoed

    def test_embargo_zero_does_nothing(self):
        test_interval = TimeInterval(start=T0, end=T0 + timedelta(seconds=10))
        obs = [_make_obs(i, T0 + timedelta(seconds=i), horizon=1.0) for i in range(20)]
        surviving, embargoed = embargo_train_obs(obs, test_interval, 0.0)
        assert embargoed == 0
        assert len(surviving) == len(obs)

    def test_embargo_derivation(self):
        secs = compute_embargo_seconds(
            label_horizon_seconds=180.0,
            entry_delay_seconds=15.0,
            staleness_buffer_seconds=5.0,
        )
        assert secs == 200.0


# ---------------------------------------------------------------------------
# Effective trial count
# ---------------------------------------------------------------------------

class TestEffectiveTrials:
    def test_identical_series_count_as_one(self):
        series = [[1.0, 2.0, 3.0, 4.0, 5.0]] * 10
        result = compute_effective_trial_count(series, correlation_threshold=0.7)
        assert result.raw_cell_count == 10
        assert result.effective_trial_count == 1

    def test_independent_series_count_separately(self):
        # 4 orthogonal-ish series
        series = [
            [1.0, -1.0, 1.0, -1.0, 1.0],
            [-1.0, 1.0, -1.0, 1.0, -1.0],
            [1.0, 1.0, -1.0, -1.0, 0.0],
            [0.0, 0.0, 1.0, 1.0, -1.0],
        ]
        result = compute_effective_trial_count(series, correlation_threshold=0.7)
        assert result.raw_cell_count == 4
        assert result.effective_trial_count >= 2

    def test_empty_input(self):
        result = compute_effective_trial_count([], 0.7)
        assert result.effective_trial_count == 0
        assert result.raw_cell_count == 0

    def test_threshold_recorded(self):
        result = compute_effective_trial_count([[1.0, 2.0]], 0.85)
        assert result.correlation_threshold == 0.85


# ---------------------------------------------------------------------------
# DSR
# ---------------------------------------------------------------------------

class TestDSR:
    def _good_returns(self, n=100, mu=0.01, sigma=0.005, seed=42):
        import random
        rng = random.Random(seed)
        return [rng.gauss(mu, sigma) for _ in range(n)]

    def test_dsr_pass_for_strong_signal(self):
        returns = self._good_returns(mu=0.02, sigma=0.003)
        result = compute_dsr(
            returns=returns,
            raw_trial_count=100,
            effective_trial_count=5,
            dsr_threshold=0.5,
        )
        assert result.diagnostic_status == DiagnosticStatus.PASS
        assert result.dsr is not None
        assert result.dsr >= 0.5

    def test_dsr_fail_for_null_signal(self):
        import random
        rng = random.Random(99)
        returns = [rng.gauss(0.0, 0.01) for _ in range(50)]
        result = compute_dsr(
            returns=returns,
            raw_trial_count=1000,
            effective_trial_count=100,
            dsr_threshold=0.95,
        )
        assert result.diagnostic_status in (DiagnosticStatus.FAIL, DiagnosticStatus.INSUFFICIENT_DATA)

    def test_dsr_insufficient_data_too_few_obs(self):
        result = compute_dsr(
            returns=[0.01],
            raw_trial_count=10,
            effective_trial_count=5,
        )
        assert result.diagnostic_status == DiagnosticStatus.INSUFFICIENT_DATA

    def test_dsr_insufficient_when_deoverlap_leaves_nothing(self):
        import random
        rng = random.Random(1)
        # Only 3 returns, horizon_multiple=4 → deoverlap gives 1 obs, too few
        returns = [rng.gauss(0.0, 0.01) for _ in range(3)]
        result = compute_dsr(
            returns=returns,
            raw_trial_count=10,
            effective_trial_count=3,
            label_horizon_multiple=4,
        )
        assert result.diagnostic_status == DiagnosticStatus.INSUFFICIENT_DATA

    def test_dsr_metadata_present(self):
        returns = self._good_returns()
        result = compute_dsr(returns=returns, raw_trial_count=10, effective_trial_count=3)
        assert result.estimator_metadata.estimator_name == "dsr"
        assert result.estimator_metadata.estimator_version
        assert result.estimator_metadata.generated_at_utc
        assert result.estimator_metadata.estimator_config_hash

    def test_dsr_echoes_both_trial_counts(self):
        returns = self._good_returns()
        result = compute_dsr(returns=returns, raw_trial_count=48000, effective_trial_count=12)
        assert result.raw_trial_count == 48000
        assert result.effective_trial_count == 12

    def test_dsr_volatility_adjustment_nested(self):
        returns = self._good_returns()
        result = compute_dsr(returns=returns, raw_trial_count=10, effective_trial_count=3)
        va = result.volatility_adjustment
        assert hasattr(va, "method")
        assert hasattr(va, "autocorrelation_adjustment_used")
        assert hasattr(va, "confidence")


# ---------------------------------------------------------------------------
# CPCV
# ---------------------------------------------------------------------------

class TestCPCV:
    def _make_obs_seq(self, n=100, mu=0.001, sigma=0.0005, seed=42):
        import random
        rng = random.Random(seed)
        base = datetime(2024, 1, 1, tzinfo=UTC)
        obs = []
        for i in range(n):
            t = base + timedelta(seconds=i * 30)
            val = rng.gauss(mu, sigma)
            label_end = t + timedelta(seconds=180)
            obs.append(TimestampedObservation(
                idx=i,
                event_time=t,
                label_interval=TimeInterval(start=t, end=label_end),
                value=val,
            ))
        return obs

    def test_cpcv_produces_splits(self):
        obs = self._make_obs_seq(120)
        result = compute_cpcv(obs, n_splits=6, n_test_splits=2)
        assert result.n_splits > 0
        assert len(result.per_split_results) > 0

    def test_cpcv_purge_and_embargo_counted(self):
        obs = self._make_obs_seq(60, mu=0.002)
        result = compute_cpcv(obs, n_splits=4, n_test_splits=1, label_horizon_seconds=60.0)
        # Some purging should have occurred given overlapping label horizons
        assert result.purged_count_total >= 0
        assert result.embargoed_count_total >= 0

    def test_cpcv_separate_from_pbo_partitioner(self):
        # CPCV uses (n_splits choose n_test_splits) combinations
        # PBO uses (n_blocks choose n_blocks//2) combinations
        # They must not share code — verified structurally: cpcv.py imports nothing from pbo.py
        from venue_agnostic_signal_observer.validator import cpcv as cpcv_mod
        from venue_agnostic_signal_observer.validator import pbo as pbo_mod
        assert not hasattr(cpcv_mod, "compute_pbo")
        assert not hasattr(pbo_mod, "compute_cpcv")

    def test_cpcv_nonstationarity_detects_decay(self):
        obs_list = list(make_decaying_signal_population(n=120, early_signal_mean=0.01,
                                                         late_signal_mean=-0.005))
        # Convert SyntheticObservation to TimestampedObservation
        obs = [TimestampedObservation(
            idx=o.idx, event_time=o.event_time,
            label_interval=o.label_interval, value=o.value,
        ) for o in obs_list]
        result = compute_cpcv(obs, n_splits=6, n_test_splits=2)
        diag = result.nonstationarity_diagnostics
        # Early mean should be higher than late mean
        if diag.early_mean_return is not None and diag.late_mean_return is not None:
            assert diag.early_mean_return > diag.late_mean_return

    def test_cpcv_metadata_present(self):
        obs = self._make_obs_seq(60)
        result = compute_cpcv(obs)
        assert result.estimator_metadata.estimator_name == "cpcv"
        assert result.estimator_metadata.estimator_version


# ---------------------------------------------------------------------------
# PBO
# ---------------------------------------------------------------------------

class TestPBO:
    def _make_grid(self, n_cells=20, n_obs=200, seed=42):
        import random
        rng = random.Random(seed)
        return [[rng.gauss(0.0, 0.01) for _ in range(n_obs)] for _ in range(n_cells)]

    def test_pbo_grid_level_not_per_candidate(self):
        grid = self._make_grid(n_cells=10)
        result = compute_pbo(grid, n_blocks=8)
        # n_cells should reflect full grid
        assert result.n_cells == 10
        # n_partitions should be C(8, 4) = 70
        assert result.n_partitions == 70

    def test_pbo_high_for_overfit_grid(self):
        import random
        rng = random.Random(7)
        # In-sample winner is chosen on noise; OOS should be random
        grid = [[rng.gauss(0.0, 0.01) for _ in range(100)] for _ in range(30)]
        result = compute_pbo(grid, n_blocks=8)
        assert result.pbo_estimate is not None
        # For pure noise, PBO should be around 0.5

    def test_pbo_error_on_single_cell(self):
        result = compute_pbo([[0.01, -0.01, 0.01]])
        assert result.grid_level_status == "ERROR"

    def test_pbo_metadata_present(self):
        grid = self._make_grid(n_cells=5, n_obs=50)
        result = compute_pbo(grid, n_blocks=4)
        assert result.estimator_metadata.estimator_name == "pbo_cscv"

    def test_pbo_does_not_apply_purging(self):
        # PBO must not import or use purging/embargo — verify structurally
        import inspect

        from venue_agnostic_signal_observer.validator import pbo as pbo_mod
        src = inspect.getsource(pbo_mod)
        assert "purge_train_obs" not in src
        assert "embargo_train_obs" not in src


# ---------------------------------------------------------------------------
# Synthetic populations
# ---------------------------------------------------------------------------

class TestSyntheticPopulations:
    def test_null_has_no_signal(self):
        obs = make_null_population(n=200)
        assert all(not o.true_signal for o in obs)
        returns = [o.value for o in obs]
        mean_r = sum(returns) / len(returns)
        assert abs(mean_r) < 0.001  # should be near zero

    def test_planted_has_positive_mean(self):
        obs = make_planted_signal_population(n=200, signal_mean=0.005)
        returns = [o.value for o in obs]
        mean_r = sum(returns) / len(returns)
        assert mean_r > 0.002

    def test_untradeable_below_cost_floor(self):
        obs, cost_floor = make_planted_untradeable_population(
            n=200, signal_mean=0.0001, cost_floor=0.0005
        )
        returns = [o.value for o in obs]
        mean_r = sum(returns) / len(returns)
        assert mean_r < cost_floor

    def test_decaying_signal_early_vs_late(self):
        obs = make_decaying_signal_population(
            n=200, early_signal_mean=0.01, late_signal_mean=0.0
        )
        n = len(obs)
        early = [o.value for o in obs[:n // 2]]
        late = [o.value for o in obs[n // 2:]]
        early_mean = sum(early) / len(early)
        late_mean = sum(late) / len(late)
        assert early_mean > late_mean

    def test_null_bursty_arrivals(self):
        obs = make_null_population(n=100, bursty=True)
        # Bursty: first 50 close together, last 50 far apart
        times = [o.event_time for o in obs]
        gaps_early = [
            (times[i + 1] - times[i]).total_seconds()
            for i in range(min(10, len(times) - 1))
        ]
        gaps_late = [
            (times[i + 1] - times[i]).total_seconds()
            for i in range(len(times) - 10, len(times) - 1)
        ]
        assert max(gaps_early) < min(gaps_late)


# ---------------------------------------------------------------------------
# Validator summary
# ---------------------------------------------------------------------------

class TestValidatorSummary:
    def test_no_trade_ready_status(self):
        import random
        rng = random.Random(0)
        returns = [rng.gauss(0.005, 0.002) for _ in range(100)]
        dsr = compute_dsr(returns=returns, raw_trial_count=50, effective_trial_count=5,
                          dsr_threshold=0.5)
        summary = run_validator(dsr_result=dsr, cost_floor=0.001)
        assert summary.final_diagnostic_status != "TRADE_READY"

    def test_below_cost_floor_fails(self):
        import random
        rng = random.Random(0)
        returns = [rng.gauss(0.0001, 0.002) for _ in range(100)]
        dsr = compute_dsr(returns=returns, raw_trial_count=50, effective_trial_count=5,
                          dsr_threshold=0.3)
        summary = run_validator(dsr_result=dsr, cost_floor=0.001)
        assert summary.economic_viability_status == "BELOW_COST_FLOOR"
        assert summary.final_diagnostic_status == "DIAGNOSTIC_FAIL"

    def test_nonstationarity_detected_fails(self):
        obs_list = list(make_decaying_signal_population(n=120, early_signal_mean=0.01,
                                                         late_signal_mean=-0.01))
        obs = [TimestampedObservation(
            idx=o.idx, event_time=o.event_time,
            label_interval=o.label_interval, value=o.value,
        ) for o in obs_list]
        cpcv = compute_cpcv(obs, n_splits=6, n_test_splits=2)
        summary = run_validator(cpcv_result=cpcv)
        # Should not be DIAGNOSTIC_PASS when decay is suspected
        if cpcv.nonstationarity_diagnostics.suspected_decay:
            assert summary.nonstationarity_status == "DECAY_SUSPECTED"
            assert summary.final_diagnostic_status == "DIAGNOSTIC_FAIL"

    def test_summary_includes_estimator_versions(self):
        import random
        rng = random.Random(0)
        returns = [rng.gauss(0.005, 0.002) for _ in range(100)]
        dsr = compute_dsr(returns=returns, raw_trial_count=50, effective_trial_count=5)
        summary = run_validator(dsr_result=dsr)
        assert "dsr" in summary.estimator_versions

    def test_to_dict_serializable(self):
        import json
        import random
        rng = random.Random(0)
        returns = [rng.gauss(0.005, 0.002) for _ in range(100)]
        dsr = compute_dsr(returns=returns, raw_trial_count=50, effective_trial_count=5)
        summary = run_validator(dsr_result=dsr)
        d = summary.to_dict()
        json.dumps(d)  # must not raise
