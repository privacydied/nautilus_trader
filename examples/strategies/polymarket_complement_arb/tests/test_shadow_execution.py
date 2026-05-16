"""Shadow execution harness tests for observer-only complement arb validation."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from examples.strategies.polymarket_complement_arb.shadow_execution import FillAssumption
from examples.strategies.polymarket_complement_arb.shadow_execution import FillStatus
from examples.strategies.polymarket_complement_arb.shadow_execution import LegQuote
from examples.strategies.polymarket_complement_arb.shadow_execution import MarketTrade
from examples.strategies.polymarket_complement_arb.shadow_execution import ShadowOpportunity
from examples.strategies.polymarket_complement_arb.shadow_execution import ShadowSufficiencyConfig
from examples.strategies.polymarket_complement_arb.shadow_execution import TradeSide
from examples.strategies.polymarket_complement_arb.shadow_execution import classify_shadow_verdict
from examples.strategies.polymarket_complement_arb.shadow_execution import evaluate_pessimistic_fill
from examples.strategies.polymarket_complement_arb.shadow_execution import (
    evaluate_shadow_opportunity,
)
from examples.strategies.polymarket_complement_arb.shadow_execution import write_shadow_reports


def quote(side: str = "YES", price: float = 0.48, size: float = 100.0, depth: float = 50.0) -> LegQuote:
    return LegQuote(
        token_side=side,
        quote_side="BUY",
        timestamp_ns=1_000_000_000,
        price=price,
        size=size,
        depth_ahead=depth,
        best_bid=price,
        best_ask=price + 0.02,
        executable_ask=price + 0.02,
        visible_depth_usdc=price * size,
    )


def opportunity(**overrides) -> ShadowOpportunity:
    data = {
        "run_id": "run-test",
        "git_sha": "abc123",
        "config_hash": "cfg123",
        "timestamp_ns": 1_000_000_000,
        "condition_id": "cond-1",
        "market_slug": "same-condition-market",
        "yes_token_id": "yes-token",
        "no_token_id": "no-token",
        "yes_quote": quote("YES", 0.48, 100.0, 20.0),
        "no_quote": quote("NO", 0.49, 100.0, 20.0),
        "sum_asks": 0.97,
        "gross_edge_per_share": 0.03,
        "fee_per_share": 0.004,
        "leg_risk_buffer": 0.003,
        "net_edge_per_share": 0.023,
        "max_safe_shares": 100.0,
        "one_leg_timeout_ms": 1_000.0,
        "min_order_shares": 10.0,
    }
    data.update(overrides)
    return ShadowOpportunity(**data)


# Detector and edge discipline covered at shadow intake boundary.
def test_no_edge_when_sum_asks_at_or_above_one():
    result = evaluate_shadow_opportunity(
        opportunity(sum_asks=1.00, gross_edge_per_share=0.0, net_edge_per_share=-0.01),
        FillAssumption.PESSIMISTIC,
        [],
        [],
    )
    assert result.reject_reason == "NO_NET_EDGE_AFTER_COSTS"
    assert result.paired_fill is False


def test_edge_only_when_yes_no_asks_fees_and_buffers_clear_one():
    result = evaluate_shadow_opportunity(
        opportunity(sum_asks=0.995, gross_edge_per_share=0.005, fee_per_share=0.004, leg_risk_buffer=0.003, net_edge_per_share=-0.002),
        FillAssumption.PESSIMISTIC,
        [],
        [],
    )
    assert result.reject_reason == "NO_NET_EDGE_AFTER_COSTS"


def test_midpoint_prices_cannot_create_shadow_edge():
    result = evaluate_shadow_opportunity(
        opportunity(sum_asks=1.01, gross_edge_per_share=0.04, net_edge_per_share=0.03),
        FillAssumption.PESSIMISTIC,
        [],
        [],
        midpoint_edge_used=True,
    )
    assert result.reject_reason == "MIDPOINT_EDGE_NOT_ALLOWED"


def test_stale_books_insufficient_depth_resolution_danger_cross_condition_and_negrisk_rejected():
    cases = [
        ({"stale": True}, "STALE_BOOK"),
        ({"max_safe_shares": 5.0}, "DUST_SIZE"),
        ({"resolution_danger": True}, "RESOLUTION_DANGER_WINDOW"),
        ({"same_condition": False}, "NOT_SAME_CONDITION"),
        ({"neg_risk": True}, "NEGRISK_OR_CROSS_MARKET_NOT_ALLOWED"),
    ]
    for overrides, reason in cases:
        result = evaluate_shadow_opportunity(opportunity(**overrides), FillAssumption.PESSIMISTIC, [], [])
        assert result.reject_reason == reason


# Pessimistic queue fill.
def test_print_at_quote_does_not_fill_until_depth_ahead_consumed():
    fill = evaluate_pessimistic_fill(
        quote(price=0.48, size=100.0, depth=50.0),
        [MarketTrade(timestamp_ns=1_000_000_100, price=0.48, size=50.0, side=TradeSide.SELL)],
    )
    assert fill.status == FillStatus.NO_FILL
    assert fill.filled_size == 0.0


def test_partial_fill_only_after_cumulative_volume_exceeds_depth_ahead():
    fill = evaluate_pessimistic_fill(
        quote(price=0.48, size=100.0, depth=50.0),
        [MarketTrade(timestamp_ns=1_000_000_100, price=0.48, size=80.0, side=TradeSide.SELL)],
    )
    assert fill.status == FillStatus.PARTIAL
    assert fill.filled_size == 30.0


def test_full_fill_only_after_depth_ahead_plus_quote_size_consumed():
    fill = evaluate_pessimistic_fill(
        quote(price=0.48, size=100.0, depth=50.0),
        [MarketTrade(timestamp_ns=1_000_000_100, price=0.47, size=151.0, side=TradeSide.SELL)],
    )
    assert fill.status == FillStatus.FULL
    assert fill.filled_size == 100.0


def test_ambiguous_direction_missing_evidence_and_insufficient_size_no_fill():
    q = quote(price=0.48, size=100.0, depth=50.0)
    assert evaluate_pessimistic_fill(q, [MarketTrade(1_000_000_100, 0.48, 999.0, TradeSide.UNKNOWN)]).status == FillStatus.NO_FILL
    assert evaluate_pessimistic_fill(q, []).status == FillStatus.NO_FILL
    assert evaluate_pessimistic_fill(q, [MarketTrade(1_000_000_100, 0.48, 49.0, TradeSide.SELL)]).status == FillStatus.NO_FILL


# Joint two-leg window.
def test_one_leg_fill_creates_residual_exposure():
    result = evaluate_shadow_opportunity(
        opportunity(),
        FillAssumption.PESSIMISTIC,
        [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)],
        [],
    )
    assert result.paired_fill is False
    assert result.one_leg_fill is True
    assert result.timeout_unwind_required is True
    assert result.reject_reason == "ONE_LEG_TIMEOUT_UNWIND"


def test_both_legs_fill_but_edge_dies_before_second_fill_rejected():
    result = evaluate_shadow_opportunity(
        opportunity(),
        FillAssumption.PESSIMISTIC,
        [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)],
        [MarketTrade(1_000_000_300, 0.49, 200.0, TradeSide.SELL)],
        edge_timeline=[(1_000_000_000, 0.023), (1_000_000_200, -0.001), (1_000_000_300, 0.020)],
    )
    assert result.paired_fill is False
    assert result.edge_survived_until_second_leg is False
    assert result.min_edge_during_joint_window == -0.001
    assert result.reject_reason == "EDGE_DIED_BEFORE_SECOND_FILL"


def test_both_legs_fill_within_timeout_and_edge_alive_counts():
    result = evaluate_shadow_opportunity(
        opportunity(),
        FillAssumption.PESSIMISTIC,
        [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)],
        [MarketTrade(1_000_000_300, 0.49, 200.0, TradeSide.SELL)],
        edge_timeline=[(1_000_000_000, 0.023), (1_000_000_200, 0.020), (1_000_000_300, 0.018)],
    )
    assert result.paired_fill is True
    assert result.first_leg_fill_ts == 1_000_000_100
    assert result.second_leg_fill_ts == 1_000_000_300
    assert result.joint_fill_latency_ms == 0.0002
    assert result.min_edge_during_joint_window == 0.018
    assert result.realized_shadow_net_edge_per_share > 0
    assert result.reject_reason is None


def test_timeout_and_bad_unwind_can_turn_theoretical_edge_into_loss():
    result = evaluate_shadow_opportunity(
        opportunity(),
        FillAssumption.PESSIMISTIC,
        [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)],
        [],
        unwind_price=0.44,
    )
    assert result.timeout_unwind_required is True
    assert result.unwind_loss_per_share == 0.04
    assert result.realized_shadow_net_edge_per_share == -0.04


# Verdict and reports.
def test_verdict_needs_more_data_uses_split_sufficiency_gates():
    cfg = ShadowSufficiencyConfig(min_observer_windows=5, min_detected_opportunities=50, min_pessimistic_paired_fills=20, min_same_condition_valid_opportunities=30, min_non_dust_opportunities=30)
    verdict = classify_shadow_verdict(
        observer_window_count=4,
        results=[],
        sufficiency=cfg,
    )
    assert verdict.verdict == "NEEDS_MORE_DATA"
    assert "MIN_OBSERVER_WINDOWS_NOT_MET" in verdict.reasons


def test_unwind_arithmetic_can_reject_even_with_paired_fills():
    good = evaluate_shadow_opportunity(
        opportunity(),
        FillAssumption.PESSIMISTIC,
        [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)],
        [MarketTrade(1_000_000_300, 0.49, 200.0, TradeSide.SELL)],
        edge_timeline=[(1_000_000_000, 0.023), (1_000_000_300, 0.018)],
    )
    bad = evaluate_shadow_opportunity(opportunity(), FillAssumption.PESSIMISTIC, [MarketTrade(1_000_000_100, 0.48, 200.0, TradeSide.SELL)], [], unwind_price=0.20)
    verdict = classify_shadow_verdict(observer_window_count=5, results=[good, bad], sufficiency=ShadowSufficiencyConfig(5, 1, 1, 1, 1))
    assert verdict.verdict == "REJECTED_FOR_CURRENT_LIVE_CONDITIONS"
    assert verdict.paired_gain <= verdict.unwind_loss
    assert "UNWIND_LOSS_ERASES_PAIRED_GAINS" in verdict.reasons


def test_optimistic_or_neutral_only_pass_cannot_promote_candidate():
    opt = evaluate_shadow_opportunity(opportunity(), FillAssumption.OPTIMISTIC, [], [])
    neutral = evaluate_shadow_opportunity(opportunity(), FillAssumption.NEUTRAL, [], [])
    verdict = classify_shadow_verdict(observer_window_count=5, results=[opt, neutral], sufficiency=ShadowSufficiencyConfig(5, 1, 0, 1, 1))
    assert verdict.verdict == "REJECTED_FOR_CURRENT_LIVE_CONDITIONS"
    assert "PESSIMISTIC_PASS_REQUIRED" in verdict.reasons


def test_pessimistic_positive_repeat_can_be_candidate_for_longer_observation():
    results = []
    for idx in range(2):
        ts = 1_000_000_000 + idx * 10_000_000
        results.append(evaluate_shadow_opportunity(
            opportunity(timestamp_ns=ts),
            FillAssumption.PESSIMISTIC,
            [MarketTrade(ts + 100, 0.48, 200.0, TradeSide.SELL)],
            [MarketTrade(ts + 300, 0.49, 200.0, TradeSide.SELL)],
            edge_timeline=[(ts, 0.023), (ts + 300, 0.018)],
        ))
    verdict = classify_shadow_verdict(observer_window_count=5, results=results, sufficiency=ShadowSufficiencyConfig(5, 2, 2, 2, 2))
    assert verdict.verdict == "CANDIDATE_FOR_LONGER_OBSERVATION"


def test_shadow_reports_written_with_required_fields_and_raw_denominators(tmp_path: Path):
    result = evaluate_shadow_opportunity(opportunity(), FillAssumption.PESSIMISTIC, [], [])
    paths = write_shadow_reports(tmp_path, [result], observer_window_count=5, sufficiency=ShadowSufficiencyConfig(5, 1, 1, 1, 1))
    assert paths["opportunities"].name == "shadow_opportunities.jsonl"
    assert paths["summary"].name == "shadow_summary.json"
    assert paths["report"].name == "shadow_report.md"
    row = json.loads(paths["opportunities"].read_text().splitlines()[0])
    for key in ["git_sha", "config_hash", "run_id", "condition_id", "yes_best_ask", "no_best_ask", "paired_fill_realized_net_edge_per_share", "reject_reason"]:
        assert key in row
    assert row["reject_reason"]
    summary = json.loads(paths["summary"].read_text())
    assert summary["rates"]["paired_fill_rate"]["numerator"] == 0
    assert summary["rates"]["paired_fill_rate"]["denominator"] == 1
    assert "verdict" in summary
    assert "paired_fill_realized_net_edge_per_share" in paths["report"].read_text()


def test_shadow_module_is_observer_only_and_run_live_guarded_remains_stubbed():
    module_path = Path("examples/strategies/polymarket_complement_arb/shadow_execution.py")
    live_path = Path("examples/strategies/polymarket_complement_arb/run_live_guarded.py")
    tree = ast.parse(module_path.read_text())
    imported = "\n".join(ast.get_source_segment(module_path.read_text(), n) or "" for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    banned = ["ExecutionClient", "LiveExec", "PolymarketLiveExec", "submit_order", "create_order"]
    assert not any(word in imported for word in banned)
    live_text = live_path.read_text()
    assert "phase 7 stub" in live_text.lower()
    assert "No real orders placed" in live_text
