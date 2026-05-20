"""Day-by-day Parquet streaming helpers for archive evaluation.

The helpers in this module intentionally keep raw tick residency bounded to a
single symbol-day plus a small continuity tail. Stress labels / return rows are
small enough to accumulate; raw ticks are not.
"""

from __future__ import annotations

import math
import random
import re
import resource
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

SEED = 42
NS_PER_SECOND = 1_000_000_000
DEFAULT_MEMORY_GUARD_LIMIT_BYTES = 8 * 1024**3
_FILENAME_RE = re.compile(
    r"^(?P<symbol_a>[A-Za-z0-9]+)_(?P<date_a>\d{4}-\d{2}-\d{2})_aggTrades\.parquet$"
    r"|^(?P<symbol_b>[A-Za-z0-9]+)_aggTrades_(?P<date_b>\d{4}-\d{2}-\d{2})\.parquet$"
)


class ArchiveStreamingMemoryGuardTriggered(RuntimeError):
    """Raised when archive streaming RSS exceeds the hard guard."""


@dataclass(frozen=True)
class SymbolDayTicks:
    symbol: str
    date: str
    timestamps: np.ndarray
    prices: np.ndarray
    sizes: np.ndarray
    is_buyer_maker: np.ndarray
    path: Path

    @property
    def tick_count(self) -> int:
        return int(len(self.timestamps))

    @property
    def first_ts(self) -> int | None:
        return int(self.timestamps[0]) if len(self.timestamps) else None

    @property
    def last_ts(self) -> int | None:
        return int(self.timestamps[-1]) if len(self.timestamps) else None


@dataclass
class RuleStreamState:
    rule_name: str
    lookback_seconds: int
    threshold_bps: float
    window: deque[tuple[int, float]]
    last_label_ts: int | None
    rng: random.Random


@dataclass(frozen=True)
class StreamingProgress:
    symbol: str
    date: str
    tick_count: int
    labels_added: int
    rss_bytes: int
    path: Path


def current_rss_bytes() -> int:
    """Return ru_maxrss as bytes on Linux."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def format_rss(bytes_value: int) -> str:
    return f"{bytes_value / (1024**3):.3f} GiB"


def check_archive_streaming_memory_guard(
    *,
    phase: str,
    symbol: str | None = None,
    date: str | None = None,
    limit_bytes: int = DEFAULT_MEMORY_GUARD_LIMIT_BYTES,
) -> int:
    rss = current_rss_bytes()
    if rss > limit_bytes:
        detail = (
            "ARCHIVE_STREAMING_MEMORY_GUARD_TRIGGERED "
            f"rss={format_rss(rss)} limit={format_rss(limit_bytes)} phase={phase} "
            f"symbol={symbol or 'NA'} date={date or 'NA'}"
        )
        raise ArchiveStreamingMemoryGuardTriggered(detail)
    return rss


def _filename_parts(path: Path) -> tuple[str, str] | None:
    match = _FILENAME_RE.match(path.name)
    if not match:
        return None
    symbol = match.group("symbol_a") or match.group("symbol_b")
    date = match.group("date_a") or match.group("date_b")
    if symbol is None or date is None:
        return None
    return symbol.upper(), date


def symbol_day_parquet_files(
    parquet_dir: Path,
    symbol: str,
    *,
    candidate_dates: set[str] | None = None,
) -> list[Path]:
    """Find supported symbol-day aggTrades parquet files in flat or per-symbol layout."""
    symbol_upper = symbol.upper()
    symbol_lower = symbol.lower()
    search_roots = [parquet_dir / symbol_lower, parquet_dir / symbol_upper, parquet_dir / symbol, parquet_dir]
    files: list[Path] = []
    seen: set[Path] = set()
    patterns = (
        f"{symbol_upper}_*_aggTrades.parquet",
        f"{symbol_lower}_*_aggTrades.parquet",
        f"{symbol.upper()}_*_aggTrades.parquet",
        f"{symbol.lower()}_*_aggTrades.parquet",
        f"{symbol_upper}_aggTrades_*.parquet",
        f"{symbol_lower}_aggTrades_*.parquet",
        f"{symbol.upper()}_aggTrades_*.parquet",
        f"{symbol.lower()}_aggTrades_*.parquet",
    )
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                parts = _filename_parts(path)
                if parts is None:
                    continue
                path_symbol, path_date = parts
                if path_symbol != symbol_upper:
                    continue
                if candidate_dates is not None and path_date not in candidate_dates:
                    continue
                files.append(path)
                seen.add(resolved)
    return sorted(files, key=lambda p: _filename_parts(p)[1] if _filename_parts(p) else p.name)


def date_from_parquet_path(path: Path) -> str:
    parts = _filename_parts(path)
    if parts is None:
        raise ValueError(f"Unexpected parquet filename: {path.name}")
    return parts[1]


def read_symbol_day_parquet(path: Path) -> SymbolDayTicks:
    """Read exactly one symbol-day Parquet file and extract required columns."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    parts = _filename_parts(path)
    if parts is None:
        raise ValueError(f"Unexpected parquet filename: {path.name}")
    symbol, date = parts
    table = pq.read_table(path, columns=["ts_event", "price", "size", "is_buyer_maker"])
    ts = table["ts_event"].to_numpy(zero_copy_only=False).astype(np.int64, copy=False)
    prices = table["price"].to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
    sizes = table["size"].to_numpy(zero_copy_only=False).astype(np.float32, copy=False)
    maker = table["is_buyer_maker"].to_numpy(zero_copy_only=False).astype(np.bool_, copy=False)
    mask = np.isfinite(prices) & (prices > 0)
    if not bool(mask.all()):
        ts = ts[mask]
        prices = prices[mask]
        sizes = sizes[mask]
        maker = maker[mask]
    if len(ts) > 1 and bool(np.any(ts[1:] < ts[:-1])):
        order = np.argsort(ts, kind="stable")
        ts = ts[order]
        prices = prices[order]
        sizes = sizes[order]
        maker = maker[order]
    return SymbolDayTicks(
        symbol=symbol,
        date=date,
        timestamps=ts,
        prices=prices,
        sizes=sizes,
        is_buyer_maker=maker,
        path=path,
    )


def iter_symbol_day_parquet(
    parquet_dir: Path,
    symbol: str,
    *,
    candidate_dates: set[str] | None = None,
) -> Iterator[SymbolDayTicks]:
    for path in symbol_day_parquet_files(parquet_dir, symbol, candidate_dates=candidate_dates):
        yield read_symbol_day_parquet(path)


def _tail_arrays(day: SymbolDayTicks, tail_ns: int) -> tuple[np.ndarray, np.ndarray]:
    if len(day.timestamps) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)
    cutoff = int(day.timestamps[-1]) - tail_ns
    start = int(np.searchsorted(day.timestamps, cutoff, side="left"))
    return day.timestamps[start:].copy(), day.prices[start:].copy()


def _combined_ticks(prior_tail: tuple[np.ndarray, np.ndarray], day: SymbolDayTicks) -> tuple[np.ndarray, np.ndarray]:
    tail_ts, tail_pr = prior_tail
    if len(tail_ts) == 0:
        return day.timestamps, day.prices
    if len(day.timestamps) == 0:
        return tail_ts, tail_pr
    ts = np.concatenate([tail_ts, day.timestamps])
    pr = np.concatenate([tail_pr, day.prices])
    if len(ts) > 1 and bool(np.any(ts[1:] < ts[:-1])):
        order = np.argsort(ts, kind="stable")
        ts = ts[order]
        pr = pr[order]
    return ts, pr


def _process_rule_day(
    state: RuleStreamState,
    day: SymbolDayTicks,
    source_symbol: str,
    StressLabel: Any,
) -> list[Any]:
    labels: list[Any] = []
    lookback_ns = state.lookback_seconds * NS_PER_SECOND
    for ts_val, price_val in zip(day.timestamps, day.prices):
        ts_ns = int(ts_val)
        price = float(price_val)
        state.window.append((ts_ns, price))
        cutoff = ts_ns - lookback_ns
        while state.window and state.window[0][0] < cutoff:
            state.window.popleft()
        if len(state.window) < 2:
            continue
        start_ts, start_price = state.window[0]
        move_bps = ((price - start_price) / start_price) * 10_000 if start_price else 0.0
        if abs(move_bps) < state.threshold_bps:
            continue
        if state.last_label_ts is not None and (ts_ns - state.last_label_ts) < 30 * NS_PER_SECOND:
            continue
        direction = "bullish" if move_bps > 0 else "bearish"
        window_rand = state.rng.randint(0, 999999)
        label_rand = state.rng.randint(0, 999999)
        labels.append(StressLabel(
            label_id=f"sl_{source_symbol}_{state.lookback_seconds}s_{ts_ns}_{label_rand:06d}",
            source_symbol=source_symbol,
            stress_start_ns=int(start_ts),
            stress_end_ns=ts_ns,
            stress_window_seconds=state.lookback_seconds,
            source_move_bps=float(move_bps),
            direction=direction,
            source_start_price=float(start_price),
            source_end_price=price,
            independent_window_id=f"iw_{source_symbol}_{state.lookback_seconds}s_{int(start_ts)}_{ts_ns}_{window_rand:06d}",
            rule_name=state.rule_name,
        ))
        state.last_label_ts = ts_ns
    return labels


def generate_stress_labels_for_symbol_days(
    day_iter: Iterable[SymbolDayTicks],
    source_symbol: str,
    stress_rules: Sequence[tuple[str, int, float]],
    *,
    StressLabel: Any,
    progress_prefix: str = "",
    memory_guard_limit_bytes: int = DEFAULT_MEMORY_GUARD_LIMIT_BYTES,
) -> tuple[list[Any], list[StreamingProgress]]:
    states = [
        RuleStreamState(
            rule_name=rule_name,
            lookback_seconds=lookback_seconds,
            threshold_bps=threshold_bps,
            window=deque(),
            last_label_ts=None,
            rng=random.Random(SEED),
        )
        for rule_name, lookback_seconds, threshold_bps in stress_rules
    ]
    labels: list[Any] = []
    progress: list[StreamingProgress] = []
    max_lookback_ns = max((rule[1] for rule in stress_rules), default=0) * NS_PER_SECOND

    for day in day_iter:
        rss = check_archive_streaming_memory_guard(
            phase="source_loading_or_stress_label_generation",
            symbol=source_symbol,
            date=day.date,
            limit_bytes=memory_guard_limit_bytes,
        )
        before = len(labels)
        for state in states:
            labels.extend(_process_rule_day(state, day, source_symbol, StressLabel))
            cutoff = int(day.timestamps[-1]) - max_lookback_ns if len(day.timestamps) else None
            if cutoff is not None:
                while state.window and state.window[0][0] < cutoff:
                    state.window.popleft()
        added = len(labels) - before
        progress.append(StreamingProgress(source_symbol, day.date, day.tick_count, added, rss, day.path))
        if progress_prefix:
            print(
                f"{progress_prefix} {source_symbol} {day.date}: ticks={day.tick_count} labels_added={added} rss={format_rss(rss)}",
                flush=True,
            )
    return labels, progress


def check_coverage_from_ts_prices(
    timestamps: Sequence[int],
    entry_ns: int,
    horizon_end_ns: int,
) -> bool:
    if len(timestamps) == 0:
        return False
    entry_idx = int(np.searchsorted(timestamps, entry_ns, side="left"))
    if entry_idx >= len(timestamps):
        return False
    horizon_idx = int(np.searchsorted(timestamps, horizon_end_ns, side="left"))
    return horizon_idx < len(timestamps)


def compute_forward_returns_from_ts_prices(
    stress_label: Any,
    timestamps: Sequence[int],
    prices: Sequence[float],
    target_symbol: str,
    horizons_ms: Sequence[int],
    *,
    TickForwardReturn: Any,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
) -> list[Any]:
    entry_ts_ns = int(stress_label.stress_end_ns) + ENTRY_DELAY_NS
    results: list[Any] = []
    entry_idx = int(np.searchsorted(timestamps, entry_ts_ns, side="left"))
    if entry_idx >= len(timestamps):
        for horizon_ms in horizons_ms:
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=None,
                forward_price=None,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="no_entry_reference_price",
            ))
        return results

    entry_price = float(prices[entry_idx])
    total_cost = FEE_BPS + SLIPPAGE_BPS + QUOTE_MISMATCH_BUFFER_BPS
    for horizon_ms in horizons_ms:
        horizon_ns = entry_ts_ns + horizon_ms * MS_TO_NS
        exit_idx = int(np.searchsorted(timestamps, horizon_ns, side="left"))
        if exit_idx >= len(timestamps):
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=None,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="no_forward_price",
            ))
            continue
        forward_price = float(prices[exit_idx])
        if entry_price <= 0 or not math.isfinite(entry_price) or not math.isfinite(forward_price):
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=forward_price,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="non_finite_or_zero_prices",
            ))
            continue
        raw_return_bps = ((forward_price - entry_price) / entry_price) * 10_000
        adjusted_return = raw_return_bps if stress_label.direction == "bullish" else -raw_return_bps
        results.append(TickForwardReturn(
            signal_id=stress_label.label_id,
            signal_ts=entry_ts_ns,
            target_venue=VENUE,
            target_symbol=target_symbol,
            horizon_ms=horizon_ms,
            entry_reference_price=entry_price,
            forward_price=forward_price,
            raw_return_bps=raw_return_bps,
            direction_adjusted_return_bps=adjusted_return,
            fee_bps=FEE_BPS,
            slippage_bps=SLIPPAGE_BPS,
            quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
            net_return_bps=adjusted_return - total_cost,
            valid=True,
        ))
    return results


def stream_target_coverage(
    parquet_dir: Path,
    target_symbols: Sequence[str],
    labels: Sequence[Any],
    *,
    max_horizon_ns: int,
    entry_delay_ns: int,
    candidate_dates: set[str] | None = None,
    memory_guard_limit_bytes: int = DEFAULT_MEMORY_GUARD_LIMIT_BYTES,
) -> tuple[list[Any], dict[str, Any], list[StreamingProgress]]:
    coverage_by_label: dict[str, set[str]] = {label.label_id: set() for label in labels}
    progress: list[StreamingProgress] = []
    for symbol in target_symbols:
        prior_tail = (np.array([], dtype=np.int64), np.array([], dtype=np.float64))
        for day in iter_symbol_day_parquet(parquet_dir, symbol, candidate_dates=candidate_dates):
            rss = check_archive_streaming_memory_guard(phase="target_coverage", symbol=symbol, date=day.date, limit_bytes=memory_guard_limit_bytes)
            combined_ts, _combined_pr = _combined_ticks(prior_tail, day)
            day_first = day.first_ts
            day_last = day.last_ts
            available = 0
            if day_first is not None and day_last is not None:
                for label in labels:
                    entry_ns = int(label.stress_end_ns) + entry_delay_ns
                    horizon_end = entry_ns + max_horizon_ns
                    # Mark on the day containing the entry timestamp. The combined
                    # array includes the prior-day tail, and the next day will catch
                    # entries near midnight when their horizon extends forward.
                    if not (day_first <= entry_ns <= day_last):
                        continue
                    if check_coverage_from_ts_prices(combined_ts, entry_ns, horizon_end):
                        coverage_by_label[label.label_id].add(symbol)
                        available += 1
            progress.append(StreamingProgress(symbol, day.date, day.tick_count, available, rss, day.path))
            print(f"  target coverage {symbol} {day.date}: ticks={day.tick_count} covered_labels={available} rss={format_rss(rss)}", flush=True)
            prior_tail = _tail_arrays(day, max_horizon_ns + entry_delay_ns)
    usable = [label for label in labels if coverage_by_label[label.label_id] == set(target_symbols)]
    summary = {
        "per_target": {
            symbol: {
                "available_count": sum(1 for covered in coverage_by_label.values() if symbol in covered),
                "total_checked": len(labels),
            }
            for symbol in target_symbols
        },
        "total_labels": len(labels),
        "usable_labels": len(usable),
        "all_target_windows": len({label.independent_window_id for label in usable}),
    }
    return usable, summary, progress


def stream_forward_returns_for_target(
    parquet_dir: Path,
    target_symbol: str,
    labels: Sequence[Any],
    horizons_ms: Sequence[int],
    *,
    TickForwardReturn: Any,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
    candidate_dates: set[str] | None = None,
    memory_guard_limit_bytes: int = DEFAULT_MEMORY_GUARD_LIMIT_BYTES,
) -> Iterator[tuple[Any, list[Any]]]:
    max_horizon_ns = max(horizons_ms) * MS_TO_NS
    prior_tail = (np.array([], dtype=np.int64), np.array([], dtype=np.float64))
    pending: list[Any] = []
    labels_sorted = sorted(labels, key=lambda label: int(label.stress_end_ns) + ENTRY_DELAY_NS)
    label_idx = 0
    for day in iter_symbol_day_parquet(parquet_dir, target_symbol, candidate_dates=candidate_dates):
        rss = check_archive_streaming_memory_guard(phase="target_forward_returns", symbol=target_symbol, date=day.date, limit_bytes=memory_guard_limit_bytes)
        if day.first_ts is None or day.last_ts is None:
            continue
        while label_idx < len(labels_sorted) and int(labels_sorted[label_idx].stress_end_ns) + ENTRY_DELAY_NS <= int(day.last_ts):
            pending.append(labels_sorted[label_idx])
            label_idx += 1
        combined_ts, combined_pr = _combined_ticks(prior_tail, day)
        still_pending: list[Any] = []
        emitted = 0
        for label in pending:
            entry_ns = int(label.stress_end_ns) + ENTRY_DELAY_NS
            horizon_end = entry_ns + max_horizon_ns
            if horizon_end > int(day.last_ts):
                still_pending.append(label)
                continue
            emitted += 1
            yield label, compute_forward_returns_from_ts_prices(
                label,
                combined_ts,
                combined_pr,
                target_symbol,
                horizons_ms,
                TickForwardReturn=TickForwardReturn,
                VENUE=VENUE,
                ENTRY_DELAY_NS=ENTRY_DELAY_NS,
                MS_TO_NS=MS_TO_NS,
                FEE_BPS=FEE_BPS,
                SLIPPAGE_BPS=SLIPPAGE_BPS,
                QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
            )
        pending = still_pending
        print(f"  target forward {target_symbol} {day.date}: ticks={day.tick_count} emitted_labels={emitted} pending={len(pending)} rss={format_rss(rss)}", flush=True)
        prior_tail = _tail_arrays(day, max_horizon_ns + ENTRY_DELAY_NS)



def generate_baseline_from_day_stream(
    day_iter: Iterable[SymbolDayTicks] | Any,
    source_symbol: str,
    label_count: int,
    *,
    seed: int,
    TickForwardReturn: Any,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
    HORIZONS_MS: Sequence[int],
) -> list[Any]:
    """Generate a deterministic baseline while streaming target days.

    This avoids concatenating every target day. It preserves the prior helper's
    core semantics: deterministic pseudo-random entry timestamps within the
    available target range, then at-or-after tick lookup for each horizon.
    """
    days = day_iter if isinstance(day_iter, list) else list(day_iter)
    if not days or label_count <= 0:
        return []
    non_empty = [day for day in days if len(day.timestamps)]
    if not non_empty:
        return []
    min_ts = min(int(day.timestamps[0]) for day in non_empty)
    max_ts = max(int(day.timestamps[-1]) for day in non_empty)
    rng = random.Random(seed)
    requested_entries = sorted(rng.randint(min_ts, max_ts) for _ in range(label_count))
    baseline_frs: list[Any] = []
    total_cost = FEE_BPS + SLIPPAGE_BPS + QUOTE_MISMATCH_BUFFER_BPS
    max_horizon_ns = max(HORIZONS_MS) * MS_TO_NS
    prior_tail = (np.array([], dtype=np.int64), np.array([], dtype=np.float64))
    req_idx = 0
    pending: list[tuple[int, int]] = []

    for day in days:
        if day.first_ts is None or day.last_ts is None:
            continue
        while req_idx < len(requested_entries) and requested_entries[req_idx] <= int(day.last_ts):
            pending.append((req_idx, requested_entries[req_idx]))
            req_idx += 1
        combined_ts, combined_pr = _combined_ticks(prior_tail, day)
        still_pending: list[tuple[int, int]] = []
        for i, requested_ts in pending:
            entry_idx = int(np.searchsorted(combined_ts, requested_ts, side="left"))
            if entry_idx >= len(combined_ts):
                continue
            actual_entry_ts = int(combined_ts[entry_idx])
            if actual_entry_ts + max_horizon_ns > int(day.last_ts):
                still_pending.append((i, requested_ts))
                continue
            entry_price = float(combined_pr[entry_idx])
            if entry_price <= 0 or not math.isfinite(entry_price):
                continue
            for horizon_ms in HORIZONS_MS:
                horizon_ns = actual_entry_ts + horizon_ms * MS_TO_NS
                exit_idx = int(np.searchsorted(combined_ts, horizon_ns, side="left"))
                if exit_idx >= len(combined_ts):
                    continue
                forward_price = float(combined_pr[exit_idx])
                if not math.isfinite(forward_price):
                    continue
                raw_return = ((forward_price - entry_price) / entry_price) * 10_000
                baseline_frs.append(TickForwardReturn(
                    signal_id=f"baseline_{source_symbol}_{i}_{horizon_ms}",
                    signal_ts=actual_entry_ts,
                    target_venue=VENUE,
                    target_symbol="BASELINE",
                    horizon_ms=horizon_ms,
                    entry_reference_price=entry_price,
                    forward_price=forward_price,
                    raw_return_bps=raw_return,
                    direction_adjusted_return_bps=raw_return,
                    fee_bps=FEE_BPS,
                    slippage_bps=SLIPPAGE_BPS,
                    quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                    net_return_bps=raw_return - total_cost,
                    valid=True,
                ))
        pending = still_pending
        prior_tail = _tail_arrays(day, max_horizon_ns + ENTRY_DELAY_NS)
    return baseline_frs

def collect_parquet_inventory(
    parquet_dir: Path,
    symbols: Sequence[str],
    *,
    candidate_dates: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        files = symbol_day_parquet_files(parquet_dir, symbol, candidate_dates=candidate_dates)
        inventory[symbol] = {
            "file_count": len(files),
            "first_date": date_from_parquet_path(files[0]) if files else None,
            "last_date": date_from_parquet_path(files[-1]) if files else None,
            "files": [str(path) for path in files],
        }
    return inventory
