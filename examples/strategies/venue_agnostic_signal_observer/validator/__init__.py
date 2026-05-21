"""
Validator estimator layer — Phase 1.

Provides DSR, CPCV, PBO/CSCV, purging, time-domain embargo,
effective trial estimation, synthetic data generators, and a
top-level Validator summary.

No diagnostic module in this package may produce TRADE_READY.
No code in this package may import live execution clients.
No private-key env vars are used here.
"""

from .cpcv import CPCVResult
from .cpcv import compute_cpcv
from .dsr import DiagnosticStatus
from .dsr import DSRResult
from .dsr import compute_dsr
from .effective_trials import compute_effective_trial_count
from .embargo import TimeInterval
from .embargo import embargo_train_obs
from .embargo import purge_train_obs
from .fdr import FDRResult
from .fdr import compute_fdr
from .metadata import EstimatorMetadata
from .metadata import make_metadata
from .pbo import PBOResult
from .pbo import compute_pbo
from .summary import ValidatorSummary
from .summary import run_validator
from .synthetic import SyntheticObservation
from .synthetic import make_decaying_signal_population
from .synthetic import make_null_population
from .synthetic import make_planted_signal_population
from .synthetic import make_planted_untradeable_population


__all__ = [
    "CPCVResult",
    "DSRResult",
    "DiagnosticStatus",
    "EstimatorMetadata",
    "FDRResult",
    "PBOResult",
    "SyntheticObservation",
    "TimeInterval",
    "ValidatorSummary",
    "compute_cpcv",
    "compute_dsr",
    "compute_effective_trial_count",
    "compute_fdr",
    "compute_pbo",
    "embargo_train_obs",
    "make_decaying_signal_population",
    "make_metadata",
    "make_null_population",
    "make_planted_signal_population",
    "make_planted_untradeable_population",
    "purge_train_obs",
    "run_validator",
]
