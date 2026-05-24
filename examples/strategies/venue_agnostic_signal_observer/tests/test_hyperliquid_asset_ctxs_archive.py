from __future__ import annotations

import ast
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_oi_velocity_compression_phase0 import (
    Phase0Config,
    run_phase0_pipeline,
)
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_asset_ctxs_archive import (
    FROZEN_OI_VELOCITY_SYMBOLS,
    asset_ctxs_s3_key,
    build_asset_ctxs_archive,
    compute_date_list_hash,
    compute_symbol_list_hash,
    parse_asset_ctxs_csv_text,
)


def _fixture_csv() -> str:
    return """time,coin,openInterest,markPx,indexPx,funding
2026-01-01T00:00:00Z,BTC,123.45,10000.5,9999.5,0.0001
2026-01-01T00:00:00Z,ETH,234.56,2000.5,1999.5,0.0002
2026-01-01T00:00:00Z,NOTFROZEN,345.67,3.0,2.9,0.5
"""


def _fixture_csv_without_timestamp() -> str:
    return """coin,openInterest,markPx,indexPx,funding
BTC,123.45,10000.5,9999.5,0.0001
ETH,234.56,2000.5,1999.5,0.0002
NOTFROZEN,345.67,3.0,2.9,0.5
"""


def _minute_inference_csv(symbols: tuple[str, ...] = ("BTC", "ETH"), minutes: int = 1440) -> str:
    lines = ["coin,openInterest,markPx,indexPx,funding"]
    for minute in range(minutes):
        for offset, symbol in enumerate(symbols):
            px = 1000 + minute + offset
            lines.append(f"{symbol},{minute * 10 + offset + 1},{px},{px - 0.5},0.0001")
    return "\n".join(lines) + "\n"


def test_fixture_parser_for_representative_asset_ctxs_csv() -> None:
    parsed = parse_asset_ctxs_csv_text(_fixture_csv(), ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))

    assert parsed.detected_columns["symbol"] == "coin"
    assert parsed.detected_columns["open_interest"] == "openInterest"
    assert parsed.detected_columns["price"] == "markPx"
    assert parsed.detected_columns["index_price"] == "indexPx"
    assert parsed.detected_columns["timestamp"] == "time"
    assert parsed.timestamp_source == "column:time"
    assert parsed.snapshot_count == 1
    assert parsed.malformed_snapshot_reason is None
    assert parsed.funding_history_available is True
    assert [row["symbol"] for row in parsed.rows] == ["BTC", "ETH"]
    assert parsed.rows[0] == {
        "ts_event": "2026-01-01T00:00:00Z",
        "symbol": "BTC",
        "open_interest": 123.45,
        "price": 10000.5,
        "price_source": "mark",
        "index_price": 9999.5,
    }


def test_missing_oi_column_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="ASSET_CTXS_OI_COLUMN_MISSING"):
        parse_asset_ctxs_csv_text("time,coin,markPx\n2026-01-01T00:00:00Z,BTC,100\n", ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC",))


def test_missing_price_column_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="ASSET_CTXS_PRICE_COLUMN_MISSING"):
        parse_asset_ctxs_csv_text("time,coin,openInterest\n2026-01-01T00:00:00Z,BTC,100\n", ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC",))


def test_timestamp_column_parsing_uses_real_intraday_time() -> None:
    csv_text = """time,coin,openInterest,markPx
2026-01-01T00:00:00Z,BTC,1,100
2026-01-01T00:01:00Z,BTC,2,101
"""

    parsed = parse_asset_ctxs_csv_text(csv_text, ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC",))

    assert [row["ts_event"] for row in parsed.rows] == ["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"]
    assert parsed.timestamp_source == "column:time"
    assert parsed.snapshot_count == 2


def test_inferred_minute_timestamp_reconstruction_from_row_order() -> None:
    parsed = parse_asset_ctxs_csv_text(_minute_inference_csv(), ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))

    btc_rows = [row for row in parsed.rows if row["symbol"] == "BTC"]
    eth_rows = [row for row in parsed.rows if row["symbol"] == "ETH"]
    assert parsed.timestamp_source == "inferred_minute_from_row_order"
    assert parsed.snapshot_count == 1440
    assert len({row["ts_event"] for row in btc_rows}) == 1440
    assert btc_rows[0]["ts_event"] == "2026-01-01T00:00:00Z"
    assert btc_rows[-1]["ts_event"] == "2026-01-01T23:59:00Z"
    assert eth_rows[-1]["ts_event"] == "2026-01-01T23:59:00Z"


def test_duplicate_timestamp_regression_one_synthetic_day_per_symbol() -> None:
    parsed = parse_asset_ctxs_csv_text(_minute_inference_csv(), ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))

    for symbol in ("BTC", "ETH"):
        symbol_ts = [row["ts_event"] for row in parsed.rows if row["symbol"] == symbol]
        counts = Counter(symbol_ts)
        duplicate_ts_rows = sum(count - 1 for count in counts.values() if count > 1)
        assert len(symbol_ts) == 1440
        assert duplicate_ts_rows == 0


def test_inferred_minute_timestamps_fail_closed_on_malformed_row_count() -> None:
    malformed = _minute_inference_csv(minutes=1439)

    with pytest.raises(RuntimeError, match="ASSET_CTXS_TIMESTAMP_INFERENCE_ROW_COUNT_INVALID"):
        parse_asset_ctxs_csv_text(malformed, ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))


def test_inferred_minute_timestamps_fail_closed_on_bad_snapshot_structure() -> None:
    lines = ["coin,openInterest,markPx,indexPx"]
    for minute in range(1440):
        symbols = ("BTC", "BTC") if minute == 17 else ("BTC", "ETH")
        for offset, symbol in enumerate(symbols):
            lines.append(f"{symbol},{minute * 10 + offset + 1},{1000 + minute},{999 + minute}")
    malformed = "\n".join(lines) + "\n"

    with pytest.raises(RuntimeError, match="ASSET_CTXS_TIMESTAMP_INFERENCE_SYMBOL_BLOCK_INVALID"):
        parse_asset_ctxs_csv_text(malformed, ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))


def test_funding_columns_are_dropped_and_values_never_enter_rows() -> None:
    parsed = parse_asset_ctxs_csv_text(_fixture_csv(), ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC", "ETH"))

    assert parsed.funding_history_available is True
    assert all("funding" not in key.lower() for row in parsed.rows for key in row)
    assert "0.0001" not in json.dumps(parsed.rows)
    assert "0.0002" not in json.dumps(parsed.rows)


def test_strict_frozen_universe_filtering_and_no_auto_expansion() -> None:
    parsed = parse_asset_ctxs_csv_text(_fixture_csv(), ts_event="2026-01-01T00:00:00Z", frozen_symbols=("BTC",))

    assert [row["symbol"] for row in parsed.rows] == ["BTC"]
    assert "NOTFROZEN" in parsed.dropped_symbols
    assert parsed.dropped_symbols["NOTFROZEN"] == "not_in_frozen_universe"
    assert "ETH" in parsed.dropped_symbols


def test_build_archive_output_schema_is_accepted_by_existing_phase0_runner(tmp_path: Path) -> None:
    def downloader(key: str) -> bytes:
        return _fixture_csv().encode("utf-8")

    result = build_asset_ctxs_archive(
        dates=[date(2026, 1, 1)],
        coins=["BTC", "ETH", "NOTFROZEN"],
        out_dir=tmp_path,
        max_usd_budget=1.0,
        confirm_token="I_HAVE_BUDGET_0.0",
        downloader=downloader,
        probe_size=lambda key: len(_fixture_csv()),
        frozen_symbols=("BTC", "ETH"),
        precommitment_hash="prehash",
    )

    assert (tmp_path / "BTC.jsonl").exists()
    first = json.loads((tmp_path / "BTC.jsonl").read_text().splitlines()[0])
    assert set(first) == {"ts_event", "symbol", "open_interest", "price", "price_source", "index_price"}

    run_result = run_phase0_pipeline(
        tmp_path,
        Phase0Config(frozen_symbols=("BTC", "ETH"), min_usable_symbols=30),
        verify_hash=False,
        write_reports=False,
    )
    assert run_result["summary"]["missing_symbols"] == []
    assert result.manifest_path == str(tmp_path / "manifest.json")


def test_manifest_hash_stability(tmp_path: Path) -> None:
    kwargs = dict(
        dates=[date(2026, 1, 1)],
        coins=["BTC", "ETH", "NOTFROZEN"],
        max_usd_budget=1.0,
        confirm_token="I_HAVE_BUDGET_0.0",
        downloader=lambda key: _fixture_csv().encode("utf-8"),
        probe_size=lambda key: len(_fixture_csv()),
        frozen_symbols=("BTC", "ETH"),
        precommitment_hash="prehash",
    )
    a = build_asset_ctxs_archive(out_dir=tmp_path / "a", **kwargs)
    b = build_asset_ctxs_archive(out_dir=tmp_path / "b", **kwargs)

    assert a.manifest_hash == b.manifest_hash
    manifest = json.loads((tmp_path / "a" / "manifest.json").read_text())
    assert manifest["timestamp_source"] == "column:time"
    assert manifest["per_date_snapshot_counts"] == {"2026-01-01": 1}
    assert manifest["malformed_or_incomplete_days"] == {}
    assert manifest["date_list_hash"] == compute_date_list_hash([date(2026, 1, 1)])
    assert manifest["frozen_symbol_list_hash"] == compute_symbol_list_hash(("BTC", "ETH"))


def test_asset_ctxs_s3_key_uses_asset_ctxs_only() -> None:
    key = asset_ctxs_s3_key(date(2026, 1, 1))
    assert key == "s3://hyperliquid-archive/asset_ctxs/20260101.csv.lz4"
    assert "l2Book" not in key


def test_default_frozen_symbols_match_phase0_universe() -> None:
    assert FROZEN_OI_VELOCITY_SYMBOLS == Phase0Config().frozen_symbols


def test_ast_safety_scan_for_forbidden_imports_and_path_fragments() -> None:
    files = [
        Path("examples/strategies/venue_agnostic_signal_observer/hyperliquid_asset_ctxs_archive.py"),
        Path("examples/strategies/venue_agnostic_signal_observer/run_hyperliquid_asset_ctxs_archive.py"),
    ]
    forbidden_import_fragments = (
        "execution", "live", "order", "bot", "private", "credential", "auth",
        "clob_client", "clobclient", "signing", "wallet", "key",
    )
    forbidden_path_fragments = ("l2Book", "l2book", "hyperliquid_funding_archive_phase0", "funding_dispersion_carry_v1_archives")
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                modules = []
            for module in modules:
                assert not any(fragment in module.lower() for fragment in forbidden_import_fragments), module
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(fragment in node.value for fragment in forbidden_path_fragments), node.value
