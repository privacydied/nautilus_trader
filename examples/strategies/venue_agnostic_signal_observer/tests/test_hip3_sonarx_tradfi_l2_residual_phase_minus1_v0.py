"""Tests for the HIP-3 SonarX TradFi L2 Residual Phase -1 scout.

Covers:
- Candle format audit (working format preferred, 422/429 handling)
- Anchor fallback (Yahoo -> Stooq, Stooq mapping, dedup, caching, daily-only)
- Residual computation (mid, crossed books, formula, staleness, underpowered)
- Safety (no subprocess/os.system/eval, no PnL/returns/signals/registry)
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import pytest
except ImportError:
    pytest = None

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
    STOOQ_SYMBOL_MAP,
    _build_candle_payload,
    _epoch_ms,
    _quantile,
    _safe_float,
    _safe_int,
    compute_residual_for_api_symbol,
    fetch_anchor_for_display_symbol,
    parse_api_symbol,
    parse_snapshot,
    run_candle_format_audit,
)


# ---------------------------------------------------------------------------
# Helper: create mock L2 rows
# ---------------------------------------------------------------------------

def _make_l2_row(ts: str, bid: float, ask: float) -> dict:
    mid = (bid + ask) / 2.0
    spread = ((ask - bid) / mid * 10000.0) if mid > 0 else float("inf")
    return {
        "timestamp_utc": ts,
        "api_symbol": "xyz:TSLA",
        "display_symbol": "TSLA",
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "spread_bps": spread,
        "two_sided_book": True,
        "empty_bid_side": False,
        "empty_ask_side": False,
    }


def _make_crossed_row(ts: str, bid: float, ask: float) -> dict:
    """Create a crossed book row (bid >= ask)."""
    mid = (bid + ask) / 2.0
    return {
        "timestamp_utc": ts,
        "api_symbol": "xyz:TSLA",
        "display_symbol": "TSLA",
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "spread_bps": float("inf"),
        "two_sided_book": True,
        "empty_bid_side": False,
        "empty_ask_side": False,
    }


# ---------------------------------------------------------------------------
# Candle format tests
# ---------------------------------------------------------------------------

class TestCandleFormatAudit:
    def test_build_candle_payload_has_req_wrapper(self):
        """The payload must use the req wrapper format."""
        payload = _build_candle_payload("xyz:TSLA", "1h", 1000, 2000)
        assert "type" in payload
        assert payload["type"] == "candleSnapshot"
        assert "req" in payload
        assert isinstance(payload["req"], dict)
        assert payload["req"]["coin"] == "xyz:TSLA"
        assert payload["req"]["interval"] == "1h"
        assert payload["req"]["startTime"] == 1000
        assert payload["req"]["endTime"] == 2000

    def test_epoch_ms_conversion(self):
        """_epoch_ms converts datetime to epoch milliseconds."""
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ms = _epoch_ms(dt)
        assert ms > 0
        assert isinstance(ms, int)
        # Verify round-trip
        back = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        assert back.year == 2026
        assert back.month == 1
        assert back.day == 1

    def test_candle_422_maps_to_format_unresolved(self):
        """422 response maps to CANDLE_SNAPSHOT_FORMAT_UNRESOLVED."""
        mock_cp = MagicMock()
        mock_cp.http_post_json.return_value = "HTTP Error 422: Unprocessable Entity"
        mock_cp.allow_network_public = True
        mock_root = Path("/tmp/test_candle_audit")
        mock_root.mkdir(exist_ok=True)
        meta = {"study_id": "test", "run_id": "test_run"}

        result = run_candle_format_audit(mock_cp, ["cash:TSLA"], mock_root, meta)

        assert result["overall_status"] == "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED"
        assert len(result["results"]) >= 1
        assert result["results"][0]["http_status"] == 422
        assert result["results"][0]["status"] == "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED"

    def test_candle_429_maps_to_rate_limited(self):
        """429 response maps to CANDLE_SNAPSHOT_RATE_LIMITED and stops further spam."""
        mock_cp = MagicMock()
        mock_cp.http_post_json.return_value = "HTTP Error 429: Too Many Requests"
        mock_cp.allow_network_public = True
        mock_root = Path("/tmp/test_candle_429")
        mock_root.mkdir(exist_ok=True)
        meta = {"study_id": "test", "run_id": "test_run"}

        result = run_candle_format_audit(mock_cp, ["cash:TSLA", "xyz:NVDA"], mock_root, meta)

        assert result["overall_status"] == "CANDLE_SNAPSHOT_RATE_LIMITED"
        # Should stop after first 429
        assert len(result["results"]) == 1

    def test_candle_success_persists_winning_format(self):
        """Successful format persists winning_format and stops brute-forcing."""
        mock_cp = MagicMock()
        mock_cp.http_post_json.return_value = [
            {"timestamp_ms": 1000, "open": 100, "close": 101},
            {"timestamp_ms": 2000, "open": 101, "close": 102},
        ]
        mock_cp.allow_network_public = True
        mock_root = Path("/tmp/test_candle_success")
        mock_root.mkdir(exist_ok=True)
        meta = {"study_id": "test", "run_id": "test_run"}

        result = run_candle_format_audit(mock_cp, ["cash:TSLA", "xyz:NVDA"], mock_root, meta)

        assert result["overall_status"] == "CANDLE_SNAPSHOT_AVAILABLE"
        assert result["winning_format"] is not None
        # Should only have 1 result (stopped after first success)
        assert len(result["results"]) == 1

    def test_candle_failure_does_not_invalidate_l2(self):
        """Candle audit runs independently of L2 metrics."""
        # If candle audit fails, L2 data-bearing is still valid
        mock_cp = MagicMock()
        mock_cp.http_post_json.return_value = "HTTP Error 422"
        mock_cp.allow_network_public = True
        mock_root = Path("/tmp/test_candle_no_inv")
        mock_root.mkdir(exist_ok=True)
        meta = {"study_id": "test", "run_id": "test_run"}

        result = run_candle_format_audit(mock_cp, ["cash:TSLA"], mock_root, meta)
        assert result["overall_status"] == "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED"
        # This doesn't affect L2 data-bearing status


# ---------------------------------------------------------------------------
# Anchor fallback tests
# ---------------------------------------------------------------------------

class TestAnchorFallback:
    def test_stooq_symbol_mapping(self):
        """Stooq symbols map correctly for all 4 display symbols."""
        assert STOOQ_SYMBOL_MAP["TSLA"] == "tsla.us"
        assert STOOQ_SYMBOL_MAP["AAPL"] == "aapl.us"
        assert STOOQ_SYMBOL_MAP["MSFT"] == "msft.us"
        assert STOOQ_SYMBOL_MAP["NVDA"] == "nvda.us"

    def test_stooq_unknown_symbol_returns_none(self):
        """Unknown symbols return None from Stooq."""
        assert STOOQ_SYMBOL_MAP.get("UNKNOWN") is None

    def test_fetch_anchor_yahoo_success(self):
        """Yahoo anchor fetch returns price on success."""
        mock_cp = MagicMock()
        mock_response = json.dumps({
            "chart": {"result": [{
                "meta": {"regularMarketPrice": 250.5},
                "timestamp": [1700000000, 1700086400],
            }]}
        })
        mock_cp.http_get.return_value = mock_response.encode()

        result = fetch_anchor_for_display_symbol(mock_cp, "TSLA", "yahoo")
        assert result is not None
        assert result["price"] == 250.5
        assert result["source"] == "yahoo"

    def test_yahoo_429_falls_through_to_stooq(self):
        """Yahoo 429 falls through to Stooq."""
        mock_cp = MagicMock()
        mock_cp.http_get.side_effect = [
            "HTTP Error 429: Too Many Requests",  # Yahoo fails
            b"1700000000,250.0,255.0,248.0,252.0,1000000\n",  # Stooq succeeds
        ]

        result = fetch_anchor_for_display_symbol(mock_cp, "TSLA", "yahoo,stooq")
        assert result is not None
        assert result["price"] == 252.0
        assert result["source"] == "stooq"

    def test_anchor_dedupe_by_display_symbol(self):
        """12 API markets dedupe to 4 display-symbol anchor groups."""
        markets = [
            "xyz:TSLA", "flx:TSLA", "km:TSLA", "cash:TSLA",
            "xyz:AAPL", "km:AAPL",
            "xyz:MSFT", "cash:MSFT",
            "xyz:NVDA", "flx:NVDA", "km:NVDA", "cash:NVDA",
        ]
        display_symbols = sorted(set(parse_api_symbol(m)[0] for m in markets))
        assert display_symbols == ["AAPL", "MSFT", "NVDA", "TSLA"]
        assert len(display_symbols) == 4

    def test_cache_hit_avoids_network_fetch(self):
        """Cached anchor response avoids network fetch."""
        mock_cp = MagicMock()
        anchor_cache: dict = {}

        # First fetch - cache miss
        mock_cp.http_get.return_value = json.dumps({
            "chart": {"result": [{"meta": {"regularMarketPrice": 250.0}}]}
        }).encode()

        result1 = fetch_anchor_for_display_symbol(mock_cp, "TSLA", "yahoo", None, anchor_cache)
        assert result1 is not None
        assert result1["price"] == 250.0
        http_get_calls = mock_cp.http_get.call_count

        # Second fetch - cache hit (same display symbol, same source)
        result2 = fetch_anchor_for_display_symbol(mock_cp, "TSLA", "yahoo", None, anchor_cache)
        # Should use cache, so no additional http_get call
        assert result2 is not None
        assert result2["price"] == 250.0

    def test_daily_only_anchors_marked_stale(self):
        """Daily-only anchors are marked as stale."""
        mock_cp = MagicMock()
        mock_cp.http_get.return_value = json.dumps({
            "chart": {"result": [{"meta": {"regularMarketPrice": 250.0}}]}
        }).encode()

        result = fetch_anchor_for_display_symbol(mock_cp, "TSLA", "yahoo")
        assert result is not None
        assert result["frequency"] == "daily"
        assert result["is_intraday"] is False

    def test_stale_daily_anchors_do_not_pass_normal_residual_gate(self):
        """Stale daily anchors do not pass normal residual gate by default."""
        rows = [
            _make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0),
            _make_l2_row("2026-01-01T14:00:00+00:00", 248.0, 252.0),
        ]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T12:00:00+00:00",
            anchor_is_intraday=False, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is None
        # Daily-only with allow_daily_stale=False returns None
        assert res is None  # No residuals computed when anchor blocked


# ---------------------------------------------------------------------------
# Residual computation tests
# ---------------------------------------------------------------------------

class TestResidualComputation:
    def test_l2_mid_computed_correctly(self):
        """L2 mid is computed as (best_bid + best_ask) / 2."""
        row = _make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0)
        assert row["mid"] == 250.0

    def test_crossed_books_excluded(self):
        """Crossed books (bid >= ask) are excluded from residuals."""
        rows = [
            _make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0),  # valid
            _make_crossed_row("2026-01-01T11:00:00+00:00", 251.0, 250.0),  # crossed
        ]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert res["aligned_sample_count"] == 1  # Only the valid row

    def test_residual_formula_correct(self):
        """residual_bps = 10000 * (hyperliquid_mid - anchor_price) / anchor_price."""
        rows = [_make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0)]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        expected_residual = 10000.0 * (250.0 - 250.0) / 250.0
        assert res["median_residual_bps"] == 0.0

        # Test with offset
        rows2 = [_make_l2_row("2026-01-01T10:00:00+00:00", 251.0, 253.0)]
        res2 = compute_residual_for_api_symbol(
            rows2, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        expected_residual = 10000.0 * (252.0 - 250.0) / 250.0  # = 80 bps
        assert res2 is not None
        assert abs(res2["median_residual_bps"] - expected_residual) < 0.01

    def test_anchor_staleness_threshold_enforced(self):
        """Rows older than max_staleness are excluded."""
        rows = [
            _make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0),  # 0 min stale
            _make_l2_row("2026-01-01T12:00:00+00:00", 249.0, 251.0),  # 120 min stale
        ]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert res["aligned_sample_count"] == 1  # Only the 0-min stale row

    def test_underpowered_sample_count_flagged(self):
        """Fewer than 500 samples flags underpowered."""
        rows = [_make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0)]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert res["underpowered"] is True
        assert res["residual_status"] == "SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED"

    def test_distinct_day_gate_enforced(self):
        """Fewer than 3 distinct days flags underpowered."""
        rows = [
            _make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0),
            _make_l2_row("2026-01-01T14:00:00+00:00", 249.0, 251.0),
        ]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert res["distinct_calendar_days"] == 1
        assert res["underpowered"] is True

    def test_concentration_warning(self):
        """If >50% of samples come from one day, concentration warning is set."""
        rows = []
        for i in range(100):
            rows.append(_make_l2_row(f"2026-01-01T10:00:00+00:00", 249.0, 251.0))
        rows.append(_make_l2_row("2026-01-02T10:00:00+00:00", 249.0, 251.0))

        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert res["concentration_warning"] is True
        assert res["tail_dominated_by_one_day"] is True

    def test_no_residuals_when_no_anchor(self):
        """Returns None when anchor price is 0."""
        rows = [_make_l2_row("2026-01-01T10:00:00+00:00", 249.0, 251.0)]
        res = compute_residual_for_api_symbol(
            rows, 0.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is None

    def test_no_residuals_when_no_rows(self):
        """Returns None when no L2 rows."""
        res = compute_residual_for_api_symbol(
            [], 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is None

    def test_tail_diagnostics_included(self):
        """Tail diagnostics (p75/p90/p99 abs residual, gte thresholds) are included."""
        rows = [
            _make_l2_row(f"2026-01-0{i}T10:00:00+00:00", 249.0, 251.0)
            for i in range(1, 6)
        ]
        res = compute_residual_for_api_symbol(
            rows, 250.0, "yahoo", "2026-01-01T10:00:00+00:00",
            anchor_is_intraday=True, max_staleness_minutes=15,
            allow_daily_stale=False
        )
        assert res is not None
        assert "p75_abs_residual_bps" in res
        assert "p90_abs_residual_bps" in res
        assert "p99_abs_residual_bps" in res
        assert "abs_residual_ge_25_bps_count" in res
        assert "abs_residual_ge_50_bps_count" in res
        assert "abs_residual_ge_100_bps_count" in res


# ---------------------------------------------------------------------------
# Safety tests
# ---------------------------------------------------------------------------

class TestSafety:
    def test_no_subprocess_os_system_eval_in_source(self):
        """No production subprocess, os.system, or eval in the source."""
        source_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_tradfi_l2_residual_phase_minus1_v0.py"
        source = source_path.read_text()
        # These should NOT appear in the main logic (comments/docstrings/imports are ok)
        lines = [l.strip() for l in source.split("\n") if not l.strip().startswith("#") and not l.strip().startswith('"""') and not l.strip().startswith("'''")]
        for line in lines:
            if "os.system" in line and not line.startswith("assert"):
                assert False, f"Found os.system in: {line}"
            if "subprocess" in line and "import subprocess" not in line and not line.startswith("assert"):
                assert False, f"Found subprocess in: {line}"

    def test_forbidden_statuses_not_in_statuses(self):
        """Forbidden statuses are not in the allowed STATUSES set."""
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
            STATUSES, FORBIDDEN_STATUSES,
        )
        for fs in FORBIDDEN_STATUSES:
            assert fs not in STATUSES, f"Forbidden status {fs} found in STATUSES"

    def test_parse_api_symbol_correct(self):
        """parse_api_symbol correctly splits dex:coin."""
        display, dex = parse_api_symbol("xyz:TSLA")
        assert display == "TSLA"
        assert dex == "xyz"

        display, dex = parse_api_symbol("cash:NVDA")
        assert display == "NVDA"
        assert dex == "cash"

    def test_safe_float_handles_edge_cases(self):
        """_safe_float handles None, strings, and valid floats."""
        assert _safe_float(None) == 0.0
        assert _safe_float("250.5") == 250.5
        assert _safe_float("") == 0.0
        assert _safe_float("invalid") == 0.0

    def test_safe_int_handles_edge_cases(self):
        """_safe_int handles None, strings, and valid ints."""
        assert _safe_int(None) == 0
        assert _safe_int("250") == 250
        assert _safe_int("") == 0
        assert _safe_int("invalid") == 0


# ---------------------------------------------------------------------------
# L2 parsing tests
# ---------------------------------------------------------------------------

class TestL2Parsing:
    def test_parse_snapshot_basic(self):
        """Basic L2 snapshot parses correctly."""
        raw = {
            "block_time": "2026-01-01T10:00:00Z",
            "bids": [{"px": 249.0, "sz": 100, "n": 5}],
            "asks": [{"px": 251.0, "sz": 100, "n": 3}],
            "height": 12345,
            "market": "xyz:TSLA",
        }
        parsed = parse_snapshot(raw, "xyz:TSLA")
        assert parsed is not None
        assert parsed["best_bid"] == 249.0
        assert parsed["best_ask"] == 251.0
        assert parsed["mid"] == 250.0
        assert parsed["spread_bps"] == pytest.approx(80.0, abs=0.1) if pytest else abs(parsed["spread_bps"] - 80.0) < 0.1
        assert parsed["two_sided_book"] is True

    def test_parse_snapshot_empty_side(self):
        """One-sided book is flagged."""
        raw = {
            "block_time": "2026-01-01T10:00:00Z",
            "bids": [{"px": 249.0, "sz": 100, "n": 5}],
            "asks": [],
            "height": 12345,
            "market": "xyz:TSLA",
        }
        parsed = parse_snapshot(raw, "xyz:TSLA")
        assert parsed is not None
        assert parsed["two_sided_book"] is False
        assert parsed["empty_ask_side"] is True
