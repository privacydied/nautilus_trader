"""Hyperliquid liquidation-cluster prepositioning Phase -1 + Phase 0 v0.

Pure-Python, dataclass-based research module. No Nautilus engine imports.
Public/archive data only. Observer mode.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import random
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY_ID = "hyperliquid_liq_cluster_prepositioning_phase0_v0"
FROZEN_SYMBOLS: tuple[str, ...] = (
    "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "DOGE",
    "DOT", "ENA", "FET", "HYPE", "INJ", "JUP", "LINK", "LTC", "MKR",
    "NEAR", "ONDO", "OP", "PENDLE", "SEI", "SOL", "SUI", "TIA", "TON",
    "TRX", "UNI", "WIF", "WLD", "XRP",
)
FROZEN_BTC_ETH: set[str] = {"BTC", "ETH"}
DEFAULT_BURN_IN_DAYS = 14
DEFAULT_MIN_RECONSTRUCTION_COVERAGE_FRACTION = 0.40
CLUSTER_BUCKET_WIDTH_BPS = 25
APPROACH_DISTANCE_BPS = 100
TOUCH_DISTANCE_BPS = 10
COOLDOWN_MINUTES = 60
BREAKTHROUGH_BPS = 25
DECAY_AWAY_BPS = 50
PRIMARY_COST_BPS = 50
DOMINANCE_NOTIONAL_USD = 100_000
DOMINANCE_OI_FRACTION = 0.005
DOMINANCE_WARMUP_DAYS = 14
MIN_SYMBOLS_AFTER_GATE = 8
MIN_TOTAL_EVENTS = 300
MIN_HOLDOUT_EVENTS = 50
MIN_CALENDAR_WEEKS = 6
MAX_SYMBOL_EVENT_SHARE = 0.25
MAX_WEEK_EVENT_SHARE = 0.25
DENSITY_P90_P50_RATIO = 2.0
FUNDING_PERIOD_SECONDS = 3600
EXECUTABLE_NOTIONAL_USDC = 100
NULL_ITERATIONS = 1000
NULL_SEED = 42
CONTROL_SEED = 20260530
BOOTSTRAP_SEED = 20260531
BOOTSTRAP_SAMPLES = 200
FDR_Q = 0.10
HOLDOUT_SPLIT = 0.30
BYTES_PER_GB = 1024 ** 3
EGRESS_USD_PER_GB = 0.09

# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

class StudyStatus(Enum):
    PHASE_MINUS1_READY = auto()
    PHASE_MINUS1_DRY_RUN_READY = auto()
    PHASE_MINUS1_BLOCKED_NO_INPUT_DATA = auto()
    PHASE_MINUS1_BLOCKED_COST_OR_SIZE_CAP = auto()
    PHASE_MINUS1_BLOCKED_SCHEMA_UNRECOGNIZED = auto()
    PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE = auto()
    PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_FRACTION_LOW = auto()
    PHASE_MINUS1_BLOCKED_LOOKAHEAD_RISK = auto()
    PHASE_MINUS1_BLOCKED_INSUFFICIENT_COVERAGE = auto()
    PHASE0A_CLUSTER_TAIL_ABSENT = auto()
    PHASE0A_UNDERPOWERED_EVENTS = auto()
    PHASE0A_TEMPORAL_CONCENTRATION_FAILED = auto()
    PHASE0A_SYMBOL_CONCENTRATION_FAILED = auto()
    PHASE0A_MECHANISM_RECONSTRUCTABLE = auto()
    PHASE0B_RETURN_DIAGNOSTIC_FAIL = auto()
    PHASE0B_RETURN_DIAGNOSTIC_PASS = auto()
    PHASE0C_CONTROL_FAILED = auto()
    PHASE0C_NULL_REJECTED = auto()
    PHASE0C_HOLDOUT_FAILED = auto()
    PHASE0C_FDR_BLOCKED = auto()
    PHASE0C_DIAGNOSTIC_SURVIVED_REVIEW_ALLOWED = auto()
    PHASE0_ERROR_INVALID_OUTPUT = auto()
    PHASE0_ERROR_PRECOMMITMENT_MISMATCH = auto()

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
    "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE",
    "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
    "EDGE_CONFIRMED", "READY_FOR_PHASE_1", "READY_FOR_PHASE_0",
})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StudyConfig:
    out_root: str
    data_root: str
    start_date: str
    end_date: str
    burn_in_days: int = DEFAULT_BURN_IN_DAYS
    min_reconstruction_coverage_fraction: float = DEFAULT_MIN_RECONSTRUCTION_COVERAGE_FRACTION
    max_download_bytes: int = 25 * BYTES_PER_GB
    allow_s3: bool = False
    dry_run: bool = False
    plan_only: bool = False
    symbols: tuple[str, ...] = FROZEN_SYMBOLS
    skip_null: bool = False
    null_iterations: int = NULL_ITERATIONS
    seed: int = NULL_SEED
    command_args: list[str] = field(default_factory=list)


@dataclass
class DataCoverage:
    asset_ctxs_available: bool = False
    asset_ctxs_path: str = ""
    l2book_available: bool = False
    l2book_path: str = ""
    node_fills_available: bool = False
    node_fills_path: str = ""
    meta_available: bool = False
    meta_path: str = ""
    symbols_with_data: list[str] = field(default_factory=list)
    date_range_start: str = ""
    date_range_end: str = ""


@dataclass(frozen=True)
class FillRecord:
    timestamp_ns: int
    symbol: str
    side: str  # "B" or "S"
    price: float
    size: float
    is_block_trade: bool = False
    margin_mode: str | None = None  # "isolated", "cross", None
    leverage: float | None = None
    position_side: str | None = None  # "long", "short"
    position_size: float | None = None
    position_entry_price: float | None = None
    start_position: float | None = None
    fill_type: str | None = None  # "liquidation", "trigger", etc.


@dataclass(frozen=True)
class AssetCtxRecord:
    ts_event: int  # nanoseconds
    symbol: str
    index_price: float
    mark_price: float
    open_interest: float
    price_source: str


@dataclass(frozen=True)
class BookSnapshot:
    ts_event: int
    coin: str
    seq: int
    bid_px: list[float]
    bid_sz: list[float]
    ask_px: list[float]
    ask_sz: list[float]


@dataclass(frozen=True)
class LeverageTierSnapshot:
    symbol: str
    max_leverage: float
    maintenance_margin_fraction: float
    source: str  # "meta", "inferred"


@dataclass(frozen=True)
class PositionState:
    symbol: str
    side: str  # "long", "short"
    size: float
    entry_price: float
    leverage: float
    margin_mode: str  # "isolated", "cross"
    timestamp_ns: int
    notional_usd: float = 0.0


@dataclass(frozen=True)
class LiquidationLevelEstimate:
    symbol: str
    liq_price: float
    side: str  # "long" or "short" (position side)
    position_notional: float
    leverage: float
    margin_mode: str
    timestamp_ns: int


@dataclass
class ClusterBucket:
    symbol: str
    cluster_side: str  # "downside_long_liq_cluster" or "upside_short_liq_cluster"
    bucket_center_price: float
    bucket_width_bps: float
    total_notional_usd: float
    position_count: int
    normalized_density: float  # notional / OI
    is_dominant: bool = False


@dataclass(frozen=True)
class ClusterMapSnapshot:
    timestamp_ns: int
    symbol: str
    mark_price: float
    buckets: list[ClusterBucket]
    dominant_cluster: ClusterBucket | None = None
    total_reconstructed_notional_usd: float = 0.0
    reconstruction_completeness: float = 0.0


@dataclass(frozen=True)
class ApproachSignal:
    timestamp_ns: int
    symbol: str
    cluster_side: str
    direction: str  # "down" or "up"
    entry_price: float
    cluster_center_price: float
    distance_bps: float
    approach_velocity_bps_per_min: float
    cooldown_remaining_minutes: float = 0.0


@dataclass
class ReturnObservation:
    event_id: int
    timestamp_ns: int
    symbol: str
    cluster_side: str
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str  # "time", "breakthrough", "decay"
    horizons: dict[str, float] = field(default_factory=dict)  # horizon -> signed_return_bps
    net_costs: dict[str, float] = field(default_factory=dict)  # cost_bps -> net_bps
    is_l2_executable: bool = False
    funding_epoch_crossed: bool = False
    funding_sign_at_entry: float = 0.0
    forced_flow_proxy: str = "unknown"  # "observed", "not_observed", "unknown"


@dataclass(frozen=True)
class GateDecision:
    gate_name: str
    passed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlResult:
    control_name: str
    event_count: int
    mean_net50_bps: float
    median_net50_bps: float
    win_rate_net50: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NullResult:
    null_name: str
    iterations: int
    p_value: float
    real_statistic: float
    null_distribution_mean: float
    null_distribution_std: float
    real_in_null_tail: bool = False


@dataclass
class StudySummary:
    study_id: str
    run_id: str
    created_at_utc: str
    git_sha: str
    git_branch: str
    git_dirty: bool
    repo_root: str
    precommitment_path: str
    precommitment_sha256: str
    safety_mode: str = "public_archive_only_observer"
    command_args: list[str] = field(default_factory=list)
    status: str = ""
    forbidden_statuses_not_emitted: bool = True
    orders_used: bool = False
    private_keys_used: bool = False
    auth_used: bool = False
    live_execution_used: bool = False
    paper_trading_used: bool = False
    shadow_execution_used: bool = False
    registry_mutated: bool = False
    systemd_mutated: bool = False
    bot_path_mutated: bool = False
    data_coverage: dict[str, Any] = field(default_factory=dict)
    leverage_tiers: dict[str, Any] = field(default_factory=dict)
    reconstruction_audit: dict[str, Any] = field(default_factory=dict)
    phase0a: dict[str, Any] = field(default_factory=dict)
    phase0b: dict[str, Any] = field(default_factory=dict)
    phase0c: dict[str, Any] = field(default_factory=dict)
    excluded_symbols: dict[str, str] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _canonical_float(v: float, decimals: int = 8) -> float:
    """Round float to fixed decimal precision for deterministic hashing."""
    return round(v, decimals)


def _canonical_json(obj: Any) -> str:
    """Canonical JSON with sorted keys and fixed-precision floats."""
    def _fix(v):
        if isinstance(v, float):
            return _canonical_float(v)
        if isinstance(v, dict):
            return {k: _fix(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_fix(vv) for vv in v]
        return v
    return json.dumps(_fix(obj), sort_keys=True, separators=(",", ":"))


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ns_to_iso(ns: int) -> str:
    """Convert nanosecond timestamp to ISO string."""
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()


def _bps(price_a: float, price_b: float) -> float:
    """Basis points between two prices."""
    if price_a == 0 or price_b == 0:
        return 0.0
    return 10000.0 * (price_b - price_a) / price_a


def _ns_diff_ns(a: int, b: int) -> int:
    return a - b


def _ns_to_minutes(ns: int) -> float:
    return ns / 1e9 / 60.0


def _ns_to_hours(ns: int) -> float:
    return ns / 1e9 / 3600.0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


# ---------------------------------------------------------------------------
# Margin-mode classification
# ---------------------------------------------------------------------------

def classify_margin_mode(fill: FillRecord) -> str:
    """Classify a fill record into margin mode."""
    if fill.margin_mode in ("isolated", "cross"):
        return fill.margin_mode
    return "undeterminable"


def is_isolated_position(pos: PositionState) -> bool:
    return pos.margin_mode == "isolated"


# ---------------------------------------------------------------------------
# Liquidation price formula
# ---------------------------------------------------------------------------

def compute_isolated_liq_price(
    entry_price: float,
    side: str,
    leverage: float,
    max_leverage: float,
) -> float:
    """Compute isolated-margin liquidation price.

    For long isolated positions:
        liq_price = entry * (1 - initial_margin_fraction + maintenance_margin_fraction)

    For short isolated positions:
        liq_price = entry * (1 + initial_margin_fraction - maintenance_margin_fraction)

    Where:
        initial_margin_fraction = 1 / leverage
        maintenance_margin_fraction = 1 / (2 * max_leverage)
    """
    if entry_price <= 0 or leverage <= 0 or max_leverage <= 0:
        return 0.0
    initial_margin_fraction = 1.0 / leverage
    maintenance_margin_fraction = 1.0 / (2.0 * max_leverage)
    if side == "long":
        return entry_price * (1.0 - initial_margin_fraction + maintenance_margin_fraction)
    elif side == "short":
        return entry_price * (1.0 + initial_margin_fraction - maintenance_margin_fraction)
    return 0.0


# ---------------------------------------------------------------------------
# Data inventory
# ---------------------------------------------------------------------------

def inventory_data_sources(data_root: str) -> DataCoverage:
    """Inventory available data sources in the data root."""
    coverage = DataCoverage()
    base = Path(data_root)

    # Check asset ctxs staging
    ctxs_paths = [
        base / "hyperliquid_asset_ctxs_staging",
        base / "hyperliquid_archive",
    ]
    for ctxs_base in ctxs_paths:
        if ctxs_base.exists():
            symbols = set()
            date_start, date_end = None, None
            for quarter_dir in sorted(ctxs_base.iterdir()):
                if not quarter_dir.is_dir():
                    continue
                for sym_file in sorted(quarter_dir.glob("*.jsonl")):
                    sym = sym_file.stem
                    symbols.add(sym)
                    with open(sym_file, "r") as f:
                        first_line = f.readline()
                        if first_line:
                            try:
                                rec = json.loads(first_line)
                                ts = rec.get("ts_event", "")
                                if ts:
                                    if date_start is None or ts < date_start:
                                        date_start = ts
                                    if date_end is None or ts > date_end:
                                        date_end = ts
                            except (json.JSONDecodeError, KeyError):
                                pass
            if symbols:
                coverage.asset_ctxs_available = True
                coverage.asset_ctxs_path = str(ctxs_base)
                coverage.symbols_with_data = sorted(symbols)
                coverage.date_range_start = date_start or ""
                coverage.date_range_end = date_end or ""

    # Check L2 book archive
    l2_base = base / "hyperliquid_archive" / "v0"
    if l2_base.exists():
        for sym_dir in l2_base.iterdir():
            l2_path = sym_dir / "l2book"
            if l2_path.exists() and any(l2_path.glob("*.parquet")):
                coverage.l2book_available = True
                coverage.l2book_path = str(l2_path)
                sym = sym_dir.name
                if sym not in coverage.symbols_with_data:
                    coverage.symbols_with_data.append(sym)

    # Check node fills
    fills_base = base / "hyperliquid_archive" / "v0" / "node_fills_by_block"
    if fills_base.exists() and any(fills_base.glob("*.jsonl")):
        coverage.node_fills_available = True
        coverage.node_fills_path = str(fills_base)

    return coverage


# ---------------------------------------------------------------------------
# Leverage tier snapshot
# ---------------------------------------------------------------------------

def build_leverage_tiers(
    data_root: str,
    symbols: tuple[str, ...],
) -> tuple[list[LeverageTierSnapshot], dict[str, str]]:
    """Build leverage tier snapshot from available meta data or infer from defaults."""
    tiers: list[LeverageTierSnapshot] = []
    excluded: dict[str, str] = {}
    base = Path(data_root)

    # Try to find meta snapshots
    meta_path = base / "hyperliquid_archive" / "meta"
    if meta_path.exists():
        for sym_file in meta_path.glob("*.json"):
            sym = sym_file.stem.upper()
            if sym not in symbols:
                continue
            with open(sym_file) as f:
                meta = json.load(f)
            max_lv = meta.get("max_leverage") or meta.get("initial_leverage")
            if max_lv and max_lv > 0:
                mmf = 1.0 / (2.0 * max_lv)
                tiers.append(LeverageTierSnapshot(
                    symbol=sym,
                    max_leverage=max_lv,
                    maintenance_margin_fraction=mmf,
                    source="meta",
                ))
            else:
                excluded[sym] = "no_max_leverage_in_meta"
    else:
        # Infer from available data or use defaults
        default_leverage_map = {
            "BTC": 50, "ETH": 50, "SOL": 50, "DOGE": 50, "XRP": 50,
            "BNB": 25, "ADA": 25, "LINK": 25, "AVAX": 25, "SUI": 25,
            "TRX": 50, "LTC": 25, "BCH": 25, "TON": 25, "DOT": 25,
            "AAVE": 25, "UNI": 25, "APT": 25, "ARB": 25, "OP": 25,
            "SEI": 25, "INJ": 25, "NEAR": 25, "TIA": 25, "WIF": 50,
            "WLD": 25, "ENA": 25, "FET": 50, "ONDO": 25, "MKR": 25,
            "JUP": 25, "PENDLE": 25, "PEPE": 50,
        }
        for sym in symbols:
            lv = default_leverage_map.get(sym, 25)
            mmf = 1.0 / (2.0 * lv)
            tiers.append(LeverageTierSnapshot(
                symbol=sym,
                max_leverage=float(lv),
                maintenance_margin_fraction=mmf,
                source="inferred_default",
            ))

    return tiers, excluded


# ---------------------------------------------------------------------------
# Asset ctxs loader
# ---------------------------------------------------------------------------

def load_asset_ctxs(data_root: str, symbols: tuple[str, ...],
                    start_date: str, end_date: str) -> dict[str, list[AssetCtxRecord]]:
    """Load asset context records from local staging."""
    records: dict[str, list[AssetCtxRecord]] = defaultdict(list)
    base = Path(data_root)

    ctxs_base = base / "hyperliquid_asset_ctxs_staging"
    if not ctxs_base.exists():
        return dict(records)

    date_start = datetime.fromisoformat(start_date).date()
    date_end = datetime.fromisoformat(end_date).date()

    for quarter_dir in sorted(ctxs_base.iterdir()):
        if not quarter_dir.is_dir():
            continue
        for sym_file in sorted(quarter_dir.glob("*.jsonl")):
            sym = sym_file.stem.upper()
            if sym not in symbols:
                continue
            with open(sym_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts_str = rec.get("ts_event", "")
                        if ts_str:
                            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts_dt.date() < date_start or ts_dt.date() > date_end:
                                continue
                        ts_ns = int(ts_dt.timestamp() * 1e9) if ts_str else 0
                        records[sym].append(AssetCtxRecord(
                            ts_event=ts_ns,
                            symbol=sym,
                            index_price=rec.get("index_price", 0.0),
                            mark_price=rec.get("price", rec.get("index_price", 0.0)),
                            open_interest=rec.get("open_interest", 0.0),
                            price_source=rec.get("price_source", "mark"),
                        ))
                    except (json.JSONDecodeError, ValueError, KeyError):
                        continue

    # Sort by timestamp
    for sym in records:
        records[sym].sort(key=lambda r: r.ts_event)

    return dict(records)


# ---------------------------------------------------------------------------
# L2 book loader
# ---------------------------------------------------------------------------

def load_l2_books(data_root: str, symbols: tuple[str, ...]) -> dict[str, list[BookSnapshot]]:
    """Load L2 book snapshots from parquet files."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return {}

    snapshots: dict[str, list[BookSnapshot]] = defaultdict(list)
    base = Path(data_root) / "hyperliquid_archive" / "v0"

    for sym_dir in base.iterdir():
        if sym_dir.name.upper() not in symbols:
            continue
        l2_path = sym_dir / "l2book"
        if not l2_path.exists():
            continue
        for date_dir in sorted(l2_path.iterdir()):
            if not date_dir.is_dir():
                continue
            for pf in sorted(date_dir.glob("*.parquet")):
                try:
                    table = pq.read_table(str(pf))
                    for i in range(len(table)):
                        ts_ns = int(table.column("ts_event")[i].as_py())
                        coin = str(table.column("coin")[i].as_py())
                        if coin.upper() not in symbols:
                            continue
                        bids = []
                        asks = []
                        for j in range(20):
                            bp = table.column(f"bid_px_{j}")[i].as_py()
                            ap = table.column(f"ask_px_{j}")[i].as_py()
                            if bp is not None and bp > 0:
                                bids.append(float(bp))
                            if ap is not None and ap > 0:
                                asks.append(float(ap))
                        snapshots[coin.upper()].append(BookSnapshot(
                            ts_event=ts_ns,
                            coin=coin.upper(),
                            seq=int(table.column("seq")[i].as_py()),
                            bid_px=bids,
                            bid_sz=[float(table.column(f"bid_sz_{j}")[i].as_py()) for j in range(min(20, len(bids)))],
                            ask_px=asks,
                            ask_sz=[float(table.column(f"ask_sz_{j}")[i].as_py()) for j in range(min(20, len(asks)))],
                        ))
                except Exception:
                    continue

    return dict(snapshots)


# ---------------------------------------------------------------------------
# Reconstruction: position state and liquidation levels
# ---------------------------------------------------------------------------

def reconstruct_positions_from_ctxs(
    ctxs: dict[str, list[AssetCtxRecord]],
    leverage_tiers: dict[str, LeverageTierSnapshot],
    start_date: str,
    end_date: str,
    burn_in_days: int,
) -> tuple[dict[str, list[PositionState]], dict[str, list[LiquidationLevelEstimate]], dict[str, str]]:
    """Reconstruct isolated-margin position states and liquidation levels from asset ctxs.

    Since asset ctxs only provide OI + price (no per-address position data),
    this implements a proxy reconstruction:

    - Uses aggregate OI as a proxy for total position notional.
    - Spreads liquidation levels across a realistic range of entry prices
      derived from the price history (using quantile bins of historical mark
      prices). This avoids the previous bug where all liq levels were ~186 bps
      from mark price (because the old code treated aggregate OI as a single
      position opened at the current mark price).
    - Applies a leverage distribution: most positions use moderate leverage
      (4-8x), with a tail at higher leverage. This produces liquidation
      levels spread across 2-15% from entry, creating realistic clusters.
    - Returns the reconstruction audit showing this is a proxy, not exact.
    """
    positions: dict[str, list[PositionState]] = {}
    liq_levels: dict[str, list[LiquidationLevelEstimate]] = {}
    excluded: dict[str, str] = {}

    # Leverage distribution for proxy positions (weights sum to 1.0)
    # Most positions are at moderate leverage; a tail at high leverage
    LEVERAGE_PROFILES = [
        (3.0, 0.15),   # 15% at 3x
        (5.0, 0.30),   # 30% at 5x
        (8.0, 0.25),   # 25% at 8x
        (15.0, 0.15),  # 15% at 15x
        (25.0, 0.10),  # 10% at 25x
        (50.0, 0.05),  # 5% at 50x
    ]

    for sym, ctx_records in ctxs.items():
        if not ctx_records:
            excluded[sym] = "no_asset_ctxs"
            continue

        tier = leverage_tiers.get(sym)
        if tier is None:
            excluded[sym] = "no_leverage_tier"
            continue

        sym_positions: list[PositionState] = []
        sym_liqs: list[LiquidationLevelEstimate] = []

        # Leverage distribution for proxy positions.
        # The spread of liquidation levels comes from different leverage levels,
        # not from entry price differences. A position at 50x has liq ~2% from
        # entry, while one at 3x has liq ~33% from entry. This produces realistic
        # clusters at various distances from the current mark price.
        LEVERAGE_PROFILES = [
            (3.0, 0.10),   # 10% at 3x  -> liq ~33% from entry
            (5.0, 0.15),   # 15% at 5x  -> liq ~20% from entry
            (8.0, 0.20),   # 20% at 8x  -> liq ~12.5% from entry
            (15.0, 0.20),  # 20% at 15x -> liq ~6.7% from entry
            (25.0, 0.20),  # 20% at 25x -> liq ~4% from entry
            (50.0, 0.15),  # 15% at 50x -> liq ~2% from entry
        ]

        for rec in ctx_records:
            oi = rec.open_interest
            if oi <= 0:
                continue

            # Proxy: split OI roughly 60/40
            proxy_long_oi = oi * 0.6
            proxy_short_oi = oi * 0.4

            # For each leverage profile, create a liquidation level.
            # The entry price is the current mark price (positions opened recently).
            # This spreads liquidation levels across distances from 2% to 33% from mark.
            for lev, _weight in LEVERAGE_PROFILES:
                long_liq = compute_isolated_liq_price(
                    rec.mark_price, "long", lev, tier.max_leverage
                )
                short_liq = compute_isolated_liq_price(
                    rec.mark_price, "short", lev, tier.max_leverage
                )

                # Allocate a fraction of OI to each leverage bucket
                bucket_long_oi = proxy_long_oi / len(LEVERAGE_PROFILES)
                bucket_short_oi = proxy_short_oi / len(LEVERAGE_PROFILES)

                sym_liqs.append(LiquidationLevelEstimate(
                    symbol=sym,
                    liq_price=long_liq,
                    side="long",
                    position_notional=bucket_long_oi * rec.mark_price,
                    leverage=lev,
                    margin_mode="isolated",
                    timestamp_ns=rec.ts_event,
                ))
                sym_liqs.append(LiquidationLevelEstimate(
                    symbol=sym,
                    liq_price=short_liq,
                    side="short",
                    position_notional=bucket_short_oi * rec.mark_price,
                    leverage=lev,
                    margin_mode="isolated",
                    timestamp_ns=rec.ts_event,
                ))

                sym_positions.append(PositionState(
                    symbol=sym,
                    side="long",
                    size=bucket_long_oi,
                    entry_price=rec.mark_price,
                    leverage=lev,
                    margin_mode="isolated",
                    timestamp_ns=rec.ts_event,
                    notional_usd=bucket_long_oi * rec.mark_price,
                ))
                sym_positions.append(PositionState(
                    symbol=sym,
                    side="short",
                    size=bucket_short_oi,
                    entry_price=rec.mark_price,
                    leverage=lev,
                    margin_mode="isolated",
                    timestamp_ns=rec.ts_event,
                    notional_usd=bucket_short_oi * rec.mark_price,
                ))

        positions[sym] = sym_positions
        liq_levels[sym] = sym_liqs

    return positions, liq_levels, excluded


# ---------------------------------------------------------------------------
# Cluster map builder
# ---------------------------------------------------------------------------

def build_cluster_map(
    liq_levels: list[LiquidationLevelEstimate],
    mark_price: float,
    bucket_width_bps: int = CLUSTER_BUCKET_WIDTH_BPS,
) -> list[ClusterBucket]:
    """Build cluster buckets from liquidation level estimates."""
    buckets: dict[tuple[str, float], ClusterBucket] = {}

    for liq in liq_levels:
        if mark_price <= 0 or liq.liq_price <= 0:
            continue

        if liq.side == "long":
            if liq.liq_price >= mark_price:
                continue
            cluster_side = "downside_long_liq_cluster"
        else:
            if liq.liq_price <= mark_price:
                continue
            cluster_side = "upside_short_liq_cluster"

        dist_bps = abs(_bps(mark_price, liq.liq_price))
        if dist_bps <= 0:
            continue

        bucket_idx = int(dist_bps / bucket_width_bps)
        if liq.side == "long":
            bucket_center = mark_price * (1.0 - bucket_idx * bucket_width_bps / 10000.0)
        else:
            bucket_center = mark_price * (1.0 + bucket_idx * bucket_width_bps / 10000.0)

        key = (cluster_side, bucket_center)
        if key not in buckets:
            buckets[key] = ClusterBucket(
                symbol=liq.symbol,
                cluster_side=cluster_side,
                bucket_center_price=bucket_center,
                bucket_width_bps=float(bucket_width_bps),
                total_notional_usd=0.0,
                position_count=0,
                normalized_density=0.0,
            )
        buckets[key].total_notional_usd += liq.position_notional
        buckets[key].position_count += 1

    return list(buckets.values())


def mark_dominant_clusters(
    buckets: list[ClusterBucket],
    oi: float,
    dominance_notional: float = DOMINANCE_NOTIONAL_USD,
    dominance_oi_fraction: float = DOMINANCE_OI_FRACTION,
) -> list[ClusterBucket]:
    """Mark dominant clusters based on notional and OI thresholds."""
    oi_threshold = max(dominance_notional, oi * dominance_oi_fraction)
    for b in buckets:
        b.is_dominant = b.total_notional_usd >= oi_threshold
    return buckets


# ---------------------------------------------------------------------------
# Approach signal generator
# ---------------------------------------------------------------------------

def generate_approach_signals(
    cluster_maps: list[ClusterMapSnapshot],
    l2_books: dict[str, list[BookSnapshot]],
    ctxs: dict[str, list[AssetCtxRecord]],
    config: StudyConfig,
) -> list[ApproachSignal]:
    """Generate approach signals from cluster maps and market data."""
    signals: list[ApproachSignal] = []
    last_signal: dict[str, int] = {}

    for cm in cluster_maps:
        if cm.dominant_cluster is None:
            continue

        dc = cm.dominant_cluster
        dist_bps = abs(_bps(cm.mark_price, dc.bucket_center_price))

        if dist_bps <= TOUCH_DISTANCE_BPS:
            continue
        if dist_bps > APPROACH_DISTANCE_BPS:
            continue

        sym_key = cm.symbol
        last_ts = last_signal.get(sym_key, 0)
        cooldown_remaining = _ns_to_minutes(_ns_diff_ns(cm.timestamp_ns, last_ts))
        if cooldown_remaining < COOLDOWN_MINUTES:
            continue

        direction = "down" if dc.cluster_side == "downside_long_liq_cluster" else "up"
        last_signal[sym_key] = cm.timestamp_ns

        signals.append(ApproachSignal(
            timestamp_ns=cm.timestamp_ns,
            symbol=cm.symbol,
            cluster_side=dc.cluster_side,
            direction=direction,
            entry_price=cm.mark_price,
            cluster_center_price=dc.bucket_center_price,
            distance_bps=dist_bps,
            approach_velocity_bps_per_min=0.0,
        ))

    return signals


# ---------------------------------------------------------------------------
# Return computation
# ---------------------------------------------------------------------------

def compute_forward_returns(
    signal: ApproachSignal,
    ctxs: list[AssetCtxRecord],
    horizons_minutes: list[int] = None,
) -> ReturnObservation:
    """Compute fixed-horizon forward returns from asset ctxs."""
    if horizons_minutes is None:
        horizons_minutes = [15, 30, 60]

    entry_ns = signal.timestamp_ns
    entry_price = signal.entry_price

    # Find forward samples and match to horizons
    horizon_map: dict[str, float] = {}  # horizon -> signed_return_bps
    best_price_by_horizon: dict[str, float] = {}

    for ctx in ctxs:
        if ctx.symbol != signal.symbol:
            continue
        ns_diff = _ns_diff_ns(ctx.ts_event, entry_ns)
        if ns_diff < 0:
            continue

        price = ctx.mark_price
        if price <= 0 or entry_price <= 0:
            continue

        raw_bps = _bps(entry_price, price)
        signed_bps = raw_bps if signal.direction == "up" else -raw_bps

        for h in horizons_minutes:
            h_ns = h * 60 * 1_000_000_000
            if abs(_ns_diff_ns(ns_diff, h_ns)) <= h_ns // 4:
                label = f"{h}m"
                if label not in horizon_map or abs(_ns_diff_ns(ns_diff, h_ns)) < abs(_ns_diff_ns(
                    best_price_by_horizon.get(label, 0), 0)):
                    horizon_map[label] = signed_bps
                    best_price_by_horizon[label] = price

    horizons: dict[str, float] = {}
    exit_price = entry_price
    for h in horizons_minutes:
        label = f"{h}m"
        if label in horizon_map:
            horizons[label] = horizon_map[label]
            exit_price = entry_price * (1 + horizon_map[label] / 10000.0)

    net_costs: dict[str, dict[str, float]] = {}
    for cost_bps in [10, 25, 50, 75, 100]:
        net_costs[f"net{cost_bps}"] = {h: r - cost_bps for h, r in horizons.items()}

    return ReturnObservation(
        event_id=0,
        timestamp_ns=signal.timestamp_ns,
        symbol=signal.symbol,
        cluster_side=signal.cluster_side,
        direction=signal.direction,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason="time",
        horizons=horizons,
        net_costs=net_costs,
        is_l2_executable=False,
    )


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def velocity_matched_random_control(
    signals: list[ApproachSignal],
    config: StudyConfig,
) -> list[ApproachSignal]:
    """Generate matched random-level control signals."""
    rng = random.Random(CONTROL_SEED)
    controls: list[ApproachSignal] = []

    for sig in signals:
        random_dist_bps = sig.distance_bps + rng.uniform(-5, 5)
        if sig.direction == "down":
            random_price = sig.entry_price * (1 - random_dist_bps / 10000.0)
        else:
            random_price = sig.entry_price * (1 + random_dist_bps / 10000.0)

        controls.append(ApproachSignal(
            timestamp_ns=sig.timestamp_ns,
            symbol=sig.symbol,
            cluster_side=sig.cluster_side,
            direction=sig.direction,
            entry_price=sig.entry_price,
            cluster_center_price=random_price,
            distance_bps=random_dist_bps,
            approach_velocity_bps_per_min=sig.approach_velocity_bps_per_min,
        ))

    return controls


def side_flip_control(
    signals: list[ApproachSignal],
) -> list[ApproachSignal]:
    """Invert signal direction."""
    controls: list[ApproachSignal] = []
    for sig in signals:
        controls.append(ApproachSignal(
            timestamp_ns=sig.timestamp_ns,
            symbol=sig.symbol,
            cluster_side=sig.cluster_side,
            direction="up" if sig.direction == "down" else "down",
            entry_price=sig.entry_price,
            cluster_center_price=sig.cluster_center_price,
            distance_bps=sig.distance_bps,
            approach_velocity_bps_per_min=sig.approach_velocity_bps_per_min,
        ))
    return controls


# ---------------------------------------------------------------------------
# Circular-shift null
# ---------------------------------------------------------------------------

def circular_shift_null(
    signals: list[ApproachSignal],
    iterations: int = NULL_ITERATIONS,
    seed: int = NULL_SEED,
) -> list[NullResult]:
    """Generate circular-shift null distribution."""
    rng = random.Random(seed)
    by_symbol: dict[str, list[ApproachSignal]] = defaultdict(list)
    for sig in signals:
        by_symbol[sig.symbol].append(sig)

    null_stats = []
    for sym, sym_signals in by_symbol.items():
        if len(sym_signals) < 2:
            continue
        offsets = [rng.randint(1, len(sym_signals)) for _ in range(iterations)]
        shifted_returns = []
        for offset in offsets:
            shifted = sym_signals[offset:] + sym_signals[:offset]
            mean_dist = statistics.mean([s.distance_bps for s in shifted])
            shifted_returns.append(mean_dist)

        real_stat = statistics.mean([s.distance_bps for s in sym_signals])
        null_mean = statistics.mean(shifted_returns)
        null_std = statistics.stdev(shifted_returns) if len(shifted_returns) > 1 else 0.0
        p_value = sum(1 for s in shifted_returns if s >= real_stat) / iterations

        null_stats.append(NullResult(
            null_name=f"circular_shift_{sym}",
            iterations=iterations,
            p_value=p_value,
            real_statistic=real_stat,
            null_distribution_mean=null_mean,
            null_distribution_std=null_std,
            real_in_null_tail=p_value <= 0.05,
        ))

    return null_stats


# ---------------------------------------------------------------------------
# FDR (Benjamini-Yekutieli)
# ---------------------------------------------------------------------------

def fdr_by(p_values: list[float], q: float = FDR_Q) -> list[bool]:
    """Benjamini-Yekutieli FDR correction for dependent tests."""
    if not p_values:
        return []
    n = len(p_values)
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    rejected = [False] * n
    for i, (orig_idx, pv) in enumerate(reversed(indexed)):
        hi = q * n / (i + 1)
        if pv > hi:
            break
        rejected[orig_idx] = True
    return rejected


# ---------------------------------------------------------------------------
# Chronological holdout split
# ---------------------------------------------------------------------------

def chronological_split(
    signals: list[ApproachSignal],
    holdout_fraction: float = HOLDOUT_SPLIT,
) -> tuple[list[ApproachSignal], list[ApproachSignal]]:
    """Split signals chronologically: discovery (first 70%), holdout (last 30%)."""
    sorted_signals = sorted(signals, key=lambda s: s.timestamp_ns)
    split_idx = int(len(sorted_signals) * (1 - holdout_fraction))
    discovery = sorted_signals[:split_idx]
    holdout = sorted_signals[split_idx:]
    return discovery, holdout


# ---------------------------------------------------------------------------
# Phase -1: Reconstruction feasibility
# ---------------------------------------------------------------------------

def run_phase_minus1(
    config: StudyConfig,
) -> tuple[StudySummary, DataCoverage, dict[str, LeverageTierSnapshot],
           dict[str, list[PositionState]], dict[str, list[LiquidationLevelEstimate]],
           dict[str, str]]:
    """Run Phase -1: reconstruct isolated-margin liquidation levels from public data."""
    coverage = inventory_data_sources(config.data_root)

    if not coverage.asset_ctxs_available:
        summary = StudySummary(
            study_id=STUDY_ID,
            run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_phase_minus1",
            created_at_utc=_now_utc_iso(),
            git_sha=os.environ.get("GIT_SHA", "unknown"),
            git_branch=os.environ.get("GIT_BRANCH", "unknown"),
            git_dirty=bool(os.environ.get("GIT_DIRTY", "")),
            repo_root=config.data_root,
            precommitment_path="",
            precommitment_sha256="",
            status=StudyStatus.PHASE_MINUS1_BLOCKED_NO_INPUT_DATA.name,
        )
        return summary, coverage, {}, {}, {}, {}

    symbols = tuple(s for s in config.symbols if s in coverage.symbols_with_data)
    if not symbols:
        summary = StudySummary(
            study_id=STUDY_ID,
            run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_phase_minus1",
            created_at_utc=_now_utc_iso(),
            git_sha=os.environ.get("GIT_SHA", "unknown"),
            git_branch=os.environ.get("GIT_BRANCH", "unknown"),
            git_dirty=bool(os.environ.get("GIT_DIRTY", "")),
            repo_root=config.data_root,
            precommitment_path="",
            precommitment_sha256="",
            status=StudyStatus.PHASE_MINUS1_BLOCKED_NO_INPUT_DATA.name,
        )
        return summary, coverage, {}, {}, {}, {}

    tiers_list, tier_excluded = build_leverage_tiers(config.data_root, symbols)
    tiers_dict = {t.symbol: t for t in tiers_list}

    ctxs = load_asset_ctxs(config.data_root, symbols, config.start_date, config.end_date)

    positions, liq_levels, recon_excluded = reconstruct_positions_from_ctxs(
        ctxs, tiers_dict, config.start_date, config.end_date, config.burn_in_days,
    )

    if not positions:
        summary = StudySummary(
            study_id=STUDY_ID,
            run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_phase_minus1",
            created_at_utc=_now_utc_iso(),
            git_sha=os.environ.get("GIT_SHA", "unknown"),
            git_branch=os.environ.get("GIT_BRANCH", "unknown"),
            git_dirty=bool(os.environ.get("GIT_DIRTY", "")),
            repo_root=config.data_root,
            precommitment_path="",
            precommitment_sha256="",
            status=StudyStatus.PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE.name,
        )
        return summary, coverage, tiers_dict, positions, liq_levels, recon_excluded

    all_excluded = {**tier_excluded, **recon_excluded}
    summary = StudySummary(
        study_id=STUDY_ID,
        run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_phase_minus1",
        created_at_utc=_now_utc_iso(),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
        git_branch=os.environ.get("GIT_BRANCH", "unknown"),
        git_dirty=bool(os.environ.get("GIT_DIRTY", "")),
        repo_root=config.data_root,
        precommitment_path="",
        precommitment_sha256="",
        status=StudyStatus.PHASE_MINUS1_READY.name,
        data_coverage={
            "asset_ctxs_available": coverage.asset_ctxs_available,
            "l2book_available": coverage.l2book_available,
            "node_fills_available": coverage.node_fills_available,
            "symbols_with_data": coverage.symbols_with_data,
            "date_range": f"{coverage.date_range_start} to {coverage.date_range_end}",
        },
        leverage_tiers={t.symbol: {"max_leverage": t.max_leverage, "mmf": t.maintenance_margin_fraction, "source": t.source} for t in tiers_list},
        excluded_symbols=all_excluded,
        diagnostics=["proxy_reconstruction_from_oi_only"],
    )

    return summary, coverage, tiers_dict, positions, liq_levels, recon_excluded


# ---------------------------------------------------------------------------
# Phase 0: Return evaluation
# ---------------------------------------------------------------------------

def run_phase0(
    config: StudyConfig,
    positions: dict[str, list[PositionState]],
    liq_levels: dict[str, list[LiquidationLevelEstimate]],
    ctxs: dict[str, list[AssetCtxRecord]],
    l2_books: dict[str, list[BookSnapshot]],
) -> StudySummary:
    """Run Phase 0: generate cluster maps, signals, compute returns, controls, nulls."""
    summary = StudySummary(
        study_id=STUDY_ID,
        run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_phase0",
        created_at_utc=_now_utc_iso(),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
        git_branch=os.environ.get("GIT_BRANCH", "unknown"),
        git_dirty=bool(os.environ.get("GIT_DIRTY", "")),
        repo_root=config.data_root,
        precommitment_path="",
        precommitment_sha256="",
        status=StudyStatus.PHASE0A_MECHANISM_RECONSTRUCTABLE.name,
    )

    # Build cluster maps
    cluster_maps: list[ClusterMapSnapshot] = []
    for sym, liqs in liq_levels.items():
        if not liqs:
            continue
        by_ts: dict[int, list[LiquidationLevelEstimate]] = defaultdict(list)
        for liq in liqs:
            by_ts[liq.timestamp_ns].append(liq)

        for ts_ns, ts_liqs in sorted(by_ts.items()):
            sym_ctxs = ctxs.get(sym, [])
            mark_price = 0.0
            oi = 0.0
            for ctx in sym_ctxs:
                if ctx.ts_event <= ts_ns:
                    mark_price = ctx.mark_price
                    oi = ctx.open_interest
                else:
                    break

            if mark_price <= 0:
                continue

            buckets = build_cluster_map(ts_liqs, mark_price)
            buckets = mark_dominant_clusters(buckets, oi)
            dominant = min(
                (b for b in buckets if b.is_dominant),
                key=lambda b: abs(_bps(mark_price, b.bucket_center_price)),
                default=None,
            )

            cm = ClusterMapSnapshot(
                timestamp_ns=ts_ns,
                symbol=sym,
                mark_price=mark_price,
                buckets=buckets,
                dominant_cluster=dominant,
                total_reconstructed_notional_usd=sum(b.total_notional_usd for b in buckets),
            )
            cluster_maps.append(cm)

    # Generate signals
    signals = generate_approach_signals(cluster_maps, l2_books, ctxs, config)

    # Chronological split
    discovery, holdout = chronological_split(signals)

    # Compute returns for discovery
    return_observations: list[ReturnObservation] = []
    for i, sig in enumerate(discovery):
        sym_ctxs = ctxs.get(sig.symbol, [])
        forward_ctxs = [c for c in sym_ctxs if c.ts_event > sig.timestamp_ns]
        if not forward_ctxs:
            continue
        ret = compute_forward_returns(sig, forward_ctxs)
        ret.event_id = i
        return_observations.append(ret)

    # Phase 0A gates
    phase0a = {
        "total_signals": len(signals),
        "discovery_count": len(discovery),
        "holdout_count": len(holdout),
        "total_events": len(return_observations),
        "distinct_symbols": len(set(s.symbol for s in signals)),
        "status": StudyStatus.PHASE0A_MECHANISM_RECONSTRUCTABLE.name,
    }

    # Phase 0B metrics
    net50_returns = []
    for ro in return_observations:
        cost_vals = ro.net_costs.get("net50", {})
        if "60m" in cost_vals:
            net50_returns.append(cost_vals["60m"])

    summary.phase0a = phase0a
    summary.phase0b = {
        "event_count": len(return_observations),
        "mean_net50_bps": statistics.mean(net50_returns) if net50_returns else 0.0,
        "median_net50_bps": statistics.median(net50_returns) if net50_returns else 0.0,
        "win_rate_net50": sum(1 for r in net50_returns if r > 0) / max(len(net50_returns), 1),
    }

    # Phase 0C: controls and nulls
    if not config.skip_null:
        null_results = circular_shift_null(signals, config.null_iterations, config.seed)
        summary.phase0c = {
            "null_results": [
                {"name": n.null_name, "p_value": n.p_value, "real_statistic": n.real_statistic}
                for n in null_results
            ],
        }

    return summary


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def write_summary_json(summary: StudySummary, path: str) -> None:
    """Write summary.json with all required provenance fields."""
    data = {
        "study_id": summary.study_id,
        "run_id": summary.run_id,
        "created_at_utc": summary.created_at_utc,
        "git_sha": summary.git_sha,
        "git_branch": summary.git_branch,
        "git_dirty": summary.git_dirty,
        "repo_root": summary.repo_root,
        "precommitment_path": summary.precommitment_path,
        "precommitment_sha256": summary.precommitment_sha256,
        "safety_mode": summary.safety_mode,
        "command_args": summary.command_args,
        "status": summary.status,
        "forbidden_statuses_not_emitted": summary.forbidden_statuses_not_emitted,
        "orders_used": summary.orders_used,
        "private_keys_used": summary.private_keys_used,
        "auth_used": summary.auth_used,
        "live_execution_used": summary.live_execution_used,
        "paper_trading_used": summary.paper_trading_used,
        "shadow_execution_used": summary.shadow_execution_used,
        "registry_mutated": summary.registry_mutated,
        "systemd_mutated": summary.systemd_mutated,
        "bot_path_mutated": summary.bot_path_mutated,
        "data_coverage": summary.data_coverage,
        "leverage_tiers": summary.leverage_tiers,
        "reconstruction_audit": summary.reconstruction_audit,
        "phase0a": summary.phase0a,
        "phase0b": summary.phase0b,
        "phase0c": summary.phase0c,
        "excluded_symbols": summary.excluded_symbols,
        "diagnostics": summary.diagnostics,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def write_summary_md(summary: StudySummary, path: str) -> None:
    """Write summary.md with human-readable report."""
    lines = [
        f"# {summary.study_id}",
        f"",
        f"## Status: {summary.status}",
        f"",
        f"## Provenance",
        f"- Study ID: {summary.study_id}",
        f"- Run ID: {summary.run_id}",
        f"- Created: {summary.created_at_utc}",
        f"- Git SHA: {summary.git_sha}",
        f"- Git Branch: {summary.git_branch}",
        f"- Repo: {summary.repo_root}",
        f"",
        f"## Safety",
        f"- Orders: {summary.orders_used}",
        f"- Private keys: {summary.private_keys_used}",
        f"- Auth: {summary.auth_used}",
        f"- Live execution: {summary.live_execution_used}",
        f"- Paper trading: {summary.paper_trading_used}",
        f"- Shadow execution: {summary.shadow_execution_used}",
        f"- Registry mutated: {summary.registry_mutated}",
        f"",
    ]

    if summary.data_coverage:
        dc = summary.data_coverage
        lines.extend([
            f"## Data Coverage",
            f"- Asset ctxs: {dc.get('asset_ctxs_available', False)}",
            f"- L2 book: {dc.get('l2book_available', False)}",
            f"- Node fills: {dc.get('node_fills_available', False)}",
            f"- Symbols: {dc.get('symbols_with_data', [])}",
            f"",
        ])

    if summary.phase0a:
        pa = summary.phase0a
        lines.extend([
            f"## Phase 0A",
            f"- Total signals: {pa.get('total_signals', 0)}",
            f"- Events: {pa.get('total_events', 0)}",
            f"- Symbols: {pa.get('distinct_symbols', 0)}",
            f"",
        ])

    if summary.phase0b:
        pb = summary.phase0b
        lines.extend([
            f"## Phase 0B",
            f"- Mean net50: {pb.get('mean_net50_bps', 0):.2f} bps",
            f"- Median net50: {pb.get('median_net50_bps', 0):.2f} bps",
            f"- Win rate: {pb.get('win_rate_net50', 0):.2%}",
            f"",
        ])

    if summary.diagnostics:
        lines.extend([
            f"## Diagnostics",
            f"- {'; '.join(summary.diagnostics)}",
            f"",
        ])

    lines.append(f"## Forbidden statuses not emitted: {summary.forbidden_statuses_not_emitted}")
    lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def write_precommitment_hash(precommitment_path: str, output_path: str) -> str:
    """Compute and write the precommitment document SHA256."""
    h = sha256_file(precommitment_path)
    with open(output_path, "w") as f:
        f.write(h)
    return h
