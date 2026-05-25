"""CLI entrypoint for Phase 0C null validation."""
import argparse
from pathlib import Path
import statistics
import math

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0c import (
    run_phase0c,
)

def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 0C null validation")
    parser.add_argument(
        "--phase0a-report",
        required=True,
        type=Path,
        help="Path to Phase 0A report directory",
    )
    parser.add_argument(
        "--phase0b-report",
        required=True,
        type=Path,
        help="Path to Phase 0B report directory",
    )
    parser.add_argument(
        "--archive-path",
        required=True,
        type=Path,
        help="Path to archive data directory",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1000,
        help="Number of primary null iterations",
    )
    parser.add_argument(
        "--cluster-iterations",
        type=int,
        default=1000,
        help="Number of circular-shift iterations",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Run profile only - no validation",
    )
    args = parser.parse_args()

    result = run_phase0c(
        phase0a_report_path=args.phase0a_report,
        phase0b_report_path=args.phase0b_report,
        archive_path=args.archive_path,
        precommitment_path=Path(
            "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0C_NULL_PRECOMMITMENT.md"
        ),
        iterations=args.iterations,
        cluster_iterations=args.cluster_iterations,
        profile_only=args.profile_only,
    )

    if not args.profile_only and result["status"] == "PHASE0C_NULL_VALIDATED_PASS":
        out_dir = Path.cwd() / "reports" / "liquidation_flush_aftershock_reversal_venue_age_aware_phase0c"
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        filename_stem = f"liquidation_flush_aftershock_reversal_venue_age_aware_phase0c_{ts}"

        summary_path = out_dir / (filename_stem + ".json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        md_path = out_dir / (filename_stem + ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Phase 0C Null Validation Report\n\n")
            f.write(
                f"""## Summary

- Status: {result['status']}
- Precommitment hash: {result['precommitment_sha256']}
- Phase 0A artifact hash verified: {result['phase0a_artifact_hash_verified']}
- Phase 0B report verified: {result['phase0b_report_verified']}

## Real Metrics

- 24h net mean bps: {result['real_24h_net_mean_bps']}
- 24h net median bps: {result['real_24h_net_median_bps']}
- 24h win rate: {result['real_24h_win_rate']}
- Event count: {result['real_event_count']}

## Primary Null (Matched Placebo)

- Iterations: {result['primary_placebo_iterations']}
- Coverage: {result['primary_placebo_matched_coverage']}
- Mean: {result['primary_placebo_mean']}
- 95th percentile: {result['primary_placebo_mean_95th']}
- Empirical p-value: {result['primary_empirical_p_value']}

## Secondary Null (Month-Matched)

- Iterations: {result['secondary_placebo_iterations']}
- Coverage: {result['secondary_placebo_matched_coverage']}
- Mean: {result['secondary_placebo_mean']}
- 95th percentile: {result['secondary_placebo_month']}

## Circular-Shift Null

- Iterations: {result['circular_shift_iterations']}
- Mean: {result['circular_shift_mean']}
- 95th percentile: {result['circular_shift_mean_95th']}

## Direction Shuffle

- Status: {result['direction_shuffle_status']}
- p-value: {result.get('direction_shuffle_p_value', 'N/A')}

## Cost Stress Tests

- 75 bps net mean: {result['cost_stress_75bps']}
- 100 bps net mean: {result['cost_stress_100bps']}

## Survivorship Audit

- Universe: {result['survivorship_audit']['universe_description']}
- Status: {result['survivorship_audit']['survivorship_status']}

"""
            )
    else:
        import pprint
        pprint.pprint(result)

if __name__ == "__main__":
    main()
