"""Observer-only shadow observe runner wiring tests."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import time
from pathlib import Path

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.models import BookSnapshot
from examples.strategies.polymarket_complement_arb.models import ComplementMarket
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    InMemoryPublicDataClient,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import (
    build_shadow_opportunity,
)
from examples.strategies.polymarket_complement_arb.run_shadow_observe import run_shadow_observe
from examples.strategies.polymarket_complement_arb.shadow_execution import ShadowSufficiencyConfig


def market() -> ComplementMarket:
    return ComplementMarket(
        condition_id="cond-shadow",
        market_slug="shadow-same-condition",
        event_slug="shadow-event",
        question="Shadow test?",
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_instrument_id_str="cond-shadow-yes-token.POLYMARKET",
        no_instrument_id_str="cond-shadow-no-token.POLYMARKET",
        neg_risk=False,
        active=True,
        closed=False,
        accepting_orders=True,
        end_date_iso=None,
        minimum_tick_size=0.001,
        minimum_order_size=5.0,
        taker_fee_rate=0.0,
        category="test",
        liquidity_num=1000.0,
        volume_num=1000.0,
    )


def book(token: str, ts_ms: float | None = None) -> BookSnapshot:
    return BookSnapshot(
        instrument_id_str=f"{token}.POLYMARKET",
        token_id=token,
        bids=[(0.48, 20.0), (0.47, 100.0)],
        asks=[(0.49, 80.0), (0.50, 100.0)],
        timestamp_ms=ts_ms if ts_ms is not None else __import__("time").time() * 1000,
    )


def test_build_shadow_opportunity_uses_detector_and_executable_books():
    config = ComplementArbConfig(
        min_top_depth_usdc=1.0,
        min_order_usdc=1.0,
        min_net_edge_per_share=0.001,
        leg_risk_buffer_per_share=0.0,
        signing_latency_buffer_per_share=0.0,
        gas_redeem_buffer_per_pair=0.0,
        one_leg_timeout_ms=1_000.0,
    )
    opp = build_shadow_opportunity(
        market(),
        book("yes-token"),
        book("no-token"),
        config,
        run_id="run-shadow",
        git_sha="abc123",
        config_hash="cfg123",
        now_ms=1_000.0,
        ts_event_ns=1_000_000_000,
    )
    assert opp is not None
    assert opp.same_condition is True
    assert opp.yes_quote.best_bid == 0.48
    assert opp.yes_quote.best_ask == 0.49
    assert opp.yes_quote.depth_ahead == 20.0
    assert opp.no_quote.depth_ahead == 20.0
    assert opp.net_edge_per_share > 0


def test_shadow_observe_runner_writes_reports_without_private_env_vars(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("POLYMARKET_"):
            monkeypatch.delenv(key, raising=False)

    mkt = market()
    client = InMemoryPublicDataClient(
        books={
            "yes-token": [book("yes-token"), book("yes-token")],
            "no-token": [book("no-token"), book("no-token")],
        },
        trades={
            "yes-token": [
                {"timestamp": time.time() + 0.1, "price": 0.48, "size": 221.0, "side": "SELL", "asset": "yes-token", "conditionId": "cond-shadow"},
            ],
            "no-token": [
                {"timestamp": time.time() + 0.2, "price": 0.48, "size": 221.0, "side": "SELL", "asset": "no-token", "conditionId": "cond-shadow"},
            ],
        },
    )
    result = asyncio.run(
        run_shadow_observe(
            markets=[mkt],
            config=ComplementArbConfig(
                min_top_depth_usdc=1.0,
                min_order_usdc=1.0,
                min_net_edge_per_share=0.001,
                leg_risk_buffer_per_share=0.0,
                signing_latency_buffer_per_share=0.0,
                gas_redeem_buffer_per_pair=0.0,
                one_leg_timeout_ms=1_000.0,
            ),
            duration_secs=0.0,
            poll_interval_secs=0.0,
            client=client,
            output_root=tmp_path,
            run_id="shadow-test-run",
            git_sha="abc123",
            observer_window_count=1,
            sufficiency=ShadowSufficiencyConfig(1, 1, 1, 1, 1),
        ),
    )
    report_dir = Path(result["report_path"])
    assert (report_dir / "shadow_opportunities.jsonl").exists()
    assert (report_dir / "shadow_summary.json").exists()
    assert (report_dir / "shadow_report.md").exists()
    metadata = json.loads((report_dir / "run_metadata.json").read_text())
    assert metadata["git_sha"] == "abc123"
    assert metadata["config_hash"]
    assert metadata["run_arguments"]["duration_secs"] == 0.0
    assert metadata["eligible_same_condition_pairs"] == 1
    summary = json.loads((report_dir / "shadow_summary.json").read_text())
    assert summary["raw"]["detected_opportunity_count"] >= 1
    assert result["pessimistic_paired_fills"] >= 1


def test_missing_trade_data_needs_more_data_not_crash(tmp_path):
    mkt = market()
    client = InMemoryPublicDataClient(
        books={"yes-token": [book("yes-token")], "no-token": [book("no-token")]},
        trades={},
    )
    result = asyncio.run(
        run_shadow_observe(
            markets=[mkt],
            config=ComplementArbConfig(
                min_top_depth_usdc=1.0,
                min_order_usdc=1.0,
                min_net_edge_per_share=0.001,
                leg_risk_buffer_per_share=0.0,
                signing_latency_buffer_per_share=0.0,
                gas_redeem_buffer_per_pair=0.0,
            ),
            duration_secs=0.0,
            poll_interval_secs=0.0,
            client=client,
            output_root=tmp_path,
            run_id="shadow-missing-trades",
            git_sha="abc123",
            observer_window_count=1,
            sufficiency=ShadowSufficiencyConfig(5, 50, 20, 30, 30),
        ),
    )
    assert result["verdict"] == "NEEDS_MORE_DATA"
    assert result["missing_trade_data_events"] >= 1
    summary = json.loads((Path(result["report_path"]) / "shadow_summary.json").read_text())
    assert summary["verdict"] == "NEEDS_MORE_DATA"


def test_no_execution_client_imports_in_shadow_observe_runner():
    path = Path("examples/strategies/polymarket_complement_arb/run_shadow_observe.py")
    tree = ast.parse(path.read_text())
    banned_parts = [("Polymarket", "ExecutionClient"), ("Live", "ExecutionClient"), ("Trading", "Node"), ("Order", "Factory")]
    banned = {"".join(parts) for parts in banned_parts}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not any(alias.name in banned for alias in node.names)
        if isinstance(node, ast.Import):
            assert not any(alias.name in banned for alias in node.names)
    content = path.read_text()
    assert "submit_order" not in content
    assert "private_key" not in content
