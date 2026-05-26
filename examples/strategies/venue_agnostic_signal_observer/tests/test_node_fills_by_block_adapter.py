"""Tests for the node_fills_by_block adapter.

Covers fill parsing, schema validation, streaming, delta computation,
filtering, and source inventory checks.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from examples.strategies.venue_agnostic_signal_observer.adapters.node_fills_by_block_adapter import (
    FillRecord,
    NodeFillsSchemaError,
    parse_fill_row,
    stream_fills_from_file,
    load_fills_to_list,
    validate_fill_row,
    filter_fills_by_vault,
    compute_signed_delta,
    check_node_fills_source,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def jsonl_fills():
    """Create a JSONL file with valid fill records."""
    d = tempfile.mkdtemp()
    fpath = os.path.join(d, "fills.jsonl")
    rows = [
        {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "0xhlp", "seller": "0xuser1",
         "time": 1700000000000000000, "hash": "0xtx1", "block": 12345, "liquidation": True},
        {"coin": "ETH", "sz": -10.0, "px": 3000.0, "buyer": "0xuser2", "seller": "0xhlp",
         "time": 1700000100000000000, "hash": "0xtx2", "block": 12346, "liquidation": False},
        {"coin": "SOL", "sz": 5.0, "px": 100.0, "buyer": "0xhlp", "seller": "0xuser3",
         "time": 1700000200000000000, "hash": "0xtx3", "block": 12347},
    ]
    with open(fpath, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    yield fpath
    import shutil
    shutil.rmtree(d)


# ===========================================================================
# Fill parsing
# ===========================================================================


def test_parse_fill_row_buyer_hlp(jsonl_fills):
    """Parse a fill where HLP is buyer."""
    fills = load_fills_to_list(jsonl_fills)
    btc_fill = fills[0]
    assert btc_fill.symbol == "BTC"
    assert btc_fill.buyer == "0xhlp"
    assert btc_fill.seller == "0xuser1"
    assert btc_fill.size == 1.0
    assert btc_fill.liquidation
    assert btc_fill.block_number == 12345


def test_parse_fill_row_seller_hlp(jsonl_fills):
    """Parse a fill where HLP is seller."""
    fills = load_fills_to_list(jsonl_fills)
    eth_fill = fills[1]
    assert eth_fill.symbol == "ETH"
    assert eth_fill.buyer == "0xuser2"
    assert eth_fill.seller == "0xhlp"
    assert eth_fill.size == 10.0
    assert not eth_fill.liquidation


def test_parse_fill_negative_size_absolute(jsonl_fills):
    """Negative sz is converted to absolute size."""
    fills = load_fills_to_list(jsonl_fills)
    eth_fill = fills[1]
    assert eth_fill.size == 10.0  # abs(-10.0)
    assert eth_fill.side == "sell"


def test_parse_fill_positive_size(jsonl_fills):
    """Positive sz produces buy side."""
    fills = load_fills_to_list(jsonl_fills)
    btc_fill = fills[0]
    assert btc_fill.side == "buy"


# =======================================================================
# Signed delta arithmetic
# =======================================================================


def test_signed_delta_vault_is_buyer(jsonl_fills):
    """Vault as buyer -> positive delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[0], "0xhlp")  # BTC fill, HLP buyer
    assert delta == 1.0


def test_signed_delta_vault_is_seller(jsonl_fills):
    """Vault as seller -> negative delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[1], "0xhlp")  # ETH fill, HLP seller
    assert delta == -10.0


def test_signed_delta_vault_not_involved(jsonl_fills):
    """Vault not in fill -> zero delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[0], "0xunknown")
    assert delta == 0.0


# =======================================================================
# Filtering by vault
# =======================================================================


def test_filter_fills_by_vault(jsonl_fills):
    """Filter fills where vault is buyer or seller."""
    fills = load_fills_to_list(jsonl_fills)
    vault_fills = list(filter_fills_by_vault(fills, {"0xhlp"}))
    assert len(vault_fills) == 3  # all three have 0xhlp on one side


def test_filter_fills_by_vault_other_address(jsonl_fills):
    """Filter for address not in fills returns empty."""
    fills = load_fills_to_list(jsonl_fills)
    vault_fills = list(filter_fills_by_vault(fills, {"0xnonexistent"}))
    assert len(vault_fills) == 0


def test_filter_fills_multiple_addresses(jsonl_fills):
    """Filter by multiple addresses works."""
    fills = load_fills_to_list(jsonl_fills)
    vault_fills = list(filter_fills_by_vault(fills, {"0xhlp", "0xuser1"}))
    assert len(vault_fills) >= 1


# =======================================================================
# Buyer/seller direction
# =======================================================================


def test_buyer_direction_correct(jsonl_fills):
    """Buyer has correct address and side."""
    fills = load_fills_to_list(jsonl_fills)
    assert fills[0].buyer == "0xhlp"
    assert fills[1].seller == "0xhlp"
    assert fills[2].buyer == "0xhlp"


# =======================================================================
# Schema validation
# =======================================================================


def test_validate_missing_required_field():
    """Missing required field raises NodeFillsSchemaError."""
    row = {"coin": "BTC", "sz": 1.0}  # missing px, buyer, seller, time
    with pytest.raises(NodeFillsSchemaError):
        validate_fill_row(row, 0)


def test_validate_null_required_field():
    """Null required field raises NodeFillsSchemaError."""
    row = {"coin": "BTC", "sz": None, "px": 50000.0, "buyer": "addr",
           "seller": "addr2", "time": 1000}
    with pytest.raises(NodeFillsSchemaError):
        validate_fill_row(row, 0)


def test_validate_valid_row():
    """Valid row passes validation."""
    row = {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "addr",
           "seller": "addr2", "time": 1000}
    validate_fill_row(row, 0)  # should not raise


# =======================================================================
# Streaming reader
# =======================================================================


def test_stream_fills_from_file(jsonl_fills):
    """Stream fills from JSONL file."""
    records = list(stream_fills_from_file(jsonl_fills))
    assert len(records) == 3


def test_stream_fills_empty_lines(jsonl_fills):
    """Empty lines in JSONL are skipped."""
    with open(jsonl_fills, "a") as f:
        f.write("\n\n")
        f.write(json.dumps({"coin": "DOT", "sz": 1.0, "px": 10.0,
                            "buyer": "a", "seller": "b", "time": 999}) + "\n")
    records = list(stream_fills_from_file(jsonl_fills))
    assert len(records) == 4


# =======================================================================
# Source inventory
# =======================================================================


def test_check_node_fills_source_found(jsonl_fills):
    """Source check finds available data."""
    data_root = os.path.dirname(jsonl_fills)
    available, path, schema, count = check_node_fills_source(data_root)
    # The fixture path doesn't match the expected naming pattern
    # Check with correct file name
    import shutil
    dst = os.path.join(data_root, "node_fills_by_block.jsonl")
    shutil.copy(jsonl_fills, dst)
    available2, path2, schema2, count2 = check_node_fills_source(data_root)
    assert available2


def test_check_node_fills_source_not_found():
    """Source check returns unavailable for missing path."""
    available, path, schema, count = check_node_fills_source("/nonexistent")
    assert not available


def test_check_node_fills_source_no_root():
    """Source check returns unavailable for None."""
    available, path, schema, count = check_node_fills_source(None)
    assert not available


# =======================================================================
# Forbidden API strings
# =======================================================================


def test_no_user_fills_by_time():
    """Adapter does not contain userFillsByTime."""
    import examples.strategies.venue_agnostic_signal_observer.adapters.node_fills_by_block_adapter as mod
    content = open(mod.__file__).read()
    assert "userFillsByTime" not in content


# =======================================================================
# Timestamp normalization
# =======================================================================


def test_parse_fill_millisecond_timestamp():
    """Fill with ms timestamp is normalized to ns."""
    row = {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1700000000000}  # ms
    fill = parse_fill_row(row, 0)
    assert fill.timestamp_ns == 1700000000000 * 1_000_000


def test_parse_fill_microsecond_timestamp():
    """Fill with us timestamp is normalized to ns."""
    row = {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1700000000000000}  # us
    fill = parse_fill_row(row, 0)
    assert fill.timestamp_ns == 1700000000000000 * 1_000


def test_parse_fill_nanosecond_timestamp():
    """Fill with ns timestamp stays as ns."""
    row = {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1700000000000000000}  # ns
    fill = parse_fill_row(row, 0)
    assert fill.timestamp_ns == 1700000000000000000


# =======================================================================
# Liquidation detection
# =======================================================================


def test_liquidation_true(jsonl_fills):
    """Fill with liquidation=True is detected."""
    fills = load_fills_to_list(jsonl_fills)
    assert fills[0].liquidation


def test_liquidation_false(jsonl_fills):
    """Fill without liquidation field defaults to False."""
    fills = load_fills_to_list(jsonl_fills)
    assert not fills[2].liquidation


def test_liquidation_via_fill_type():
    """Liquidation detected from fillType field."""
    row = {"coin": "BTC", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1000, "fillType": "liquidation"}
    fill = parse_fill_row(row, 0)
    assert fill.liquidation


# =======================================================================
# Symbol normalization
# =======================================================================


def test_symbol_uppercase():
    """Coin symbol is uppercased."""
    row = {"coin": "btc", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1000}
    fill = parse_fill_row(row, 0)
    assert fill.symbol == "BTC"


def test_symbol_slash_removed():
    """Coin with slash is normalized."""
    row = {"coin": "BTC/USD", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b",
           "time": 1000}
    fill = parse_fill_row(row, 0)
    assert fill.symbol == "BTC"