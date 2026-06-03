"""Tests for Hyperliquid funding archive backfill.

Tests cover:
- Date parsing and validation
- Normalization logic
- JSONL output format
- Manifest structure and provenance
- Safety constraints (no forbidden imports/terms)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_funding_archive_backfill as backfill


def test_date_parsing_and_validation() -> None:
    """Test date parsing functions."""
    dt = backfill._parse_date("2024-01-15")
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 15
    assert dt.tzinfo == UTC
    
    ms = backfill._date_to_ms("2024-01-01")
    assert isinstance(ms, int)
    assert ms > 0
    
    iso = backfill._ms_to_iso(ms)
    assert iso.endswith("Z")
    assert "2024-01-01" in iso


def test_normalize_funding_entry() -> None:
    """Test normalization of API response entries."""
    entry = {
        "time": 1704067200000,
        "fundingRate": 0.0001,
    }
    
    row = backfill.normalize_funding_entry(entry, "BTC")
    
    assert row.coin == "BTC"
    assert row.timestamp_ms == 1704067200000
    assert "2024-01-01" in row.timestamp_iso
    assert row.funding_rate == 0.0001
    assert row.hourly_funding_bps == 1.0
    assert row.projected_8h_funding_bps == 8.0
    assert backfill.ENDPOINT in row.source_endpoint


def test_normalize_funding_entry_eth() -> None:
    """Test normalization for ETH."""
    entry = {
        "time": 1704067200000,
        "fundingRate": 0.0002,
    }
    
    row = backfill.normalize_funding_entry(entry, "ETH")
    
    assert row.coin == "ETH"
    assert row.hourly_funding_bps == 2.0
    assert row.projected_8h_funding_bps == 16.0


def test_backfill_result_structure() -> None:
    """Test BackfillResult dataclass structure."""
    result = backfill.BackfillResult(
        asset="BTC",
        start_date="2024-01-01",
        end_date="2024-01-02",
        rows_fetched=100,
        output_path="/tmp/test.jsonl",
        manifest={"test": True},
        error=None,
    )
    
    assert result.asset == "BTC"
    assert result.rows_fetched == 100
    assert result.error is None
    assert result.manifest["test"] is True


def test_sha256_determinism() -> None:
    """Test SHA256 hash is deterministic."""
    text = "test content"
    hash1 = backfill._sha256_text(text)
    hash2 = backfill._sha256_text(text)
    
    assert hash1 == hash2
    assert len(hash1) == 64


def test_ms_to_iso_roundtrip() -> None:
    """Test millisecond to ISO conversion."""
    ms = 1704067200000
    iso = backfill._ms_to_iso(ms)
    assert "2024-01-01T00:00:00" in iso
    assert iso.endswith("Z")


def test_empty_backfill(tmp_path: Path) -> None:
    """Test backfill with no data returned (mocked)."""
    with patch.object(backfill, 'fetch_funding_history', return_value=[]):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    assert result.asset == "BTC"
    assert result.rows_fetched == 0
    assert result.error is None
    assert Path(result.output_path).exists()


def test_manifest_structure(tmp_path: Path) -> None:
    """Test manifest has required fields."""
    with patch.object(backfill, 'fetch_funding_history', return_value=[]):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    manifest = result.manifest
    assert "asset" in manifest
    assert "start_date" in manifest
    assert "end_date" in manifest
    assert "rows_fetched" in manifest
    assert "api_calls" in manifest
    assert "output_jsonl" in manifest
    assert "output_jsonl_sha256" in manifest
    assert "endpoint" in manifest
    assert "generated_at_utc" in manifest
    assert "provenance" in manifest
    assert "validation" in manifest
    
    assert manifest["provenance"]["source"] == "Hyperliquid public /info endpoint"
    assert manifest["provenance"]["request_type"] == "fundingHistory"
    assert manifest["provenance"]["authentication"] == "none"
    
    assert manifest["phase0_ready"] is False
    assert manifest["source_public_unauthenticated"] is True
    assert manifest["auth_headers_used"] is False
    assert manifest["api_keys_used"] is False
    assert manifest["order_or_execution_paths_used"] is False
    assert manifest["polling_mode"] is False
    assert manifest["explicit_date_range_only"] is True
    assert manifest["timestamp_unit_detected"] == "ms"
    assert manifest["cadence_status"] == "HOURLY_ROWS_NOT_CONFIRMED"
    assert manifest["detected_rate_basis"] == "native_decimal_hourly_funding_rate"
    assert manifest["native_interval_hours"] == 1.0
    assert "source_sha256" in manifest
    assert "source_first_row_timestamp_utc" in manifest
    assert "source_last_row_timestamp_utc" in manifest

    assert "rows_sorted_by_timestamp" in manifest["validation"]
    assert "immutable_archive" in manifest["validation"]
    assert "jsonl_format" in manifest["validation"]


def test_jsonl_output_format(tmp_path: Path) -> None:
    """Test JSONL output is valid line-delimited JSON."""
    mock_data = [
        {"time": 1704067200000, "fundingRate": 0.0001},
        {"time": 1704070800000, "fundingRate": 0.0002},
    ]
    
    with patch.object(backfill, 'fetch_funding_history', side_effect=[mock_data, []]):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    jsonl_path = Path(result.output_path)
    assert jsonl_path.exists()
    
    content = jsonl_path.read_text()
    lines = content.strip().split("\n") if content.strip() else []
    
    for line in lines:
        obj = json.loads(line)
        assert "coin" in obj
        assert "timestamp_ms" in obj
        assert "funding_rate" in obj
        assert "hourly_funding_bps" in obj


def test_safety_no_forbidden_imports() -> None:
    """Test that forbidden imports are not present."""
    source = Path(backfill.__file__).read_text()
    
    forbidden_imports = [
        "nautilus_trader",
        "execution",
        "trading",
        "order",
        "wallet",
        "private_key",
        "signing",
    ]
    
    for term in forbidden_imports:
        assert f"import {term}" not in source
        assert f"from {term}" not in source


def test_safety_no_forbidden_terms_in_context() -> None:
    """Test that forbidden terms are not used in function calls."""
    source = Path(backfill.__file__).read_text()
    
    forbidden_patterns = [
        r"submit_order",
        r"cancel_order",
        r"create_order",
        r"TradingNode",
        r"ExecutionClient",
        r"BotGate",
        r"paper_trading",
        r"shadow_execution",
    ]
    
    for pattern in forbidden_patterns:
        assert re.search(pattern, source) is None, f"Forbidden pattern {pattern} found in source"


def test_safety_constants_and_guards() -> None:
    """Test that safety constants are defined."""
    assert hasattr(backfill, 'ENDPOINT')
    assert hasattr(backfill, 'ASSETS')
    assert hasattr(backfill, 'DEFAULT_SLEEP_MS')
    
    assert "api.hyperliquid.xyz" in backfill.ENDPOINT
    assert "info" in backfill.ENDPOINT


def test_backfill_multiple_assets(tmp_path: Path) -> None:
    """Test backfilling multiple assets."""
    def mock_fetch(coin, start_time_ms, end_time_ms=None, sleep_ms=500):
        if start_time_ms > backfill._date_to_ms("2024-01-01"):
            return []
        return [{"time": end_time_ms or start_time_ms, "fundingRate": 0.0001}]
    
    with patch.object(backfill, 'fetch_funding_history', side_effect=mock_fetch):
        results = backfill.backfill_assets(
            assets=["BTC", "ETH"],
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    assert len(results) == 2
    assert results[0].asset == "BTC"
    assert results[1].asset == "ETH"
    
    for result in results:
        assert Path(result.output_path).exists()


def test_pagination_logic(tmp_path: Path) -> None:
    """Test that backfill handles pagination correctly when API returns full pages."""
    call_count = 0
    
    def mock_fetch(coin, start_time_ms, end_time_ms=None, sleep_ms=500):
        nonlocal call_count
        call_count += 1
        
        # Simulate API returning data - first call returns some rows
        if call_count == 1:
            # Return rows up to MAX_ROWS_PER_REQUEST
            return [{"time": 1704067200000 + i * 3600000, "fundingRate": 0.0001} 
                    for i in range(backfill.MAX_ROWS_PER_REQUEST)]
        # Second call would be made if we're paginating
        else:
            return []
    
    with patch.object(backfill, 'fetch_funding_history', side_effect=mock_fetch):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-03",  # 2 day range to allow pagination
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    # Should have made at least 1 call
    assert call_count >= 1
    # Should have fetched rows from first call
    assert result.rows_fetched == backfill.MAX_ROWS_PER_REQUEST


def test_cli_argument_parsing() -> None:
    """Test CLI argument parsing."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import (
        build_parser,
    )
    
    parser = build_parser()
    
    args = parser.parse_args([
        "--asset", "BTC",
        "--start-date", "2024-01-01",
        "--end-date", "2024-01-31",
        "--output-dir", "/tmp/test",
    ])
    
    assert args.asset == ["BTC"]
    assert args.start_date == "2024-01-01"
    assert args.end_date == "2024-01-31"
    assert args.output_dir == Path("/tmp/test")
    assert args.sleep_ms == 500


def test_cli_multiple_assets() -> None:
    """Test CLI with multiple assets."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import (
        build_parser,
    )
    
    parser = build_parser()
    args = parser.parse_args([
        "--asset", "BTC",
        "--asset", "ETH",
        "--start-date", "2024-01-01",
        "--end-date", "2024-01-31",
        "--output-dir", "/tmp/test",
    ])
    
    assert args.asset == ["BTC", "ETH"]


def test_cli_sleep_ms() -> None:
    """Test CLI sleep-ms argument."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import (
        build_parser,
    )
    
    parser = build_parser()
    args = parser.parse_args([
        "--asset", "BTC",
        "--start-date", "2024-01-01",
        "--end-date", "2024-01-31",
        "--output-dir", "/tmp/test",
        "--sleep-ms", "1000",
    ])
    
    assert args.sleep_ms == 1000


def test_date_validation() -> None:
    """Test date validation in CLI."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import (
        _validate_date,
    )
    
    assert _validate_date("2024-01-01") is True
    assert _validate_date("2024-12-31") is True
    assert _validate_date("01-01-2024") is False
    assert _validate_date("2024/01/01") is False
    assert _validate_date("invalid") is False


def test_manifest_sha256_verification(tmp_path: Path) -> None:
    """Test that manifest SHA256 matches file content."""
    mock_data = [
        {"time": 1704067200000, "fundingRate": 0.0001},
    ]
    
    with patch.object(backfill, 'fetch_funding_history', side_effect=[mock_data, []]):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    jsonl_path = Path(result.output_path)
    content = jsonl_path.read_text()
    
    expected_hash = backfill._sha256_text(content)
    assert result.manifest["output_jsonl_sha256"] == expected_hash


def test_timestamp_sorting(tmp_path: Path) -> None:
    """Test that output is sorted by timestamp."""
    mock_data = [
        {"time": 1704074400000, "fundingRate": 0.0003},
        {"time": 1704067200000, "fundingRate": 0.0001},
        {"time": 1704070800000, "fundingRate": 0.0002},
    ]
    
    with patch.object(backfill, 'fetch_funding_history', side_effect=[mock_data, []]):
        result = backfill.backfill_asset(
            coin="BTC",
            start_date="2024-01-01",
            end_date="2024-01-01",
            output_dir=tmp_path,
            sleep_ms=0,
        )
    
    jsonl_path = Path(result.output_path)
    content = jsonl_path.read_text()
    lines = content.strip().split("\n")
    
    timestamps = []
    for line in lines:
        obj = json.loads(line)
        timestamps.append(obj["timestamp_ms"])
    
    assert timestamps == sorted(timestamps)


def test_cli_runner_smoke(tmp_path: Path) -> None:
    """Smoke test for CLI runner with mocked API."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import main
    
    with patch.object(backfill, 'fetch_funding_history', return_value=[]):
        rc = main([
            "--asset", "BTC",
            "--start-date", "2024-01-01",
            "--end-date", "2024-01-01",
            "--output-dir", str(tmp_path),
            "--sleep-ms", "0",
        ])
    
    assert rc == 0
    assert (tmp_path / "hyperliquid_funding_BTC_2024-01-01_to_2024-01-01.jsonl").exists()
    assert (tmp_path / "hyperliquid_funding_BTC_2024-01-01_to_2024-01-01.manifest.json").exists()


def test_cli_invalid_asset() -> None:
    """Test CLI rejects invalid assets."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import main
    
    rc = main([
        "--asset", "INVALID",
        "--start-date", "2024-01-01",
        "--end-date", "2024-01-01",
        "--output-dir", "/tmp/test",
    ])
    
    assert rc == 1


def test_cli_date_before_end() -> None:
    """Test CLI rejects end-date before start-date."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import main
    
    rc = main([
        "--asset", "BTC",
        "--start-date", "2024-01-31",
        "--end-date", "2024-01-01",
        "--output-dir", "/tmp/test",
    ])
    
    assert rc == 1


def test_cli_invalid_date_format() -> None:
    """Test CLI rejects invalid date format."""
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_funding_archive_backfill import main
    
    rc = main([
        "--asset", "BTC",
        "--start-date", "01-01-2024",
        "--end-date", "2024-01-31",
        "--output-dir", "/tmp/test",
    ])
    
    assert rc == 1
