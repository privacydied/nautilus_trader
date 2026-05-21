"""
Local-file parsers for Binance, Kraken, and Coinbase historical files.

Observer-only. No network calls, no auth, no order paths.

Supported formats
-----------------
Binance spot:
  aggTrades CSV  — columns: agg_trade_id, price, qty, first_trade_id,
                             last_trade_id, transact_time, is_buyer_maker
  klines CSV     — columns: open_time, open, high, low, close, volume,
                             close_time, quote_asset_volume, number_of_trades,
                             taker_buy_base_asset_volume,
                             taker_buy_quote_asset_volume, ignore
Kraken:
  OHLCVT CSV     — columns: timestamp, open, high, low, close, vwap, volume,
                             count  (epoch seconds)
  time-and-sales — columns: timestamp, price, volume  (epoch seconds)

Coinbase:
  candles CSV    — columns: start, low, high, open, close, volume
  simple export  — columns: time, price, size, side
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import List
from typing import Sequence

from .offline_historical_models import RESOLUTION_AGG_TRADE
from .offline_historical_models import RESOLUTION_BAR
from .offline_historical_models import RESOLUTION_TRADE
from .offline_historical_models import OfflineBarRecord
from .offline_historical_models import OfflineTradeRecord
from .offline_historical_normalize import to_nanoseconds
from .offline_historical_normalize import validate_timestamp_range


# ---------------------------------------------------------------------------
# Supported source kinds
# ---------------------------------------------------------------------------

SUPPORTED_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "binance_spot_agg_trades",
        "binance_spot_klines",
        "binance_um_agg_trades",
        "binance_um_klines",
        "kraken_ohlcvt",
        "kraken_trades",
        "coinbase_candles",
        "coinbase_trades",
    }
)


def assert_known_source_kind(source_kind: str) -> None:
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        raise ValueError(
            f"Unknown source_kind {source_kind!r}. "
            f"Supported: {sorted(SUPPORTED_SOURCE_KINDS)}"
        )


# ---------------------------------------------------------------------------
# Parse result
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    trades: List[OfflineTradeRecord]
    bars: List[OfflineBarRecord]
    row_count: int
    data_start_ns: int
    data_end_ns: int
    was_sorted: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_csv(path: str | Path) -> List[List[str]]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # strip blank trailing rows
    return [r for r in rows if any(c.strip() for c in r)]


def _ts_range(timestamps_ns: Sequence[int]) -> tuple[int, int]:
    return min(timestamps_ns), max(timestamps_ns)


# ---------------------------------------------------------------------------
# Binance aggTrades
# ---------------------------------------------------------------------------


def parse_binance_agg_trades(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    source_kind: str,
    logical_source_id: str,
    timestamp_unit: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Binance aggTrades CSV file.

    Expected CSV header (optional):
      agg_trade_id, price, qty, first_trade_id, last_trade_id,
      transact_time, is_buyer_maker

    Timestamp column: transact_time (index 5 if no header, else by name).
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    # Detect header row
    first = rows[0]
    if first[0].strip().lstrip("#").strip().lower() in (
        "agg_trade_id", "aggTradeid", "aggregate_tradeId".lower()
    ):
        data_rows = rows[1:]
        col_time = 5
        col_price = 1
        col_qty = 2
        col_maker = 6
    else:
        data_rows = rows
        col_time = 5
        col_price = 1
        col_qty = 2
        col_maker = 6

    records: List[OfflineTradeRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 7:
            continue
        raw_ts = float(row[col_time].strip())
        ts_ns = to_nanoseconds(raw_ts, timestamp_unit)
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        is_maker = row[col_maker].strip().lower() in ("true", "1")
        side = "sell" if is_maker else "buy"

        records.append(
            OfflineTradeRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                timestamp_ns=ts_ns,
                price=float(row[col_price].strip()),
                size=float(row[col_qty].strip()),
                side=side,
                trade_id=row[0].strip(),
                source_file=logical_source_id,
                source_kind=source_kind,
                resolution_type=RESOLUTION_AGG_TRADE,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=records,
        bars=[],
        row_count=len(records),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Binance klines
# ---------------------------------------------------------------------------


def parse_binance_klines(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    source_kind: str,
    logical_source_id: str,
    timestamp_unit: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Binance klines CSV file.

    Expected CSV columns (no header):
      open_time, open, high, low, close, volume, close_time,
      quote_asset_volume, number_of_trades, taker_buy_base_asset_volume,
      taker_buy_quote_asset_volume, ignore
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    # Skip optional header
    if rows[0][0].strip().lower() in ("open_time", "opentime"):
        data_rows = rows[1:]
    else:
        data_rows = rows

    bars: List[OfflineBarRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 6:
            continue
        raw_ts = float(row[0].strip())
        ts_ns = to_nanoseconds(raw_ts, timestamp_unit)
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        trade_count = int(row[8].strip()) if len(row) > 8 else None

        bars.append(
            OfflineBarRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                timestamp_ns=ts_ns,
                open=float(row[1].strip()),
                high=float(row[2].strip()),
                low=float(row[3].strip()),
                close=float(row[4].strip()),
                volume=float(row[5].strip()),
                trade_count=trade_count,
                source_file=logical_source_id,
                source_kind=source_kind,
                resolution_type=RESOLUTION_BAR,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=[],
        bars=bars,
        row_count=len(bars),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Kraken OHLCVT
# ---------------------------------------------------------------------------


def parse_kraken_ohlcvt(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    logical_source_id: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Kraken OHLCVT CSV file.

    Expected columns: timestamp(s), open, high, low, close, vwap, volume, count

    Timestamps are in UNIX seconds (integer or float).
    quote_asset is preserved exactly — USD and USDT are never collapsed.
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    # Skip optional header
    if rows[0][0].strip().lower() in ("timestamp", "time"):
        data_rows = rows[1:]
    else:
        data_rows = rows

    bars: List[OfflineBarRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 7:
            continue
        raw_ts = float(row[0].strip())
        ts_ns = to_nanoseconds(raw_ts, "s")
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        trade_count = int(row[7].strip()) if len(row) > 7 else None

        bars.append(
            OfflineBarRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,    # preserved exactly
                timestamp_ns=ts_ns,
                open=float(row[1].strip()),
                high=float(row[2].strip()),
                low=float(row[3].strip()),
                close=float(row[4].strip()),
                volume=float(row[6].strip()),
                trade_count=trade_count,
                source_file=logical_source_id,
                source_kind="kraken_ohlcvt",
                resolution_type=RESOLUTION_BAR,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=[],
        bars=bars,
        row_count=len(bars),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Kraken time-and-sales
# ---------------------------------------------------------------------------


def parse_kraken_trades(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    logical_source_id: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Kraken historical trades CSV.

    Expected columns: timestamp(s), price, volume
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    if rows[0][0].strip().lower() in ("timestamp", "time"):
        data_rows = rows[1:]
    else:
        data_rows = rows

    records: List[OfflineTradeRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 3:
            continue
        raw_ts = float(row[0].strip())
        ts_ns = to_nanoseconds(raw_ts, "s")
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        records.append(
            OfflineTradeRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                timestamp_ns=ts_ns,
                price=float(row[1].strip()),
                size=float(row[2].strip()),
                side=None,
                trade_id=None,
                source_file=logical_source_id,
                source_kind="kraken_trades",
                resolution_type=RESOLUTION_TRADE,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=records,
        bars=[],
        row_count=len(records),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Coinbase candles
# ---------------------------------------------------------------------------


def parse_coinbase_candles(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    logical_source_id: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Coinbase candles CSV.

    Expected columns: start(s), low, high, open, close, volume

    Venue identity and quote_asset are preserved exactly.
    Coinbase BTC-USD is a USD reference stream; not treated as same-engine
    as Binance or Kraken.
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    if rows[0][0].strip().lower() in ("start", "time", "timestamp"):
        data_rows = rows[1:]
    else:
        data_rows = rows

    bars: List[OfflineBarRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 6:
            continue
        raw_ts = float(row[0].strip())
        ts_ns = to_nanoseconds(raw_ts, "s")
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        bars.append(
            OfflineBarRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                timestamp_ns=ts_ns,
                open=float(row[3].strip()),
                high=float(row[2].strip()),
                low=float(row[1].strip()),
                close=float(row[4].strip()),
                volume=float(row[5].strip()),
                trade_count=None,
                source_file=logical_source_id,
                source_kind="coinbase_candles",
                resolution_type=RESOLUTION_BAR,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=[],
        bars=bars,
        row_count=len(bars),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Coinbase simple trades export
# ---------------------------------------------------------------------------


def parse_coinbase_trades(
    path: str | Path,
    venue: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    logical_source_id: str,
    expected_start_ns: int,
    expected_end_ns: int,
    tolerance_ns: int = 86_400_000_000_000,
) -> ParseResult:
    """
    Parse a Coinbase simple CSV trade export.

    Expected columns: time(s), price, size, side
    """
    rows = _open_csv(path)
    if not rows:
        return ParseResult([], [], 0, 0, 0, False)

    if rows[0][0].strip().lower() in ("time", "timestamp"):
        data_rows = rows[1:]
    else:
        data_rows = rows

    records: List[OfflineTradeRecord] = []
    timestamps_ns: List[int] = []

    for row in data_rows:
        if len(row) < 3:
            continue
        raw_ts = float(row[0].strip())
        ts_ns = to_nanoseconds(raw_ts, "s")
        validate_timestamp_range(ts_ns, expected_start_ns, expected_end_ns, tolerance_ns)

        side = row[3].strip().lower() if len(row) > 3 else None

        records.append(
            OfflineTradeRecord(
                venue=venue,
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                timestamp_ns=ts_ns,
                price=float(row[1].strip()),
                size=float(row[2].strip()),
                side=side or None,
                trade_id=None,
                source_file=logical_source_id,
                source_kind="coinbase_trades",
                resolution_type=RESOLUTION_TRADE,
            )
        )
        timestamps_ns.append(ts_ns)

    if not timestamps_ns:
        return ParseResult([], [], 0, 0, 0, False)

    was_sorted = _check_and_note_sorting(timestamps_ns)
    start_ns, end_ns = _ts_range(timestamps_ns)

    return ParseResult(
        trades=records,
        bars=[],
        row_count=len(records),
        data_start_ns=start_ns,
        data_end_ns=end_ns,
        was_sorted=was_sorted,
    )


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _check_and_note_sorting(timestamps_ns: List[int]) -> bool:
    return any(timestamps_ns[i] < timestamps_ns[i - 1] for i in range(1, len(timestamps_ns)))
