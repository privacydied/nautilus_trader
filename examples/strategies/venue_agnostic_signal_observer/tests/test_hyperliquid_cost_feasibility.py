from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_cost_feasibility import (
    DO_NOT_PROCEED_COST_WALL_PERSISTS,
)
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_cost_feasibility import (
    compute_cost_feasibility,
)


def _write_book(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False), path)


def test_cost_feasibility_computes_spreads_correctly_on_synthetic_book_data(tmp_path: Path) -> None:
    base = tmp_path / "BTC" / "l2book" / "2026-01-01" / "00.parquet"
    now_ns = 1_800_000_000_000_000_000
    _write_book(base, [
        {"ts_event": now_ns, "coin": "BTC", "seq": 1, "bid_px_0": 99.0, "ask_px_0": 101.0, "bid_sz_0": 2.0, "ask_sz_0": 3.0},
        {"ts_event": now_ns + 1_000_000_000, "coin": "BTC", "seq": 2, "bid_px_0": 100.0, "ask_px_0": 100.0, "bid_sz_0": 1.0, "ask_sz_0": 1.0},
    ])
    result = compute_cost_feasibility(tmp_path, ["BTC"], window_hours=0, maker_fee_bps=1.5, taker_fee_bps=4.5)
    s = result.summaries[0]
    assert round(s.spread_bps_p50 or -1, 2) == 100.0
    assert s.maker_round_trip_bps == 3.0


def test_cost_feasibility_recommendation_cannot_proceed_if_cost_ge_18_bps(tmp_path: Path) -> None:
    base = tmp_path / "BTC" / "l2book" / "2026-01-01" / "00.parquet"
    _write_book(base, [{"ts_event": 1_800_000_000_000_000_000 + i * 3_600_000_000_000, "coin": "BTC", "seq": i, "bid_px_0": 99.0, "ask_px_0": 101.0, "bid_sz_0": 1.0, "ask_sz_0": 1.0} for i in range(26)])
    result = compute_cost_feasibility(tmp_path, ["BTC"], window_hours=0, maker_fee_bps=10.0, taker_fee_bps=4.5)
    assert result.recommendation == DO_NOT_PROCEED_COST_WALL_PERSISTS


def test_no_execution_side_hyperliquid_classes_imported_anywhere() -> None:
    root = Path("examples/strategies/venue_agnostic_signal_observer")
    files = [
        root / "hyperliquid_observer.py",
        root / "run_hyperliquid_observer.py",
        root / "hyperliquid_s3_archive.py",
        root / "run_hyperliquid_s3_archive.py",
        root / "hyperliquid_cost_feasibility.py",
        root / "run_hyperliquid_cost_feasibility.py",
    ]
    forbidden_parts = [("Hyperliquid", "Live", "Exec", "Client", "Factory"), ("Hyperliquid", "Exec", "Client")]
    for file in files:
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.get_source_segment(file.read_text(), node) or ""
                for parts in forbidden_parts:
                    assert "".join(parts) not in text
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"submit" + "_order", "cancel" + "_order", "modify" + "_order"}



def test_cost_feasibility_archive_source_reads_archive_layout(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "BTC" / "l2book" / "2025-11-01" / "00.parquet"
    _write_book(archive, [{"ts_event": 1_761_955_208_909_000_000, "coin": "BTC", "seq": 1, "bid_px_0": 99.0, "ask_px_0": 101.0, "bid_sz_0": 2.0, "ask_sz_0": 3.0}])
    result = compute_cost_feasibility(tmp_path / "missing_live", ["BTC"], window_hours=0, maker_fee_bps=1.5, taker_fee_bps=4.5, data_source="archive", archive_data_dir=tmp_path / "archive")
    assert result.data_source == "archive"
    assert result.summaries[0].snapshot_count == 1


def test_cost_feasibility_stress_labels_filter_archive_to_event_hours(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    _write_book(archive / "BTC" / "l2book" / "2025-11-01" / "00.parquet", [{"ts_event": 1_761_955_208_909_000_000, "coin": "BTC", "seq": 1, "bid_px_0": 99.0, "ask_px_0": 101.0, "bid_sz_0": 2.0, "ask_sz_0": 3.0}])
    _write_book(archive / "BTC" / "l2book" / "2025-11-01" / "01.parquet", [{"ts_event": 1_761_958_800_000_000_000, "coin": "BTC", "seq": 2, "bid_px_0": 98.0, "ask_px_0": 102.0, "bid_sz_0": 1.0, "ask_sz_0": 1.0}])
    labels = tmp_path / "stress_labels.jsonl"
    labels.write_text('{"stress_end_ns":1761955208909000000}\n')
    result = compute_cost_feasibility(tmp_path / "live", ["BTC"], window_hours=0, maker_fee_bps=1.5, taker_fee_bps=4.5, data_source="archive", archive_data_dir=archive, stress_labels_file=labels)
    assert result.summaries[0].snapshot_count == 1
    assert result.summaries[0].first_ts_event == 1_761_955_208_909_000_000


def test_cost_feasibility_both_source_combines_live_and_archive(tmp_path: Path) -> None:
    live = tmp_path / "live" / "BTC" / "l2book" / "2025-11-01" / "00.parquet"
    archive = tmp_path / "archive" / "BTC" / "l2book" / "2025-11-01" / "00.parquet"
    _write_book(live, [{"ts_event": 1_761_955_208_000_000_000, "coin": "BTC", "seq": 1, "bid_px_0": 99.0, "ask_px_0": 101.0, "bid_sz_0": 2.0, "ask_sz_0": 3.0}])
    _write_book(archive, [{"ts_event": 1_761_955_209_000_000_000, "coin": "BTC", "seq": 2, "bid_px_0": 100.0, "ask_px_0": 100.5, "bid_sz_0": 1.0, "ask_sz_0": 1.0}])
    result = compute_cost_feasibility(tmp_path / "live", ["BTC"], window_hours=0, maker_fee_bps=1.5, taker_fee_bps=4.5, data_source="both", archive_data_dir=tmp_path / "archive")
    assert result.data_source == "both"
    assert result.summaries[0].snapshot_count == 2
