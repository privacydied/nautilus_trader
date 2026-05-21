"""
Validator summary — combines all estimator outputs.

No summary status here may be TRADE_READY.
Structured output is required for Phase 2 evidence ledger compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cpcv import CPCVResult
from .dsr import DiagnosticStatus as DSRStatus
from .dsr import DSRResult
from .metadata import EstimatorMetadata
from .metadata import make_metadata
from .pbo import PBOResult


ESTIMATOR_NAME = "validator_summary"
ESTIMATOR_VERSION = "1.0.0"


@dataclass
class ValidatorSummary:
    candidate_hash: str | None
    parent_grid_hash: str | None
    estimator_versions: dict[str, str]
    estimator_config_hashes: dict[str, str]
    input_dataset_hash: str | None
    dsr_result: DSRResult | None
    cpcv_result: CPCVResult | None
    pbo_result: PBOResult | None
    fdr_result: dict[str, Any] | None
    mcpt_result: dict[str, Any] | None
    cost_floor: float | None
    economic_viability_status: str
    statistical_validity_status: str
    nonstationarity_status: str
    final_diagnostic_status: str
    summary_metadata: EstimatorMetadata

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "candidate_hash": self.candidate_hash,
            "parent_grid_hash": self.parent_grid_hash,
            "estimator_versions": self.estimator_versions,
            "estimator_config_hashes": self.estimator_config_hashes,
            "input_dataset_hash": self.input_dataset_hash,
            "cost_floor": self.cost_floor,
            "economic_viability_status": self.economic_viability_status,
            "statistical_validity_status": self.statistical_validity_status,
            "nonstationarity_status": self.nonstationarity_status,
            "final_diagnostic_status": self.final_diagnostic_status,
            "summary_metadata": self.summary_metadata.to_dict(),
            "dsr_result": self.dsr_result.to_dict() if self.dsr_result else None,
            "cpcv_result": self.cpcv_result.to_dict() if self.cpcv_result else None,
            "pbo_result": self.pbo_result.to_dict() if self.pbo_result else None,
            "fdr_result": self.fdr_result,
            "mcpt_result": self.mcpt_result,
        }
        return d


def _econ_viable(
    dsr_result: DSRResult | None,
    cost_floor: float | None,
) -> str:
    """Check economic viability: net-of-cost edge must exceed cost floor."""
    if dsr_result is None:
        return "UNKNOWN"
    if dsr_result.diagnostic_status == DSRStatus.INSUFFICIENT_DATA:
        return "INSUFFICIENT_DATA"
    if dsr_result.mean_return is None:
        return "UNKNOWN"
    if cost_floor is not None and dsr_result.mean_return <= cost_floor:
        return "BELOW_COST_FLOOR"
    return "VIABLE"


def _stat_valid(dsr_result: DSRResult | None) -> str:
    if dsr_result is None:
        return "UNKNOWN"
    status = dsr_result.diagnostic_status
    if status == DSRStatus.PASS:
        return "PASS"
    if status == DSRStatus.FAIL:
        return "FAIL"
    if status == DSRStatus.INSUFFICIENT_DATA:
        return "INSUFFICIENT_DATA"
    return "UNKNOWN"


def _nonstat_status(cpcv_result: CPCVResult | None) -> str:
    if cpcv_result is None:
        return "UNKNOWN"
    diag = cpcv_result.nonstationarity_diagnostics
    if diag.suspected_decay or cpcv_result.fold_stability.monotone_degradation:
        return "DECAY_SUSPECTED"
    return "STABLE"


def _final_status(econ: str, stat: str, nonstat: str) -> str:
    """
    No status here is TRADE_READY. Possible statuses:
      DIAGNOSTIC_PASS - all checks pass (candidate may proceed to governance)
      DIAGNOSTIC_FAIL - at least one check failed
      INSUFFICIENT_DATA - not enough data to conclude
      UNKNOWN - missing estimator results
    Hard failures take precedence over UNKNOWN/INSUFFICIENT_DATA.
    """
    # Hard failures are definitive regardless of missing estimators
    if stat == "FAIL":
        return "DIAGNOSTIC_FAIL"
    if econ == "BELOW_COST_FLOOR":
        return "DIAGNOSTIC_FAIL"
    if nonstat == "DECAY_SUSPECTED":
        return "DIAGNOSTIC_FAIL"
    # Soft blocks
    if "UNKNOWN" in (econ, stat, nonstat):
        return "UNKNOWN"
    if "INSUFFICIENT_DATA" in (econ, stat, nonstat):
        return "INSUFFICIENT_DATA"
    if stat == "PASS" and econ == "VIABLE" and nonstat == "STABLE":
        return "DIAGNOSTIC_PASS"
    return "DIAGNOSTIC_FAIL"


def run_validator(
    dsr_result: DSRResult | None = None,
    cpcv_result: CPCVResult | None = None,
    pbo_result: PBOResult | None = None,
    fdr_result: dict[str, Any] | None = None,
    mcpt_result: dict[str, Any] | None = None,
    cost_floor: float | None = None,
    candidate_hash: str | None = None,
    parent_grid_hash: str | None = None,
    input_dataset_hash: str | None = None,
) -> ValidatorSummary:
    """
    Combine estimator outputs into a structured Validator summary.

    The final_diagnostic_status is never TRADE_READY. A DIAGNOSTIC_PASS
    indicates the candidate may proceed to Phase 2 governance review.
    """
    meta = make_metadata(
        ESTIMATOR_NAME,
        ESTIMATOR_VERSION,
        config={
            "cost_floor": cost_floor,
            "has_dsr": dsr_result is not None,
            "has_cpcv": cpcv_result is not None,
            "has_pbo": pbo_result is not None,
        },
        candidate_hash=candidate_hash,
        parent_grid_hash=parent_grid_hash,
        input_dataset_hash=input_dataset_hash,
    )

    versions: dict[str, str] = {}
    config_hashes: dict[str, str] = {}

    for _name, result in [
        ("dsr", dsr_result),
        ("cpcv", cpcv_result),
        ("pbo", pbo_result),
    ]:
        if result is not None:
            em: EstimatorMetadata = result.estimator_metadata
            versions[em.estimator_name] = em.estimator_version
            config_hashes[em.estimator_name] = em.estimator_config_hash

    econ = _econ_viable(dsr_result, cost_floor)
    stat = _stat_valid(dsr_result)
    nonstat = _nonstat_status(cpcv_result)
    final = _final_status(econ, stat, nonstat)

    if fdr_result is not None:
        versions["fdr"] = fdr_result.get("primary_fdr_method", "unknown")
        config_hashes["fdr"] = str(fdr_result.get("primary_fdr_q", ""))
    if mcpt_result is not None:
        versions["mcpt"] = mcpt_result.get("mcpt_version", "native_permutation")
        config_hashes["mcpt"] = str(mcpt_result.get("n_permutations", ""))

    return ValidatorSummary(
        candidate_hash=candidate_hash,
        parent_grid_hash=parent_grid_hash,
        estimator_versions=versions,
        estimator_config_hashes=config_hashes,
        input_dataset_hash=input_dataset_hash,
        dsr_result=dsr_result,
        cpcv_result=cpcv_result,
        pbo_result=pbo_result,
        fdr_result=fdr_result,
        mcpt_result=mcpt_result,
        cost_floor=cost_floor,
        economic_viability_status=econ,
        statistical_validity_status=stat,
        nonstationarity_status=nonstat,
        final_diagnostic_status=final,
        summary_metadata=meta,
    )
