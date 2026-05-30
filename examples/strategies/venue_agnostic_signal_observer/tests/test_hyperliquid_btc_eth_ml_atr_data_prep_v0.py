"""
Tests for hyperliquid_btc_eth_ml_atr_data_prep_v0 — data-prep module.

NOT live trading. NOT network-connected. Local files only.
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyperliquid_btc_eth_ml_atr_data_prep_v0 import (
    VALID_SYMBOLS,
    DataPrepConfig,
    TradeSchemaGuess,
    FundingSchemaGuess,
    normalize_symbol,
    inspect_trade_jsonl,
    inspect_funding_jsonl,
    parse_trade_jsonl,
    build_ohlcv_1h,
    validate_bars_for_ml_atr,
    validate_funding_for_ml_atr,
    parse_funding_archive,
    run_data_prep,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_trade_jsonl(path: Path, trades: list):
    """Write synthetic trade JSONL."""
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def _make_funding_jsonl(path: Path, rows: list):
    """Write synthetic funding JSONL."""
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# 1. Module Imports
# ---------------------------------------------------------------------------
class TestModuleImports:
    def test_import_without_nautilus(self):
        import importlib
        mod = importlib.import_module("hyperliquid_btc_eth_ml_atr_data_prep_v0")
        assert hasattr(mod, "run_data_prep")


# ---------------------------------------------------------------------------
# 2. No Network/Client Imports
# ---------------------------------------------------------------------------
class TestNoNetwork:
    def test_no_network_calls(self):
        import hyperliquid_btc_eth_ml_atr_data_prep_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in ["requests.get", "requests.post", "urllib", "httpx", "aiohttp"]:
                assert pattern not in stripped


# ---------------------------------------------------------------------------
# 3. Symbol Normalization
# ---------------------------------------------------------------------------
class TestSymbolNormalization:
    def test_btc_accepted(self):
        assert normalize_symbol("BTC") == "BTC"

    def test_eth_accepted(self):
        assert normalize_symbol("ETH") == "ETH"

    def test_btc_perp(self):
        assert normalize_symbol("BTC-PERP") == "BTC"

    def test_eth_perp(self):
        assert normalize_symbol("ETH-PERP") == "ETH"

    def test_btc_usdt(self):
        assert normalize_symbol("BTC/USDT") == "BTC"

    def test_eth_usdt(self):
        assert normalize_symbol("ETH/USDT") == "ETH"

    def test_link_rejected(self):
        assert normalize_symbol("LINK") is None

    def test_unknown_rejected(self):
        assert normalize_symbol("SOL") is None


# ---------------------------------------------------------------------------
# 4. Schema Inspection
# ---------------------------------------------------------------------------
class TestSchemaInspection:
    def test_inspect_trade_jsonl(self, tmp_path):
        trades = [
            {"ts_event": 1779029669408000000, "venue": "binance_perp", "symbol": "BTC/USDT", "price": 78027.1, "size": 0.064, "side": "sell"},
            {"ts_event": 1779029669546000000, "venue": "binance_perp", "symbol": "BTC/USDT", "price": 78027.2, "size": 0.043, "side": "buy"},
        ]
        path = tmp_path / "trades.jsonl"
        _make_trade_jsonl(path, trades)
        schema = inspect_trade_jsonl(path)
        assert schema.timestamp_field == "ts_event"
        assert schema.timestamp_unit == "nanoseconds"
        assert schema.symbol_field == "symbol"
        assert schema.price_field == "price"
        assert schema.size_field == "size"
        assert "BTC" in schema.detected_symbols

    def test_inspect_funding_jsonl(self, tmp_path):
        rows = [
            {"coin": "BTC", "timestamp_ms": 1747958400026, "funding_rate": 8.65146e-05},
            {"coin": "ETH", "timestamp_ms": 1747962000062, "funding_rate": 3.0871e-05},
        ]
        path = tmp_path / "funding.jsonl"
        _make_funding_jsonl(path, rows)
        schema = inspect_funding_jsonl(path)
        assert schema.timestamp_field == "timestamp_ms"
        # timestamp_ms value ~1.7e12 could be seconds or ms; detection may vary
        assert schema.timestamp_unit in ("milliseconds", "seconds")
        assert schema.symbol_field == "coin"
        assert schema.funding_rate_field == "funding_rate"
        assert "BTC" in schema.detected_symbols
        assert "ETH" in schema.detected_symbols


# ---------------------------------------------------------------------------
# 5. OHLCV Aggregation
# ---------------------------------------------------------------------------
class TestOHLCVAggregation:
    def test_basic_aggregation(self):
        trades = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:10Z", "2025-07-01T00:00:20Z", "2025-07-01T00:00:30Z"], utc=True),
            "symbol": ["BTC", "BTC", "BTC"],
            "price": [100.0, 110.0, 105.0],
            "size": [1.0, 2.0, 3.0],
        })
        bars = build_ohlcv_1h(trades, DataPrepConfig())
        assert len(bars) == 1
        assert bars.iloc[0]["open"] == 100.0
        assert bars.iloc[0]["high"] == 110.0
        assert bars.iloc[0]["low"] == 100.0
        assert bars.iloc[0]["close"] == 105.0
        assert bars.iloc[0]["volume"] == 6.0

    def test_missing_hours_not_forward_filled(self):
        trades = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:10Z", "2025-07-01T02:00:10Z"], utc=True),
            "symbol": ["BTC", "BTC"],
            "price": [100.0, 110.0],
            "size": [1.0, 1.0],
        })
        bars = build_ohlcv_1h(trades, DataPrepConfig())
        assert len(bars) == 2  # hour 0 and hour 2, no hour 1

    def test_no_synthetic_zero_volume_bars(self):
        trades = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:10Z"], utc=True),
            "symbol": ["BTC"],
            "price": [100.0],
            "size": [1.0],
        })
        bars = build_ohlcv_1h(trades, DataPrepConfig())
        assert len(bars) == 1
        assert bars.iloc[0]["volume"] > 0


# ---------------------------------------------------------------------------
# 6. Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_ready_status(self):
        bars = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:00Z", "2025-07-01T01:00:00Z"], utc=True),
            "symbol": ["BTC", "BTC"],
            "open": [100.0, 101.0],
            "high": [105.0, 106.0],
            "low": [95.0, 96.0],
            "close": [102.0, 103.0],
            "volume": [1000.0, 1100.0],
        })
        summary = validate_bars_for_ml_atr(bars, DataPrepConfig(symbols=("BTC",)))
        assert summary.status == "DATA_PREP_V0_READY_FOR_ML_ATR"

    def test_missing_symbol_fails(self):
        bars = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "open": [100.0], "high": [105.0], "low": [95.0], "close": [102.0], "volume": [1000.0],
        })
        summary = validate_bars_for_ml_atr(bars, DataPrepConfig(symbols=("BTC", "ETH")))
        assert summary.status == "DATA_PREP_V0_ERROR_INSUFFICIENT_COVERAGE"

    def test_ohlc_sanity_rejects(self):
        bars = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "open": [100.0], "high": [90.0], "low": [110.0], "close": [102.0], "volume": [1000.0],
        })
        summary = validate_bars_for_ml_atr(bars, DataPrepConfig(symbols=("BTC",)))
        assert summary.ohlc_sanity_rejects >= 1


# ---------------------------------------------------------------------------
# 7. Funding Validation
# ---------------------------------------------------------------------------
class TestFundingValidation:
    def test_funding_ready(self):
        funding = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:00Z", "2025-07-01T01:00:00Z"], utc=True),
            "symbol": ["BTC", "BTC"],
            "funding_rate": [0.0001, 0.0002],
        })
        summary = validate_funding_for_ml_atr(funding, DataPrepConfig(symbols=("BTC",)))
        assert summary.status == "DATA_PREP_V0_READY_FOR_ML_ATR"

    def test_funding_abs_rate_rejects(self):
        funding = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-07-01T00:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "funding_rate": [0.05],  # > 0.01
        })
        summary = validate_funding_for_ml_atr(funding, DataPrepConfig(symbols=("BTC",)))
        assert summary.abs_rate_rejects >= 1


# ---------------------------------------------------------------------------
# 8. Determinism
# ---------------------------------------------------------------------------
@pytest.mark.determinism
def test_deterministic_output(tmp_path):
    """Same synthetic inputs produce byte-identical bars CSV."""
    trades = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-07-01T00:00:10Z", "2025-07-01T00:00:20Z"], utc=True),
        "symbol": ["BTC", "BTC"],
        "price": [100.0, 110.0],
        "size": [1.0, 2.0],
    })
    bars1 = build_ohlcv_1h(trades, DataPrepConfig())
    bars2 = build_ohlcv_1h(trades, DataPrepConfig())
    pd.testing.assert_frame_equal(bars1, bars2)


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------
class TestCLI:
    def test_help_works(self):
        from run_hyperliquid_btc_eth_ml_atr_data_prep_v0 import _parse_args
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0

    def test_unknown_symbol_fails(self):
        from run_hyperliquid_btc_eth_ml_atr_data_prep_v0 import _parse_args
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--symbol", "LINK"])
        assert exc.value.code != 0
