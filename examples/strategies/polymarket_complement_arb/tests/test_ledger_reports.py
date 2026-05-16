"""
Tests for ledger and report generation.
"""
import json
import os
import tempfile

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.ledger import Ledger
from examples.strategies.polymarket_complement_arb.models import LedgerEntry
from examples.strategies.polymarket_complement_arb.reports import (
    compute_config_hash,
    generate_run_id,
    generate_run_summary,
)


def test_ledger_writes_jsonl():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = Ledger("test_run", base_dir=os.path.join(tmpdir, "reports", "test_run"))
        entry = LedgerEntry(
            run_id="test_run",
            timestamp_ns=1234567890,
            mode="backtest",
            event_type="market_discovered",
            condition_id="c1",
            market_slug="test",
        )
        ledger.write_ledger_entry(entry)
        filepath = os.path.join(ledger.base_dir, "ledger.jsonl")
        assert os.path.exists(filepath)
        with open(filepath) as f:
            line = f.readline().strip()
            data = json.loads(line)
            assert data["run_id"] == "test_run"
            assert data["event_type"] == "market_discovered"


def test_ledger_writes_skipped_markets():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = Ledger("test_run", base_dir=os.path.join(tmpdir, "reports", "test_run"))
        ledger.write_skipped_market("c1", "test", "not_active")
        filepath = os.path.join(ledger.base_dir, "skipped_markets.jsonl")
        assert os.path.exists(filepath)


def test_ledger_writes_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = Ledger("test_run", base_dir=os.path.join(tmpdir, "reports", "test_run"))
        config = ComplementArbConfig()
        ledger.write_config(vars(config))
        filepath = os.path.join(ledger.base_dir, "config.json")
        assert os.path.exists(filepath)
        with open(filepath) as f:
            data = json.load(f)
            assert data["mode"] == "observe"


def test_run_summary_generated():
    config = ComplementArbConfig()
    summary = generate_run_summary(
        run_id="test_run",
        git_sha="abc123",
        mode="backtest",
        config=config,
        markets_discovered=[],
        skipped_reasons=[],
        opportunities=[],
        rejected_opportunities=[],
        passive_estimates=[],
        passive_summary={"total_quotes_recorded": 0, "touches": 0, "crosses": 0, "expired": 0, "touch_rate_pct": 0.0, "source": "book_movement_only"},
        adapter_implementation="python",
        depth_mode="top_of_book_only",
        passive_estimate_source="book_movement_only",
        run_duration_secs=10.0,
    )
    assert "Run Summary" in summary
    assert "Limitations" in summary


def test_config_hash_stable():
    config = ComplementArbConfig()
    hash1 = compute_config_hash(config)
    hash2 = compute_config_hash(config)
    assert hash1 == hash2
    assert len(hash1) == 16


def test_config_hash_different_for_different_config():
    c1 = ComplementArbConfig(max_order_usdc=100)
    c2 = ComplementArbConfig(max_order_usdc=200)
    assert compute_config_hash(c1) != compute_config_hash(c2)


def test_run_id_format():
    run_id = generate_run_id()
    assert run_id.startswith("run_")
    assert len(run_id) > 10


def test_skip_reasons_counted():
    from examples.strategies.polymarket_complement_arb.models import MarketSkipReason
    reasons = [
        MarketSkipReason("c1", "m1", "not active"),
        MarketSkipReason("c2", "m2", "closed"),
    ]
    assert len(reasons) == 2
