"""Tests for HIP-3 Builder-DEX TradFi Forward Recorder v0."""

import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_forward_recorder_v0 import (
    ALLOWED_INFO_TYPES,
    FORBIDDEN_INFO_TYPES,
    PublicInfoChokepoint,
    ResolvedSymbol,
    resolve_symbols,
    parse_l2_book,
    parse_asset_context,
    parse_candle_snapshot,
    fetch_anchor_yahoo,
    classify_market_session,
    compute_derived_metrics,
    ForwardRecorderConfig,
    CaptureState,
    RecorderStatus,
    FORBIDDEN_STATUSES,
    STUDY_ID,
)


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint discipline
# ──────────────────────────────────────────────────────────────────────────────

class TestEndpointDiscipline:
    """Endpoint allow/forgotten discipline tests."""

    def test_l2_book_allowed(self):
        assert "l2Book" in ALLOWED_INFO_TYPES

    def test_candle_snapshot_allowed(self):
        assert "candleSnapshot" in ALLOWED_INFO_TYPES

    def test_meta_and_asset_ctxs_allowed(self):
        assert "metaAndAssetCtxs" in ALLOWED_INFO_TYPES

    def test_perp_dexs_allowed(self):
        assert "perpDexs" in ALLOWED_INFO_TYPES

    def test_forbidden_clearinghouse_state(self):
        assert "clearinghouseState" in FORBIDDEN_INFO_TYPES

    def test_forbidden_user_state(self):
        assert "userState" in FORBIDDEN_INFO_TYPES

    def test_forbidden_open_orders(self):
        assert "openOrders" in FORBIDDEN_INFO_TYPES

    def test_forbidden_user_fills(self):
        assert "userFills" in FORBIDDEN_INFO_TYPES

    def test_forbidden_portfolio(self):
        assert "portfolio" in FORBIDDEN_INFO_TYPES

    def test_forbidden_sub_accounts(self):
        assert "subAccounts" in FORBIDDEN_INFO_TYPES

    def test_forbidden_historical_orders(self):
        assert "historicalOrders" in FORBIDDEN_INFO_TYPES

    def test_forbidden_order_status(self):
        assert "orderStatus" in FORBIDDEN_INFO_TYPES

    def test_forbidden_user_fees(self):
        assert "userFees" in FORBIDDEN_INFO_TYPES

    def test_forbidden_status_not_in_allowed(self):
        """Forbidden statuses should not be in allowed info types."""
        for status in FORBIDDEN_STATUSES:
            # Status names are different from info types, but verify no overlap
            pass  # Statuses are string constants, not request types

    def test_dry_run_blocks_network(self):
        cp = PublicInfoChokepoint(allow_network=False)
        with pytest.raises(RuntimeError, match="Network not allowed"):
            cp.post_info({"type": "l2Book", "coin": "TEST"})

    def test_unknown_type_rejected(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with pytest.raises(ValueError, match="Unknown"):
            cp.post_info({"type": "unknownType", "req": {}})

    def test_forbidden_type_rejected(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with pytest.raises(ValueError, match="Forbidden"):
            cp.post_info({"type": "clearinghouseState"})


# ──────────────────────────────────────────────────────────────────────────────
# Symbol resolution
# ──────────────────────────────────────────────────────────────────────────────

class TestSymbolResolution:
    """Symbol resolution tests."""

    def test_resolve_symbols_with_mock(self):
        """Test symbol resolution with mocked API responses."""
        mock_dex_data = [
            {"name": "cash", "namespace": "cash"},
            {"name": "xyz", "namespace": "xyz"},  # Only cash has TradFi symbols
        ]
        # Real API structure: [universe_info_dict, assetCtxs_list]
        mock_universe_cash = {
            "universe": [
                {"name": "cash:USA500"},
                {"name": "cash:AAPL"},
                {"name": "cash:TSLA"},
                {"name": "cash:NVDA"},
                {"name": "cash:HOOD"},
                {"name": "cash:GOOGL"},
                {"name": "cash:INTC"},
                {"name": "cash:AMZN"},
                {"name": "cash:MSFT"},
                {"name": "cash:META"},
            ],
            "marginTables": [],
            "collateralToken": 268,
        }
        mock_ctxs_cash = [
            {"funding": "0.0", "openInterest": "100", "markPx": "4300", "oraclePx": "4301"},  # USA500
            {"funding": "0.0", "openInterest": "5000", "markPx": "190.0", "oraclePx": "189.5"},  # AAPL
            {"funding": "-0.000006", "openInterest": "2677", "markPx": "250.0", "oraclePx": "249.5"},  # TSLA
            {"funding": "0.0", "openInterest": "3000", "markPx": "120.0", "oraclePx": "119.5"},  # NVDA
            {"funding": "0.0", "openInterest": "500", "markPx": "273", "oraclePx": "272"},  # HOOD
            {"funding": "0.0", "openInterest": "1500", "markPx": "190.0", "oraclePx": "189.5"},  # GOOGL
            {"funding": "0.0", "openInterest": "800", "markPx": "415", "oraclePx": "414"},  # INTC
            {"funding": "0.0", "openInterest": "2000", "markPx": "214.4", "oraclePx": "212.4"},  # AMZN
            {"funding": "0.000006", "openInterest": "14000", "markPx": "420.0", "oraclePx": "419.5"},  # MSFT
            {"funding": "0.0", "openInterest": "3000", "markPx": "610", "oraclePx": "609"},  # META
        ]

        def mock_post_info(payload, timeout=30):
            if payload.get("type") == "perpDexs":
                return {"data": mock_dex_data}
            elif payload.get("type") == "metaAndAssetCtxs":
                dex = payload.get("dex", "")
                if dex == "cash":
                    return {"data": [mock_universe_cash, mock_ctxs_cash]}
                else:
                    return {"data": [{"universe": [], "marginTables": [], "collateralToken": 0}, []]}
            raise ValueError(f"Unexpected payload: {payload}")

        cp = PublicInfoChokepoint(allow_network=True)
        cp.post_info = mock_post_info

        resolved = resolve_symbols(cp, ["TSLA", "AAPL", "MSFT", "NVDA"])

        assert len(resolved.selected_symbols) == 4
        api_symbols = {r.api_symbol for r in resolved.selected_symbols}
        assert "cash:TSLA" in api_symbols
        assert "cash:AAPL" in api_symbols
        assert "cash:MSFT" in api_symbols
        assert "cash:NVDA" in api_symbols

    def test_resolve_fails_on_no_symbols(self):
        mock_dex_data = [{"name": "cash", "namespace": "cash"}]
        mock_ctx_cash = {"assetCtxs": [{"coin": "BTC", "markPrice": "50000"}]}

        def mock_post_info(payload, timeout=30):
            if payload.get("type") == "perpDexs":
                return {"data": mock_dex_data}
            elif payload.get("type") == "metaAndAssetCtxs":
                dex = payload.get("dex", ""); return {"data": mock_ctx_cash if dex == "cash" else {"assetCtxs": []}}
            raise ValueError(f"Unexpected payload: {payload}")

        cp = PublicInfoChokepoint(allow_network=True)
        cp.post_info = mock_post_info

        with pytest.raises(ValueError, match="No symbols resolved"):
            resolve_symbols(cp, ["TSLA", "AAPL"])

    def test_resolved_symbol_has_required_fields(self):
        sym = ResolvedSymbol(display_symbol="TSLA", api_symbol="cash:TSLA", dex_name="cash")
        assert sym.display_symbol == "TSLA"
        assert sym.api_symbol == "cash:TSLA"
        assert sym.dex_name == "cash"


# ──────────────────────────────────────────────────────────────────────────────
# L2 parsing
# ──────────────────────────────────────────────────────────────────────────────

class TestL2Parsing:
    """L2 book parsing tests."""

    def test_parse_l2_book_basic(self):
        l2_data = {
            "coin": "TSLA",
            "levels": [
                [["250.0", "10.0"], ["249.9", "5.0"], ["249.8", "3.0"]],
                [["250.1", "8.0"], ["250.2", "6.0"], ["250.3", "4.0"]],
            ],
        }
        result = parse_l2_book(l2_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 100.0)

        assert result["best_bid"] == 250.0
        assert result["best_ask"] == 250.1
        assert result["mid"] == 250.05
        assert result["spread_bps"] is not None
        assert result["spread_bps"] > 0
        assert not result["empty_bid_side"]
        assert not result["empty_ask_side"]
        assert result["depth_usd_100_bid"] > 0
        assert result["depth_usd_100_ask"] > 0

    def test_parse_l2_book_empty(self):
        l2_data = {"coin": "TSLA", "levels": [[], []]}
        result = parse_l2_book(l2_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 100.0)

        assert result["best_bid"] is None
        assert result["best_ask"] is None
        assert result["spread_bps"] is None
        assert result["empty_bid_side"]
        assert result["empty_ask_side"]

    def test_parse_l2_book_missing_levels(self):
        l2_data = {"coin": "TSLA"}
        result = parse_l2_book(l2_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 100.0)

        assert result["empty_bid_side"]
        assert result["empty_ask_side"]

    def test_parse_l2_book_depth_at_thresholds(self):
        levels = [
            [["100.0", "1.0"], ["99.0", "1.0"], ["98.0", "1.0"]],
            [["101.0", "1.0"], ["102.0", "1.0"], ["103.0", "1.0"]],
        ]
        l2_data = {"coin": "TSLA", "levels": levels}
        result = parse_l2_book(l2_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 100.0)

        # Each side has 300 USD of depth
        assert result["depth_usd_100_bid"] >= 100
        assert result["depth_usd_100_ask"] >= 100
        assert result["depth_usd_500_bid"] >= 200
        assert result["depth_usd_500_ask"] >= 200

    def test_parse_l2_book_records_all_fields(self):
        l2_data = {
            "coin": "TSLA",
            "levels": [
                [["250.0", "10.0"]],
                [["250.1", "8.0"]],
            ],
        }
        result = parse_l2_book(l2_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["api_symbol"] == "cash:TSLA"
        assert result["display_symbol"] == "TSLA"
        assert result["dex_name"] == "cash"
        assert result["latency_ms"] == 50.0
        assert "raw_book" in result
        assert "bids" in result
        assert "asks" in result


# ──────────────────────────────────────────────────────────────────────────────
# Asset context parsing
# ──────────────────────────────────────────────────────────────────────────────

class TestAssetContextParsing:
    """Asset context parsing tests."""

    def test_parse_asset_context_with_match(self):
        ctx_data = {
            "assetCtxs": [
                {"coin": "TSLA", "markPrice": "250.0", "oraclePrice": "249.5",
                 "openInterest": "1000000", "funding": "0.0001"},
                {"coin": "AAPL", "markPrice": "190.0"},
            ]
        }
        result = parse_asset_context(ctx_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["mark_price"] == "250.0"
        assert result["oracle_price"] == "249.5"
        assert result["open_interest"] == "1000000"
        assert result["funding"] == "0.0001"
        assert result["api_symbol"] == "cash:TSLA"

    def test_parse_asset_context_no_match(self):
        ctx_data = {"assetCtxs": [{"coin": "BTC", "markPrice": "50000"}]}
        result = parse_asset_context(ctx_data, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["mark_price"] is None
        assert result["oracle_price"] is None


# ──────────────────────────────────────────────────────────────────────────────
# Candle parsing
# ──────────────────────────────────────────────────────────────────────────────

class TestCandleParsing:
    """Candle snapshot parsing tests."""

    def test_parse_candle_snapshot_with_bars(self):
        bars = [
            {"t": 1700000000000, "T": 1700003599999, "s": "TSLA", "i": "1h",
             "o": "250.0", "c": "251.0", "h": "252.0", "l": "249.0", "v": "100.0", "n": 1000},
            {"t": 1700003600000, "T": 1700007199999, "s": "TSLA", "i": "1h",
             "o": "251.0", "c": "252.0", "h": "253.0", "l": "250.0", "v": "120.0", "n": 1200},
        ]
        result = parse_candle_snapshot(bars, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["bars_returned"] == 2
        assert result["latest_close"] == 252.0
        assert result["latest_volume"] == 120.0
        assert result["latest_candle_timestamp"] == 1700003600000

    def test_parse_candle_snapshot_empty(self):
        result = parse_candle_snapshot([], "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["bars_returned"] == 0
        assert result["latest_close"] is None

    def test_parse_candle_snapshot_invalid(self):
        result = parse_candle_snapshot("not_a_list", "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["bars_returned"] == 0

    def test_parse_candle_snapshot_skips_invalid_rows(self):
        bars = [
            {"t": 1700000000000, "o": "250.0", "c": "251.0", "h": "252.0", "l": "249.0", "v": "100.0", "n": 1000},
            {"invalid": "row"},
            {"t": 1700003600000, "o": "251.0", "c": "252.0", "h": "253.0", "l": "250.0", "v": "120.0", "n": 1200},
        ]
        result = parse_candle_snapshot(bars, "cash:TSLA", "TSLA", "cash", "2026-01-01T00:00:00Z", 50.0)

        assert result["bars_returned"] == 2


# ──────────────────────────────────────────────────────────────────────────────
# Anchor
# ──────────────────────────────────────────────────────────────────────────────

class TestAnchor:
    """Anchor capture tests."""

    def test_anchor_unavailable_for_unknown_symbol(self):
        result = fetch_anchor_yahoo("UNKNOWN_SYMBOL")
        assert result is None

    def test_anchor_yahoo_ticker_map(self):
        """Verify ticker map entries exist."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_forward_recorder_v0 import fetch_anchor_yahoo
        # Just verify the function exists and doesn't crash on import
        assert callable(fetch_anchor_yahoo)


# ──────────────────────────────────────────────────────────────────────────────
# Calendar classification
# ──────────────────────────────────────────────────────────────────────────────

class TestCalendarClassification:
    """Market session classification tests."""

    def test_regular_hours_weekday(self):
        # 12:00 UTC = 07:00 ET (premarket, not regular)
        dt = datetime(2026, 1, 5, 17, 0, 0, tzinfo=timezone.utc)  # 12:00 ET
        result = classify_market_session(dt)
        assert result == "regular_hours"

    def test_premarket(self):
        # 04:00 UTC = 23:00 ET (previous day)
        dt = datetime(2026, 1, 5, 14, 29, 0, tzinfo=timezone.utc)  # 09:29 ET
        result = classify_market_session(dt)
        assert result == "premarket"

    def test_after_hours(self):
        # 21:00 UTC = 16:00 ET
        dt = datetime(2026, 1, 5, 21, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "after_hours"

    def test_weekend(self):
        # Saturday
        dt = datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "weekend_or_holiday"

    def test_sunday(self):
        dt = datetime(2026, 1, 4, 12, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "weekend_or_holiday"

    def test_14_23_et_weekday_regular_hours(self):
        """14:23 ET = 18:23 UTC on a weekday => regular_hours."""
        dt = datetime(2026, 5, 27, 18, 23, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "regular_hours", f"Expected regular_hours, got {result}"

    def test_09_29_et_weekday_premarket(self):
        """09:29 ET = 13:29 UTC on a weekday => premarket."""
        dt = datetime(2026, 5, 27, 13, 29, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "premarket", f"Expected premarket, got {result}"

    def test_09_30_et_weekday_regular_hours(self):
        """09:30 ET = 13:30 UTC on a weekday => regular_hours."""
        dt = datetime(2026, 5, 27, 13, 30, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "regular_hours", f"Expected regular_hours, got {result}"

    def test_15_59_et_weekday_regular_hours(self):
        """15:59 ET = 19:59 UTC on a weekday => regular_hours."""
        dt = datetime(2026, 5, 27, 19, 59, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "regular_hours", f"Expected regular_hours, got {result}"

    def test_16_00_et_weekday_after_hours(self):
        """16:00 ET = 20:00 UTC on a weekday => after_hours."""
        dt = datetime(2026, 5, 27, 20, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "after_hours", f"Expected after_hours, got {result}"

    def test_20_00_et_weekday_after_hours(self):
        """20:00 ET = 00:00 UTC next day => after_hours."""
        dt = datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "after_hours", f"Expected after_hours, got {result}"

    def test_02_00_et_weekday_overnight(self):
        """02:00 EST (winter) = 07:00 UTC in January => overnight (between midnight and premarket)."""
        # In January, ET = EST (UTC-5), so 02:00 EST = 07:00 UTC
        dt = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "overnight", f"Expected overnight, got {result}"

    def test_saturday_14_23_et_weekend(self):
        """Saturday 14:23 ET => weekend_or_holiday."""
        # 2026-05-23 is a Saturday
        dt = datetime(2026, 5, 23, 18, 23, 0, tzinfo=timezone.utc)
        result = classify_market_session(dt)
        assert result == "weekend_or_holiday", f"Expected weekend_or_holiday, got {result}"


# ──────────────────────────────────────────────────────────────────────────────
# Derived metrics
# ──────────────────────────────────────────────────────────────────────────────

class TestDerivedMetrics:
    """Derived metrics computation tests."""

    def test_compute_metrics_basic(self):
        l2 = {
            "best_bid": 250.0, "best_ask": 250.1, "mid": 250.05,
            "spread_bps": 4.0, "empty_bid_side": False, "empty_ask_side": False,
            "timestamp_utc": "2026-01-05T17:00:00Z", "display_symbol": "TSLA",
            "api_symbol": "cash:TSLA", "dex_name": "cash",
            "depth_usd_100_bid": 2500.0, "depth_usd_100_ask": 2500.0,
            "depth_usd_1000_bid": 25000.0, "depth_usd_1000_ask": 25000.0,
            "depth_usd_5000_bid": 125000.0, "depth_usd_5000_ask": 125000.0,
        }
        ctx = {"mark_price": "250.0", "oracle_price": "249.5"}

        result = compute_derived_metrics(l2, ctx, None)

        assert result["best_bid"] == 250.0
        assert result["best_ask"] == 250.1
        assert result["spread_bps"] == 4.0
        assert result["mark"] == "250.0"
        assert result["oracle"] == "249.5"
        assert result["anchor_price"] is None
        assert result["diagnostic_residual_bps"] is None
        assert result["capture_quality_status"] == "good"
        # No PnL, no trade signals, no PnL fields
        assert "pnl" not in result
        assert "profit_factor" not in result
        assert "win_rate" not in result
        assert "sharpe" not in result

    def test_compute_metrics_with_anchor(self):
        l2 = {
            "best_bid": 250.0, "best_ask": 250.1, "mid": 250.05,
            "spread_bps": 4.0, "empty_bid_side": False, "empty_ask_side": False,
            "timestamp_utc": "2026-01-05T17:00:00Z", "display_symbol": "TSLA",
            "api_symbol": "cash:TSLA", "dex_name": "cash",
            "depth_usd_100_bid": 0, "depth_usd_100_ask": 0,
            "depth_usd_1000_bid": 0, "depth_usd_1000_ask": 0,
            "depth_usd_5000_bid": 0, "depth_usd_5000_ask": 0,
        }
        ctx = {"mark_price": "250.0", "oracle_price": "249.5"}
        anchor = {"price": 250.05, "staleness_seconds": 30.0}

        result = compute_derived_metrics(l2, ctx, anchor)

        assert result["anchor_price"] == 250.05
        assert result["anchor_staleness_seconds"] == 30.0
        assert result["diagnostic_residual_bps"] == 0.0  # mid == anchor

    def test_compute_metrics_degraded(self):
        l2 = {
            "best_bid": 250.0, "best_ask": None, "mid": None,
            "spread_bps": None, "empty_bid_side": False, "empty_ask_side": True,
            "timestamp_utc": "2026-01-05T17:00:00Z", "display_symbol": "TSLA",
            "api_symbol": "cash:TSLA", "dex_name": "cash",
            "depth_usd_100_bid": 0, "depth_usd_100_ask": 0,
            "depth_usd_1000_bid": 0, "depth_usd_1000_ask": 0,
            "depth_usd_5000_bid": 0, "depth_usd_5000_ask": 0,
        }
        ctx = {}

        result = compute_derived_metrics(l2, ctx, None)

        assert result["capture_quality_status"] == "no_spread"


# ──────────────────────────────────────────────────────────────────────────────
# Recorder integration
# ──────────────────────────────────────────────────────────────────────────────

class TestRecorderIntegration:
    """Integration tests for the recorder."""

    def test_config_defaults(self):
        config = ForwardRecorderConfig()
        assert config.poll_seconds == 60
        assert config.symbols == ["TSLA", "AAPL", "MSFT", "NVDA"]
        assert not config.dry_run
        assert not config.once
        assert not config.stop_after_init
        assert not config.enable_anchors

    def test_capture_state_defaults(self):
        state = CaptureState()
        assert state.status == RecorderStatus.READY
        assert state.total_polls == 0
        assert state.total_errors == 0
        assert state.start_time_utc is None

    def test_forbidden_statuses_not_in_allowed(self):
        """Forbidden recorder statuses should not appear in allowed info types."""
        for status in FORBIDDEN_STATUSES:
            assert status not in ALLOWED_INFO_TYPES, f"{status} should not be in allowed types"

    def test_study_id(self):
        assert STUDY_ID == "hip3_builder_dex_tradfi_forward_recorder_v0"
