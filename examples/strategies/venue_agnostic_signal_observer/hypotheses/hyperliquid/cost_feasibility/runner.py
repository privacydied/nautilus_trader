from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq


PROCEED_TO_V1_EVALUATION = "PROCEED_TO_V1_EVALUATION"
DO_NOT_PROCEED_COST_WALL_PERSISTS = "DO_NOT_PROCEED_COST_WALL_PERSISTS"
INSUFFICIENT_LIVE_CAPTURE = "INSUFFICIENT_LIVE_CAPTURE"
AWAITING_STRESS_WINDOWS = "AWAITING_STRESS_WINDOWS"
DataSource = Literal["live", "archive", "both"]


@dataclass(frozen=True)
class CoinCostSummary:
    coin: str
    snapshot_count: int
    first_ts_event: int | None
    last_ts_event: int | None
    capture_hours: float
    spread_bps_p50: float | None
    spread_bps_p95: float | None
    spread_bps_p99: float | None
    top_depth_usd_p50: float | None
    top_depth_usd_p95: float | None
    maker_round_trip_bps: float
    taker_round_trip_bps_p50: float | None
    taker_round_trip_bps_p95: float | None
    funding_cost_per_minute_bps: float | None
    realistic_300s_hold_maker_bps: float | None


@dataclass(frozen=True)
class CostFeasibilityResult:
    run_id: str
    data_dir: str
    coins: list[str]
    maker_fee_bps: float
    taker_fee_bps: float
    summaries: list[CoinCostSummary]
    recommendation: str
    reason: str
    data_source: str = "live"
    archive_data_dir: str | None = None
    stress_labels_file: str | None = None


def _stress_hours(stress_labels_file: Path | None) -> set[tuple[str, str]] | None:
    if stress_labels_file is None:
        return None
    hours: set[tuple[str, str]] = set()
    for line in stress_labels_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ts_ns = int(row["stress_end_ns"])
        dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
        hours.add((f"{dt:%Y-%m-%d}", f"{dt:%H}"))
    return hours


def _read_coin_channel(data_dir: Path, coin: str, channel: str, since_ns: int | None, hour_filter: set[tuple[str, str]] | None = None) -> pd.DataFrame:
    files = sorted((data_dir / coin / channel).glob("*/*.parquet"))
    frames: list[pd.DataFrame] = []
    for path in files:
        if hour_filter is not None:
            date_part = path.parent.name
            hour_part = path.stem
            if (date_part, hour_part) not in hour_filter:
                continue
        df = pq.read_table(path).to_pandas()
        if since_ns is not None and "ts_event" in df:
            df = df[df["ts_event"] >= since_ns]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("ts_event")


def _read_source_channel(
    data_dir: Path,
    archive_data_dir: Path | None,
    coin: str,
    channel: str,
    since_ns: int | None,
    data_source: DataSource,
    hour_filter: set[tuple[str, str]] | None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if data_source in ("live", "both"):
        live = _read_coin_channel(data_dir, coin, channel, since_ns)
        if not live.empty:
            frames.append(live)
    if data_source in ("archive", "both") and archive_data_dir is not None:
        archive = _read_coin_channel(archive_data_dir, coin, channel, since_ns, hour_filter)
        if not archive.empty:
            frames.append(archive)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).sort_values("ts_event")
    if channel == "l2book" and "seq" in df:
        df = df.drop_duplicates(subset=["coin", "seq"], keep="last").sort_values("ts_event")
    return df


def compute_cost_feasibility(
    data_dir: Path,
    coins: list[str],
    window_hours: int,
    maker_fee_bps: float,
    taker_fee_bps: float,
    *,
    data_source: DataSource = "live",
    archive_data_dir: Path | None = None,
    stress_labels_file: Path | None = None,
) -> CostFeasibilityResult:
    if data_source not in {"live", "archive", "both"}:
        raise ValueError("data_source must be live, archive, or both")
    if data_source in {"archive", "both"} and archive_data_dir is None:
        archive_data_dir = Path("data/hyperliquid_archive/v0")
    now = datetime.now(tz=UTC)
    since_ns = int((now - timedelta(hours=window_hours)).timestamp() * 1_000_000_000) if window_hours > 0 else None
    hour_filter = _stress_hours(stress_labels_file)
    summaries: list[CoinCostSummary] = []
    any_cost_ge_18 = False
    all_under_18 = True
    min_capture_hours = math.inf
    for coin in coins:
        books = _read_source_channel(data_dir, archive_data_dir, coin, "l2book", since_ns, data_source, hour_filter)
        funding = _read_source_channel(data_dir, archive_data_dir, coin, "funding", since_ns, data_source, hour_filter)
        summary = _summarize_coin(coin, books, funding, maker_fee_bps, taker_fee_bps)
        summaries.append(summary)
        min_capture_hours = min(min_capture_hours, summary.capture_hours)
        cost = summary.realistic_300s_hold_maker_bps
        if cost is None or summary.taker_round_trip_bps_p95 is None:
            all_under_18 = False
        else:
            realistic = min(cost, summary.taker_round_trip_bps_p95)
            if realistic >= 18.0:
                any_cost_ge_18 = True
                all_under_18 = False
    if data_source == "archive" and stress_labels_file is not None:
        required_hours = 0.0
    else:
        required_hours = min(24.0, float(window_hours))
    if not summaries or min_capture_hours < required_hours:
        recommendation = INSUFFICIENT_LIVE_CAPTURE
        reason = "captured less than requested/24h live window"
    elif any_cost_ge_18:
        recommendation = DO_NOT_PROCEED_COST_WALL_PERSISTS
        reason = "observed realistic round-trip cost is >= 18 bps"
    elif all_under_18:
        recommendation = AWAITING_STRESS_WINDOWS
        reason = "quiet-window observed cost is below 18 bps; stress-window confirmation still needed"
    else:
        recommendation = INSUFFICIENT_LIVE_CAPTURE
        reason = "missing book or funding fields"
    return CostFeasibilityResult(
        run_id=datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"),
        data_dir=str(data_dir),
        coins=coins,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        summaries=summaries,
        recommendation=recommendation,
        reason=reason,
        data_source=data_source,
        archive_data_dir=str(archive_data_dir) if archive_data_dir else None,
        stress_labels_file=str(stress_labels_file) if stress_labels_file else None,
    )


def _summarize_coin(coin: str, books: pd.DataFrame, funding: pd.DataFrame, maker_fee_bps: float, taker_fee_bps: float) -> CoinCostSummary:
    maker_rt = 2 * maker_fee_bps
    if books.empty:
        return CoinCostSummary(coin, 0, None, None, 0.0, None, None, None, None, None, maker_rt, None, None, None, None)
    bid = books["bid_px_0"].astype(float)
    ask = books["ask_px_0"].astype(float)
    mid = (bid + ask) / 2.0
    spread = ((ask - bid) / mid) * 10000.0
    top_depth_usd = ((books["bid_sz_0"].astype(float) * bid) + (books["ask_sz_0"].astype(float) * ask)) / 2.0
    first_ts = int(books["ts_event"].min())
    last_ts = int(books["ts_event"].max())
    capture_hours = max(0.0, (last_ts - first_ts) / 1_000_000_000 / 3600)
    funding_per_minute_bps = None
    if not funding.empty and "funding_rate" in funding:
        vals = pd.to_numeric(funding["funding_rate"], errors="coerce").dropna().abs()
        if not vals.empty:
            funding_per_minute_bps = float(vals.median() * 10000.0 / 60.0)
    realistic = maker_rt + (5.0 * funding_per_minute_bps if funding_per_minute_bps is not None else 0.0)
    half_spread = spread / 2.0
    taker_rt = 2.0 * (taker_fee_bps + half_spread)
    return CoinCostSummary(
        coin=coin,
        snapshot_count=len(books),
        first_ts_event=first_ts,
        last_ts_event=last_ts,
        capture_hours=capture_hours,
        spread_bps_p50=float(spread.quantile(0.50)),
        spread_bps_p95=float(spread.quantile(0.95)),
        spread_bps_p99=float(spread.quantile(0.99)),
        top_depth_usd_p50=float(top_depth_usd.quantile(0.50)),
        top_depth_usd_p95=float(top_depth_usd.quantile(0.95)),
        maker_round_trip_bps=maker_rt,
        taker_round_trip_bps_p50=float(taker_rt.quantile(0.50)),
        taker_round_trip_bps_p95=float(taker_rt.quantile(0.95)),
        funding_cost_per_minute_bps=funding_per_minute_bps,
        realistic_300s_hold_maker_bps=realistic,
    )


def write_cost_outputs(result: CostFeasibilityResult, out_dir: Path) -> Path:
    run_dir = out_dir / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = dataclasses.asdict(result)
    (run_dir / "cost_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (run_dir / "recommendation.json").write_text(json.dumps({"recommendation": result.recommendation, "reason": result.reason}, indent=2, sort_keys=True) + "\n")
    lines = [
        f"# Hyperliquid cost feasibility {result.run_id}",
        "",
        f"Data source: {result.data_source}",
        f"Recommendation: {result.recommendation}",
        f"Reason: {result.reason}",
        "",
    ]
    for s in result.summaries:
        lines.append(f"## {s.coin}")
        lines.append(f"snapshots={s.snapshot_count} capture_hours={s.capture_hours:.3f} spread_p50_bps={s.spread_bps_p50} spread_p95_bps={s.spread_bps_p95} maker_300s_bps={s.realistic_300s_hold_maker_bps}")
        lines.append("")
    (run_dir / "cost_summary.md").write_text("\n".join(lines))
    return run_dir
