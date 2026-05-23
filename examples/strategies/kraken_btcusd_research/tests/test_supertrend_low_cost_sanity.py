from pathlib import Path

import numpy as np

from examples.strategies.kraken_btcusd_research import supertrend_low_cost_sanity as st


def test_wilder_rma_matches_seeded_smoothing():
    values = np.array([1.0, 2.0, 3.0, 6.0, 10.0], dtype=float)

    rma = st.wilder_rma(values, period=3)

    assert np.isnan(rma[0])
    assert np.isnan(rma[1])
    assert rma[2] == 2.0
    assert rma[3] == (2.0 * 2.0 + 6.0) / 3.0
    assert rma[4] == (((2.0 * 2.0 + 6.0) / 3.0) * 2.0 + 10.0) / 3.0


def test_supertrend_state_transition_on_tiny_synthetic_ohlc_sequence():
    bars = st.BarArrays(
        timestamps=np.arange(8, dtype=float),
        open=np.array([10.0, 10.0, 10.0, 10.0, 8.0, 8.0, 12.0, 13.0]),
        high=np.array([10.2, 10.2, 10.2, 10.2, 8.2, 8.2, 12.2, 13.2]),
        low=np.array([9.8, 9.8, 9.8, 9.8, 7.8, 7.8, 11.8, 12.8]),
        close=np.array([10.0, 10.0, 10.0, 10.0, 8.0, 8.0, 12.0, 13.0]),
        volume=np.ones(8),
    )

    result = st.compute_supertrend(bars, atr_period=3, multiplier=1.0)

    assert result.trend.tolist() == [1, 1, 1, 1, -1, -1, 1, 1]
    assert result.buy_signals.tolist() == [False, False, False, False, False, False, True, False]
    assert result.sell_signals.tolist() == [False, False, False, False, True, False, False, False]


def test_low_cost_accounting_is_nine_bps_round_trip():
    trade = st.compute_trade_metrics(entry_price=100.0, exit_price=101.0, entry_fee_bps=4.5, exit_fee_bps=4.5)

    assert round(trade.gross_bps, 10) == 100.0
    assert trade.fee_bps == 9.0
    assert round(trade.net_bps, 10) == 91.0


def test_forbidden_verdicts_are_not_emitted(tmp_path):
    summary = st.build_no_data_summary(
        data_source_path="missing.csv",
        report_dir=tmp_path,
        git_sha="abc123",
        branch="test-branch",
    )
    rendered = st.render_summary_markdown(summary)

    for forbidden in st.FORBIDDEN_VERDICTS:
        assert forbidden not in summary["status"]
    assert summary["non_promotion_statement"] == st.NON_PROMOTION_STATEMENT
    assert st.STATUS_NO_DATA in rendered


def test_new_files_do_not_contain_guarded_runtime_strings():
    package_dir = Path(st.__file__).resolve().parent
    new_files = [
        package_dir / "supertrend_low_cost_sanity.py",
        package_dir / "tests" / "test_supertrend_low_cost_sanity.py",
    ]
    forbidden = ("client =", "wall" + "et", "private" + "_key", "secret" + "_key", "api" + "_key", "submit" + "_order", "market" + "_order")

    for path in new_files:
        text = path.read_text()
        if path.name.startswith("test_"):
            text = "\n".join(line for line in text.splitlines() if "forbidden =" not in line and "private" + "_key" not in line and "submit" + "_order" not in line)
        for token in forbidden:
            assert token not in text
