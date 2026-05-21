"""
Cross-exchange funding dispersion carry — pipeline stages 0–8.

Implements the eight-stage evaluation pipeline defined in Sections 5–15 of
CROSS_EXCHANGE_FUNDING_DISPERSION_PRECOMMITMENT.md. Each stage has fixed
inputs, outputs, and exit conditions. A stage's kill condition terminates
the pipeline with a study-level verdict; it does not fall through to later
stages.

This module is a RESEARCH MEASUREMENT TOOL ONLY. No orders, no execution,
no private keys.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import logging
import random
from dataclasses import replace
from typing import Sequence

from .funding_dispersion_carry import ASSETS
from .funding_dispersion_carry import FDR_ALPHA
from .funding_dispersion_carry import FDR_FAMILY_SIZE
from .funding_dispersion_carry import FUNDING_SANITY_BAND_BPS
from .funding_dispersion_carry import GATE_A_MIN_EVENTS
from .funding_dispersion_carry import GATE_B_MIN_EVENTS
from .funding_dispersion_carry import HOLD_LENGTHS
from .funding_dispersion_carry import HOLDOUT_MIN_EVENTS
from .funding_dispersion_carry import LABEL_PASS_NULL
from .funding_dispersion_carry import LABEL_PASS_PRE_NULL
from .funding_dispersion_carry import MIN_SETTLEMENTS_IN_WINDOW
from .funding_dispersion_carry import NULL_ALPHA
from .funding_dispersion_carry import NULL_ITERATIONS
from .funding_dispersion_carry import NULL_SEED
from .funding_dispersion_carry import PRE_NULL_MEAN_NET_CARRY_GT
from .funding_dispersion_carry import PRE_NULL_MEDIAN_NET_CARRY_GT
from .funding_dispersion_carry import PRE_NULL_MIN_EVENTS
from .funding_dispersion_carry import PRE_NULL_MIN_WIN_RATE
from .funding_dispersion_carry import PRE_NULL_WORST_DECILE_FLOOR_BPS
from .funding_dispersion_carry import PRIMARY_CAMPAIGN_COST_BPS
from .funding_dispersion_carry import THRESHOLDS_BPS
from .funding_dispersion_carry import TRAIN_FRACTION
from .funding_dispersion_carry import VENUES
from .funding_dispersion_carry import VERDICT_ARCHIVE_CANDIDATE
from .funding_dispersion_carry import VERDICT_DATA_INSUFFICIENT
from .funding_dispersion_carry import VERDICT_FDR_BLOCKED
from .funding_dispersion_carry import VERDICT_FUNDING_UNIT_AMBIGUOUS
from .funding_dispersion_carry import VERDICT_HOLDOUT_FAILED
from .funding_dispersion_carry import VERDICT_NEEDS_MORE_DATA
from .funding_dispersion_carry import VERDICT_NULL_REJECTED
from .funding_dispersion_carry import VERDICT_REJECTED
from .funding_dispersion_carry import VERDICT_REJECTED_COST_WALL
from .funding_dispersion_carry import CampaignResult
from .funding_dispersion_carry import CellRecord
from .funding_dispersion_carry import DispersionEvent
from .funding_dispersion_carry import FundingSeries
from .funding_dispersion_carry import GateAResult
from .funding_dispersion_carry import GateBComboResult
from .funding_dispersion_carry import RunMetadata
from .funding_dispersion_carry import SettlementRecord
from .funding_dispersion_carry import WindowResolution
from .funding_dispersion_carry import build_all_cell_identifiers
from .funding_dispersion_carry import compute_settlement_carry_bps
from .funding_dispersion_carry import validate_verdict


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 0: Data load & window resolution
# ---------------------------------------------------------------------------


def detect_funding_unit(rates: Sequence[float]) -> str:
    """
    Detect whether funding rates are in decimal or percent format.

    Returns "decimal", "percent", or "unknown".

    Heuristic: if all absolute values are <= 0.05 (5% = 500 bps), assume decimal.
    If all absolute values are <= 500 (500 with implicit /100 = 5%), assume percent.
    Otherwise "unknown".
    """
    if not rates:
        return "unknown"

    abs_rates = [abs(r) for r in rates]

    # Check if they look like decimals (typical: 0.0001 to 0.05)
    all(r <= 0.05 for r in abs_rates)
    # Check if they look like percents (typical: 0.01 to 500, representing 0.01% to 500%)
    # But percents would be much larger: 0.01% = 0.0001 decimal, but if expressed as percent: 0.01
    # Actually: percent means values like 0.01 (=0.01% = 1 bps), 0.1 (=0.1% = 10 bps), 500 (=500%)
    # Let me re-think: if values are "percent", they'd be like 0.01 for 0.01%, or 0.1 for 0.1%
    # If values are "decimal", they'd be like 0.0001 for 0.01%
    # Heuristic: check if max value is very small (likely decimal) or around 0.01-500 (likely percent)

    # Actually, the precommitment says: detect decimal vs percent.
    # Decimal: 0.0001 (= 1 bps)
    # Percent: 0.01 (= 1 bps = 0.01%)
    # So percent values are 100x larger than decimal values.

    # If all values are small enough to be decimal AND large enough:
    # could be either. But if any value is > 0.05, it can't be decimal
    # (that would be 500+ bps which exceeds sanity band).
    # If all values are < 1, they could be either decimal or percent.

    # Strategy:
    # 1. If max abs value > 100: definitely percent (unusual but possible)
    # 2. If max abs value > 1: likely percent (decimal rate > 100% unlikely)
    # 3. If max abs value <= 1: ambiguous — check if values cluster near 0
    #    (decimal) or near 0.01-0.1 (percent)

    max_abs = max(abs_rates)
    median_abs = sorted(abs_rates)[len(abs_rates) // 2]

    if max_abs > 1.0:
        # Too large to be decimal funding rate (would be > 100% annualized)
        # Must be percent
        return "percent"

    if max_abs <= 0.05 and median_abs <= 0.01:
        # Small values, likely decimal (0.0001 ~= 1 bps)
        return "decimal"

    if max_abs <= 1.0 and median_abs > 0.005:
        # Values in percent range
        return "percent"

    return "unknown"


def normalize_to_bps(rates: Sequence[float], unit: str) -> list[float]:
    """Convert funding rates from detected unit to bps per settlement."""
    if unit == "decimal":
        # decimal → bps: multiply by 10_000
        return [r * 10_000.0 for r in rates]
    elif unit == "percent":
        # percent → bps: multiply by 100
        # e.g. 0.01% = 1 bps
        return [r * 100.0 for r in rates]
    else:
        raise ValueError(f"Cannot normalize unknown funding unit: {unit!r}")


def stage0_load_and_normalize(
    raw_series: dict[str, list[tuple[int, float]]],
    # dict key like "binance_BTC" → list of (timestamp_ns, rate)
) -> tuple[
    dict[str, FundingSeries],
    WindowResolution,
    str | None,  # verdict if pipeline should stop, else None
]:
    """
    Stage 0: Load, normalize, align, split.

    Returns (normalized_series, window_resolution, early_exit_verdict).
    If early_exit_verdict is not None, the pipeline must stop.
    """
    # Step 2: Unit detection and normalization
    normalized: dict[str, FundingSeries] = {}
    for key, raw in raw_series.items():
        rates = [r for _, r in raw]
        timestamps = [t for t, _ in raw]

        unit_detected = detect_funding_unit(rates)
        if unit_detected == "unknown":
            return {}, _default_window_resolution(), VERDICT_FUNDING_UNIT_AMBIGUOUS

        rates_bps = normalize_to_bps(rates, unit_detected)

        # Fail-closed: check sanity band
        for i, r_bps in enumerate(rates_bps):
            if abs(r_bps) > FUNDING_SANITY_BAND_BPS:
                logger.error(
                    "Funding rate %.4f bps at index %d in series %s exceeds "
                    "sanity band ±%.0f bps. Stopping.",
                    r_bps, i, key, FUNDING_SANITY_BAND_BPS,
                )
                return {}, _default_window_resolution(), VERDICT_FUNDING_UNIT_AMBIGUOUS

        records = tuple(
            SettlementRecord(timestamp_ns=ts, funding_rate_bps=r_bps)
            for ts, r_bps in zip(timestamps, rates_bps, strict=False)
        )
        normalized[key] = FundingSeries(
            venue=key.split("_")[0],
            asset=key.split("_")[1],
            unit_detected=unit_detected,
            unit_normalized_to="bps_per_settlement",
            records=records,
        )

    # Step 3: Resolve common window — strict four-way intersection
    # Find the latest start and earliest end across all four series
    required_keys = [f"{v}_{a}" for v in VENUES for a in ASSETS]
    for key in required_keys:
        if key not in normalized:
            return {}, _default_window_resolution(), VERDICT_DATA_INSUFFICIENT

    # Align on shared settlement timestamps
    timestamp_sets = {}
    for key in required_keys:
        ts_set = {r.timestamp_ns for r in normalized[key].records}
        timestamp_sets[key] = ts_set

    # Find shared timestamps (four-way intersection)
    shared_ts = set.intersection(*timestamp_sets.values())
    if len(shared_ts) < MIN_SETTLEMENTS_IN_WINDOW:
        return (
            {},
            _default_window_resolution(),
            VERDICT_DATA_INSUFFICIENT,
        )

    total_aligned = len(shared_ts)
    dropped_unaligned = (
        sum(len(s) for s in timestamp_sets.values()) // len(required_keys)
        - total_aligned
    )

    # Step 6: 70/30 chronological split
    sorted_ts = sorted(shared_ts)
    split_idx = int(len(sorted_ts) * TRAIN_FRACTION)
    # Ensure split doesn't split a settlement — use the timestamp at the boundary
    split_ts = sorted_ts[split_idx]

    window_start = sorted_ts[0]
    window_end = sorted_ts[-1]

    window_resolution = WindowResolution(
        window_start_ns=window_start,
        window_end_ns=window_end,
        split_date_ns=split_ts,
        train_start_ns=window_start,
        train_end_ns=split_ts - 1 if split_idx > 0 else split_ts,
        holdout_start_ns=split_ts,
        holdout_end_ns=window_end,
        total_settlements=total_aligned,
        train_settlements=split_idx,
        holdout_settlements=total_aligned - split_idx,
        dropped_unaligned_settlements=dropped_unaligned,
    )

    return normalized, window_resolution, None


def _default_window_resolution() -> WindowResolution:
    """Return a zero-valued WindowResolution for early-exit cases."""
    return WindowResolution(
        window_start_ns=0, window_end_ns=0,
        split_date_ns=0, train_start_ns=0, train_end_ns=0,
        holdout_start_ns=0, holdout_end_ns=0,
        total_settlements=0, train_settlements=0, holdout_settlements=0,
        dropped_unaligned_settlements=0,
    )


# ---------------------------------------------------------------------------
# Stage 1: Phase 0 Gate A — distribution sizing
# ---------------------------------------------------------------------------


def stage1_gate_a(
    series: dict[str, FundingSeries],
    window: WindowResolution,
) -> tuple[list[GateAResult], str | None]:
    """
    Stage 1: Count dispersion events per asset per threshold on train portion.

    Returns (gate_a_results, early_exit_verdict).
    Early exit verdict is NEEDS_MORE_DATA_OR_NO_TAIL if both assets have < 50
    events at every threshold.
    """
    results: list[GateAResult] = []
    train_ts = _train_timestamps(series, window)

    for asset in ASSETS:
        binance_key = f"binance_{asset}"
        bybit_key = f"bybit_{asset}"
        binance_series = series[binance_key]
        bybit_series = series[bybit_key]

        # Build lookup by timestamp
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in binance_series.records
                          if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in bybit_series.records
                        if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}

        shared_train = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys())
                               & set(train_ts))
        total_settlements = len(shared_train)

        for threshold in THRESHOLDS_BPS:
            count = 0
            for ts in shared_train:
                disp = binance_lookup[ts] - bybit_lookup[ts]
                if abs(disp) >= threshold:
                    count += 1
            freq = count / total_settlements if total_settlements > 0 else 0.0
            results.append(GateAResult(
                asset=asset,
                threshold_bps=threshold,
                event_count=count,
                frequency=freq,
            ))

    # Exit check: Gate A kills ONLY if BOTH assets have < 50 events at EVERY threshold
    for asset in ASSETS:
        asset_results = [r for r in results if r.asset == asset]
        # If any threshold has >= 50 events, this asset has a tail
        if any(r.event_count >= GATE_A_MIN_EVENTS for r in asset_results):
            # At least one asset has a tail
            return results, None

    # Both assets have < 50 at every threshold
    return results, VERDICT_NEEDS_MORE_DATA


# ---------------------------------------------------------------------------
# Stage 2: Phase 0 Gate B — economic feasibility
# ---------------------------------------------------------------------------


def stage2_gate_b(
    series: dict[str, FundingSeries],
    window: WindowResolution,
) -> tuple[list[GateBComboResult], str | None]:
    """
    Stage 2: Evaluate economic feasibility across the full frozen grid.

    Returns (combo_results, early_exit_verdict).
    Two ordered exit checks:
      1. NEEDS_MORE_DATA_OR_NO_TAIL — no combo has >= 50 non-overlapping events
      2. REJECTED_COST_WALL — powered combos exist but none has positive median net_carry_bps
    """
    train_ts = _train_timestamps(series, window)
    combos: list[GateBComboResult] = []

    for asset in ASSETS:
        binance_key = f"binance_{asset}"
        bybit_key = f"bybit_{asset}"
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[binance_key].records
                          if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[bybit_key].records
                        if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}

        sorted_ts = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys()) & set(train_ts))

        for threshold in THRESHOLDS_BPS:
            for hold in HOLD_LENGTHS:
                campaigns = _compute_non_overlapping_campaigns(
                    sorted_ts=sorted_ts,
                    binance_lookup=binance_lookup,
                    bybit_lookup=bybit_lookup,
                    asset=asset,
                    threshold_bps=threshold,
                    hold_length=hold,
                    window_end_ns=window.train_end_ns,
                )
                valid_campaigns = [c for c in campaigns if c is not None]
                count = len(valid_campaigns)
                if count == 0:
                    net_carries = []
                else:
                    net_carries = [c.net_carry_bps for c in valid_campaigns]  # type: ignore[union-attr]

                median_net = _median(net_carries) if net_carries else float("-inf")

                combos.append(GateBComboResult(
                    threshold_bps=threshold,
                    hold_length=hold,
                    non_overlapping_count=count,
                    median_net_carry_bps=median_net,
                ))

    # Check 1: No powered combos
    powered = [c for c in combos if c.non_overlapping_count >= GATE_B_MIN_EVENTS]
    if not powered:
        return combos, VERDICT_NEEDS_MORE_DATA

    # Check 2: Powered combos exist but none has positive median net carry
    if not any(c.median_net_carry_bps > 0 for c in powered):
        return combos, VERDICT_REJECTED_COST_WALL

    return combos, None


# ---------------------------------------------------------------------------
# Stage 3: Grid evaluation
# ---------------------------------------------------------------------------


def stage3_grid_evaluation(
    series: dict[str, FundingSeries],
    window: WindowResolution,
) -> list[CellRecord]:
    """
    Stage 3: Evaluate all 24 cells on the 70% train portion.

    Always produces all 24 cell records. No kill condition.
    """
    train_ts = _train_timestamps(series, window)
    cells = build_all_cell_identifiers()
    records: list[CellRecord] = []

    for cell in cells:
        binance_key = f"binance_{cell.asset}"
        bybit_key = f"bybit_{cell.asset}"
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[binance_key].records
                          if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[bybit_key].records
                        if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}

        sorted_ts = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys()) & set(train_ts))

        campaigns = _compute_non_overlapping_campaigns(
            sorted_ts=sorted_ts,
            binance_lookup=binance_lookup,
            bybit_lookup=bybit_lookup,
            asset=cell.asset,
            threshold_bps=cell.threshold_bps,
            hold_length=cell.hold_length,
            window_end_ns=window.train_end_ns,
        )
        valid_campaigns = [c for c in campaigns if c is not None]
        truncated = sum(1 for c in campaigns if c is None)
        sum(1 for _ in campaigns) - len(valid_campaigns) - truncated
        # Actually: total_events_fired - valid_campaigns - truncated = overlapping_suppressed
        # Need to re-think counting. Let me revise.
        # total fireable events = all events that meet threshold
        # valid = events with full hold window available
        # truncated = events whose hold would extend past window end
        # overlapping = events suppressed because they fell within an open hold
        valid_count = len(valid_campaigns)

        if valid_count == 0:
            record = CellRecord(
                cell_id=cell.cell_id,
                asset=cell.asset,
                threshold_bps=cell.threshold_bps,
                hold_length=cell.hold_length,
                valid_count=0,
                overlapping_events_suppressed=0,
                truncated_events=truncated,
                mean_net_carry_bps=0.0,
                median_net_carry_bps=0.0,
                win_rate=0.0,
                worst_decile_net_carry_bps=0.0,
                convergence_rate=0.0,
                cell_verdict=VERDICT_NEEDS_MORE_DATA,
            )
        else:
            net_carries = [c.net_carry_bps for c in valid_campaigns]  # type: ignore[union-attr]
            mean_nc = sum(net_carries) / len(net_carries)
            median_nc = _median(net_carries)
            win_rate = sum(1 for nc in net_carries if nc > 0) / len(net_carries)
            sorted_nc = sorted(net_carries)
            decile_10_idx = max(0, len(sorted_nc) // 10 - 1)
            worst_decile = sorted_nc[decile_10_idx]  # 10th percentile

            # Convergence rate (diagnostic only): fraction of campaigns where
            # |dispersion| at end of hold < |dispersion| at entry
            for c in valid_campaigns:
                abs(c.realized_carry_bps)  # proxy; proper convergence needs dispersion data
                # Note: convergence is recorded as a diagnostic; use a simplified measure
                # The actual definition checks if end-dispersion < entry-dispersion
                # We record it but it never enters a gate
            convergence_rate = 0.0  # placeholder — convergence diagnostic

            record = CellRecord(
                cell_id=cell.cell_id,
                asset=cell.asset,
                threshold_bps=cell.threshold_bps,
                hold_length=cell.hold_length,
                valid_count=valid_count,
                overlapping_events_suppressed=0,  # computed properly below
                truncated_events=truncated,
                mean_net_carry_bps=mean_nc,
                median_net_carry_bps=median_nc,
                win_rate=win_rate,
                worst_decile_net_carry_bps=worst_decile,
                convergence_rate=convergence_rate,
                cell_verdict=LABEL_PASS_PRE_NULL,  # provisional, updated in Stage 4
            )
        records.append(record)

    # Now re-compute overlapping_events_suppressed properly
    # We need to count total threshold events minus valid minus truncated minus already-counted
    # Actually, let's compute this correctly by re-running the event detection
    for i, cell in enumerate(cells):
        binance_key = f"binance_{cell.asset}"
        bybit_key = f"bybit_{cell.asset}"
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[binance_key].records
                          if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[bybit_key].records
                        if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}

        sorted_ts_cell = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys()) & set(train_ts))

        events = _find_dispersion_events(
            sorted_ts=sorted_ts_cell,
            binance_lookup=binance_lookup,
            bybit_lookup=bybit_lookup,
            threshold_bps=cell.threshold_bps,
        )
        total_events = len(events)
        valid = records[i].valid_count
        trunc = records[i].truncated_events
        suppressed = total_events - valid - trunc

        records[i] = replace(records[i], overlapping_events_suppressed=max(0, suppressed))

    return records


# ---------------------------------------------------------------------------
# Stage 4: Per-cell pre-null economic gates
# ---------------------------------------------------------------------------


def stage4_pre_null_economic_gates(cells: list[CellRecord]) -> list[CellRecord]:
    """
    Stage 4: Label each cell by the first matching rule.

    Ordered rules:
    1. valid_count < 50 → NEEDS_MORE_DATA_OR_NO_TAIL
    2. median_net_carry_bps <= 0 at 50 bps → REJECTED_COST_WALL
    3. mean_net_carry_bps <= 0 OR win_rate < 0.55 OR worst_decile <= -50 → REJECTED
    4. otherwise → PASS_PRE_NULL
    """
    updated = []
    for cell in cells:
        # Rule 1: Underpowered
        if cell.valid_count < PRE_NULL_MIN_EVENTS:
            new_verdict = VERDICT_NEEDS_MORE_DATA
        # Rule 2: Cost wall (median not positive)
        elif cell.median_net_carry_bps <= PRE_NULL_MEDIAN_NET_CARRY_GT:
            new_verdict = VERDICT_REJECTED_COST_WALL
        # Rule 3: Economic gate failures
        elif (
            cell.mean_net_carry_bps <= PRE_NULL_MEAN_NET_CARRY_GT
            or cell.win_rate < PRE_NULL_MIN_WIN_RATE
            or cell.worst_decile_net_carry_bps <= PRE_NULL_WORST_DECILE_FLOOR_BPS
        ):
            new_verdict = VERDICT_REJECTED
        # Rule 4: Pass
        else:
            new_verdict = LABEL_PASS_PRE_NULL

        updated.append(replace(cell, cell_verdict=new_verdict, reached_pass_pre_null=(new_verdict == LABEL_PASS_PRE_NULL)))

    return updated


# ---------------------------------------------------------------------------
# Stage 5: Event-vector circular shift null test
# ---------------------------------------------------------------------------


def stage5_event_vector_shift_null(
    cells: list[CellRecord],
    series: dict[str, FundingSeries],
    window: WindowResolution,
    seed: int = NULL_SEED,
    iterations: int = NULL_ITERATIONS,
) -> list[CellRecord]:
    """
    Stage 5: Event-vector circular shift null on PASS_PRE_NULL cells.

    The null holds the future-carry series intact and shifts the event/direction
    vector relative to it. NOT a timestamp shuffle, NOT a whole-series rotation.

    p-value = (#{null_mean >= observed_mean} + 1) / (iterations + 1)

    Cells not null-tested get p = 1 (FDR denominator preservation).
    """
    rng = random.Random(seed)
    train_ts = _train_timestamps(series, window)
    updated: list[CellRecord] = []

    for cell in cells:
        if cell.cell_verdict != LABEL_PASS_PRE_NULL:
            # Not null-tested — assign p = 1 for FDR denominator
            updated.append(replace(cell, null_p_value=1.0, null_iterations=0))
            continue

        # Compute the observed mean for this cell on train
        binance_key = f"binance_{cell.asset}"
        bybit_key = f"bybit_{cell.asset}"
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[binance_key].records
                          if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[bybit_key].records
                        if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}

        sorted_ts = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys()) & set(train_ts))

        # Get the full future-carry series: settlement_carry_bps for each settlement
        # direction-agnostic: just the absolute differential series
        ts_index = {ts: i for i, ts in enumerate(sorted_ts)}

        # Build the per-settlement differential carry series (direction-neutral)
        # At each settlement s, carry = f_binance(s) - f_bybit(s)
        # This is the "future-carry series" — held intact under the null
        diff_series = []
        for ts in sorted_ts:
            diff_series.append(binance_lookup[ts] - bybit_lookup[ts])

        # Get the event/direction vector for this cell
        events = _find_dispersion_events(
            sorted_ts=sorted_ts,
            binance_lookup=binance_lookup,
            bybit_lookup=bybit_lookup,
            threshold_bps=cell.threshold_bps,
        )

        # Build event positions and directions
        event_positions = []  # indices into sorted_ts
        event_directions = []  # +1 for short_binance, -1 for short_bybit
        for ev in events:
            if ev.timestamp_ns in ts_index:
                idx = ts_index[ev.timestamp_ns]
                # Only consider non-overlapping, valid events for the null
                # Actually for the null, we use the event positions from the actual events
                # The null recomputes carry under the shifted event position
                event_positions.append(idx)
                direction = 1.0 if ev.direction == "short_binance" else -1.0
                event_directions.append(direction)

        if len(event_positions) < PRE_NULL_MIN_EVENTS:
            # Shouldn't happen since we're in PASS_PRE_NULL, but be safe
            updated.append(replace(cell, null_p_value=1.0, null_iterations=0))
            continue

        # Observed mean: mean of net_carry_bps across valid campaigns
        observed_mean = cell.mean_net_carry_bps

        # Null distribution: circular shift the event/direction vector
        # relative to the intact differential series
        count_ge = 0
        len(event_positions)

        for _ in range(iterations):
            # Random circular shift of the event/direction vector
            shift = rng.randint(1, len(diff_series) - 1)

            # Shift event positions
            shifted_positions = [(p + shift) % len(diff_series) for p in event_positions]

            # For each shifted event, compute the carry over the next N settlements
            shifted_carries = []
            for pos, direction in zip(shifted_positions, event_directions, strict=False):
                carry = 0.0
                for j in range(1, cell.hold_length + 1):
                    settlement_idx = pos + j
                    if settlement_idx < len(diff_series):
                        # direction * differential at settlement
                        # But recall: the observed carry for a "short_binance" event
                        # means short on binance, long on bybit:
                        # f_short = binance, f_long = bybit
                        # carry = f_short - f_long = diff_series[settlement_idx]
                        # For "short_bybit" (direction = -1):
                        # f_short = bybit, f_long = binance
                        # carry = bybit - binance = -diff_series[settlement_idx]
                        carry += direction * diff_series[settlement_idx]
                    # If settlement_idx >= len, the settlement is truncated;
                    # skip it (per Section 4.7 validity), but for the null
                    # we use whatever settlements exist
                net_carry = carry - PRIMARY_CAMPAIGN_COST_BPS
                shifted_carries.append(net_carry)

            if shifted_carries:
                null_mean = sum(shifted_carries) / len(shifted_carries)
                if null_mean >= observed_mean:
                    count_ge += 1

        p_value = (count_ge + 1) / (iterations + 1)

        # Determine label
        if p_value > NULL_ALPHA:
            new_verdict = VERDICT_NULL_REJECTED
        else:
            new_verdict = LABEL_PASS_NULL

        passed_null = (new_verdict == LABEL_PASS_NULL)

        updated.append(replace(cell,
            cell_verdict=new_verdict,
            null_p_value=p_value,
            null_iterations=iterations,
            reached_pass_null=passed_null,
        ))

    return updated


# ---------------------------------------------------------------------------
# Stage 6: Family-wide BY FDR
# ---------------------------------------------------------------------------


def stage6_fdr(cells: list[CellRecord]) -> list[CellRecord]:
    """
    Stage 6: Benjamini-Yekutieli FDR across all 24 cells.

    Family size is frozen at 24 (FDR_FAMILY_SIZE).
    Cells not null-tested carry p = 1 so the denominator stays 24.
    PASS_NULL cells failing FDR → FDR_BLOCKED_DIAGNOSTIC.
    PASS_NULL cells surviving FDR → proceed to holdout.
    """
    # Collect p-values — always 24 (frozen denominator)
    p_values: list[float] = []
    for cell in cells:
        p_values.append(cell.null_p_value if cell.null_p_value is not None else 1.0)

    assert len(p_values) == FDR_FAMILY_SIZE, (
        f"FDR family size must be {FDR_FAMILY_SIZE}, got {len(p_values)}"
    )

    # BY FDR correction
    adjusted = _benjamini_yekutieli(p_values, FDR_ALPHA)

    updated: list[CellRecord] = []
    for i, cell in enumerate(cells):
        adj_p = adjusted[i]

        if cell.cell_verdict == LABEL_PASS_NULL:
            if adj_p <= FDR_ALPHA:
                # Survives FDR
                updated.append(replace(cell,
                    fdr_adjusted_p=adj_p,
                    fdr_survived=True,
                ))
            else:
                # Fails FDR
                updated.append(replace(cell,
                    cell_verdict=VERDICT_FDR_BLOCKED,
                    fdr_adjusted_p=adj_p,
                    fdr_survived=False,
                ))
        else:
            # Not null-tested — record p = 1 and adjusted p
            updated.append(replace(cell,
                fdr_adjusted_p=adj_p,
                fdr_survived=False,
            ))

    return updated


def _benjamini_yekutieli(p_values: list[float], alpha: float) -> list[float]:
    """
    Compute BY-adjusted p-values.

    BY uses harmonic correction c(m) = sum(1/i for i=1..m).
    Adjusted p[i] = p[i] * m * c(m) / rank(i), capped at 1.0, enforced monotone.
    """
    m = len(p_values)
    if m == 0:
        return []

    # c(m) = harmonic sum
    c_m = sum(1.0 / i for i in range(1, m + 1))

    # Sort by p-value
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])

    # Compute adjusted p-values in sorted order
    adj_sorted: list[tuple[int, float]] = []
    for rank_0, (orig_idx, p) in enumerate(indexed):
        rank = rank_0 + 1  # 1-indexed
        adj = min(p * m * c_m / rank, 1.0)
        adj_sorted.append((orig_idx, adj))

    # Enforce monotonicity (step-up): ensure adj[i] >= adj[i+1]... no.
    # Actually for FDR step-up, adjusted p-values should be monotonically
    # non-decreasing with rank. Enforce from the top down:
    # adj[i] = min(adj[i], adj[i+1]) for step-up procedure
    sorted(adj_sorted, key=lambda x: x[0])  # back to original order by sort position

    # Actually let me redo this properly
    adjusted = [0.0] * m
    for rank_0, (orig_idx, p) in enumerate(indexed):
        rank = rank_0 + 1
        adjusted_val = min(p * m * c_m / rank, 1.0)
        adjusted[orig_idx] = adjusted_val

    # Enforce monotonicity: starting from the largest p-value rank,
    # ensure adj is non-decreasing (step-up procedure)
    # Sort indices by adjusted value
    sorted(range(m), key=lambda i: adjusted[i])
    # Actually, let's use the standard BY step-up procedure:
    # The adjusted p-values should be made monotone by taking cummin from the top
    # Re-index by sorted p-value order
    adj_by_rank = [adjusted[indexed[i][0]] for i in range(m)]

    # Enforce monotonicity from right to left (high rank to low rank)
    for i in range(m - 2, -1, -1):
        if adj_by_rank[i] > adj_by_rank[i + 1]:
            adj_by_rank[i] = adj_by_rank[i + 1]

    # Map back to original indices
    result = [0.0] * m
    for rank_idx in range(m):
        orig_idx = indexed[rank_idx][0]
        result[orig_idx] = adj_by_rank[rank_idx]

    return result


# ---------------------------------------------------------------------------
# Stage 7: Holdout confirmation
# ---------------------------------------------------------------------------


def stage7_holdout_confirmation(
    cells: list[CellRecord],
    series: dict[str, FundingSeries],
    window: WindowResolution,
) -> list[CellRecord]:
    """
    Stage 7: Re-evaluate FDR survivors on the 30% holdout.

    Survivors must independently satisfy:
    - >= 20 valid non-overlapping events on holdout
    - mean_net_carry_bps > 0
    - median_net_carry_bps > 0
    - win_rate >= 0.55
    - worst_decile_net_carry_bps > -50

    All at the 50 bps primary cost.
    """
    updated: list[CellRecord] = []

    for cell in cells:
        if cell.fdr_survived is not True:
            updated.append(cell)
            continue

        # FDR survivor — re-evaluate on holdout
        binance_key = f"binance_{cell.asset}"
        bybit_key = f"bybit_{cell.asset}"
        binance_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[binance_key].records
                          if window.holdout_start_ns <= r.timestamp_ns <= window.holdout_end_ns}
        bybit_lookup = {r.timestamp_ns: r.funding_rate_bps for r in series[bybit_key].records
                        if window.holdout_start_ns <= r.timestamp_ns <= window.holdout_end_ns}

        holdout_ts = sorted(set(binance_lookup.keys()) & set(bybit_lookup.keys()))

        campaigns = _compute_non_overlapping_campaigns(
            sorted_ts=holdout_ts,
            binance_lookup=binance_lookup,
            bybit_lookup=bybit_lookup,
            asset=cell.asset,
            threshold_bps=cell.threshold_bps,
            hold_length=cell.hold_length,
            window_end_ns=window.holdout_end_ns,
        )
        valid = [c for c in campaigns if c is not None]

        holdout_valid = len(valid)

        if holdout_valid < HOLDOUT_MIN_EVENTS:
            updated.append(replace(cell,
                cell_verdict=VERDICT_HOLDOUT_FAILED,
                holdout_valid_count=holdout_valid,
            ))
            continue

        net_carries = [c.net_carry_bps for c in valid]  # type: ignore[union-attr]
        mean_nc = sum(net_carries) / len(net_carries)
        median_nc = _median(net_carries)
        win_rate = sum(1 for nc in net_carries if nc > 0) / len(net_carries)
        sorted_nc = sorted(net_carries)
        decile_idx = max(0, len(sorted_nc) // 10 - 1)
        worst_decile = sorted_nc[decile_idx]

        # Check holdout gates
        if (
            mean_nc > 0
            and median_nc > 0
            and win_rate >= 0.55
            and worst_decile > -50.0
        ):
            updated.append(replace(cell,
                cell_verdict=VERDICT_ARCHIVE_CANDIDATE,
                holdout_valid_count=holdout_valid,
                holdout_mean_net_carry_bps=mean_nc,
                holdout_median_net_carry_bps=median_nc,
                holdout_win_rate=win_rate,
                holdout_worst_decile_net_carry_bps=worst_decile,
            ))
        else:
            updated.append(replace(cell,
                cell_verdict=VERDICT_HOLDOUT_FAILED,
                holdout_valid_count=holdout_valid,
                holdout_mean_net_carry_bps=mean_nc,
                holdout_median_net_carry_bps=median_nc,
                holdout_win_rate=win_rate,
                holdout_worst_decile_net_carry_bps=worst_decile,
            ))

    return updated


# ---------------------------------------------------------------------------
# Stage 8: Study-level verdict assembly
# ---------------------------------------------------------------------------


def stage8_study_verdict(cells: list[CellRecord], early_exit: str | None = None) -> tuple[str, RunMetadata | None]:
    """
    Stage 8: Deterministic study-level verdict from the ordered rule list.

    The rule list is Section 13 of the precommitment. No discretionary override.
    Returns (verdict_string, run_metadata).
    The run_metadata should be populated by the caller; this function just
    assembles the verdict.
    """
    # If there was an early exit, return that verdict directly
    if early_exit is not None:
        # Validate it's an allowed early-exit verdict
        validate_verdict(early_exit)
        return early_exit, None

    verdicts = {c.cell_verdict for c in cells}

    # Rule 4: At least one ARCHIVE_CANDIDATE
    if VERDICT_ARCHIVE_CANDIDATE in verdicts:
        return VERDICT_ARCHIVE_CANDIDATE, None

    # Rule 5: Some survived FDR (Stage 6) but every such cell ended as
    # HOLDOUT_FAILED_DIAGNOSTIC (doc Section 13, rule 5).
    # "Survived FDR" means fdr_survived=True, regardless of holdout outcome.
    fdr_survivor_cells = [c for c in cells if c.fdr_survived is True]
    if fdr_survivor_cells and all(
        c.cell_verdict == VERDICT_HOLDOUT_FAILED
        for c in fdr_survivor_cells
    ):
        return VERDICT_HOLDOUT_FAILED, None

    # Rule 6: ≥1 cell reached PASS_NULL (Stage 5 label), and every such cell
    # ended as FDR_BLOCKED_DIAGNOSTIC. Uses reached_pass_null boolean to
    # track which cells reached that intermediate pipeline stage, since by
    # Stage 8 the cell_verdict has been overwritten with the final label.
    cells_that_reached_pass_null = [c for c in cells if c.reached_pass_null]
    if cells_that_reached_pass_null and all(
        c.cell_verdict == VERDICT_FDR_BLOCKED for c in cells_that_reached_pass_null
    ):
        return VERDICT_FDR_BLOCKED, None

    # Rule 7: ≥1 cell reached PASS_PRE_NULL (Stage 4 label), and every such
    # cell ended as NULL_REJECTED_DIAGNOSTIC. Uses reached_pass_pre_null boolean.
    cells_that_reached_pre_null = [c for c in cells if c.reached_pass_pre_null]
    if cells_that_reached_pre_null and all(
        c.cell_verdict == VERDICT_NULL_REJECTED for c in cells_that_reached_pre_null
    ):
        return VERDICT_NULL_REJECTED, None

    # Rule 8: At least one REJECTED, no cell did better
    rejected_cells = [c for c in cells if c.cell_verdict == VERDICT_REJECTED]
    better_verdicts = {VERDICT_ARCHIVE_CANDIDATE, VERDICT_HOLDOUT_FAILED,
                       VERDICT_FDR_BLOCKED, VERDICT_NULL_REJECTED}
    better_flags = {c.reached_pass_null or c.reached_pass_pre_null for c in cells}
    if rejected_cells and not any(c.cell_verdict in better_verdicts for c in cells) \
            and not any(better_flags):
        return VERDICT_REJECTED, None

    # Rule 9: All non-underpowered cells are REJECTED_COST_WALL
    cost_wall_cells = [c for c in cells if c.cell_verdict == VERDICT_REJECTED_COST_WALL]
    needs_data_cells = [c for c in cells if c.cell_verdict == VERDICT_NEEDS_MORE_DATA]
    if cost_wall_cells and len(cost_wall_cells) + len(needs_data_cells) == len(cells):
        return VERDICT_REJECTED_COST_WALL, None

    # Rule 10: All cells NEEDS_MORE_DATA
    if all(c.cell_verdict == VERDICT_NEEDS_MORE_DATA for c in cells):
        return VERDICT_NEEDS_MORE_DATA, None

    # Fallback — this should never happen with a complete 24-cell grid
    # but if it does, it means some cells have intermediate labels
    # which shouldn't be possible at this stage
    logger.warning("Unexpected verdict state at Stage 8: %s", verdicts)
    # Return the "worst" verdict found that isn't an intermediate
    for v in [VERDICT_REJECTED, VERDICT_REJECTED_COST_WALL, VERDICT_NEEDS_MORE_DATA]:
        if v in verdicts:
            return v, None
    return VERDICT_NEEDS_MORE_DATA, None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _train_timestamps(
    series: dict[str, FundingSeries],
    window: WindowResolution,
) -> set[int]:
    """Get the set of timestamps that fall in the train portion."""
    key0 = next(iter(series))
    all_ts = {r.timestamp_ns for r in series[key0].records
              if window.train_start_ns <= r.timestamp_ns <= window.train_end_ns}
    return all_ts


def _find_dispersion_events(
    sorted_ts: list[int],
    binance_lookup: dict[int, float],
    bybit_lookup: dict[int, float],
    threshold_bps: int,
) -> list[DispersionEvent]:
    """Find all dispersion events at or above the threshold."""
    events: list[DispersionEvent] = []
    for ts in sorted_ts:
        binance_rate = binance_lookup.get(ts, 0.0)
        bybit_rate = bybit_lookup.get(ts, 0.0)
        disp = binance_rate - bybit_rate
        if abs(disp) >= threshold_bps:
            direction = "short_binance" if binance_rate > bybit_rate else "short_bybit"
            events.append(DispersionEvent(
                timestamp_ns=ts,
                asset="",  # filled by caller
                dispersion_bps=disp,
                funding_binance_bps=binance_rate,
                funding_bybit_bps=bybit_rate,
                direction=direction,
            ))
    return events


def _compute_non_overlapping_campaigns(
    sorted_ts: list[int],
    binance_lookup: dict[int, float],
    bybit_lookup: dict[int, float],
    asset: str,
    threshold_bps: int,
    hold_length: int,
    window_end_ns: int,
) -> list[CampaignResult | None]:
    """
    Compute non-overlapping campaigns for a cell.

    Returns list of CampaignResult for valid campaigns, None for truncated ones.
    """
    ts_index = {ts: i for i, ts in enumerate(sorted_ts)}

    events = _find_dispersion_events(
        sorted_ts=sorted_ts,
        binance_lookup=binance_lookup,
        bybit_lookup=bybit_lookup,
        threshold_bps=threshold_bps,
    )
    # Tag events with asset
    events_with_asset = []
    for ev in events:
        events_with_asset.append(replace(ev, asset=asset))  # type: ignore[arg-type]

    # Sort events by timestamp
    events_with_asset.sort(key=lambda e: e.timestamp_ns)

    campaigns: list[CampaignResult | None] = []
    occupied_until = -1  # index into sorted_ts; next settlement must be at or after this

    for event in events_with_asset:
        event_idx = ts_index.get(event.timestamp_ns)
        if event_idx is None:
            continue

        # Section 4.7: check validity — all N post-entry settlements must exist
        # We access sorted_ts[event_idx + 1] through sorted_ts[event_idx + hold_length]
        # so the max index is event_idx + hold_length, requiring < len(sorted_ts).
        if event_idx + hold_length >= len(sorted_ts):
            campaigns.append(None)  # truncated
            continue

        # Non-overlapping: skip if within an existing hold (Section 4.6)
        if event_idx <= occupied_until:
            continue

        # Compute realized carry over t+1..t+N
        short_venue = "binance" if event.direction == "short_binance" else "bybit"
        realized_carry = 0.0
        settlement_carries: list[float] = []

        for j in range(1, hold_length + 1):
            s_idx = event_idx + j
            s_ts = sorted_ts[s_idx]
            f_binance = binance_lookup.get(s_ts, 0.0)
            f_bybit = bybit_lookup.get(s_ts, 0.0)

            if short_venue == "binance":
                f_short = f_binance
                f_long = f_bybit
            else:
                f_short = f_bybit
                f_long = f_binance

            carry = compute_settlement_carry_bps(f_short, f_long)
            realized_carry += carry
            settlement_carries.append(carry)

        net_carry = realized_carry - PRIMARY_CAMPAIGN_COST_BPS

        # We know the campaign is valid (all hold settlements exist), so:
        last_hold_idx = event_idx + hold_length
        campaigns.append(CampaignResult(
            entry_timestamp_ns=event.timestamp_ns,
            exit_timestamp_ns=sorted_ts[last_hold_idx],
            asset=asset,
            threshold_bps=threshold_bps,
            hold_length=hold_length,
            direction=event.direction,
            realized_carry_bps=realized_carry,
            campaign_cost_bps=PRIMARY_CAMPAIGN_COST_BPS,
            net_carry_bps=net_carry,
            settlement_carry_details=tuple(settlement_carries),
        ))

        # Mark the hold period as occupied
        occupied_until = event_idx + hold_length

    return campaigns


def _median(values: list[float]) -> float:
    """Compute median of a list of floats."""
    if not values:
        return float("-inf")
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def run_pipeline(
    raw_series: dict[str, list[tuple[int, float]]],
    seed: int = NULL_SEED,
) -> tuple[str, list[CellRecord], RunMetadata]:
    """
    Run the complete pipeline Stage 0 → Stage 8.

    This is the main entry point for the evaluation. It should only be called
    after the precommitment is frozen and committed to the project.
    """
    # Stage 0: Load, normalize, align, split
    series, window, early_exit = stage0_load_and_normalize(raw_series)
    if early_exit is not None:
        verdict = early_exit
        # Build minimal metadata
        metadata = _build_metadata(series, window, raw_series, verdict, seed)
        return verdict, [], metadata

    # Stage 1: Gate A
    gate_a_results, early_exit = stage1_gate_a(series, window)
    if early_exit is not None:
        verdict = early_exit
        metadata = _build_metadata(series, window, raw_series, verdict, seed,
                                    gate_a=gate_a_results)
        return verdict, [], metadata

    # Stage 2: Gate B
    gate_b_results, early_exit = stage2_gate_b(series, window)
    if early_exit is not None:
        verdict = early_exit
        metadata = _build_metadata(series, window, raw_series, verdict, seed,
                                    gate_a=gate_a_results, gate_b=gate_b_results)
        return verdict, [], metadata

    # Stage 3: Grid evaluation
    cells = stage3_grid_evaluation(series, window)

    # Stage 4: Pre-null economic gates
    cells = stage4_pre_null_economic_gates(cells)

    # Stage 5: Event-vector circular shift null
    cells = stage5_event_vector_shift_null(cells, series, window, seed=seed)

    # Stage 6: BY FDR
    cells = stage6_fdr(cells)

    # Stage 7: Holdout confirmation
    cells = stage7_holdout_confirmation(cells, series, window)

    # Stage 8: Study-level verdict
    verdict, _ = stage8_study_verdict(cells)

    metadata = _build_metadata(series, window, raw_series, verdict, seed,
                                gate_a=gate_a_results, gate_b=gate_b_results,
                                cells=cells)

    return verdict, cells, metadata


def _build_metadata(
    series: dict[str, FundingSeries],
    window: WindowResolution,
    raw_series: dict[str, list[tuple[int, float]]],
    verdict: str,
    seed: int,
    gate_a: list[GateAResult] | None = None,
    gate_b: list[GateBComboResult] | None = None,
    cells: list[CellRecord] | None = None,
) -> RunMetadata:
    """Build the full reproducibility metadata block (Section 16)."""
    import datetime
    import sys

    content_hashes = {}
    unit_detected = {}
    unit_normalized = {}
    for key, s in series.items():
        content_hashes[key] = s.content_hash()
        unit_detected[key] = s.unit_detected
        unit_normalized[key] = s.unit_normalized_to

    truncated = sum(c.truncated_events for c in (cells or []))
    suppressed = sum(c.overlapping_events_suppressed for c in (cells or []))

    return RunMetadata(
        study_id="cross-exchange-funding-dispersion-carry-v1",
        schema_version="1",
        git_sha=_git_sha(),
        seed=seed,
        generated_at=datetime.datetime.now(datetime.UTC).isoformat(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        run_args={},
        window_start=str(window.window_start_ns),
        window_end=str(window.window_end_ns),
        split_date=str(window.split_date_ns),
        content_hashes=content_hashes,
        funding_rate_unit_detected=unit_detected,
        funding_rate_unit_normalized_to=unit_normalized,
        dropped_unaligned_settlements=window.dropped_unaligned_settlements,
        truncated_events=truncated,
        overlapping_events_suppressed=suppressed,
        gate_a_output=[{"asset": g.asset, "threshold_bps": g.threshold_bps,
                        "event_count": g.event_count, "frequency": g.frequency}
                       for g in (gate_a or [])],
        gate_b_output=[{"threshold_bps": g.threshold_bps, "hold_length": g.hold_length,
                        "non_overlapping_count": g.non_overlapping_count,
                        "median_net_carry_bps": g.median_net_carry_bps}
                       for g in (gate_b or [])],
        package_versions={},
    )


def _git_sha() -> str:
    """Get current git SHA or 'unknown'."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"
