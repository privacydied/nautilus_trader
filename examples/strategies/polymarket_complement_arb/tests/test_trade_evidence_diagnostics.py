"""
Tests for trade evidence diagnostics in the shadow observe pipeline.

The Polymarket data-api /trades endpoint is a global trade feed that returns the
latest N trades across all tokens. It does NOT support per-asset server-side
filtering — the ``asset``, ``conditionId``, and ``slug`` query parameters are
silently ignored. Trades for specific tokens only appear if they happen to be
among the most recent ~1000 global trades.

These tests validate that:
1. Trade evidence status classifications are correct
2. Diagnostics are written to summary and markdown output
3. No execution-client imports are introduced
4. run_live_guarded.py remains stubbed
"""

from __future__ import annotations

import ast
from pathlib import Path

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.models import BookSnapshot
from examples.strategies.polymarket_complement_arb.models import ComplementMarket
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    PUBLIC_TRADE_EVIDENCE_INSUFFICIENT,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import TRADE_EVIDENCE_EMPTY
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    TRADE_EVIDENCE_ENDPOINT_ERROR,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    TRADE_EVIDENCE_JOIN_FAILED,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import TRADE_EVIDENCE_READY
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    _compute_trade_evidence_status,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import run_shadow_observe
from examples.strategies.polymarket_complement_arb.shadow_execution import ShadowOpportunityResult
from examples.strategies.polymarket_complement_arb.shadow_execution import ShadowSufficiencyConfig
from examples.strategies.polymarket_complement_arb.shadow_execution import write_shadow_reports


# --- _compute_trade_evidence_status tests ---


def test_empty_trade_response_produces_empty_status():
    status = _compute_trade_evidence_status(
        trade_rows_total=0,
        missing_trade_data_events=0,
        total_opportunities=5,
        trade_fetch_success_count=3,
        trade_fetch_error_count=0,
        trade_fetch_attempt_count=3,
        non_dust_opportunity_count=5,
    )
    assert status == TRADE_EVIDENCE_EMPTY


def test_no_fetch_attempts_produces_empty():
    status = _compute_trade_evidence_status(
        trade_rows_total=0,
        missing_trade_data_events=0,
        total_opportunities=0,
        trade_fetch_success_count=0,
        trade_fetch_error_count=0,
        trade_fetch_attempt_count=0,
        non_dust_opportunity_count=0,
    )
    assert status == TRADE_EVIDENCE_EMPTY


def test_all_opportunities_missing_trade_data_produces_insufficient():
    status = _compute_trade_evidence_status(
        trade_rows_total=500,
        missing_trade_data_events=10,
        total_opportunities=10,
        trade_fetch_success_count=5,
        trade_fetch_error_count=0,
        trade_fetch_attempt_count=5,
        non_dust_opportunity_count=8,
    )
    assert status == PUBLIC_TRADE_EVIDENCE_INSUFFICIENT


def test_endpoint_error_produces_endpoint_error():
    status = _compute_trade_evidence_status(
        trade_rows_total=0,
        missing_trade_data_events=0,
        total_opportunities=0,
        trade_fetch_success_count=0,
        trade_fetch_error_count=5,
        trade_fetch_attempt_count=5,
        non_dust_opportunity_count=0,
    )
    assert status == TRADE_EVIDENCE_ENDPOINT_ERROR


def test_high_error_rate_produces_endpoint_error():
    status = _compute_trade_evidence_status(
        trade_rows_total=0,
        missing_trade_data_events=0,
        total_opportunities=0,
        trade_fetch_success_count=1,
        trade_fetch_error_count=4,
        trade_fetch_attempt_count=5,
        non_dust_opportunity_count=0,
    )
    assert status == TRADE_EVIDENCE_ENDPOINT_ERROR


def test_join_failure_when_missing_more_than_non_dust():
    status = _compute_trade_evidence_status(
        trade_rows_total=1000,
        missing_trade_data_events=5,
        total_opportunities=10,
        trade_fetch_success_count=5,
        trade_fetch_error_count=0,
        trade_fetch_attempt_count=5,
        non_dust_opportunity_count=3,
    )
    assert status == TRADE_EVIDENCE_JOIN_FAILED


def test_successful_trade_join_produces_ready():
    status = _compute_trade_evidence_status(
        trade_rows_total=1000,
        missing_trade_data_events=2,
        total_opportunities=10,
        trade_fetch_success_count=5,
        trade_fetch_error_count=0,
        trade_fetch_attempt_count=5,
        non_dust_opportunity_count=8,
    )
    assert status == TRADE_EVIDENCE_READY


# --- Diagnostics written to summary and markdown ---


def test_trade_diagnostics_written_to_summary(tmp_path: Path):
    """Trade evidence diagnostics appear in shadow summary enriched output."""
    results: list[ShadowOpportunityResult] = []
    trade_evidence = {
        "status": TRADE_EVIDENCE_EMPTY,
        "fetch_attempts": 3,
        "fetch_successes": 3,
        "fetch_empty_responses": 3,
        "fetch_errors": 0,
        "rows_fetched_total": 0,
        "rows_joined_to_opportunities": 0,
        "missing_trade_data_events": 0,
    }
    paths = write_shadow_reports(
        tmp_path,
        results,
        observer_window_count=5,
        sufficiency=ShadowSufficiencyConfig(5, 1, 1, 1, 1),
        trade_evidence=trade_evidence,
    )
    report_text = paths["report"].read_text()
    # Check markdown includes the trade evidence section
    assert "## Trade Evidence" in report_text
    assert TRADE_EVIDENCE_EMPTY in report_text
    assert "fetch attempts: 3" in report_text


def test_varied_trade_evidence_renderings(tmp_path: Path):
    """Different trade evidence status values produce appropriate markdown."""
    for status, keyword in [
        (TRADE_EVIDENCE_EMPTY, "no data"),
        (PUBLIC_TRADE_EVIDENCE_INSUFFICIENT, "did return some"),
        (TRADE_EVIDENCE_JOIN_FAILED, "fewer-than-expected"),
        (TRADE_EVIDENCE_READY, "successfully joined"),
    ]:
        sub = tmp_path / status.replace(" ", "_")
        sub.mkdir(parents=True, exist_ok=True)
        te = {
            "status": status,
            "fetch_attempts": 1,
            "fetch_successes": 1,
            "fetch_empty_responses": 0,
            "fetch_errors": 0,
            "rows_fetched_total": 100,
            "rows_joined_to_opportunities": 0,
            "missing_trade_data_events": 0,
        }
        paths = write_shadow_reports(
            sub,
            [],
            observer_window_count=5,
            sufficiency=ShadowSufficiencyConfig(5, 1, 1, 1, 1),
            trade_evidence=te,
        )
        report = paths["report"].read_text()
        assert keyword.lower() in report.lower(), f"Expected '{keyword}' in report for status {status}"


# --- No execution-client imports ---


def test_no_execution_client_imports_in_shadow_modules():
    """run_shadow_observe.py and shadow_execution.py have no exec imports."""
    banned = ["ExecutionClient", "LiveExec", "PolymarketLiveExec", "submit_order", "create_order"]
    for mod_name in ["run_shadow_observe.py", "shadow_execution.py"]:
        mod_path = Path(f"examples/strategies/polymarket_complement_arb/{mod_name}")
        assert mod_path.exists(), f"{mod_path} does not exist"
        text = mod_path.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    for banned_name in banned:
                        assert banned_name not in alias.name, f"{mod_name} imports {banned_name}"


# --- run_live_guarded.py remains guarded ---


def test_run_live_guarded_remains_stubbed():
    live_path = Path("examples/strategies/polymarket_complement_arb/run_live_guarded.py")
    live_text = live_path.read_text()
    assert "phase 7 stub" in live_text.lower()
    assert "No real orders placed" in live_text


# --- Smoke test: run_shadow_observe with mock client ---


def test_shadow_observe_with_mock_trade_evidence(tmp_path: Path):
    """run_shadow_observe with mocked client produces correct diagnostics."""
    import asyncio

    config = ComplementArbConfig(max_markets=2, min_observer_windows=1)
    market = ComplementMarket(
        condition_id="cond-1",
        market_slug="test-market",
        event_slug="test-event",
        question="Test?",
        yes_token_id="111",
        no_token_id="222",
        yes_instrument_id_str="cond-1-111.POLYMARKET",
        no_instrument_id_str="cond-1-222.POLYMARKET",
        neg_risk=False,
        active=True,
        closed=False,
        accepting_orders=True,
        end_date_iso="2027-01-01T00:00:00Z",
        minimum_tick_size=0.001,
        minimum_order_size=5.0,
        taker_fee_rate=0.02,
        category="test",
        liquidity_num=1000.0,
        volume_num=5000.0,
    )

    class MockClient:
        async def discover_markets(self, limit):
            return []

        async def fetch_book(self, token_id):
            return BookSnapshot(
                instrument_id_str=f"{token_id}.POLYMARKET",
                token_id=token_id,
                bids=[(0.48, 100.0), (0.47, 200.0)],
                asks=[(0.52, 100.0), (0.53, 200.0)],
                timestamp_ms=1000.0,
            )

        async def fetch_trades(self, after_ts=None, limit=1000):
            return []  # Empty global feed

    async def _run():
        return await run_shadow_observe(
            markets=[market],
            config=config,
            duration_secs=0.1,
            poll_interval_secs=0.01,
            client=MockClient(),
            output_root=str(tmp_path),
        )

    result = asyncio.run(_run())
    assert result is not None
    assert "run_id" in result


# --- Needs more data, not rejection ---


def test_missing_trade_data_produces_needs_more_data_not_rejection():
    """Missing trade evidence leads to NEEDS_MORE_DATA, not REJECTED."""
    from examples.strategies.polymarket_complement_arb.shadow_execution import (
        classify_shadow_verdict,
    )

    verdict = classify_shadow_verdict(
        observer_window_count=5,
        results=[],
        sufficiency=ShadowSufficiencyConfig(5, 50, 20, 30, 30),
    )
    assert verdict.verdict == "NEEDS_MORE_DATA"
    assert "MIN_PESSIMISTIC_PAIRED_FILLS_NOT_MET" in verdict.reasons
    assert "REJECTED" not in verdict.verdict
