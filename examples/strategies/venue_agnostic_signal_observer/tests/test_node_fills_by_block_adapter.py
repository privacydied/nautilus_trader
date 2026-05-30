"""Tests for the node_fills_by_block adapter.

Covers fill parsing, schema validation, streaming, delta computation,
filtering, pair reconstruction, and the new real-archive pair/event schema.
"""

from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal

import pytest

from examples.strategies.venue_agnostic_signal_observer.adapters.node_fills_by_block_adapter import (
    FillRecord,
    NodeFillsSchemaError,
    NodeFillsSideError,
    NodeFillRecord,
    PairedFill,
    PairDiagnostics,
    SmokeReport,
    parse_fill_row,
    stream_fills_from_file,
    load_fills_to_list,
    validate_fill_row,
    filter_fills_by_vault,
    compute_signed_delta,
    check_node_fills_source,
    # New functions
    parse_block,
    parse_node_fill_event,
    signed_delta_for_side,
    compute_address_signed_delta,
    total_signed_delta_for_address,
    compute_pair_diagnostics,
    normalize_coin,
    is_frozen_coin,
    has_xyz_prefix,
    stream_records_from_blocks,
    stream_fills_from_jsonl,
    build_smoke_report,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def jsonl_fills():
    """Create a JSONL file with valid legacy fill records."""
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


@pytest.fixture
def real_archive_block():
    """A single block in real node_fills_by_block archive format."""
    return {
        "local_time": "2026-05-24T00:00:00.043500051",
        "block_time": "2026-05-23T23:59:59.905363201",
        "block_number": 1008112033,
        "events": [
            [
                "0x77001f3760e212769cb102dd82477e1b07f84216",
                {
                    "coin": "xyz:SILVER", "px": "77.632", "sz": "9.22",
                    "side": "B", "time": 1779580799905,
                    "startPosition": "-9479.48", "dir": "Close Short",
                    "closedPnl": "-8.192892",
                    "hash": "0x9e36451deb44f09b9fb0043c1691a10204c7000386480f6d41fef070aa48ca86",
                    "oid": 439553278953, "crossed": False, "fee": "-0.001431",
                    "tid": 756173076119856,
                    "cloid": "0x0000000000000000e425c08088ffbe8b",
                    "feeToken": "USDC", "twapId": None, "deployerFee": "-0.000715",
                },
            ],
            [
                "0x456ba8160cdd04ad1d85402cb682619ced374d92",
                {
                    "coin": "xyz:SILVER", "px": "77.632", "sz": "9.22",
                    "side": "A", "time": 1779580799905,
                    "startPosition": "0.0", "dir": "Open Short",
                    "closedPnl": "0.0",
                    "hash": "0x9e36451deb44f09b9fb0043c1691a10204c7000386480f6d41fef070aa48ca86",
                    "oid": 439553310151, "crossed": True, "fee": "0.670243",
                    "builderFee": "0.608401",
                    "tid": 756173076119856,
                    "feeToken": "USDC",
                    "builder": "0xb84168cf3be63c6b8dad05ff5d755e97432ff80b",
                    "twapId": None, "deployerFee": "0.027829",
                },
            ],
        ],
    }


@pytest.fixture
def real_archive_block_no_events():
    """A block with no events."""
    return {
        "local_time": "2026-05-24T00:00:01.000000000",
        "block_time": "2026-05-23T23:59:59.905363201",
        "block_number": 1008112034,
        "events": [],
    }


@pytest.fixture
def real_archive_jsonl(real_archive_block, real_archive_block_no_events):
    """A JSONL string with two blocks."""
    lines = [
        json.dumps(real_archive_block),
        json.dumps(real_archive_block_no_events),
    ]
    return "\n".join(lines)


# ===========================================================================
# NodeFillRecord: real archive pair/event schema
# ===========================================================================


def test_parse_node_fill_event(real_archive_block):
    """Parse a single [address, fill_detail] event."""
    address, detail = real_archive_block["events"][0]
    record = parse_node_fill_event(
        address=address,
        detail=detail,
        block_number=1008112033,
        block_time=None,
        local_time=None,
    )
    assert record.address == "0x77001f3760e212769cb102dd82477e1b07f84216"
    assert record.coin == "xyz:SILVER"
    assert record.px == Decimal("77.632")
    assert record.sz == Decimal("9.22")
    assert record.side == "B"
    assert record.dir == "Close Short"
    assert record.oid == 439553278953
    assert record.tid == 756173076119856


def test_parse_node_fill_event_side_a(real_archive_block):
    """Parse a side-A event."""
    address, detail = real_archive_block["events"][1]
    record = parse_node_fill_event(
        address=address, detail=detail,
        block_number=1008112033, block_time=None, local_time=None,
    )
    assert record.address == "0x456ba8160cdd04ad1d85402cb682619ced374d92"
    assert record.coin == "xyz:SILVER"
    assert record.sz == Decimal("9.22")
    assert record.side == "A"
    assert record.dir == "Open Short"
    assert record.crossed is True
    assert record.builder == "0xb84168cf3be63c6b8dad05ff5d755e97432ff80b"


def test_parse_block(real_archive_block):
    """Parse a full block into NodeFillRecords."""
    records = parse_block(real_archive_block)
    assert len(records) == 2
    assert records[0].address == "0x77001f3760e212769cb102dd82477e1b07f84216"
    assert records[0].side == "B"
    assert records[1].address == "0x456ba8160cdd04ad1d85402cb682619ced374d92"
    assert records[1].side == "A"
    assert records[0].tid == records[1].tid  # same trade


def test_parse_block_no_events(real_archive_block_no_events):
    """Block with no events returns empty list."""
    records = parse_block(real_archive_block_no_events)
    assert records == []


def test_stream_records_from_blocks(real_archive_block, real_archive_block_no_events):
    """Stream records from multiple blocks."""
    records = list(stream_records_from_blocks([
        real_archive_block_no_events,
        real_archive_block,
    ]))
    assert len(records) == 2
    assert records[0].block_number == 1008112033  # ordered by block_number
    assert records[1].address == "0x456ba8160cdd04ad1d85402cb682619ced374d92"


# ===========================================================================
# Decimal precision
# ===========================================================================


def test_node_fill_decimal_precision(real_archive_block):
    """Size and price use Decimal, not float."""
    records = parse_block(real_archive_block)
    assert isinstance(records[0].px, Decimal)
    assert isinstance(records[0].sz, Decimal)
    assert records[0].px == Decimal("77.632")
    assert records[0].sz == Decimal("9.22")


# ===========================================================================
# Side-to-signed-delta mapping
# ===========================================================================


def test_signed_delta_side_b_positive():
    """Side B produces positive delta."""
    delta = signed_delta_for_side("B", Decimal("10.5"))
    assert delta == Decimal("10.5")


def test_signed_delta_side_a_negative():
    """Side A produces negative delta."""
    delta = signed_delta_for_side("A", Decimal("10.5"))
    assert delta == Decimal("-10.5")


def test_signed_delta_side_abs_size():
    """Sign is applied to abs(sz)."""
    delta = signed_delta_for_side("B", Decimal("-10.5"))
    assert delta == Decimal("10.5")


def test_signed_delta_unknown_side():
    """Unknown side raises NodeFillsSideError."""
    with pytest.raises(NodeFillsSideError):
        signed_delta_for_side("C", Decimal("1.0"))


def test_signed_delta_empty_side():
    """Empty side raises NodeFillsSideError."""
    with pytest.raises(NodeFillsSideError):
        signed_delta_for_side("", Decimal("1.0"))


# ===========================================================================
# Per-address inventory delta (NodeFillRecord schema)
# ===========================================================================


def test_address_signed_delta_matches(real_archive_block):
    """compute_address_signed_delta for matching address."""
    records = parse_block(real_archive_block)
    # Side B -> positive delta
    delta_b = compute_address_signed_delta(
        records[0], "0x77001f3760e212769cb102dd82477e1b07f84216"
    )
    assert delta_b == Decimal("9.22")
    # Side A -> negative delta
    delta_a = compute_address_signed_delta(
        records[1], "0x456ba8160cdd04ad1d85402cb682619ced374d92"
    )
    assert delta_a == Decimal("-9.22")


def test_address_signed_delta_not_matches(real_archive_block):
    """compute_address_signed_delta for non-matching address."""
    records = parse_block(real_archive_block)
    delta = compute_address_signed_delta(records[0], "0xunknown")
    assert delta == Decimal("0")


def test_total_signed_delta_for_address(real_archive_block):
    """Sum signed deltas for a specific address."""
    records = parse_block(real_archive_block)
    total = total_signed_delta_for_address(records, "0x456ba8160cdd04ad1d85402cb682619ced374d92")
    assert total == Decimal("-9.22")


# ===========================================================================
# Pair reconstruction diagnostic
# ===========================================================================


def test_compute_pair_diagnostics(real_archive_block):
    """Two events with same trade keys -> pairable."""
    records = parse_block(real_archive_block)
    diag = compute_pair_diagnostics(records)
    assert diag.total_fills == 2
    assert diag.pairable_fills == 2
    assert diag.unpaired_fills == 0
    assert diag.paired_fraction == 1.0
    assert diag.side_consistency_failures == 0


def test_compute_pair_diagnostics_unpaired():
    """Single event -> unpaired."""
    records = [
        NodeFillRecord(
            address="0xA", block_number=1, block_time=None,
            local_time=None, fill_time=None, coin="BTC",
            px=Decimal("50000"), sz=Decimal("1.0"),
            side="B", dir=None, oid=1, tid=100, hash="0x1",
            start_position=None, closed_pnl=None,
            fee=None, crossed=None, builder_fee=None,
            deployer_fee=None, fee_token=None, builder=None,
            cloid=None, twap_id=None, priority_gas=None,
        ),
    ]
    diag = compute_pair_diagnostics(records)
    assert diag.pairable_fills == 0
    assert diag.unpaired_fills == 1
    assert diag.paired_fraction == 0.0
    assert diag.unpaired_side_b == 1


def test_compute_pair_diagnostics_side_inconsistency():
    """Two events with same side -> consistency failure."""
    records = [
        NodeFillRecord(
            address="0xA", block_number=1, block_time=None,
            local_time=None, fill_time=None, coin="BTC",
            px=Decimal("50000"), sz=Decimal("1.0"),
            side="B", dir=None, oid=1, tid=100, hash="0x1",
            start_position=None, closed_pnl=None,
            fee=None, crossed=None, builder_fee=None,
            deployer_fee=None, fee_token=None, builder=None,
            cloid=None, twap_id=None, priority_gas=None,
        ),
        NodeFillRecord(
            address="0xB", block_number=1, block_time=None,
            local_time=None, fill_time=None, coin="BTC",
            px=Decimal("50000"), sz=Decimal("1.0"),
            side="B", dir=None, oid=1, tid=100, hash="0x1",
            start_position=None, closed_pnl=None,
            fee=None, crossed=None, builder_fee=None,
            deployer_fee=None, fee_token=None, builder=None,
            cloid=None, twap_id=None, priority_gas=None,
        ),
    ]
    diag = compute_pair_diagnostics(records)
    assert diag.pairable_fills == 2
    assert diag.side_consistency_failures == 1


def test_compute_pair_diagnostics_empty():
    """Empty list returns zeros."""
    diag = compute_pair_diagnostics([])
    assert diag.total_fills == 0
    assert diag.paired_fraction == 0.0


# ===========================================================================
# Coin normalization (xyz: prefix)
# ===========================================================================


def test_normalize_coin_xyz():
    """xyz: prefix is stripped."""
    assert normalize_coin("xyz:SILVER") == "SILVER"
    assert normalize_coin("xyz:BTC") == "BTC"


def test_normalize_coin_no_prefix():
    """No prefix -> unchanged."""
    assert normalize_coin("BTC") == "BTC"
    assert normalize_coin("ETH") == "ETH"


def test_normalize_coin_case():
    """Coin is uppercased."""
    assert normalize_coin("xyz:silver") == "SILVER"
    assert normalize_coin("btc") == "BTC"


def test_is_frozen_coin():
    """Frozen symbols recognized with and without prefix."""
    assert is_frozen_coin("BTC")
    assert is_frozen_coin("xyz:BTC")
    assert is_frozen_coin("ETH")
    assert not is_frozen_coin("SILVER")
    assert not is_frozen_coin("xyz:SILVER")


def test_has_xyz_prefix():
    """Detect xyz: prefix."""
    assert has_xyz_prefix("xyz:BTC")
    assert has_xyz_prefix("xyz:SILVER")
    assert not has_xyz_prefix("BTC")
    assert not has_xyz_prefix("ETH")


# ===========================================================================
# Stream fills from JSONL (real schema)
# ===========================================================================


def test_stream_fills_from_jsonl(real_archive_jsonl):
    """Stream NodeFillRecords from real-schema JSONL."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(real_archive_jsonl)
        fpath = f.name

    try:
        records = list(stream_fills_from_jsonl(fpath))
        assert len(records) == 2
        assert isinstance(records[0], NodeFillRecord)
        assert records[0].coin == "xyz:SILVER"
        assert records[1].side == "A"
    finally:
        os.unlink(fpath)


# ===========================================================================
# Build smoke report
# ===========================================================================


def test_build_smoke_report(real_archive_block):
    """Smoke report correctly summarizes parsed records."""
    records = parse_block(real_archive_block)
    report = build_smoke_report(
        records=records,
        date="20260524",
        hours=[0],
        bytes_downloaded=10000,
        parse_seconds=0.05,
    )
    assert report.total_rows == 2
    assert report.unique_addresses == 2
    assert "xyz:SILVER" in report.unique_coins_raw
    assert report.frozen_coins_present == []
    assert "xyz:SILVER" in report.non_frozen_coins  # xyz:SILVER is NOT frozen
    assert report.pairable_fraction == 1.0
    assert report.side_consistency_failures == 0
    assert not report.address_resolution_attempted
    assert not report.backstop_attribution_possible


def test_smoke_report_with_frozen_coin():
    """Frozen coins appear in frozen_coins_present."""
    mock = NodeFillRecord(
        address="0xA", block_number=1, block_time=None,
        local_time=None, fill_time=None, coin="xyz:BTC",
        px=Decimal("50000"), sz=Decimal("1.0"),
        side="B", dir=None, oid=1, tid=100, hash="0x1",
        start_position=None, closed_pnl=None,
        fee=None, crossed=None, builder_fee=None,
        deployer_fee=None, fee_token=None, builder=None,
        cloid=None, twap_id=None, priority_gas=None,
    )
    report = build_smoke_report(
        records=[mock],
        date="20260524",
        hours=[0],
        bytes_downloaded=1000,
        parse_seconds=0.01,
    )
    assert "xyz:BTC" in report.unique_coins_raw
    assert "xyz:BTC" in report.frozen_coins_present


# ===========================================================================
# Legacy tests preserved (backward compatibility)
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


def test_signed_delta_vault_is_buyer(jsonl_fills):
    """Vault as buyer -> positive delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[0], "0xhlp")
    assert delta == 1.0


def test_signed_delta_vault_is_seller(jsonl_fills):
    """Vault as seller -> negative delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[1], "0xhlp")
    assert delta == -10.0


def test_signed_delta_vault_not_involved(jsonl_fills):
    """Vault not in fill -> zero delta."""
    fills = load_fills_to_list(jsonl_fills)
    delta = compute_signed_delta(fills[0], "0xunknown")
    assert delta == 0.0


def test_filter_fills_by_vault(jsonl_fills):
    """Filter fills where vault is buyer or seller."""
    fills = load_fills_to_list(jsonl_fills)
    vault_fills = list(filter_fills_by_vault(fills, {"0xhlp"}))
    assert len(vault_fills) == 3


def test_filter_fills_by_vault_other_address(jsonl_fills):
    """Filter for address not in fills returns empty."""
    fills = load_fills_to_list(jsonl_fills)
    vault_fills = list(filter_fills_by_vault(fills, {"0xnonexistent"}))
    assert len(vault_fills) == 0


def test_validate_missing_required_field():
    """Missing required field raises NodeFillsSchemaError."""
    row = {"coin": "BTC", "sz": 1.0}
    with pytest.raises(NodeFillsSchemaError):
        validate_fill_row(row, 0)


def test_check_node_fills_source_found(jsonl_fills):
    """Source check finds available data."""
    import shutil
    data_root = os.path.dirname(jsonl_fills)
    dst = os.path.join(data_root, "node_fills_by_block.jsonl")
    shutil.copy(jsonl_fills, dst)
    available, path, schema, count = check_node_fills_source(data_root)
    assert available


def test_check_node_fills_source_not_found():
    """Source check returns unavailable for missing path."""
    available, path, schema, count = check_node_fills_source("/nonexistent")
    assert not available


def test_no_user_fills_by_time():
    """Adapter does not contain userFillsByTime."""
    import examples.strategies.venue_agnostic_signal_observer.adapters.node_fills_by_block_adapter as mod
    content = open(mod.__file__).read()
    assert "userFillsByTime" not in content


def test_liquidation_true(jsonl_fills):
    """Fill with liquidation=True is detected."""
    fills = load_fills_to_list(jsonl_fills)
    assert fills[0].liquidation


def test_liquidation_false(jsonl_fills):
    """Fill without liquidation field defaults to False."""
    fills = load_fills_to_list(jsonl_fills)
    assert not fills[2].liquidation


def test_symbol_uppercase():
    """Coin symbol is uppercased."""
    row = {"coin": "btc", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b", "time": 1000}
    fill = parse_fill_row(row, 0)
    assert fill.symbol == "BTC"


def test_symbol_slash_removed():
    """Coin with slash is normalized."""
    row = {"coin": "BTC/USD", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b", "time": 1000}
    fill = parse_fill_row(row, 0)
    assert fill.symbol == "BTC"


def test_symbol_xyz_prefix_stripped_legacy():
    """Legacy fill row with xyz: prefix is normalized."""
    row = {"coin": "xyz:SILVER", "sz": 1.0, "px": 50000.0, "buyer": "a", "seller": "b", "time": 1000}
    fill = parse_fill_row(row, 0)
    assert fill.symbol == "SILVER"
