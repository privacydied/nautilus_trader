"""Cross-asset spot impulse lead-lag evaluator.

**RESEARCH MEASUREMENT TOOL ONLY.**  Scans source-asset spot trade ticks (BTC,
ETH) for trade-flow impulse signals and measures forward returns on *different*
target-asset spot ticks (SOL, LINK, AVAX, ADA, DOGE, XRP).

There is **no execution logic, no order submission, no position tracking, and
no live-trading code** anywhere in this module.

Cross-asset direction mapping
-----------------------------
Unlike same-asset lead-lag, where source direction maps directly to target
direction, cross-asset beta-lag uses positive-beta propagation:

- Source bullish impulse -> target expected up -> long_executable=True
- Source bearish impulse -> target expected down -> diagnostic_only=True

For spot-only execution, downside signals are diagnostic (avoid-long /
exit-existing-spot exposure).  Upside signals are the only long-executable
spot signal.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tick_models import TickForwardReturn, TickSignalEvent, TradeTickLite
from .symbol_aliases import same_asset
from .trade_flow_impulse import TradeFlowImpulseConfig, TradeFlowImpulseSignalGenerator
from .event_study import (
    evaluate_tick_signal,
    generate_random_baseline,
    evaluate_candidate_group,
)

_MS_TO_NS = 1_000_000
_SOURCE_ASSETS = {"BTC", "ETH"}

# ── Stream health tracking ──────────────────────────────────────────────────


@dataclass
class StreamHealth:
    """Tracks subscription status and tick health for one captured stream."""

    venue: str
    symbol: str
    tick_count: int = 0
    first_tick_ts: int = 0
    last_tick_ts: int = 0
    price_min: float | None = None
    price_max: float | None = None
    subscription_ok: bool = True
    zero_tick_warning: bool = False

    @property
    def range_bps(self) -> float:
        if self.price_min is not None and self.price_max is not None and self.price_min > 0:
            return (self.price_max - self.price_min) / self.price_min * 10000
        return 0.0

    @property
    def overlap_duration_ms(self) -> float:
        """Overlap duration between first and last tick in ms."""
        if self.first_tick_ts and self.last_tick_ts:
            return (self.last_tick_ts - self.first_tick_ts) / _MS_TO_NS
        return 0.0

    def record_price(self, price: float) -> None:
        if self.price_min is None or price < self.price_min:
            self.price_min = price
        if self.price_max is None or price > self.price_max:
            self.price_max = price


# ── Source-target pair key ──────────────────────────────────────────────────


@dataclass(frozen=True)
class PairKey:
    source_venue: str
    source_symbol: str
    target_venue: str
    target_symbol: str

    def group_key(self) -> str:
        return f"{self.source_venue}:{self.source_symbol}->{self.target_venue}:{self.target_symbol}"


# ── Cross-asset signal generation ───────────────────────────────────────────


def _make_source_config(
    source_venue: str, source_symbol: str, source_asset: str, signal_type: str,
    flow_lookbacks_ms: list[int], baseline_window_ms: int, cooldown_ms: int,
    imbalance_threshold: float = 0.65,
) -> TradeFlowImpulseConfig:
    """Build a TradeFlowImpulseConfig for one source asset."""
    return TradeFlowImpulseConfig(
        source_venue=source_venue,
        target_venue="__CROSS_ASSET__",  # target assigned at evaluation time
        symbol=source_symbol,
        asset=source_asset,
        flow_lookbacks_ms=flow_lookbacks_ms,
        baseline_window_ms=baseline_window_ms,
        signal_types=[signal_type],
        cooldown_ms=cooldown_ms,
        imbalance_threshold=imbalance_threshold,
    )


def generate_source_impulses(
    source_ticks: list[TradeTickLite],
    source_venue: str,
    source_symbol: str,
    signal_types: list[str],
    flow_lookbacks_ms: list[int],
    baseline_window_ms: int,
    cooldown_ms: int,
) -> list[TickSignalEvent]:
    """Generate trade-flow impulse signals on source-asset tick data.

    Returns a list of TickSignalEvent with target fields left as source
    (they get remapped during cross-asset pairing).
    """
    if not source_ticks:
        return []

    asset = _resolve_source_asset(source_symbol)
    if asset is None:
        return []

    events: list[TickSignalEvent] = []
    for stype in signal_types:
        cfg = _make_source_config(
            source_venue=source_venue,
            source_symbol=source_symbol,
            source_asset=asset,
            signal_type=stype,
            flow_lookbacks_ms=flow_lookbacks_ms,
            baseline_window_ms=baseline_window_ms,
            cooldown_ms=cooldown_ms,
        )
        gen = TradeFlowImpulseSignalGenerator(cfg)
        raw_events = gen.generate(source_ticks)
        # Mark these as cross-asset source signals
        for evt in raw_events:
            # Overwrite target fields -- they'll be set per-pair later
            evt.target_venue = "__CROSS_ASSET__"
            evt.target_symbol = "__CROSS_ASSET__"
            meta_val = evt.metadata.get('flow_signal_type', 'trade_flow_impulse') if evt.metadata else 'trade_flow_impulse'
            evt.signal_type = f"cross_asset_{meta_val}"
        events.extend(raw_events)

    return events


def _resolve_source_asset(symbol: str) -> str | None:
    """Extract the base asset from a symbol like 'BTC/USD' or 'ETH-USD'.

    Returns None if the symbol doesn't match our source assets.
    """
    clean = symbol.upper().replace("-", "/").replace("_", "/")
    parts = clean.split("/")
    if parts:
        asset = parts[0]
        if asset in _SOURCE_ASSETS:
            return asset
        # Handle inverted symbols like XBTUSD
        if asset == "XBT":
            return "BTC"
    return None


# ── Cross-asset signal remapping ────────────────────────────────────────────


def _is_same_symbol(source_symbol: str, target_symbol: str) -> bool:
    """Check if source and target symbols resolve to the same asset.

    For cross-asset, we skip any pairing where source and target are the
    same asset (e.g., BTC/USD -> BTC/USD).
    """
    try:
        from .symbol_aliases import same_asset as sa
        return sa(source_symbol, target_symbol)
    except (ValueError, KeyError):
        return source_symbol.upper().replace("-", "/") == target_symbol.upper().replace("-", "/")


def _remap_signal_for_target(
    signal: TickSignalEvent,
    target_venue: str,
    target_symbol: str,
    pair_idx: int,
) -> TickSignalEvent:
    """Clone a source signal for a specific target pair.

    Direction propagation uses positive-beta: source long -> target long,
    source short -> target short.  The long-executable vs diagnostic-only
    marker is stored in the signal metadata and also determines the
    downstream evaluation path.
    """
    meta = dict(signal.metadata or {})
    meta["original_source_venue"] = signal.source_venue
    meta["original_source_symbol"] = signal.source_symbol

    # Long-executable if source direction is long/up (bullish impulse)
    long_executable = signal.direction == "long"

    return TickSignalEvent(
        signal_id=f"{signal.signal_id}_{pair_idx}",
        ts_event=signal.ts_event,
        source_venue=signal.source_venue,
        source_symbol=signal.source_symbol,
        target_venue=target_venue,
        target_symbol=target_symbol,
        asset=signal.asset,
        signal_type=signal.signal_type,
        direction=signal.direction,
        lookback_ms=signal.lookback_ms,
        threshold_bps=signal.threshold_bps,
        source_move_bps=signal.source_move_bps,
        source_start_price=signal.source_start_price,
        source_end_price=signal.source_end_price,
        strength=signal.strength,
        metadata={
            **meta,
            "long_executable": long_executable,
            "diagnostic_only": not long_executable,
        },
    )


# ── Overlap and range computation ───────────────────────────────────────────


def compute_overlap(
    source_stream: StreamHealth,
    target_stream: StreamHealth,
) -> dict[str, Any]:
    """Compute true overlap between source and target streams."""
    overlap = {}
    if source_stream.first_tick_ts and target_stream.first_tick_ts:
        overlap["overlap_start_ns"] = max(
            source_stream.first_tick_ts, target_stream.first_tick_ts
        )
        overlap["overlap_end_ns"] = min(
            source_stream.last_tick_ts, target_stream.last_tick_ts
        )
        if overlap["overlap_end_ns"] > overlap["overlap_start_ns"]:
            overlap["overlap_duration_ms"] = (
                overlap["overlap_end_ns"] - overlap["overlap_start_ns"]
            ) / _MS_TO_NS
        else:
            overlap["overlap_duration_ms"] = 0.0
    else:
        overlap["overlap_start_ns"] = None
        overlap["overlap_end_ns"] = None
        overlap["overlap_duration_ms"] = 0.0

    overlap["source_range_bps"] = source_stream.range_bps
    overlap["target_range_bps"] = target_stream.range_bps
    overlap["source_tick_count"] = source_stream.tick_count
    overlap["target_tick_count"] = target_stream.tick_count
    overlap["source_subscription_ok"] = source_stream.subscription_ok
    overlap["target_subscription_ok"] = target_stream.subscription_ok
    overlap["source_zero_tick"] = source_stream.zero_tick_warning
    overlap["target_zero_tick"] = target_stream.zero_tick_warning

    return overlap


# ── Cross-asset evaluation ──────────────────────────────────────────────────


@dataclass
class PairResult:
    """Aggregated result for one source->target pair."""

    pair_key: str
    overlap: dict[str, Any]
    signal_count: int
    long_executable_count: int
    diagnostic_only_count: int
    valid_returns: list[TickForwardReturn]
    valid_long_executable_returns: list[TickForwardReturn]
    valid_diagnostic_returns: list[TickForwardReturn]
    candidate_gate: dict[str, object] | None = None
    candidate_gate_long: dict[str, object] | None = None
    candidate_gate_diagnostic: dict[str, object] | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    data_issue_reasons: list[str] = field(default_factory=list)

    @property
    def has_true_overlap(self) -> bool:
        dur = self.overlap.get("overlap_duration_ms", 0)
        return dur is not None and dur > 0

    def has_sufficient_range(self, min_source_range_bps: float, min_target_range_bps: float) -> tuple[bool, str]:
        """Check whether actual source/target range meets the stress-test thresholds.

        Returns (False, reason) when either asset moved less than the minimum
        required to meaningfully test the stress-beta-lag mechanism.
        """
        source_bps = self.overlap.get("source_range_bps", 0) or 0
        target_bps = self.overlap.get("target_range_bps", 0) or 0
        if source_bps < min_source_range_bps:
            return False, f"source_range_{source_bps:.1f}bps<{min_source_range_bps:.0f}bps"
        if target_bps < min_target_range_bps:
            return False, f"target_range_{target_bps:.1f}bps<{min_target_range_bps:.0f}bps"
        return True, ""


# ── Verdict logic ───────────────────────────────────────────────────────────


@dataclass
class CrossAssetVerdict:
    """Top-level verdict for a cross-asset impulse run."""

    verdict: str
    reasons: list[str]
    total_signal_events: int
    total_long_executable: int
    total_diagnostic_only: int
    pairs_evaluated: int
    pairs_with_overlap: int
    pairs_with_sufficient_data: int
    pairs_with_events: int
    best_pair: str | None = None
    best_pair_mean_net: float | None = None
    worst_pair: str | None = None
    worst_pair_mean_net: float | None = None
    best_long_executable_group: str | None = None
    best_long_executable_mean_net: float | None = None
    best_diagnostic_group: str | None = None
    best_diagnostic_mean_net: float | None = None
    candidate_pairs: list[str] = field(default_factory=list)
    stream_health: dict[str, dict[str, Any]] = field(default_factory=dict)
    stream_warnings: list[str] = field(default_factory=list)
    same_symbol_skips: list[str] = field(default_factory=list)
    source_venues: list[str] = field(default_factory=list)
    source_symbols: list[str] = field(default_factory=list)
    target_venues: list[str] = field(default_factory=list)
    target_symbols: list[str] = field(default_factory=list)


def compute_verdict(
    pair_results: list[PairResult],
    stream_health: dict[str, StreamHealth],
    same_symbol_skips: list[str],
    source_venues: list[str],
    source_symbols: list[str],
    target_venues: list[str],
    target_symbols: list[str],
    min_source_range_bps: float,
    min_target_range_bps: float,
    min_events: int,
) -> CrossAssetVerdict:
    """Apply verdict logic across all evaluated pairs.

    Returns a CrossAssetVerdict with the appropriate verdict and reasons.
    """
    # Check stream health issues
    stream_warnings: list[str] = []
    has_failed_subscription = False
    has_zero_tick = False

    for key, sh in stream_health.items():
        if not sh.subscription_ok:
            has_failed_subscription = True
            stream_warnings.append(
                f"FAILED subscription: {key} (venue={sh.venue}, symbol={sh.symbol})"
            )
        if sh.zero_tick_warning or sh.tick_count == 0:
            has_zero_tick = True
            stream_warnings.append(
                f"ZERO ticks: {key} after capture window"
            )

    # Aggregate stats
    pairs_with_overlap = sum(1 for p in pair_results if p.has_true_overlap)
    pairs_with_sufficient = 0
    pairs_with_events = 0
    total_signals = 0
    total_long = 0
    total_diag = 0
    any_data_issue = False

    for p in pair_results:
        total_signals += p.signal_count
        total_long += p.long_executable_count
        total_diag += p.diagnostic_only_count

        if p.data_issue_reasons:
            any_data_issue = True
            continue

        ok, reason = p.has_sufficient_range(min_source_range_bps, min_target_range_bps)
        if not ok:
            continue

        pairs_with_sufficient += 1

        if p.signal_count >= min_events:
            pairs_with_events += 1

    best_pair = None
    best_pair_mean = None
    worst_pair = None
    worst_pair_mean = None
    best_long_group = None
    best_long_mean = None
    best_diag_group = None
    best_diag_mean = None
    candidate_pairs: list[str] = []

    for p in pair_results:
        if not p.valid_returns:
            continue

        nets = [r.net_return_bps for r in p.valid_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        if not nets:
            continue

        mean_net = statistics.mean(nets)
        if best_pair_mean is None or mean_net > best_pair_mean:
            best_pair = p.pair_key
            best_pair_mean = mean_net
        if worst_pair_mean is None or mean_net < worst_pair_mean:
            worst_pair = p.pair_key
            worst_pair_mean = mean_net

        # long-executable subset
        long_nets = [r.net_return_bps for r in p.valid_long_executable_returns
                     if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        if long_nets:
            long_mean = statistics.mean(long_nets)
            if best_long_mean is None or long_mean > best_long_mean:
                best_long_group = p.pair_key
                best_long_mean = long_mean

        # diagnostic subset
        diag_nets = [r.net_return_bps for r in p.valid_diagnostic_returns
                     if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        if diag_nets:
            diag_mean = statistics.mean(diag_nets)
            if best_diag_mean is None or diag_mean > best_diag_mean:
                best_diag_group = p.pair_key
                best_diag_mean = diag_mean

        if p.candidate_gate and p.candidate_gate.get("candidate", False):
            candidate_pairs.append(p.pair_key)

    # Determine verdict
    verdict, reasons = _determine_verdict(
        pairs_with_overlap=pairs_with_overlap,
        pairs_with_sufficient=pairs_with_sufficient,
        pairs_with_events=pairs_with_events,
        total_long=total_long,
        total_diag=total_diag,
        total_signals=total_signals,
        has_failed_subscription=has_failed_subscription,
        has_zero_tick=has_zero_tick,
        any_data_issue=any_data_issue,
        candidate_pairs=candidate_pairs,
        pair_results=pair_results,
        min_events=min_events,
        min_source_range_bps=min_source_range_bps,
        min_target_range_bps=min_target_range_bps,
    )

    return CrossAssetVerdict(
        verdict=verdict,
        reasons=reasons,
        total_signal_events=total_signals,
        total_long_executable=total_long,
        total_diagnostic_only=total_diag,
        pairs_evaluated=len(pair_results),
        pairs_with_overlap=pairs_with_overlap,
        pairs_with_sufficient_data=pairs_with_sufficient,
        pairs_with_events=pairs_with_events,
        best_pair=best_pair,
        best_pair_mean_net=best_pair_mean,
        worst_pair=worst_pair,
        worst_pair_mean_net=worst_pair_mean,
        best_long_executable_group=best_long_group,
        best_long_executable_mean_net=best_long_mean,
        best_diagnostic_group=best_diag_group,
        best_diagnostic_mean_net=best_diag_mean,
        candidate_pairs=candidate_pairs,
        stream_health={k: _stream_health_dict(v) for k, v in stream_health.items()},
        stream_warnings=stream_warnings,
        same_symbol_skips=same_symbol_skips,
        source_venues=source_venues,
        source_symbols=source_symbols,
        target_venues=target_venues,
        target_symbols=target_symbols,
    )


def _stream_health_dict(sh: StreamHealth) -> dict:
    return {
        "venue": sh.venue,
        "symbol": sh.symbol,
        "tick_count": sh.tick_count,
        "first_tick_ts": sh.first_tick_ts,
        "last_tick_ts": sh.last_tick_ts,
        "price_min": sh.price_min,
        "price_max": sh.price_max,
        "range_bps": sh.range_bps,
        "subscription_ok": sh.subscription_ok,
        "zero_tick_warning": sh.zero_tick_warning,
    }


def _safe_gate_float(gate: dict[str, Any] | None, key: str, default: float = -999.0) -> float:
    if not gate:
        return default
    value = gate.get(key)
    try:
        result = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _determine_verdict(
    pairs_with_overlap: int,
    pairs_with_sufficient: int,
    pairs_with_events: int,
    total_long: int,
    total_diag: int,
    total_signals: int,
    has_failed_subscription: bool,
    has_zero_tick: bool,
    any_data_issue: bool,
    candidate_pairs: list[str],
    pair_results: list[PairResult],
    min_events: int,
    min_source_range_bps: float,
    min_target_range_bps: float,
) -> tuple[str, list[str]]:
    """Determine the verdict based on aggregated evaluation results."""
    reasons: list[str] = []

    # Data/subscription issues
    if has_failed_subscription:
        reasons.append("one_or_more_streams_failed_subscription")
    if has_zero_tick:
        reasons.append("one_or_more_streams_produced_zero_ticks")

    # No overlap at all
    if pairs_with_overlap == 0:
        reasons.append("no_true_overlap_between_any_source_target_pairs")
        return "NEEDS_MORE_DATA", reasons

    # Check if all overlapping pairs have range below stress thresholds
    if pairs_with_sufficient == 0 and pairs_with_overlap > 0:
        ranges_ok = sum(
            1 for p in pair_results
            if p.has_true_overlap and p.has_sufficient_range(min_source_range_bps, min_target_range_bps)[0]
        )
        if ranges_ok == 0:
            reasons.append(
                f"insufficient_price_range: source<{min_source_range_bps}bps "
                f"or target<{min_target_range_bps}bps in all overlapping pairs"
            )
            return "NEEDS_MORE_DATA", reasons

    # Insufficient events across all pairs
    if pairs_with_events == 0:
        all_signals = sum(p.signal_count for p in pair_results)
        if all_signals == 0:
            reasons.append("no_signal_events_generated")
            return "NEEDS_MORE_DATA", reasons
        else:
            reasons.append(
                f"insufficient_events: {all_signals} total signals but "
                f"no pair reaches min_events={min_events} with valid forward returns"
            )
            return "NEEDS_MORE_DATA", reasons

    # We have sufficient data and events. Check candidate results.
    num_candidates = len(candidate_pairs)
    num_long = sum(1 for cp in candidate_pairs if any(r for r in pair_results if r.pair_key == cp and r.long_executable_count > 0))

    if num_candidates == 1 and total_long + total_diag >= min_events:
        # Only one pair looks promising
        # Check if it has enough events itself
        best_candidate = candidate_pairs[0]
        best_pair_data = next((p for p in pair_results if p.pair_key == best_candidate), None)
        if best_pair_data and best_pair_data.candidate_gate:
            event_count_val = best_pair_data.candidate_gate.get("event_count", 0)
            event_count = event_count_val if isinstance(event_count_val, int) else 0
            if event_count >= min_events:
                return "SINGLE_PAIR_CANDIDATE_DIAGNOSTIC", [
                    f"One source-target pair ({best_candidate}) passes candidate gates. "
                    f"Not confirmed across multiple assets/venues.",
                ]

    if num_candidates >= 1 and pairs_with_events >= 1:
        # At least one long-executable signal group passes
        has_long_candidate = any(
            p.long_executable_count > 0 and p.candidate_gate and p.candidate_gate.get("candidate", False)
            for p in pair_results
        )
        if has_long_candidate and num_candidates >= 1:
            # Check diversity: at least 2 target assets or 2 source assets or 2 venues
            candidate_pair_keys = set(candidate_pairs)
            unique_sources = set()
            unique_targets = set()
            unique_venues = set()
            for p in pair_results:
                if p.pair_key in candidate_pair_keys:
                    parts = p.pair_key.split("->")
                    unique_sources.add(parts[0])
                    if len(parts) > 1:
                        unique_targets.add(parts[1])
                        unique_venues.add(parts[1].split(":")[0])

            is_diverse = (
                len(unique_targets) >= 2
                or len(unique_sources) >= 2
                or len(unique_venues) >= 2
            )
            if is_diverse:
                return "CANDIDATE_FOR_LONGER_OBSERVATION", [
                    f"{num_candidates} candidate pair(s) pass gates with diversity "
                    f"(sources={len(unique_sources)}, targets={len(unique_targets)}, venues={len(unique_venues)}).",
                ]

    # If we have sufficient data but no candidates pass
    if pairs_with_sufficient >= 1 and pairs_with_events >= 1:
        # Check if the signal fails after costs
        any_positive = any(
            p.candidate_gate is not None
            and _safe_gate_float(p.candidate_gate, "mean_net_return_bps") > 0
            for p in pair_results
        )
        if not any_positive:
            return "REJECTED", [
                "Sufficient overlap, source movement, target movement, and event count exist, "
                "but signal fails after costs across all evaluated pairs.",
            ]

    # Fallback for edge cases
    return "NEEDS_MORE_DATA", [
        "Marginal results: overlap/events exist but do not clearly meet any verdict threshold.",
    ]


# ── Report generation ───────────────────────────────────────────────────────


def _format_optional_bps(value: float | None) -> str:
    return f"{value:.2f}" if value is not None and math.isfinite(value) else "N/A"


def generate_markdown_report(
    verdict_obj: CrossAssetVerdict,
    all_in_cost_bps: float,
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float,
    signal_types: list[str],
    lookbacks_ms: list[int],
    horizons_ms: list[int],
    cooldown_ms: int,
    baseline_window_ms: int,
    out_dir: str,
    num_pairs_evaluated: int,
) -> str:
    """Generate a human-readable markdown report for the cross-asset impulse study."""

    v = verdict_obj
    lines: list[str] = []

    lines.append("# Cross-asset Spot Impulse Lead-Lag — Research Report")
    lines.append("")
    lines.append("## Hypothesis")
    lines.append(
        "Large-cap spot trade-flow impulses in BTC/USD and ETH/USD may lead "
        "higher-beta spot altcoin movement on Kraken, Coinbase, and optionally "
        "Binance spot by 5s to 300s during volatile windows."
    )
    lines.append(
        "When BTC or ETH spot reprices violently, thinner high-beta spot alts "
        "may lag briefly before repricing in the same direction (positive-beta propagation)."
    )
    lines.append("")

    lines.append("## Safety Statement")
    lines.append("- **No orders** submitted")
    lines.append("- **No keys** or credentials required")
    lines.append("- **No private endpoints** accessed")
    lines.append("- **No derivatives execution**")
    lines.append("- **Public spot data only**")
    lines.append("- **Spot-only, no leverage, no margin, no perps, no futures, no CFDs, no options, no short execution**")
    lines.append("")

    lines.append("## Source Venues / Symbols")
    lines.append(f"- Venues: {', '.join(v.source_venues)}")
    lines.append(f"- Symbols: {', '.join(v.source_symbols)}")
    lines.append("")

    lines.append("## Target Venues / Symbols")
    lines.append(f"- Venues: {', '.join(v.target_venues)}")
    lines.append(f"- Symbols: {', '.join(v.target_symbols)}")
    lines.append("")

    lines.append("## Source→Target Matrix")
    lines.append("| Source | Target | Pairs |")
    lines.append("|--------|--------|-------|")
    for sv in v.source_venues:
        for ss in v.source_symbols:
            for tv in v.target_venues:
                for ts_ in v.target_symbols:
                    lines.append(f"| {sv} {ss} | {tv} {ts_} | evaluated |")
    lines.append("")

    lines.append("## Same-Symbol Skips")
    if v.same_symbol_skips:
        for skip in v.same_symbol_skips:
            lines.append(f"- {skip}")
    else:
        lines.append("- None (all evaluated cross-asset)")
    lines.append("")

    lines.append("## Stream Subscription Health")
    if v.stream_warnings:
        for w in v.stream_warnings:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("- All streams subscribed and producing ticks.")
    lines.append("")

    lines.append("## Zero-Tick Stream Warnings")
    zero_tick_streams = [k for k, sh in v.stream_health.items() if sh.get("zero_tick_warning") or sh.get("tick_count", 0) == 0]
    if zero_tick_streams:
        for k in zero_tick_streams:
            sh = v.stream_health[k]
            lines.append(f"- ⚠️ `{k}`: {sh.get('tick_count', 0)} ticks")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Overlap Windows per Pair")
    lines.append(f"- **Pairs evaluated:** {v.pairs_evaluated}")
    lines.append(f"- **Pairs with true overlap:** {v.pairs_with_overlap}")
    lines.append(f"- **Pairs with sufficient range:** {v.pairs_with_sufficient_data}")
    lines.append(f"- **Pairs with ≥ min_events signals:** {v.pairs_with_events}")
    lines.append("")

    lines.append("## Source & Target Range (bps)")
    lines.append(f"- **Source range threshold:** min={v.source_venues}")
    lines.append(f"- **Target range threshold:** min={v.target_venues}")
    lines.append("")

    lines.append("## Signal Parameters")
    lines.append(f"- **Signal types:** {', '.join(signal_types)}")
    lines.append(f"- **Lookbacks (ms):** {lookbacks_ms}")
    lines.append(f"- **Horizons (ms):** {horizons_ms}")
    lines.append(f"- **Cooldown (ms):** {cooldown_ms}")
    lines.append(f"- **Baseline window (ms):** {baseline_window_ms}")
    lines.append("")

    lines.append("## All-in Cost Wall")
    lines.append(f"- **Fee:** {fee_bps} bps")
    lines.append(f"- **Slippage:** {slippage_bps} bps")
    lines.append(f"- **Quote mismatch buffer:** {quote_mismatch_buffer_bps} bps")
    lines.append(f"- **All-in:** {all_in_cost_bps} bps")
    lines.append("")

    lines.append("## Event Counts")
    lines.append(f"- **Total signal events:** {v.total_signal_events}")
    lines.append(f"- **Long-executable signals:** {v.total_long_executable}")
    lines.append(f"- **Downside diagnostic signals:** {v.total_diagnostic_only}")
    lines.append("")

    lines.append("## Best Source-Target Pair")
    if v.best_pair:
        lines.append(f"- `{v.best_pair}`: mean net return = {_format_optional_bps(v.best_pair_mean_net)} bps")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Worst Source-Target Pair")
    if v.worst_pair:
        lines.append(f"- `{v.worst_pair}`: mean net return = {_format_optional_bps(v.worst_pair_mean_net)} bps")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Best Long-Executable Group")
    if v.best_long_executable_group:
        lines.append(f"- `{v.best_long_executable_group}`: mean net return = {_format_optional_bps(v.best_long_executable_mean_net)} bps")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Best Downside Diagnostic Group")
    if v.best_diagnostic_group:
        lines.append(f"- `{v.best_diagnostic_group}`: mean net return = {_format_optional_bps(v.best_diagnostic_mean_net)} bps")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Random Baseline Comparison")
    if v.candidate_pairs:
        lines.append(f"- **Candidate pairs:** {v.candidate_pairs}")
    else:
        lines.append("- No pairs exceed baseline.")
    lines.append("")

    lines.append("## Verdict")
    lines.append(f"**{v.verdict}**")
    lines.append("")
    for r in v.reasons:
        lines.append(f"- {r}")
    lines.append("")

    content = "\n".join(lines)

    report_path = Path(out_dir) / "cross_asset_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    return content
