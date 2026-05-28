"""
Hyperliquid BTC/ETH ML+ATR SonarX L2 Midbar V0 — L2 Snapshot → 1h Midquote Bars
================================================================================

Derives 1h midquote OHLC bars from SonarX public Hyperliquid L2 summary snapshots.
NOT trade OHLCV. NOT live trading. S3 archive only.

Statuses:
  SONARX_L2_MIDBAR_V0_PLAN_READY
  SONARX_L2_MIDBAR_V0_READY_FOR_DIAGNOSTIC
  SONARX_L2_MIDBAR_V0_NEEDS_MORE_DATA
  SONARX_L2_MIDBAR_V0_BLOCKED_AWS_CLI_MISSING
  SONARX_L2_MIDBAR_V0_BLOCKED_REQUESTER_PAYS
  SONARX_L2_MIDBAR_V0_BLOCKED_S3_LIST_FAILED
  SONARX_L2_MIDBAR_V0_BLOCKED_COST_OR_SIZE_CAP
  SONARX_L2_MIDBAR_V0_BLOCKED_SCHEMA_UNRECOGNIZED
  SONARX_L2_MIDBAR_V0_BLOCKED_PARSE_FAILED
  SONARX_L2_MIDBAR_V0_BLOCKED_INSUFFICIENT_COVERAGE
  SONARX_L2_MIDBAR_V0_ERROR_INVALID_OUTPUT
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import subprocess
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
SPEC_VERSION = "sonarx_l2_midbar_v0"
STUDY_ID = "hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_v0"
SOURCE_KIND = "SONARX_L2_SUMMARY_MIDQUOTE"

DEFAULT_S3_PREFIXES = {
    "BTC": "s3://sonarx-hyperliquid-public/market_data/perp/BTC/l2-summary-snapshots",
    "ETH": "s3://sonarx-hyperliquid-public/market_data/perp/ETH/l2-summary-snapshots",
}

FORBIDDEN_STATUSES = {"TRADE_READY", "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE", "PROFITABLE"}

BARS_CSV_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
RICH_CSV_COLUMNS = [
    "timestamp", "symbol", "open", "high", "low", "close", "volume",
    "snapshot_count", "mean_spread_bps", "median_spread_bps",
    "mean_bid_depth_top20", "mean_ask_depth_top20",
    "mean_bid_notional_top20", "mean_ask_notional_top20", "source_kind",
]
FUNDING_CSV_COLUMNS = ["timestamp", "symbol", "funding_rate"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SonarXL2Config:
    output_root: Path = Path("reports/hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_v0")
    run_id: Optional[str] = None
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    s3_prefixes: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_S3_PREFIXES))
    funding_root: Path = Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0")
    max_download_gb: float = 25.0
    plan_only: bool = False
    sample_only: bool = False
    execute: bool = False
    keep_raw: bool = False
    request_payer: bool = True
    max_objects: Optional[int] = None
    max_gap_hours: int = 24
    min_total_bars_per_symbol: int = 3000
    write_csv: bool = True
    write_parquet: bool = True
    dry_run: bool = False


@dataclass
class SonarXS3Object:
    key: str
    size: int
    last_modified: str = ""
    etag: str = ""
    symbol: str = ""
    partition: str = ""


@dataclass
class SonarXDownloadPlan:
    status: str
    reason: str = ""
    objects: List[SonarXS3Object] = field(default_factory=list)
    total_bytes: int = 0
    total_gb: float = 0.0
    earliest_date: str = ""
    latest_date: str = ""
    prefixes_inspected: List[str] = field(default_factory=list)


@dataclass
class SonarXSchemaProbe:
    status: str
    reason: str = ""
    sample_file: str = ""
    sample_size: int = 0
    record_count: int = 0
    fields: List[str] = field(default_factory=list)
    market_examples: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class SonarXSnapshot:
    height: int
    block_time: datetime
    market: str
    best_bid: float
    best_ask: float
    mid: float
    spread_bps: float
    bid_depth_top20: float
    ask_depth_top20: float
    bid_notional_top20: float
    ask_notional_top20: float
    raw_bids: List[Dict[str, Any]] = field(default_factory=list)
    raw_asks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SonarXMidbarSummary:
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    snapshot_count: int = 0
    mean_spread_bps: float = 0.0
    median_spread_bps: float = 0.0
    mean_bid_depth_top20: float = 0.0
    mean_ask_depth_top20: float = 0.0
    mean_bid_notional_top20: float = 0.0
    mean_ask_notional_top20: float = 0.0
    source_kind: str = SOURCE_KIND


@dataclass
class SonarXMidbarRunSummary:
    status: str
    reason: str = ""
    run_id: str = ""
    plan: Optional[SonarXDownloadPlan] = None
    schema: Optional[SonarXSchemaProbe] = None
    coverage_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gaps_by_symbol: Dict[str, List[str]] = field(default_factory=dict)
    snapshot_counts: Dict[str, int] = field(default_factory=dict)
    rejected_snapshot_counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool Checks
# ---------------------------------------------------------------------------
def check_aws_cli() -> bool:
    """Check if AWS CLI is available."""
    try:
        result = subprocess.run(["aws", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# S3 Listing
# ---------------------------------------------------------------------------
def _aws_cmd(args: List[str], request_payer: bool = True) -> subprocess.CompletedProcess:
    """Run AWS CLI command with requester-pays."""
    cmd = ["aws"] + args
    if request_payer:
        cmd.extend(["--request-payer", "requester"])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def list_sonarx_objects(
    symbols: Tuple[str, ...],
    prefixes: Dict[str, str],
    request_payer: bool = True,
) -> List[SonarXS3Object]:
    """List SonarX L2 summary snapshot objects in S3."""
    objects = []
    for symbol in symbols:
        if symbol not in prefixes:
            logger.warning("No S3 prefix for symbol %s", symbol)
            continue
        prefix = prefixes[symbol]
        # Parse s3://bucket/path
        if not prefix.startswith("s3://"):
            logger.error("Invalid S3 prefix: %s", prefix)
            continue
        parts = prefix[5:].split("/", 1)
        bucket = parts[0]
        s3_path = parts[1] if len(parts) > 1 else ""
        
        # List objects (paginated)
        continuation_token = None
        while True:
            cmd = ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", s3_path]
            if continuation_token:
                cmd.extend(["--continuation-token", continuation_token])
            result = _aws_cmd(cmd, request_payer)
            if result.returncode != 0:
                logger.error("Failed to list S3 objects for %s: %s", symbol, result.stderr)
                break
            
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                logger.error("Failed to parse S3 listing for %s", symbol)
                break
            
            for obj in data.get("Contents", []):
                key = obj["Key"]
                # Skip directory markers
                if key.endswith("/"):
                    continue
                # Extract partition from key
                partition = ""
                if "date=" in key:
                    partition = key.split("date=")[1].split("/")[0]
                elif "height=" in key:
                    partition = key.split("height=")[1].split("/")[0]
                
                objects.append(SonarXS3Object(
                    key=f"s3://{bucket}/{key}",
                    size=obj.get("Size", 0),
                    last_modified=obj.get("LastModified", ""),
                    etag=obj.get("ETag", "").strip('"'),
                    symbol=symbol,
                    partition=partition,
                ))
            
            if data.get("IsTruncated"):
                continuation_token = data.get("NextContinuationToken")
            else:
                break
    
    return objects


def parse_aws_s3_ls_output(output: str, symbol: str) -> List[SonarXS3Object]:
    """Parse AWS S3 ls output into SonarXS3Object list."""
    objects = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        # AWS S3 ls format: "2026-03-16 16:11:17      12585 885811000.json.gz"
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        date_str, time_str, size_str, key = parts[0], parts[1], parts[2], parts[3]
        # Skip directory markers
        if key.endswith("/"):
            continue
        try:
            size = int(size_str)
        except ValueError:
            continue
        # Ensure key has s3:// prefix for consistency
        if not key.startswith("s3://"):
            key = f"s3://sonarx-hyperliquid-public/{key}"
        objects.append(SonarXS3Object(
            key=key,
            size=size,
            last_modified=f"{date_str} {time_str}",
            symbol=symbol,
        ))
    return objects


def infer_partition_or_height_from_key(key: str) -> str:
    """Extract partition or height from S3 key."""
    if "date=" in key:
        return key.split("date=")[1].split("/")[0]
    if "height=" in key:
        return key.split("height=")[1].split("/")[0]
    # Try to extract from filename
    filename = key.split("/")[-1]
    if ".json.gz" in filename:
        return filename.replace(".json.gz", "")
    return ""


# ---------------------------------------------------------------------------
# Download Plan
# ---------------------------------------------------------------------------
def build_download_plan(
    objects: List[SonarXS3Object],
    max_download_gb: float,
) -> SonarXDownloadPlan:
    """Build download plan from listed objects."""
    total_bytes = sum(obj.size for obj in objects)
    total_gb = total_bytes / (1024**3)
    
    if total_gb > max_download_gb:
        return SonarXDownloadPlan(
            status="BLOCKED_SONARX_COST_OR_SIZE_CAP",
            reason=f"Total size {total_gb:.2f} GB exceeds cap {max_download_gb:.2f} GB",
            objects=objects,
            total_bytes=total_bytes,
            total_gb=total_gb,
        )
    
    # Sort by partition/key for deterministic ordering
    sorted_objects = sorted(objects, key=lambda o: (o.symbol, o.partition, o.key))
    
    return SonarXDownloadPlan(
        status="SONARX_L2_MIDBAR_V0_PLAN_READY",
        objects=sorted_objects,
        total_bytes=total_bytes,
        total_gb=total_gb,
    )


# ---------------------------------------------------------------------------
# Schema Probe
# ---------------------------------------------------------------------------
def sample_sonarx_schema(
    objects: List[SonarXS3Object],
    max_sample_bytes: int = 1024 * 1024,  # 1 MB
) -> SonarXSchemaProbe:
    """Sample SonarX schema by downloading a small portion."""
    if not objects:
        return SonarXSchemaProbe(
            status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
            reason="No objects to sample",
        )
    
    # Pick first small object
    small_objects = sorted(objects, key=lambda o: o.size)[:5]
    if not small_objects:
        return SonarXSchemaProbe(
            status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
            reason="No small objects found",
        )
    
    sample_obj = small_objects[0]
    sample_size = min(sample_obj.size, max_sample_bytes)
    
    # Download sample
    try:
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tmp:
            tmp_path = tmp.name
        
        # Download full file (AWS CLI doesn't support --range for s3 cp)
        cmd = [
            "aws", "s3", "cp",
            sample_obj.key,
            tmp_path,
            "--request-payer", "requester",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            return SonarXSchemaProbe(
                status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
                reason=f"Failed to download sample: {result.stderr}",
            )
        
        # Parse sample
        with gzip.open(tmp_path, "rt") as f:
            data = json.load(f)
        
        if not isinstance(data, list) or len(data) == 0:
            return SonarXSchemaProbe(
                status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
                reason="Sample is not a non-empty JSON array",
            )
        
        first_record = data[0]
        fields = list(first_record.keys())
        market_examples = list({r.get("market", "") for r in data[:10]})
        
        return SonarXSchemaProbe(
            status="SONARX_L2_MIDBAR_V0_SCHEMA_RECOGNIZED",
            sample_file=sample_obj.key,
            sample_size=sample_size,
            record_count=len(data),
            fields=fields,
            market_examples=market_examples,
        )
    
    except Exception as e:
        return SonarXSchemaProbe(
            status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
            reason=f"Failed to parse sample: {e}",
        )
    
    finally:
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Download & Parse
# ---------------------------------------------------------------------------
def download_object_atomic(
    obj: SonarXS3Object,
    output_dir: Path,
    request_payer: bool = True,
) -> Optional[Path]:
    """Download a single S3 object atomically."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = obj.key.split("/")[-1]
    output_path = output_dir / filename
    tmp_path = output_path.with_suffix(".tmp")
    
    cmd = [
        "aws", "s3", "cp",
        obj.key,
        str(tmp_path),
        "--request-payer", "requester" if request_payer else "no-requester",
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error("Failed to download %s: %s", obj.key, result.stderr)
        return None
    
    tmp_path.rename(output_path)
    return output_path


def parse_snapshot_file(file_path: Path) -> List[SonarXSnapshot]:
    """Parse a gzipped JSON array of SonarX L2 summary snapshots."""
    snapshots = []
    
    try:
        with gzip.open(file_path, "rt") as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            logger.error("File %s is not a JSON array", file_path)
            return []
        
        for i, record in enumerate(data):
            snapshot = parse_snapshot_record(record, index=i)
            if snapshot is not None:
                snapshots.append(snapshot)
    
    except Exception as e:
        logger.error("Failed to parse %s: %s", file_path, e)
    
    return snapshots


def parse_snapshot_record(record: Dict[str, Any], index: int = 0) -> Optional[SonarXSnapshot]:
    """Parse a single SonarX snapshot record."""
    try:
        # Extract fields
        height = record.get("height", 0)
        block_time_str = record.get("block_time", "")
        market = record.get("market", "")
        bids = record.get("bids", [])
        asks = record.get("asks", [])
        
        # Validate
        if not bids or not asks:
            return None
        
        # Parse block_time
        if isinstance(block_time_str, str):
            # Try ISO format
            try:
                block_time = datetime.fromisoformat(block_time_str.replace("Z", "+00:00"))
            except ValueError:
                # Try epoch seconds
                try:
                    block_time = datetime.fromtimestamp(float(block_time_str), tz=timezone.utc)
                except (ValueError, OSError):
                    return None
        elif isinstance(block_time_str, (int, float)):
            block_time = datetime.fromtimestamp(block_time_str, tz=timezone.utc)
        else:
            return None
        
        # Normalize market
        normalized_market = normalize_market(market)
        if normalized_market is None:
            return None
        
        # Extract top-of-book
        best_bid, best_ask, bid_depth, ask_depth, bid_notional, ask_notional = snapshot_to_top_of_book(bids, asks)
        
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            return None
        
        mid = (best_bid + best_ask) / 2
        spread_bps = (best_ask - best_bid) / mid * 10000
        
        return SonarXSnapshot(
            height=height,
            block_time=block_time,
            market=normalized_market,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread_bps=spread_bps,
            bid_depth_top20=bid_depth,
            ask_depth_top20=ask_depth,
            bid_notional_top20=bid_notional,
            ask_notional_top20=ask_notional,
            raw_bids=bids[:20],
            raw_asks=asks[:20],
        )
    
    except Exception as e:
        logger.debug("Failed to parse snapshot record %d: %s", index, e)
        return None


def normalize_market(market: str) -> Optional[str]:
    """Normalize market name to standard symbol."""
    if not market:
        return None
    
    market_upper = market.upper()
    
    # Standard perp BTC
    if market_upper in ("BTC", "BTC-PERP", "BTC_USDC"):
        return "BTC"
    
    # Standard perp ETH
    if market_upper in ("ETH", "ETH-PERP", "ETH_USDC"):
        return "ETH"
    
    # HIP-3 markets (e.g., hyna:BTC) - not standard perp
    if ":" in market_upper:
        return None
    
    # LINK and others - reject
    return None


def snapshot_to_top_of_book(
    bids: List[Dict[str, Any]],
    asks: List[Dict[str, Any]],
) -> Tuple[float, float, float, float, float, float]:
    """Extract top-of-book metrics from bids and asks."""
    if not bids or not asks:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    
    # Validate bid ordering (descending by px)
    bid_prices = [float(b.get("px", 0)) for b in bids if b.get("px")]
    ask_prices = [float(a.get("px", 0)) for a in asks if a.get("px")]
    
    if not bid_prices or not ask_prices:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    
    # Sort bids descending, asks ascending
    bids_sorted = sorted(bids, key=lambda x: float(x.get("px", 0)), reverse=True)
    asks_sorted = sorted(asks, key=lambda x: float(x.get("px", 0)))
    
    best_bid = float(bids_sorted[0].get("px", 0))
    best_ask = float(asks_sorted[0].get("px", 0))
    
    # Sum top 20 levels
    bid_depth = sum(float(b.get("sz", 0)) for b in bids_sorted[:20])
    ask_depth = sum(float(a.get("sz", 0)) for a in asks_sorted[:20])
    
    bid_notional = sum(float(b.get("px", 0)) * float(b.get("sz", 0)) for b in bids_sorted[:20])
    ask_notional = sum(float(a.get("px", 0)) * float(a.get("sz", 0)) for a in asks_sorted[:20])
    
    return best_bid, best_ask, bid_depth, ask_depth, bid_notional, ask_notional


# ---------------------------------------------------------------------------
# 1h Aggregation
# ---------------------------------------------------------------------------
def aggregate_snapshots_to_1h_midbars(
    snapshots: List[SonarXSnapshot],
) -> List[SonarXMidbarSummary]:
    """Aggregate snapshots into 1h midquote bars."""
    if not snapshots:
        return []
    
    # Group by symbol and hour
    df = pd.DataFrame([
        {
            "timestamp": s.block_time,
            "symbol": s.market,
            "mid": s.mid,
            "spread_bps": s.spread_bps,
            "bid_depth_top20": s.bid_depth_top20,
            "ask_depth_top20": s.ask_depth_top20,
            "bid_notional_top20": s.bid_notional_top20,
            "ask_notional_top20": s.ask_notional_top20,
        }
        for s in snapshots
    ])
    
    # Floor to hour
    df["hour"] = df["timestamp"].dt.floor("h")
    
    # Group by symbol and hour
    midbars = []
    for (symbol, hour), group in df.groupby(["symbol", "hour"]):
        if len(group) == 0:
            continue
        
        # Sort by timestamp within hour
        group = group.sort_values("timestamp")
        
        midbars.append(SonarXMidbarSummary(
            timestamp=hour,
            symbol=symbol,
            open=group["mid"].iloc[0],
            high=group["mid"].max(),
            low=group["mid"].min(),
            close=group["mid"].iloc[-1],
            volume=0.0,  # Placeholder
            snapshot_count=len(group),
            mean_spread_bps=group["spread_bps"].mean(),
            median_spread_bps=group["spread_bps"].median(),
            mean_bid_depth_top20=group["bid_depth_top20"].mean(),
            mean_ask_depth_top20=group["ask_depth_top20"].mean(),
            mean_bid_notional_top20=group["bid_notional_top20"].mean(),
            mean_ask_notional_top20=group["ask_notional_top20"].mean(),
            source_kind=SOURCE_KIND,
        ))
    
    return midbars


def validate_midbars(midbars: List[SonarXMidbarSummary]) -> List[str]:
    """Validate midbars and return warnings."""
    warnings = []
    
    for mb in midbars:
        if mb.high < mb.low:
            warnings.append(f"Invalid OHLC at {mb.timestamp} {mb.symbol}: high < low")
        if mb.open < mb.low or mb.open > mb.high:
            warnings.append(f"Invalid OHLC at {mb.timestamp} {mb.symbol}: open outside high-low range")
        if mb.close < mb.low or mb.close > mb.high:
            warnings.append(f"Invalid OHLC at {mb.timestamp} {mb.symbol}: close outside high-low range")
        if mb.snapshot_count == 0:
            warnings.append(f"Zero snapshots at {mb.timestamp} {mb.symbol}")
    
    return warnings


# ---------------------------------------------------------------------------
# Funding Normalization
# ---------------------------------------------------------------------------
def normalize_existing_funding(
    funding_root: Path,
    symbols: Tuple[str, ...],
) -> pd.DataFrame:
    """Normalize existing funding archive into v0 funding schema."""
    funding_dfs = []
    
    for symbol in symbols:
        # Look for funding files
        pattern = f"*{symbol}*funding*.csv"
        funding_files = list(funding_root.glob(pattern))
        
        if not funding_files:
            logger.warning("No funding files found for %s in %s", symbol, funding_root)
            continue
        
        for fpath in funding_files:
            try:
                df = pd.read_csv(fpath)
                # Ensure required columns
                required_cols = {"timestamp", "symbol", "funding_rate"}
                if not required_cols.issubset(df.columns):
                    # Try to normalize
                    if "ts" in df.columns and "rate" in df.columns:
                        df = df.rename(columns={"ts": "timestamp", "rate": "funding_rate"})
                        df["symbol"] = symbol
                
                if required_cols.issubset(df.columns):
                    funding_dfs.append(df[["timestamp", "symbol", "funding_rate"]])
            except Exception as e:
                logger.warning("Failed to load funding from %s: %s", fpath, e)
    
    if funding_dfs:
        return pd.concat(funding_dfs, ignore_index=True)
    else:
        return pd.DataFrame(columns=FUNDING_CSV_COLUMNS)


# ---------------------------------------------------------------------------
# Output Writing
# ---------------------------------------------------------------------------
def write_outputs_atomic(
    midbars: List[SonarXMidbarSummary],
    funding_df: pd.DataFrame,
    output_dir: Path,
    write_csv: bool = True,
    write_parquet: bool = True,
) -> List[Path]:
    """Write midbar and funding outputs atomically."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files = []
    
    # Convert midbars to DataFrames
    bars_data = []
    rich_data = []
    for mb in midbars:
        bars_row = {
            "timestamp": mb.timestamp,
            "symbol": mb.symbol,
            "open": mb.open,
            "high": mb.high,
            "low": mb.low,
            "close": mb.close,
            "volume": mb.volume,
        }
        bars_data.append(bars_row)
        
        rich_row = {
            "timestamp": mb.timestamp,
            "symbol": mb.symbol,
            "open": mb.open,
            "high": mb.high,
            "low": mb.low,
            "close": mb.close,
            "volume": mb.volume,
            "snapshot_count": mb.snapshot_count,
            "mean_spread_bps": mb.mean_spread_bps,
            "median_spread_bps": mb.median_spread_bps,
            "mean_bid_depth_top20": mb.mean_bid_depth_top20,
            "mean_ask_depth_top20": mb.mean_ask_depth_top20,
            "mean_bid_notional_top20": mb.mean_bid_notional_top20,
            "mean_ask_notional_top20": mb.mean_ask_notional_top20,
            "source_kind": mb.source_kind,
        }
        rich_data.append(rich_row)
    
    bars_df = pd.DataFrame(bars_data)
    rich_df = pd.DataFrame(rich_data)
    
    # Write bars
    if write_parquet:
        bars_path = output_dir / "hyperliquid_btc_eth_sonarx_l2_mid_1h_bars.parquet"
        bars_df.to_parquet(bars_path, index=False)
        written_files.append(bars_path)
    
    if write_csv:
        bars_csv_path = output_dir / "hyperliquid_btc_eth_sonarx_l2_mid_1h_bars.csv"
        bars_df.to_csv(bars_csv_path, index=False)
        written_files.append(bars_csv_path)
    
    # Write rich
    if write_parquet:
        rich_path = output_dir / "hyperliquid_btc_eth_sonarx_l2_mid_1h_rich.parquet"
        rich_df.to_parquet(rich_path, index=False)
        written_files.append(rich_path)
    
    if write_csv:
        rich_csv_path = output_dir / "hyperliquid_btc_eth_sonarx_l2_mid_1h_rich.csv"
        rich_df.to_csv(rich_csv_path, index=False)
        written_files.append(rich_csv_path)
    
    # Write funding
    if write_parquet:
        funding_path = output_dir / "hyperliquid_btc_eth_hourly_funding.parquet"
        funding_df.to_parquet(funding_path, index=False)
        written_files.append(funding_path)
    
    if write_csv:
        funding_csv_path = output_dir / "hyperliquid_btc_eth_hourly_funding.csv"
        funding_df.to_csv(funding_csv_path, index=False)
        written_files.append(funding_csv_path)
    
    return written_files


def write_manifest(
    output_dir: Path,
    run_summary: SonarXMidbarRunSummary,
    config: SonarXL2Config,
    output_files: List[Path],
    git_sha: str = "",
) -> Path:
    """Write manifest.json with all metadata."""
    manifest = {
        "spec_version": SPEC_VERSION,
        "run_id": run_summary.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "bucket_prefixes": list(config.s3_prefixes.values()),
        "object_count": len(run_summary.plan.objects) if run_summary.plan else 0,
        "total_planned_download_bytes": run_summary.plan.total_bytes if run_summary.plan else 0,
        "requester_pays": config.request_payer,
        "max_download_gb": config.max_download_gb,
        "coverage_by_symbol": run_summary.coverage_by_symbol,
        "gaps_by_symbol": run_summary.gaps_by_symbol,
        "snapshot_counts": run_summary.snapshot_counts,
        "rejected_snapshot_counts": run_summary.rejected_snapshot_counts,
        "placeholder_volume": True,
        "source_kind": SOURCE_KIND,
        "output_files": {
            f.name: {
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                "size_bytes": f.stat().st_size,
            }
            for f in output_files if f.exists()
        },
        "warnings": run_summary.warnings,
    }
    
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    
    return manifest_path


def write_inputs_md(
    output_dir: Path,
    run_summary: SonarXMidbarRunSummary,
    config: SonarXL2Config,
) -> Path:
    """Write INPUTS.md documenting data sources."""
    content = f"""# INPUTS

## Data Sources

### SonarX L2 Summary Snapshots
- Bucket: s3://sonarx-hyperliquid-public
- Prefixes:
"""
    for symbol, prefix in config.s3_prefixes.items():
        content += f"  - {symbol}: {prefix}\n"
    
    content += f"""
- Source Kind: {SOURCE_KIND}
- Requester Pays: {config.request_payer}
- Max Download GB: {config.max_download_gb}

### Funding
- Root: {config.funding_root}
- Source: Hyperliquid funding archive

## Coverage
"""
    for symbol, coverage in run_summary.coverage_by_symbol.items():
        content += f"- {symbol}: {coverage.get('start', 'N/A')} to {coverage.get('end', 'N/A')} ({coverage.get('bar_count', 0)} bars)\n"
    
    content += f"""
## Warnings
"""
    for warning in run_summary.warnings:
        content += f"- {warning}\n"
    
    inputs_path = output_dir / "INPUTS.md"
    with open(inputs_path, "w") as f:
        f.write(content)
    
    return inputs_path


def write_summary(
    output_dir: Path,
    run_summary: SonarXMidbarRunSummary,
    config: SonarXL2Config,
) -> Path:
    """Write summary.md and summary.json."""
    # JSON summary
    summary_dict = {
        "status": run_summary.status,
        "reason": run_summary.reason,
        "run_id": run_summary.run_id,
        "spec_version": SPEC_VERSION,
        "source_kind": SOURCE_KIND,
        "quote_derived": True,
        "traded_ohlcv": False,
        "placeholder_volume": True,
        "coverage_by_symbol": run_summary.coverage_by_symbol,
        "gaps_by_symbol": run_summary.gaps_by_symbol,
        "snapshot_counts": run_summary.snapshot_counts,
        "rejected_snapshot_counts": run_summary.rejected_snapshot_counts,
        "warnings": run_summary.warnings,
    }
    
    summary_json_path = output_dir / "summary.json"
    with open(summary_json_path, "w") as f:
        json.dump(summary_dict, f, indent=2, default=str)
    
    # Markdown summary
    content = f"""DO NOT USE FOR LIVE TRADING

Status: {run_summary.status}
Reason: {run_summary.reason or "N/A"}

## Data Source
- Source Kind: {SOURCE_KIND}
- Quote-derived midbar, not trade OHLCV
- Placeholder volume = 0.0

## Coverage
"""
    for symbol, coverage in run_summary.coverage_by_symbol.items():
        content += f"### {symbol}\n"
        content += f"- Start: {coverage.get('start', 'N/A')}\n"
        content += f"- End: {coverage.get('end', 'N/A')}\n"
        content += f"- Unique hourly bars: {coverage.get('bar_count', 0)}\n"
        content += f"- Snapshot count: {run_summary.snapshot_counts.get(symbol, 0)}\n"
        content += f"- Rejected snapshots: {run_summary.rejected_snapshot_counts.get(symbol, 0)}\n"
    
    content += """
## Gaps
"""
    for symbol, gaps in run_summary.gaps_by_symbol.items():
        content += f"- {symbol}: {len(gaps)} gap hours\n"
    
    content += """
## Funding Coverage
- Source: Hyperliquid funding archive
- Mapped to hourly bars

## Authorization
- NOT authorized for live trading
- NOT authorized for exchange-paper
- NOT authorized for bot/order routing
- Local simulated-paper only if diagnostic passes

## Warnings
"""
    for warning in run_summary.warnings:
        content += f"- {warning}\n"
    
    summary_md_path = output_dir / "summary.md"
    with open(summary_md_path, "w") as f:
        f.write(content)
    
    return summary_md_path


def write_gaps(
    output_dir: Path,
    gaps_by_symbol: Dict[str, List[str]],
) -> Path:
    """Write gaps.json."""
    gaps_path = output_dir / "gaps.json"
    with open(gaps_path, "w") as f:
        json.dump(gaps_by_symbol, f, indent=2)
    return gaps_path


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def run_sonarx_l2_midbar_pipeline(
    config: SonarXL2Config,
) -> SonarXMidbarRunSummary:
    """Run the SonarX L2 midbar pipeline."""
    run_id = config.run_id or f"sonarx_midbars_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir = config.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    warnings = []
    
    # Check AWS CLI
    if not check_aws_cli():
        return SonarXMidbarRunSummary(
            status="BLOCKED_SONARX_AWS_CLI_MISSING",
            reason="AWS CLI not found or not working",
            run_id=run_id,
        )
    
    # Dry run
    if config.dry_run:
        return SonarXMidbarRunSummary(
            status="SONARX_L2_MIDBAR_V0_DRY_RUN_OK",
            reason="AWS CLI available, dry run completed",
            run_id=run_id,
        )
    
    # List objects
    objects = list_sonarx_objects(config.symbols, config.s3_prefixes, config.request_payer)
    if not objects:
        return SonarXMidbarRunSummary(
            status="BLOCKED_SONARX_S3_LIST_FAILED",
            reason="No objects found in S3 prefixes",
            run_id=run_id,
        )
    
    # Apply max objects limit
    if config.max_objects and len(objects) > config.max_objects:
        objects = objects[:config.max_objects]
        warnings.append(f"Truncated to {config.max_objects} objects")
    
    # Build download plan
    plan = build_download_plan(objects, config.max_download_gb)
    if plan.status != "SONARX_L2_MIDBAR_V0_PLAN_READY":
        return SonarXMidbarRunSummary(
            status=plan.status,
            reason=plan.reason,
            run_id=run_id,
            plan=plan,
        )
    
    # Plan only
    if config.plan_only:
        # Write plan outputs
        plan_dir = output_dir
        plan_dir.mkdir(parents=True, exist_ok=True)
        
        # Write download_plan.json
        with open(plan_dir / "download_plan.json", "w") as f:
            json.dump(asdict(plan), f, indent=2, default=str)
        
        # Write s3_inventory.json
        with open(plan_dir / "s3_inventory.json", "w") as f:
            json.dump([asdict(o) for o in objects], f, indent=2, default=str)
        
        # Compute coverage
        coverage_by_symbol = {}
        for symbol in config.symbols:
            sym_objects = [o for o in objects if o.symbol == symbol]
            if sym_objects:
                # Estimate coverage from partitions
                partitions = sorted(set(o.partition for o in sym_objects if o.partition))
                coverage_by_symbol[symbol] = {
                    "object_count": len(sym_objects),
                    "total_bytes": sum(o.size for o in sym_objects),
                    "partitions": len(partitions),
                    "earliest": partitions[0] if partitions else "",
                    "latest": partitions[-1] if partitions else "",
                }
        
        run_summary = SonarXMidbarRunSummary(
            status="SONARX_L2_MIDBAR_V0_PLAN_READY",
            run_id=run_id,
            plan=plan,
            coverage_by_symbol=coverage_by_symbol,
            warnings=warnings,
        )
        
        write_summary(output_dir, run_summary, config)
        write_manifest(output_dir, run_summary, config, [], "")
        write_inputs_md(output_dir, run_summary, config)
        
        return run_summary
    
    # Sample only
    if config.sample_only:
        schema = sample_sonarx_schema(objects)
        if schema.status != "SONARX_L2_MIDBAR_V0_SCHEMA_RECOGNIZED":
            return SonarXMidbarRunSummary(
                status="BLOCKED_SONARX_SCHEMA_UNRECOGNIZED",
                reason=schema.reason,
                run_id=run_id,
                plan=plan,
                schema=schema,
            )
        
        # Write schema report
        with open(output_dir / "schema_report.json", "w") as f:
            json.dump(asdict(schema), f, indent=2, default=str)
        
        return SonarXMidbarRunSummary(
            status="SONARX_L2_MIDBAR_V0_SCHEMA_RECOGNIZED",
            run_id=run_id,
            plan=plan,
            schema=schema,
            warnings=warnings,
        )
    
    # Execute
    if not config.execute:
        return SonarXMidbarRunSummary(
            status="BLOCKED_SONARX_NEEDS_EXECUTE_FLAG",
            reason="Must specify --execute, --plan-only, or --sample-only",
            run_id=run_id,
        )
    
    # Download and parse - use bulk download for speed
    all_snapshots = []
    rejected_counts = {"empty_bids_asks": 0, "crossed_book": 0, "invalid_market": 0, "parse_error": 0}
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Group objects by symbol for bulk download
        for symbol in config.symbols:
            sym_objects = [o for o in objects if o.symbol == symbol]
            if not sym_objects:
                continue
            
            # Get the prefix from the first object's key
            # key format: s3://bucket/market_data/perp/SYMBOL/l2-summary-snapshots/...
            first_key = sym_objects[0].key
            # Extract the S3 prefix from the objects
            prefix = config.s3_prefixes.get(symbol, "")
            if not prefix:
                continue
            
            # Parse bucket and path from prefix
            if not prefix.startswith("s3://"):
                continue
            prefix_parts = prefix[5:].split("/", 1)
            bucket = prefix_parts[0]
            s3_path = prefix_parts[1] if len(prefix_parts) > 1 else ""
            
            # Bulk download all objects for this symbol
            local_dir = tmp_path / symbol
            local_dir.mkdir(exist_ok=True)
            
            logger.info("Bulk downloading %d objects for %s...", len(sym_objects), symbol)
            cmd = [
                "aws", "s3", "cp",
                f"s3://{bucket}/{s3_path}",
                str(local_dir),
                "--recursive",
                "--request-payer", "requester",
                "--include", "*.json.gz",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                logger.error("Bulk download failed for %s: %s", symbol, result.stderr)
                continue
            
            # Parse all downloaded files
            for local_file in sorted(local_dir.glob("*.json.gz")):
                try:
                    snapshots = parse_snapshot_file(local_file)
                    all_snapshots.extend(snapshots)
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", local_file, e)
                    rejected_counts["parse_error"] += 1
            
            # Delete raw unless keep_raw
            if not config.keep_raw:
                import shutil
                shutil.rmtree(local_dir, ignore_errors=True)
    
    if not all_snapshots:
        return SonarXMidbarRunSummary(
            status="BLOCKED_SONARX_PARSE_FAILED",
            reason="No valid snapshots parsed from downloaded files",
            run_id=run_id,
            plan=plan,
            rejected_snapshot_counts=rejected_counts,
        )
    
    # Aggregate to 1h midbars
    midbars = aggregate_snapshots_to_1h_midbars(all_snapshots)
    
    # Validate
    validation_warnings = validate_midbars(midbars)
    warnings.extend(validation_warnings)
    
    # Check coverage
    coverage_by_symbol = {}
    gaps_by_symbol = {}
    
    for symbol in config.symbols:
        sym_bars = [mb for mb in midbars if mb.symbol == symbol]
        if sym_bars:
            # Sort by timestamp
            sym_bars.sort(key=lambda mb: mb.timestamp)
            timestamps = [mb.timestamp for mb in sym_bars]
            
            # Find gaps
            gaps = []
            for i in range(1, len(timestamps)):
                delta = (timestamps[i] - timestamps[i-1]).total_seconds() / 3600
                if delta > 1:
                    gaps.append(f"{timestamps[i-1].isoformat()} to {timestamps[i].isoformat()}")
            
            coverage_by_symbol[symbol] = {
                "start": timestamps[0].isoformat(),
                "end": timestamps[-1].isoformat(),
                "bar_count": len(sym_bars),
                "unique_hours": len(set(timestamps)),
            }
            gaps_by_symbol[symbol] = gaps
        else:
            coverage_by_symbol[symbol] = {"bar_count": 0}
            gaps_by_symbol[symbol] = []
    
    # Check minimum bars
    insufficient = []
    for symbol in config.symbols:
        bar_count = coverage_by_symbol.get(symbol, {}).get("bar_count", 0)
        if bar_count < config.min_total_bars_per_symbol:
            insufficient.append(f"{symbol}: {bar_count} < {config.min_total_bars_per_symbol}")
    
    if insufficient:
        return SonarXMidbarRunSummary(
            status="BLOCKED_SONARX_INSUFFICIENT_COVERAGE",
            reason=f"Insufficient coverage: {'; '.join(insufficient)}",
            run_id=run_id,
            plan=plan,
            coverage_by_symbol=coverage_by_symbol,
            gaps_by_symbol=gaps_by_symbol,
            snapshot_counts={s: len([sn for sn in all_snapshots if sn.market == s]) for s in config.symbols},
            rejected_snapshot_counts=rejected_counts,
            warnings=warnings,
        )
    
    # Normalize funding
    funding_df = normalize_existing_funding(config.funding_root, config.symbols)
    
    # Write outputs
    output_files = write_outputs_atomic(midbars, funding_df, output_dir, config.write_csv, config.write_parquet)
    
    # Write gaps
    write_gaps(output_dir, gaps_by_symbol)
    
    # Write plan/inventory
    with open(output_dir / "download_plan.json", "w") as f:
        json.dump(asdict(plan), f, indent=2, default=str)
    
    with open(output_dir / "s3_inventory.json", "w") as f:
        json.dump([asdict(o) for o in objects], f, indent=2, default=str)
    
    # Final summary
    run_summary = SonarXMidbarRunSummary(
        status="SONARX_L2_MIDBAR_V0_READY_FOR_DIAGNOSTIC",
        run_id=run_id,
        plan=plan,
        coverage_by_symbol=coverage_by_symbol,
        gaps_by_symbol=gaps_by_symbol,
        snapshot_counts={s: len([sn for sn in all_snapshots if sn.market == s]) for s in config.symbols},
        rejected_snapshot_counts=rejected_counts,
        warnings=warnings,
    )
    
    write_summary(output_dir, run_summary, config)
    write_manifest(output_dir, run_summary, config, output_files, "")
    write_inputs_md(output_dir, run_summary, config)
    
    return run_summary


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for SonarX L2 midbar builder."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="SonarX L2 Midbar Builder — L2 Snapshot → 1h Midquote Bars",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument("--output-root", type=Path, default=Path("reports/hyperliquid_btc_eth_ml_atr_sonarx_l2_midbar_v0"))
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--symbol", action="append", choices=["BTC", "ETH"], default=None)
    parser.add_argument("--s3-prefix", action="append", default=None)
    parser.add_argument("--funding-root", type=Path, default=Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0"))
    parser.add_argument("--max-download-gb", type=float, default=25.0)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--request-payer", type=bool, default=True)
    parser.add_argument("--max-objects", type=int, default=None)
    parser.add_argument("--min-total-bars-per-symbol", type=int, default=3000)
    parser.add_argument("--max-gap-hours", type=int, default=24)
    parser.add_argument("--write-csv", action="store_true", default=True)
    parser.add_argument("--write-parquet", action="store_true", default=True)
    
    args = parser.parse_args(argv)
    
    # Validate mode
    mode_count = sum([args.dry_run, args.plan_only, args.sample_only, args.execute])
    if mode_count != 1:
        parser.error("Exactly one of --dry-run, --plan-only, --sample-only, --execute required")
    
    # Symbols
    symbols = tuple(args.symbol) if args.symbol else ("BTC", "ETH")
    if not all(s in VALID_SYMBOLS for s in symbols):
        parser.error(f"Invalid symbols: {symbols}. Only {VALID_SYMBOLS} allowed.")
    
    # S3 prefixes
    s3_prefixes = dict(DEFAULT_S3_PREFIXES)
    if args.s3_prefix:
        # Parse custom prefixes
        for prefix in args.s3_prefix:
            # Infer symbol from prefix
            if "BTC" in prefix.upper():
                s3_prefixes["BTC"] = prefix
            elif "ETH" in prefix.upper():
                s3_prefixes["ETH"] = prefix
    
    config = SonarXL2Config(
        output_root=args.output_root,
        run_id=args.run_id,
        symbols=symbols,
        s3_prefixes=s3_prefixes,
        funding_root=args.funding_root,
        max_download_gb=args.max_download_gb,
        plan_only=args.plan_only,
        sample_only=args.sample_only,
        execute=args.execute,
        keep_raw=args.keep_raw,
        request_payer=args.request_payer,
        max_objects=args.max_objects,
        max_gap_hours=args.max_gap_hours,
        min_total_bars_per_symbol=args.min_total_bars_per_symbol,
        write_csv=args.write_csv,
        write_parquet=args.write_parquet,
        dry_run=args.dry_run,
    )
    
    # Run pipeline
    run_summary = run_sonarx_l2_midbar_pipeline(config)
    
    # Print summary
    print(f"Status: {run_summary.status}")
    print(f"Reason: {run_summary.reason or 'N/A'}")
    print(f"Run ID: {run_summary.run_id}")
    
    if run_summary.coverage_by_symbol:
        print("\nCoverage:")
        for symbol, coverage in run_summary.coverage_by_symbol.items():
            print(f"  {symbol}: {coverage.get('bar_count', 0)} bars")
    
    if run_summary.warnings:
        print("\nWarnings:")
        for warning in run_summary.warnings:
            print(f"  - {warning}")
    
    # Exit code
    if run_summary.status.startswith("BLOCKED"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())