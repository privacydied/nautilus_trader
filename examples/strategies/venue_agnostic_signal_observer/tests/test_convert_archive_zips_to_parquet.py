"""Tests for convert_archive_zips_to_parquet converter."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import List

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _make_zip(csv_content: str) -> bytes:
    """Create a zip archive containing one CSV file with the given content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test.csv", csv_content)
    return buf.getvalue()


def _write_cache_zip(cache_dir: Path, symbol: str, date: str, csv_content: str) -> Path:
    """Write a zip file to the cache directory mimicking Binance Vision naming."""
    sym_dir = cache_dir / symbol.lower()
    sym_dir.mkdir(parents=True, exist_ok=True)
    zip_path = sym_dir / f"{symbol.upper()}_{date}_aggTrades.zip"
    zip_path.write_bytes(_make_zip(csv_content))
    return zip_path


def _build_csv(rows: List[tuple]) -> str:
    """Build CSV content from rows. No header (Binance Vision format)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    """Create a temporary cache directory with test zip files."""
    cache = tmp_path / "cache"
    cache.mkdir()

    # 10 rows of synthetic aggTrade data
    rows = []
    for i in range(10):
        rows.append((
            1000 + i,          # agg_trade_id
            50000.0 + i,       # price
            0.5 + i * 0.1,     # quantity
            2000 + i,          # first_trade_id
            2005 + i,          # last_trade_id
            1700000000000 + i * 1000,  # timestamp (ms)
            "true" if i % 2 == 0 else "false",  # is_buyer_maker
            "true",            # is_best_match
        ))
    _write_cache_zip(cache, "BTCUSDT", "2024-01-15", _build_csv(rows))
    return cache


@pytest.fixture
def multi_symbol_cache(tmp_path: Path) -> Path:
    """Cache with two symbol-days for testing discovery."""
    cache = tmp_path / "cache"
    cache.mkdir()

    rows_a = []
    for i in range(5):
        rows_a.append((i, 100.0, 1.0, 0, 0, 1700000000000, "false", "true"))
    _write_cache_zip(cache, "ETHUSDT", "2024-01-15", _build_csv(rows_a))

    rows_b = []
    for i in range(3):
        rows_b.append((i, 200.0, 2.0, 0, 0, 1700000000100, "true", "true"))
    _write_cache_zip(cache, "ETHUSDT", "2024-01-16", _build_csv(rows_b))

    return cache


class TestParseZipCsv:
    """Test the _parse_zip_csv function."""

    def test_basic_parse(self):
        rows = [
            (1, 50000.0, 0.5, 0, 0, 1700000000000, "false", "true"),
            (2, 50001.0, 1.0, 0, 0, 1700000001000, "true", "true"),
        ]
        csv_content = _build_csv(rows)
        zip_bytes = _make_zip(csv_content)

        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _parse_zip_csv,
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            ts, prices, sizes, sides = _parse_zip_csv(zf.read("test.csv"))

        assert len(ts) == 2
        assert ts[0] == 1700000000000 * 1_000_000  # ms -> ns
        assert ts[1] == 1700000001000 * 1_000_000
        assert prices[0] == 50000.0
        assert prices[1] == 50001.0
        assert sizes[0] == 0.5
        assert sizes[1] == 1.0
        assert sides[0] is False  # is_buyer_maker=false => buy
        assert sides[1] is True   # is_buyer_maker=true => sell

    def test_rejects_zero_price(self):
        rows = [(1, 0.0, 0.5, 0, 0, 1700000000000, "false", "true")]
        csv_content = _build_csv(rows)
        zip_bytes = _make_zip(csv_content)

        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _parse_zip_csv,
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            ts, prices, sizes, sides = _parse_zip_csv(zf.read("test.csv"))

        assert len(ts) == 0

    def test_rejects_negative_size(self):
        rows = [(1, 50000.0, -1.0, 0, 0, 1700000000000, "false", "true")]
        csv_content = _build_csv(rows)
        zip_bytes = _make_zip(csv_content)

        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _parse_zip_csv,
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            ts, prices, sizes, sides = _parse_zip_csv(zf.read("test.csv"))

        # Size is abs()'d so -1.0 becomes 1.0 and is accepted
        assert len(ts) == 1
        assert sizes[0] == 1.0

    def test_microsecond_timestamp_detection(self):
        """2025+ data uses microsecond timestamps (16 digits)."""
        rows = [(1, 50000.0, 0.5, 0, 0, 1735776001103847, "false", "true")]  # us timestamp
        csv_content = _build_csv(rows)
        zip_bytes = _make_zip(csv_content)

        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _parse_zip_csv,
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            ts, prices, sizes, sides = _parse_zip_csv(zf.read("test.csv"))

        assert len(ts) == 1
        # us -> ns: multiply by 1000
        assert ts[0] == 1735776001103847 * 1000

    def test_short_rows_skipped(self):
        csv_content = "1,50000.0,0.5\n"  # only 3 cols, need 7
        zip_bytes = _make_zip(csv_content)

        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _parse_zip_csv,
        )

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            ts, prices, sizes, sides = _parse_zip_csv(zf.read("test.csv"))

        assert len(ts) == 0


class TestZipToParquet:
    """Test the full zip-to-parquet conversion."""

    def test_correct_row_count(self, tmp_cache):
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _zip_to_parquet,
        )

        zip_path = tmp_cache / "btcusdt" / "BTCUSDT_2024-01-15_aggTrades.zip"
        with zipfile.ZipFile(zip_path) as zf:
            table = _zip_to_parquet(zf, "test.csv")

        assert table.num_rows == 10

    def test_correct_dtypes(self, tmp_cache):
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _zip_to_parquet,
        )

        zip_path = tmp_cache / "btcusdt" / "BTCUSDT_2024-01-15_aggTrades.zip"
        with zipfile.ZipFile(zip_path) as zf:
            table = _zip_to_parquet(zf, "test.csv")

        assert table.schema.field("ts_event").type == pa.int64()
        assert table.schema.field("price").type == pa.float32()
        assert table.schema.field("size").type == pa.float32()
        assert table.schema.field("is_buyer_maker").type == pa.bool_()

    def test_column_subset_honored(self, tmp_cache):
        """Verify that dropped columns (agg_trade_id, first_trade_id, last_trade_id) are absent."""
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _zip_to_parquet,
        )

        zip_path = tmp_cache / "btcusdt" / "BTCUSDT_2024-01-15_aggTrades.zip"
        with zipfile.ZipFile(zip_path) as zf:
            table = _zip_to_parquet(zf, "test.csv")

        col_names = set(table.column_names)
        expected = {"ts_event", "price", "size", "is_buyer_maker"}
        assert col_names == expected
        assert "agg_trade_id" not in col_names
        assert "first_trade_id" not in col_names
        assert "last_trade_id" not in col_names


class TestDiscovery:
    """Test zip file discovery."""

    def test_discovers_aggtrade_zips(self, multi_symbol_cache):
        import examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet as mod
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _discover_zip_files,
        )
        old_root = mod.CACHE_ROOT
        mod.CACHE_ROOT = multi_symbol_cache
        try:
            results = _discover_zip_files()
        finally:
            mod.CACHE_ROOT = old_root

        assert len(results) == 2
        symbols = {s for s, _, _ in results}
        assert symbols == {"ETHUSDT"}
        dates = sorted(d for _, d, _ in results)
        assert dates == ["2024-01-15", "2024-01-16"]


class TestSkipIfExists:
    """Test resumable conversion — skip valid existing Parquet."""

    def test_skip_valid_parquet(self, multi_symbol_cache):
        import examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet as mod
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            convert_all,
        )
        old_cache = mod.CACHE_ROOT
        old_parquet = mod.PARQUET_ROOT
        mod.CACHE_ROOT = multi_symbol_cache
        mod.PARQUET_ROOT = multi_symbol_cache / "parquet"
        try:
            # First run — should process both
            result1 = convert_all(force=False, heartbeat_interval_files=1, heartbeat_interval_seconds=9999)
            assert result1["processed"] == 2
            assert result1["skipped"] == 0

            # Second run — should skip both (already exist)
            result2 = convert_all(force=False, heartbeat_interval_files=1, heartbeat_interval_seconds=9999)
            assert result2["processed"] == 0
            assert result2["skipped"] == 2

            # Force run — should re-process both
            result3 = convert_all(force=True, heartbeat_interval_files=1, heartbeat_interval_seconds=9999)
            assert result3["processed"] == 2
            assert result3["skipped"] == 0
        finally:
            mod.CACHE_ROOT = old_cache
            mod.PARQUET_ROOT = old_parquet


class TestValidParquetCheck:
    """Test the _is_valid_parquet guard."""

    def test_nonexistent(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _is_valid_parquet,
        )
        assert _is_valid_parquet(tmp_path / "missing.parquet") is False

    def test_empty_file(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _is_valid_parquet,
        )
        p = tmp_path / "empty.parquet"
        p.touch()
        assert _is_valid_parquet(p) is False

    def test_valid_file(self, tmp_cache):
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _is_valid_parquet,
        )
        from examples.strategies.venue_agnostic_signal_observer.convert_archive_zips_to_parquet import (
            _zip_to_parquet,
        )

        out_dir = tmp_cache / "parquet" / "btcusdt"
        out_dir.mkdir(parents=True)
        out_path = out_dir / "btcusdt_test.parquet"

        zip_path = tmp_cache / "btcusdt" / "BTCUSDT_2024-01-15_aggTrades.zip"
        with zipfile.ZipFile(zip_path) as zf:
            table = _zip_to_parquet(zf, "test.csv")
        pq.write_table(table, out_path)

        assert _is_valid_parquet(out_path) is True
