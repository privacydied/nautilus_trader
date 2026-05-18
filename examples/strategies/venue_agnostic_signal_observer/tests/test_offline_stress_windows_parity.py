"""Parity tests for the O(n) stress-window rewrite (commit 91d5f02789).

This file contains two categories of parity test:

1. HISTORICAL-EQUIVALENCE parity (primary):
   - Recovers the pre-rewrite ``_eligible_points``, ``_range_bps``, and
     ``_absolute_return_bps`` functions from git history at
     ``91d5f02789^``.
   - Compares the windows produced by the old and new paths on identical
     fixtures.
   - Asserts that the optimized ``_eligible_slice`` sliding-pointer path
     produces identical windows to the removed O(n²) list-creation path.

2. Overlapping-stress-window fixture:
   - A fixture where two stress events have overlapping eligible windows.
   - Exercises the dedup/cooldown logic that the sliding-pointer rewrite
     is most likely to break (dropped, merged, or double-counted windows).

Every assertion that compares against the historical reference is explicitly
labelled "historical-equivalence parity" and uses functions derived from
git show 91d5f02789^:<file> to avoid re-deriving from intent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    WINDOW_MODE_CAUSAL,
    OfflineSourceFile,
    OfflineTradeRecord,
)
from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import (
    compute_data_corpus_hash,
    sha256_file,
)
from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
    OFFLINE_STRESS_WINDOW_SCHEMA_VERSION,
    STATUS_OFFLINE_STRESS_INDEX_READY,
    StressRuleConfig,
    StressWindowIndexResult,
    build_stress_window_index,
)

NS = 1_000_000_000

# =========================================================================
# Historical reference functions — recovered from git history
# =========================================================================
# Source: git show 91d5f02789^:examples/strategies/venue_agnostic_signal_observer/offline_stress_windows.py
# These are the pre-rewrite implementations, preserved here so the parity
# test compares against the actual removed code, not against intent.
#
# Historical _eligible_points: O(n²) list-creation approach.
# Historical _range_bps / _absolute_return_bps: take a pre-filtered list
# rather than (points, start, end) indices.

def _historical_eligible_points(
    points: Sequence[dict[str, Any]], index: int, lookback_ns: int,
) -> list[dict[str, Any]]:
    """Pre-rewrite _eligible_points — O(n²) list creation from commit 91d5f02789^."""
    current_ts = points[index]["timestamp_ns"]
    start_ts = current_ts - lookback_ns
    return [point for point in points[: index + 1] if point["timestamp_ns"] >= start_ts]


def _historical_range_bps(points: Sequence[dict[str, Any]]) -> float:
    """Pre-rewrite _range_bps — operates on a pre-filtered list."""
    min_low = min(point["low"] for point in points)
    max_high = max(point["high"] for point in points)
    if min_low <= 0:
        return 0.0
    return ((max_high - min_low) / min_low) * 10_000.0


def _historical_absolute_return_bps(points: Sequence[dict[str, Any]]) -> float:
    """Pre-rewrite _absolute_return_bps — operates on a pre-filtered list."""
    start_price = points[0]["price"]
    end_price = points[-1]["price"]
    if start_price <= 0:
        return 0.0
    return abs((end_price - start_price) / start_price) * 10_000.0


# =========================================================================
# Fixture helpers
# =========================================================================

def _trade(ts_s: int, price: float, source_file: str = "stream") -> OfflineTradeRecord:
    return OfflineTradeRecord(
        venue="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        timestamp_ns=ts_s * NS,
        price=price,
        size=1.0,
        side="buy",
        trade_id=f"t-{ts_s}",
        source_file=source_file,
        source_kind="kraken_trades",
        resolution_type=RESOLUTION_AGG_TRADE,
    )


def _source_file(
    tmp_path: Path,
    *,
    logical_source_id: str = "stream",
) -> OfflineSourceFile:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload_path = tmp_path / f"{logical_source_id}.csv"
    payload_path.write_text("fixture\n", encoding="utf-8")
    return OfflineSourceFile(
        path=str(payload_path),
        logical_source_id=logical_source_id,
        venue="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
        source_kind="kraken_trades",
        stream_type="agg_trades",
        resolution_type=RESOLUTION_AGG_TRADE,
        timestamp_unit="s",
        expected_start_ns=0,
        expected_end_ns=10_000 * NS,
        file_size_bytes=payload_path.stat().st_size,
        mtime_ns=payload_path.stat().st_mtime_ns,
        file_sha256=sha256_file(payload_path),
        row_count=1,
        data_start_ns=0,
        data_end_ns=10_000 * NS,
    )


def _dataset(tmp_path: Path, *, trades: list[OfflineTradeRecord]):
    source = _source_file(tmp_path)
    source_files = [source]
    ds = type(
        "OfflinePreparedDataset",
        (),
        {
            "source_files": source_files,
            "trades_by_stream": {source.logical_source_id: trades},
            "bars_by_stream": {},
            "data_corpus_hash": compute_data_corpus_hash(
                source_files, OFFLINE_DATA_SCHEMA_VERSION
            ),
            "schema_version": OFFLINE_DATA_SCHEMA_VERSION,
            "created_at_utc": "2024-01-01T00:00:00+00:00",
            "git_sha": "deadbeef",
        },
    )()
    return ds, source


def _points_for_fixture(trades: list[OfflineTradeRecord]) -> list[dict[str, Any]]:
    """Replicate _points_for_stream from the current codebase."""
    sorted_trades = sorted(trades, key=lambda item: item.timestamp_ns)
    return [
        {
            "timestamp_ns": item.timestamp_ns,
            "price": item.price,
            "high": item.price,
            "low": item.price,
        }
        for item in sorted_trades
    ]


def _hist_evaluate_rule_on_points(
    *,
    points: Sequence[dict[str, Any]],
    source: OfflineSourceFile,
    rule: StressRuleConfig,
) -> list[dict[str, Any]]:
    """Replicate the pre-rewrite _evaluate_rule_on_points for parity comparison.

    Uses the historical _eligible_points, _range_bps, and _absolute_return_bps.
    Returns window payload dicts (not OfflineStressWindow objects) for comparison.
    """
    lookback_ns = rule.lookback_seconds * NS
    cooldown_ns = rule.cooldown_seconds * NS
    last_emitted_ts: int | None = None
    windows: list[dict[str, Any]] = []
    suppressed = 0

    for index in range(len(points)):
        eligible = _historical_eligible_points(points, index, lookback_ns)
        if len(eligible) < rule.min_required_points:
            continue

        if rule.rule_name == "rolling_range_bps":
            trigger_value = _historical_range_bps(eligible)
        elif rule.rule_name == "rolling_absolute_return_bps":
            trigger_value = _historical_absolute_return_bps(eligible)
        else:
            continue

        if trigger_value < rule.threshold_bps:
            continue

        trigger_ts = points[index]["timestamp_ns"]
        if last_emitted_ts is not None and trigger_ts - last_emitted_ts < cooldown_ns:
            suppressed += 1
            continue

        windows.append({
            "trigger_timestamp_ns": trigger_ts,
            "trigger_value": round(trigger_value, 12),
            "point_count": len(eligible),
            "suppressed": suppressed,
        })
        last_emitted_ts = trigger_ts

    return windows


# =========================================================================
# Part 1A: Historical-equivalence parity test
# =========================================================================

class TestHistoricalEquivalenceParity:
    """Tests that compare the optimized path against the historical reference.

    These tests use the actual pre-rewrite implementation recovered from
    ``git show 91d5f02789^``.  Every assertion here is labelled
    "historical-equivalence parity."
    """

    def test_eligible_slice_returns_identical_points(
        self, tmp_path: Path,
    ) -> None:
        """HISTORICAL-EQUIVALENCE PARITY: _eligible_slice vs _eligible_points.

        For every index in a fixture, compare the points selected by the
        historical _eligible_points (O(n²) list creation) against the
        points selected by the current _eligible_slice (O(n) sliding window).
        """
        # Import the current implementation
        from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
            _eligible_slice,
        )

        # Fixture: 10 points at irregular intervals with duplicate timestamps
        raw_trades = [
            _trade(0, 100.0),
            _trade(50, 100.5),
            _trade(50, 100.3),   # same timestamp as above
            _trade(120, 101.0),
            _trade(200, 102.0),
            _trade(350, 105.0),
            _trade(500, 108.0),
            _trade(500, 108.2),   # same timestamp
            _trade(700, 110.0),
            _trade(900, 112.0),
        ]
        points = _points_for_fixture(raw_trades)

        lookback_ns = 300 * NS  # 300-second lookback

        for index in range(len(points)):
            # Historical path: creates a filtered list
            old_selected = _historical_eligible_points(points, index, lookback_ns)

            # Optimized path: returns (start, end) indices, using left=0
            # (no sliding pointer — fresh scan each time for an apples-to-apples
            # comparison of the selection logic itself).
            new_start, new_end = _eligible_slice(points, index, lookback_ns, left=0)
            new_selected = points[new_start:new_end]

            # HISTORICAL-EQUIVALENCE PARITY assertion
            assert len(old_selected) == len(new_selected), (
                f"index={index}, lookback_ns={lookback_ns}: "
                f"old returned {len(old_selected)} points, new returned {len(new_selected)}"
            )
            for old_pt, new_pt in zip(old_selected, new_selected):
                assert old_pt["timestamp_ns"] == new_pt["timestamp_ns"], (
                    f"index={index}: timestamp mismatch"
                )
                assert old_pt["price"] == new_pt["price"], (
                    f"index={index}: price mismatch"
                )

    def test_eligible_slice_sliding_window_matches_historical(
        self, tmp_path: Path,
    ) -> None:
        """HISTORICAL-EQUIVALENCE PARITY: sliding-pointer _eligible_slice in loop context.

        Replicates the actual loop from _evaluate_rule_on_points, using the
        sliding window_left pointer, and compares each window against the
        historical reference.
        """
        from examples.strategies.venue_agnostic_signal_observer.offline_stress_windows import (
            _eligible_slice,
            _range_bps,
            _absolute_return_bps,
        )

        raw_trades = [
            _trade(0, 100.0),
            _trade(100, 100.5),
            _trade(200, 101.0),
            _trade(300, 102.0),
            _trade(400, 110.0),   # triggers: range over [100,400] = 1000bps
            _trade(500, 112.0),
            _trade(600, 115.0),
            _trade(700, 120.0),   # triggers: range over [400,700] = 1000bps
        ]
        points = _points_for_fixture(raw_trades)
        lookback_ns = 300 * NS
        cooldown_ns = 200 * NS
        threshold_bps = 500.0
        min_required_points = 2

        # --- Historical path ---
        hist_windows = _hist_evaluate_rule_on_points(
            points=points,
            source=_source_file(tmp_path / "src"),
            rule=StressRuleConfig(
                rule_name="rolling_range_bps",
                rule_version="v1",
                lookback_seconds=300,
                threshold_bps=threshold_bps,
                cooldown_seconds=200,
                pre_window_seconds=60,
                post_window_seconds=300,
                min_required_points=min_required_points,
                supported_resolutions=("trade", "agg_trade"),
                trigger_metric="range_bps",
            ),
        )

        # --- Optimized path (with sliding window_left) ---
        opt_windows: list[dict[str, Any]] = []
        last_emitted_ts: int | None = None
        window_left = 0
        opt_suppressed = 0

        for index in range(len(points)):
            start_idx, end_idx = _eligible_slice(points, index, lookback_ns, window_left)
            window_left = start_idx
            eligible_count = end_idx - start_idx
            if eligible_count < min_required_points:
                continue

            trigger_value = _range_bps(points, start_idx, end_idx)
            if trigger_value < threshold_bps:
                continue

            trigger_ts = points[index]["timestamp_ns"]
            if last_emitted_ts is not None and trigger_ts - last_emitted_ts < cooldown_ns:
                opt_suppressed += 1
                continue

            opt_windows.append({
                "trigger_timestamp_ns": trigger_ts,
                "trigger_value": round(trigger_value, 12),
                "point_count": eligible_count,
                "suppressed": opt_suppressed,
            })
            last_emitted_ts = trigger_ts

        # HISTORICAL-EQUIVALENCE PARITY: same number of windows
        assert len(hist_windows) == len(opt_windows), (
            f"historical produced {len(hist_windows)} windows, "
            f"optimized produced {len(opt_windows)}"
        )
        # HISTORICAL-EQUIVALENCE PARITY: each window matches
        for i, (hw, ow) in enumerate(zip(hist_windows, opt_windows)):
            assert hw["trigger_timestamp_ns"] == ow["trigger_timestamp_ns"], (
                f"window[{i}] trigger_timestamp_ns: "
                f"historical={hw['trigger_timestamp_ns']}, "
                f"optimized={ow['trigger_timestamp_ns']}"
            )
            assert hw["trigger_value"] == ow["trigger_value"], (
                f"window[{i}] trigger_value: "
                f"historical={hw['trigger_value']}, "
                f"optimized={ow['trigger_value']}"
            )
            assert hw["point_count"] == ow["point_count"], (
                f"window[{i}] point_count: "
                f"historical={hw['point_count']}, "
                f"optimized={ow['point_count']}"
            )

    def test_full_stress_window_index_matches_historical_reference(
        self, tmp_path: Path,
    ) -> None:
        """HISTORICAL-EQUIVALENCE PARITY: full build_stress_window_index vs historical.

        Runs the full current build_stress_window_index and compares the
        produced windows against the historical reference implementation.
        """
        trades = [
            _trade(0, 100.0),
            _trade(100, 100.5),
            _trade(200, 101.0),
            _trade(300, 102.0),
            _trade(400, 110.0),
            _trade(500, 112.0),
            _trade(600, 115.0),
            _trade(700, 120.0),
        ]
        ds, src = _dataset(tmp_path / "data", trades=trades)

        rule = StressRuleConfig(
            rule_name="rolling_range_bps",
            rule_version="v1",
            lookback_seconds=300,
            threshold_bps=500.0,
            cooldown_seconds=200,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade"),
            trigger_metric="range_bps",
        )

        # Current optimized path
        opt_result: StressWindowIndexResult = build_stress_window_index(
            ds, [rule], selection_mode=WINDOW_MODE_CAUSAL,
        )

        # Historical reference: manually run the old logic
        points = _points_for_fixture(trades)
        hist_windows = _hist_evaluate_rule_on_points(
            points=points, source=src, rule=rule,
        )

        # HISTORICAL-EQUIVALENCE PARITY
        assert opt_result.status == STATUS_OFFLINE_STRESS_INDEX_READY
        assert len(opt_result.windows) == len(hist_windows), (
            f"Full build: historical={len(hist_windows)} windows, "
            f"optimized={len(opt_result.windows)}"
        )
        for i, (ow, hw) in enumerate(zip(opt_result.windows, hist_windows)):
            assert ow.trigger_timestamp_ns == hw["trigger_timestamp_ns"]
            # Allow tiny float rounding differences
            assert abs(ow.trigger_value - hw["trigger_value"]) < 1e-9


# =========================================================================
# Part 1B: Overlapping-stress-window fixture
# =========================================================================

class TestOverlappingStressWindowDedup:
    """Tests that overlapping stress windows are correctly handled.

    The sliding-pointer rewrite is most likely to break on fixtures where
    two stress events have overlapping eligible lookback windows but
    survive cooldown separation.  A bug could cause the optimized path to
    drop, merge, or double-count a window.

    This fixture stresses the dedup/cooldown logic with overlapping
    eligible-window ranges.

    The absolute_return_bps rule is used because with range_bps, a single
    price spike stays in the window for ``lookback_seconds``, causing all
    intermediate points to also trigger (and be suppressed by cooldown).
    Absolute return only considers the first and last point in each window,
    making it possible to cleanly separate the two overlapping windows.
    """

    def test_overlapping_windows_both_survive_cooldown(self, tmp_path: Path) -> None:
        """Two stress events with overlapping eligible windows, both surviving cooldown.

        Fixture:
          Points at 0s, 100s, 200s, 300s, 400s, 500s, 600s, 700s
          Prices:    100, 100,  100,  100,  110,  101,  102,  120

          Lookback:  400s — window 1 (400s): eligible = [0s, 400s]  → (100, 110) = 1000bps
                            window 2 (700s): eligible = [300s, 700s] → (100, 120) = 2000bps
                            -> OVERLAP at [300s, 400s]

          Cooldown:  250s — 700-400=300s >= 250s → both survive

          Threshold: 500bps — both windows exceed this

          Intermediate points (500s, 600s) have prices close to baseline
          (101, 102) so their absolute return is below threshold.
          This means NO suppressed triggers between the two windows.

        The overlapping eligible regions ([300s, 400s]) exercise the
        sliding window_left pointer where it must advance past previously-
        eligible points without skipping points that remain eligible.
        """
        trades = [
            _trade(0, 100.0),
            _trade(100, 100.0),
            _trade(200, 100.0),
            _trade(300, 100.0),
            _trade(400, 110.0),   # trigger 1: absolute return over [0,400] = 1000bps
            _trade(500, 101.0),   # low return: no trigger
            _trade(600, 102.0),   # low return: no trigger
            _trade(700, 120.0),   # trigger 2: absolute return over [300,700] = 2000bps
        ]
        ds, _ = _dataset(tmp_path / "data", trades=trades)

        rule = StressRuleConfig(
            rule_name="rolling_absolute_return_bps",
            rule_version="v1",
            lookback_seconds=400,
            threshold_bps=500.0,
            cooldown_seconds=250,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade"),
            trigger_metric="absolute_return_bps",
        )

        result: StressWindowIndexResult = build_stress_window_index(
            ds, [rule], selection_mode=WINDOW_MODE_CAUSAL,
        )

        assert result.status == STATUS_OFFLINE_STRESS_INDEX_READY, (
            f"Expected OFFLINE_STRESS_INDEX_READY, got {result.status}"
        )

        # We expect exactly 2 stress windows with overlapping eligible windows
        assert len(result.windows) == 2, (
            f"Expected 2 windows (overlapping eligible, both alive after cooldown), "
            f"got {len(result.windows)}: "
            f"{[w.trigger_timestamp_ns for w in result.windows]}"
        )

        # Trigger timestamps should be at 400s and 700s
        timestamps = sorted([w.trigger_timestamp_ns for w in result.windows])
        assert timestamps[0] == 400 * NS, (
            f"First trigger should be at 400s, got {timestamps[0] / NS}s"
        )
        assert timestamps[1] == 700 * NS, (
            f"Second trigger should be at 700s, got {timestamps[1] / NS}s"
        )

        # Both should have promotion allowed (causal mode)
        for w in result.windows:
            assert w.promotion_allowed is True, (
                f"Window at {w.trigger_timestamp_ns / NS}s should have promotion_allowed=True"
            )

        # Verify the windows have distinct window_ids (no double-counting)
        window_ids = [w.window_id for w in result.windows]
        assert len(set(window_ids)) == 2, "Windows should have distinct IDs (no merging)"

        # No suppressed triggers — intermediate points stay below threshold
        assert result.manifest_metadata.get("suppressed_trigger_count", -1) == 0, (
            f"Both windows should survive cooldown with zero suppression, but "
            f"suppressed_trigger_count={result.manifest_metadata.get('suppressed_trigger_count')}"
        )

    def test_overlapping_windows_with_suppression(self, tmp_path: Path) -> None:
        """Stress events where one intermediate trigger is suppressed by cooldown.

        Points at 500s have a price spike (115) that exceeds threshold, but
        is within 200s cooldown of the 400s trigger and thus suppressed.
        """
        trades = [
            _trade(0, 100.0),
            _trade(100, 100.0),
            _trade(200, 100.0),
            _trade(300, 100.0),
            _trade(400, 110.0),   # trigger 1: start=100, end=110 → 1000bps
            _trade(500, 115.0),   # triggers (1500bps) but 500-400=100 < 200 → suppressed
            _trade(600, 101.0),   # no trigger (low return)
            _trade(700, 120.0),   # trigger 2: start=100, end=120 → 2000bps, gap OK
        ]
        ds, _ = _dataset(tmp_path / "data", trades=trades)

        rule = StressRuleConfig(
            rule_name="rolling_absolute_return_bps",
            rule_version="v1",
            lookback_seconds=400,
            threshold_bps=500.0,
            cooldown_seconds=200,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade"),
            trigger_metric="absolute_return_bps",
        )

        result: StressWindowIndexResult = build_stress_window_index(
            ds, [rule], selection_mode=WINDOW_MODE_CAUSAL,
        )

        assert result.status == STATUS_OFFLINE_STRESS_INDEX_READY
        # Expect 2 windows (400s and 700s), 1 suppressed (500s)
        assert len(result.windows) == 2, (
            f"Expected 2 windows (400s and 700s), got {len(result.windows)}: "
            f"{[w.trigger_timestamp_ns for w in result.windows]}"
        )
        # 500s triggers but is within cooldown of 400s
        assert result.manifest_metadata.get("suppressed_trigger_count", -1) == 1, (
            f"Expected 1 suppressed trigger (500s), got "
            f"{result.manifest_metadata.get('suppressed_trigger_count')}"
        )

        timestamps = sorted([w.trigger_timestamp_ns for w in result.windows])
        assert timestamps[0] == 400 * NS
        assert timestamps[1] == 700 * NS

    def test_overlapping_windows_historical_parity(self, tmp_path: Path) -> None:
        """HISTORICAL-EQUIVALENCE PARITY: overlapping fixture via both paths.

        Runs the overlapping fixture through both the historical reference
        and the optimized path to confirm they agree.
        """
        trades = [
            _trade(0, 100.0),
            _trade(100, 100.0),
            _trade(200, 100.0),
            _trade(300, 100.0),
            _trade(400, 110.0),
            _trade(500, 112.0),
            _trade(600, 115.0),
            _trade(700, 120.0),
        ]
        ds, src = _dataset(tmp_path / "data", trades=trades)

        rule = StressRuleConfig(
            rule_name="rolling_range_bps",
            rule_version="v1",
            lookback_seconds=400,
            threshold_bps=500.0,
            cooldown_seconds=250,
            pre_window_seconds=60,
            post_window_seconds=300,
            min_required_points=2,
            supported_resolutions=("trade", "agg_trade"),
            trigger_metric="range_bps",
        )

        # Optimized path
        opt = build_stress_window_index(ds, [rule], selection_mode=WINDOW_MODE_CAUSAL)

        # Historical reference
        points = _points_for_fixture(trades)
        hist = _hist_evaluate_rule_on_points(points=points, source=src, rule=rule)

        # HISTORICAL-EQUIVALENCE PARITY
        assert len(opt.windows) == len(hist), (
            f"Overlapping fixture: historical={len(hist)} windows, "
            f"optimized={len(opt.windows)}"
        )
        for i, (ow, hw) in enumerate(zip(opt.windows, hist)):
            assert ow.trigger_timestamp_ns == hw["trigger_timestamp_ns"]
            assert abs(ow.trigger_value - hw["trigger_value"]) < 1e-9
