#!/usr/bin/env python3
"""CLI runner for Hyperliquid funding divergence Phase 0 audit."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer import (
    hyperliquid_funding_divergence_phase0 as h,
)


def _git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except Exception:
        return ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_divergence_phase0",
        description="Hyperliquid funding divergence Phase 0 distribution-only audit",
    )
    p.add_argument("--output-root", default="reports/hyperliquid_funding_divergence_phase0")
    for venue in ["hyperliquid", "binance", "bybit"]:
        for asset in ["btc", "eth"]:
            p.add_argument(f"--{venue}-{asset}", default=None)
    return p


def _empty_outputs(run_dir: Path) -> None:
    h.write_csv(run_dir / "alignment_summary.csv", h.ALIGNMENT_COLUMNS, [])
    h.write_csv(run_dir / "divergence_distribution.csv", h.DIVERGENCE_DISTRIBUTION_COLUMNS, [])
    h.write_csv(run_dir / "divergence_bucket_counts.csv", h.BUCKET_COUNT_COLUMNS, [])
    h.write_csv(run_dir / "persistence_half_life.csv", h.PERSISTENCE_COLUMNS, [])
    h.write_csv(run_dir / "calendar_stratification.csv", h.CALENDAR_COLUMNS, [])


def _report(status: str, kill_decision: str, dist_rows: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    btc = manifest.get("kill_decision_table", {}).get("BTC", {})
    eth = manifest.get("kill_decision_table", {}).get("ETH", {})
    next_rule = {
        h.STATUS_KILLED: "No v1 precommitment should be written.",
        h.STATUS_SINGLE_ASSET: "A future v1 precommitment must decide upfront whether the family covers one asset or both, and the FDR family size must reflect that choice.",
        h.STATUS_READY: "The next artifact must be a separate v1 precommitment. No evaluator may be written from this Phase 0 report alone.",
    }.get(status, "No evaluator may be written from this Phase 0 report alone.")
    lines = [
        "# Hyperliquid funding divergence Phase 0 audit",
        "",
        "## 1. Study identity",
        "Study ID: hyperliquid_funding_divergence_phase0. Distribution-only funding-divergence audit.",
        "",
        "## 2. Not a precommitment",
        "This is not a precommitment, not an evaluator, not a return study, not a PnL study, not shadow execution, and not a bot path.",
        "",
        "## 3. Kill criteria written before histogram",
        "The runner records pre_data_kill_criteria before loading any data. Module section constants define: `KILL_CRITERION_MAX_ABS_BPS = 10.0` and `KILL_CRITERION_P99_ABS_BPS = 8.0`.",
        "",
        "## 4. Data sources and coverage",
        json.dumps(manifest.get("data_source_provenance", []), indent=2),
        "",
        "## 5. Data-source provenance table",
        "See manifest.json data_source_provenance for first row timestamp, last row timestamp, row count, source sha256, detected timestamp unit, and detected native funding interval.",
        "",
        "## 6. Alignment method and no-lookahead proof",
        "Each Hyperliquid timestamp aligns to the latest reference timestamp where reference_timestamp <= hyperliquid_timestamp. Future rows are never eligible; no interpolation or covering-interval logic is used.",
        "",
        "## 7. Funding normalization method",
        "hourly_funding_bps = native_funding_rate * 10000 / native_interval_hours. projected_8h_funding_bps = hourly_funding_bps * 8.",
        "",
        "## 8. Distribution table",
        json.dumps(dist_rows, indent=2),
        "",
        "## 9. Kill decision table",
        f"BTC p99 vs 8 bps: {btc.get('p99_abs_divergence_bps_hourly')}",
        f"BTC max vs 10 bps: {btc.get('max_abs_divergence_bps_hourly')}",
        f"ETH p99 vs 8 bps: {eth.get('p99_abs_divergence_bps_hourly')}",
        f"ETH max vs 10 bps: {eth.get('max_abs_divergence_bps_hourly')}",
        "",
        "## 10. Single-asset distribution-ready interpretation",
        "If exactly one asset clears, future v1 must predeclare one-asset vs two-asset scope and count family size accordingly. Do not silently drop the failing asset later.",
        "",
        "## 11. Persistence / half-life",
        "Reported in persistence_half_life.csv with n_observations, n_censored, and percentile suppression where n < 30.",
        "",
        "## 12. Calendar stratification",
        "Descriptive only. Calendar conditioning cannot create evaluation cells in Phase 0. If used later, it must be frozen in a separate v1 precommitment and counted in family size / FDR.",
        "",
        "## 13. Non-coverage",
        "This is separate from Family 2 funding crowding reversal, Family 3 funding x OI variants, and the Hyperliquid BTC->LINK fixed-cell paper replay.",
        "The invalidated +39.47 bps BTC->LINK replay result is not evidence for this funding-divergence hypothesis.",
        "The corrected +6.13 bps BTC->LINK replay result is not evidence against this funding-divergence hypothesis.",
        "BTC->LINK replay involved cross-asset beta-lag. This audit involves same-asset funding divergence and venue-specific crowding pressure.",
        "",
        "## 14. Self-test paragraph",
        "If this hypothesis advances to v1 and the v1 return leg is BTC spot, the separation risks laundering Family 2 / Family 3 unless the load-bearing mechanism is explicitly the venue-specific crowding pressure on Hyperliquid identified via HL-vs-reference funding divergence, not BTC spot funding extremes alone. Prefer a Hyperliquid perp return leg, Hyperliquid venue crowding, explicit funding-paid-while-held treatment, or another clearly distinct mechanism.",
        "",
        "## 15. Final Phase 0 status",
        status,
        "",
        "## 16. Next-step rule",
        next_rule,
        "",
        "## Appendix: branch handling",
        manifest.get("branch_handling", "remote branch absent; branch created from develop"),
        "",
        f"Kill decision: {kill_decision}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = "hyperliquid_funding_divergence_phase0_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    pre = h.pre_data_kill_criteria()
    manifest: dict[str, Any] = {
        "run_id": run_id, "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git_sha": _git(["rev-parse", "--short=12", "HEAD"]), "git_branch": _git(["branch", "--show-current"]),
        "git_dirty": bool(_git(["status", "--porcelain"])), "command_args": vars(args),
        "pre_data_kill_criteria": pre,
        "data_sources": {}, "data_source_provenance": [], "source_row_counts": {}, "aligned_row_counts": {},
        "interval_ambiguity_detected": False, "safety_mode": h.SAFETY_MODE,
        "no_forward_returns_used": True, "no_pnl_used": True, "no_price_path_after_event_used": True,
        "no_orders": True, "no_private_keys": True, "no_execution": True, "no_shadow_execution": True,
        "no_bot_path": True, "no_null_testing": True, "no_fdr": True, "no_holdout": True,
        "phase0_not_precommitment": True,
        "branch_handling": "remote branch feat/hyperliquid-funding-divergence-phase0 absent at start; created from develop",
    }

    paths = {(venue, asset.upper()): getattr(args, f"{venue}_{asset}") for venue in ["hyperliquid", "binance", "bybit"] for asset in ["btc", "eth"]}
    if not any(paths.values()):
        status = h.STATUS_SOURCE_UNAVAILABLE
        _empty_outputs(run_dir)
        summary = {"final_phase0_status": status, "kill_decision": "source unavailable", "real_data_available": False}
        manifest.update(summary, final_phase0_status=status, kill_decision_table={})
        h.atomic_json(run_dir / "summary.json", summary)
        h.atomic_json(run_dir / "manifest.json", manifest)
        (run_dir / "PHASE0_REPORT.md").write_text(_report(status, "source unavailable", [], manifest), encoding="utf-8")
        print(str(run_dir))
        return 0

    loaded: dict[tuple[str, str], list[h.NormalizedFundingRow]] = {}
    unusable: dict[str, Any] | None = None
    for (venue, asset), p in paths.items():
        if not p: continue
        rows, prov, bad = h.load_funding_file(Path(p), venue, asset)
        loaded[(venue, asset)] = rows
        manifest["data_source_provenance"].append(prov)
        manifest["source_row_counts"][f"{venue}_{asset}"] = len(rows)
        manifest["data_sources"][f"{venue}_{asset}"] = p
        if bad and unusable is None:
            unusable = bad
    if unusable:
        status = h.STATUS_DATA_UNUSABLE
        _empty_outputs(run_dir)
        manifest.update(unusable, final_phase0_status=status)
        summary = {"final_phase0_status": status, "kill_decision": "data unusable", "real_data_available": True}
        h.atomic_json(run_dir / "summary.json", summary); h.atomic_json(run_dir / "manifest.json", manifest)
        (run_dir / "PHASE0_REPORT.md").write_text(_report(status, "data unusable", [], manifest), encoding="utf-8")
        print(str(run_dir)); return 0

    all_aligned: list[h.AlignedDivergenceRow] = []
    alignment_rows = []
    for asset in h.ASSETS:
        hl = loaded.get(("hyperliquid", asset), [])
        refs = {v: loaded[(v, asset)] for v in ["binance", "bybit"] if (v, asset) in loaded}
        for v, rrows in refs.items():
            aligned, missing = h.align_last_observed_reference(hl, rrows, v)
            all_aligned.extend(aligned)
            lags = [r.alignment_lag_seconds for r in aligned]
            alignment_rows.append({"asset": asset, "reference_venue": v, "hyperliquid_rows": len(hl), "aligned_rows": len(aligned), "missing_reference_rows": missing,
                                   "p50_alignment_lag_seconds": h.percentile(lags, 50), "p95_alignment_lag_seconds": h.percentile(lags, 95), "max_alignment_lag_seconds": max(lags) if lags else None, "alignment_status": "OK" if aligned else "NO_ALIGNED_ROWS"})
        if len(refs) >= 2:
            all_aligned.extend(h.align_median_reference(hl, refs))

    grouped: dict[tuple[str, str], list[h.AlignedDivergenceRow]] = {}
    for r in all_aligned: grouped.setdefault((r.asset, r.reference_basis), []).append(r)
    preferred_basis = "median_reference" if any(k[1] == "median_reference" for k in grouped) else ("binance" if any(k[1] == "binance" for k in grouped) else "bybit")
    dist_rows = []
    kill_metrics: dict[str, dict[str, float]] = {}
    for (asset, basis), rows in grouped.items():
        s = h.distribution_summary(rows); s.update(asset=asset, reference_basis=basis); dist_rows.append(s)
        if basis == preferred_basis:
            kill_metrics[asset] = {"count": s["count"], "max": s["max_absolute_divergence_bps_hourly"] or 0.0, "p99": s["p99_absolute_divergence_bps_hourly"] or 0.0}
    status = h.decide_phase0_status(kill_metrics) if set(kill_metrics) >= set(h.ASSETS) else h.STATUS_ALIGNMENT_FAILED
    manifest["aligned_row_counts"] = {f"{a}_{b}": len(r) for (a, b), r in grouped.items()}
    manifest["kill_decision_table"] = {a: {"max_abs_divergence_bps_hourly": kill_metrics.get(a, {}).get("max"), "p99_abs_divergence_bps_hourly": kill_metrics.get(a, {}).get("p99")} for a in h.ASSETS}
    manifest["final_phase0_status"] = status

    h.write_csv(run_dir / "alignment_summary.csv", h.ALIGNMENT_COLUMNS, alignment_rows)
    h.write_csv(run_dir / "divergence_distribution.csv", h.DIVERGENCE_DISTRIBUTION_COLUMNS, dist_rows)
    h.write_csv(run_dir / "divergence_bucket_counts.csv", h.BUCKET_COUNT_COLUMNS, h.compute_bucket_counts(all_aligned))
    h.write_csv(run_dir / "persistence_half_life.csv", h.PERSISTENCE_COLUMNS, h.compute_persistence_half_life(all_aligned))
    h.write_csv(run_dir / "calendar_stratification.csv", h.CALENDAR_COLUMNS, h.compute_calendar_stratification(all_aligned))
    kill_decision = "Phase 0 — killed at distribution audit, no precommitment written." if status == h.STATUS_KILLED else status
    summary = {"final_phase0_status": status, "kill_decision": kill_decision, "real_data_available": True, "preferred_reference_basis": preferred_basis, "kill_decision_table": manifest["kill_decision_table"]}
    h.atomic_json(run_dir / "summary.json", summary); h.atomic_json(run_dir / "manifest.json", manifest)
    (run_dir / "PHASE0_REPORT.md").write_text(_report(status, kill_decision, dist_rows, manifest), encoding="utf-8")
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
