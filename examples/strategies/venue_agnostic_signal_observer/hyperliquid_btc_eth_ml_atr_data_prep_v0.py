"""
Hyperliquid BTC/ETH ML+ATR Data Prep v0 — Local Trade → 1h OHLCV Bar Builder
=============================================================================

Deterministic local data-prep module that:
1. Discovers and inspects local trade-level JSONL files.
2. Parses trade timestamps, symbols, prices, sizes.
3. Aggregates BTC and ETH trades into UTC-aware 1h OHLCV bars.
4. Normalizes existing funding archive into v0 funding schema.
5. Produces manifests, input hashes, coverage reports, gap reports.

NOT live trading. NOT network-connected. Local files only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SYMBOLS = frozenset({"BTC", "ETH"})
SPEC_VERSION = "data_prep_v0"

FORBIDDEN_STATUSES = {"TRADE_READY", "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE", "PROFITABLE"}

BARS_CSV_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
FUNDING_CSV_COLUMNS = ["timestamp", "symbol", "funding_rate"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataPrepConfig:
    trade_roots: Tuple[Path, ...] = ()
    trade_paths: Tuple[Path, ...] = ()
    funding_roots: Tuple[Path, ...] = ()
    funding_paths: Tuple[Path, ...] = ()
    output_root: Path = Path("reports/hyperliquid_btc_eth_ml_atr_data_prep_v0")
    run_id: Optional[str] = None
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    input_kind: str = "auto"  # auto, trades, bars
    max_gap_hours: int = 24
    max_abs_funding_rate: float = 0.01
    allow_zero_volume_bars: bool = True
    write_csv: bool = True
    write_parquet: bool = True
    dry_run: bool = False


@dataclass
class TradeSchemaGuess:
    file_path: str
    file_size: int
    first_keys: List[str]
    timestamp_field: Optional[str] = None
    timestamp_unit: str = "unknown"
    symbol_field: Optional[str] = None
    price_field: Optional[str] = None
    size_field: Optional[str] = None
    min_timestamp: Optional[str] = None
    max_timestamp: Optional[str] = None
    row_count: int = 0
    detected_symbols: List[str] = field(default_factory=list)


@dataclass
class FundingSchemaGuess:
    file_path: str
    file_size: int
    first_keys: List[str]
    timestamp_field: Optional[str] = None
    timestamp_unit: str = "unknown"
    symbol_field: Optional[str] = None
    funding_rate_field: Optional[str] = None
    min_timestamp: Optional[str] = None
    max_timestamp: Optional[str] = None
    row_count: int = 0
    detected_symbols: List[str] = field(default_factory=list)


@dataclass
class BarBuildSummary:
    status: str
    reason: str = ""
    bars_row_count: int = 0
    coverage_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gap_hours_by_symbol: Dict[str, int] = field(default_factory=dict)
    largest_gap_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ohlc_sanity_rejects: int = 0
    zero_volume_bar_count: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class FundingBuildSummary:
    status: str
    reason: str = ""
    funding_row_count: int = 0
    coverage_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gap_hours_by_symbol: Dict[str, int] = field(default_factory=dict)
    cadence_violations: int = 0
    abs_rate_rejects: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class DataPrepSummary:
    status: str
    reason: str = ""
    run_id: str = ""
    bars_summary: Optional[BarBuildSummary] = None
    funding_summary: Optional[FundingBuildSummary] = None
    trade_schema: Optional[TradeSchemaGuess] = None
    funding_schema: Optional[FundingSchemaGuess] = None
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_event_hash(data: dict) -> str:
    payload = {k: v for k, v in data.items() if k != "event_hash"}
    raw = json.dumps(payload, sort_keys=True, indent=2, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_json(path: Path, data: Any) -> None:
    dirpath = path.parent
    dirpath.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dirpath), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True, indent=2, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_csv(path: Path, df: pd.DataFrame, float_format: str = "%.8f") -> None:
    dirpath = path.parent
    dirpath.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dirpath), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            df.to_csv(f, index=False, float_format=float_format)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Symbol Normalization
# ---------------------------------------------------------------------------
def normalize_symbol(raw: str) -> Optional[str]:
    """Normalize raw symbol to BTC or ETH. Returns None if unknown."""
    raw_upper = raw.upper().strip()
    # Direct match
    if raw_upper in ("BTC", "ETH"):
        return raw_upper
    # Hyperliquid perp forms
    for prefix in ("BTC-PERP", "ETH-PERP", "BTC/USDC", "ETH/USDC"):
        if raw_upper == prefix:
            return prefix.split("-")[0].split("/")[0]
    # Binance/Kraken/Coinbase forms
    for prefix in ("BTC/USDT", "ETH/USDT", "BTC-USD", "ETH-USD", "BTCUSDT", "ETHUSDT"):
        if raw_upper == prefix:
            return "BTC" if "BTC" in raw_upper else "ETH"
    # Partial match
    if "BTC" in raw_upper:
        return "BTC"
    if "ETH" in raw_upper:
        return "ETH"
    return None


# ---------------------------------------------------------------------------
# Schema Inspection
# ---------------------------------------------------------------------------
def inspect_trade_jsonl(path: Path) -> TradeSchemaGuess:
    """Inspect first few lines of a trade JSONL to guess schema."""
    row_count = 0
    first_keys = []
    symbols = set()
    timestamps = []
    sizes = []

    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            row_count += 1
            if i == 0:
                first_keys = list(obj.keys())
            # Detect symbol
            for key in ("symbol", "coin", "instrument", "pair", "market"):
                if key in obj:
                    sym = str(obj[key])
                    norm = normalize_symbol(sym)
                    if norm:
                        symbols.add(norm)
            # Detect timestamp
            for key in ("ts_event", "timestamp", "time", "ts", "timestamp_ms", "created_at"):
                if key in obj:
                    val = obj[key]
                    if isinstance(val, (int, float)):
                        timestamps.append(val)
                    elif isinstance(val, str):
                        try:
                            pd.Timestamp(val)
                        except Exception:
                            pass
            # Detect size
            for key in ("size", "amount", "qty", "quantity", "base_amount"):
                if key in obj:
                    val = obj[key]
                    if isinstance(val, (int, float)):
                        sizes.append(val)
            if row_count >= 100:
                break

    # Guess fields
    ts_field = None
    ts_unit = "unknown"
    for key in ("ts_event", "timestamp_ms", "timestamp", "time", "ts"):
        if key in first_keys:
            ts_field = key
            if timestamps:
                val = timestamps[0]
                if isinstance(val, (int, float)):
                    if val > 1e18:
                        ts_unit = "nanoseconds"
                    elif val > 1e15:
                        ts_unit = "microseconds"
                    elif val > 1e12:
                        ts_unit = "milliseconds"
                    else:
                        ts_unit = "seconds"
            break

    sym_field = None
    for key in ("symbol", "coin", "instrument", "pair", "market"):
        if key in first_keys:
            sym_field = key
            break

    price_field = None
    for key in ("price", "px", "last_price", "close"):
        if key in first_keys:
            price_field = key
            break

    size_field = None
    for key in ("size", "amount", "qty", "quantity", "base_amount"):
        if key in first_keys:
            size_field = key
            break

    min_ts = min(timestamps) if timestamps else None
    max_ts = max(timestamps) if timestamps else None

    return TradeSchemaGuess(
        file_path=str(path),
        file_size=path.stat().st_size,
        first_keys=first_keys,
        timestamp_field=ts_field,
        timestamp_unit=ts_unit,
        symbol_field=sym_field,
        price_field=price_field,
        size_field=size_field,
        min_timestamp=str(min_ts) if min_ts else None,
        max_timestamp=str(max_ts) if max_ts else None,
        row_count=row_count,
        detected_symbols=sorted(symbols),
    )


def inspect_funding_jsonl(path: Path) -> FundingSchemaGuess:
    """Inspect first few lines of a funding JSONL to guess schema."""
    row_count = 0
    first_keys = []
    symbols = set()
    timestamps = []

    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            row_count += 1
            if i == 0:
                first_keys = list(obj.keys())
            for key in ("coin", "symbol", "asset"):
                if key in obj:
                    norm = normalize_symbol(str(obj[key]))
                    if norm:
                        symbols.add(norm)
            for key in ("timestamp_ms", "timestamp", "ts"):
                if key in obj and isinstance(obj[key], (int, float)):
                    timestamps.append(obj[key])
            if row_count >= 100:
                break

    ts_field = None
    ts_unit = "unknown"
    for key in ("timestamp_ms", "timestamp", "ts"):
        if key in first_keys:
            ts_field = key
            if timestamps:
                val = timestamps[0]
                if val > 1e15:
                    ts_unit = "milliseconds"
                elif val > 1e12:
                    ts_unit = "seconds"
            break

    sym_field = None
    for key in ("coin", "symbol", "asset"):
        if key in first_keys:
            sym_field = key
            break

    fr_field = None
    for key in ("funding_rate", "rate", "fr"):
        if key in first_keys:
            fr_field = key
            break

    min_ts = min(timestamps) if timestamps else None
    max_ts = max(timestamps) if timestamps else None

    return FundingSchemaGuess(
        file_path=str(path),
        file_size=path.stat().st_size,
        first_keys=first_keys,
        timestamp_field=ts_field,
        timestamp_unit=ts_unit,
        symbol_field=sym_field,
        funding_rate_field=fr_field,
        min_timestamp=str(min_ts) if min_ts else None,
        max_timestamp=str(max_ts) if max_ts else None,
        row_count=row_count,
        detected_symbols=sorted(symbols),
    )


# ---------------------------------------------------------------------------
# Trade Parsing
# ---------------------------------------------------------------------------
def parse_trade_jsonl(path: Path, schema: TradeSchemaGuess, config: DataPrepConfig) -> pd.DataFrame:
    """Parse a trade JSONL file into a DataFrame with normalized columns."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Normalize symbol
            raw_sym = str(obj.get(schema.symbol_field, ""))
            sym = normalize_symbol(raw_sym)
            if sym is None or sym not in config.symbols:
                continue
            # Parse timestamp
            ts_val = obj.get(schema.timestamp_field)
            if ts_val is None:
                continue
            if schema.timestamp_unit == "nanoseconds":
                ts = pd.Timestamp(ts_val, unit="ns", tz="UTC")
            elif schema.timestamp_unit == "microseconds":
                ts = pd.Timestamp(ts_val, unit="us", tz="UTC")
            elif schema.timestamp_unit == "milliseconds":
                ts = pd.Timestamp(ts_val, unit="ms", tz="UTC")
            elif schema.timestamp_unit == "seconds":
                ts = pd.Timestamp(ts_val, unit="s", tz="UTC")
            else:
                try:
                    ts = pd.Timestamp(ts_val, tz="UTC")
                except Exception:
                    continue
            # Parse price and size
            price = obj.get(schema.price_field)
            size = obj.get(schema.size_field)
            if price is None or size is None:
                continue
            try:
                price = float(price)
                size = float(size)
            except (ValueError, TypeError):
                continue
            if price <= 0 or size < 0:
                continue
            records.append({
                "timestamp": ts,
                "symbol": sym,
                "price": price,
                "size": size,
            })

    if not records:
        return pd.DataFrame(columns=["timestamp", "symbol", "price", "size"])

    df = pd.DataFrame(records)
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# OHLCV Aggregation
# ---------------------------------------------------------------------------
def build_ohlcv_1h(trades: pd.DataFrame, config: DataPrepConfig) -> pd.DataFrame:
    """Aggregate trades into 1h OHLCV bars."""
    if len(trades) == 0:
        return pd.DataFrame(columns=BARS_CSV_COLUMNS)

    # Bucket by symbol and hour
    trades = trades.copy()
    trades["hour"] = trades["timestamp"].dt.floor("h")

    bars = []
    for sym in sorted(trades["symbol"].unique()):
        sub = trades[trades["symbol"] == sym]
        for hour in sorted(sub["hour"].unique()):
            hour_trades = sub[sub["hour"] == hour].sort_values("timestamp")
            if len(hour_trades) == 0:
                continue
            open_price = float(hour_trades.iloc[0]["price"])
            high_price = float(hour_trades["price"].max())
            low_price = float(hour_trades["price"].min())
            close_price = float(hour_trades.iloc[-1]["price"])
            volume = float(hour_trades["size"].sum())
            bars.append({
                "timestamp": hour,
                "symbol": sym,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            })

    if not bars:
        return pd.DataFrame(columns=BARS_CSV_COLUMNS)

    df = pd.DataFrame(bars)
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df[BARS_CSV_COLUMNS]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_bars_for_ml_atr(bars: pd.DataFrame, config: DataPrepConfig) -> BarBuildSummary:
    """Validate bars meet v0 ML+ATR requirements."""
    if len(bars) == 0:
        return BarBuildSummary(status="DATA_PREP_V0_ERROR_NO_TRADE_INPUTS", reason="no_bars_produced")

    # Check symbols
    present = set(bars["symbol"].unique())
    missing = set(config.symbols) - present
    if missing:
        return BarBuildSummary(
            status="DATA_PREP_V0_ERROR_INSUFFICIENT_COVERAGE",
            reason=f"missing_symbols_{missing}",
        )

    # OHLC sanity
    mask = (
        (bars["high"] < bars["low"])
        | (bars["high"] < bars["open"])
        | (bars["high"] < bars["close"])
        | (bars["low"] > bars["open"])
        | (bars["low"] > bars["close"])
        | (bars["open"] <= 0) | (bars["high"] <= 0) | (bars["low"] <= 0) | (bars["close"] <= 0)
        | bars["open"].isna() | bars["high"].isna() | bars["low"].isna() | bars["close"].isna()
        | (bars["volume"] < 0) | bars["volume"].isna()
    )
    ohlc_rejects = int(mask.sum())

    # Zero-volume
    zero_vol = int((bars["volume"] == 0).sum())

    # Coverage and gaps per symbol
    coverage = {}
    gaps = {}
    for sym in sorted(bars["symbol"].unique()):
        sub = bars[bars["symbol"] == sym].sort_values("timestamp")
        if len(sub) == 0:
            continue
        min_ts = sub["timestamp"].min()
        max_ts = sub["timestamp"].max()
        n_bars = len(sub)
        coverage[sym] = {
            "min_timestamp": str(min_ts),
            "max_timestamp": str(max_ts),
            "bar_count": n_bars,
        }
        # Detect gaps
        if n_bars > 1:
            ts = sub["timestamp"].values
            diffs = np.diff(ts).astype("timedelta64[h]").astype(int)
            gap_mask = diffs > 1
            total_gap = int(diffs[gap_mask].sum() - gap_mask.sum()) if gap_mask.any() else 0
            gaps[sym] = total_gap
        else:
            gaps[sym] = 0

    # Check max gap
    for sym, gap_h in gaps.items():
        if gap_h > config.max_gap_hours:
            return BarBuildSummary(
                status="DATA_PREP_V0_ERROR_INSUFFICIENT_COVERAGE",
                reason=f"gap_hours_{sym}_{gap_h}_exceeds_{config.max_gap_hours}",
                coverage_by_symbol=coverage,
                gap_hours_by_symbol=gaps,
                ohlc_sanity_rejects=ohlc_rejects,
                zero_volume_bar_count=zero_vol,
            )

    return BarBuildSummary(
        status="DATA_PREP_V0_READY_FOR_ML_ATR",
        bars_row_count=len(bars),
        coverage_by_symbol=coverage,
        gap_hours_by_symbol=gaps,
        ohlc_sanity_rejects=ohlc_rejects,
        zero_volume_bar_count=zero_vol,
    )


def validate_funding_for_ml_atr(funding: pd.DataFrame, config: DataPrepConfig) -> FundingBuildSummary:
    """Validate funding meets v0 requirements."""
    if len(funding) == 0:
        return FundingBuildSummary(status="DATA_PREP_V0_ERROR_FUNDING_MISSING", reason="no_funding_rows")

    # Check symbols
    present = set(funding["symbol"].unique())
    missing = set(config.symbols) - present
    if missing:
        return FundingBuildSummary(
            status="DATA_PREP_V0_ERROR_FUNDING_INVALID",
            reason=f"missing_symbols_{missing}",
        )

    # Abs rate sanity
    abs_rejects = int((funding["funding_rate"].abs() > config.max_abs_funding_rate).sum())

    # Coverage per symbol
    coverage = {}
    gaps = {}
    for sym in sorted(funding["symbol"].unique()):
        sub = funding[funding["symbol"] == sym].sort_values("timestamp")
        if len(sub) == 0:
            continue
        min_ts = sub["timestamp"].min()
        max_ts = sub["timestamp"].max()
        coverage[sym] = {
            "min_timestamp": str(min_ts),
            "max_timestamp": str(max_ts),
            "row_count": len(sub),
        }
        # Detect gaps
        if len(sub) > 1:
            ts = sub["timestamp"].values
            diffs = np.diff(ts).astype("timedelta64[h]").astype(int)
            gap_mask = diffs > 1
            total_gap = int(diffs[gap_mask].sum() - gap_mask.sum()) if gap_mask.any() else 0
            gaps[sym] = total_gap
        else:
            gaps[sym] = 0

    return FundingBuildSummary(
        status="DATA_PREP_V0_READY_FOR_ML_ATR",
        funding_row_count=len(funding),
        coverage_by_symbol=coverage,
        gap_hours_by_symbol=gaps,
        abs_rate_rejects=abs_rejects,
    )


# ---------------------------------------------------------------------------
# Funding Parsing
# ---------------------------------------------------------------------------
def parse_funding_archive(path_or_dir: Path, config: DataPrepConfig) -> pd.DataFrame:
    """Parse Hyperliquid funding JSONL files into a normalized DataFrame."""
    files = []
    if path_or_dir.is_file():
        files = [path_or_dir]
    elif path_or_dir.is_dir():
        files = sorted(path_or_dir.glob("*.jsonl"))

    records = []
    for f in files:
        schema = inspect_funding_jsonl(f)
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw_sym = str(obj.get(schema.symbol_field or "coin", ""))
                sym = normalize_symbol(raw_sym)
                if sym is None or sym not in config.symbols:
                    continue
                ts_val = obj.get(schema.timestamp_field or "timestamp_ms")
                if ts_val is None:
                    continue
                if schema.timestamp_unit == "milliseconds":
                    ts = pd.Timestamp(ts_val, unit="ms", tz="UTC")
                elif schema.timestamp_unit == "seconds":
                    ts = pd.Timestamp(ts_val, unit="s", tz="UTC")
                else:
                    try:
                        ts = pd.Timestamp(ts_val, tz="UTC")
                    except Exception:
                        continue
                fr = obj.get(schema.funding_rate_field or "funding_rate")
                if fr is None:
                    continue
                try:
                    fr = float(fr)
                except (ValueError, TypeError):
                    continue
                records.append({
                    "timestamp": ts,
                    "symbol": sym,
                    "funding_rate": fr,
                })

    if not records:
        return pd.DataFrame(columns=FUNDING_CSV_COLUMNS)

    df = pd.DataFrame(records)
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df[FUNDING_CSV_COLUMNS]


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def run_data_prep(config: DataPrepConfig) -> DataPrepSummary:
    """Run the full data-prep pipeline."""
    run_id = config.run_id or f"data_prep_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    output_dir = config.output_root / run_id

    # Discover trade files
    trade_files = []
    for root in config.trade_roots:
        if root.is_dir():
            trade_files.extend(sorted(root.glob("**/*.jsonl")))
    for p in config.trade_paths:
        if p.is_file():
            trade_files.append(p)

    if not trade_files:
        return DataPrepSummary(
            status="DATA_PREP_V0_ERROR_NO_TRADE_INPUTS",
            reason="no_trade_files_found",
            run_id=run_id,
        )

    # Inspect trade schemas
    schemas = [inspect_trade_jsonl(f) for f in trade_files]

    # Parse all trades
    all_trades = []
    for schema in schemas:
        trades = parse_trade_jsonl(Path(schema.file_path), schema, config)
        if len(trades) > 0:
            all_trades.append(trades)

    if not all_trades:
        return DataPrepSummary(
            status="DATA_PREP_V0_ERROR_NO_TRADE_INPUTS",
            reason="no_btc_eth_trades_found",
            run_id=run_id,
            trade_schema=schemas[0] if schemas else None,
        )

    trades_df = pd.concat(all_trades, ignore_index=True)
    trades_df = trades_df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    # Build OHLCV bars
    bars = build_ohlcv_1h(trades_df, config)

    # Validate bars
    bars_summary = validate_bars_for_ml_atr(bars, config)

    # Discover funding files
    funding_files = []
    for root in config.funding_roots:
        if root.is_dir():
            funding_files.extend(sorted(root.glob("**/*.jsonl")))
    for p in config.funding_paths:
        if p.is_file():
            funding_files.append(p)

    # Parse funding
    funding_df = pd.DataFrame(columns=FUNDING_CSV_COLUMNS)
    funding_summary = FundingBuildSummary(status="DATA_PREP_V0_ERROR_FUNDING_MISSING", reason="no_funding_files")
    funding_schema = None

    if funding_files:
        funding_schema = inspect_funding_jsonl(funding_files[0])
        funding_df = parse_funding_archive(funding_files[0].parent, config)
        funding_summary = validate_funding_for_ml_atr(funding_df, config)

    # Overall status
    if bars_summary.status != "DATA_PREP_V0_READY_FOR_ML_ATR":
        status = bars_summary.status
        reason = bars_summary.reason
    elif funding_summary.status != "DATA_PREP_V0_READY_FOR_ML_ATR":
        status = funding_summary.status
        reason = funding_summary.reason
    else:
        status = "DATA_PREP_V0_READY_FOR_ML_ATR"
        reason = ""

    summary = DataPrepSummary(
        status=status,
        reason=reason,
        run_id=run_id,
        bars_summary=bars_summary,
        funding_summary=funding_summary,
        trade_schema=schemas[0] if schemas else None,
        funding_schema=funding_schema,
    )

    # Write outputs
    if not config.dry_run and status == "DATA_PREP_V0_READY_FOR_ML_ATR":
        output_dir.mkdir(parents=True, exist_ok=True)

        if config.write_csv:
            _atomic_write_csv(output_dir / "hyperliquid_btc_eth_1h_bars.csv", bars)
            _atomic_write_csv(output_dir / "hyperliquid_btc_eth_hourly_funding.csv", funding_df)

        if config.write_parquet:
            bars.to_parquet(output_dir / "hyperliquid_btc_eth_1h_bars.parquet", index=False)
            funding_df.to_parquet(output_dir / "hyperliquid_btc_eth_hourly_funding.parquet", index=False)

        # Summary JSON
        _atomic_write_json(output_dir / "summary.json", {
            "status": status,
            "reason": reason,
            "run_id": run_id,
            "bars_row_count": bars_summary.bars_row_count,
            "funding_row_count": funding_summary.funding_row_count,
            "coverage_by_symbol": bars_summary.coverage_by_symbol,
            "gap_hours_by_symbol": bars_summary.gap_hours_by_symbol,
            "warnings": summary.warnings,
        })

        # Summary MD
        lines = [
            "DO NOT USE FOR LIVE TRADING",
            "",
            f"Status: {status}",
        ]
        if reason:
            lines.append(f"Reason: {reason}")
        lines.extend([
            "",
            f"Run ID: {run_id}",
            f"Bars: {bars_summary.bars_row_count} rows",
            f"Funding: {funding_summary.funding_row_count} rows",
            "",
            "## Coverage",
        ])
        for sym, cov in bars_summary.coverage_by_symbol.items():
            lines.append(f"- {sym}: {cov['bar_count']} bars, {cov['min_timestamp']} to {cov['max_timestamp']}")
        lines.append("")
        lines.append("This does not authorize live, exchange-paper, bot, or order-routing execution.")
        (output_dir / "summary.md").write_text("\n".join(lines) + "\n")

        # Manifest
        _atomic_write_json(output_dir / "manifest.json", {
            "spec_version": SPEC_VERSION,
            "run_id": run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_files": [s.file_path for s in schemas],
            "output_bars_csv": str(output_dir / "hyperliquid_btc_eth_1h_bars.csv"),
            "output_bars_parquet": str(output_dir / "hyperliquid_btc_eth_1h_bars.parquet"),
            "output_funding_csv": str(output_dir / "hyperliquid_btc_eth_hourly_funding.csv"),
            "output_funding_parquet": str(output_dir / "hyperliquid_btc_eth_hourly_funding.parquet"),
            "bars_row_count": bars_summary.bars_row_count,
            "funding_row_count": funding_summary.funding_row_count,
            "coverage_by_symbol": bars_summary.coverage_by_symbol,
            "gap_hours_by_symbol": bars_summary.gap_hours_by_symbol,
            "ohlc_sanity_rejects": bars_summary.ohlc_sanity_rejects,
            "zero_volume_bar_count": bars_summary.zero_volume_bar_count,
            "safety_flags": {
                "local_files_only": True,
                "no_network": True,
                "no_orders": True,
                "no_auth": True,
                "no_live_execution": True,
            },
        })

        # INPUTS.md
        input_lines = ["# Input Files", ""]
        for s in schemas:
            input_lines.extend([
                f"## {Path(s.file_path).name}",
                f"- Path: `{s.file_path}`",
                f"- Rows: {s.row_count}",
                "",
            ])
        (output_dir / "INPUTS.md").write_text("\n".join(input_lines) + "\n")

    return summary
