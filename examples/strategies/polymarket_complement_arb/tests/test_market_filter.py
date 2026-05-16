"""
Tests for the market_filter module.
"""

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.market_filter import extract_complement_markets


def _make_market(
    condition_id="cond_001",
    slug="test-market",
    question="Will X happen?",
    active=True,
    closed=False,
    neg_risk=False,
    outcomes=("YES", "NO"),
    token_ids=("token_yes", "token_no"),
    accepting_orders=True,
    taker_fee_rate=0.03,
):
    """Helper to create a minimal Gamma market dict."""
    return {
        "conditionId": condition_id,
        "slug": slug,
        "question": question,
        "active": active,
        "closed": closed,
        "negRisk": neg_risk,
        "outcomes": list(outcomes),
        "clobTokenIds": list(token_ids),
        "acceptingOrders": str(accepting_orders).lower(),
        "orderPriceMinTickSize": 0.001,
        "orderMinSize": 5,
        "feeSchedule": {"rate": str(taker_fee_rate)},
        "events": [{"slug": "test-event"}],
    }


def test_parses_binary_yes_no_market():
    config = ComplementArbConfig(max_markets=10)
    market = _make_market()
    eligible, skips = extract_complement_markets([market], config)
    assert len(eligible) == 1
    assert len(skips) == 0
    assert eligible[0].condition_id == "cond_001"
    assert eligible[0].yes_token_id == "token_yes"
    assert eligible[0].no_token_id == "token_no"
    assert eligible[0].taker_fee_rate == 0.03


def test_rejects_non_binary_markets():
    config = ComplementArbConfig(max_markets=10)
    # Single outcome
    market = _make_market(outcomes=("YES",), token_ids=("token_yes",))
    eligible, skips = extract_complement_markets([market], config)
    assert len(eligible) == 0
    assert any("not binary" in s.reason for s in skips)


def test_rejects_negrisk_markets():
    config = ComplementArbConfig(max_markets=10)
    market = _make_market(neg_risk=True)
    eligible, skips = extract_complement_markets([market], config)
    assert len(eligible) == 0
    assert any("negRisk" in s.reason for s in skips)


def test_rejects_missing_condition_id():
    config = ComplementArbConfig(max_markets=10)
    market = _make_market(condition_id="")
    eligible, _ = extract_complement_markets([market], config)
    assert len(eligible) == 0


def test_rejects_missing_token_ids():
    config = ComplementArbConfig(max_markets=10)
    market = _make_market(outcomes=("YES", "NO"), token_ids=("token_yes", ""))
    eligible, skips = extract_complement_markets([market], config)
    assert len(eligible) == 0
    assert any("missing" in s.reason for s in skips)


def test_logs_skip_reason():
    config = ComplementArbConfig(max_markets=10)
    market1 = _make_market(condition_id="c1", slug="good")
    market2 = _make_market(condition_id="c2", slug="bad", active=False)
    eligible, skips = extract_complement_markets([market1, market2], config)
    assert len(eligible) == 1
    assert len(skips) >= 1
    assert any("not active" in s.reason for s in skips)


def test_supports_slug_allowlist():
    config = ComplementArbConfig(max_markets=10, market_slug_allowlist=("good",))
    market1 = _make_market(condition_id="c1", slug="good")
    market2 = _make_market(condition_id="c2", slug="bad")
    eligible, _ = extract_complement_markets([market1, market2], config)
    assert len(eligible) == 1
    assert eligible[0].market_slug == "good"


def test_supports_event_slug_allowlist():
    config = ComplementArbConfig(max_markets=10, event_slug_allowlist=("good-event",))
    market1 = _make_market(condition_id="c1")
    # market1 gets "test-event" from default events data
    market2 = _make_market(condition_id="c2", slug="market2")
    market2["events"] = [{"slug": "other-event"}]
    eligible, _ = extract_complement_markets([market1, market2], config)
    assert len(eligible) == 0  # test-event != good-event


def test_enforces_max_markets():
    config = ComplementArbConfig(max_markets=2)
    markets = [
        _make_market(condition_id=f"c{i}", slug=f"market{i}")
        for i in range(5)
    ]
    eligible, _ = extract_complement_markets(markets, config)
    assert len(eligible) <= 2


def test_deduplicates_condition_id():
    config = ComplementArbConfig(max_markets=10)
    markets = [
        _make_market(condition_id="cond_001", slug="market1"),
        _make_market(condition_id="cond_001", slug="market2"),
    ]
    eligible, _ = extract_complement_markets(markets, config)
    assert len(eligible) == 1


def test_rejects_when_not_accepting_orders():
    config = ComplementArbConfig(max_markets=10)
    market = _make_market(accepting_orders=False)
    eligible, skips = extract_complement_markets([market], config)
    assert len(eligible) == 0
    assert any("accepting" in s.reason.lower() for s in skips)
