"""CLI runner for generic stress independent return audit.

Uses existing archive-loading infrastructure from the
liquidation-flush phase0a and venue-age-aware phase0b modules.

No orders. No private keys. No trading auth. No live execution.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.generic_stress_independent_return_audit import (
    ALTCOIN_EXCLUDED_SYMBOLS,
    GENERIC_EVENT_DIRECTION,
    GENERIC_TRADE_DIRECTION,
    NegativeControlResult,
    IndependentAuditResult,
    run_independent_audit,
    run_negative_control_events,
    select_boring_events,
    select_random_timestamp_events,
    compute_temporal_concentration,
    load_accepted_events,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    load_archive_rows,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import (
    build_price_series,
)

DEFAULT_OUT_ROOT = Path("reports/generic_stress_independent_return_audit")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic stress independent return audit",
    )
    parser.add_argument("--events", type=Path, required=True,
                        help="Path to accepted_events.jsonl from Phase 0A report")
    parser.add_argument("--archive-path", type=Path, action="append", required=True,
                        help="Archive directory path (may be repeated)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--seed-boring", type=int, default=20260526)
    parser.add_argument("--seed-random", type=int, default=20260527)
    return parser


def _fmt(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"


def _run_inverse_control(
    events: list[dict[str, Any]],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> NegativeControlResult:
    """Run inverse-direction control (short instead of long)."""
    evaluated_ind = 0
    missing = 0
    gross_vals: list[float] = []
    net_50: list[float] = []

    for event in events:
        symbol = str(event.get("symbol", "")).upper()
        direction = str(event.get("event_direction", ""))
        if direction not in {"downside_price_drop", "long"}:
            continue
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        ts_str = event.get("event_timestamp_utc", "")
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            continue
        entry_price = float(event.get("price_t", 0))
        series_list = price_series.get(symbol, [])
        if not series_list:
            missing += 1
            continue
        target = ts + timedelta(hours=24)
        timestamps = [s[0] for s in series_list]
        idx = bisect_left(timestamps, target)
        if idx >= len(series_list):
            missing += 1
            continue
        ft, exit_price = series_list[idx]
        if abs((ft - target).total_seconds()) > 65 * 60:
            missing += 1
            continue
        if entry_price <= 0 or exit_price <= 0:
            missing += 1
            continue
        # INVERSE direction
        gross = (entry_price / exit_price - 1.0) * 10000.0
        gross_vals.append(gross)
        net_50.append(gross - 50.0)
        evaluated_ind += 1

    if not net_50:
        return NegativeControlResult(
            name="inverse_direction", event_count=len(events), evaluated_count=0,
            gross_mean_bps_24h=None, gross_median_bps_24h=None,
            net_mean_bps_50=None, net_median_bps_50=None,
            win_rate_50=None, net_mean_bps_75=None, net_mean_bps_100=None,
            description="Same events, trade_direction=short (inverse return)",
        )

    net_75 = [g - 75.0 for g in gross_vals]
    net_100 = [g - 100.0 for g in gross_vals]

    return NegativeControlResult(
        name="inverse_direction",
        event_count=len(events),
        evaluated_count=evaluated_ind,
        gross_mean_bps_24h=statistics.mean(gross_vals),
        gross_median_bps_24h=statistics.median(gross_vals),
        net_mean_bps_50=statistics.mean(net_50),
        net_median_bps_50=statistics.median(net_50),
        win_rate_50=sum(1 for v in net_50 if v > 0) / len(net_50),
        net_mean_bps_75=statistics.mean(net_75),
        net_mean_bps_100=statistics.mean(net_100),
        description="Same events as generic stress, but trade_direction=short (inverse return)",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.events.exists():
        print(f"ERROR: events file not found: {args.events}", file=sys.stderr)
        return 1

    print(f"Loading events from {args.events}...")
    events = load_accepted_events(args.events)
    print(f"  Loaded {len(events)} events")

    print(f"Loading archive data from {args.archive_path}...")
    t0 = time.time()
    rows, diagnostics = load_archive_rows(args.archive_path)
    t1 = time.time()
    print(f"  Loaded {len(rows)} rows in {t1 - t0:.1f}s")
    print(f"  Diagnostics: {diagnostics.total_raw_rows} raw, {diagnostics.loaded_rows} loaded, "
          f"{diagnostics.field_validation_failures} field failures, "
          f"{diagnostics.timestamp_parse_failures} ts failures")

    print("Building price series...")
    price_series = build_price_series(rows)
    alt_symbols = [s for s in price_series.keys() if s.upper() not in ALTCOIN_EXCLUDED_SYMBOLS]
    print(f"  Built series for {len(alt_symbols)} altcoin symbols")

    # Independent return recomputation
    print("\n=== Independent return recomputation ===")
    audit = run_independent_audit(events, price_series)
    print(f"  Event count: {audit.event_count}")
    print(f"  Evaluated: {audit.evaluated_count}")
    print(f"  Missing forward: {audit.missing_forward_count}")
    print(f"  Gross mean 24h (bps): {_fmt(audit.gross_mean_bps_24h)}")
    print(f"  Gross median 24h (bps): {_fmt(audit.gross_median_bps_24h)}")
    print(f"  Net mean 24h after 50 bps: {_fmt(audit.net_mean_bps_50)}")
    print(f"  Net median 24h after 50 bps: {_fmt(audit.net_median_bps_50)}")
    print(f"  Win rate (50 bps): {_fmt(audit.win_rate_50, 4)}")
    print(f"  Net mean 75 bps: {_fmt(audit.net_mean_bps_75)}")
    print(f"  Net mean 100 bps: {_fmt(audit.net_mean_bps_100)}")

    # Negative controls
    print("\n=== Negative controls ===")
    target = max(audit.evaluated_count, 100) if audit.evaluated_count else 556

    # Boring
    print(f"\n[Control A] Boring events (target: {target})")
    boring_rng = random.Random(args.seed_boring)
    boring_events = select_boring_events(price_series, target, boring_rng)
    print(f"  Selected {len(boring_events)} boring events")
    boring_result = run_negative_control_events(boring_events, price_series, "boring",
        "Boring: abs(1h ret) <= 50 bps, vol pctile 0.40-0.60")
    print(f"  Net mean 50 bps: {_fmt(boring_result.net_mean_bps_50)}")
    print(f"  Win rate: {_fmt(boring_result.win_rate_50, 4)}")

    # Random
    print(f"\n[Control B] Random timestamps (target: {target})")
    random_rng = random.Random(args.seed_random)
    random_events = select_random_timestamp_events(price_series, target, random_rng)
    print(f"  Selected {len(random_events)} random events")
    random_result = run_negative_control_events(random_events, price_series, "random",
        "Uniform random timestamps from eligible symbol-time universe")
    print(f"  Net mean 50 bps: {_fmt(random_result.net_mean_bps_50)}")
    print(f"  Win rate: {_fmt(random_result.win_rate_50, 4)}")

    # Inverse
    print(f"\n[Control C] Inverse direction")
    inverse_result = _run_inverse_control(events, price_series)
    print(f"  Net mean 50 bps: {_fmt(inverse_result.net_mean_bps_50)}")
    print(f"  Win rate: {_fmt(inverse_result.win_rate_50, 4)}")

    # Temporal concentration
    print("\n=== Temporal concentration ===")
    conc = compute_temporal_concentration(events)
    print(f"  Year counts: {conc['year_counts']}")
    print(f"  Max year share: {conc['max_year_share']:.4f} (year={conc['max_year']})")
    print(f"  Year > 50%: {conc['year_concentration_exceeds_50pct']}")

    # Summary verdict
    print("\n=== Verdict ===")
    reported_net_mean = 339.59
    reported_net_median = 273.99
    reported_win_rate = 0.6331

    mean_match = audit.net_mean_bps_50 is not None and abs(audit.net_mean_bps_50 - reported_net_mean) <= 5.0
    median_match = audit.net_median_bps_50 is not None and abs(audit.net_median_bps_50 - reported_net_median) <= 5.0
    wr_match = audit.win_rate_50 is not None and abs(audit.win_rate_50 - reported_win_rate) <= 0.02

    if mean_match and median_match and wr_match:
        print("  RESULT REPRODUCES WITHIN TOLERANCE ✓")
    else:
        print(f"  RESULT CHECK: mean_match={mean_match} median_match={median_match} wr_match={wr_match}")
        if audit.net_mean_bps_50 is not None:
            print(f"  Independent net mean: {audit.net_mean_bps_50:.2f} vs reported {reported_net_mean:.2f}")
            print(f"  Independent net median: {audit.net_median_bps_50:.2f} vs reported {reported_net_median:.2f}")

    boring_warn = (boring_result.net_mean_bps_50 is not None and boring_result.net_mean_bps_50 > 50.0) or \
                  (boring_result.win_rate_50 is not None and boring_result.win_rate_50 > 0.55)
    random_warn = (random_result.net_mean_bps_50 is not None and random_result.net_mean_bps_50 > 50.0) or \
                  (random_result.win_rate_50 is not None and random_result.win_rate_50 > 0.55)
    inverse_pos = inverse_result.net_mean_bps_50 is not None and inverse_result.net_mean_bps_50 > 0

    if boring_warn:
        print("  NEGATIVE CONTROL WARNING: boring control is too positive")
    if random_warn:
        print("  NEGATIVE CONTROL WARNING: random control is too positive")
    if inverse_pos:
        print("  DIRECTION SEMANTICS WARNING: inverse direction is positive (sign bug?)")
    else:
        print("  Inverse direction is negative ✓")

    if conc["year_concentration_exceeds_50pct"]:
        print("  TEMPORAL CONCENTRATION FAILED: max year share > 50%")
    else:
        print("  Temporal concentration OK ✓")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())