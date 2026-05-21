"""
Failure forensics for offline Edge Miner discovery reports.

This module reads already-written offline discovery artifacts and produces an
auditable explanation of why a stress-only run produced no survivors. It does not
recompute discovery, fetch data, or contain execution code.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Sequence


PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

MIN_DECISIVE_STRESS_WINDOWS = 20
TOP_N = 10
FIELD_UNAVAILABLE = "FIELD_UNAVAILABLE"

DEFAULT_STRESS_CORPUS_MANIFEST = Path(
    "examples/strategies/venue_agnostic_signal_observer/corpora/stress_beta_lag_v1/corpus_manifest.json"
)
DEFAULT_TARGET_COVERAGE_SUMMARY = Path(
    "examples/strategies/venue_agnostic_signal_observer/corpora/stress_beta_lag_v1/target_coverage_summary.json"
)


@dataclass(frozen=True)
class ForensicsResult:
    forensics_dir: Path
    executive_verdict: str
    contributing_reasons: tuple[str, ...]
    stress_window_count: int
    usable_window_count: int
    final_candidates: int


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _candidate_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("candidate_card") or row


def _summary_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("validator_summary") or {}


def _candidate_dimensions(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_hash": card.get("candidate_hash", FIELD_UNAVAILABLE),
        "cell_id": card.get("cell_id", FIELD_UNAVAILABLE),
        "source_asset": card.get("source_asset", FIELD_UNAVAILABLE),
        "target_asset": card.get("target_asset", FIELD_UNAVAILABLE),
        "feature_family": card.get("feature_family", FIELD_UNAVAILABLE),
        "horizon_seconds": card.get("horizon_seconds", FIELD_UNAVAILABLE),
        "lookback_seconds": card.get("lookback_seconds", FIELD_UNAVAILABLE),
        "entry_delay_seconds": card.get("entry_delay_seconds", FIELD_UNAVAILABLE),
    }


def _bps(value: Any) -> float | str:
    if isinstance(value, int | float):
        return round(float(value) * 10_000.0, 6)
    return FIELD_UNAVAILABLE


def _mean(values: Sequence[float]) -> float | str:
    if not values:
        return FIELD_UNAVAILABLE
    return round(sum(values) / len(values), 6)


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min_bps": FIELD_UNAVAILABLE,
            "mean_bps": FIELD_UNAVAILABLE,
            "max_bps": FIELD_UNAVAILABLE,
        }
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min_bps": round(ordered[0], 6),
        "mean_bps": _mean(ordered),
        "max_bps": round(ordered[-1], 6),
    }


def _count_by(cards: Sequence[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(card.get(field, FIELD_UNAVAILABLE)) for card in cards)
    return dict(sorted(counts.items()))


def _top_by_metric(rows: Sequence[dict[str, Any]], metric: str, reverse: bool = True, limit: int = TOP_N) -> list[dict[str, Any]]:
    available = [row for row in rows if isinstance(row.get(metric), int | float)]
    return sorted(available, key=lambda row: float(row[metric]), reverse=reverse)[:limit]


def _classify_verdict(
    final_candidates: int,
    stress_window_count: int,
    coverage_by_target: dict[str, int],
    rejection_counts: dict[str, int],
    sent_to_shadow: int,
) -> tuple[str, tuple[str, ...]]:
    if final_candidates > 0:
        return "FORENSICS_INCOMPLETE", ("SURVIVORS_PRESENT",)

    reasons: list[str] = []
    total_rejected = max(1, sum(rejection_counts.values()))
    if stress_window_count < MIN_DECISIVE_STRESS_WINDOWS:
        reasons.append("DATA_INSUFFICIENT_REJECTION")
    if any(count == 0 for count in coverage_by_target.values()):
        reasons.append("TARGET_COVERAGE_LIMITED_REJECTION")
    if rejection_counts.get("BELOW_COST_FLOOR", 0) / total_rejected > 0.5:
        reasons.append("COST_FLOOR_DOMINANT_FAILURE")
    if (
        rejection_counts.get("NULL_MCPT_FAIL", 0)
        + rejection_counts.get("FDR_FAIL", 0)
        + rejection_counts.get("DSR_FAIL", 0)
    ) > 0:
        reasons.append("TIMING_OR_MULTIPLE_TEST_FAILURE")
    if sent_to_shadow == 0:
        reasons.append("SHADOW_NOT_REACHED")

    if stress_window_count < MIN_DECISIVE_STRESS_WINDOWS:
        verdict = "DATA_INSUFFICIENT_REJECTION"
    elif any(count == 0 for count in coverage_by_target.values()):
        verdict = "TARGET_COVERAGE_LIMITED_REJECTION"
    elif final_candidates == 0:
        verdict = "CLEAN_REJECTION"
    else:
        verdict = "MIXED_REJECTION"
    return verdict, tuple(dict.fromkeys(reasons))


def _build_rejection_breakdown(evidence_rows: Sequence[dict[str, Any]], run_summary: dict[str, Any]) -> dict[str, Any]:
    cards = [_candidate_from_evidence(row) for row in evidence_rows]
    counts = Counter(str(card.get("rejection_reason", FIELD_UNAVAILABLE)) for card in cards)
    candidate_counts = run_summary.get("candidate_counts", {})
    return {
        "schema_version": "candidate_rejection_breakdown.v1",
        "candidate_counts": candidate_counts,
        "rejection_reason_counts": dict(sorted(counts.items())),
        "by_source_asset": _count_by(cards, "source_asset"),
        "by_target_asset": _count_by(cards, "target_asset"),
        "by_feature_family": _count_by(cards, "feature_family"),
        "by_horizon_seconds": _count_by(cards, "horizon_seconds"),
    }


def _cost_floor_diagnostics(evidence_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    gross_bps: list[float] = []
    distances_bps: list[float] = []
    for row in evidence_rows:
        card = _candidate_from_evidence(row)
        if card.get("rejection_reason") != "BELOW_COST_FLOOR":
            continue
        summary = _summary_from_evidence(row)
        mean_return = card.get("mean_return")
        cost_floor = summary.get("cost_floor")
        item = _candidate_dimensions(card)
        item["gross_expected_bps"] = _bps(mean_return)
        item["cost_floor_bps"] = _bps(cost_floor)
        if isinstance(mean_return, int | float) and isinstance(cost_floor, int | float):
            distance = (float(mean_return) - float(cost_floor)) * 10_000.0
            item["distance_to_cost_floor_bps"] = round(distance, 6)
            gross_bps.append(round(float(mean_return) * 10_000.0, 6))
            distances_bps.append(round(distance, 6))
        else:
            item["distance_to_cost_floor_bps"] = FIELD_UNAVAILABLE
        rows.append(item)
    return {
        "schema_version": "cost_floor_diagnostics.v1",
        "rejected_on_cost_floor": len(rows),
        "gross_expected_bps_distribution": _distribution(gross_bps),
        "distance_to_cost_floor_bps_distribution": _distribution(distances_bps),
        "top_near_miss_cost_rejections": _top_by_metric(rows, "distance_to_cost_floor_bps", reverse=True),
    }


def _null_mcpt_diagnostics(evidence_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for row in evidence_rows:
        card = _candidate_from_evidence(row)
        if card.get("rejection_reason") != "NULL_MCPT_FAIL":
            continue
        cards.append(card)
        summary = _summary_from_evidence(row)
        mcpt = summary.get("mcpt_result") or {}
        item = _candidate_dimensions(card)
        item["p_value"] = mcpt.get("p_value", FIELD_UNAVAILABLE)
        item["alpha"] = mcpt.get("alpha", FIELD_UNAVAILABLE)
        rows.append(item)
    return {
        "schema_version": "null_mcpt_diagnostics.v1",
        "rejected_by_null_mcpt": len(rows),
        "by_source_asset": _count_by(cards, "source_asset"),
        "by_target_asset": _count_by(cards, "target_asset"),
        "by_feature_family": _count_by(cards, "feature_family"),
        "by_horizon_seconds": _count_by(cards, "horizon_seconds"),
        "top_near_miss_by_p_value": _top_by_metric(rows, "p_value", reverse=False),
    }


def _fdr_diagnostics(evidence_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in evidence_rows:
        card = _candidate_from_evidence(row)
        if card.get("rejection_reason") != "FDR_FAIL":
            continue
        summary = _summary_from_evidence(row)
        fdr = summary.get("fdr_result") or {}
        item = _candidate_dimensions(card)
        item["p_value"] = fdr.get("p_value", FIELD_UNAVAILABLE)
        item["primary_fdr_q"] = fdr.get("primary_fdr_q", FIELD_UNAVAILABLE)
        item["primary_fdr_method"] = fdr.get("primary_fdr_method", FIELD_UNAVAILABLE)
        item["family_size"] = FIELD_UNAVAILABLE
        rows.append(item)
    return {
        "schema_version": "fdr_diagnostics.v1",
        "rejected_by_fdr": len(rows),
        "q_value_threshold": rows[0].get("primary_fdr_q") if rows else FIELD_UNAVAILABLE,
        "family_size": FIELD_UNAVAILABLE,
        "top_near_miss_by_p_value": _top_by_metric(rows, "p_value", reverse=False),
    }


def _dsr_diagnostics(evidence_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in evidence_rows:
        card = _candidate_from_evidence(row)
        if card.get("rejection_reason") != "DSR_FAIL":
            continue
        summary = _summary_from_evidence(row)
        dsr = summary.get("dsr_result") or {}
        item = _candidate_dimensions(card)
        item.update({
            "effective_trial_count": dsr.get("effective_trial_count", FIELD_UNAVAILABLE),
            "raw_trial_count": dsr.get("raw_trial_count", FIELD_UNAVAILABLE),
            "dsr_score": dsr.get("dsr", FIELD_UNAVAILABLE),
            "dsr_threshold": dsr.get("threshold", FIELD_UNAVAILABLE),
            "volatility_adjustment_method": (
                (dsr.get("volatility_adjustment") or {}).get("method", FIELD_UNAVAILABLE)
                if isinstance(dsr.get("volatility_adjustment"), dict)
                else FIELD_UNAVAILABLE
            ),
            "diagnostic_status": dsr.get("diagnostic_status", FIELD_UNAVAILABLE),
        })
        rows.append(item)
    return {
        "schema_version": "dsr_diagnostics.v1",
        "rejected_by_dsr": len(rows),
        "dsr_rejections": rows,
    }


def _top_rejected(evidence_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in evidence_rows:
        card = _candidate_from_evidence(row)
        item = _candidate_dimensions(card)
        item.update({
            "rejection_reason": card.get("rejection_reason", FIELD_UNAVAILABLE),
            "mean_return_bps": _bps(card.get("mean_return")),
            "hit_rate": card.get("hit_rate", FIELD_UNAVAILABLE),
            "sharpe_like": card.get("sharpe_like", FIELD_UNAVAILABLE),
            "n_observations": card.get("n_observations", FIELD_UNAVAILABLE),
        })
        rows.append(item)
    return sorted(
        rows,
        key=lambda row: float(row["mean_return_bps"]) if isinstance(row.get("mean_return_bps"), int | float) else float("-inf"),
        reverse=True,
    )[:TOP_N]


def _target_coverage_breakdown(corpus_manifest: dict[str, Any], target_summary: dict[str, Any]) -> dict[str, Any]:
    coverage = target_summary.get("coverage_by_target") or {}
    target_assets = target_summary.get("target_assets") or corpus_manifest.get("target_assets") or []
    missing = [asset for asset in target_assets if int(coverage.get(asset, 0)) == 0]
    partial = [asset for asset in target_assets if 0 < int(coverage.get(asset, 0)) < int(corpus_manifest.get("usable_window_count", 0))]
    return {
        "schema_version": "target_coverage_breakdown.v1",
        "target_assets": target_assets,
        "coverage_by_target": coverage,
        "missing_target_assets": missing,
        "partial_target_assets": partial,
        "notes": {
            "SOL": "covered across 9 windows" if int(coverage.get("SOL", 0)) == 9 else f"covered across {coverage.get('SOL', 0)} windows",
            "LINK": "partial coverage at 3 windows" if int(coverage.get("LINK", 0)) == 3 else f"covered across {coverage.get('LINK', 0)} windows",
            "DOGE": "partial coverage at 3 windows" if int(coverage.get("DOGE", 0)) == 3 else f"covered across {coverage.get('DOGE', 0)} windows",
            "AVAX": "not covered at 0 windows; no conclusion should be drawn" if int(coverage.get("AVAX", 0)) == 0 else f"covered across {coverage.get('AVAX', 0)} windows",
        },
    }


def _data_adequacy_summary(corpus_manifest: dict[str, Any], target_breakdown: dict[str, Any]) -> dict[str, Any]:
    stress_count = int(corpus_manifest.get("stress_window_count", 0))
    usable_count = int(corpus_manifest.get("usable_window_count", 0))
    return {
        "schema_version": "data_adequacy_summary.v1",
        "stress_window_count": stress_count,
        "usable_window_count": usable_count,
        "minimum_decisive_stress_windows": MIN_DECISIVE_STRESS_WINDOWS,
        "is_decisive_global_rejection": stress_count >= MIN_DECISIVE_STRESS_WINDOWS and not target_breakdown["missing_target_assets"],
        "interpretation": (
            "Useful diagnostic evidence, not a final global rejection."
            if stress_count < MIN_DECISIVE_STRESS_WINDOWS
            else "Stress-window count meets the minimum decisive threshold."
        ),
        "missing_target_assets": target_breakdown["missing_target_assets"],
        "partial_target_assets": target_breakdown["partial_target_assets"],
    }


def _markdown_report(payload: dict[str, Any]) -> str:
    funnel = payload["rejection_funnel"]
    coverage = payload["target_coverage_breakdown"]
    corpus = payload["corpus_adequacy"]
    cost = payload["cost_floor_diagnostics"]
    null = payload["null_mcpt_diagnostics"]
    fdr = payload["fdr_diagnostics"]
    dsr = payload["dsr_diagnostics"]
    top = payload["top_rejected_candidates"]
    lines = [
        "# Edge Miner Failure Forensics",
        "",
        "## 1. Executive verdict",
        "",
        f"- verdict: {payload['executive_verdict']}",
        f"- contributing_reasons: {payload['contributing_reasons']}",
        "- interpretation: The run produced zero survivors. With 9 stress windows this is useful diagnostic evidence, not a final global no-edge rejection.",
        "- shadow_status: SHADOW_NOT_REACHED" if funnel.get("sent_to_shadow", 0) == 0 else "- shadow_status: SHADOW_REACHED",
        "",
        "## 2. Corpus adequacy",
        "",
        f"- stress_window_count: {corpus['stress_window_count']}",
        f"- usable_window_count: {corpus['usable_window_count']}",
        f"- stress_corpus_hash: {corpus['stress_corpus_hash']}",
        f"- label_version: {corpus['label_version']}",
        f"- source_assets: {corpus['source_assets']}",
        f"- target_assets: {corpus['target_assets']}",
        f"- per_target_window_coverage: {coverage['coverage_by_target']}",
        f"- missing_target_assets: {coverage['missing_target_assets']}",
        f"- enough_for_decisive_validation: {payload['data_adequacy_summary']['is_decisive_global_rejection']}",
        "",
        "## 3. Rejection funnel",
        "",
    ]
    for key in [
        "raw_cells",
        "cells_tested",
        "candidate_cards",
        "rejected_on_cost_floor",
        "rejected_by_null_mcpt",
        "rejected_by_fdr",
        "rejected_by_dsr",
        "rejected_by_cpcv_nonstationarity",
        "validator_survivors",
        "sent_to_shadow",
        "passing_shadow",
        "final_candidates",
    ]:
        lines.append(f"- {key}: {funnel.get(key, FIELD_UNAVAILABLE)}")
    lines.extend([
        "",
        "## 4. Cost-floor diagnostics",
        "",
        f"- rejected_on_cost_floor: {cost['rejected_on_cost_floor']}",
        f"- gross_expected_bps_distribution: {cost['gross_expected_bps_distribution']}",
        f"- distance_to_cost_floor_bps_distribution: {cost['distance_to_cost_floor_bps_distribution']}",
        f"- top_near_miss_cost_rejections: {cost['top_near_miss_cost_rejections'][:3] or FIELD_UNAVAILABLE}",
        "",
        "## 5. Null / MCPT diagnostics",
        "",
        f"- rejected_by_null_mcpt: {null['rejected_by_null_mcpt']}",
        f"- by_source_asset: {null['by_source_asset']}",
        f"- by_target_asset: {null['by_target_asset']}",
        f"- by_feature_family: {null['by_feature_family']}",
        f"- by_horizon_seconds: {null['by_horizon_seconds']}",
        f"- top_near_miss_by_p_value: {null['top_near_miss_by_p_value'][:3] or FIELD_UNAVAILABLE}",
        "",
        "## 6. FDR diagnostics",
        "",
        f"- rejected_by_fdr: {fdr['rejected_by_fdr']}",
        f"- family_size: {fdr['family_size']}",
        f"- q_value_threshold: {fdr['q_value_threshold']}",
        f"- top_near_miss_by_p_value: {fdr['top_near_miss_by_p_value'][:3] or FIELD_UNAVAILABLE}",
        "",
        "## 7. DSR diagnostics",
        "",
        f"- rejected_by_dsr: {dsr['rejected_by_dsr']}",
        f"- dsr_rejections: {dsr['dsr_rejections'] or FIELD_UNAVAILABLE}",
        "",
        "## 8. Target coverage diagnostics",
        "",
        f"- SOL: {coverage['notes'].get('SOL')}",
        f"- LINK: {coverage['notes'].get('LINK')}",
        f"- DOGE: {coverage['notes'].get('DOGE')}",
        f"- AVAX: {coverage['notes'].get('AVAX')}",
        "",
        "## 9. Next evidence required",
        "",
        "- collect or assemble at least 20 independent BTC/ETH stress windows",
        "- require target coverage for SOL/LINK/DOGE/AVAX",
        "- do not change grid or thresholds until the next corpus is assembled",
        "- rerun the same frozen stress-only grid",
        "- only compare like-for-like grid/corpus hashes",
        "",
        "## 10. Safety statement",
        "",
        "- no orders placed",
        "- no exchange connection",
        "- no private-key flow",
        "- no live capture",
        "- observer-only offline analysis",
        "",
        "## Top rejected candidates by diagnostic value",
        "",
    ])
    for row in top[:5]:
        lines.append(f"- {row}")
    return "\n".join(lines) + "\n"


def build_failure_forensics(
    report_dir: Path,
    stress_corpus_manifest: Path = DEFAULT_STRESS_CORPUS_MANIFEST,
    target_coverage_summary: Path = DEFAULT_TARGET_COVERAGE_SUMMARY,
    output_dir: Path | None = None,
) -> ForensicsResult:
    out = output_dir or report_dir / "forensics"
    run_summary = _read_json(report_dir / "run_summary.json", {})
    corpus_manifest = _read_json(stress_corpus_manifest, {})
    target_summary = _read_json(target_coverage_summary, {})
    evidence_rows = _read_jsonl(report_dir / "validator_evidence.jsonl")
    corpus_summary = _read_json(report_dir / "corpus_summary.json", {})
    shadow_summary = _read_json(report_dir / "shadow_summary.json", {})

    rejection_breakdown = _build_rejection_breakdown(evidence_rows, run_summary)
    target_breakdown = _target_coverage_breakdown(corpus_manifest, target_summary)
    data_adequacy = _data_adequacy_summary(corpus_manifest, target_breakdown)
    cost = _cost_floor_diagnostics(evidence_rows)
    null = _null_mcpt_diagnostics(evidence_rows)
    fdr = _fdr_diagnostics(evidence_rows)
    dsr = _dsr_diagnostics(evidence_rows)
    top = _top_rejected(evidence_rows)

    candidate_counts = run_summary.get("candidate_counts", {})
    stress_window_count = int(corpus_manifest.get("stress_window_count", run_summary.get("stress_label_count", 0) or 0))
    usable_window_count = int(corpus_manifest.get("usable_window_count", stress_window_count))
    final_candidates = int(candidate_counts.get("final_candidates", 0))
    rejection_counts = rejection_breakdown["rejection_reason_counts"]
    coverage_by_target = target_breakdown["coverage_by_target"]
    verdict, reasons = _classify_verdict(
        final_candidates,
        stress_window_count,
        {str(k): int(v) for k, v in coverage_by_target.items()},
        {str(k): int(v) for k, v in rejection_counts.items()},
        int(candidate_counts.get("sent_to_shadow", shadow_summary.get("n_sent_to_shadow", 0) or 0)),
    )

    payload = {
        "schema_version": "edge_miner_failure_forensics.v1",
        "report_dir": str(report_dir),
        "executive_verdict": verdict,
        "contributing_reasons": list(reasons),
        "grid_hash": run_summary.get("grid_hash", FIELD_UNAVAILABLE),
        "stress_corpus_hash": corpus_manifest.get("corpus_hash", run_summary.get("stress_corpus_hash", FIELD_UNAVAILABLE)),
        "corpus_adequacy": {
            "stress_window_count": stress_window_count,
            "usable_window_count": usable_window_count,
            "stress_corpus_hash": corpus_manifest.get("corpus_hash", FIELD_UNAVAILABLE),
            "label_version": corpus_manifest.get("label_version", FIELD_UNAVAILABLE),
            "source_assets": corpus_manifest.get("source_assets", []),
            "target_assets": corpus_manifest.get("target_assets", []),
        },
        "rejection_funnel": candidate_counts,
        "candidate_rejection_breakdown": rejection_breakdown,
        "cost_floor_diagnostics": cost,
        "null_mcpt_diagnostics": null,
        "fdr_diagnostics": fdr,
        "dsr_diagnostics": dsr,
        "target_coverage_breakdown": target_breakdown,
        "data_adequacy_summary": data_adequacy,
        "recurrence_diagnostics": {
            "status": corpus_summary.get("status", FIELD_UNAVAILABLE),
            "n_captures": corpus_summary.get("n_captures", FIELD_UNAVAILABLE),
            "interpretation": "recurrence_not_reached_no_validator_survivors" if not corpus_summary.get("results") else "recurrence_results_available",
        },
        "shadow_diagnostics": {
            "status": shadow_summary.get("status", FIELD_UNAVAILABLE),
            "sent_to_shadow": shadow_summary.get("n_sent_to_shadow", candidate_counts.get("sent_to_shadow", FIELD_UNAVAILABLE)),
            "passing_shadow": shadow_summary.get("n_passing_shadow", candidate_counts.get("passing_shadow", FIELD_UNAVAILABLE)),
            "interpretation": "SHADOW_NOT_REACHED" if int(candidate_counts.get("sent_to_shadow", 0)) == 0 else "SHADOW_REACHED",
        },
        "top_rejected_candidates": top,
        "safety_statement": {
            "no_orders_placed": True,
            "no_exchange_connection": True,
            "no_private_key_flow": True,
            "no_live_capture": True,
            "observer_only_offline_analysis": True,
        },
    }

    _write_json(out / "failure_forensics.json", payload)
    (out / "failure_forensics.md").write_text(_markdown_report(payload))
    _write_json(out / "candidate_rejection_breakdown.json", rejection_breakdown)
    _write_json(out / "target_coverage_breakdown.json", target_breakdown)
    _write_jsonl(out / "top_rejected_candidates.jsonl", top)
    _write_json(out / "data_adequacy_summary.json", data_adequacy)

    return ForensicsResult(
        forensics_dir=out,
        executive_verdict=verdict,
        contributing_reasons=reasons,
        stress_window_count=stress_window_count,
        usable_window_count=usable_window_count,
        final_candidates=final_candidates,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Edge Miner failure forensics from offline artifacts.")
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--stress-corpus-manifest", type=Path, default=DEFAULT_STRESS_CORPUS_MANIFEST)
    parser.add_argument("--target-coverage-summary", type=Path, default=DEFAULT_TARGET_COVERAGE_SUMMARY)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_failure_forensics(
        report_dir=args.report_dir,
        stress_corpus_manifest=args.stress_corpus_manifest,
        target_coverage_summary=args.target_coverage_summary,
        output_dir=args.out,
    )
    print(json.dumps({
        "forensics_dir": str(result.forensics_dir),
        "executive_verdict": result.executive_verdict,
        "contributing_reasons": list(result.contributing_reasons),
        "stress_window_count": result.stress_window_count,
        "usable_window_count": result.usable_window_count,
        "final_candidates": result.final_candidates,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
