"""Hyperliquid requester-pays asset context archive ingestion for OI Phase 0.

This module downloads historical public ``asset_ctxs`` CSV snapshots and emits
flat per-symbol JSONL files accepted by the OI velocity compression Phase 0
runner. It is archive-only and contains no capture, account, or trading path.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_oi_velocity_compression_phase0 import (
    FROZEN_SYMBOLS,
    sha256_file,
)

FROZEN_OI_VELOCITY_SYMBOLS: tuple[str, ...] = FROZEN_SYMBOLS
DEFAULT_OUTPUT_DIR = Path("data/hyperliquid_oi_velocity_compression_phase0")
SAFETY_MODE = "public_s3_archive_only_observer_no_funding_values"
BYTES_PER_GB = 1024 ** 3
EGRESS_USD_PER_GB = 0.09


class AssetCtxsArchiveError(RuntimeError):
    """Domain error for fail-closed asset context archive ingestion."""


@dataclass(frozen=True)
class ParsedAssetCtxs:
    rows: list[dict[str, Any]]
    detected_columns: dict[str, str]
    dropped_symbols: dict[str, str]
    funding_history_available: bool
    timestamp_source: str
    snapshot_count: int
    malformed_snapshot_reason: str | None = None


@dataclass(frozen=True)
class AssetCtxsArchiveResult:
    manifest_path: str
    manifest_hash: str
    output_dir: str
    symbols_found: list[str]
    symbols_missing: list[str]
    detected_columns: dict[str, str]


def _canonical_symbols(symbols: Sequence[str]) -> list[str]:
    return [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]


def compute_symbol_list_hash(symbols: Sequence[str]) -> str:
    canonical = "".join(f"{symbol}\n" for symbol in _canonical_symbols(symbols))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_date_list_hash(dates: Sequence[date]) -> str:
    canonical = "".join(f"{day.isoformat()}\n" for day in sorted(set(dates)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def asset_ctxs_s3_key(day: date) -> str:
    return f"s3://hyperliquid-archive/asset_ctxs/{day:%Y%m%d}.csv.lz4"


def iso_ts_for_asset_ctxs_date(day: date) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _norm_header(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _detect_column(fieldnames: Sequence[str], candidates: Sequence[str], error_code: str) -> str:
    by_norm = {_norm_header(name): name for name in fieldnames}
    for candidate in candidates:
        found = by_norm.get(_norm_header(candidate))
        if found is not None:
            return found
    raise AssetCtxsArchiveError(error_code)


def _detect_price_column(fieldnames: Sequence[str]) -> tuple[str, str]:
    options = (
        ("mark", ("markPx", "mark_price", "markPrice", "mark")),
        ("index", ("indexPx", "index_price", "indexPrice", "index", "oraclePx", "oracle_price", "oraclePrice", "oracle")),
        ("last", ("midPx", "mid_price", "mid", "price", "last", "lastPrice")),
    )
    by_norm = {_norm_header(name): name for name in fieldnames}
    for source, candidates in options:
        for candidate in candidates:
            found = by_norm.get(_norm_header(candidate))
            if found is not None:
                return found, source
    raise AssetCtxsArchiveError("ASSET_CTXS_PRICE_COLUMN_MISSING")


def _optional_column(fieldnames: Sequence[str], candidates: Sequence[str]) -> str | None:
    by_norm = {_norm_header(name): name for name in fieldnames}
    for candidate in candidates:
        found = by_norm.get(_norm_header(candidate))
        if found is not None:
            return found
    return None


def _float_value(value: Any) -> float:
    if value in (None, ""):
        raise ValueError("empty numeric value")
    return float(str(value).replace(",", ""))


def _normalize_ts_event(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty timestamp value")
    if raw.isdigit():
        integer = int(raw)
        if integer > 10_000_000_000_000:
            dt = datetime.fromtimestamp(integer / 1_000_000, tz=UTC)
        elif integer > 10_000_000_000:
            dt = datetime.fromtimestamp(integer / 1_000, tz=UTC)
        else:
            dt = datetime.fromtimestamp(integer, tz=UTC)
    else:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp_column(fieldnames: Sequence[str]) -> str | None:
    return _optional_column(fieldnames, ("time", "timestamp", "ts_event", "ts", "datetime", "date_time"))


def _minute_ts(base_ts_event: str, minute: int) -> str:
    base = datetime.fromisoformat(base_ts_event.replace("Z", "+00:00"))
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    else:
        base = base.astimezone(UTC)
    return (base + timedelta(minutes=minute)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _infer_minute_timestamps(raw_rows: Sequence[dict[str, Any]], *, symbol_col: str, ts_event: str) -> tuple[list[str], int]:
    total_rows = len(raw_rows)
    if total_rows == 0 or total_rows % 1440 != 0:
        raise AssetCtxsArchiveError(f"ASSET_CTXS_TIMESTAMP_INFERENCE_ROW_COUNT_INVALID rows={total_rows}")
    rows_per_snapshot = total_rows // 1440
    if rows_per_snapshot <= 0:
        raise AssetCtxsArchiveError(f"ASSET_CTXS_TIMESTAMP_INFERENCE_ROW_COUNT_INVALID rows={total_rows}")
    first_symbols = [str(row.get(symbol_col, "")).strip().upper() for row in raw_rows[:rows_per_snapshot]]
    expected_symbols = set(first_symbols)
    if len(expected_symbols) != rows_per_snapshot or any(not symbol for symbol in expected_symbols):
        raise AssetCtxsArchiveError("ASSET_CTXS_TIMESTAMP_INFERENCE_SYMBOL_BLOCK_INVALID snapshot=0")
    timestamps: list[str] = []
    for snapshot in range(1440):
        block = raw_rows[snapshot * rows_per_snapshot : (snapshot + 1) * rows_per_snapshot]
        block_symbols = [str(row.get(symbol_col, "")).strip().upper() for row in block]
        if set(block_symbols) != expected_symbols or len(set(block_symbols)) != rows_per_snapshot:
            raise AssetCtxsArchiveError(f"ASSET_CTXS_TIMESTAMP_INFERENCE_SYMBOL_BLOCK_INVALID snapshot={snapshot}")
        timestamps.extend([_minute_ts(ts_event, snapshot)] * rows_per_snapshot)
    return timestamps, 1440


def parse_asset_ctxs_csv_text(text: str, *, ts_event: str, frozen_symbols: Sequence[str]) -> ParsedAssetCtxs:
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise AssetCtxsArchiveError("ASSET_CTXS_HEADER_MISSING")
    fieldnames = list(reader.fieldnames)
    raw_rows = list(reader)
    symbol_col = _detect_column(fieldnames, ("coin", "symbol", "asset"), "ASSET_CTXS_SYMBOL_COLUMN_MISSING")
    oi_col = _detect_column(fieldnames, ("openInterest", "open_interest", "oi"), "ASSET_CTXS_OI_COLUMN_MISSING")
    price_col, price_source = _detect_price_column(fieldnames)
    index_col = _optional_column(fieldnames, ("indexPx", "index_price", "indexPrice", "index", "oraclePx", "oracle_price", "oraclePrice", "oracle"))
    time_col = _timestamp_column(fieldnames)
    funding_cols = [name for name in fieldnames if "funding" in _norm_header(name)]
    frozen = set(_canonical_symbols(frozen_symbols))
    if time_col:
        row_timestamps = [_normalize_ts_event(raw.get(time_col)) for raw in raw_rows]
        timestamp_source = f"column:{time_col}"
        snapshot_count = len(set(row_timestamps))
    else:
        row_timestamps, snapshot_count = _infer_minute_timestamps(raw_rows, symbol_col=symbol_col, ts_event=ts_event)
        timestamp_source = "inferred_minute_from_row_order"
    seen_frozen: set[str] = set()
    rows: list[dict[str, Any]] = []
    dropped: dict[str, str] = {}
    for raw, row_ts_event in zip(raw_rows, row_timestamps, strict=True):
        symbol = str(raw.get(symbol_col, "")).strip().upper()
        if not symbol:
            continue
        if symbol not in frozen:
            dropped[symbol] = "not_in_frozen_universe"
            continue
        try:
            open_interest = _float_value(raw.get(oi_col))
            price = _float_value(raw.get(price_col))
            index_price = _float_value(raw.get(index_col)) if index_col else price
        except ValueError:
            dropped[symbol] = "invalid_numeric_value"
            continue
        seen_frozen.add(symbol)
        rows.append(
            {
                "ts_event": row_ts_event,
                "symbol": symbol,
                "open_interest": open_interest,
                "price": price,
                "price_source": price_source,
                "index_price": index_price,
            }
        )
    for symbol in sorted(frozen - seen_frozen):
        dropped.setdefault(symbol, "missing_from_snapshot")
    detected = {"symbol": symbol_col, "open_interest": oi_col, "price": price_col, "price_source": price_source}
    if index_col:
        detected["index_price"] = index_col
    if time_col:
        detected["timestamp"] = time_col
    return ParsedAssetCtxs(
        rows=rows,
        detected_columns=detected,
        dropped_symbols=dropped,
        funding_history_available=bool(funding_cols),
        timestamp_source=timestamp_source,
        snapshot_count=snapshot_count,
    )


def _aws_probe_size(s3_path: str) -> int:
    cmd = ["aws", "s3", "ls", s3_path, "--request-payer", "requester"]
    try:
        proc = subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=60)  # noqa: S603
    except FileNotFoundError as exc:
        raise AssetCtxsArchiveError("AWS_CLI_MISSING: no bulk download was attempted") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or f"exit_code={exc.returncode}").strip()
        raise AssetCtxsArchiveError(f"AWS_S3_PROBE_FAILED path={s3_path} detail={detail}. no bulk download was attempted") from exc
    parts = proc.stdout.strip().split()
    if len(parts) < 3:
        raise AssetCtxsArchiveError(f"AWS_S3_PROBE_PARSE_FAILED path={s3_path}")
    return int(parts[2])


def _aws_download(s3_path: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hyperliquid_asset_ctxs_") as tmp_raw:
        dest = Path(tmp_raw) / "asset_ctxs.csv.lz4"
        cmd = ["aws", "s3", "cp", s3_path, str(dest), "--request-payer", "requester"]
        try:
            subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=300)  # noqa: S603
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or f"exit_code={exc.returncode}").strip()
            raise AssetCtxsArchiveError(f"AWS_S3_DOWNLOAD_FAILED path={s3_path} detail={detail}") from exc
        return _decompress_lz4_bytes(dest.read_bytes())


def _decompress_lz4_bytes(payload: bytes) -> bytes:
    try:
        import lz4.frame  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        with tempfile.TemporaryDirectory(prefix="hyperliquid_asset_ctxs_lz4_") as tmp_raw:
            src = Path(tmp_raw) / "in.csv.lz4"
            dst = Path(tmp_raw) / "out.csv"
            src.write_bytes(payload)
            for cmd in (["lz4", "-d", "-f", str(src), str(dst)], ["unlz4", str(src), str(dst)]):
                try:
                    subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=120)  # noqa: S603
                    return dst.read_bytes()
                except FileNotFoundError:
                    continue
                except subprocess.CalledProcessError as exc:
                    raise AssetCtxsArchiveError(f"LZ4_DECOMPRESS_FAILED detail={(exc.stderr or exc.stdout or '').strip()}") from exc
            raise AssetCtxsArchiveError("LZ4_TOOL_MISSING")
    return lz4.frame.decompress(payload)


def estimate_asset_ctxs_cost(dates: Sequence[date], *, probe_size: Callable[[str], int] | None = None) -> tuple[int, float, str]:
    probe = probe_size or _aws_probe_size
    total = sum(int(probe(asset_ctxs_s3_key(day))) for day in sorted(set(dates)))
    usd = round((total / BYTES_PER_GB) * EGRESS_USD_PER_GB, 6)
    return total, usd, f"I_HAVE_BUDGET_{usd}"


def _atomic_write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(tmp, path)
    return sha256_file(path)


def _stable_manifest_hash(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_hash", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_asset_ctxs_archive(
    *,
    dates: Sequence[date],
    coins: Sequence[str],
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    max_usd_budget: float,
    confirm_token: str,
    downloader: Callable[[str], bytes] | None = None,
    probe_size: Callable[[str], int] | None = None,
    frozen_symbols: Sequence[str] = FROZEN_OI_VELOCITY_SYMBOLS,
    precommitment_hash: str,
) -> AssetCtxsArchiveResult:
    norm_dates = sorted(set(dates))
    if not norm_dates:
        raise AssetCtxsArchiveError("ASSET_CTXS_DATE_LIST_EMPTY")
    requested = [coin for coin in _canonical_symbols(coins) if coin in set(_canonical_symbols(frozen_symbols))]
    estimated_bytes, estimated_usd, expected_token = estimate_asset_ctxs_cost(norm_dates, probe_size=probe_size)
    if estimated_usd > max_usd_budget:
        raise AssetCtxsArchiveError(f"ASSET_CTXS_BUDGET_EXCEEDED estimate_usd={estimated_usd} max_usd_budget={max_usd_budget}")
    if confirm_token != expected_token:
        raise AssetCtxsArchiveError(f"ASSET_CTXS_CONFIRM_TOKEN_REQUIRED expected={expected_token}")
    fetch = downloader or _aws_download
    per_symbol_rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in requested}
    per_symbol_drops: dict[str, set[str]] = {symbol: set() for symbol in _canonical_symbols(frozen_symbols)}
    detected_columns_by_date: dict[str, dict[str, str]] = {}
    timestamp_sources_by_date: dict[str, str] = {}
    per_date_snapshot_counts: dict[str, int] = {}
    malformed_or_incomplete_days: dict[str, str] = {}
    funding_seen = False
    total_downloaded = 0
    for day in norm_dates:
        s3_path = asset_ctxs_s3_key(day)
        payload = fetch(s3_path)
        total_downloaded += len(payload)
        actual_usd = (total_downloaded / BYTES_PER_GB) * EGRESS_USD_PER_GB
        if actual_usd > max_usd_budget:
            raise AssetCtxsArchiveError(f"ASSET_CTXS_COST_GUARD_TRIGGERED actual_usd={actual_usd:.6f} max_usd_budget={max_usd_budget}")
        try:
            parsed = parse_asset_ctxs_csv_text(payload.decode("utf-8"), ts_event=iso_ts_for_asset_ctxs_date(day), frozen_symbols=requested)
        except AssetCtxsArchiveError as exc:
            malformed_or_incomplete_days[day.isoformat()] = str(exc)
            raise
        detected_columns_by_date[day.isoformat()] = parsed.detected_columns
        timestamp_sources_by_date[day.isoformat()] = parsed.timestamp_source
        per_date_snapshot_counts[day.isoformat()] = parsed.snapshot_count
        funding_seen = funding_seen or parsed.funding_history_available
        symbols_found_for_date = sorted({row["symbol"] for row in parsed.rows})
        symbols_missing_for_date = sorted(symbol for symbol in requested if symbol not in symbols_found_for_date)
        for row in parsed.rows:
            per_symbol_rows[row["symbol"]].append(row)
        for symbol, reason in parsed.dropped_symbols.items():
            if symbol in per_symbol_drops:
                per_symbol_drops[symbol].add(reason)
        print(
            "FETCHED_ASSET_CTXS "
            f"date={day.isoformat()} "
            f"rows={len(parsed.rows)} "
            f"symbols_found={len(symbols_found_for_date)} "
            f"symbols_missing={len(symbols_missing_for_date)} "
            f"cumulative_mb={total_downloaded / (1024 ** 2):.3f} "
            f"output_dir={out_dir}",
            flush=True,
        )
        if symbols_missing_for_date:
            print(
                "MISSING_ASSET_CTXS_SYMBOLS "
                f"date={day.isoformat()} "
                f"missing={','.join(symbols_missing_for_date)}",
                flush=True,
            )
    out_dir = Path(out_dir)
    content_hashes: dict[str, str] = {}
    per_symbol_counts: dict[str, int] = {}
    first_last: dict[str, dict[str, str | None]] = {}
    dropped: dict[str, str] = {}
    for symbol in requested:
        rows = sorted(per_symbol_rows.get(symbol, []), key=lambda row: row["ts_event"])
        per_symbol_counts[symbol] = len(rows)
        if rows:
            content_hashes[symbol] = _atomic_write_jsonl(out_dir / f"{symbol}.jsonl", rows)
            first_last[symbol] = {"first_timestamp": rows[0]["ts_event"], "last_timestamp": rows[-1]["ts_event"]}
        else:
            first_last[symbol] = {"first_timestamp": None, "last_timestamp": None}
            reasons = sorted(per_symbol_drops.get(symbol) or {"missing_from_all_snapshots"})
            dropped[symbol] = ";".join(reasons)
    symbols_found = [symbol for symbol, count in per_symbol_counts.items() if count > 0]
    symbols_missing = [symbol for symbol in requested if per_symbol_counts.get(symbol, 0) == 0]
    manifest = {
        "safety_mode": SAFETY_MODE,
        "source_path_pattern": "s3://hyperliquid-archive/asset_ctxs/<YYYYMMDD>.csv.lz4",
        "date_list": [day.isoformat() for day in norm_dates],
        "date_list_hash": compute_date_list_hash(norm_dates),
        "frozen_symbols": list(_canonical_symbols(frozen_symbols)),
        "requested_symbols": requested,
        "frozen_symbol_list_hash": compute_symbol_list_hash(frozen_symbols),
        "precommitment_hash": precommitment_hash,
        "detected_columns": detected_columns_by_date,
        "timestamp_source": next(iter(set(timestamp_sources_by_date.values()))) if len(set(timestamp_sources_by_date.values())) == 1 else "mixed",
        "timestamp_sources_by_date": timestamp_sources_by_date,
        "per_date_snapshot_counts": per_date_snapshot_counts,
        "malformed_or_incomplete_days": malformed_or_incomplete_days,
        "per_symbol_row_counts": per_symbol_counts,
        "first_last_timestamp": first_last,
        "content_hashes": content_hashes,
        "dropped_symbols": dropped,
        "funding_history_available": funding_seen,
        "estimated_bytes": estimated_bytes,
        "estimated_usd": estimated_usd,
        "actual_bytes": total_downloaded,
    }
    manifest["manifest_hash"] = _stable_manifest_hash(manifest)
    manifest_path = out_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    return AssetCtxsArchiveResult(
        manifest_path=str(manifest_path),
        manifest_hash=str(manifest["manifest_hash"]),
        output_dir=str(out_dir),
        symbols_found=symbols_found,
        symbols_missing=symbols_missing,
        detected_columns=detected_columns_by_date[norm_dates[0].isoformat()],
    )


def result_as_dict(result: AssetCtxsArchiveResult) -> dict[str, Any]:
    return dataclasses.asdict(result)
