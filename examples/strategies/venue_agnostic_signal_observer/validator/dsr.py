"""
Deflated Sharpe Ratio (DSR) — candidate-level deflated performance diagnostic.

Consumes effective_trial_count, not raw grid cell count.
Handles non-IID returns via de-overlapping or autocorrelation adjustment.
Returns INSUFFICIENT_DATA when observations are too few for a reliable estimate.

No verdict produced here is TRADE_READY.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Sequence

from .metadata import EstimatorMetadata


ESTIMATOR_NAME = "dsr"
ESTIMATOR_VERSION = "1.0.0"

MIN_OBS_FOR_AC_ADJUSTED = 10
MIN_OBS_FOR_DEOVERLAP = 5


class DiagnosticStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ERROR = "ERROR"


@dataclass
class VolatilityAdjustment:
    method: str  # deoverlap | autocorrelation_adjusted | none
    autocorrelation_adjustment_used: bool
    deoverlap_method: str | None
    adjusted_sharpe: float | None
    confidence: str  # normal | lower_confidence | insufficient_data
    reason: str | None


@dataclass
class DSRResult:
    dsr: float | None
    sharpe: float | None
    mean_return: float | None
    volatility: float | None
    effective_sample_size: int | None
    skew: float | None
    kurtosis: float | None
    raw_trial_count: int
    effective_trial_count: int
    effective_trial_method: str
    effective_trial_correlation_threshold: float
    threshold: float
    diagnostic_status: DiagnosticStatus
    volatility_adjustment: VolatilityAdjustment
    estimator_metadata: EstimatorMetadata
    input_metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["diagnostic_status"] = self.diagnostic_status.value
        return d


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _skewness(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    m = _mean(xs)
    s = _std(xs)
    if s == 0.0:
        return 0.0
    return sum(((x - m) / s) ** 3 for x in xs) / n


def _excess_kurtosis(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 4:
        return 0.0
    m = _mean(xs)
    s = _std(xs)
    if s == 0.0:
        return 0.0
    return sum(((x - m) / s) ** 4 for x in xs) / n - 3.0


def _lag1_autocorr(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, n))
    denom = sum((x - m) ** 2 for x in xs)
    if denom == 0.0:
        return 0.0
    return num / denom


def _deoverlap(returns: Sequence[float], horizon_multiple: int) -> list[float]:
    """Take every horizon_multiple-th return to remove overlap."""
    return [returns[i] for i in range(0, len(returns), horizon_multiple)]


def _autocorr_adjusted_sharpe(
    sharpe: float,
    rho1: float,
    n: int,
) -> float:
    """Adjust Sharpe for lag-1 autocorrelation using Lo (2002) approximation."""
    adjustment = math.sqrt(1 + 2 * rho1 / (1 - rho1)) if abs(rho1) < 1.0 else 1.0
    return sharpe / adjustment if adjustment != 0.0 else sharpe


def _psr(sharpe: float, sharpe_benchmark: float, skew: float, kurt: float, n: int) -> float:
    """Probabilistic Sharpe Ratio — probability that true SR > benchmark SR."""
    if n <= 1:
        return 0.0
    var = (1 - skew * sharpe + ((kurt - 1) / 4) * sharpe ** 2) / (n - 1)
    if var <= 0:
        return 0.5
    z = (sharpe - sharpe_benchmark) / math.sqrt(var)
    return _standard_normal_cdf(z)


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _dsr_value(
    sharpe: float,
    skew: float,
    kurt: float,
    n: int,
    effective_trial_count: int,
) -> float:
    """Deflated Sharpe Ratio using Bailey & Lopez de Prado (2014)."""
    if effective_trial_count <= 0 or n <= 1:
        return 0.0
    # Expected maximum SR across effective_trial_count independent trials
    # approximation: E[max SR] ≈ (1 - euler_gamma) * Phi^-1(1 - 1/effective_trial_count)
    #                             + euler_gamma * Phi^-1(1 - 1/(effective_trial_count * e))
    euler_gamma = 0.5772156649
    e = math.e
    if effective_trial_count == 1:
        sr_star = 0.0
    else:
        z1 = _probit(1.0 - 1.0 / effective_trial_count)
        z2 = _probit(1.0 - 1.0 / (effective_trial_count * e))
        sr_star = (1.0 - euler_gamma) * z1 + euler_gamma * z2
    return _psr(sharpe, sr_star, skew, kurt, n)


def _probit(p: float) -> float:
    """Inverse normal CDF using rational approximation (Beasley-Springer-Moro)."""
    p = max(1e-10, min(1 - 1e-10, p))
    if p < 0.5:
        return -_rational_approx(math.sqrt(-2.0 * math.log(p)))
    return _rational_approx(math.sqrt(-2.0 * math.log(1.0 - p)))


def _rational_approx(t: float) -> float:
    c = [2.515517, 0.802853, 0.010328]
    d = [1.432788, 0.189269, 0.001308]
    return t - (c[0] + c[1] * t + c[2] * t ** 2) / (
        1.0 + d[0] * t + d[1] * t ** 2 + d[2] * t ** 3
    )


def compute_dsr(
    returns: Sequence[float],
    raw_trial_count: int,
    effective_trial_count: int,
    effective_trial_method: str = "correlation_cluster",
    effective_trial_correlation_threshold: float = 0.7,
    dsr_threshold: float = 0.95,
    label_horizon_multiple: int = 1,
    estimator_metadata: EstimatorMetadata | None = None,
    input_metadata: dict[str, Any] | None = None,
) -> DSRResult:
    """Compute Deflated Sharpe Ratio for a candidate return series.

    Parameters
    ----------
    returns:
        Raw return series (may be overlapping if label_horizon_multiple > 1).
    raw_trial_count:
        Total number of grid cells (not used as DSR denominator).
    effective_trial_count:
        Number of correlation clusters (used as DSR trial denominator).
    label_horizon_multiple:
        If > 1, returns are overlapping at this multiple. De-overlap is
        attempted first; autocorrelation adjustment is fallback.
    dsr_threshold:
        DSR value above which PASS is returned.
    """
    from .metadata import make_metadata

    meta = estimator_metadata or make_metadata(
        ESTIMATOR_NAME,
        ESTIMATOR_VERSION,
        config={
            "dsr_threshold": dsr_threshold,
            "label_horizon_multiple": label_horizon_multiple,
            "effective_trial_correlation_threshold": effective_trial_correlation_threshold,
        },
    )
    inp = input_metadata or {}

    def _insufficient(reason: str) -> DSRResult:
        return DSRResult(
            dsr=None,
            sharpe=None,
            mean_return=None,
            volatility=None,
            effective_sample_size=None,
            skew=None,
            kurtosis=None,
            raw_trial_count=raw_trial_count,
            effective_trial_count=effective_trial_count,
            effective_trial_method=effective_trial_method,
            effective_trial_correlation_threshold=effective_trial_correlation_threshold,
            threshold=dsr_threshold,
            diagnostic_status=DiagnosticStatus.INSUFFICIENT_DATA,
            volatility_adjustment=VolatilityAdjustment(
                method="none",
                autocorrelation_adjustment_used=False,
                deoverlap_method=None,
                adjusted_sharpe=None,
                confidence="insufficient_data",
                reason=reason,
            ),
            estimator_metadata=meta,
            input_metadata=inp,
            error_message=reason,
        )

    if len(returns) < 2:
        return _insufficient("Too few observations to compute DSR")

    if effective_trial_count <= 0:
        return _insufficient("effective_trial_count must be >= 1")

    # --- Overlap handling ---
    working_returns = list(returns)
    va_method = "none"
    va_ac_used = False
    va_deoverlap_method: str | None = None
    va_adjusted_sharpe: float | None = None
    va_confidence = "normal"
    va_reason: str | None = None

    if label_horizon_multiple > 1:
        deoverlapped = _deoverlap(returns, label_horizon_multiple)
        if len(deoverlapped) >= MIN_OBS_FOR_DEOVERLAP:
            working_returns = deoverlapped
            va_method = "deoverlap"
            va_deoverlap_method = f"stride_{label_horizon_multiple}"
        else:
            # Fall back to autocorrelation adjustment
            if len(returns) >= MIN_OBS_FOR_AC_ADJUSTED:
                rho1 = _lag1_autocorr(returns)
                raw_sharpe = (
                    _mean(returns) / _std(returns)
                    if _std(returns) > 0 else 0.0
                )
                adj_sharpe = _autocorr_adjusted_sharpe(raw_sharpe, rho1, len(returns))
                va_method = "autocorrelation_adjusted"
                va_ac_used = True
                va_adjusted_sharpe = adj_sharpe
                va_confidence = "lower_confidence"
                va_reason = (
                    f"De-overlap left only {len(deoverlapped)} obs "
                    f"(< {MIN_OBS_FOR_DEOVERLAP}); used AC-adjusted Sharpe"
                )
            else:
                return _insufficient(
                    f"De-overlap left {len(deoverlapped)} obs and raw series has "
                    f"only {len(returns)} obs (< {MIN_OBS_FOR_AC_ADJUSTED}) "
                    "for autocorrelation adjustment"
                )

    n = len(working_returns)
    if n < 2:
        return _insufficient(f"Only {n} observations after overlap handling")

    mu = _mean(working_returns)
    sigma = _std(working_returns)
    if sigma == 0.0:
        return _insufficient("Zero volatility in return series")

    sharpe = mu / sigma
    if va_ac_used and va_adjusted_sharpe is not None:
        sharpe = va_adjusted_sharpe

    skew = _skewness(working_returns)
    kurt = _excess_kurtosis(working_returns)
    dsr_val = _dsr_value(sharpe, skew, kurt, n, effective_trial_count)

    status = DiagnosticStatus.PASS if dsr_val >= dsr_threshold else DiagnosticStatus.FAIL

    return DSRResult(
        dsr=round(dsr_val, 6),
        sharpe=round(sharpe, 6),
        mean_return=round(mu, 8),
        volatility=round(sigma, 8),
        effective_sample_size=n,
        skew=round(skew, 6),
        kurtosis=round(kurt, 6),
        raw_trial_count=raw_trial_count,
        effective_trial_count=effective_trial_count,
        effective_trial_method=effective_trial_method,
        effective_trial_correlation_threshold=effective_trial_correlation_threshold,
        threshold=dsr_threshold,
        diagnostic_status=status,
        volatility_adjustment=VolatilityAdjustment(
            method=va_method,
            autocorrelation_adjustment_used=va_ac_used,
            deoverlap_method=va_deoverlap_method,
            adjusted_sharpe=va_adjusted_sharpe,
            confidence=va_confidence,
            reason=va_reason,
        ),
        estimator_metadata=meta,
        input_metadata=inp,
    )
