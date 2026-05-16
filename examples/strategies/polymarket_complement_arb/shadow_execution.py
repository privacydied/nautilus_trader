"""
Pure observer-only shadow execution model for complement arb validation.

This module deliberately has no order submission, credential handling, execution
client imports, or Nautilus live-node dependencies. It models whether detected
same-condition YES+NO complement edges would have survived executable pricing,
queue position, paired-fill timing, and timeout/unwind losses.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from typing import Literal


class FillAssumption(StrEnum):
    PESSIMISTIC = "pessimistic"
    NEUTRAL = "neutral"
    OPTIMISTIC = "optimistic"


class FillStatus(StrEnum):
    NO_FILL = "no_fill"
    PARTIAL = "partial"
    FULL = "full"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


QuoteSide = Literal["BUY", "SELL"]
VerdictValue = Literal[
    "NEEDS_MORE_DATA",
    "REJECTED_FOR_CURRENT_LIVE_CONDITIONS",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
]


@dataclass(frozen=True)
class LegQuote:
    """Would-be resting maker quote for one complement leg."""

    token_side: Literal["YES", "NO"]
    quote_side: QuoteSide
    timestamp_ns: int
    price: float
    size: float
    depth_ahead: float
    best_bid: float
    best_ask: float
    executable_ask: float
    visible_depth_usdc: float


@dataclass(frozen=True)
class MarketTrade:
    """Public trade evidence used by the shadow fill model."""

    timestamp_ns: int
    price: float
    size: float | None
    side: TradeSide


@dataclass(frozen=True)
class LegFillResult:
    token_side: Literal["YES", "NO"]
    fill_model: FillAssumption
    status: FillStatus
    filled_size: float
    fill_price: float | None
    fill_ts: int | None
    cumulative_fillable_volume: float
    reject_reason: str | None = None


@dataclass(frozen=True)
class EdgeSurvivalResult:
    first_leg_fill_ts: int | None
    second_leg_fill_ts: int | None
    joint_fill_latency_ms: float | None
    edge_at_detection: float
    edge_at_first_fill: float | None
    edge_at_second_fill: float | None
    min_edge_during_joint_window: float | None
    edge_survived_until_second_leg: bool


@dataclass(frozen=True)
class ShadowOpportunity:
    """Detected complement opportunity plus shadow execution inputs."""

    run_id: str
    git_sha: str
    config_hash: str
    timestamp_ns: int
    condition_id: str
    market_slug: str
    yes_token_id: str
    no_token_id: str
    yes_quote: LegQuote
    no_quote: LegQuote
    sum_asks: float
    gross_edge_per_share: float
    fee_per_share: float
    leg_risk_buffer: float
    net_edge_per_share: float
    max_safe_shares: float
    one_leg_timeout_ms: float
    min_order_shares: float
    same_condition: bool = True
    stale: bool = False
    resolution_danger: bool = False
    neg_risk: bool = False


@dataclass(frozen=True)
class ShadowOpportunityResult:
    opportunity: ShadowOpportunity
    fill_model: FillAssumption
    yes_fill: LegFillResult
    no_fill: LegFillResult
    paired_fill: bool
    one_leg_fill: bool
    first_leg_fill_ts: int | None
    second_leg_fill_ts: int | None
    joint_fill_latency_ms: float | None
    edge_at_detection: float
    edge_at_first_fill: float | None
    edge_at_second_fill: float | None
    min_edge_during_joint_window: float | None
    edge_survived_until_second_leg: bool
    timeout_unwind_required: bool
    unwind_price: float | None
    unwind_loss_per_share: float
    realized_shadow_net_edge_per_share: float
    paired_fill_realized_net_edge_per_share: float | None
    reject_reason: str | None


@dataclass(frozen=True)
class ShadowSufficiencyConfig:
    min_observer_windows: int = 5
    min_detected_opportunities: int = 50
    min_pessimistic_paired_fills: int = 20
    min_same_condition_valid_opportunities: int = 30
    min_non_dust_opportunities: int = 30


@dataclass(frozen=True)
class ShadowVerdict:
    verdict: VerdictValue
    reasons: list[str]
    paired_gain: float
    unwind_loss: float
    net_shadow_harvest: float
    expected_edge_per_detected_opportunity: float
    raw: dict[str, Any]


def _is_trade_fill_compatible(quote: LegQuote, trade: MarketTrade) -> bool:
    if trade.size is None or trade.size <= 0:
        return False
    if trade.side == TradeSide.UNKNOWN:
        return False
    if quote.quote_side == "BUY":
        return trade.side == TradeSide.SELL and trade.price <= quote.price
    return trade.side == TradeSide.BUY and trade.price >= quote.price


def evaluate_pessimistic_fill(quote: LegQuote, trades: list[MarketTrade]) -> LegFillResult:
    """
    Evaluate conservative queue fill from public trade evidence.

    A BUY quote fills only after observed sell-side traded volume at-or-through
    the bid exceeds visible depth ahead at quote time. Full fill requires volume
    strictly greater than depth_ahead + our_quote_size. Ambiguous direction,
    size, event order, or missing evidence produces no fill.
    """
    cumulative = 0.0
    first_partial_ts: int | None = None
    last_price: float | None = None
    ordered = sorted(trades, key=lambda t: t.timestamp_ns)
    for trade in ordered:
        if trade.timestamp_ns < quote.timestamp_ns:
            continue
        if not _is_trade_fill_compatible(quote, trade):
            continue
        cumulative += float(trade.size or 0.0)
        last_price = trade.price
        if cumulative > quote.depth_ahead and first_partial_ts is None:
            first_partial_ts = trade.timestamp_ns
        if cumulative > quote.depth_ahead + quote.size:
            return LegFillResult(
                token_side=quote.token_side,
                fill_model=FillAssumption.PESSIMISTIC,
                status=FillStatus.FULL,
                filled_size=quote.size,
                fill_price=quote.price,
                fill_ts=trade.timestamp_ns,
                cumulative_fillable_volume=cumulative,
            )

    if cumulative > quote.depth_ahead:
        return LegFillResult(
            token_side=quote.token_side,
            fill_model=FillAssumption.PESSIMISTIC,
            status=FillStatus.PARTIAL,
            filled_size=max(0.0, min(quote.size, cumulative - quote.depth_ahead)),
            fill_price=quote.price if last_price is not None else None,
            fill_ts=first_partial_ts,
            cumulative_fillable_volume=cumulative,
        )

    return LegFillResult(
        token_side=quote.token_side,
        fill_model=FillAssumption.PESSIMISTIC,
        status=FillStatus.NO_FILL,
        filled_size=0.0,
        fill_price=None,
        fill_ts=None,
        cumulative_fillable_volume=cumulative,
        reject_reason="VISIBLE_DEPTH_AHEAD_NOT_CONSUMED",
    )


def _evaluate_neutral_fill(quote: LegQuote, trades: list[MarketTrade]) -> LegFillResult:
    cumulative = 0.0
    for trade in sorted(trades, key=lambda t: t.timestamp_ns):
        if trade.timestamp_ns < quote.timestamp_ns or not _is_trade_fill_compatible(quote, trade):
            continue
        cumulative += float(trade.size or 0.0)
        if cumulative >= quote.size:
            return LegFillResult(quote.token_side, FillAssumption.NEUTRAL, FillStatus.FULL, quote.size, quote.price, trade.timestamp_ns, cumulative)
    if cumulative > 0:
        return LegFillResult(quote.token_side, FillAssumption.NEUTRAL, FillStatus.PARTIAL, min(cumulative, quote.size), quote.price, None, cumulative)
    return LegFillResult(quote.token_side, FillAssumption.NEUTRAL, FillStatus.NO_FILL, 0.0, None, None, cumulative, "NO_COMPATIBLE_TRADE")


def _evaluate_optimistic_fill(quote: LegQuote, trades: list[MarketTrade]) -> LegFillResult:
    for trade in sorted(trades, key=lambda t: t.timestamp_ns):
        if trade.timestamp_ns >= quote.timestamp_ns and _is_trade_fill_compatible(quote, trade):
            return LegFillResult(quote.token_side, FillAssumption.OPTIMISTIC, FillStatus.FULL, quote.size, quote.price, trade.timestamp_ns, float(trade.size or 0.0))
    return LegFillResult(quote.token_side, FillAssumption.OPTIMISTIC, FillStatus.NO_FILL, 0.0, None, None, 0.0, "NO_TOP_OF_QUEUE_TOUCH")


def _evaluate_fill(quote: LegQuote, trades: list[MarketTrade], mode: FillAssumption) -> LegFillResult:
    if mode == FillAssumption.PESSIMISTIC:
        return evaluate_pessimistic_fill(quote, trades)
    if mode == FillAssumption.NEUTRAL:
        return _evaluate_neutral_fill(quote, trades)
    return _evaluate_optimistic_fill(quote, trades)


def _edge_at(ts: int | None, edge_at_detection: float, edge_timeline: list[tuple[int, float]]) -> float | None:
    if ts is None:
        return None
    edge = edge_at_detection
    for edge_ts, value in sorted(edge_timeline, key=lambda item: item[0]):
        if edge_ts <= ts:
            edge = value
        else:
            break
    return edge


def evaluate_edge_survival(
    yes_fill: LegFillResult,
    no_fill: LegFillResult,
    edge_at_detection: float,
    edge_timeline: list[tuple[int, float]] | None = None,
) -> EdgeSurvivalResult:
    """Evaluate joint two-leg edge survival, not two independent booleans."""
    timeline = edge_timeline or []
    fill_times = [ts for ts in (yes_fill.fill_ts, no_fill.fill_ts) if ts is not None]
    first_ts = min(fill_times) if fill_times else None
    second_ts = max(fill_times) if len(fill_times) == 2 else None
    latency = (second_ts - first_ts) / 1_000_000 if first_ts is not None and second_ts is not None else None
    edge_first = _edge_at(first_ts, edge_at_detection, timeline)
    edge_second = _edge_at(second_ts, edge_at_detection, timeline)
    window_edges = [edge_at_detection]
    if first_ts is not None and second_ts is not None:
        window_edges = [value for ts, value in timeline if first_ts <= ts <= second_ts]
        if edge_first is not None:
            window_edges.append(edge_first)
        if edge_second is not None:
            window_edges.append(edge_second)
    min_edge = min(window_edges) if window_edges else None
    survived = bool(second_ts is not None and min_edge is not None and min_edge > 0 and (edge_second or 0.0) > 0)
    return EdgeSurvivalResult(first_ts, second_ts, latency, edge_at_detection, edge_first, edge_second, min_edge, survived)


def _pre_fill_reject_reason(opportunity: ShadowOpportunity, midpoint_edge_used: bool) -> str | None:
    if midpoint_edge_used:
        return "MIDPOINT_EDGE_NOT_ALLOWED"
    if not opportunity.same_condition:
        return "NOT_SAME_CONDITION"
    if opportunity.neg_risk:
        return "NEGRISK_OR_CROSS_MARKET_NOT_ALLOWED"
    if opportunity.stale:
        return "STALE_BOOK"
    if opportunity.resolution_danger:
        return "RESOLUTION_DANGER_WINDOW"
    if opportunity.sum_asks >= 1.0 or opportunity.net_edge_per_share <= 0:
        return "NO_NET_EDGE_AFTER_COSTS"
    if opportunity.max_safe_shares < opportunity.min_order_shares:
        return "DUST_SIZE"
    return None


def evaluate_shadow_opportunity(
    opportunity: ShadowOpportunity,
    fill_model: FillAssumption,
    yes_trades: list[MarketTrade],
    no_trades: list[MarketTrade],
    *,
    edge_timeline: list[tuple[int, float]] | None = None,
    midpoint_edge_used: bool = False,
    unwind_price: float | None = None,
) -> ShadowOpportunityResult:
    """Evaluate one detected opportunity through the shadow execution model."""
    reject = _pre_fill_reject_reason(opportunity, midpoint_edge_used)
    yes_fill = _evaluate_fill(opportunity.yes_quote, yes_trades, fill_model)
    no_fill = _evaluate_fill(opportunity.no_quote, no_trades, fill_model)
    if reject is not None:
        empty_survival = EdgeSurvivalResult(None, None, None, opportunity.net_edge_per_share, None, None, None, False)
        return _build_result(opportunity, fill_model, yes_fill, no_fill, False, False, empty_survival, False, None, 0.0, 0.0, None, reject)

    survival = evaluate_edge_survival(yes_fill, no_fill, opportunity.net_edge_per_share, edge_timeline)
    full_yes = yes_fill.status == FillStatus.FULL
    full_no = no_fill.status == FillStatus.FULL
    one_leg = full_yes ^ full_no
    both_full = full_yes and full_no
    within_timeout = bool(survival.joint_fill_latency_ms is not None and survival.joint_fill_latency_ms <= opportunity.one_leg_timeout_ms)
    paired = bool(both_full and within_timeout and survival.edge_survived_until_second_leg)
    timeout_unwind_required = bool(one_leg or (both_full and not within_timeout))
    unwind_loss = 0.0
    if timeout_unwind_required:
        filled_quote = opportunity.yes_quote if full_yes else opportunity.no_quote
        if unwind_price is None:
            unwind_price = filled_quote.price
        if filled_quote.quote_side == "BUY":
            unwind_loss = round(max(0.0, filled_quote.price - unwind_price), 12)
        else:
            unwind_loss = round(max(0.0, unwind_price - filled_quote.price), 12)

    realized = 0.0
    paired_edge: float | None = None
    reject_reason: str | None = None
    if paired:
        paired_edge = min(opportunity.net_edge_per_share, survival.min_edge_during_joint_window or opportunity.net_edge_per_share)
        realized = paired_edge
        if realized <= 0:
            reject_reason = "REALIZED_NET_EDGE_NON_POSITIVE"
            paired = False
    elif one_leg:
        realized = -unwind_loss
        reject_reason = "ONE_LEG_TIMEOUT_UNWIND"
    elif both_full and not within_timeout:
        realized = -unwind_loss
        reject_reason = "SECOND_LEG_TIMEOUT"
    elif both_full and not survival.edge_survived_until_second_leg:
        reject_reason = "EDGE_DIED_BEFORE_SECOND_FILL"
    else:
        reject_reason = "PESSIMISTIC_NO_PAIRED_FILL" if fill_model == FillAssumption.PESSIMISTIC else "DIAGNOSTIC_NO_PAIRED_FILL"

    return _build_result(
        opportunity,
        fill_model,
        yes_fill,
        no_fill,
        paired,
        one_leg,
        survival,
        timeout_unwind_required,
        unwind_price,
        unwind_loss,
        realized,
        paired_edge if paired else None,
        reject_reason,
    )


def _build_result(
    opportunity: ShadowOpportunity,
    fill_model: FillAssumption,
    yes_fill: LegFillResult,
    no_fill: LegFillResult,
    paired: bool,
    one_leg: bool,
    survival: EdgeSurvivalResult,
    timeout_unwind_required: bool,
    unwind_price: float | None,
    unwind_loss: float,
    realized: float,
    paired_edge: float | None,
    reject_reason: str | None,
) -> ShadowOpportunityResult:
    return ShadowOpportunityResult(
        opportunity=opportunity,
        fill_model=fill_model,
        yes_fill=yes_fill,
        no_fill=no_fill,
        paired_fill=paired,
        one_leg_fill=one_leg,
        first_leg_fill_ts=survival.first_leg_fill_ts,
        second_leg_fill_ts=survival.second_leg_fill_ts,
        joint_fill_latency_ms=survival.joint_fill_latency_ms,
        edge_at_detection=survival.edge_at_detection,
        edge_at_first_fill=survival.edge_at_first_fill,
        edge_at_second_fill=survival.edge_at_second_fill,
        min_edge_during_joint_window=survival.min_edge_during_joint_window,
        edge_survived_until_second_leg=survival.edge_survived_until_second_leg,
        timeout_unwind_required=timeout_unwind_required,
        unwind_price=unwind_price,
        unwind_loss_per_share=unwind_loss,
        realized_shadow_net_edge_per_share=realized,
        paired_fill_realized_net_edge_per_share=paired_edge,
        reject_reason=reject_reason,
    )


def _sufficiency_reasons(
    observer_window_count: int,
    detected: int,
    same_condition_valid: int,
    non_dust: int,
    pessimistic_paired: int,
    cfg: ShadowSufficiencyConfig,
) -> list[str]:
    checks = [
        (observer_window_count, cfg.min_observer_windows, "MIN_OBSERVER_WINDOWS_NOT_MET"),
        (detected, cfg.min_detected_opportunities, "MIN_DETECTED_OPPORTUNITIES_NOT_MET"),
        (same_condition_valid, cfg.min_same_condition_valid_opportunities, "MIN_SAME_CONDITION_VALID_OPPORTUNITIES_NOT_MET"),
        (non_dust, cfg.min_non_dust_opportunities, "MIN_NON_DUST_OPPORTUNITIES_NOT_MET"),
        (pessimistic_paired, cfg.min_pessimistic_paired_fills, "MIN_PESSIMISTIC_PAIRED_FILLS_NOT_MET"),
    ]
    return [reason for actual, required, reason in checks if actual < required]


def _rejection_reasons(
    results: list[ShadowOpportunityResult],
    pess_paired: list[ShadowOpportunityResult],
    non_dust: int,
    paired_gain: float,
    unwind_loss: float,
    net_shadow_harvest: float,
    expected: float,
) -> list[str]:
    reasons = []
    if not any(r.opportunity.net_edge_per_share > 0 for r in results):
        reasons.append("NO_OPPORTUNITIES_SURVIVE_NET_EDGE_AFTER_COSTS")
    if not pess_paired:
        reasons.append("PESSIMISTIC_PASS_REQUIRED")
    paired_edges = [r.paired_fill_realized_net_edge_per_share or 0.0 for r in pess_paired]
    if paired_edges and statistics.median(paired_edges) <= 0:
        reasons.append("MEDIAN_PESSIMISTIC_REALIZED_EDGE_NON_POSITIVE")
    if unwind_loss >= paired_gain and (unwind_loss > 0 or paired_gain > 0):
        reasons.append("UNWIND_LOSS_ERASES_PAIRED_GAINS")
    if net_shadow_harvest <= 0:
        reasons.append("NET_SHADOW_HARVEST_NON_POSITIVE")
    if expected <= 0:
        reasons.append("EXPECTED_EDGE_PER_DETECTED_OPPORTUNITY_NON_POSITIVE")
    if any(r.fill_model != FillAssumption.PESSIMISTIC and r.paired_fill for r in results) and not pess_paired:
        reasons.append("ONLY_DIAGNOSTIC_FILL_MODE_PASSES")
    if not all(r.edge_survived_until_second_leg for r in pess_paired):
        reasons.append("EDGE_DOES_NOT_SURVIVE_JOINT_WINDOW")
    if non_dust == 0:
        reasons.append("OPPORTUNITIES_ONLY_AT_DUST_SIZE")
    return sorted(set(reasons))


def classify_shadow_verdict(
    *,
    observer_window_count: int,
    results: list[ShadowOpportunityResult],
    sufficiency: ShadowSufficiencyConfig | None = None,
) -> ShadowVerdict:
    """Classify a shadow run using precommitted sufficiency and rejection gates."""
    cfg = sufficiency or ShadowSufficiencyConfig()
    detected = len(results)
    same_condition_valid = sum(1 for r in results if r.opportunity.same_condition and not r.opportunity.neg_risk)
    non_dust = sum(1 for r in results if r.opportunity.max_safe_shares >= r.opportunity.min_order_shares)
    pess = [r for r in results if r.fill_model == FillAssumption.PESSIMISTIC]
    pess_paired = [r for r in pess if r.paired_fill]
    one_leg = [r for r in results if r.one_leg_fill or r.timeout_unwind_required]
    paired_gain = sum((r.paired_fill_realized_net_edge_per_share or 0.0) * r.opportunity.max_safe_shares for r in pess_paired)
    unwind_loss = sum(r.unwind_loss_per_share * r.opportunity.max_safe_shares for r in one_leg)
    net_shadow_harvest = paired_gain - unwind_loss
    expected = net_shadow_harvest / detected if detected else 0.0
    raw = {
        "observer_window_count": observer_window_count,
        "detected_opportunity_count": detected,
        "same_condition_valid_count": same_condition_valid,
        "non_dust_opportunity_count": non_dust,
        "pessimistic_paired_fill_count": len(pess_paired),
        "one_leg_fill_count": len(one_leg),
        "paired_gain": paired_gain,
        "unwind_loss": unwind_loss,
        "net_shadow_harvest": net_shadow_harvest,
        "expected_edge_per_detected_opportunity": expected,
    }

    needs_more = _sufficiency_reasons(
        observer_window_count,
        detected,
        same_condition_valid,
        non_dust,
        len(pess_paired),
        cfg,
    )
    if needs_more:
        return ShadowVerdict("NEEDS_MORE_DATA", needs_more, paired_gain, unwind_loss, net_shadow_harvest, expected, raw)

    reject = _rejection_reasons(
        results,
        pess_paired,
        non_dust,
        paired_gain,
        unwind_loss,
        net_shadow_harvest,
        expected,
    )
    if reject:
        return ShadowVerdict("REJECTED_FOR_CURRENT_LIVE_CONDITIONS", reject, paired_gain, unwind_loss, net_shadow_harvest, expected, raw)

    return ShadowVerdict("CANDIDATE_FOR_LONGER_OBSERVATION", ["PESSIMISTIC_AFTER_COST_EDGE_SURVIVED_WITH_POSITIVE_UNWIND_ADJUSTED_EXPECTED_EDGE"], paired_gain, unwind_loss, net_shadow_harvest, expected, raw)


def shadow_result_to_row(result: ShadowOpportunityResult) -> dict[str, Any]:
    opp = result.opportunity
    return {
        "git_sha": opp.git_sha,
        "config_hash": opp.config_hash,
        "run_id": opp.run_id,
        "timestamp": opp.timestamp_ns,
        "condition_id": opp.condition_id,
        "market_slug": opp.market_slug,
        "yes_token_id": opp.yes_token_id,
        "no_token_id": opp.no_token_id,
        "yes_best_ask": opp.yes_quote.best_ask,
        "no_best_ask": opp.no_quote.best_ask,
        "yes_best_bid": opp.yes_quote.best_bid,
        "no_best_bid": opp.no_quote.best_bid,
        "yes_depth_usdc": opp.yes_quote.visible_depth_usdc,
        "no_depth_usdc": opp.no_quote.visible_depth_usdc,
        "yes_depth_ahead": opp.yes_quote.depth_ahead,
        "no_depth_ahead": opp.no_quote.depth_ahead,
        "sum_asks": opp.sum_asks,
        "gross_edge_per_share": opp.gross_edge_per_share,
        "fee_per_share": opp.fee_per_share,
        "leg_risk_buffer": opp.leg_risk_buffer,
        "net_edge_per_share": opp.net_edge_per_share,
        "max_safe_shares": opp.max_safe_shares,
        "fill_model": result.fill_model.value,
        "yes_fill_status": result.yes_fill.status.value,
        "no_fill_status": result.no_fill.status.value,
        "paired_fill": result.paired_fill,
        "one_leg_fill": result.one_leg_fill,
        "first_leg_fill_ts": result.first_leg_fill_ts,
        "second_leg_fill_ts": result.second_leg_fill_ts,
        "joint_fill_latency_ms": result.joint_fill_latency_ms,
        "edge_at_detection": result.edge_at_detection,
        "edge_at_first_fill": result.edge_at_first_fill,
        "edge_at_second_fill": result.edge_at_second_fill,
        "min_edge_during_joint_window": result.min_edge_during_joint_window,
        "edge_survived_until_second_leg": result.edge_survived_until_second_leg,
        "timeout_unwind_required": result.timeout_unwind_required,
        "unwind_price": result.unwind_price,
        "unwind_loss_per_share": result.unwind_loss_per_share,
        "realized_shadow_net_edge_per_share": result.realized_shadow_net_edge_per_share,
        "paired_fill_realized_net_edge_per_share": result.paired_fill_realized_net_edge_per_share,
        "reject_reason": result.reject_reason,
    }


def _rate(numerator: float, denominator: float) -> dict[str, float | int | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator else None,
    }


def build_shadow_summary(
    results: list[ShadowOpportunityResult],
    *,
    observer_window_count: int,
    sufficiency: ShadowSufficiencyConfig | None = None,
) -> dict[str, Any]:
    verdict = classify_shadow_verdict(observer_window_count=observer_window_count, results=results, sufficiency=sufficiency)
    detected = len(results)
    paired = sum(1 for r in results if r.paired_fill)
    one_leg = sum(1 for r in results if r.one_leg_fill)
    same_condition = sum(1 for r in results if r.opportunity.same_condition)
    non_dust = sum(1 for r in results if r.opportunity.max_safe_shares >= r.opportunity.min_order_shares)
    pess_paired = sum(1 for r in results if r.fill_model == FillAssumption.PESSIMISTIC and r.paired_fill)
    return {
        "verdict": verdict.verdict,
        "verdict_reasons": verdict.reasons,
        "raw": verdict.raw,
        "sufficiency": asdict(sufficiency or ShadowSufficiencyConfig()),
        "rates": {
            "paired_fill_rate": _rate(paired, detected),
            "one_leg_fill_rate": _rate(one_leg, detected),
            "same_condition_valid_rate": _rate(same_condition, detected),
            "non_dust_rate": _rate(non_dust, detected),
            "pessimistic_paired_fill_rate": _rate(pess_paired, detected),
        },
        "paired_gain": verdict.paired_gain,
        "unwind_loss": verdict.unwind_loss,
        "net_shadow_harvest": verdict.net_shadow_harvest,
        "expected_edge_per_detected_opportunity": verdict.expected_edge_per_detected_opportunity,
    }


def write_shadow_reports(
    output_dir: str | Path,
    results: list[ShadowOpportunityResult],
    *,
    observer_window_count: int,
    sufficiency: ShadowSufficiencyConfig | None = None,
) -> dict[str, Path]:
    """Write shadow_opportunities.jsonl, shadow_summary.json, and shadow_report.md."""
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    opportunities_path = base / "shadow_opportunities.jsonl"
    summary_path = base / "shadow_summary.json"
    report_path = base / "shadow_report.md"

    with opportunities_path.open("w") as f:
        for result in results:
            f.write(json.dumps(shadow_result_to_row(result), sort_keys=True, default=str) + "\n")

    summary = build_shadow_summary(results, observer_window_count=observer_window_count, sufficiency=sufficiency)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
    report_path.write_text(_render_shadow_report(summary))
    return {"opportunities": opportunities_path, "summary": summary_path, "report": report_path}


def _render_shadow_report(summary: dict[str, Any]) -> str:
    return "\n".join([
        "# Polymarket Complement Arb Shadow Report",
        "",
        f"- verdict: {summary['verdict']}",
        f"- reasons: {', '.join(summary['verdict_reasons'])}",
        f"- paired_gain: {summary['paired_gain']}",
        f"- unwind_loss: {summary['unwind_loss']}",
        f"- net_shadow_harvest: {summary['net_shadow_harvest']}",
        f"- expected_edge_per_detected_opportunity: {summary['expected_edge_per_detected_opportunity']}",
        "- key_metric: paired_fill_realized_net_edge_per_share",
        "",
        "## Raw rates",
        json.dumps(summary["rates"], indent=2, sort_keys=True),
        "",
        "Theoretical edge is not treated as tradeable edge. Only pessimistic paired fills with positive realized shadow net edge can support CANDIDATE_FOR_LONGER_OBSERVATION.",
    ])
