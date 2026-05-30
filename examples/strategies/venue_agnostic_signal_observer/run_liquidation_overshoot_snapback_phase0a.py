"""CLI runner for Hyperliquid cascade overshoot snapback Phase -2 + Phase 0A v0.

Usage:
    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_liquidation_overshoot_snapback_phase0a \\
      --out-root reports/liquidation_overshoot_snapback_phase0a \\
      --data-root data/hyperliquid \\
      --symbols AAVE,ADA,APT,ARB,ATOM,AVAX,DOGE,DOT,ENA,INJ,JUP,LINK,LTC,NEAR,ONDO,OP,PENDLE,SEI,SOL,SUI,TIA,TRX,UNI,WIF,WLD,XRP \\
      --start-date 2025-10-01 \\
      --end-date 2025-10-31 \\
      --positive-control-window 2025-10-10T15:00:00Z,2025-10-10T20:00:00Z \\
      --run-phase-minus2 \\
      --run-phase0a \\
      --max-download-bytes 25000000000 \\
      --min-free-disk-gib 25 \\
      --allow-s3-archive-read \\
      --allow-network-public \\
      --exact-liquidation-required false
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_overshoot_snapback_phase0a import (
    BLOCKED_LIQUIDATION_ATTRIBUTION,
    CASCADE_PROXY_READY,
    EVENT_UNIVERSE,
    FORBIDDEN_STATUSES,
    FUNDING_NOT_INCLUDED,
    PHASE0A_DIAGNOSTIC_PASS,
    PHASE0A_ERROR,
    PHASE0A_UNDERPOWERED,
    PHASE_MINUS2_ERROR,
    STUDY_ID,
    Phase0AResult,
    PhaseMinus2Result,
    compute_precommitment_hash,
    run_phase0a,
    run_phase_minus2,
    utc_iso,
    write_report_artifacts,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    create_run_dir,
    create_run_id,
)


def _build_summary(
    run_id: str,
    git_sha: str,
    git_branch: str,
    git_dirty: bool,
    repo_root: str,
    command_args: list[str],
    phase_minus2: PhaseMinus2Result,
    phase0a: Phase0AResult | None,
    symbols_requested: list[str],
    symbols_evaluated: list[str],
    symbols_excluded: dict[str, str],
    exact_liquidation_required: bool,
) -> dict:
    precommitment_hash = compute_precommitment_hash()

    # Determine final status
    if phase0a is not None:
        final_status = phase0a.status
    else:
        final_status = phase_minus2.status

    # Build fill counts by model
    fill_counts = {}
    if phase0a is not None:
        for e in phase0a.passive_fill_events:
            if e.filled:
                key = f"{e.fill_model}_offset_{e.offset_bps}"
                fill_counts[key] = fill_counts.get(key, 0) + 1

    # Primary metrics
    primary_metrics = {}
    if phase0a is not None:
        for m in phase0a.fill_model_metrics:
            if m["offset_bps"] == 75.0 and m["horizon_minutes"] == 60:
                primary_metrics[m["fill_model"]] = m

    # Overlap summary
    overlap_summary = {}
    for r in phase_minus2.overlap_reports:
        sym = r["symbol"] if isinstance(r, dict) else r.symbol
        overlap_summary[sym] = {
            "overlap_hours": r["overlap_hours_available"] if isinstance(r, dict) else r.overlap_hours_available,
            "missing_reason": r["missing_reason"] if isinstance(r, dict) else r.missing_reason,
        }

    # Check survivorship
    survivorship_ambiguity = len(symbols_evaluated) < len(EVENT_UNIVERSE) * 0.8

    summary = {
        "study_id": STUDY_ID,
        "run_id": run_id,
        "created_at_utc": utc_iso(datetime.now(UTC)),
        "git_sha": git_sha,
        "git_branch": git_branch,
        "git_dirty": git_dirty,
        "repo_root": repo_root,
        "command_args": command_args,
        "precommitment_hash": precommitment_hash,
        "exact_liquidation_required": exact_liquidation_required,
        "exact_liquidation_available": phase_minus2.exact_liquidation_available,
        "attribution_status": phase_minus2.attribution_status,
        "final_status": final_status,
        "phase_minus2_status": phase_minus2.status,
        "phase0a_status": phase0a.status if phase0a else "not_run",
        # Auto-promotion firewall
        "registry_verdict_authorized": False,
        "promotion_candidate": False,
        "observer_only": True,
        "no_order_intent": True,
        "paper_registry_write_authorized": False,
        "paper_registry_written": False,
        "conductor_promotion_authorized": False,
        "shadow_or_live_unlock": False,
        # Data
        "symbols_requested": symbols_requested,
        "symbols_evaluated": symbols_evaluated,
        "symbols_excluded_with_reasons": symbols_excluded,
        "fills_l2_overlap_summary": overlap_summary,
        "byte_estimate": phase_minus2.byte_estimate,
        # Metrics
        "filled_event_counts_by_model": fill_counts,
        "primary_offset_bps": 75.0,
        "primary_horizon_minutes": 60,
        "primary_cost_bps": 9.5,
        "primary_metrics_by_model": primary_metrics,
        "adverse_selection_metrics": phase0a.adverse_selection if phase0a else {},
        "temporal_concentration": phase0a.temporal_concentration if phase0a else {},
        "circular_shift_null": phase0a.null_results.get("circular_shift", {}) if phase0a else {},
        "timestamp_placebo_null": phase0a.null_results.get("timestamp_placebo", {}) if phase0a else {},
        "staleness_metrics": phase0a.staleness_metrics if phase0a else {},
        "survivorship_ambiguity_present": survivorship_ambiguity,
        "funding_not_included_diagnostic": FUNDING_NOT_INCLUDED,
        "safety": {
            "no_orders": True,
            "no_private_keys": True,
            "no_trading_auth": True,
            "no_live_execution": True,
            "no_paper_trading": True,
            "no_shadow_execution": True,
            "no_systemd_changes": True,
            "no_bot_path_changes": True,
            "no_conductor_promotion": True,
            "no_paper_registry_mutation": True,
            "no_rejected_research_mutation": True,
        },
        "warnings": phase0a.warnings if phase0a else phase_minus2.warnings,
    }

    # Validate no forbidden statuses
    assert final_status not in FORBIDDEN_STATUSES, f"Forbidden status emitted: {final_status}"

    return summary


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid cascade overshoot snapback Phase -2 + Phase 0A v0")
    parser.add_argument("--out-root", type=Path, default=Path("reports/liquidation_overshoot_snapback_phase0a"))
    parser.add_argument("--data-root", type=Path, default=Path("data/hyperliquid"))
    parser.add_argument("--symbols", type=str, default=",".join(EVENT_UNIVERSE))
    parser.add_argument("--start-date", type=str, default="2025-10-01")
    parser.add_argument("--end-date", type=str, default="2025-10-31")
    parser.add_argument("--positive-control-window", type=str, default=None)
    parser.add_argument("--max-download-bytes", type=int, default=25_000_000_000)
    parser.add_argument("--min-free-disk-gib", type=int, default=25)
    parser.add_argument("--stress-window-inflation-factor", type=float, default=2.5)
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--allow-network-public", action="store_true")
    parser.add_argument("--exact-liquidation-required", type=str, default="false")
    parser.add_argument("--run-phase-minus2", action="store_true")
    parser.add_argument("--run-phase0a", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--seed", type=int, default=20260531)
    parser.add_argument("--max-events", type=int, default=None)

    args = parser.parse_args()
    command_args = sys.argv[1:]

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    exact_liq = args.exact_liquidation_required.lower() == "true"

    # Safety: default mode is dry-run/plan-only
    if not args.allow_s3_archive_read and not args.dry_run:
        args.dry_run = True
        args.plan_only = True
        print("NOTE: No --allow-s3-archive-read flag. Defaulting to dry-run/plan-only mode.")

    # Create output directory
    run_id = create_run_id("liq_overshoot")
    report_dir = create_run_dir(args.out_root, run_id)

    # Phase -2
    phase_minus2 = None
    if args.run_phase_minus2 or args.dry_run or args.plan_only:
        phase_minus2 = run_phase_minus2(
            symbols=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            data_root=args.data_root,
            exact_liquidation_required=exact_liq,
            positive_control_window=None,  # TODO: parse from CLI
            max_download_bytes=args.max_download_bytes,
            min_free_disk_gib=args.min_free_disk_gib,
            stress_window_inflation=args.stress_window_inflation_factor,
            allow_s3=args.allow_s3_archive_read,
            allow_network=args.allow_network_public,
        )
        print(f"PHASE_MINUS2_STATUS: {phase_minus2.status}")
        print(f"ATTRIBUTION: {phase_minus2.attribution_status}")
        print(f"OVERLAP_SYMBOLS: {len(phase_minus2.overlap_reports)}")

    # Phase 0A
    phase0a = None
    if args.run_phase0a and phase_minus2 and phase_minus2.status in (CASCADE_PROXY_READY, "LIQ_OVERSHOOT_PHASE_MINUS2_READY"):
        phase0a = run_phase0a(
            symbols=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            data_root=args.data_root,
            phase_minus2=phase_minus2,
            max_events=args.max_events,
            seed=args.seed,
        )
        print(f"PHASE0A_STATUS: {phase0a.status}")
        print(f"CANDIDATES: {len(phase0a.cascade_candidates)}")
        print(f"FILL_EVENTS: {len(phase0a.passive_fill_events)}")

    # Build summary
    git_sha = phase_minus2.byte_estimate.get("git_sha", "unknown") if phase_minus2 else "unknown"
    git_branch = "unknown"
    git_dirty = False
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        git_sha = r.stdout.strip() if r.returncode == 0 else git_sha
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5)
        git_branch = r.stdout.strip() if r.returncode == 0 else git_branch
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
        git_dirty = bool(r.stdout.strip()) if r.returncode == 0 else False
    except Exception:
        pass

    symbols_evaluated = [s for s in symbols if s in EVENT_UNIVERSE]
    symbols_excluded = {s: "not_in_event_universe" for s in symbols if s not in EVENT_UNIVERSE}
    if phase0a and phase0a.warnings:
        for w in phase0a.warnings:
            if "excluded" in w.lower():
                pass  # Already captured

    summary = _build_summary(
        run_id=run_id,
        git_sha=git_sha,
        git_branch=git_branch,
        git_dirty=git_dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        command_args=command_args,
        phase_minus2=phase_minus2 or PhaseMinus2Result(
            status=PHASE_MINUS2_ERROR,
            attribution_status="not_run",
            exact_liquidation_available=False,
            overlap_reports=[],
            byte_estimate={},
            warnings=["Phase -2 was not run"],
        ),
        phase0a=phase0a,
        symbols_requested=symbols,
        symbols_evaluated=symbols_evaluated,
        symbols_excluded=symbols_excluded,
        exact_liquidation_required=exact_liq,
    )

    write_report_artifacts(summary, report_dir)

    # Write phase-specific artifacts
    if phase0a:
        # Write cascade candidates JSONL
        candidates_path = report_dir / "cascade_candidates.jsonl"
        with open(candidates_path, "w") as f:
            for c in phase0a.cascade_candidates:
                f.write(json.dumps({
                    "event_id": c.event_id,
                    "symbol": c.symbol,
                    "event_timestamp_utc": c.event_timestamp_utc,
                    "event_timestamp_ms": c.event_timestamp_ms,
                    "pre_event_mid": c.pre_event_mid,
                    "trailing_5m_return_bps": c.trailing_5m_return_bps,
                    "fills_5m_notional": c.fills_5m_notional,
                    "fills_5m_notional_p95": c.fills_5m_notional_p95,
                    "event_low_trade_px": c.event_low_trade_px,
                    "event_low_trade_bps_below_mid": c.event_low_trade_bps_below_mid,
                }, default=str) + "\n")

        # Write passive fill events JSONL
        fills_path = report_dir / "passive_fill_events.jsonl"
        with open(fills_path, "w") as f:
            for e in phase0a.passive_fill_events:
                f.write(json.dumps({
                    "event_id": e.event_id,
                    "symbol": e.symbol,
                    "event_timestamp_utc": e.event_timestamp_utc,
                    "pre_event_mid": e.pre_event_mid,
                    "offset_bps": e.offset_bps,
                    "bid_px": e.bid_px,
                    "fill_model": e.fill_model,
                    "filled": e.filled,
                    "fill_price": e.fill_price,
                    "future_mid_60m": e.future_mid_60m,
                    "pre_mid_book_staleness_ms": e.pre_mid_book_staleness_ms,
                    "is_control": e.is_control,
                }, default=str) + "\n")

        # Write non-touched controls JSONL
        controls_path = report_dir / "non_touched_controls.jsonl"
        with open(controls_path, "w") as f:
            for e in phase0a.non_touched_controls:
                f.write(json.dumps({
                    "event_id": e.event_id,
                    "symbol": e.symbol,
                    "event_timestamp_utc": e.event_timestamp_utc,
                    "pre_event_mid": e.pre_event_mid,
                    "offset_bps": e.offset_bps,
                    "fill_model": e.fill_model,
                    "future_mid_60m": e.future_mid_60m,
                }, default=str) + "\n")

        # Write horizon metrics CSV
        if phase0a.horizon_metrics:
            import csv
            hm_path = report_dir / "horizon_metrics.csv"
            with open(hm_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(phase0a.horizon_metrics[0].keys()))
                writer.writeheader()
                writer.writerows(phase0a.horizon_metrics)

        # Write fill model metrics CSV
        if phase0a.fill_model_metrics:
            import csv
            fmm_path = report_dir / "fill_model_metrics.csv"
            with open(fmm_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(phase0a.fill_model_metrics[0].keys()))
                writer.writeheader()
                writer.writerows(phase0a.fill_model_metrics)

        # Write adverse selection JSON
        atomic_json(report_dir / "adverse_selection_metrics.json", phase0a.adverse_selection)

        # Write temporal concentration JSON
        atomic_json(report_dir / "temporal_concentration.json", phase0a.temporal_concentration)

        # Write null results JSON
        atomic_json(report_dir / "null_results.json", phase0a.null_results)

        # Write staleness metrics JSON
        atomic_json(report_dir / "staleness_metrics.json", phase0a.staleness_metrics)

    # Write phase minus2 artifacts
    if phase_minus2:
        atomic_json(report_dir / "phase_minus2_reachability.json", {
            "status": phase_minus2.status,
            "attribution_status": phase_minus2.attribution_status,
            "exact_liquidation_available": phase_minus2.exact_liquidation_available,
            "warnings": phase_minus2.warnings,
        })
        atomic_json(report_dir / "phase_minus2_plan_estimate.json", phase_minus2.byte_estimate)

    print(f"\nFINAL_STATUS: {summary['final_status']}")
    print(f"REPORT_DIR: {report_dir}")
    print(f"SUMMARY: {report_dir / 'summary.json'}")
    return 0


def atomic_json(path, data):
    """Write JSON atomically."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
