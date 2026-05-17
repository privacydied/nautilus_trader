"""
Validator estimator layer — Phase 1.

Provides DSR, CPCV, PBO/CSCV, purging, time-domain embargo,
effective trial estimation, synthetic data generators, and a
top-level Validator summary.

No diagnostic module in this package may produce TRADE_READY.
No code in this package may import live execution clients.
No private-key env vars are used here.
"""

from .metadata import EstimatorMetadata, make_metadata
from .embargo import purge_train_obs, embargo_train_obs, TimeInterval
from .effective_trials import compute_effective_trial_count
from .dsr import compute_dsr, DSRResult, DiagnosticStatus
from .cpcv import compute_cpcv, CPCVResult
from .pbo import compute_pbo, PBOResult
from .synthetic import (
    make_null_population,
    make_planted_signal_population,
    make_planted_untradeable_population,
    make_decaying_signal_population,
    SyntheticObservation,
)
from .summary import run_validator, ValidatorSummary

__all__ = [
    "EstimatorMetadata",
    "make_metadata",
    "TimeInterval",
    "purge_train_obs",
    "embargo_train_obs",
    "compute_effective_trial_count",
    "DSRResult",
    "DiagnosticStatus",
    "compute_dsr",
    "CPCVResult",
    "compute_cpcv",
    "PBOResult",
    "compute_pbo",
    "SyntheticObservation",
    "make_null_population",
    "make_planted_signal_population",
    "make_planted_untradeable_population",
    "make_decaying_signal_population",
    "ValidatorSummary",
    "run_validator",
]
