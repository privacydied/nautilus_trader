from __future__ import annotations

import calendar
import abc
import csv
import hashlib
import io
import json
import zipfile
from abc import abstractmethod
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Iterable
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import urlopen

from .config import TARGET_SPOT_SYMBOLS
from .timestamp_parsing import iso_hour
from .timestamp_parsing import parse_binance_open_time

BINANCE_VISION_BASE = "https://data.binance.vision/data/spot"
MANIFEST_NAME = "spot_cache_manifest.json"
CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
DEFAULT_TIMEOUT_SECONDS = 60
APPROX_BYTES_PER_DAY = 12_000
APPROX_BYTES_PER_MONTH = 360_000


@dataclass(frozen=True)
class DownloadPlanItem:
    symbol: str
    day: date
    url: str
    relative_zip_path: str
    csv_entry_name: str
    estimated_bytes: int = APPROX_BYTES_PER_DAY
    is_monthly: bool = False


@dataclass(frozen=True)
class DownloadedFile:
    symbol: str
    day: str
    url: str
    relative_zip_path: str
    sha256: str
    size_bytes: int
    is_monthly: bool = False


class NetworkAccessDeniedError(RuntimeError):
    pass


class DownloadBudgetExceededError(RuntimeError):
    pass


class AmbiguousTimestampError(ValueError):
    pass


class SpotArchiveFetcher(Protocol):
    def fetch(self, url: str) -> bytes | None:
        ...


class _DefaultDownloadFetcher:
    def fetch(self, url: str) -> bytes:
        with urlopen(url, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return response.read()


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current = current.fromordinal(current.toordinal() + 1)


def monthrange(start: date, end: date) -> Iterable[date]:
    current = start.replace(day=1)
    while current <= end:
        yield current
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_daily_plan(symbols: Iterable[str], start: str, end: str) -> list[DownloadPlanItem]:
    start_date = parse_date(start)
    end_date = parse_date(end)
    items: list[DownloadPlanItem] = []
    for symbol in symbols:
        symbol_upper = symbol.upper()
        for day in daterange(start_date, end_date):
            stamp = day.isoformat()
            file_name = f"{symbol_upper}-1h-{stamp}.zip"
            relative_zip_path = f"{symbol_upper}/{file_name}"
            items.append(
                DownloadPlanItem(
                    symbol=symbol_upper,
                    day=day,
                    url=f"{BINANCE_VISION_BASE}/daily/klines/{symbol_upper}/1h/{file_name}",
                    relative_zip_path=relative_zip_path,
                    csv_entry_name=f"{symbol_upper}-1h-{stamp}.csv",
                    estimated_bytes=APPROX_BYTES_PER_DAY,
                    is_monthly=False,
                )
            )
    return items


def build_monthly_plan(symbols: Iterable[str], start: str, end: str) -> list[DownloadPlanItem]:
    start_date = parse_date(start)
    end_date = parse_date(end)
    items: list[DownloadPlanItem] = []
    for symbol in symbols:
        symbol_upper = symbol.upper()
        for month_start in monthrange(start_date, end_date):
            stamp = f"{month_start.year:04d}-{month_start.month:02d}"
            file_name = f"{symbol_upper}-1h-{stamp}.zip"
            relative_zip_path = f"{symbol_upper}/{file_name}"
            items.append(
                DownloadPlanItem(
                    symbol=symbol_upper,
                    day=month_start,
                    url=f"{BINANCE_VISION_BASE}/monthly/klines/{symbol_upper}/1h/{file_name}",
                    relative_zip_path=relative_zip_path,
                    csv_entry_name=f"{symbol_upper}-1h-{stamp}.csv",
                    estimated_bytes=APPROX_BYTES_PER_MONTH,
                    is_monthly=True,
                )
            )
    return items


def estimate_total_bytes(plan: Iterable[DownloadPlanItem]) -> int:
    return sum(item.estimated_bytes for item in plan)


def normalize_kline_timestamp(value: str) -> str:
    raw = str(value).strip()
    if len(raw) not in {13, 16}:
        raise AmbiguousTimestampError(f"ambiguous timestamp unit: {raw}")
    return iso_hour(parse_binance_open_time(raw))


def normalize_kline_row(row: list[str]) -> dict[str, str]:
    if len(row) < 6:
        raise ValueError("kline row must contain at least 6 fields")
    return {
        "timestamp": normalize_kline_timestamp(row[0]),
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
    }


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _find_raw_zip_path(
    raw_root: Path,
    symbol: str,
    file_name: str,
    is_monthly: bool,
) -> Path | None:
    if is_monthly:
        candidate = raw_root / "raw_zips_monthly" / symbol / file_name
    else:
        candidate = raw_root / "raw_zips_daily" / symbol / file_name
    if candidate.exists():
        return candidate
    legacy_candidate = raw_root / "raw_zips" / symbol / file_name
    if legacy_candidate.exists():
        return legacy_candidate
    return None


def _month_date_range(month_date: date) -> tuple[date, date]:
    last_day = calendar.monthrange(month_date.year, month_date.month)[1]
    return month_date.replace(day=1), month_date.replace(day=last_day)


def _write_normalized_csv(csv_root: Path, symbol: str, rows: dict[str, dict[str, str]]) -> Path:
    out_path = csv_root / f"{symbol}-1h.csv"
    ordered = [rows[key] for key in sorted(rows)]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_HEADER))
        writer.writeheader()
        writer.writerows(ordered)
    return out_path


def _parse_zip_payload(payload: bytes, csv_entry_name: str, url: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if csv_entry_name not in names:
            raise ValueError(f"expected {csv_entry_name} in {url}, found {names}")
        with archive.open(csv_entry_name, "r") as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            reader = csv.reader(text)
            for row in reader:
                normalized = normalize_kline_row(row)
                rows[normalized["timestamp"]] = normalized
    return rows


def build_spot_cache(
    *,
    cache_root: Path,
    symbols: Iterable[str],
    start: str,
    end: str,
    download_budget_bytes: int,
    allow_network_public: bool,
    dry_run: bool,
    archive_granularity: str = "monthly-first",
    overwrite: bool = False,
    fetcher: SpotArchiveFetcher | None = None,
) -> dict[str, object]:
    if archive_granularity not in {"monthly-first", "monthly-only", "daily-only"}:
        raise ValueError(f"Unsupported archive_granularity: {archive_granularity}")

    fetcher = fetcher or _DefaultDownloadFetcher()
    symbol_list = [symbol.upper() for symbol in symbols]
    existing_manifest_path = cache_root / MANIFEST_NAME
    existing_manifest: dict[str, object] = {}
    if existing_manifest_path.exists():
        existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))

    if archive_granularity == "daily-only":
        plan = build_daily_plan(symbol_list, start, end)
    else:
        plan = build_monthly_plan(symbol_list, start, end)

    total_estimated_bytes = estimate_total_bytes(plan)
    if download_budget_bytes and total_estimated_bytes > download_budget_bytes:
        raise DownloadBudgetExceededError(
            f"download plan {total_estimated_bytes} exceeds budget {download_budget_bytes}"
        )

    if not allow_network_public and not dry_run:
        raise NetworkAccessDeniedError("public network download requires --allow-network-public")

    cache_root.mkdir(parents=True, exist_ok=True)
    raw_root = cache_root / "raw_zips"
    raw_root.mkdir(parents=True, exist_ok=True)
    csv_root = cache_root / "csv"
    csv_root.mkdir(parents=True, exist_ok=True)

    symbol_rows: dict[str, dict[str, dict[str, str]]] = {symbol: {} for symbol in symbol_list}
    files_downloaded: list[DownloadedFile] = []
    monthly_files_downloaded = 0
    daily_files_downloaded = 0
    total_bytes_downloaded = 0
    network_used = False
    seen_file_paths: set[str] = set()

    start_dt = datetime.combine(parse_date(start), datetime.min.time()).replace(tzinfo=UTC)
    end_dt = datetime.combine(parse_date(end), datetime.max.time()).replace(tzinfo=UTC)

    if dry_run:
        for symbol in symbol_list:
            out_path = csv_root / f"{symbol}-1h.csv"
            if out_path.exists():
                with out_path.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        ts = row["timestamp"]
                        try:
                            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if start_dt <= ts_dt <= end_dt:
                            symbol_rows[symbol][ts] = dict(row)
    else:
        for item in plan:
            zip_path = _find_raw_zip_path(
                raw_root,
                item.symbol,
                item.relative_zip_path.split("/")[-1],
                is_monthly=item.is_monthly,
            )
            payload = None
            if zip_path is not None and not overwrite:
                payload = zip_path.read_bytes()
            else:
                try:
                    payload = fetcher.fetch(item.url)
                    network_used = True
                except HTTPError as exc:
                    if exc.code == 404:
                        continue
                    raise
                if payload is None:
                    continue
                total_bytes_downloaded += len(payload)
                if item.is_monthly:
                    write_root = raw_root / "raw_zips_monthly"
                else:
                    write_root = raw_root / "raw_zips_daily"
                write_path = write_root / item.relative_zip_path
                write_path.parent.mkdir(parents=True, exist_ok=True)
                write_path.write_bytes(payload)
                zip_path = write_path

            if payload is None:
                continue

            try:
                parsed = _parse_zip_payload(payload, item.csv_entry_name, item.url)
            except (zipfile.BadZipFile, ValueError):
                parsed = {}

            if not parsed:
                continue

            filtered: dict[str, dict[str, str]] = {}
            for ts, row in parsed.items():
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if start_dt <= ts_dt <= end_dt:
                    filtered[ts] = row
            symbol_rows[item.symbol].update(filtered)

            if item.relative_zip_path in seen_file_paths:
                continue
            seen_file_paths.add(item.relative_zip_path)
            files_downloaded.append(
                DownloadedFile(
                    symbol=item.symbol,
                    day=item.day.isoformat(),
                    url=item.url,
                    relative_zip_path=str(zip_path.relative_to(cache_root)) if zip_path else item.relative_zip_path,
                    sha256=sha256_bytes(payload),
                    size_bytes=len(payload),
                    is_monthly=item.is_monthly,
                )
            )
            if item.is_monthly:
                monthly_files_downloaded += 1
            else:
                daily_files_downloaded += 1

        if archive_granularity == "monthly-first":
            for symbol in symbol_list:
                for month_start in monthrange(parse_date(start), parse_date(end)):
                    month_key = f"{month_start.year:04d}-{month_start.month:02d}"
                    has_monthly_data = any(
                        ts.startswith(month_key)
                        for ts in symbol_rows[symbol]
                    )
                    if has_monthly_data:
                        continue

                    month_start_date, month_end_date = _month_date_range(month_start)
                    fallback_plan = build_daily_plan(
                        [symbol],
                        month_start_date.isoformat(),
                        month_end_date.isoformat(),
                    )
                    for daily_item in fallback_plan:
                        zip_path = _find_raw_zip_path(
                            raw_root,
                            daily_item.symbol,
                            daily_item.relative_zip_path.split("/")[-1],
                            is_monthly=False,
                        )
                        payload = None
                        if zip_path is not None and not overwrite:
                            payload = zip_path.read_bytes()
                        else:
                            try:
                                payload = fetcher.fetch(daily_item.url)
                                network_used = True
                            except HTTPError as exc:
                                if exc.code == 404:
                                    continue
                                raise
                        if payload is None:
                            continue

                        try:
                            parsed = _parse_zip_payload(payload, daily_item.csv_entry_name, daily_item.url)
                        except (zipfile.BadZipFile, ValueError):
                            parsed = {}

                        if not parsed:
                            continue

                        symbol_rows[daily_item.symbol].update(parsed)

                        if daily_item.relative_zip_path in seen_file_paths:
                            continue
                        seen_file_paths.add(daily_item.relative_zip_path)
                        total_bytes_downloaded += len(payload)
                        if zip_path is None or overwrite:
                            write_path = raw_root / "raw_zips_daily" / daily_item.relative_zip_path
                            write_path.parent.mkdir(parents=True, exist_ok=True)
                            write_path.write_bytes(payload)
                            zip_path = write_path
                        files_downloaded.append(
                            DownloadedFile(
                                symbol=daily_item.symbol,
                                day=daily_item.day.isoformat(),
                                url=daily_item.url,
                                relative_zip_path=str(zip_path.relative_to(cache_root)) if zip_path else daily_item.relative_zip_path,
                                sha256=sha256_bytes(payload),
                                size_bytes=len(payload),
                                is_monthly=False,
                            )
                        )
                        daily_files_downloaded += 1

    for symbol, rows in symbol_rows.items():
        if not rows:
            continue
        _write_normalized_csv(csv_root, symbol, rows)

    manifest_files: dict[str, dict[str, object]] = {}
    existing_file_hashes = existing_manifest.get("file_hashes", {})
    if isinstance(existing_file_hashes, dict):
        manifest_files.update(existing_file_hashes)

    current_csv_symbols: set[str] = set()
    for symbol in symbol_list:
        out_path = csv_root / f"{symbol}-1h.csv"
        if out_path.exists():
            current_csv_symbols.add(symbol)
            rows = out_path.read_text(encoding="utf-8").strip().splitlines()
            row_count = max(len(rows) - 1, 0)
            first_ts = None
            last_ts = None
            if row_count > 0:
                first_ts = rows[1].split(",", 1)[0]
                last_ts = rows[-1].split(",", 1)[0]
            manifest_files[str(out_path.relative_to(cache_root))] = {
                "sha256": sha256_file(out_path),
                "row_count": row_count,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "missing_row_count": 0,
            }

    previous_symbols_downloaded_raw = existing_manifest.get("symbols_downloaded", [])
    previous_symbols_downloaded = {
        str(s) for s in previous_symbols_downloaded_raw if isinstance(previous_symbols_downloaded_raw, list)
    }
    for symbol in previous_symbols_downloaded:
        if symbol not in current_csv_symbols and (csv_root / f"{symbol}-1h.csv").exists():
            current_csv_symbols.add(symbol)
            out_path = csv_root / f"{symbol}-1h.csv"
            rows = out_path.read_text(encoding="utf-8").strip().splitlines()
            row_count = max(len(rows) - 1, 0)
            first_ts = None
            last_ts = None
            if row_count > 0:
                first_ts = rows[1].split(",", 1)[0]
                last_ts = rows[-1].split(",", 1)[0]
            manifest_files[str(out_path.relative_to(cache_root))] = {
                "sha256": sha256_file(out_path),
                "row_count": row_count,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "missing_row_count": 0,
            }

    symbols_downloaded = sorted(current_csv_symbols)
    previous_symbols_requested_raw = existing_manifest.get("symbols_requested", [])
    previous_symbols_requested = {
        str(s) for s in previous_symbols_requested_raw if isinstance(previous_symbols_requested_raw, list)
    }
    merged_symbols_requested = sorted(previous_symbols_requested | set(symbol_list))
    merged_symbols_downloaded = symbols_downloaded
    symbols_unavailable = sorted(set(merged_symbols_requested) - set(merged_symbols_downloaded))

    existing_downloaded_files_raw = existing_manifest.get("files_downloaded", [])
    existing_downloaded_files = list(existing_downloaded_files_raw) if isinstance(existing_downloaded_files_raw, list) else []
    seen_paths: set[str] = set()
    merged_files_downloaded: list[dict[str, object]] = []
    for entry in existing_downloaded_files:
        path = entry.get("relative_zip_path")
        if path and path not in seen_paths:
            seen_paths.add(path)
            merged_files_downloaded.append(entry)
    for downloaded in files_downloaded:
        if downloaded.relative_zip_path not in seen_paths:
            seen_paths.add(downloaded.relative_zip_path)
            merged_files_downloaded.append(downloaded.__dict__)

    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "updated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": "Binance Vision public archive",
        "symbols_requested": merged_symbols_requested,
        "symbols_downloaded": merged_symbols_downloaded,
        "symbols_unavailable": symbols_unavailable,
        "start": start,
        "end": end,
        "files_downloaded": merged_files_downloaded,
        "monthly_files_downloaded": monthly_files_downloaded,
        "daily_files_downloaded": daily_files_downloaded,
        "file_hashes": manifest_files,
        "row_counts": {key: value["row_count"] for key, value in manifest_files.items()},
        "first_last_timestamp_per_symbol": {
            key: {
                "first": value["first_timestamp"],
                "last": value["last_timestamp"],
            }
            for key, value in manifest_files.items()
        },
        "missing_row_counts": {key: value["missing_row_count"] for key, value in manifest_files.items()},
        "no_auth": True,
        "no_orders": True,
        "no_exchange_account": True,
        "network_used": network_used,
        "total_bytes_downloaded": total_bytes_downloaded,
        "budget_bytes": download_budget_bytes,
        "download_plan_count": len(plan),
        "archive_granularity": archive_granularity,
        "dry_run": dry_run,
        "estimated_total_bytes": total_estimated_bytes,
    }
    write_manifest(cache_root / MANIFEST_NAME, manifest)
    return manifest


def default_symbols_csv() -> str:
    return ",".join(TARGET_SPOT_SYMBOLS)
