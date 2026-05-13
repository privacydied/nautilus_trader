import json
from pathlib import Path

import pytest

from examples.strategies.polymarket_btcusd_arb.settlement_predictor import SettlementPredictor

FIXTURE = Path(__file__).parent / "fixtures" / "settlement_cases.json"


def _load_cases():
    return json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["case_id"])
def test_settlement_predictor_parity_with_rust_fixture(case):
    predictor = SettlementPredictor()
    result = None
    for observation in case["inputs"]["observations"]:
        now_ms = observation["now_ms"]
        view = {key: value for key, value in observation.items() if key != "now_ms"}
        result = predictor.predict(view, now_ms)

    assert result is not None
    expected = case["expected"]
    assert result.probability_yes == pytest.approx(expected["probability_yes"], abs=1e-12)
    assert result.confidence == pytest.approx(expected["confidence"], abs=1e-12)
    assert result.direction == expected["direction"]
    assert result.score == pytest.approx(expected["score"], abs=1e-12)
    assert result.in_no_trade_band is expected["in_no_trade_band"]
    assert list(result.contributing_signals) == expected["contributing_signals"]
    assert getattr(result, "cvd_strength") == expected["cvd_strength"]
    assert getattr(result, "cvd_agrees") is expected["cvd_agrees"]
    assert getattr(result, "funding_bias") == expected["funding_bias"]
    assert getattr(result, "direction_stable") is expected["direction_stable"]
    assert getattr(result, "liq_contradicts") is expected["liq_contradicts"]
    assert getattr(result, "flush_cooling_detected") is expected["flush_cooling_detected"]
    assert 0.0 <= result.confidence <= 1.0


def test_settlement_predictor_waits_for_five_samples():
    predictor = SettlementPredictor()
    for idx in range(4):
        assert predictor.predict({"spot_mid": 100_000.0 + idx}, 1_700_000_000_000 + idx) is None
