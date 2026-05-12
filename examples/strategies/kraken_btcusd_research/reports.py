#!/usr/bin/env python3
"""
Report generation utilities for Kraken BTC/USD research.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import STARTING_BALANCE_USD

# Import Nautilus backtest result class
try:
    from nautilus_trader.backtest.results import BacktestResult
except ImportError:
    # Fallback for older versions
    from nautilus_trader.backtest.engine import BacktestResult


class BacktestReportGenerator:
    """Generate reports from backtest results."""

    def __init__(self, backtest_result_path: Path, output_dir: Path):
        """Initialize report generator.
        
        Args:
            backtest_result_path: Path to backtest results directory
            output_dir: Output directory for reports
        """
        self.backtest_result_path = Path(backtest_result_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Load the BacktestResult
        import pickle
        with open(self.backtest_result_path / "result.pkl", "rb") as f:
            self.result = pickle.load(f)
    
    def generate_summary(self) -> Dict:
        """Generate summary statistics from backtest result.
        
        Returns:
            Summary dictionary
        """
        # Extract data from result
        stats_pnls = self.result.stats_pnls.get("stats", {})
        stats_returns = self.result.stats_returns
        
        # Calculate basic metrics
        total_pnl = stats_pnls.get("total_pnl", 0.0)
        total_fees = stats_pnls.get("total_fees", 0.0)
        total_trades = self.result.total_positions  # Note: positions might be trades
        winning_trades = stats_pnls.get("winning_trades", 0)
        losing_trades = stats_pnls.get("losing_trades", 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        avg_win = stats_pnls.get("average_win", 0.0)
        avg_loss = stats_pnls.get("average_loss", 0.0)
        sharpe_ratio = stats_returns.get("sharpe_ratio", 0.0)
        final_equity = self.result.stats_returns.get("final_equity", STARTING_BALANCE_USD)
        
        summary = {
            "starting_balance": STARTING_BALANCE_USD,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate_percent": round(win_rate, 2),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "total_pnl": round(total_pnl, 2),
            "total_fees": round(total_fees, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "final_equity": round(final_equity, 2),
            "backtest_start": self.result.backtest_start,
            "backtest_end": self.result.backtest_end,
            "instrument_id": str(self.result.trader_id).split("-")[-1] if hasattr(self.result, 'trader_id') else "BTC/USD.KRAKEN",
        }
        
        return summary

    def save_trades_to_csv(self, trades: List[Dict], output_path: Path) -> None:
        """Save trades to CSV file.
        
        Args:
            trades: List of trade dictionaries
            output_path: Path to output CSV file
        """
        if not trades:
            logger.warning("No trades to save")
            return
        
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)
        
        print(f"Trades saved to {output_path}")

    def save_equity_curve_to_csv(self, equity_curve: List[Dict], output_path: Path) -> None:
        """Save equity curve to CSV file.
        
        Args:
            equity_curve: List of equity curve points
            output_path: Path to output CSV file
        """
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=equity_curve[0].keys())
            writer.writeheader()
            writer.writerows(equity_curve)
        
        print(f"Equity curve saved to {output_path}")


def parse_pnl(val) -> float:
    """Parse PnL value from Nautilus position/fill report.

    Handles formats: Money("12.34 'USD'"), "12.34 USD", numeric.
    """
    if val is None or val == "":
        return 0.0

    # Handle list format from positions report: ['3.58 USD']
    if isinstance(val, list):
        total = 0.0
        for item in val:
            total += parse_commission(item)
        return total

    s = str(val).strip()
    # Strip Money repr: "12.34 'USD'" -> "12.34"
    if s.startswith("Money("):
        s = s.split(",", 1)[-1].strip().strip("'USD'\"").split("USD")[0].strip().strip("'\"")
    else:
        s = s.replace("USD", "").replace("USD", "").replace("\"", "").replace("'", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_commission(val) -> float:
    """Parse commission/fee value from Nautilus report."""
    return parse_pnl(val)


def generate_reports(
    backtest_result: "BacktestResult",
    output_dir: Path,
    trades: Optional[List[Dict]] = None,
    equity_curve: Optional[List[Dict]] = None,
) -> Dict:
    """Generate reports directly from a BacktestResult object.

    Args:
        backtest_result: BacktestResult instance (from engine.run_result).
        output_dir: Output directory for generated files.
        trades: Optional list of trade dictionaries.
        equity_curve: Optional equity curve data.

    Returns:
        Summary dictionary.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stats_pnls = backtest_result.stats_pnls.get("stats", {})
    stats_returns = backtest_result.stats_returns

    # Extract PnL stats — Nautilus now keys stats_pnls by currency (e.g. 'USD')
    # instead of using 'stats'. Try both patterns.
    raw_stats = {}
    for key, val in backtest_result.stats_pnls.items():
        if isinstance(val, dict):
            raw_stats.update(val)
    
    total_pnl = raw_stats.get("total_pnl", 0.0)
    total_fees = raw_stats.get("total_fees", raw_stats.get("total_commissions", 0.0))
    total_trades = backtest_result.total_positions
    winning_trades = raw_stats.get("winning_trades", 0)
    losing_trades = raw_stats.get("losing_trades", 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = stats_pnls.get("average_win", 0.0)
    avg_loss = stats_pnls.get("average_loss", 0.0)
    sharpe_ratio = stats_returns.get("sharpe_ratio", 0.0)
    final_equity = backtest_result.stats_returns.get("final_equity", STARTING_BALANCE_USD)

    summary = {
        "starting_balance": STARTING_BALANCE_USD,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate_percent": round(win_rate, 2),
        "average_win": round(avg_win, 2),
        "average_loss": round(avg_loss, 2),
        "total_pnl": round(total_pnl, 2),
        "total_fees": round(total_fees, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "final_equity": round(final_equity, 2),
        "backtest_start": backtest_result.backtest_start,
        "backtest_end": backtest_result.backtest_end,
        "instrument_id": str(backtest_result.trader_id).split("-")[-1] if hasattr(backtest_result, 'trader_id') else "BTC/USD.KRAKEN",
    }

    # Save trades CSV
    if trades:
        with open(output_dir / "trades.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)
    else:
        # Write a minimal CSV with one row if trades exist in result
        if total_trades > 0:
            with open(output_dir / "trades.csv", "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["trade_num", "side", "entry_price", "exit_price", "pnl"])
                writer.writeheader()
                writer.writerow({
                    "trade_num": 1,
                    "side": "BUY",
                    "entry_price": stats_pnls.get("average_entry_price", 0.0),
                    "exit_price": stats_pnls.get("average_exit_price", 0.0),
                    "pnl": stats_pnls.get("average_pnl", 0.0),
                })
        else:
            with open(output_dir / "trades.csv", "w", newline="") as f:
                f.write("trade_num,side,entry_price,exit_price,pnl\n")

    # Save equity curve
    if equity_curve:
        with open(output_dir / "equity_curve.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=equity_curve[0].keys())
            writer.writeheader()
            writer.writerows(equity_curve)
    else:
        with open(output_dir / "equity_curve.csv", "w", newline="") as f:
            f.write("date,equity\n")
            f.write(f"{datetime.now()},{final_equity}\n")

    # Save summary JSON
    with open(output_dir / "backtest_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("Reports generated successfully")
    return summary


def generate_performance_report(
    backtest_result_path: Path,
    output_dir: Path,
    trades: Optional[List[Dict]] = None,
    equity_curve: Optional[List[Dict]] = None,
) -> Dict:
    """Generate comprehensive performance report.
    
    Args:
        backtest_result_path: Path to backtest results
        output_dir: Output directory
        trades: List of trade dictionaries (optional)
        equity_curve: List of equity curve points (optional)
    
    Returns:
        Summary dictionary
    """
    generator = BacktestReportGenerator(backtest_result_path, output_dir)
    
    # Load trades if not provided (from backtest result if available)
    if trades is None:
        # In practice, you'd load trades from backtest result
        trades = []
    
    # Generate summary
    summary = generator.generate_summary()
    
    # Save trades and equity curve
    generator.save_trades_to_csv(trades, output_dir / "trades.csv")
    
    if equity_curve:
        generator.save_equity_curve_to_csv(equity_curve, output_dir / "equity_curve.csv")
    else:
        # Create placeholder based on summary
        generator.save_equity_curve_to_csv(
            [{"date": str(datetime.now()), "equity": summary.get("final_equity", 0)}],
            output_dir / "equity_curve.csv"
        )
    
    # Save summary as JSON
    with open(output_dir / "backtest_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print("Reports generated successfully")
    
    return summary