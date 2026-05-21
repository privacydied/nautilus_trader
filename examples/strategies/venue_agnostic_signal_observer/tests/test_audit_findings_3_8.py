"""Regression tests for audit findings 3 and 8 hardening."""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest


def _tick_forward_return_cls():
    from venue_agnostic_signal_observer.tick_models import TickForwardReturn

    return TickForwardReturn


def test_baseline_preserves_real_target_symbol_and_requested_exact_cell_horizon():
    from venue_agnostic_signal_observer.streaming_stress_labels import (
        generate_baseline_from_ts_prices,
    )

    results = generate_baseline_from_ts_prices(
        {
            "ETHUSDT": (np.array([1_000, 2_000, 3_000], dtype=np.int64), np.array([100.0, 101.0, 102.0])),
            "SOLUSDT": (np.array([1_000, 2_000, 3_000], dtype=np.int64), np.array([50.0, 55.0, 60.0])),
        },
        "BTCUSDT",
        label_count=0,
        seed=1,
        TickForwardReturn=_tick_forward_return_cls(),
        VENUE="binance_spot_archive",
        ENTRY_DELAY_NS=0,
        MS_TO_NS=1,
        FEE_BPS=0.0,
        SLIPPAGE_BPS=0.0,
        QUOTE_MISMATCH_BUFFER_BPS=0.0,
        HORIZONS_MS=[999],
        baseline_requests=[{"target_symbol": "SOLUSDT", "signal_ts": 1_000, "horizons_ms": [1_000]}],
    )

    assert len(results) == 1
    assert results[0].target_symbol == "SOLUSDT"
    assert results[0].target_symbol != "BASELINE"
    assert results[0].horizon_ms == 1_000
    assert results[0].entry_reference_price == 50.0
    assert results[0].forward_price == 55.0


def test_baseline_rejects_unsorted_timestamps():
    from venue_agnostic_signal_observer.streaming_stress_labels import (
        generate_baseline_from_ts_prices,
    )

    with pytest.raises(ValueError, match="sorted"):
        generate_baseline_from_ts_prices(
            {"ETHUSDT": (np.array([2_000, 1_000], dtype=np.int64), np.array([101.0, 100.0]))},
            "BTCUSDT",
            label_count=1,
            seed=1,
            TickForwardReturn=_tick_forward_return_cls(),
            VENUE="binance_spot_archive",
            ENTRY_DELAY_NS=0,
            MS_TO_NS=1,
            FEE_BPS=0.0,
            SLIPPAGE_BPS=0.0,
            QUOTE_MISMATCH_BUFFER_BPS=0.0,
            HORIZONS_MS=[1_000],
        )


def test_forward_return_helper_rejects_unsorted_timestamps():
    from venue_agnostic_signal_observer.streaming_stress_labels import (
        compute_forward_returns_from_ts_prices,
    )

    stress_label = SimpleNamespace(label_id="sl", stress_end_ns=1_000, direction="bullish")

    with pytest.raises(ValueError, match="sorted"):
        compute_forward_returns_from_ts_prices(
            stress_label,
            [2_000, 1_000],
            [101.0, 100.0],
            "ETHUSDT",
            [1_000],
            _tick_forward_return_cls(),
            "binance_spot_archive",
            0,
            1,
            0.0,
            0.0,
            0.0,
        )


def test_signal_event_from_dict_requires_valid_direction():
    from venue_agnostic_signal_observer.models import SignalEvent

    base = {
        "signal_id": "s",
        "timestamp": 0.0,
        "source_venue": "A",
        "source_instrument": "BTC/USDT",
        "target_venue": "B",
        "target_instrument": "ETH/USDT",
        "signal_type": "test",
        "strength": 1.0,
    }

    with pytest.raises(ValueError, match="direction"):
        SignalEvent.from_dict(base)
    with pytest.raises(ValueError, match="Invalid"):
        SignalEvent.from_dict({**base, "direction": "up"})

    assert SignalEvent.from_dict({**base, "direction": "short"}).direction == "short"


def test_tick_signal_event_from_dict_rejects_invalid_direction():
    from venue_agnostic_signal_observer.tick_models import TickSignalEvent

    base = {
        "signal_id": "s",
        "ts_event": 1,
        "source_venue": "A",
        "source_symbol": "BTCUSDT",
        "target_venue": "B",
        "target_symbol": "ETHUSDT",
        "asset": "BTC",
        "signal_type": "test",
        "lookback_ms": 1,
        "threshold_bps": 1.0,
        "source_move_bps": 2.0,
        "source_start_price": 100.0,
        "source_end_price": 101.0,
        "strength": 2.0,
    }

    with pytest.raises(ValueError, match="direction"):
        TickSignalEvent.from_dict(base)
    with pytest.raises(ValueError, match="Invalid"):
        TickSignalEvent.from_dict({**base, "direction": "bullish"})


def _zip_csv(content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.csv", content)
    return buf.getvalue()


def test_archive_parse_status_distinguishes_missing_parse_and_schema_errors():
    from venue_agnostic_signal_observer.binance_vision_archive import ArchiveFileStatus
    from venue_agnostic_signal_observer.binance_vision_archive import (
        parse_agg_trade_csv_with_status,
    )

    missing = parse_agg_trade_csv_with_status(None, "BTCUSDT")
    assert missing.status is ArchiveFileStatus.MISSING_FILE

    malformed = parse_agg_trade_csv_with_status(b"not a zip", "BTCUSDT")
    assert malformed.status is ArchiveFileStatus.PARSE_ERROR

    schema = parse_agg_trade_csv_with_status(_zip_csv("not,enough\n"), "BTCUSDT")
    assert schema.status is ArchiveFileStatus.SCHEMA_ERROR

    ok = parse_agg_trade_csv_with_status(
        _zip_csv("1,100.0,0.5,1,1,1700000000000,false,true\n"),
        "BTCUSDT",
    )
    assert ok.status is ArchiveFileStatus.AVAILABLE
    assert len(ok.ticks) == 1
