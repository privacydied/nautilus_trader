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
    """Parse PnL value from Nautilus position report. Returns 0.0 if unavailable."""
    if val is None or val == "":
        return 0.0
    s = str(val).strip().replace(",", "")
    # Handle formats like "-20.41 USD", "140.71 USD", or "['24.01 USD']"
    # Strip python list repr if present
    if s.startswith("['") and s.endswith("']"):
        s = s[2:-2].strip()
    elif s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    # Remove " USD" suffix
    s = s.replace(" USD", "").replace("USD", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0

def parse_commission(val) -> float:
    """Parse commission/fee value from Nautilus position report."""
    if val is None or val == "":
        return 0.0
    s = str(val).strip().replace(",", "")
    # Handle formats like "['24.01 USD']", "[24.01 USD]"
    if s.startswith("['") and s.endswith("']"):
        s = s[2:-2].strip()
    elif s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    s = s.replace(" USD", "").replace("USD", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def generate_reports(
    backtest_result: "BacktestResult",
    output_dir: Path,
    engine=None,  # Pass engine for fills/positions reports when available
    *,
    trades=None,  # legacy
    equity_curve=None,  # legacy
) -> Dict:
    """Generate reports from a BacktestResult, using engine trade/position data when available."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Extract stats from Nautilus reports (the real data) ---
    fills_df = None
    positions_df = None
    if engine is not None:
        try:
            fills_df = engine.trader.generate_order_fills_report()
        except Exception:
            pass
        try:
            positions_df = engine.trader.generate_positions_report()
        except Exception:
            pass

    total_trades = backtest_result.total_positions
    total_orders = backtest_result.total_orders

    # Calculate PnL metrics from position report
    total_pnl = 0.0
    total_fees = 0.0
    win_count = 0
    loss_count = 0
    win_total = 0.0
    loss_total = 0.0

    if positions_df is not None and len(positions_df) > 0 and "realized_pnl" in positions_df.columns:
        for _, row in positions_df.iterrows():
            pnl = row.get("realized_pnl", None)
            pnl_val = parse_pnl(pnl)
            total_pnl += pnl_val
            if pnl_val > 0:
                win_count += 1
                win_total += pnl_val
            elif pnl_val < 0:
                loss_count += 1
                loss_total += pnl_val

            # Commission
            comm = row.get("commissions", None)
            comm_val = parse_commission(comm)
            total_fees += comm_val

    avg_win = win_total / win_count if win_count > 0 else 0.0
    avg_loss = loss_total / loss_count if loss_count > 0 else 0.0
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0

    stats_returns = backtest_result.stats_returns
    sharpe = stats_returns.get("sharpe_ratio", 0.0) if stats_returns else 0.0
    final_eq = stats_returns.get("final_equity", STARTING_BALANCE_USD) if stats_returns else STARTING_BALANCE_USD

    summary = {
        "starting_balance": STARTING_BALANCE_USD,
        "total_trades": total_trades,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate_percent": round(win_rate, 2),
        "average_win": round(avg_win, 2),
        "average_loss": round(avg_loss, 2),
        "total_pnl": round(total_pnl, 2),
        "total_fees": round(total_fees, 2),
        "sharpe_ratio": round(sharpe, 2),
        "final_equity": round(final_eq, 2),
        "backtest_start": backtest_result.backtest_start,
        "backtest_end": backtest_result.backtest_end,
        "instrument_id": str(backtest_result.trader_id).split("-")[0] if hasattr(backtest_result, 'trader_id') else "BTC/USD.KRAKEN",
        "total_orders": total_orders,
        "average_holding_time": "",
        "largest_losing_streak": "",
    }

    # Write positions CSV
    if positions_df is not None and len(positions_df) > 0:
        positions_df.to_csv(output_dir / "positions.csv")
        print(f"Position report written: {output_dir / 'positions.csv'} ({len(positions_df)} rows)")

    # Write fills CSV
    if fills_df is not None and len(fills_df) > 0:
        fills_df.to_csv(output_dir / "fills.csv")
        print(f"Fills report written: {output_dir / 'fills.csv'} ({len(fills_df)} rows)")

    # Legacy trades.csv
    with open(output_dir / "trades.csv", "w", newline="") as f:
        if positions_df is not None and len(positions_df) > 0:
            f.write("trade_num,side,entry_price,exit_price,realized_pnl,commissions\n")
            for i, (_, row) in enumerate(positions_df.iterrows(), 1):
                pnl = row.get("realized_pnl", "")
                comm = row.get("commissions", "")
                avg_open = row.get("avg_px_open", "")
                avg_close = row.get("avg_px_close", "")
                side = row.get("side", "")
                f.write(f"{i},{side},{avg_open},{avg_close},{pnl},{comm}\n")
        else:
            f.write("trade_num,side,entry_price,exit_price,realized_pnl,commissions\n")

    # Legacy equity_curve.csv
    with open(output_dir / "equity_curve.csv", "w", newline="") as f:
        f.write("date,equity\n")
        if final_eq is not None:
            from datetime import datetime
            f.write(f"{datetime.now()},{final_eq}\n")

    # Write summary JSON
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