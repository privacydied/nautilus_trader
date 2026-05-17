"""
Synthetic data generators for Validator testing.

Four populations required by the plan:
  A. Null / no-signal
  B. Planted signal (real, tradeable)
  C. Planted-but-untradeable (real effect, below cost floor)
  D. Decaying signal (real early, weakens/vanishes later)

These generators exist so Validator estimators can be tested on known-truth
data before any real edge exists. This is the oracle-before-miner approach.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Sequence

from .embargo import TimeInterval, TimestampedObservation


@dataclass(frozen=True)
class SyntheticObservation(TimestampedObservation):
    """TimestampedObservation with ground-truth label."""
    true_signal: bool  # whether the true DGP had a signal at this point


def _make_obs(
    idx: int,
    event_time: datetime,
    label_horizon_seconds: float,
    value: float,
    true_signal: bool,
) -> SyntheticObservation:
    label_end = event_time + timedelta(seconds=label_horizon_seconds)
    return SyntheticObservation(
        idx=idx,
        event_time=event_time,
        label_interval=TimeInterval(start=event_time, end=label_end),
        value=value,
        true_signal=true_signal,
    )


def _bursty_times(
    n_dense: int,
    n_sparse: int,
    start: datetime,
    dense_interval_seconds: float = 1.0,
    sparse_interval_seconds: float = 60.0,
) -> list[datetime]:
    """Generate bursty arrival times: dense cluster then sparse tail."""
    times = []
    t = start
    for _ in range(n_dense):
        times.append(t)
        t += timedelta(seconds=dense_interval_seconds)
    for _ in range(n_sparse):
        times.append(t)
        t += timedelta(seconds=sparse_interval_seconds)
    return times


def make_null_population(
    n: int = 200,
    noise_scale: float = 0.001,
    label_horizon_seconds: float = 180.0,
    seed: int = 42,
    start: datetime | None = None,
    bursty: bool = False,
) -> list[SyntheticObservation]:
    """No true edge. Validator should reject or refuse promotion."""
    rng = random.Random(seed)
    base = start or datetime(2024, 1, 1, tzinfo=UTC)

    if bursty:
        times = _bursty_times(n // 2, n - n // 2, base)
    else:
        times = [base + timedelta(seconds=i * 10) for i in range(n)]

    return [
        _make_obs(
            idx=i,
            event_time=times[i],
            label_horizon_seconds=label_horizon_seconds,
            value=rng.gauss(0.0, noise_scale),
            true_signal=False,
        )
        for i in range(n)
    ]


def make_planted_signal_population(
    n: int = 200,
    signal_mean: float = 0.002,
    noise_scale: float = 0.001,
    label_horizon_seconds: float = 180.0,
    seed: int = 42,
    start: datetime | None = None,
) -> list[SyntheticObservation]:
    """True positive signal with sufficient effect size.
    Validator should score more favorably than null.
    signal_mean >> noise_scale for a clear planted effect.
    """
    rng = random.Random(seed)
    base = start or datetime(2024, 1, 1, tzinfo=UTC)
    times = [base + timedelta(seconds=i * 10) for i in range(n)]
    return [
        _make_obs(
            idx=i,
            event_time=times[i],
            label_horizon_seconds=label_horizon_seconds,
            value=signal_mean + rng.gauss(0.0, noise_scale),
            true_signal=True,
        )
        for i in range(n)
    ]


def make_planted_untradeable_population(
    n: int = 200,
    signal_mean: float = 0.0001,
    noise_scale: float = 0.001,
    cost_floor: float = 0.0005,
    label_horizon_seconds: float = 180.0,
    seed: int = 42,
    start: datetime | None = None,
) -> tuple[list[SyntheticObservation], float]:
    """Statistically real effect but below cost floor.
    Statistical diagnostics may detect the effect. Verdict logic must
    refuse promotion because net-of-cost edge is dead.
    Returns (observations, cost_floor).
    """
    rng = random.Random(seed)
    base = start or datetime(2024, 1, 1, tzinfo=UTC)
    times = [base + timedelta(seconds=i * 10) for i in range(n)]
    obs = [
        _make_obs(
            idx=i,
            event_time=times[i],
            label_horizon_seconds=label_horizon_seconds,
            value=signal_mean + rng.gauss(0.0, noise_scale),
            true_signal=True,
        )
        for i in range(n)
    ]
    return obs, cost_floor


def make_decaying_signal_population(
    n: int = 200,
    early_signal_mean: float = 0.003,
    late_signal_mean: float = 0.0,
    noise_scale: float = 0.001,
    label_horizon_seconds: float = 180.0,
    decay_start_fraction: float = 0.5,
    seed: int = 42,
    start: datetime | None = None,
) -> list[SyntheticObservation]:
    """Real signal in early data; weakens or vanishes in late data.
    CPCV and purged folds should expose the non-stationarity.
    Naive in-sample fitting should look better than honest OOS.
    """
    rng = random.Random(seed)
    base = start or datetime(2024, 1, 1, tzinfo=UTC)
    times = [base + timedelta(seconds=i * 10) for i in range(n)]
    decay_idx = int(n * decay_start_fraction)
    obs = []
    for i in range(n):
        if i < decay_idx:
            mu = early_signal_mean
        else:
            frac = (i - decay_idx) / max(1, n - decay_idx)
            mu = early_signal_mean * (1.0 - frac) + late_signal_mean * frac
        obs.append(
            _make_obs(
                idx=i,
                event_time=times[i],
                label_horizon_seconds=label_horizon_seconds,
                value=mu + rng.gauss(0.0, noise_scale),
                true_signal=i < decay_idx,
            )
        )
    return obs
