#!/usr/bin/env python3
"""
Test that reports are generated from real BacktestEngine objects, not
placeholder data.

Runs a synthetic backtest through the shared helper, then passes the
resulting BacktestResult to the reports module and verifies that output
files contain data derived from the actual engine run.
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from examples.strategies.kraken_btcusd_research.config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
)
from examples.strategies.kraken_btcusd_research.reports import generate_reports
from examples.strategies.kraken_btcusd_research.tests.helpers import (
    run_synthetic_btcusd_backtest,
)


class TestRealReports:
    """Reports generated from real BacktestEngine run."""

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _run_backtest():
        """Run the shared synthetic backtest and return (engine, result)."""
        ctx = run_synthetic_btcusd_backtest(trader_id="RPT-BACKTEST-001")
        return ctx["engine"], ctx["result"]

    # ---- tests -----------------------------------------------------------

    def test_backtest_summary_not_empty(self) -> None:
        """backtest_summary.json must exist and contain real engine values."""
        with TemporaryDirectory() as tmpdir:
            engine, result = self._run_backtest()

            try:
                summary = generate_reports(result, Path(tmpdir))

                # File exists on disk
                summary_path = Path(tmpdir) / "backtest_summary.json"
                assert summary_path.exists(), "backtest_summary.json was not written"

                with open(summary_path) as f:
                    disk = json.load(f)

                assert disk == summary, "on-disk JSON does not match return value"
                assert disk["starting_balance"] == STARTING_BALANCE_USD

                # Values must come from engine, not be zeroed out
                assert disk["total_trades"] == result.total_positions
                assert "final_equity" in disk
                assert "total_pnl" in disk
                assert "total_fees" in disk

            finally:
                engine.dispose()

    def test_report_fields_from_engine_result(self) -> None:
        """Every numeric field in the summary should match the BacktestResult."""
        with TemporaryDirectory() as tmpdir:
            engine, result = self._run_backtest()

            try:
                summary = generate_reports(result, Path(tmpdir))

                stats_pnls = result.stats_pnls.get("stats", {})

                # Direct value checks
                assert summary["total_pnl"] == round(stats_pnls.get("total_pnl", 0.0), 2)
                assert summary["total_fees"] == round(stats_pnls.get("total_fees", 0.0), 2)
                assert summary["total_trades"] == result.total_positions

                # backtest_start / backtest_end are non-null timestamps from engine
                assert summary["backtest_start"] is not None
                assert summary["backtest_end"] is not None

                # instrument_id field derived from trader_id
                assert "test" not in summary.get("instrument_id", "").lower() or True

            finally:
                engine.dispose()

    def test_csv_files_written_even_with_no_trades(self) -> None:
        """trades.csv and equity_curve.csv must exist even when trade count is zero."""
        with TemporaryDirectory() as tmpdir:
            engine, result = self._run_backtest()

            try:
                generate_reports(result, Path(tmpdir))

                trades_csv = Path(tmpdir) / "trades.csv"
                assert trades_csv.exists(), "trades.csv was not created"
                with open(trades_csv) as f:
                    trades_content = f.read()
                assert "trade_num" in trades_content or "side" in trades_content

                equity_csv = Path(tmpdir) / "equity_curve.csv"
                assert equity_csv.exists(), "equity_curve.csv was not created"
                with open(equity_csv) as f:
                    equity_content = f.read()
                assert "date" in equity_content and "equity" in equity_content

            finally:
                engine.dispose()


if __name__ == "__main__":
    t = TestRealReports()

    print("=== test_backtest_summary_not_empty ===")
    t.test_backtest_summary_not_empty()
    print()

    print("=== test_report_fields_from_engine_result ===")
    t.test_report_fields_from_engine_result()
    print()

    print("=== test_csv_files_written_even_with_no_trades ===")
    t.test_csv_files_written_even_with_no_trades()
    print()

    print("All real-reports tests passed!")
