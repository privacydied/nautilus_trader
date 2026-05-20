from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from examples.strategies.venue_agnostic_signal_observer import archive_parquet_streaming as streaming


def test_symbol_day_parquet_files_supports_actual_lowercase_archive_layout(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "btcusdt"
    symbol_dir.mkdir()
    expected = symbol_dir / "btcusdt_aggTrades_2024-01-02.parquet"
    expected.write_bytes(b"placeholder")
    (symbol_dir / "btcusdt_aggTrades_2024-01-03.parquet").write_bytes(b"placeholder")

    files = streaming.symbol_day_parquet_files(
        tmp_path,
        "BTCUSDT",
        candidate_dates={"2024-01-02"},
    )

    assert files == [expected]
    assert streaming.date_from_parquet_path(expected) == "2024-01-02"


@dataclass(frozen=True)
class DummyStressLabel:
    label_id: str
    source_symbol: str
    stress_start_ns: int
    stress_end_ns: int
    stress_window_seconds: int
    source_move_bps: float
    direction: str
    source_start_price: float
    source_end_price: float
    independent_window_id: str
    rule_name: str


def test_source_ticks_stream_day_by_day_without_retaining_all_days(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = [tmp_path / f"BTCUSDT_2024-01-0{i}_aggTrades.parquet" for i in range(1, 4)]
    for file_path in files:
        file_path.write_bytes(b"placeholder")

    live_loaded: list[Path] = []
    max_live_loaded = 0

    def fake_read_symbol_day_parquet(file_path: Path) -> streaming.SymbolDayTicks:
        nonlocal max_live_loaded
        assert not live_loaded, "loader retained a prior day while opening the next day"
        live_loaded.append(file_path)
        max_live_loaded = max(max_live_loaded, len(live_loaded))
        day = file_path.name.split("_")[1]
        day_idx = int(day.rsplit("-", 1)[1])
        return streaming.SymbolDayTicks(
            symbol="BTCUSDT",
            date=day,
            timestamps=[day_idx],
            prices=[float(day_idx)],
            sizes=[1.0],
            is_buyer_maker=[False],
            path=file_path,
        )

    monkeypatch.setattr(streaming, "read_symbol_day_parquet", fake_read_symbol_day_parquet)

    yielded: list[str] = []
    for day_ticks in streaming.iter_symbol_day_parquet(tmp_path, "BTCUSDT", candidate_dates={"2024-01-01", "2024-01-02", "2024-01-03"}):
        yielded.append(day_ticks.date)
        live_loaded.clear()

    assert yielded == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert max_live_loaded == 1


def test_source_day_boundary_continuity_detects_cross_midnight_stress(tmp_path: Path) -> None:
    ns = 1_000_000_000
    day0 = 1_704_067_200 * ns
    day1 = day0 + 86_400 * ns

    first = streaming.SymbolDayTicks(
        symbol="BTCUSDT",
        date="2024-01-01",
        timestamps=[day1 - 15 * ns],
        prices=[100.0],
        sizes=[1.0],
        is_buyer_maker=[False],
        path=tmp_path / "BTCUSDT_2024-01-01_aggTrades.parquet",
    )
    second = streaming.SymbolDayTicks(
        symbol="BTCUSDT",
        date="2024-01-02",
        timestamps=[day1 + 15 * ns],
        prices=[101.0],
        sizes=[1.0],
        is_buyer_maker=[False],
        path=tmp_path / "BTCUSDT_2024-01-02_aggTrades.parquet",
    )

    labels, _carry = streaming.generate_stress_labels_for_symbol_days(
        [first, second],
        "BTCUSDT",
        [("30s_50bps", 30, 50.0)],
        StressLabel=DummyStressLabel,
    )

    assert len(labels) == 1
    assert labels[0].stress_start_ns == day1 - 15 * ns
    assert labels[0].stress_end_ns == day1 + 15 * ns
    assert labels[0].source_move_bps == pytest.approx(100.0)


def test_memory_guard_fires_on_rss_spike(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "current_rss_bytes", lambda: 9 * 1024**3)

    with pytest.raises(streaming.ArchiveStreamingMemoryGuardTriggered) as excinfo:
        streaming.check_archive_streaming_memory_guard(
            phase="source_loading",
            symbol="BTCUSDT",
            date="2024-01-01",
            limit_bytes=8 * 1024**3,
        )

    message = str(excinfo.value)
    assert "ARCHIVE_STREAMING_MEMORY_GUARD_TRIGGERED" in message
    assert "source_loading" in message
    assert "BTCUSDT" in message
    assert "2024-01-01" in message
