"""
Gated Hyperliquid historical S3 archive helpers.

Hyperliquid historical market data is requester-pays. The default runner mode is
estimate-only. Bulk transfer is fail-closed behind both a budget cap and a
confirm string derived from the estimate. This module never starts a bulk copy
unless the caller explicitly supplies the matching confirmation.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


BYTES_PER_GB = 1024 ** 3
EGRESS_USD_PER_GB = 0.09
SAFETY_MODE = "public_data_observer_only"
BOOK_DEPTH = 20


class S3ProbeUnavailable(RuntimeError):
    """Raised when requester-pays S3 sample sizing cannot be probed locally."""


@dataclass(frozen=True)
class S3CostEstimate:
    coins: list[str]
    start_date: str
    end_date: str
    include_l2book: bool
    include_fills: bool
    sample_keys: list[str]
    sample_bytes: int
    estimated_bytes: int
    estimated_usd: float
    confirm_token: str


@dataclass(frozen=True)
class S3FetchResult:
    estimate: S3CostEstimate
    files_written: list[str]
    bytes_downloaded: int
    total_rows: int = 0
    missing_files: list[str] = dataclasses.field(default_factory=list)
    failed_files: list[dict[str, str]] = dataclasses.field(default_factory=list)
    elapsed_seconds: float = 0.0
    estimated_usd_actual: float = 0.0
    manifest_path: str = ""


def _iter_dates(start_date: date, end_date: date):
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def _normalize_dates(start_date: date, end_date: date, dates: Sequence[date] | None = None) -> list[date]:
    if dates is None:
        return list(_iter_dates(start_date, end_date))
    unique = sorted(set(dates))
    if not unique:
        raise ValueError("dates must not be empty")
    return unique


def _l2_key(day: date, hour: int, coin: str) -> str:
    return f"s3://hyperliquid-archive/market_data/{day:%Y%m%d}/{hour}/l2Book/{coin}.lz4"


def _out_path(out_dir: Path, coin: str, day: date, hour: int) -> Path:
    return Path(out_dir) / coin / "l2book" / day.isoformat() / f"{hour:02d}.parquet"


def expected_l2book_columns(depth: int = BOOK_DEPTH) -> list[str]:
    cols = ["ts_event", "coin", "seq"]
    for side in ("bid", "ask"):
        for i in range(depth):
            cols.extend([f"{side}_px_{i}", f"{side}_sz_{i}", f"{side}_n_{i}"])
    return cols


def estimate_s3_cost(
    coins: list[str],
    start_date: date,
    end_date: date,
    include_l2book: bool = True,
    include_fills: bool = False,
    *,
    probe_size: Callable[[str], int] | None = None,
    dates: Sequence[date] | None = None,
) -> S3CostEstimate:
    if not include_l2book and not include_fills:
        raise ValueError("at least one channel must be included")
    norm_dates = _normalize_dates(start_date, end_date, dates)
    if dates is None and end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    probe_size = probe_size or _aws_probe_size
    sample_keys: list[str] = []
    sample_bytes = 0
    if include_l2book:
        for coin in coins:
            key = _l2_key(norm_dates[0], 0, coin)
            sample_keys.append(key)
            sample_bytes += int(probe_size(key))
    if include_fills:
        key = f"s3://hl-mainnet-node-data/node_fills_by_block/{norm_dates[0]:%Y%m%d}.lz4"
        sample_keys.append(key)
        sample_bytes += int(probe_size(key))
    days = len(norm_dates)
    l2_multiplier = days * 24 if include_l2book else 0
    fills_multiplier = days if include_fills else 0
    per_coin_l2_sample = sample_bytes / max(1, (len(coins) if include_l2book else 0) + (1 if include_fills else 0))
    estimated_bytes = int(per_coin_l2_sample * ((len(coins) * l2_multiplier) + fills_multiplier))
    estimated_usd = round((estimated_bytes / BYTES_PER_GB) * EGRESS_USD_PER_GB, 2)
    return S3CostEstimate(
        coins=coins,
        start_date=norm_dates[0].isoformat(),
        end_date=norm_dates[-1].isoformat(),
        include_l2book=include_l2book,
        include_fills=include_fills,
        sample_keys=sample_keys,
        sample_bytes=sample_bytes,
        estimated_bytes=estimated_bytes,
        estimated_usd=estimated_usd,
        confirm_token=f"I_HAVE_BUDGET_{estimated_usd}",
    )


def _aws_probe_size(key: str) -> int:
    cmd = ["aws", "s3", "ls", key, "--request-payer", "requester"]
    try:
        proc = subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=60)  # noqa: S603
    except FileNotFoundError as exc:
        raise S3ProbeUnavailable(
            "AWS_CLI_MISSING: install/configure the AWS CLI to probe Hyperliquid requester-pays S3 archive sizes. "
            "No bulk download was attempted."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or f"exit_code={exc.returncode}"
        raise S3ProbeUnavailable(
            f"AWS_S3_PROBE_FAILED key={key} detail={detail}. "
            "Requester-pays buckets usually require configured AWS credentials even for estimate-only probes. "
            "No bulk download was attempted."
        ) from exc
    parts = proc.stdout.strip().split()
    if len(parts) < 3:
        raise RuntimeError(f"could not parse aws ls output for {key}: {proc.stdout!r}")
    return int(parts[2])


def _aws_download(key: str, dest: Path) -> int:
    cmd = ["aws", "s3", "cp", key, str(dest), "--request-payer", "requester"]
    try:
        subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=300)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if "404" in detail or "Not Found" in detail or "does not exist" in detail:
            raise FileNotFoundError(detail) from exc
        raise RuntimeError(f"AWS_S3_DOWNLOAD_FAILED key={key} detail={detail}") from exc
    return dest.stat().st_size


def _is_valid_l2_parquet(path: Path, depth: int = BOOK_DEPTH) -> int | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        table = pq.read_table(path)
        if table.num_rows < 1:
            return None
        existing = set(table.schema.names)
        if not set(expected_l2book_columns(depth)).issubset(existing):
            return None
        return int(table.num_rows)
    except Exception:
        return None


def _decompress_lz4_to_json(lz4_path: Path, json_path: Path) -> None:
    try:
        import lz4.frame  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        for cmd in (["lz4", "-d", "-f", str(lz4_path), str(json_path)], ["unlz4", str(lz4_path), str(json_path)]):
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)  # noqa: S603
                return
            except FileNotFoundError:
                continue
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"LZ4_DECOMPRESS_FAILED detail={(exc.stderr or exc.stdout or '').strip()}") from exc
        raise RuntimeError("LZ4_TOOL_MISSING: install python-lz4, lz4, or unlz4")
    else:
        with lz4.frame.open(lz4_path, "rb") as src, json_path.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)


def _parse_outer_time_ns(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1_000_000_000)
    except ValueError:
        return None


def _normalize_ts_event(record: dict[str, Any], data: dict[str, Any]) -> int:
    raw_time = data.get("time")
    if raw_time is not None:
        ts = int(raw_time)
        digits = len(str(abs(ts)))
        if digits <= 13:
            return ts * 1_000_000
        if digits <= 16:
            return ts * 1_000
        return ts
    outer = _parse_outer_time_ns(record.get("time"))
    if outer is None:
        raise ValueError("missing timestamp")
    return outer


def _level_tuple(level: Any) -> tuple[float, float, int]:
    if isinstance(level, dict):
        return float(level["px"]), float(level["sz"]), int(level.get("n", 0))
    if isinstance(level, (list, tuple)):
        return float(level[0]), float(level[1]), int(level[2]) if len(level) > 2 else 0
    raise ValueError(f"unsupported level format {level!r}")


def _record_to_row(record: dict[str, Any], fallback_coin: str, depth: int = BOOK_DEPTH) -> dict[str, Any]:
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else record
    data = raw.get("data", raw) if isinstance(raw, dict) else record
    if not isinstance(data, dict):
        raise ValueError("record data is not an object")
    levels = data.get("levels") or [[], []]
    if len(levels) < 2:
        raise ValueError("record levels missing bid/ask sides")
    ts_event = _normalize_ts_event(record, data)
    coin = str(data.get("coin") or fallback_coin).upper()
    seq = int(data.get("time") or ts_event)
    row: dict[str, Any] = {"ts_event": int(ts_event), "coin": coin, "seq": seq}
    for side_name, side_levels in (("bid", levels[0]), ("ask", levels[1])):
        parsed = [_level_tuple(x) for x in list(side_levels)[:depth]]
        for i in range(depth):
            if i < len(parsed):
                px, sz, n_orders = parsed[i]
            else:
                px, sz, n_orders = math.nan, math.nan, 0
            row[f"{side_name}_px_{i}"] = float(px)
            row[f"{side_name}_sz_{i}"] = float(sz)
            row[f"{side_name}_n_{i}"] = int(n_orders)
    return row


def _convert_jsonl_to_parquet(json_path: Path, out_path: Path, coin: str, depth: int = BOOK_DEPTH) -> int:
    rows: list[dict[str, Any]] = []
    with json_path.open() as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(_record_to_row(json.loads(line), coin, depth))
            except Exception as exc:
                raise RuntimeError(f"L2BOOK_PARSE_FAILED path={json_path} line={line_no} error={exc}") from exc
    df = pd.DataFrame(rows, columns=expected_l2book_columns(depth))
    if not df.empty:
        df = df.drop_duplicates(subset=["seq"], keep="last").sort_values("ts_event")
        for i in range(depth):
            df[f"bid_n_{i}"] = df[f"bid_n_{i}"].astype("int32")
            df[f"ask_n_{i}"] = df[f"ask_n_{i}"].astype("int32")
        df["ts_event"] = df["ts_event"].astype("int64")
        df["seq"] = df["seq"].astype("int64")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression="zstd")
    os.replace(tmp, out_path)
    return len(df)


def fetch_s3_archive(  # noqa: C901
    coins: list[str],
    start_date: date,
    end_date: date,
    out_dir: Path,
    max_usd_budget: float,
    confirm_token: str,
    include_l2book: bool = True,
    include_fills: bool = False,
    *,
    dates: Sequence[date] | None = None,
    hours: Iterable[int] | None = None,
) -> S3FetchResult:
    norm_dates = _normalize_dates(start_date, end_date, dates)
    estimate = estimate_s3_cost(coins, norm_dates[0], norm_dates[-1], include_l2book, include_fills, dates=norm_dates)
    if estimate.estimated_usd > max_usd_budget:
        raise RuntimeError(f"S3_BUDGET_EXCEEDED estimate_usd={estimate.estimated_usd} max_usd_budget={max_usd_budget}")
    if confirm_token != estimate.confirm_token:
        raise RuntimeError(f"S3_CONFIRM_TOKEN_REQUIRED expected={estimate.confirm_token}")
    if not include_l2book or include_fills:
        raise NotImplementedError("only l2Book archive conversion is implemented")

    hour_values = list(range(24)) if hours is None else list(hours)
    start = time.monotonic()
    files_written: list[str] = []
    missing_files: list[str] = []
    failed_files: list[dict[str, str]] = []
    bytes_downloaded = 0
    total_rows = 0
    out_dir = Path(out_dir)

    with tempfile.TemporaryDirectory(prefix="hyperliquid_s3_") as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        for day in norm_dates:
            for hour in hour_values:
                for coin in coins:
                    out_path = _out_path(out_dir, coin, day, hour)
                    existing_rows = _is_valid_l2_parquet(out_path)
                    if existing_rows is not None:
                        total_rows += existing_rows
                        print(f"SKIP coin={coin} date={day.isoformat()} hour={hour:02d} rows={existing_rows} cumulative_mb={bytes_downloaded / 1_000_000:.3f}", flush=True)
                        continue
                    key = _l2_key(day, hour, coin)
                    lz4_path = tmp_dir / f"{coin}_{day:%Y%m%d}_{hour:02d}.lz4"
                    json_path = tmp_dir / f"{coin}_{day:%Y%m%d}_{hour:02d}.json"
                    try:
                        downloaded = _aws_download(key, lz4_path)
                        bytes_downloaded += int(downloaded)
                        actual_usd = (bytes_downloaded / BYTES_PER_GB) * EGRESS_USD_PER_GB
                        if actual_usd > max_usd_budget:
                            raise RuntimeError(f"COST_GUARD_TRIGGERED actual_usd={actual_usd:.6f} max_usd_budget={max_usd_budget}")
                        _decompress_lz4_to_json(lz4_path, json_path)
                        rows = _convert_jsonl_to_parquet(json_path, out_path, coin)
                        total_rows += rows
                        files_written.append(str(out_path))
                        print(f"FETCHED coin={coin} date={day.isoformat()} hour={hour:02d} rows={rows} cumulative_mb={bytes_downloaded / 1_000_000:.3f}", flush=True)
                    except FileNotFoundError:
                        missing_files.append(key)
                        print(f"MISSING coin={coin} date={day.isoformat()} hour={hour:02d} rows=0 cumulative_mb={bytes_downloaded / 1_000_000:.3f}", flush=True)
                    except RuntimeError as exc:
                        if "COST_GUARD_TRIGGERED" in str(exc):
                            raise
                        failed_files.append({"key": key, "error": str(exc)})
                        print(f"FAILED coin={coin} date={day.isoformat()} hour={hour:02d} rows=0 cumulative_mb={bytes_downloaded / 1_000_000:.3f} error={exc}", flush=True)
                    finally:
                        lz4_path.unlink(missing_ok=True)
                        json_path.unlink(missing_ok=True)

    elapsed = time.monotonic() - start
    actual_usd = round((bytes_downloaded / BYTES_PER_GB) * EGRESS_USD_PER_GB, 6)
    result = S3FetchResult(
        estimate=estimate,
        files_written=files_written,
        bytes_downloaded=bytes_downloaded,
        total_rows=total_rows,
        missing_files=missing_files,
        failed_files=failed_files,
        elapsed_seconds=elapsed,
        estimated_usd_actual=actual_usd,
    )
    manifest = {
        "safety_mode": SAFETY_MODE,
        "files_fetched": files_written,
        "total_bytes": bytes_downloaded,
        "total_rows": total_rows,
        "elapsed": elapsed,
        "estimated_usd_actual": actual_usd,
        "missing_files": missing_files,
        "failed_files": failed_files,
    }
    manifest_path = out_dir / "fetch_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    tmp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_manifest, manifest_path)
    return dataclasses.replace(result, manifest_path=str(manifest_path))


def estimate_as_dict(estimate: S3CostEstimate) -> dict:
    return dataclasses.asdict(estimate)
