"""
Tests for capture JSONL to CSV converter.

Tiny synthetic JSONL fixtures only. No network calls. No private keys.
Tests that the converter produces parser-compatible CSV output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prep_capture_data import CONVERTER_VERSION
from scripts.prep_capture_data import _resolve_symbol
from scripts.prep_capture_data import _to_source_kind
from scripts.prep_capture_data import _to_venue
from scripts.prep_capture_data import _write_binance_csv
from scripts.prep_capture_data import _write_coinbase_csv
from scripts.prep_capture_data import _write_kraken_csv
from scripts.prep_capture_data import convert_capture


# =====================================================================
# Fixture helpers
# =====================================================================


def _make_row(
    ts_event: int = 1779029669408000000,
    venue: str = "binance_perp",
    symbol: str = "BTC/USDT",
    price: float = 78000.0,
    size: float = 0.1,
    side: str = "sell",
    trade_id: str = "1",
) -> dict:
    return {
        "ts_event": ts_event,
        "venue": venue,
        "symbol": symbol,
        "price": price,
        "size": size,
        "side": side,
        "trade_id": trade_id,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        f.writelines(json.dumps(row) + "\n" for row in rows)


def _make_capture_dir(
    tmp_path: Path,
    *,
    binance_rows: list[dict] | None = None,
    kraken_rows: list[dict] | None = None,
    coinbase_rows: list[dict] | None = None,
) -> Path:
    cap_dir = tmp_path / "capture"
    cap_dir.mkdir(parents=True)

    if binance_rows:
        _write_jsonl(cap_dir / "trades_binance_perp_BTC-USDT_otherstuff.jsonl", binance_rows)
    if kraken_rows:
        _write_jsonl(cap_dir / "trades_kraken_BTC-USD_otherstuff.jsonl", kraken_rows)
    if coinbase_rows:
        _write_jsonl(cap_dir / "trades_coinbase_BTC-USD_otherstuff.jsonl", coinbase_rows)

    return cap_dir


# =====================================================================
# Tests
# =====================================================================


class TestConverterVenueMapping:
    def test_to_venue(self) -> None:
        assert _to_venue("binance_perp") == "binance_perp"
        assert _to_venue("binance") == "binance_perp"
        assert _to_venue("kraken") == "kraken"
        assert _to_venue("coinbase") == "coinbase"

    def test_to_source_kind(self) -> None:
        assert _to_source_kind("binance_perp") == "binance_um_agg_trades"
        assert _to_source_kind("kraken") == "kraken_trades"
        assert _to_source_kind("coinbase") == "coinbase_trades"

    def test_resolve_symbol(self) -> None:
        sym, base, quote = _resolve_symbol("binance_perp", "BTC/USDT")
        assert sym == "BTC/USDT"
        assert base == "BTC"
        assert quote == "USDT"

        sym, base, quote = _resolve_symbol("kraken", "BTC/USD")
        assert sym == "BTC/USD"
        assert base == "BTC"
        assert quote == "USD"


class TestBinanceConverter:
    """Test 1-2: Binance conversion produces nonzero rows."""

    def test_binance_converts_to_nonzero_rows(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=1779029669408000000, trade_id="1", side="sell"),
            _make_row(ts_event=1779029669546000000, trade_id="2", side="buy"),
            _make_row(ts_event=1779029670000000000, trade_id="3", side="sell"),
        ]
        csv_path = tmp_path / "binance_test.csv"
        written = _write_binance_csv(rows, csv_path, "binance_perp")
        assert written == 3

        content = csv_path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 4  # header + 3 data rows
        assert lines[0] == "agg_trade_id,price,qty,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
        # Check first data row
        data = lines[1].split(",")
        assert data[0] == "1"  # trade_id
        assert float(data[1]) == 78000.0  # price
        assert float(data[2]) == 0.1  # qty/size

    def test_binance_timestamp_in_ms(self, tmp_path: Path) -> None:
        """Binance transact_time must be in milliseconds."""
        rows = [_make_row(ts_event=1779029669408000000)]
        csv_path = tmp_path / "btc.csv"
        _write_binance_csv(rows, csv_path, "binance_perp")
        content = csv_path.read_text()
        data_line = content.strip().split("\n")[1]
        transact_time = data_line.split(",")[5]
        assert transact_time == "1779029669408"  # ns // 1_000_000 = ms

    def test_binance_is_buyer_maker(self, tmp_path: Path) -> None:
        """Sell -> is_buyer_maker=true, buy -> false."""
        rows = [
            _make_row(trade_id="1", side="sell"),
            _make_row(trade_id="2", side="buy"),
        ]
        csv_path = tmp_path / "side_test.csv"
        _write_binance_csv(rows, csv_path, "binance_perp")
        lines = csv_path.read_text().strip().split("\n")
        maker1 = lines[1].split(",")[6]
        maker2 = lines[2].split(",")[6]
        assert maker1 == "true"  # sell -> maker
        assert maker2 == "false"  # buy -> taker

    def test_binance_ordering_is_deterministic(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=300, trade_id="c"),
            _make_row(ts_event=100, trade_id="a"),
            _make_row(ts_event=200, trade_id="b"),
        ]
        csv_path = tmp_path / "order.csv"
        _write_binance_csv(rows, csv_path, "binance_perp")
        lines = csv_path.read_text().strip().split("\n")
        # Should be sorted by ts_event: a (100), b (200), c (300)
        ids = [line.split(",")[0] for line in lines[1:]]
        assert ids == ["a", "b", "c"]

        # Second call produces identical output
        csv_path2 = tmp_path / "order2.csv"
        _write_binance_csv(rows, csv_path2, "binance_perp")
        assert csv_path.read_bytes() == csv_path2.read_bytes()


class TestKrakenConverter:
    """Test 3: Kraken conversion."""

    def test_kraken_converts(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=1779029673404099072, venue="kraken", symbol="BTC/USD", size=0.001),
            _make_row(ts_event=1779029673696245000, venue="kraken", symbol="BTC/USD", size=0.002),
        ]
        csv_path = tmp_path / "kraken.csv"
        written = _write_kraken_csv(rows, csv_path)
        assert written == 2
        content = csv_path.read_text().strip().split("\n")
        assert len(content) == 2
        # Timestamp in seconds
        ts1 = float(content[0].split(",")[0])
        assert ts1 == pytest.approx(1779029673.404099, rel=1e-6)

    def test_kraken_deterministic(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=200, venue="kraken", symbol="BTC/USD"),
            _make_row(ts_event=100, venue="kraken", symbol="BTC/USD"),
        ]
        csv_path = tmp_path / "k1.csv"
        csv_path2 = tmp_path / "k2.csv"
        _write_kraken_csv(rows, csv_path)
        _write_kraken_csv(rows, csv_path2)
        assert csv_path.read_bytes() == csv_path2.read_bytes()


class TestCoinbaseConverter:
    """Test 4: Coinbase conversion."""

    def test_coinbase_converts(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=1779029669182158848, venue="coinbase", symbol="BTC/USD", side="sell"),
            _make_row(ts_event=1779029669335359000, venue="coinbase", symbol="BTC/USD", side="buy"),
        ]
        csv_path = tmp_path / "coinbase.csv"
        written = _write_coinbase_csv(rows, csv_path)
        assert written == 2
        content = csv_path.read_text().strip().split("\n")
        assert len(content) == 2
        side1 = content[0].split(",")[3]
        side2 = content[1].split(",")[3]
        assert side1 == "sell"
        assert side2 == "buy"

    def test_coinbase_no_side_optional(self, tmp_path: Path) -> None:
        rows = [
            {"ts_event": 100, "venue": "coinbase", "symbol": "BTC/USD", "price": 100.0, "size": 1.0},
        ]
        csv_path = tmp_path / "cb.csv"
        written = _write_coinbase_csv(rows, csv_path)
        assert written == 1
        content = csv_path.read_text().strip().split("\n")[0]
        parts = content.split(",")
        assert len(parts) == 4
        assert parts[3] == ""  # empty side

    def test_coinbase_deterministic(self, tmp_path: Path) -> None:
        rows = [
            _make_row(ts_event=200, venue="coinbase", symbol="BTC/USD"),
            _make_row(ts_event=100, venue="coinbase", symbol="BTC/USD"),
        ]
        csv_path = tmp_path / "c1.csv"
        csv_path2 = tmp_path / "c2.csv"
        _write_coinbase_csv(rows, csv_path)
        _write_coinbase_csv(rows, csv_path2)
        assert csv_path.read_bytes() == csv_path2.read_bytes()


class TestConversionManifest:
    """Test 10-12: Manifest contains per-file and per-venue counts."""

    def test_manifest_recorded(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(
            tmp_path,
            binance_rows=[_make_row(trade_id=str(i)) for i in range(5)],
            kraken_rows=[_make_row(ts_event=100 + i, venue="kraken", symbol="BTC/USD",
                                    trade_id=str(i)) for i in range(3)],
        )
        out_root = tmp_path / "out"
        manifest = convert_capture(cap_dir, out_root, overwrite=True)

        assert manifest["status"] == "CAPTURE_CONVERSION_READY"
        assert len(manifest["per_file"]) == 2
        assert manifest["total_input_rows"] == 8
        assert manifest["total_output_rows"] == 8
        assert manifest["per_venue"]["binance_perp"] == 5
        assert manifest["per_venue"]["kraken"] == 3
        assert manifest["per_symbol"]["BTC/USDT"] == 5
        assert manifest["per_symbol"]["BTC/USD"] == 3

    def test_manifest_integrity(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(
            tmp_path,
            binance_rows=[_make_row()],
        )
        out_root = tmp_path / "out2"
        manifest = convert_capture(cap_dir, out_root, overwrite=True)
        assert manifest["converter_version"] == CONVERTER_VERSION
        assert manifest["safety"] == "public_data_observer_only"
        assert manifest["input_root"] is not None
        assert manifest["output_root"] is not None

    def test_manifest_per_file_status(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(
            tmp_path,
            binance_rows=[_make_row()],
            kraken_rows=[_make_row(ts_event=100, venue="kraken", symbol="BTC/USD")],
            coinbase_rows=[_make_row(ts_event=200, venue="coinbase", symbol="BTC/USD")],
        )
        out_root = tmp_path / "out3"
        manifest = convert_capture(cap_dir, out_root, overwrite=True)
        for entry in manifest["per_file"]:
            assert entry["status"] == "OK"
            assert entry["output_row_count"] > 0
            assert entry["input_path"].endswith(".jsonl")
            assert entry["output_path"].endswith(".csv")


class TestZeroOutputFailure:
    """Test 13: Non-empty input + zero converted rows fails."""

    def test_zero_output_without_allow_empty_fails(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(
            tmp_path,
            kraken_rows=[_make_row(ts_event=100, venue="kraken", symbol="BTC/USD")],
            # No binance rows -> will also fail from missing required source
        )
        out_root = tmp_path / "zero_out"
        with pytest.raises(SystemExit):
            convert_capture(cap_dir, out_root)


class TestUnknownShape:
    """Test 14: Unknown venue/row shape fails loudly."""

    def test_unknown_venue_fails(self, tmp_path: Path) -> None:
        cap_dir = tmp_path / "cap"
        cap_dir.mkdir()
        _write_jsonl(cap_dir / "trades_unknownvenue_BTC-USD_stuff.jsonl", [
            {"ts_event": 100, "venue": "unknownvenue", "symbol": "BTC/USD", "price": 100.0},
        ])
        out_root = tmp_path / "unknown_out"
        with pytest.raises(SystemExit):
            convert_capture(cap_dir, out_root, overwrite=True)


class TestMissingFields:
    """Test 15: Missing required fields fail loudly."""

    def test_missing_ts_event_fails(self, tmp_path: Path) -> None:
        cap_dir = tmp_path / "cap_miss"
        cap_dir.mkdir()
        _write_jsonl(cap_dir / "trades_binance_perp_BTC-USDT_stuff.jsonl", [
            {"venue": "binance_perp", "symbol": "BTC/USDT", "price": 100.0},  # no ts_event
        ])
        out_root = tmp_path / "miss_out"
        with pytest.raises(SystemExit):
            convert_capture(cap_dir, out_root, overwrite=True)

    def test_missing_price_fails(self, tmp_path: Path) -> None:
        cap_dir = tmp_path / "cap_price"
        cap_dir.mkdir()
        _write_jsonl(cap_dir / "trades_binance_perp_BTC-USDT_stuff.jsonl", [
            {"ts_event": 100, "venue": "binance_perp", "symbol": "BTC/USDT"},  # no price
        ])
        out_root = tmp_path / "price_out"
        with pytest.raises(SystemExit):
            convert_capture(cap_dir, out_root, overwrite=True)


class TestOverwrite:
    """Test 16: --overwrite behavior."""

    def test_existing_output_fails_without_overwrite(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(tmp_path, binance_rows=[_make_row()])
        out_root = tmp_path / "existing_out"
        out_root.mkdir(parents=True)
        (out_root / "existing.txt").write_text("placeholder")

        with pytest.raises(SystemExit):
            convert_capture(cap_dir, out_root)

    def test_overwrite_succeeds(self, tmp_path: Path) -> None:
        cap_dir = _make_capture_dir(tmp_path, binance_rows=[_make_row()])
        out_root = tmp_path / "overwrite_out"
        out_root.mkdir(parents=True)

        manifest = convert_capture(cap_dir, out_root, overwrite=True)
        assert manifest["status"] == "CAPTURE_CONVERSION_READY"


class TestFullPipelineSmoke:
    """Test 17-18: No network calls, no private keys, Phase 1 smoke test."""

    def test_no_network_imports(self) -> None:
        """Verify converter does not import network-dependent modules."""
        import ast

        script_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "scripts" / "prep_capture_data.py"
        with open(script_path) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if name.startswith("http") or name in ("urllib", "requests", "aiohttp", "websockets"):
                        pytest.fail(f"Network import found: {name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and ("http" in node.module or node.module in ("urllib", "requests")):
                    pytest.fail(f"Network import found: {node.module}")

    def test_no_private_key_or_auth_imports(self) -> None:

        script_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "scripts" / "prep_capture_data.py"
        text = script_path.read_text()
        forbidden = [
            "OrderFactory", "submit_order", "TradingNode", "LiveNode",
            "private_key", "wallet", "signing",
            "CANDIDATE_FOR_LIVE", "TRADE_READY", "EXECUTION_READY",
            "POLYMARKET_PK", "KRAKEN_API_KEY",
        ]
        for term in forbidden:
            assert term not in text, f"Forbidden term found: {term}"

    def test_deterministic_output(self, tmp_path: Path) -> None:
        """Identical captures produce identical conversion manifests."""
        cap_dir = _make_capture_dir(
            tmp_path / "src",
            binance_rows=[_make_row(trade_id=str(i)) for i in range(3)],
        )
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"

        m1 = convert_capture(cap_dir, out_a, overwrite=True)
        m2 = convert_capture(cap_dir, out_b, overwrite=True)

        assert m1["total_output_rows"] == m2["total_output_rows"]
        assert m1["per_venue"] == m2["per_venue"]

        # Compare output CSVs
        csv_a = list(out_a.glob("*.csv"))
        csv_b = list(out_b.glob("*.csv"))
        for csv_a_file, csv_b_file in zip(sorted(csv_a), sorted(csv_b), strict=False):
            assert csv_a_file.read_bytes() == csv_b_file.read_bytes()


class TestPhase1Smoke:
    """Smoke test: converted CSV loads with nonzero rows in Phase 1 parser."""

    def test_binance_csv_parsed_by_phase1(self, tmp_path: Path) -> None:
        """
        Verify that a converted binance CSV is parsed with nonzero rows
        by the existing Phase 1 binance_um_agg_trades parser.
        """
        from examples.strategies.venue_agnostic_signal_observer.offline_historical_sources import (
            parse_binance_agg_trades,
        )

        rows = [
            _make_row(ts_event=1779029669408000000, price=78000.0, size=0.1, side="sell", trade_id="1"),
            _make_row(ts_event=1779029669546000000, price=78001.0, size=0.2, side="buy", trade_id="2"),
        ]
        csv_path = tmp_path / "smoke_test.csv"
        _write_binance_csv(rows, csv_path, "binance_perp")

        result = parse_binance_agg_trades(
            path=csv_path,
            venue="binance_perp",
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            source_kind="binance_um_agg_trades",
            logical_source_id="test",
            timestamp_unit="ms",
            expected_start_ns=1779029660000000000,
            expected_end_ns=1779029680000000000,
            tolerance_ns=86_400_000_000_000,
        )

        assert result.row_count == 2
        assert len(result.trades) == 2
        assert result.trades[0].price == 78000.0
        assert result.trades[0].size == 0.1
        assert result.trades[0].side == "sell"
        assert result.trades[1].price == 78001.0
        assert result.trades[1].size == 0.2
        assert result.trades[1].side == "buy"

    def test_kraken_csv_parsed_by_phase1(self, tmp_path: Path) -> None:
        """Verify that a converted kraken CSV is parsed with nonzero rows."""
        from examples.strategies.venue_agnostic_signal_observer.offline_historical_sources import (
            parse_kraken_trades,
        )

        rows = [
            _make_row(ts_event=1779029673404099072, venue="kraken", symbol="BTC/USD", price=78000.0, size=0.1),
        ]
        csv_path = tmp_path / "kraken_smoke.csv"
        _write_kraken_csv(rows, csv_path)

        result = parse_kraken_trades(
            path=csv_path,
            venue="kraken",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            logical_source_id="test",
            expected_start_ns=1779029660000000000,
            expected_end_ns=1779029680000000000,
        )

        assert result.row_count == 1
        assert result.trades[0].price == 78000.0
        assert result.trades[0].size == 0.1

    def test_coinbase_csv_parsed_by_phase1(self, tmp_path: Path) -> None:
        """Verify that a converted coinbase CSV is parsed with nonzero rows."""
        from examples.strategies.venue_agnostic_signal_observer.offline_historical_sources import (
            parse_coinbase_trades,
        )

        rows = [
            _make_row(ts_event=1779029669182158848, venue="coinbase", symbol="BTC/USD", price=78000.0, size=0.1, side="sell"),
        ]
        csv_path = tmp_path / "coinbase_smoke.csv"
        _write_coinbase_csv(rows, csv_path)

        result = parse_coinbase_trades(
            path=csv_path,
            venue="coinbase",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            logical_source_id="test",
            expected_start_ns=1779029660000000000,
            expected_end_ns=1779029680000000000,
        )

        assert result.row_count == 1
        assert result.trades[0].price == 78000.0
        assert result.trades[0].size == 0.1
        assert result.trades[0].side == "sell"
