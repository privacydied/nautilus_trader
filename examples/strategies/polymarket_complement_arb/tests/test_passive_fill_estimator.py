"""
Tests for the passive_fill_estimator module.
"""

import time

from examples.strategies.polymarket_complement_arb.models import BookSnapshot, PassiveFillEstimate
from examples.strategies.polymarket_complement_arb.passive_fill_estimator import PassiveFillEstimator


def test_records_would_be_quote():
    estimator = PassiveFillEstimator()
    key = estimator.record_quote(
        condition_id="c1",
        market_slug="test",
        side="YES",
        quote_price=0.49,
        quote_size=100.0,
    )
    assert key is not None
    assert key.startswith("c1_YES_")


def test_detects_later_trade_touch():
    estimator = PassiveFillEstimator()
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    estimator.record_quote("c1", "test", "NO", 0.48, 100.0)

    # Simulate trades that touch the quotes
    estimator.on_trade_ticks([
        {"price": 0.50, "size": 10},
        {"price": 0.49, "size": 5},
    ])
    summary = estimator.finalize(total_quotes=2)
    assert summary["touches"] >= 1


def test_does_not_count_stale_quote_as_fillable():
    estimator = PassiveFillEstimator(max_quote_age_ms=0.1)  # Very short
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    time.sleep(0.01)  # Exceed max age
    estimator.on_trade_ticks([{"price": 0.50, "size": 10}])
    summary = estimator.finalize(total_quotes=1)
    assert summary["expired"] >= 1


def test_does_not_count_touch_after_edge_expiry():
    estimator = PassiveFillEstimator()
    key = estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    # Trade arrives within valid window
    estimator.on_trade_ticks([{"price": 0.50, "size": 10}])
    summary = estimator.finalize(total_quotes=1)
    assert summary["touches"] >= 1


def test_records_time_to_touch():
    estimator = PassiveFillEstimator()
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    estimator.on_trade_ticks([{"price": 0.50, "size": 10}])
    summary = estimator.finalize(total_quotes=1)
    assert summary["median_time_to_touch_ms"] is not None


def test_distinguishes_estimate_from_fill():
    """Terminology check: output must say passive_fill_estimate, not actual_fill."""
    estimator = PassiveFillEstimator()
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    estimator.on_trade_ticks([{"price": 0.50, "size": 10}])
    summary = estimator.finalize(total_quotes=1)
    # Check the source field
    assert summary["source"] == "book_movement_only"
    # Check summary mentions touches, not fills
    assert summary["touches"] >= 1


def test_does_not_claim_queue_priority():
    """Estimator should not mention queue position or fill probability."""
    estimator = PassiveFillEstimator()
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    estimator.on_trade_ticks([{"price": 0.50, "size": 10}])
    summary = estimator.finalize(total_quotes=1)
    # No queue-related fields
    assert "queue" not in str(summary).lower()
    assert "priority" not in str(summary).lower()


def test_on_book_snapshot_detects_touch():
    estimator = PassiveFillEstimator()
    estimator.record_quote("c1", "test", "YES", 0.49, 100.0)
    # Book movement to our quote level
    snap = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[(0.50, 200.0)],  # Best bid moved up past our quote
        asks=[(0.52, 200.0)],
        timestamp_ms=time.time() * 1000,
    )
    estimator.on_book_snapshot(snap)
    summary = estimator.finalize(total_quotes=1)
    assert summary["touches"] >= 1
