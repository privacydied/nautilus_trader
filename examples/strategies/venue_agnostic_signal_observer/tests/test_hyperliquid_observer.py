from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_observer import HourlyParquetSink
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_observer import HyperliquidBookSnapshot
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_observer import HyperliquidLiveObserver
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_observer import HyperliquidTrade
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_observer import ObserverConfig


def test_observer_schemas_serialize_deserialize_parquet_roundtrip(tmp_path: Path) -> None:
    sink = HourlyParquetSink(tmp_path)
    book = HyperliquidBookSnapshot(ts_event=1_700_000_000_000_000_000, coin="BTC", bids=[(100.0, 2.0, 1)], asks=[(101.0, 3.0, 2)], seq=1)
    trade = HyperliquidTrade(ts_event=1_700_000_000_000_000_001, coin="BTC", px=100.5, sz=0.1, side="B", hash="h1")
    assert sink.add("BTC", "l2book", book.to_row())
    assert sink.add("BTC", "trades", trade.to_row())
    sink.flush_all()
    book_df = pq.read_table(next((tmp_path / "BTC" / "l2book").glob("*/*.parquet"))).to_pandas()
    trade_df = pq.read_table(next((tmp_path / "BTC" / "trades").glob("*/*.parquet"))).to_pandas()
    assert book_df.loc[0, "bid_px_0"] == 100.0
    assert book_df.loc[0, "ask_sz_0"] == 3.0
    assert trade_df.loc[0, "hash"] == "h1"


def test_observer_crash_recovery_deduplicates_seq(tmp_path: Path) -> None:
    first = HourlyParquetSink(tmp_path)
    row = HyperliquidBookSnapshot(ts_event=1_700_000_000_000_000_000, coin="BTC", bids=[(100.0, 1.0, 1)], asks=[(101.0, 1.0, 1)], seq=42).to_row()
    assert first.add("BTC", "l2book", row)
    first.flush_all()
    second = HourlyParquetSink(tmp_path)
    second.load_recovery_keys(["BTC"], ["l2book"])
    assert not second.add("BTC", "l2book", row)


def test_observer_respects_memory_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    obs = HyperliquidLiveObserver(ObserverConfig(coins=("BTC",), out=tmp_path, max_rss_gb=4, hard_rss_gb=6))
    monkeypatch.setattr(obs, "rss_gb", lambda: 6.1)
    with pytest.raises(MemoryError):
        obs.assert_memory_guard()
