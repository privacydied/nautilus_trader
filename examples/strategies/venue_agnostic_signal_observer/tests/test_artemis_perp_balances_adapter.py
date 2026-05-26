"""Tests for the Artemis daily balances adapter.

Covers CSV/JSON parsing, delta computation, schema validation,
source-unavailable results, and source inventory checks.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile

import pytest

from examples.strategies.venue_agnostic_signal_observer.adapters.artemis_perp_balances_adapter import (
    DailyBalanceRecord,
    DailyBalanceResult,
    ArtemisSchemaError,
    parse_artemis_csv_line,
    load_daily_balances_from_csv,
    load_daily_balances_from_json,
    compute_daily_deltas,
    check_artemis_source,
)


# ===========================================================================
# CSV parsing
# ===========================================================================


def test_parse_csv_line_valid():
    """Valid CSV line parses correctly."""
    row = {"date": "2025-08-17", "symbol": "BTC", "position_size": "10.5",
           "spot_balance": "2.0"}
    record = parse_artemis_csv_line(row)
    assert record.date_iso == "2025-08-17"
    assert record.symbol == "BTC"
    assert record.position_size == 10.5
    assert record.spot_balance == 2.0


def test_parse_csv_line_alternate_columns():
    """Alternate column names work."""
    row = {"Date": "2025-08-17", "Symbol": "ETH", "Position": "-5.0"}
    record = parse_artemis_csv_line(row)
    assert record.symbol == "ETH"
    assert record.position_size == -5.0


def test_parse_csv_line_missing_required():
    """Missing required fields raises ArtemisSchemaError."""
    row = {"date": "2025-08-17"}  # missing symbol and position
    with pytest.raises(ArtemisSchemaError):
        parse_artemis_csv_line(row)


# ===========================================================================
# CSV file loading
# ===========================================================================


def test_load_csv_valid(tmp_path):
    """Load valid CSV file."""
    fpath = tmp_path / "balances.csv"
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "symbol", "position_size", "spot_balance"])
        writer.writeheader()
        writer.writerow({"date": "2025-08-17", "symbol": "BTC", "position_size": "10.0", "spot_balance": "2.0"})
        writer.writerow({"date": "2025-08-18", "symbol": "BTC", "position_size": "12.0", "spot_balance": "2.5"})

    result = load_daily_balances_from_csv(fpath)
    assert result.available
    assert len(result.records) == 2
    assert result.schema_version == "artemis_csv_v1"


def test_load_csv_empty(tmp_path):
    """Load empty CSV returns unavailable."""
    fpath = tmp_path / "empty.csv"
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "symbol"])
        writer.writeheader()

    result = load_daily_balances_from_csv(fpath)
    assert not result.available


def test_load_csv_not_found():
    """Load non-existent CSV returns unavailable."""
    result = load_daily_balances_from_csv("/nonexistent/file.csv")
    assert not result.available
    assert "not found" in (result.error or "")


# ===========================================================================
# JSON file loading
# ===========================================================================


def test_load_json_valid(tmp_path):
    """Load valid JSON balance data."""
    data = [
        {"date": "2025-08-17", "symbol": "BTC", "position_size": 10.0},
        {"date": "2025-08-18", "symbol": "BTC", "position_size": 12.0},
    ]
    fpath = tmp_path / "balances.json"
    with open(fpath, "w") as f:
        json.dump(data, f)

    result = load_daily_balances_from_json(fpath)
    assert result.available
    assert len(result.records) == 2


def test_load_json_not_found():
    """Load non-existent JSON returns unavailable."""
    result = load_daily_balances_from_json("/nonexistent/file.json")
    assert not result.available


def test_load_json_invalid(tmp_path):
    """Load invalid JSON returns unavailable."""
    fpath = tmp_path / "bad.json"
    with open(fpath, "w") as f:
        f.write("not json")

    result = load_daily_balances_from_json(fpath)
    assert not result.available
    assert "Parse error" in (result.error or "")


# ===========================================================================
# Missing source produces empty artifact metadata
# ===========================================================================


def test_artemis_unavailable():
    """Missing source has available=False and no records."""
    result = check_artemis_source("/nonexistent")
    assert not result.available
    assert len(result.records) == 0


def test_artemis_unavailable_no_root():
    """None data_root produces unavailable result."""
    result = check_artemis_source(None)
    assert not result.available
    assert result.error == "No data_root provided"


# ===========================================================================
# Daily delta computation
# ===========================================================================


def test_compute_daily_deltas():
    """Day-over-day deltas are computed correctly."""
    records = [
        DailyBalanceRecord(date_iso="2025-08-17", symbol="BTC", vault_address="0xhlp", position_size=10.0),
        DailyBalanceRecord(date_iso="2025-08-18", symbol="BTC", vault_address="0xhlp", position_size=15.0),
        DailyBalanceRecord(date_iso="2025-08-19", symbol="BTC", vault_address="0xhlp", position_size=12.0),
    ]
    deltas = compute_daily_deltas(records)
    key = "0xhlp:BTC"
    assert key in deltas
    assert len(deltas[key]) == 2
    assert deltas[key][0] == ("2025-08-18", 5.0)  # 15 - 10
    assert deltas[key][1] == ("2025-08-19", -3.0)  # 12 - 15


def test_compute_daily_deltas_empty():
    """Empty records produce empty deltas."""
    deltas = compute_daily_deltas([])
    assert len(deltas) == 0


def test_compute_daily_deltas_multiple_symbols():
    """Multiple symbols are handled independently."""
    records = [
        DailyBalanceRecord(date_iso="2025-08-17", symbol="BTC", vault_address="0xhlp", position_size=10.0),
        DailyBalanceRecord(date_iso="2025-08-17", symbol="ETH", vault_address="0xhlp", position_size=100.0),
        DailyBalanceRecord(date_iso="2025-08-18", symbol="BTC", vault_address="0xhlp", position_size=12.0),
    ]
    deltas = compute_daily_deltas(records)
    assert "0xhlp:BTC" in deltas
    assert "0xhlp:ETH" in deltas
    assert len(deltas["0xhlp:ETH"]) == 0  # only one entry, no delta


# ===========================================================================
# Source inventory
# ===========================================================================


def test_artemis_source_found(tmp_path):
    """Existing CSV file is found by source check."""
    fpath = tmp_path / "artemis" / "daily_perp_balances.csv"
    os.makedirs(os.path.dirname(fpath))
    with open(fpath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "symbol", "position_size"])
        writer.writeheader()
        writer.writerow({"date": "2025-08-17", "symbol": "BTC", "position_size": "10.0"})

    result = check_artemis_source(str(tmp_path))
    assert result.available


def test_artemis_source_not_found():
    """No Artemis file returns unavailable."""
    with tempfile.TemporaryDirectory() as d:
        result = check_artemis_source(d)
        assert not result.available
        assert "No Artemis balance file found" in (result.error or "")


# ===========================================================================
# Forbidden API strings
# ===========================================================================


def test_no_user_fills_by_time():
    """Adapter does not contain userFillsByTime."""
    import examples.strategies.venue_agnostic_signal_observer.adapters.artemis_perp_balances_adapter as mod
    content = open(mod.__file__).read()
    assert "userFillsByTime" not in content