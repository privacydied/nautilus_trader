#!/usr/bin/env python3
"""Diagnostic-only fixed-parameter Supertrend sanity check on local OHLCV bars."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

import numpy as np

STATUS_READY = "SUPERtrend_LOW_COST_SANITY_CHECK_READY"
STATUS_NO_DATA = "SUPERtrend_LOW_COST_SANITY_CHECK_NO_DATA"
STATUS_ERROR = "SUPERtrend_LOW_COST_SANITY_CHECK_ERROR"
SAFETY = "public_data_observer_only"
LOCKED_REJECTION_STATEMENT = "This diagnostic does not reopen the locked Kraken BTC/USD OHLCV indicator rejection."
NON_PROMOTION_STATEMENT = "This is not CANDIDATE, not REJECTED, not EXECUTION_READY, not TRADE_READY."
FORBIDDEN_VERDICTS = ("CANDIDATE", "REJECTED", "EXECUTION_READY", "TRADE_READY")
DEFAULT_DATA_PATHS = (
    "data/lead_lag/kraken_btc_usd_1m_20260505_20260512.csv",
    "data/btc_kraken_1s.csv",
)
DEFAULT_ATR_PERIOD = 10
DEFAULT_MULTIPLIER = 3.0
LOW_COST_ENTRY_FEE_BPS = 4.5
LOW_COST_EXIT_FEE_BPS = 4.5
KRAKEN_LIKE_ENTRY_FEE_BPS = 40.0
KRAKEN_LIKE_EXIT_FEE_BPS = 40.0


@dataclass(frozen=True)
class BarArrays:
    timestamps: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray


@dataclass(frozen=True)
class SupertrendResult:
    atr: np.ndarray
    up: np.ndarray
    dn: np.ndarray
    trend: np.ndarray
    buy_signals: np.ndarray
    sell_signals: np.ndarray


@dataclass(frozen=True)
class TradeMetrics:
    entry_price: float
    exit_price: float
    gross_bps: float
    fee_bps: float
    net_bps: float


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def git_value(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def current_git_sha(cwd: Path) -> str:
    sha = git_value(["rev-parse", "--short=12", "HEAD"], cwd)
    status = git_value(["status", "--porcelain"], cwd)
    if sha and status:
        return f"{sha}-dirty"
    return sha


def current_branch(cwd: Path) -> str:
    return git_value(["branch", "--show-current"], cwd)


def parse_timestamp(value: str) -> float:
    text = value.strip()
    if not text:
        return math.nan
    try:
        return float(text)
    except ValueError:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()


def load_ohlcv_csv(path: Path) -> BarArrays:
    timestamps: list[float] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []

    with path.open(newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample)
        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                ts = parse_timestamp(row.get("timestamp") or row.get("time") or row.get("ts") or "")
                if math.isnan(ts):
                    continue
                timestamps.append(ts)
                opens.append(float(row.get("open", row.get("close", "nan"))))
                highs.append(float(row.get("high", row.get("close", "nan"))))
                lows.append(float(row.get("low", row.get("close", "nan"))))
                closes.append(float(row.get("close", "nan")))
                volumes.append(float(row.get("volume", 0.0) or 0.0))
        else:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 5:
                    continue
                ts = parse_timestamp(row[0])
                if math.isnan(ts):
                    continue
                timestamps.append(ts)
                opens.append(float(row[1]))
                highs.append(float(row[2]))
                lows.append(float(row[3]))
                closes.append(float(row[4]))
                volumes.append(float(row[5]) if len(row) > 5 and row[5] else 0.0)

    arrays = BarArrays(
        timestamps=np.asarray(timestamps, dtype=float),
        open=np.asarray(opens, dtype=float),
        high=np.asarray(highs, dtype=float),
        low=np.asarray(lows, dtype=float),
        close=np.asarray(closes, dtype=float),
        volume=np.asarray(volumes, dtype=float),
    )
    mask = np.isfinite(arrays.timestamps) & np.isfinite(arrays.open) & np.isfinite(arrays.high) & np.isfinite(arrays.low) & np.isfinite(arrays.close)
    order = np.argsort(arrays.timestamps[mask])
    return BarArrays(
        timestamps=arrays.timestamps[mask][order],
        open=arrays.open[mask][order],
        high=arrays.high[mask][order],
        low=arrays.low[mask][order],
        close=arrays.close[mask][order],
        volume=arrays.volume[mask][order],
    )


def discover_data_path(repo_root: Path, explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_absolute():
            path = repo_root / path
        return path if path.exists() else None
    for rel_path in DEFAULT_DATA_PATHS:
        path = repo_root / rel_path
        if path.exists():
            return path
    return None


def infer_timeframe_seconds(timestamps: np.ndarray) -> int | None:
    if len(timestamps) < 2:
        return None
    diffs = np.diff(timestamps)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(diffs) == 0:
        return None
    return int(round(float(np.median(diffs))))


def timeframe_label(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def iso_utc(timestamp_s: float) -> str:
    return datetime.fromtimestamp(float(timestamp_s), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    tr = np.empty_like(close, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return tr


def wilder_rma(values: np.ndarray, period: int) -> np.ndarray:
    if period <= 0:
        raise ValueError("period must be positive")
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return out
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = ((out[i - 1] * (period - 1)) + values[i]) / period
    return out


def compute_supertrend(bars: BarArrays, atr_period: int = DEFAULT_ATR_PERIOD, multiplier: float = DEFAULT_MULTIPLIER) -> SupertrendResult:
    n = len(bars.close)
    atr = wilder_rma(true_range(bars.high, bars.low, bars.close), atr_period)
    src = (bars.high + bars.low) / 2.0
    base_up = src - multiplier * atr
    base_dn = src + multiplier * atr
    up = np.array(base_up, copy=True)
    dn = np.array(base_dn, copy=True)
    trend = np.ones(n, dtype=int)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)

    for i in range(1, n):
        up1 = up[i - 1] if np.isfinite(up[i - 1]) else up[i]
        dn1 = dn[i - 1] if np.isfinite(dn[i - 1]) else dn[i]

        if np.isfinite(up[i]) and np.isfinite(up1) and bars.close[i - 1] > up1:
            up[i] = max(up[i], up1)
        if np.isfinite(dn[i]) and np.isfinite(dn1) and bars.close[i - 1] < dn1:
            dn[i] = min(dn[i], dn1)

        trend[i] = trend[i - 1]
        if trend[i - 1] == -1 and np.isfinite(dn1) and bars.close[i] > dn1:
            trend[i] = 1
        elif trend[i - 1] == 1 and np.isfinite(up1) and bars.close[i] < up1:
            trend[i] = -1

        buy[i] = trend[i] == 1 and trend[i - 1] == -1
        sell[i] = trend[i] == -1 and trend[i - 1] == 1

    return SupertrendResult(atr=atr, up=up, dn=dn, trend=trend, buy_signals=buy, sell_signals=sell)


def compute_trade_metrics(entry_price: float, exit_price: float, entry_fee_bps: float, exit_fee_bps: float) -> TradeMetrics:
    gross_bps = (exit_price / entry_price - 1.0) * 10000.0
    fee_bps = entry_fee_bps + exit_fee_bps
    return TradeMetrics(
        entry_price=float(entry_price),
        exit_price=float(exit_price),
        gross_bps=float(gross_bps),
        fee_bps=float(fee_bps),
        net_bps=float(gross_bps - fee_bps),
    )


def build_trades(bars: BarArrays, signals: SupertrendResult, entry_fee_bps: float, exit_fee_bps: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    entry_index: int | None = None
    for i in range(len(bars.close)):
        if entry_index is None and signals.buy_signals[i]:
            entry_index = i
            continue
        if entry_index is not None and signals.sell_signals[i] and i > entry_index:
            metrics = compute_trade_metrics(bars.close[entry_index], bars.close[i], entry_fee_bps, exit_fee_bps)
            trades.append(
                {
                    "entry_index": entry_index,
                    "exit_index": i,
                    "entry_timestamp": iso_utc(bars.timestamps[entry_index]),
                    "exit_timestamp": iso_utc(bars.timestamps[i]),
                    "entry_price": metrics.entry_price,
                    "exit_price": metrics.exit_price,
                    "gross_bps": metrics.gross_bps,
                    "fee_bps": metrics.fee_bps,
                    "net_bps": metrics.net_bps,
                }
            )
            entry_index = None
    return trades


def max_drawdown_bps_from_trade_curve(trades: list[dict[str, Any]], field: str = "net_bps") -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += float(trade[field])
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return float(max_dd)


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gross = np.asarray([t["gross_bps"] for t in trades], dtype=float)
    net = np.asarray([t["net_bps"] for t in trades], dtype=float)
    fees = np.asarray([t["fee_bps"] for t in trades], dtype=float)
    if len(trades) == 0:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "gross_pnl_bps": 0.0,
            "total_fees_bps": 0.0,
            "net_pnl_bps": 0.0,
            "mean_gross_trade_bps": 0.0,
            "mean_net_trade_bps": 0.0,
            "median_gross_trade_bps": 0.0,
            "median_net_trade_bps": 0.0,
            "best_trade_bps": 0.0,
            "worst_trade_bps": 0.0,
            "max_drawdown_bps": 0.0,
        }
    return {
        "trade_count": int(len(trades)),
        "win_rate": float(np.mean(net > 0.0)),
        "gross_pnl_bps": float(np.sum(gross)),
        "total_fees_bps": float(np.sum(fees)),
        "net_pnl_bps": float(np.sum(net)),
        "mean_gross_trade_bps": float(np.mean(gross)),
        "mean_net_trade_bps": float(np.mean(net)),
        "median_gross_trade_bps": float(np.median(gross)),
        "median_net_trade_bps": float(np.median(net)),
        "best_trade_bps": float(np.max(net)),
        "worst_trade_bps": float(np.min(net)),
        "max_drawdown_bps": max_drawdown_bps_from_trade_curve(trades, "net_bps"),
    }


def build_no_data_summary(data_source_path: str, report_dir: Path, git_sha: str, branch: str) -> dict[str, Any]:
    return {
        "status": STATUS_NO_DATA,
        "safety": SAFETY,
        "locked_rejection_statement": LOCKED_REJECTION_STATEMENT,
        "non_promotion_statement": NON_PROMOTION_STATEMENT,
        "data_source_path": data_source_path,
        "report_dir": str(report_dir),
        "git_sha": git_sha,
        "branch": branch,
        "error": "No local Kraken BTC/USD OHLCV/bar data found or usable.",
    }


def build_summary(repo_root: Path, report_dir: Path, data_path: Path, bars: BarArrays) -> dict[str, Any]:
    signals = compute_supertrend(bars)
    low_cost_trades = build_trades(bars, signals, LOW_COST_ENTRY_FEE_BPS, LOW_COST_EXIT_FEE_BPS)
    gross_trades = build_trades(bars, signals, 0.0, 0.0)
    kraken_like_trades = build_trades(bars, signals, KRAKEN_LIKE_ENTRY_FEE_BPS, KRAKEN_LIKE_EXIT_FEE_BPS)
    low_cost = summarize_trades(low_cost_trades)
    gross = summarize_trades(gross_trades)
    kraken_like = summarize_trades(kraken_like_trades)
    timeframe_seconds = infer_timeframe_seconds(bars.timestamps)
    flip_count = int(np.sum(signals.buy_signals) + np.sum(signals.sell_signals))

    comparison_note = (
        f"Low-cost net mean trade {low_cost['mean_net_trade_bps']:.2f} bps versus the 9.00 bps "
        f"round-trip cost wall; gross mean trade {gross['mean_gross_trade_bps']:.2f} bps."
    )
    recommendation = "Do not proceed"
    if low_cost["trade_count"] > 0 and low_cost["net_pnl_bps"] > 0 and low_cost["mean_net_trade_bps"] > 0:
        recommendation = "Requires formal precommitment before any further work"

    return {
        "status": STATUS_READY,
        "safety": SAFETY,
        "locked_rejection_statement": LOCKED_REJECTION_STATEMENT,
        "non_promotion_statement": NON_PROMOTION_STATEMENT,
        "data_source_path": str(data_path),
        "timeframes_available_used": [timeframe_label(timeframe_seconds)],
        "date_range": {"start": iso_utc(bars.timestamps[0]), "end": iso_utc(bars.timestamps[-1])},
        "bar_count": int(len(bars.close)),
        "atr_period": DEFAULT_ATR_PERIOD,
        "source": "hl2",
        "multiplier": DEFAULT_MULTIPLIER,
        "atr_method": "TradingView atr() / Wilder RMA equivalent",
        "execution_model": "long_flat_close_execution_diagnostic",
        "low_cost_taker_model": {"entry_fee_bps": LOW_COST_ENTRY_FEE_BPS, "exit_fee_bps": LOW_COST_EXIT_FEE_BPS, "round_trip_bps": 9.0},
        "zero_cost_gross_metrics": gross,
        "low_cost_net_metrics": low_cost,
        "kraken_like_cost_comparison": {
            "entry_fee_bps": KRAKEN_LIKE_ENTRY_FEE_BPS,
            "exit_fee_bps": KRAKEN_LIKE_EXIT_FEE_BPS,
            "round_trip_bps": KRAKEN_LIKE_ENTRY_FEE_BPS + KRAKEN_LIKE_EXIT_FEE_BPS,
            "metrics": kraken_like,
        },
        "turnover_number_of_flips": flip_count,
        "comparison_note_versus_9bps_round_trip_cost_wall": comparison_note,
        "recommendation": recommendation,
        "git_sha": current_git_sha(repo_root),
        "branch": current_branch(repo_root),
        "report_dir": str(report_dir),
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Supertrend low-cost sanity check",
        "",
        f"status: {summary['status']}",
        f"safety: {summary['safety']}",
        "",
        summary["locked_rejection_statement"],
        summary["non_promotion_statement"],
        "",
    ]
    if summary["status"] != STATUS_READY:
        lines.extend([
            f"data_source_path: {summary.get('data_source_path', '')}",
            f"error: {summary.get('error', '')}",
        ])
        return "\n".join(lines) + "\n"

    m = summary["low_cost_net_metrics"]
    gross = summary["zero_cost_gross_metrics"]
    kraken = summary["kraken_like_cost_comparison"]
    lines.extend(
        [
            "## Scope",
            "Fixed TradingView-style Supertrend only: ATR period 10, hl2 source, multiplier 3.0, Wilder RMA ATR. No grid search, no tuning, no optimizer, no null test, no holdout promotion.",
            "",
            "## Data",
            f"data source path/catalog used: {summary['data_source_path']}",
            f"timeframe(s) actually available/used: {', '.join(summary['timeframes_available_used'])}",
            f"date range: {summary['date_range']['start']} to {summary['date_range']['end']}",
            f"bar count: {summary['bar_count']}",
            "",
            "## Low-cost taker result",
            f"trade count: {m['trade_count']}",
            f"win rate: {m['win_rate']:.4f}",
            f"gross PnL: {m['gross_pnl_bps']:.4f} bps",
            f"total fees: {m['total_fees_bps']:.4f} bps",
            f"net PnL: {m['net_pnl_bps']:.4f} bps",
            f"mean gross trade bps: {m['mean_gross_trade_bps']:.4f}",
            f"mean net trade bps: {m['mean_net_trade_bps']:.4f}",
            f"median gross trade bps: {m['median_gross_trade_bps']:.4f}",
            f"median net trade bps: {m['median_net_trade_bps']:.4f}",
            f"best trade bps: {m['best_trade_bps']:.4f}",
            f"worst trade bps: {m['worst_trade_bps']:.4f}",
            f"max drawdown: {m['max_drawdown_bps']:.4f} bps",
            f"turnover / number of flips: {summary['turnover_number_of_flips']}",
            "",
            "## Comparisons",
            f"zero-cost gross net PnL field: {gross['net_pnl_bps']:.4f} bps",
            f"old Kraken-like round trip cost: {kraken['round_trip_bps']:.4f} bps",
            f"old Kraken-like net PnL: {kraken['metrics']['net_pnl_bps']:.4f} bps",
            summary["comparison_note_versus_9bps_round_trip_cost_wall"],
            "",
            f"recommendation: {summary['recommendation']}",
        ]
    )
    return "\n".join(lines) + "\n"


def make_report_dir(repo_root: Path, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = repo_root / "reports" / f"supertrend_low_cost_sanity_check_{stamp}"
    if report_dir.exists() and any(report_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty report dir: {report_dir}")
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def run(repo_root: Path, data_path_arg: str | None = None) -> dict[str, Any]:
    report_dir = make_report_dir(repo_root)
    data_path = discover_data_path(repo_root, data_path_arg)
    if data_path is None:
        summary = build_no_data_summary(data_path_arg or ",".join(DEFAULT_DATA_PATHS), report_dir, current_git_sha(repo_root), current_branch(repo_root))
    else:
        try:
            bars = load_ohlcv_csv(data_path)
            if len(bars.close) < DEFAULT_ATR_PERIOD + 2:
                summary = build_no_data_summary(str(data_path), report_dir, current_git_sha(repo_root), current_branch(repo_root))
            else:
                summary = build_summary(repo_root, report_dir, data_path, bars)
        except Exception as exc:  # noqa: BLE001 - diagnostic report records the failure mode.
            summary = {
                "status": STATUS_ERROR,
                "safety": SAFETY,
                "locked_rejection_statement": LOCKED_REJECTION_STATEMENT,
                "non_promotion_statement": NON_PROMOTION_STATEMENT,
                "data_source_path": str(data_path),
                "report_dir": str(report_dir),
                "git_sha": current_git_sha(repo_root),
                "branch": current_branch(repo_root),
                "error": repr(exc),
            }
    atomic_write_json(report_dir / "summary.json", summary)
    atomic_write_text(report_dir / "summary.md", render_summary_markdown(summary))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run diagnostic-only fixed Supertrend low-cost sanity check on local OHLCV bars.")
    parser.add_argument("--repo-root", default=".", help="Repository root containing reports/ and local data.")
    parser.add_argument("--data", default=None, help="Optional explicit local OHLCV CSV path. No network access is performed.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run(Path(args.repo_root).resolve(), args.data)
    print(summary["report_dir"])
    print(summary["status"])
    return 0 if summary["status"] in {STATUS_READY, STATUS_NO_DATA} else 1


if __name__ == "__main__":
    raise SystemExit(main())
