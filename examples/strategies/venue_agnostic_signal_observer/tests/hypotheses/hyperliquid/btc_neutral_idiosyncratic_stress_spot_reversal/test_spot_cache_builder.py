from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import cast

import pytest

from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import AmbiguousTimestampError
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import DownloadBudgetExceededError
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import NetworkAccessDeniedError
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import build_monthly_plan
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import build_spot_cache
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import normalize_kline_row
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_cache_builder import normalize_kline_timestamp
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.btc_neutral_idiosyncratic_stress_spot_reversal.spot_klines_loader import load_spot_klines_csv
from examples.strategies.venue_agnostic_signal_observer.run_btc_neutral_spot_reversal_spot_cache_builder import main as wrapper_main


class FakeFetcher:
    def __init__(self, payloads: dict[str, bytes] | None = None) -> None:
        self.payloads = payloads or {}
        self.calls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        payload = self.payloads.get(url)
        if payload is None:
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "not found", hdrs=None, fp=None)
        return payload


def _zip_payload(file_name: str, rows: list[list[str]]) -> bytes:
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerows(rows)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(file_name, csv_buf.getvalue())
    return outer.getvalue()


def test_parse_binance_kline_timestamp_ms() -> None:
    assert normalize_kline_timestamp("1704067200000") == "2024-01-01T00:00:00Z"


def test_parse_binance_kline_timestamp_us() -> None:
    assert normalize_kline_timestamp("1735689600000000") == "2025-01-01T00:00:00Z"


def test_parse_binance_kline_timestamp_rejects_ambiguous() -> None:
    with pytest.raises(AmbiguousTimestampError):
        normalize_kline_timestamp("1704067200")


def test_kline_row_normalizes_to_runner_csv_schema() -> None:
    row = normalize_kline_row(["1704067200000", "1.0", "1.1", "0.9", "1.05", "42.0"])
    assert row == {
        "timestamp": "2024-01-01T00:00:00Z",
        "open": "1.0",
        "high": "1.1",
        "low": "0.9",
        "close": "1.05",
        "volume": "42.0",
    }


def test_build_monthly_plan_urls() -> None:
    items = build_monthly_plan(["ETHUSDT"], "2024-01-15", "2024-03-02")
    assert len(items) == 3
    assert items[0].url.endswith("monthly/klines/ETHUSDT/1h/ETHUSDT-1h-2024-01.zip")
    assert items[0].is_monthly is True
    assert items[2].url.endswith("2024-03.zip")


def test_monthly_first_uses_monthly_when_available(tmp_path: Path) -> None:
    monthly_url = "https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/1h/ETHUSDT-1h-2024-01.zip"
    payload = _zip_payload("ETHUSDT-1h-2024-01.csv", [["1704067200000", "1", "1", "1", "1", "1"]] * 24)
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["ETHUSDT"],
        start="2024-01-01",
        end="2024-01-20",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="monthly-first",
        fetcher=FakeFetcher({monthly_url: payload}),
    )
    assert manifest["symbols_downloaded"] == ["ETHUSDT"]
    assert manifest["monthly_files_downloaded"] == 1
    assert manifest["daily_files_downloaded"] == 0


def test_monthly_first_falls_back_to_daily_when_monthly_unavailable(tmp_path: Path) -> None:
    daily_url = "https://data.binance.vision/data/spot/daily/klines/ETHUSDT/1h/ETHUSDT-1h-2024-01-01.zip"
    payload = _zip_payload("ETHUSDT-1h-2024-01-01.csv", [["1704067200000", "2", "2", "2", "2", "2"]])
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["ETHUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="monthly-first",
        fetcher=FakeFetcher({daily_url: payload}),
    )
    assert manifest["symbols_downloaded"] == ["ETHUSDT"]
    assert manifest["daily_files_downloaded"] == 1


def test_monthly_only_does_not_fallback(tmp_path: Path) -> None:
    monthly_url = "https://data.binance.vision/data/spot/monthly/klines/ADAUSDT/1h/ADAUSDT-1h-2024-01.zip"
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["ADAUSDT"],
        start="2024-01-01",
        end="2024-01-02",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="monthly-only",
        fetcher=FakeFetcher({monthly_url: _zip_payload("ADAUSDT-1h-2024-01.csv", [["999", "1", "1", "1", "1", "1"]])}),
    )
    assert manifest["symbols_unavailable"] == ["ADAUSDT"]


def test_daily_only_preserves_old_daily_behavior(tmp_path: Path) -> None:
    daily_url = "https://data.binance.vision/data/spot/daily/klines/BNBUSDT/1h/BNBUSDT-1h-2024-01-01.zip"
    payload = _zip_payload("BNBUSDT-1h-2024-01-01.csv", [["1704067200000", "2", "2", "2", "2", "2"]])
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["BNBUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="daily-only",
        fetcher=FakeFetcher({daily_url: payload}),
    )
    assert manifest["symbols_downloaded"] == ["BNBUSDT"]
    assert manifest["daily_files_downloaded"] == 1


def test_budget_cap_counts_only_newly_downloaded_bytes(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    raw_root = cache_root / "raw_zips_daily"
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / "SOLUSDT").mkdir(parents=True, exist_ok=True)
    raw_existing = raw_root / "SOLUSDT/SOLUSDT-1h-2024-01-01.zip"
    raw_existing.write_bytes(b"oldpayload")
    daily_url = "https://data.binance.vision/data/spot/daily/klines/SOLUSDT/1h/SOLUSDT-1h-2024-01-01.zip"
    new_payload = _zip_payload(
        "SOLUSDT-1h-2024-01-01.csv",
        [["1704067200000", "3", "3", "3", "3", "3"]],
    )
    manifest = build_spot_cache(
        cache_root=cache_root,
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=50_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="daily-only",
        overwrite=False,
        fetcher=FakeFetcher({daily_url: new_payload}),
    )
    files_downloaded = manifest.get("files_downloaded", [])
    assert isinstance(files_downloaded, list)
    downloaded_paths = {
        str(entry.get("relative_zip_path", ""))
        for entry in files_downloaded
        if isinstance(entry, dict)
    }
    assert "raw_zips_daily/SOLUSDT/SOLUSDT-1h-2024-01-01.zip" not in downloaded_paths
    assert manifest["total_bytes_downloaded"] == len(new_payload)


def test_dry_run_does_not_download(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-02",
        download_budget_bytes=1_000_000,
        allow_network_public=False,
        dry_run=True,
        fetcher=fetcher,
    )
    assert fetcher.calls == []
    assert manifest["network_used"] is False
    assert manifest["dry_run"] is True


def test_manifest_merge_preserves_previous_downloaded_symbols(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    (cache_root / "csv").mkdir(parents=True)
    existing_csv = cache_root / "csv" / "SOLUSDT-1h.csv"
    existing_csv.write_text(
        "timestamp,open,high,low,close,volume\n2024-01-01T00:00:00Z,1.0,1.1,0.9,1.05,42.0\n",
        encoding="utf-8",
    )
    cache_root.joinpath("spot_cache_manifest.json").write_text(
        json.dumps(
            {
                "symbols_requested": ["SOLUSDT"],
                "symbols_downloaded": ["SOLUSDT"],
                "symbols_unavailable": [],
                "file_hashes": {
                    "csv/SOLUSDT-1h.csv": {
                        "sha256": "existing",
                        "row_count": 1,
                        "first_timestamp": "2024-01-01T00:00:00Z",
                        "last_timestamp": "2024-01-01T00:00:00Z",
                        "missing_row_count": 0,
                    }
                },
                "files_downloaded": [
                    {
                        "symbol": "SOLUSDT",
                        "day": "2024-01-01",
                        "url": "https://example.invalid/SOLUSDT-1h-2024-01-01.zip",
                        "relative_zip_path": "raw_zips/SOLUSDT/SOLUSDT-1h-2024-01-01.zip",
                        "sha256": "abc",
                        "size_bytes": 123,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    daily_url = "https://data.binance.vision/data/spot/daily/klines/DOGEUSDT/1h/DOGEUSDT-1h-2024-01-01.zip"
    manifest = build_spot_cache(
        cache_root=cache_root,
        symbols=["DOGEUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="daily-only",
        fetcher=FakeFetcher(
            {
                daily_url: _zip_payload(
                    "DOGEUSDT-1h-2024-01-01.csv",
                    [["1704067200000", "2.0", "2.1", "1.9", "2.05", "24.0"]],
                )
            }
        ),
    )

    assert manifest["symbols_downloaded"] == ["DOGEUSDT", "SOLUSDT"]
    assert manifest["symbols_requested"] == ["DOGEUSDT", "SOLUSDT"]
    assert manifest["symbols_unavailable"] == []


def test_dry_run_performs_no_network_writes(tmp_path: Path) -> None:
    fetcher = FakeFetcher()
    manifest = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-02",
        download_budget_bytes=1_000_000,
        allow_network_public=False,
        dry_run=True,
        fetcher=fetcher,
    )
    assert fetcher.calls == []
    assert manifest["network_used"] is False
    assert manifest["dry_run"] is True


def test_idempotent_repeated_run_does_not_grow_files_downloaded(tmp_path: Path) -> None:
    url = "https://data.binance.vision/data/spot/daily/klines/SOLUSDT/1h/SOLUSDT-1h-2024-01-01.zip"
    payload = _zip_payload("SOLUSDT-1h-2024-01-01.csv", [["1704067200000", "1", "1", "1", "1", "1"]])

    first = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="daily-only",
        fetcher=FakeFetcher({url: payload}),
    )
    second = build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=False,
        archive_granularity="daily-only",
        fetcher=FakeFetcher({url: payload}),
    )
    assert first["symbols_downloaded"] == second["symbols_downloaded"]
    assert len(first["files_downloaded"]) == len(second["files_downloaded"])


def test_wrapper_imports_and_delegates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_build(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "examples.strategies.venue_agnostic_signal_observer.run_btc_neutral_spot_reversal_spot_cache_builder.build_spot_cache",
        fake_build,
    )
    import examples.strategies.venue_agnostic_signal_observer.run_btc_neutral_spot_reversal_spot_cache_builder as wrapper

    result = wrapper.build_spot_cache(
        cache_root=tmp_path / "cache",
        symbols=["SOLUSDT"],
        start="2024-01-01",
        end="2024-01-01",
        download_budget_bytes=1_000_000,
        allow_network_public=True,
        dry_run=True,
    )
    assert result == {"ok": True}
    assert captured["cache_root"] == tmp_path / "cache"
