#!/usr/bin/env python3
"""
HIP-3 Builder-DEX TradFi Off-Hours Oracle-Basis Phase -1 Scout v0

Corrected discovery: queries builder DEX namespaces via perpDexs endpoint,
not just the default validator-operated universe.

This is a research-scaffold-only Phase -1 scout. NOT a strategy, PnL evaluator,
Phase 0 precommitment, paper/live trading unlock, or registry rejection.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

UTC = timezone.utc
STUDY_ID = "hip3_builder_dex_tradfi_offhours_scout_v0"
SCHEMA_VERSION = "1.0.0"
SAFETY_MODE = "public_data_observer_only"
SANITY_SEEDS = ["TSLA", "AAPL", "MSFT", "NVDA"]
ALL_SEED_TICKERS = [
    "TSLA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META",
    "SPX", "NDX", "NAS100", "QQQ", "GOLD", "XAU", "SILVER", "XAG",
    "OIL", "WTI", "BRENT",
]
FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
    "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
    "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED",
    "PROMOTION_AUTHORIZED", "EDGE_CONFIRMED",
})
ALLOWED_INFO_TYPES = frozenset({"perpDexs", "meta", "metaAndAssetCtxs"})
FORBIDDEN_INFO_TYPES = frozenset({"clearinghouseState", "userState", "openOrders"})
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HL_FRONTEND_BASE = "https://app.hyperliquid.xyz/trade"


# ═══════════════════════════════════════════════════════════════════
# Status Enums
# ═══════════════════════════════════════════════════════════════════

class DiscoveryStatus(str, Enum):
    SCOUT_READY = "HIP3_BUILDER_DEX_SCOUT_READY"
    PERP_DEXS_ENDPOINT_FAILED = "HIP3_PERP_DEXS_ENDPOINT_FAILED"
    NO_BUILDER_DEXS_FOUND = "HIP3_NO_BUILDER_DEXS_FOUND"
    BUILDER_DEXS_FOUND = "HIP3_BUILDER_DEXS_FOUND"
    BUILDER_DEX_META_FAILED = "HIP3_BUILDER_DEX_META_FAILED"
    BUILDER_DEX_META_AND_CTXS_FAILED = "HIP3_BUILDER_DEX_META_AND_CTXS_FAILED"
    TRADFI_SYMBOLS_FOUND = "HIP3_TRADFI_SYMBOLS_FOUND"
    SEED_TICKERS_NOT_FOUND = "HIP3_SEED_TICKERS_NOT_FOUND"
    NO_TRADFI_SYMBOLS_FOUND = "HIP3_NO_TRADFI_SYMBOLS_FOUND"
    FRONTEND_API_DESYNC = "HIP3_FRONTEND_API_DESYNC"
    SCOUT_ERROR = "HIP3_SCOUT_ERROR"


class ArchiveStatus(str, Enum):
    VISIBILITY_READY = "HIP3_BUILDER_ARCHIVE_VISIBILITY_READY"
    VISIBLE = "HIP3_BUILDER_ARCHIVE_VISIBLE"
    NOT_FOUND = "HIP3_BUILDER_ARCHIVE_NOT_FOUND"
    ACCESS_BLOCKED = "HIP3_BUILDER_ARCHIVE_ACCESS_BLOCKED"
    SCHEMA_UNKNOWN = "HIP3_BUILDER_ARCHIVE_SCHEMA_UNKNOWN"
    REQUESTER_PAYS_CREDENTIALS_REQUIRED = "HIP3_BUILDER_ARCHIVE_REQUESTER_PAYS_CREDENTIALS_REQUIRED"
    REQUESTER_PAYS_ACCESS_DENIED = "HIP3_BUILDER_ARCHIVE_REQUESTER_PAYS_ACCESS_DENIED"


class FeeLiqOracleStatus(str, Enum):
    FEE_READY = "HIP3_FEE_REALITY_READY"
    FEE_BLOCKED = "HIP3_FEE_REALITY_BLOCKED"
    L2_READY = "HIP3_L2_LIQUIDITY_READY"
    L2_INSUFFICIENT = "HIP3_L2_LIQUIDITY_INSUFFICIENT"
    L2_SUFFICIENT = "HIP3_L2_LIQUIDITY_SUFFICIENT"
    ORACLE_READY = "HIP3_ORACLE_ANCHOR_READY"
    ORACLE_BLOCKED = "HIP3_ORACLE_ANCHOR_BLOCKED"
    ORACLE_TRACKS_TIGHTLY = "HIP3_ORACLE_TRACKS_FAIR_VALUE_TIGHTLY"
    ORACLE_RESIDUAL_MEASURABLE = "HIP3_ORACLE_RESIDUAL_MEASURABLE"


class BasisTailStatus(str, Enum):
    READY = "HIP3_OFFHOURS_BASIS_TAIL_READY"
    NO_TAIL = "HIP3_NO_OFFHOURS_BASIS_TAIL"
    EXISTS = "HIP3_OFFHOURS_BASIS_TAIL_EXISTS"
    INCONCLUSIVE = "HIP3_OFFHOURS_BASIS_TAIL_INCONCLUSIVE"
    PHASE0_WARRANTED = "HIP3_PHASE0_PRECOMMITMENT_WARRANTED"
    PHASE0_NOT_WARRANTED = "HIP3_PHASE0_PRECOMMITMENT_NOT_WARRANTED"
    SCOUT_ERROR = "HIP3_SCOUT_ERROR"


class FinalGateStatus(str, Enum):
    BUILDER_SURFACE = "BUILDER_DEX_TRADFI_SURFACE_CONFIRMED"
    FRONTEND_DESYNC = "FRONTEND_API_DESYNC"
    ARCHIVE_BLOCKED = "ARCHIVE_VISIBILITY_BLOCKED"
    ANCHOR_BLOCKED = "ANCHOR_BLOCKED"
    LIQUIDITY_BLOCKED = "LIQUIDITY_BLOCKED"
    NO_TAIL = "NO_OFFHOURS_BASIS_TAIL"
    TAIL_INCONCLUSIVE = "OFFHOURS_BASIS_TAIL_INCONCLUSIVE"
    PHASE0_WARRANTED = "PHASE0_PRECOMMITMENT_WARRANTED"
    PENDING_SECOND_RUN = "OFFHOURS_BASIS_TAIL_INCONCLUSIVE_PENDING_SECOND_RUN"


# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoutConfig:
    out_root: str = "reports/hip3_builder_dex_tradfi_offhours_scout_v0"
    seed_tickers: list = field(default_factory=lambda: list(ALL_SEED_TICKERS))
    start_date: str = "2025-10-13"
    end_date: str = "latest"
    max_symbols: int = 12
    max_archive_days_per_symbol: int = 7
    max_l2_hours_per_symbol: int = 72
    download_budget_bytes: int = 500_000_000
    l2_budget_bytes: int = 250_000_000
    require_sanity_seeds: bool = True
    allow_network_public: bool = False
    allow_s3_archive_read: bool = False
    dry_run: bool = False
    run_id: str = ""
    study_id: str = STUDY_ID


@dataclass
class GateResult:
    phase: str
    status: str
    reason: str = ""
    computed_from_real_data: bool = False
    measurements: dict = field(default_factory=dict)
    blocked: bool = False


@dataclass
class PerpDexEntry:
    dex_index: int
    dex_name: str | None
    full_name: str | None
    deployer: str | None
    oracle_updater: str | None
    fee_recipient: str | None
    raw: dict = field(default_factory=dict)
    classification: str = "unknown_dex"


@dataclass
class DexMetaResult:
    dex_name: str
    universe: list = field(default_factory=list)
    universe_count: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class DexAssetCtxsResult:
    dex_name: str
    universe: list = field(default_factory=list)
    asset_ctxs: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class SymbolResolution:
    dex_name: str
    index_in_meta: int
    coin: str
    api_symbol: str
    asset_id: int
    max_leverage: int = 0
    margin_table_id: int = 0
    only_isolated: bool = False
    sz_decimals: int = 0
    mark: float = None
    oracle: float = None
    open_interest: float = None
    funding: float = None
    classification: str = "unknown"
    raw_universe: dict = field(default_factory=dict)
    raw_ctx: dict = field(default_factory=dict)


@dataclass
class FrontendApiCheck:
    ticker: str
    frontend_url: str
    frontend_head_status: int = 0
    frontend_non_404: bool = False
    api_resolved: bool = False
    resolved_dex: str = ""
    resolved_coin: str = ""
    resolved_api_symbol: str = ""
    resolved_asset_id: int = 0
    match_status: str = "neither"


@dataclass
class ArchiveProbeResult:
    symbol: str
    dex: str
    asset_id: int
    archive_visible: bool = False
    l2_visible: bool = False
    asset_ctxs_visible: bool = False
    paths_attempted: list = field(default_factory=list)
    official_bare_coin_l2_attempted: bool = False
    official_asset_ctxs_daily_attempted: bool = False
    l2_prefix_listing_attempted: bool = False
    bare_coin_l2_visible: bool = False
    asset_ctxs_daily_visible: bool = False
    asset_ctxs_symbol_present: bool = False
    archive_blocker_reason: str = ""


@dataclass
class FeeProbeResult:
    symbol: str
    dex: str
    conservative_taker_bps: float = None
    conservative_maker_bps: float = None
    deployer_surcharge_bps: float = None
    round_trip_fee_bps: float = None
    median_spread_bps: float = None
    p75_spread_bps: float = None
    p90_spread_bps: float = None
    total_conservative_cost_bps: float = None
    fee_confidence: str = "unknown"
    computed_from_real_data: bool = False


@dataclass
class OracleAnchorResult:
    symbol: str
    asset_class: str
    selected_anchor_class: str = "no_reliable_anchor"
    anchor_candidates_attempted: list = field(default_factory=list)
    anchor_selection_order: list = field(default_factory=list)
    anchor_staleness_seconds_median: float = None
    anchor_staleness_seconds_max: float = None
    timestamp_alignment_rate: float = None
    off_hours_aligned_samples: int = 0
    oracle_mark_correlation: float = None
    residual_bps_median: float = None
    tracks_fair_value_tightly: bool = False
    computed_from_real_data: bool = False


@dataclass
class L2DepthResult:
    symbol: str
    dex: str
    off_hours_samples: int = 0
    regular_hours_samples: int = 0
    median_spread_bps_offhours: float = None
    p75_spread_bps_offhours: float = None
    p90_spread_bps_offhours: float = None
    median_spread_bps_regular: float = None
    depth_100_both_sides_pct: float = None
    depth_500_both_sides_pct: float = None
    depth_1000_both_sides_pct: float = None
    stale_book_rate: float = None
    empty_side_rate: float = None
    sufficient: bool = False
    computed_from_real_data: bool = False


@dataclass
class BasisTailResult:
    symbol: str
    dex: str
    total_off_hours_samples: int = 0
    total_regular_hours_samples: int = 0
    residual_bps_mean: float = None
    residual_bps_median: float = None
    residual_bps_std: float = None
    tail_count_25bps: int = 0
    tail_count_50bps: int = 0
    tail_count_100bps: int = 0
    tail_count_above_cost: int = 0
    independent_tail_events: int = 0
    concentration_by_week: dict = field(default_factory=dict)
    dominated_by_one_week: bool = False
    weeks_with_events: int = 0
    anchor_staleness_explains: bool = False
    l2_tradeable_at_tail: bool = True
    status: str = "HIP3_OFFHOURS_BASIS_TAIL_INCONCLUSIVE"
    computed_from_real_data: bool = False


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _get_git_info():
    repo = Path(__file__).resolve().parent.parent.parent
    git_dir = repo / ".git"
    sha, dirty = "", False
    try:
        head_ref = (git_dir / "HEAD").read_text().strip()
        if head_ref.startswith("ref: "):
            ref_file = git_dir / head_ref[5:]
            if ref_file.exists():
                sha = ref_file.read_text().strip()[:10]
        else:
            sha = head_ref[:10]
        dirty = (git_dir / "index").stat().st_mtime > (git_dir / "HEAD").stat().st_mtime
    except Exception:
        pass
    return sha, dirty


def _now_utc():
    return datetime.now(UTC).isoformat()


def _config_hash(config):
    return hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str).encode()).hexdigest()[:16]


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _classify_symbol(name):
    upper = name.upper().strip()
    single_stocks = {
        "TSLA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META",
        "NFLX", "DIS", "BABA", "NIO", "PLTR", "SNOW", "COIN", "HOOD",
        "RIVN", "LCID", "SOFI", "PYPL", "SQ", "SHOP", "ROKU", "ZM",
        "ABNB", "UBER", "LYFT", "GRAB", "SE", "PDD", "JD", "BIDU",
    }
    if upper in single_stocks:
        return "single_stock_like"
    indices = {"SPX", "US500", "NDX", "NAS100", "DOW", "DJI", "XYZ100", "SPX500"}
    if upper in indices:
        return "index_like"
    etfs = {"QQQ", "SPY", "IWM", "DIA", "ARKK", "GLD", "SLV", "USO", "UNG"}
    if upper in etfs:
        return "etf_like"
    commodities = {
        "GOLD", "XAU", "SILVER", "XAG", "OIL", "WTI", "BRENT", "NATGAS",
        "COPPER", "XCU", "PLATINUM", "XPT", "PALLADIUM", "XPD",
    }
    if upper in commodities:
        return "commodity_like"
    if "/" in upper and len(upper) <= 7:
        return "fx_like"
    crypto_known = {
        "BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "DOT", "AVAX",
        "LINK", "MATIC", "ATOM", "UNI", "AAVE", "LTC", "BCH", "ETC",
    }
    if upper in crypto_known:
        return "crypto_like"
    return "unknown"



def _extract_coin_from_name(name_str):
    """Extract coin from universe name field like 'xyz:XYZ100' -> 'XYZ100'."""
    if not name_str:
        return ""
    if ":" in name_str:
        return name_str.split(":", 1)[1]
    return name_str

def _derive_builder_asset_id(perp_dex_index, index_in_meta):
    return 100000 + perp_dex_index * 10000 + index_in_meta


def _is_off_hours_equity(dt):
    if dt.weekday() >= 5:
        return True
    t = dt.hour * 60 + dt.minute
    return t < 570 or t >= 960


def _make_artifact_base(run_id, config, git_sha, git_dirty, branch, final_status):
    return {
        "study_id": STUDY_ID, "run_id": run_id, "created_at_utc": _now_utc(),
        "git_sha": git_sha, "git_dirty": git_dirty,
        "repo_root": str(Path(__file__).resolve().parent.parent.parent),
        "branch": branch, "safety_mode": SAFETY_MODE, "schema_version": SCHEMA_VERSION,
        "final_status": final_status, "bytes_downloaded_total": 0,
        "bytes_downloaded_by_source": {}, "public_endpoints_queried": [],
        "archive_prefixes_queried": [], "no_orders_no_auth_no_live_confirmation": True,
    }


def _validate_no_forbidden_status(status_str):
    if status_str in FORBIDDEN_STATUSES:
        raise ValueError(f"Forbidden status: {status_str}")


# ═══════════════════════════════════════════════════════════════════
# Chokepoints
# ═══════════════════════════════════════════════════════════════════

class PublicInfoChokepoint:
    def __init__(self, allow_network):
        self.allow_network = allow_network
        self.endpoints_queried = []
        self.bytes_downloaded = 0
        self.bytes_by_source = {}

    def _check(self):
        if not self.allow_network:
            raise RuntimeError("Network access denied: --allow-network-public not set")

    def post_info(self, payload, timeout=30):
        self._check()
        req_type = payload.get("type", "")
        if req_type in FORBIDDEN_INFO_TYPES:
            raise ValueError(f"Forbidden info request type: {req_type}")
        if req_type not in ALLOWED_INFO_TYPES:
            raise ValueError(f"Unknown info request type: {req_type}")
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(HL_INFO_URL, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            self.bytes_downloaded += len(data)
            self.bytes_by_source["hyperliquid_info"] = self.bytes_by_source.get("hyperliquid_info", 0) + len(data)
            self.endpoints_queried.append(f"POST {HL_INFO_URL} type={req_type}")
            return json.loads(data.decode("utf-8"))

    def head_frontend(self, ticker, timeout=15):
        self._check()
        url = f"{HL_FRONTEND_BASE}/{ticker}/USDC"
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0
        finally:
            self.endpoints_queried.append(f"HEAD {url}")


class ArchiveChokepoint:
    def __init__(self, allow_s3, budget_bytes):
        self.allow_s3 = allow_s3
        self.budget_bytes = budget_bytes
        self.bytes_downloaded = 0
        self.bytes_by_source = {}
        self.paths_attempted = []

    def probe_path(self, path):
        result = {"path": path, "attempt_type": "archive_probe",
                  "status_code_or_outcome": "not_attempted", "bytes_read": 0,
                  "error_summary": None, "timestamp_utc": _now_utc()}
        if not self.allow_s3:
            result["status_code_or_outcome"] = "blocked_no_s3_flag"
            self.paths_attempted.append(result)
            return result
        if self.bytes_downloaded >= self.budget_bytes:
            result["status_code_or_outcome"] = "budget_exceeded"
            self.paths_attempted.append(result)
            return result
        try:
            req = urllib.request.Request(path)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read(1024 * 1024)
                nbytes = len(data)
                self.bytes_downloaded += nbytes
                src = path.split("/")[2] if "/" in path else path
                self.bytes_by_source[src] = self.bytes_by_source.get(src, 0) + nbytes
                result["status_code_or_outcome"] = "200"
                result["bytes_read"] = nbytes
        except urllib.error.HTTPError as e:
            result["status_code_or_outcome"] = str(e.code)
            result["error_summary"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            result["status_code_or_outcome"] = "error"
            result["error_summary"] = str(e)[:200]
        self.paths_attempted.append(result)
        return result


# ═══════════════════════════════════════════════════════════════════
# Phase A — Corrected builder-DEX discovery
# ═══════════════════════════════════════════════════════════════════

def phase_a_discovery(config, chokepoint, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch,
                               DiscoveryStatus.SCOUT_READY.value)
    try:
        perp_dexs_resp = chokepoint.post_info({"type": "perpDexs"})
    except Exception as e:
        gate = GateResult(phase="A", status=DiscoveryStatus.PERP_DEXS_ENDPOINT_FAILED.value,
                          reason=f"perpDexs endpoint failed: {e}", blocked=True)
        return gate, base

    dex_list = [x for x in (perp_dexs_resp if isinstance(perp_dexs_resp, list) else []) if x is not None]
    entries = []
    for i, raw in enumerate(dex_list):
        name = raw.get("name") if raw.get("name") else None
        entries.append(PerpDexEntry(
            dex_index=i, dex_name=name, full_name=raw.get("fullName"),
            deployer=raw.get("deployer"), oracle_updater=raw.get("oracleUpdater"),
            fee_recipient=raw.get("feeRecipient"), raw=raw,
            classification="builder_dex" if name else "default_validator_dex"))

    builder_dexes = [e for e in entries if e.classification == "builder_dex"]
    inventory = {**base,
                 "final_status": DiscoveryStatus.BUILDER_DEXS_FOUND.value if builder_dexes else DiscoveryStatus.NO_BUILDER_DEXS_FOUND.value,
                 "total_dex_count": len(entries), "builder_dex_count": len(builder_dexes),
                 "entries": [{"dex_index": e.dex_index, "dex_name": e.dex_name,
                              "full_name": e.full_name, "deployer": e.deployer,
                              "classification": e.classification} for e in entries]}
    _write_json(run_dir / "perp_dexs_inventory.json", inventory)

    if not builder_dexes:
        gate = GateResult(phase="A", status=DiscoveryStatus.NO_BUILDER_DEXS_FOUND.value,
                          reason="No builder DEXs found", blocked=True)
        return gate, inventory

    # Default baseline
    try:
        default_resp = chokepoint.post_info({"type": "metaAndAssetCtxs"})
        if isinstance(default_resp, (list, tuple)) and len(default_resp) >= 1 and isinstance(default_resp[0], dict):
            du = default_resp[0].get("universe", [])
            default_baseline = {**base, "final_status": "default_baseline_captured",
                                "universe_count": len(du),
                                "sample_symbols": [_extract_coin_from_name(u.get("name", "")) for u in du[:10]]}
        else:
            default_baseline = {**base, "final_status": "default_baseline_empty"}
    except Exception as e:
        default_baseline = {**base, "final_status": "default_baseline_failed", "error": str(e)[:200]}
    _write_json(run_dir / "default_dex_baseline_inventory.json", default_baseline)

    # Query each builder DEX
    builder_meta_results = []
    builder_ctxs_results = []
    for entry in builder_dexes:
        dex_name = entry.dex_name
        try:
            meta_resp = chokepoint.post_info({"type": "meta", "dex": dex_name})
            universe = meta_resp.get("universe", []) if isinstance(meta_resp, dict) else []
            builder_meta_results.append(DexMetaResult(dex_name=dex_name, universe=universe, universe_count=len(universe)))
        except Exception as e:
            builder_meta_results.append(DexMetaResult(dex_name=dex_name, raw={"error": str(e)[:200]}))

        try:
            ctxs_resp = chokepoint.post_info({"type": "metaAndAssetCtxs", "dex": dex_name})
            u2, ac = [], []
            if isinstance(ctxs_resp, (list, tuple)) and len(ctxs_resp) >= 1:
                if isinstance(ctxs_resp[0], dict):
                    u2 = ctxs_resp[0].get("universe", [])
                if len(ctxs_resp) >= 2 and isinstance(ctxs_resp[1], list):
                    ac = ctxs_resp[1]
            builder_ctxs_results.append(DexAssetCtxsResult(dex_name=dex_name, universe=u2, asset_ctxs=ac))
        except Exception as e:
            builder_ctxs_results.append(DexAssetCtxsResult(dex_name=dex_name, raw={"error": str(e)[:200]}))

    meta_inv = {**base, "final_status": DiscoveryStatus.BUILDER_DEXS_FOUND.value,
                "dex_count": len(builder_meta_results),
                "dexes": [{"dex_name": r.dex_name, "universe_count": r.universe_count,
                           "sample_coins": [_extract_coin_from_name(u.get("name", "")) for u in r.universe[:5]]}
                          for r in builder_meta_results]}
    _write_json(run_dir / "builder_dex_meta_inventory.json", meta_inv)

    ctxs_inv = {**base, "final_status": DiscoveryStatus.BUILDER_DEXS_FOUND.value,
                "dex_count": len(builder_ctxs_results),
                "dexes": [{"dex_name": r.dex_name, "universe_count": len(r.universe),
                           "asset_ctxs_count": len(r.asset_ctxs)} for r in builder_ctxs_results]}
    _write_json(run_dir / "builder_dex_asset_ctxs_inventory.json", ctxs_inv)

    gate = GateResult(phase="A", status=DiscoveryStatus.BUILDER_DEXS_FOUND.value,
                      reason=f"{len(builder_dexes)} builder DEXs discovered",
                      computed_from_real_data=True,
                      measurements={"builder_dex_count": len(builder_dexes),
                                     "total_symbols": sum(r.universe_count for r in builder_meta_results)})
    return gate, {"inventory": inventory, "builder_meta": meta_inv, "builder_ctxs": ctxs_inv,
                  "default_baseline": default_baseline, "gate": asdict(gate)}


# ═══════════════════════════════════════════════════════════════════
# Phase A2 — Frontend/API consistency check
# ═══════════════════════════════════════════════════════════════════

def phase_a2_frontend_api(config, chokepoint, builder_meta_results, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, DiscoveryStatus.SCOUT_READY.value)
    if config.dry_run:
        result = {**base, "final_status": "dry_run_skipped",
                  "frontend_api_consistency_check_skipped": True, "checks": []}
        _write_json(run_dir / "frontend_api_consistency_check.json", result)
        return GateResult(phase="A2", status=DiscoveryStatus.SCOUT_READY.value, reason="Dry run skipped"), result

    all_coins = {}
    for meta in builder_meta_results:
        for idx, u in enumerate(meta.universe):
            coin = _extract_coin_from_name(u.get("name", ""))
            all_coins.setdefault(coin.upper(), []).append(
                {"dex_name": meta.dex_name, "coin": coin, "asset_id": _derive_builder_asset_id(0, idx), "index": idx})

    checks = []
    for ticker in SANITY_SEEDS:
        frontend_url = f"{HL_FRONTEND_BASE}/{ticker}/USDC"
        fs = chokepoint.head_frontend(ticker)
        fn4 = fs not in (0, 404, 403)
        ar = ticker.upper() in all_coins
        res = all_coins.get(ticker.upper(), [{}])[0] if ar else {}
        if fn4 and ar:
            ms = "consistent"
        elif fn4 and not ar:
            ms = "frontend_only"
        elif not fn4 and ar:
            ms = "api_only"
        else:
            ms = "neither"
        checks.append(asdict(FrontendApiCheck(
            ticker=ticker, frontend_url=frontend_url, frontend_head_status=fs,
            frontend_non_404=fn4, api_resolved=ar,
            resolved_dex=res.get("dex_name", ""), resolved_coin=res.get("coin", ""),
            resolved_api_symbol=f"{res.get('dex_name', '')}:{res.get('coin', '')}" if ar else "",
            resolved_asset_id=res.get("asset_id", 0), match_status=ms)))

    has_fo = any(c["match_status"] == "frontend_only" for c in checks)
    api_ct = sum(1 for c in checks if c["api_resolved"])
    fe_ct = sum(1 for c in checks if c["frontend_non_404"])

    if has_fo:
        st, reason = DiscoveryStatus.FRONTEND_API_DESYNC.value, "Frontend visible but API cannot resolve"
    elif api_ct == 0 and fe_ct > 0:
        st, reason = DiscoveryStatus.SCOUT_ERROR.value, "KNOWN_FRONTEND_SYMBOLS_NOT_REACHABLE_VIA_PUBLIC_API"
    elif api_ct > 0:
        st, reason = DiscoveryStatus.BUILDER_DEXS_FOUND.value, f"{api_ct}/{len(SANITY_SEEDS)} seeds resolved"
    else:
        st, reason = DiscoveryStatus.NO_TRADFI_SYMBOLS_FOUND.value, "No seeds resolved"

    result = {**base, "final_status": st, "checks": checks,
              "api_resolved_count": api_ct, "frontend_non404_count": fe_ct, "has_frontend_only_desync": has_fo}
    _write_json(run_dir / "frontend_api_consistency_check.json", result)
    blocked = has_fo or (api_ct == 0 and fe_ct > 0 and config.require_sanity_seeds)
    return GateResult(phase="A2", status=st, reason=reason, computed_from_real_data=True,
                      measurements={"api_resolved": api_ct, "frontend_non404": fe_ct}, blocked=blocked), result


# ═══════════════════════════════════════════════════════════════════
# Phase B — Target symbol resolution
# ═══════════════════════════════════════════════════════════════════

def phase_b_resolution(config, builder_meta_results, builder_ctxs_results, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, DiscoveryStatus.SCOUT_READY.value)
    all_res, all_cls = [], []
    ctx_lookup = {c.dex_name: c.asset_ctxs for c in builder_ctxs_results}

    for meta in builder_meta_results:
        dex_name = meta.dex_name
        ctxs = ctx_lookup.get(dex_name, [])
        for idx, u in enumerate(meta.universe):
            coin = _extract_coin_from_name(u.get("name", ""))
            asset_id = _derive_builder_asset_id(0, idx)
            ctx = ctxs[idx] if idx < len(ctxs) and isinstance(ctxs[idx], dict) else {}
            classification = _classify_symbol(coin)
            mark_val = ctx.get("markPrice")
            oracle_val = ctx.get("oraclePrice")
            sym = SymbolResolution(
                dex_name=dex_name, index_in_meta=idx, coin=coin,
                api_symbol=f"{dex_name}:{coin}", asset_id=asset_id,
                max_leverage=u.get("maxLeverage", 0), margin_table_id=u.get("marginTableId", 0),
                only_isolated=u.get("onlyIsolated", False), sz_decimals=u.get("szDecimals", 0),
                mark=float(mark_val) if mark_val else None,
                oracle=float(oracle_val) if oracle_val else None,
                open_interest=float(ctx.get("openInterest")) if ctx.get("openInterest") else None,
                funding=float(ctx.get("funding")) if ctx.get("funding") else None,
                classification=classification, raw_universe=u, raw_ctx=ctx)
            all_res.append(sym)
            all_cls.append({"dex_name": dex_name, "coin": coin, "api_symbol": f"{dex_name}:{coin}",
                            "asset_id": asset_id, "classification": classification,
                            "max_leverage": sym.max_leverage, "sz_decimals": sym.sz_decimals,
                            "mark": sym.mark, "oracle": sym.oracle})

    resolved_seeds, missing_seeds = {}, []
    for t in config.seed_tickers:
        found = [r for r in all_res if r.coin.upper() == t.upper()]
        resolved_seeds[t] = asdict(found[0]) if found else None
        if not found:
            missing_seeds.append(t)

    tradfi = [c for c in all_cls if c["classification"] in ("single_stock_like", "index_like", "etf_like", "commodity_like")]
    has_primary = any(s in resolved_seeds and resolved_seeds[s] for s in ["TSLA", "AAPL", "MSFT", "NVDA"])

    st = DiscoveryStatus.TRADFI_SYMBOLS_FOUND.value if (has_primary or tradfi) else (
        DiscoveryStatus.NO_TRADFI_SYMBOLS_FOUND.value if all_res else DiscoveryStatus.SEED_TICKERS_NOT_FOUND.value)

    class_counts = {}
    for c in all_cls:
        class_counts[c["classification"]] = class_counts.get(c["classification"], 0) + 1

    res_art = {**base, "final_status": st, "total_symbols": len(all_res),
               "resolved_seeds": resolved_seeds, "missing_seeds": missing_seeds,
               "tradfi_count": len(tradfi), "class_counts": class_counts}
    cls_art = {**base, "final_status": st, "symbols": all_cls, "tradfi_symbols": tradfi}
    _write_json(run_dir / "target_symbol_resolution.json", res_art)
    _write_json(run_dir / "tradfi_candidate_classification.json", cls_art)

    blocked = not has_primary and not tradfi
    return GateResult(phase="B", status=st, reason=f"{len(tradfi)} TradFi, {len(all_res)} total",
                      computed_from_real_data=True,
                      measurements={"total": len(all_res), "tradfi": len(tradfi), "seeds": len(resolved_seeds) - len(missing_seeds)},
                      blocked=blocked), {"resolution": res_art, "classification": cls_art, "all_res": all_res, "tradfi": tradfi}


# ═══════════════════════════════════════════════════════════════════
# Phase D — Fee reality
# ═══════════════════════════════════════════════════════════════════

def phase_d_fees(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.FEE_READY.value)
    fee_probes = []
    for sym in tradfi_symbols[:config.max_symbols]:
        # Conservative defaults for Hyperliquid HIP-3 builder perps
        # Taker ~1-3.5 bps, maker ~0-1 bps, deployer surcharge unknown
        fee_probes.append(asdict(FeeProbeResult(
            symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""),
            conservative_taker_bps=3.5, conservative_maker_bps=1.0,
            deployer_surcharge_bps=None, round_trip_fee_bps=7.0,
            median_spread_bps=None, p75_spread_bps=None, p90_spread_bps=None,
            total_conservative_cost_bps=7.0, fee_confidence="conservative_default",
            computed_from_real_data=False)))
    artifact = {**base, "final_status": FeeLiqOracleStatus.FEE_READY.value,
                "fee_probes": fee_probes, "note": "Conservative defaults; L2 spread data not yet available"}
    _write_json(run_dir / "fee_reality_probe.json", artifact)
    return GateResult(phase="D", status=FeeLiqOracleStatus.FEE_READY.value,
                      reason="Conservative fee bounds established",
                      computed_from_real_data=False,
                      measurements={"symbols": len(fee_probes)}), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase E — Oracle/anchor classification
# ═══════════════════════════════════════════════════════════════════

ANCHOR_ORDER = {
    "single_stock_like": ["extended_hours_equity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "index_like": ["futures_proxy_anchor", "index_etf_proxy_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "etf_like": ["extended_hours_equity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "commodity_like": ["commodity_futures_anchor", "spot_commodity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "unknown": ["no_reliable_anchor"],
}

def phase_e_oracle(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.ORACLE_READY.value)
    results = []
    any_measurable = False
    for sym in tradfi_symbols[:config.max_symbols]:
        cls = sym.get("classification", "unknown")
        order = ANCHOR_ORDER.get(cls, ANCHOR_ORDER["unknown"])
        # No actual anchor fetch in Phase -1 scout — mark as blocked
        results.append(asdict(OracleAnchorResult(
            symbol=sym.get("coin", ""), asset_class=cls,
            selected_anchor_class="no_reliable_anchor",
            anchor_candidates_attempted=[], anchor_selection_order=order,
            computed_from_real_data=False)))
    artifact = {**base, "final_status": FeeLiqOracleStatus.ORACLE_BLOCKED.value,
                "results": results, "note": "No public anchor fetched in Phase -1; anchor order recorded"}
    _write_json(run_dir / "oracle_anchor_classification.json", artifact)
    return GateResult(phase="E", status=FeeLiqOracleStatus.ORACLE_BLOCKED.value,
                      reason="No anchor data fetched; order recorded",
                      computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase F — L2 depth/spread feasibility
# ═══════════════════════════════════════════════════════════════════

def phase_f_liquidity(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.L2_READY.value)
    results = []
    for sym in tradfi_symbols[:config.max_symbols]:
        results.append(asdict(L2DepthResult(
            symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""),
            computed_from_real_data=False)))
    artifact = {**base, "final_status": FeeLiqOracleStatus.L2_INSUFFICIENT.value,
                "results": results, "note": "No L2 data fetched in Phase -1 scout"}
    _write_json(run_dir / "l2_depth_spread_probe.json", artifact)
    return GateResult(phase="F", status=FeeLiqOracleStatus.L2_INSUFFICIENT.value,
                      reason="No L2 data; liquidity unbounded",
                      computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase G — Off-hours residual basis-tail existence
# ═══════════════════════════════════════════════════════════════════

def phase_g_basis_tail(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, BasisTailStatus.INCONCLUSIVE.value)
    results = []
    for sym in tradfi_symbols[:config.max_symbols]:
        results.append(asdict(BasisTailResult(
            symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""),
            status=BasisTailStatus.INCONCLUSIVE.value,
            computed_from_real_data=False)))
    artifact = {**base, "final_status": BasisTailStatus.INCONCLUSIVE.value,
                "results": results, "note": "No archive/anchor data; sample size = 0; INCONCLUSIVE"}
    _write_json(run_dir / "offhours_basis_tail_probe.json", artifact)
    return GateResult(phase="G", status=BasisTailStatus.INCONCLUSIVE.value,
                      reason="No data; sample count = 0; underpowered",
                      computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase H — Corrective registry note preview
# ═══════════════════════════════════════════════════════════════════

def phase_h_registry_note(config, gate_results, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, "corrective_preview")
    statuses = [g.status for g in gate_results]
    final_gate = _determine_final_gate(gate_results)

    note = f"""# Corrective Registry Note — HIP-3 Builder-DEX TradFi Scout v0

## Prior entry heading
HIP-3 Builder-Deployed Off-Hours Oracle-Basis Residual — Public Discovery Unresolved

## Correction summary
The prior discovery work queried only the default validator-operated perp DEX universe
(metaAndAssetCtxs without the dex parameter). Builder-deployed HIP-3 perps live under
separate DEX namespaces accessible via the `perpDexs` endpoint and the `dex` parameter
on `meta`/`metaAndAssetCtxs` requests. The previous default-dex-only discovery result
is not evidence that HIP-3 builder-deployed TradFi markets are absent.

## New API route
Builder DEX enumeration via `perpDexs` is required before drawing any public-discovery
conclusion. Each returned builder DEX has its own universe queried via:
- `{{"type": "meta", "dex": "<dex_name>"}}`
- `{{"type": "metaAndAssetCtxs", "dex": "<dex_name>"}}`

## Frontend/API consistency result
Frontend trade URLs (e.g., `app.hyperliquid.xyz/trade/TSLA/USDC`) are used only as
sanity seeds; the scout must verify symbols through public API/archive data.

## New final status
{final_gate}

## Whether old note should be amended
Yes. The old "Public Discovery Unresolved" note should be read as a query-design gap
from querying the wrong API slice, not as exhaustion of the builder DEX public surface.

## Suggested wording
**This entry supersedes the prior "Public Discovery Unresolved" conclusion.**

Builder DEX enumeration via `perpDexs` was not performed in the prior scout. The
corrected scout confirmed that builder DEX namespaces exist and contain perp universes
accessible through the `dex` parameter. The prior no-findings result was based on
querying the default validator-operated universe only.

Registry posture: `{final_gate}`

A pass here does not prove profitability.
A fail here does not reject HIP-3 generally.
Phase 0 drafting is not authorized by this scout unless the final status is
`HIP3_PHASE0_PRECOMMITMENT_WARRANTED`, and even then the precommitment must be
drafted in a separate explicitly authorized run.
Single-run Phase 0 warrant is forbidden; two independent passing scouts are required.

## Gate statuses
{json.dumps(statuses, indent=2)}

## TradFi candidates found
{len(tradfi_symbols)} symbols classified as TradFi-like.

## No automatic registry mutation
This is a preview only. Registry correction requires the separate command:
`python -m examples.strategies.venue_agnostic_signal_observer.write_hip3_builder_dex_registry_correction_v0 --from-report <dir> --authorize`
"""
    preview_path = run_dir / "corrective_registry_note_preview.md"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(note)
    return GateResult(phase="H", status="corrective_preview_generated", reason="Preview written"), base


# ═══════════════════════════════════════════════════════════════════
# Phase I — Final gate verdict
# ═══════════════════════════════════════════════════════════════════

def _determine_final_gate(gate_results):
    for g in gate_results:
        if g.blocked:
            if g.phase == "A":
                return FinalGateStatus.ARCHIVE_BLOCKED.value if "archive" in g.reason.lower() else FinalGateStatus.BUILDER_SURFACE.value
            if g.phase == "A2":
                if "FRONTEND_API_DESYNC" in g.status:
                    return FinalGateStatus.FRONTEND_DESYNC.value
                if "KNOWN_FRONTEND" in g.reason:
                    return FinalGateStatus.FRONTEND_DESYNC.value
                return FinalGateStatus.FRONTEND_DESYNC.value
            if g.phase == "B":
                return FinalGateStatus.BUILDER_SURFACE.value
            if g.phase == "C":
                return FinalGateStatus.ARCHIVE_BLOCKED.value
            if g.phase == "E":
                return FinalGateStatus.ANCHOR_BLOCKED.value
            if g.phase == "F":
                return FinalGateStatus.LIQUIDITY_BLOCKED.value
            if g.phase == "G":
                if "INCONCLUSIVE" in g.status:
                    return FinalGateStatus.TAIL_INCONCLUSIVE.value
                return FinalGateStatus.NO_TAIL.value
    # Check if any phase has INCONCLUSIVE
    for g in gate_results:
        if "INCONCLUSIVE" in g.status:
            return FinalGateStatus.TAIL_INCONCLUSIVE.value
    # If we got through most phases but blocked at oracle/liquidity
    has_tradfi = any(g.measurements.get("tradfi", 0) > 0 for g in gate_results if g.phase == "B")
    if has_tradfi:
        return FinalGateStatus.BUILDER_SURFACE.value
    return FinalGateStatus.TAIL_INCONCLUSIVE.value


def phase_i_verdict(config, gate_results, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, "")
    final_status = _determine_final_gate(gate_results)
    base["final_status"] = final_status

    # Check for prior independent run evidence
    prior_evidence_path = run_dir.parent / "prior_independent_run_evidence.json"
    has_prior = prior_evidence_path.exists()
    if not has_prior:
        # Check in current run dir
        has_prior = (run_dir / "prior_independent_run_evidence.json").exists()

    # If tail exists but no prior run, downgrade
    if final_status == FinalGateStatus.PHASE0_WARRANTED.value and not has_prior:
        final_status = FinalGateStatus.PENDING_SECOND_RUN.value
        base["final_status"] = final_status

    gate_decisions = {**base, "final_status": final_status,
                      "phase_results": [{"phase": g.phase, "status": g.status,
                                         "reason": g.reason, "blocked": g.blocked} for g in gate_results],
                      "prior_independent_run_evidence": has_prior}
    _write_json(run_dir / "gate_decisions.json", gate_decisions)
    return GateResult(phase="I", status=final_status, reason="Final verdict",
                      computed_from_real_data=any(g.computed_from_real_data for g in gate_results)), gate_decisions


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# Phase C — Official S3 archive-path audit with date discovery
# ═══════════════════════════════════════════════════════════════════

import re as _re
import io as _io

S3_BUCKET = "hyperliquid-archive"
S3_BASE_URL = "https://hyperliquid-history.s3.us-east-1.amazonaws.com"
SANITY_SEEDS_PRIMARY = ["TSLA", "AAPL", "MSFT", "NVDA"]

# ── S3 Outcome Taxonomy ──
class S3Outcome:
    OBJECT_EXISTS = "S3_OBJECT_EXISTS"
    NO_SUCH_KEY = "S3_NO_SUCH_KEY"
    PREFIX_EMPTY = "S3_PREFIX_EMPTY"
    PREFIX_LISTED = "S3_PREFIX_LISTED"
    ACCESS_DENIED = "S3_ACCESS_DENIED"
    REQUESTER_PAYS_CREDENTIALS_REQUIRED = "S3_REQUESTER_PAYS_CREDENTIALS_REQUIRED"
    REQUESTER_PAYS_ACCESS_DENIED = "S3_REQUESTER_PAYS_ACCESS_DENIED"
    REDIRECT_OR_REGION_MISMATCH = "S3_REDIRECT_OR_REGION_MISMATCH"
    TRANSPORT_ERROR = "S3_TRANSPORT_ERROR"
    BOTO3_UNAVAILABLE = "S3_BOTO3_UNAVAILABLE"
    UNKNOWN_ERROR = "S3_UNKNOWN_ERROR"


def _classify_s3_outcome(exc=None, status_code=None, error_str=""):
    """Classify an S3 exception or HTTP status into the outcome taxonomy."""
    if status_code in (301, 307, 308):
        return S3Outcome.REDIRECT_OR_REGION_MISMATCH
    if status_code == 404:
        return S3Outcome.NO_SUCH_KEY
    if status_code in (403,):
        if "RequestPayer" in error_str or "requester" in error_str.lower():
            return S3Outcome.REQUESTER_PAYS_ACCESS_DENIED
        return S3Outcome.ACCESS_DENIED
    if exc is not None:
        import botocore.exceptions
        if isinstance(exc, botocore.exceptions.NoCredentialsError):
            return S3Outcome.REQUESTER_PAYS_CREDENTIALS_REQUIRED
        if isinstance(exc, botocore.exceptions.ClientError):
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "NoSuchKey":
                return S3Outcome.NO_SUCH_KEY
            if code == "404":
                return S3Outcome.NO_SUCH_KEY
            if code in ("AccessDenied", "403"):
                return S3Outcome.ACCESS_DENIED
            if code in ("301", "MovedPermanently"):
                return S3Outcome.REDIRECT_OR_REGION_MISMATCH
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return S3Outcome.TRANSPORT_ERROR
    if error_str:
        lower = error_str.lower()
        if "301" in lower or "redirect" in lower or "moved" in lower:
            return S3Outcome.REDIRECT_OR_REGION_MISMATCH
        if "nosuchkey" in lower or "not found" in lower or "404" in lower:
            return S3Outcome.NO_SUCH_KEY
        if "access" in lower and "denied" in lower:
            return S3Outcome.ACCESS_DENIED
        if "credential" in lower:
            return S3Outcome.REQUESTER_PAYS_CREDENTIALS_REQUIRED
        if "timeout" in lower or "connection" in lower:
            return S3Outcome.TRANSPORT_ERROR
    return S3Outcome.UNKNOWN_ERROR


def _get_s3_client():
    """Get a boto3 S3 client for public requester-pays reads."""
    try:
        import boto3
        from botocore.config import Config
        return boto3.client("s3", region_name="us-east-1",
                            config=Config(signature_version="s3v4"))
    except ImportError:
        return None
    except Exception:
        return None


def _s3_list_prefix(client, bucket, prefix, max_keys=500):
    """List objects under an S3 prefix. Returns (outcome, keys)."""
    if client is None:
        return S3Outcome.BOTO3_UNAVAILABLE, []
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix,
                                      MaxKeys=max_keys, RequestPayer="requester")
        contents = resp.get("Contents", [])
        keys = [obj["Key"] for obj in contents]
        is_truncated = resp.get("IsTruncated", False)
        if not keys:
            return S3Outcome.PREFIX_EMPTY, []
        return S3Outcome.PREFIX_LISTED, keys
    except Exception as e:
        return _classify_s3_outcome(exc=e, error_str=str(e)), []


def _s3_head_object(client, bucket, key):
    """HEAD an S3 object. Returns (outcome, metadata)."""
    if client is None:
        return S3Outcome.BOTO3_UNAVAILABLE, {}
    try:
        resp = client.head_object(Bucket=bucket, Key=key, RequestPayer="requester")
        return S3Outcome.OBJECT_EXISTS, {"size": resp.get("ContentLength", 0),
                                          "last_modified": str(resp.get("LastModified", ""))}
    except Exception as e:
        return _classify_s3_outcome(exc=e, error_str=str(e)), {}


def _s3_get_object_bytes(client, bucket, key, max_bytes=15_000_000):
    """Get first max_bytes of an S3 object. Returns (outcome, data_bytes)."""
    if client is None:
        return S3Outcome.BOTO3_UNAVAILABLE, b""
    try:
        resp = client.get_object(Bucket=bucket, Key=key, RequestPayer="requester",
                                 Range=f"bytes=0-{max_bytes - 1}")
        data = resp["Body"].read(max_bytes)
        return S3Outcome.OBJECT_EXISTS, data
    except Exception as e:
        return _classify_s3_outcome(exc=e, error_str=str(e)), b""


def _discover_archive_dates(client):
    """Discover available dates in the public S3 archive."""
    result = {
        "asset_ctxs_dates_available": [],
        "market_data_dates_available": [],
        "latest_confirmed_asset_ctxs_date": None,
        "latest_confirmed_market_data_date": None,
        "archive_timeliness_warning": "Hyperliquid archive uploaded ~monthly, may be delayed",
        "requester_pays_status": "required_for_listing",
    }

    # List asset_ctxs dates
    outcome, keys = _s3_list_prefix(client, S3_BUCKET, "asset_ctxs/", max_keys=1000)
    if outcome == S3Outcome.PREFIX_LISTED:
        dates = []
        for k in keys:
            m = _re.search(r"asset_ctxs/(\d{8})\.csv\.lz4", k)
            if m:
                dates.append(m.group(1))
        dates.sort(reverse=True)
        result["asset_ctxs_dates_available"] = dates
        result["latest_confirmed_asset_ctxs_date"] = dates[0] if dates else None
    result["asset_ctxs_listing_outcome"] = outcome

    # List market_data dates
    outcome2, keys2 = _s3_list_prefix(client, S3_BUCKET, "market_data/", max_keys=1000)
    if outcome2 == S3Outcome.PREFIX_LISTED:
        date_set = set()
        for k in keys2:
            m = _re.search(r"market_data/(\d{8})/", k)
            if m:
                date_set.add(m.group(1))
        dates2 = sorted(date_set, reverse=True)
        result["market_data_dates_available"] = dates2
        result["latest_confirmed_market_data_date"] = dates2[0] if dates2 else None
    result["market_data_listing_outcome"] = outcome2

    return result


def _select_confirmed_sample_dates(date_discovery, max_samples=5):
    """Select sample dates from confirmed available archive dates only."""
    asset_dates = set(date_discovery.get("asset_ctxs_dates_available", []))
    market_dates = set(date_discovery.get("market_data_dates_available", []))
    all_dates = sorted(asset_dates | market_dates, reverse=True)

    if not all_dates:
        return [], ["ARCHIVE_DATE_UNAVAILABLE_OR_DELAYED"]

    # Prioritize: latest, weekday, weekend, 2026-04-30 if present
    selected = []
    caveats = []

    # Latest date
    if all_dates:
        selected.append(all_dates[0])

    # Latest weekday
    from datetime import datetime
    for d in all_dates:
        try:
            dt = datetime.strptime(d, "%Y%m%d")
            if dt.weekday() < 5 and d not in selected:
                selected.append(d)
                break
        except ValueError:
            pass

    # Latest weekend
    for d in all_dates:
        try:
            dt = datetime.strptime(d, "%Y%m%d")
            if dt.weekday() >= 5 and d not in selected:
                selected.append(d)
                break
        except ValueError:
            pass

    # 2026-04-30 if present
    if "20260430" in all_dates and "20260430" not in selected:
        selected.append("20260430")

    # Fill up to max_samples with most recent
    for d in all_dates:
        if len(selected) >= max_samples:
            break
        if d not in selected:
            selected.append(d)

    return selected[:max_samples], caveats


def _coin_variants(coin, dex):
    """Generate coin variants for archive probing."""
    variants = [coin]
    if dex:
        variants.extend([f"{dex}:{coin}", f"{dex}%3A{coin}", f"{dex}_{coin}", f"{dex}-{coin}"])
    return variants


def _s3_l2_key(date_str, hour_str, coin):
    """Official L2 key: market_data/YYYYMMDD/HH/l2Book/<coin>.lz4"""
    return f"market_data/{date_str}/{hour_str}/l2Book/{coin}.lz4"


def _s3_l2_prefix(date_str, hour_str):
    """Official L2 prefix: market_data/YYYYMMDD/HH/l2Book/"""
    return f"market_data/{date_str}/{hour_str}/l2Book/"


def _s3_asset_ctxs_key(date_str):
    """Official asset_ctxs key: asset_ctxs/YYYYMMDD.csv.lz4"""
    return f"asset_ctxs/{date_str}.csv.lz4"


def phase_c_archive(config, archive_cp, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    """Enhanced Phase C: date-discovering S3 archive audit with proper outcome classification."""
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, ArchiveStatus.VISIBILITY_READY.value)

    # ── Step 0: Get S3 client ──
    s3_client = _get_s3_client()
    boto3_available = s3_client is not None

    # ── Step 1: Discover available archive dates ──
    date_discovery = _discover_archive_dates(s3_client)

    # ── Step 2: Select confirmed sample dates ──
    sampled_confirmed, date_caveats = _select_confirmed_sample_dates(date_discovery)

    # Hours: one regular (14:00 UTC ~ 10:00 ET), one off-hours (02:00 UTC ~ 22:00 ET)
    sampled_hours = ["14", "02"]

    # ── Step 3: Build coin variants for primary seeds ──
    primary_variants = {}
    for seed in SANITY_SEEDS_PRIMARY:
        found = [s for s in tradfi_symbols if s.get("coin", "").upper() == seed]
        dex = found[0].get("dex_name", "xyz") if found else "xyz"
        primary_variants[seed] = _coin_variants(seed, dex)

    # ── Step 4: Exact L2 HEAD probes on confirmed dates ──
    l2_head_results = []
    redirect_count = 0
    nosuchkey_count = 0
    bare_coin_l2_found = False
    dex_qualified_l2_found = False

    for seed, variants in primary_variants.items():
        for date_str in sampled_confirmed[:3]:
            for hour_str in sampled_hours:
                for variant in variants[:2]:
                    key = _s3_l2_key(date_str, hour_str, variant)
                    outcome, meta = _s3_head_object(s3_client, S3_BUCKET, key)
                    l2_head_results.append({
                        "symbol": seed, "variant": variant,
                        "date": date_str, "hour": hour_str,
                        "key": key, "outcome": outcome,
                        "size": meta.get("size", 0)})
                    if outcome == S3Outcome.OBJECT_EXISTS:
                        if ":" not in variant:
                            bare_coin_l2_found = True
                        else:
                            dex_qualified_l2_found = True
                    elif outcome == S3Outcome.REDIRECT_OR_REGION_MISMATCH:
                        redirect_count += 1
                    elif outcome == S3Outcome.NO_SUCH_KEY:
                        nosuchkey_count += 1

    # ── Step 5: L2 prefix listings on confirmed dates ──
    l2_prefix_results = []
    l2_prefix_matches = []
    prefix_listed_count = 0

    for date_str in sampled_confirmed[:3]:
        for hour_str in sampled_hours:
            prefix = _s3_l2_prefix(date_str, hour_str)
            outcome, keys = _s3_list_prefix(s3_client, S3_BUCKET, prefix, max_keys=200)
            l2_prefix_results.append({
                "date": date_str, "hour": hour_str,
                "prefix": prefix, "outcome": outcome,
                "key_count": len(keys),
                "sample_keys": keys[:10]})
            if outcome == S3Outcome.PREFIX_LISTED:
                prefix_listed_count += 1
                # Search for seed symbols in listed keys
                for seed in SANITY_SEEDS_PRIMARY:
                    for k in keys:
                        if seed in k.upper():
                            l2_prefix_matches.append({"symbol": seed, "date": date_str,
                                                       "hour": hour_str, "key": k})
                            break

    # ── Step 6: Daily asset_ctxs read on confirmed dates ──
    asset_ctxs_results = []
    asset_ctxs_read_count = 0
    asset_ctxs_matches = {}
    asset_ctxs_bare_coin_found = False
    transport_error_count = 0

    for date_str in sampled_confirmed[:3]:
        key = _s3_asset_ctxs_key(date_str)
        outcome, data = _s3_get_object_bytes(s3_client, S3_BUCKET, key, max_bytes=15_000_000)
        result_entry = {"date": date_str, "key": key, "outcome": outcome,
                        "bytes_read": len(data), "matches": {}}
        if outcome == S3Outcome.OBJECT_EXISTS and len(data) > 0:
            asset_ctxs_read_count += 1
            # Try lz4 decompression
            try:
                import lz4.frame
                content = lz4.frame.decompress(data).decode("utf-8", errors="replace")
            except (ImportError, Exception):
                content = data.decode("utf-8", errors="replace")
            # Parse CSV properly: column 1 is "coin"
            coins_found = set()
            for line in content.split("\n"):
                parts = line.split(",")
                if len(parts) >= 2:
                    coins_found.add(parts[1].strip())
            # Check all seeds and their dex-qualified variants
            all_search_targets = list(SANITY_SEEDS_PRIMARY)
            for seed in SANITY_SEEDS_PRIMARY:
                all_search_targets.extend([f"xyz:{seed}", f"flx:{seed}", f"vntl:{seed}",
                                           f"hyna:{seed}", f"km:{seed}", f"abcd:{seed}",
                                           f"cash:{seed}", f"para:{seed}"])
            for target in all_search_targets:
                present = target in coins_found
                result_entry["matches"][target] = present
                asset_ctxs_matches[target] = asset_ctxs_matches.get(target, False) or present
                if present and ":" not in target:
                    asset_ctxs_bare_coin_found = True
        elif outcome == S3Outcome.TRANSPORT_ERROR:
            transport_error_count += 1
        asset_ctxs_results.append(result_entry)

    # ── Step 7: Per-symbol probe results ──
    probes = []
    any_visible = bare_coin_l2_found or dex_qualified_l2_found or asset_ctxs_bare_coin_found

    for sym in tradfi_symbols[:config.max_symbols]:
        coin = sym.get("coin", "")
        dex = sym.get("dex_name", "")
        asset_id = sym.get("asset_id", 0)
        probe = ArchiveProbeResult(
            symbol=coin, dex=dex, asset_id=asset_id,
            official_bare_coin_l2_attempted=coin in [e["symbol"] for e in l2_head_results],
            official_asset_ctxs_daily_attempted=True,
            l2_prefix_listing_attempted=True,
            bare_coin_l2_visible=any(e["symbol"] == coin and e["outcome"] == S3Outcome.OBJECT_EXISTS
                                     for e in l2_head_results),
            asset_ctxs_daily_visible=asset_ctxs_matches.get(coin, False),
            asset_ctxs_symbol_present=asset_ctxs_matches.get(coin, False))

        # Determine blocker reason
        if probe.bare_coin_l2_visible or probe.asset_ctxs_daily_visible:
            probe.archive_visible = True
            probe.archive_blocker_reason = ""
        elif redirect_count > 0 and nosuchkey_count == 0:
            probe.archive_blocker_reason = "S3_REDIRECT_OR_REGION_MISMATCH_UNRESOLVED"
        elif not sampled_confirmed:
            probe.archive_blocker_reason = "ARCHIVE_DATE_UNAVAILABLE_OR_DELAYED"
        elif asset_ctxs_read_count == 0 and redirect_count > 0:
            probe.archive_blocker_reason = "OFFICIAL_ASSET_CTXS_DAILY_READ_BLOCKED"
        elif prefix_listed_count == 0:
            probe.archive_blocker_reason = "OFFICIAL_MARKET_DATA_PREFIX_LIST_BLOCKED"
        elif probe.official_asset_ctxs_daily_attempted and not probe.asset_ctxs_daily_visible:
            probe.archive_blocker_reason = "OFFICIAL_ASSET_CTXS_DAILY_NOT_FOUND"
        elif probe.official_bare_coin_l2_attempted and not probe.bare_coin_l2_visible:
            probe.archive_blocker_reason = "OFFICIAL_BARE_COIN_L2_NOT_FOUND"
        else:
            probe.archive_blocker_reason = "BUILDER_DEX_ARCHIVE_NOT_IN_PUBLIC_S3"

        probe.l2_visible = probe.bare_coin_l2_visible
        probe.asset_ctxs_visible = probe.asset_ctxs_daily_visible
        probes.append(asdict(probe))

    # ── Step 8: Determine final status ──
    if bare_coin_l2_found or dex_qualified_l2_found:
        final_status = ArchiveStatus.VISIBLE.value
        reason = "Builder DEX symbols found in official S3 archive L2"
    elif asset_ctxs_bare_coin_found:
        final_status = ArchiveStatus.VISIBLE.value
        reason = "Builder DEX symbols found in daily asset_ctxs"
    elif redirect_count > 0 and nosuchkey_count == 0:
        final_status = ArchiveStatus.NOT_FOUND.value
        reason = "S3 redirects received; no confirmed absence (301 ≠ 404)"
    elif not sampled_confirmed:
        final_status = ArchiveStatus.NOT_FOUND.value
        reason = "No confirmed available archive dates found"
    elif asset_ctxs_read_count == 0:
        final_status = ArchiveStatus.NOT_FOUND.value
        reason = "Daily asset_ctxs files unreadable on confirmed dates"
    else:
        final_status = ArchiveStatus.NOT_FOUND.value
        reason = "Official S3 paths audited on confirmed dates: symbols absent"

    # ── Step 9: Write artifacts ──
    audit = {**base, "final_status": final_status,
        "s3_outcome_taxonomy_version": "1.0.0",
        "s3_301_treated_as_absence": False,
        "archive_date_discovery": date_discovery,
        "latest_confirmed_asset_ctxs_date": date_discovery.get("latest_confirmed_asset_ctxs_date"),
        "latest_confirmed_market_data_date": date_discovery.get("latest_confirmed_market_data_date"),
        "sampled_confirmed_dates": sampled_confirmed,
        "sampled_confirmed_hours": sampled_hours,
        "sampled_unavailable_dates": [],
        "unavailable_date_caveats": date_caveats,
        "exact_l2_head_results": l2_head_results,
        "l2_prefix_listing_results": l2_prefix_results,
        "l2_prefix_listing_matches": l2_prefix_matches,
        "asset_ctxs_daily_read_results": asset_ctxs_results,
        "asset_ctxs_symbol_search_results": asset_ctxs_matches,
        "bare_coin_l2_found": bare_coin_l2_found,
        "dex_qualified_l2_found": dex_qualified_l2_found,
        "asset_ctxs_bare_coin_found": asset_ctxs_bare_coin_found,
        "asset_ctxs_dex_qualified_found": any(asset_ctxs_matches.get(f"xyz:{s}", False)
                                               for s in SANITY_SEEDS_PRIMARY),
        "redirect_or_region_mismatch_count": redirect_count,
        "no_such_key_count": nosuchkey_count,
        "prefix_empty_count": sum(1 for r in l2_prefix_results if r["outcome"] == S3Outcome.PREFIX_EMPTY),
        "confirmed_absence_count": nosuchkey_count,
        "transport_error_count": transport_error_count,
        "boto3_available": boto3_available,
        "bytes_downloaded": archive_cp.bytes_downloaded + sum(r.get("bytes_read", 0) for r in asset_ctxs_results),
        "final_archive_path_audit_status": final_status}
    _write_json(run_dir / "official_archive_path_audit.json", audit)

    probe_art = {**base, "final_status": final_status, "probes": probes,
                 "any_visible": any_visible,
                 "bytes_downloaded_total": archive_cp.bytes_downloaded,
                 "bytes_downloaded_by_source": archive_cp.bytes_by_source,
                 "official_archive_path_audit": audit}
    _write_json(run_dir / "archive_visibility_probe.json", probe_art)

    blocked = not any_visible
    return GateResult(phase="C", status=final_status, reason=reason,
                      computed_from_real_data=True,
                      measurements={"symbols_probed": len(probes), "any_visible": any_visible,
                                     "l2_head_probes": len(l2_head_results),
                                     "l2_prefix_listings": len(l2_prefix_results),
                                     "asset_ctxs_files_read": asset_ctxs_read_count,
                                     "bare_coin_l2_found": bare_coin_l2_found,
                                     "redirect_count": redirect_count,
                                     "nosuchkey_count": nosuchkey_count,
                                     "confirmed_dates": len(sampled_confirmed)},
                      blocked=blocked), probe_art
# Phase D — Fee reality
# ═══════════════════════════════════════════════════════════════════

def phase_d_fees(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.FEE_READY.value)
    fee_probes = []
    for sym in tradfi_symbols[:config.max_symbols]:
        fee_probes.append(asdict(FeeProbeResult(
            symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""),
            conservative_taker_bps=3.5, conservative_maker_bps=1.0,
            round_trip_fee_bps=7.0, total_conservative_cost_bps=7.0,
            fee_confidence="conservative_default", computed_from_real_data=False)))
    artifact = {**base, "final_status": FeeLiqOracleStatus.FEE_READY.value,
                "fee_probes": fee_probes, "note": "Conservative defaults; no L2 spread yet"}
    _write_json(run_dir / "fee_reality_probe.json", artifact)
    return GateResult(phase="D", status=FeeLiqOracleStatus.FEE_READY.value,
                      reason="Conservative fee bounds", computed_from_real_data=False,
                      measurements={"symbols": len(fee_probes)}), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase E — Oracle/anchor classification
# ═══════════════════════════════════════════════════════════════════

ANCHOR_ORDER = {
    "single_stock_like": ["extended_hours_equity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "index_like": ["futures_proxy_anchor", "index_etf_proxy_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "etf_like": ["extended_hours_equity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "commodity_like": ["commodity_futures_anchor", "spot_commodity_anchor", "cash_regular_hours_only", "no_reliable_anchor"],
    "unknown": ["no_reliable_anchor"],
}

def phase_e_oracle(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.ORACLE_BLOCKED.value)
    results = []
    for sym in tradfi_symbols[:config.max_symbols]:
        cls = sym.get("classification", "unknown")
        order = ANCHOR_ORDER.get(cls, ANCHOR_ORDER["unknown"])
        results.append(asdict(OracleAnchorResult(
            symbol=sym.get("coin", ""), asset_class=cls,
            selected_anchor_class="no_reliable_anchor",
            anchor_selection_order=order, computed_from_real_data=False)))
    artifact = {**base, "final_status": FeeLiqOracleStatus.ORACLE_BLOCKED.value,
                "results": results}
    _write_json(run_dir / "oracle_anchor_classification.json", artifact)
    return GateResult(phase="E", status=FeeLiqOracleStatus.ORACLE_BLOCKED.value,
                      reason="No anchor data; order recorded", computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase F — L2 depth/spread feasibility
# ═══════════════════════════════════════════════════════════════════

def phase_f_liquidity(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, FeeLiqOracleStatus.L2_INSUFFICIENT.value)
    results = []
    for sym in tradfi_symbols[:config.max_symbols]:
        results.append(asdict(L2DepthResult(symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""))))
    artifact = {**base, "final_status": FeeLiqOracleStatus.L2_INSUFFICIENT.value, "results": results}
    _write_json(run_dir / "l2_depth_spread_probe.json", artifact)
    return GateResult(phase="F", status=FeeLiqOracleStatus.L2_INSUFFICIENT.value,
                      reason="No L2 data", computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase G — Off-hours residual basis-tail existence
# ═══════════════════════════════════════════════════════════════════

def phase_g_basis_tail(config, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, BasisTailStatus.INCONCLUSIVE.value)
    results = []
    for sym in tradfi_symbols[:config.max_symbols]:
        results.append(asdict(BasisTailResult(
            symbol=sym.get("coin", ""), dex=sym.get("dex_name", ""),
            status=BasisTailStatus.INCONCLUSIVE.value)))
    artifact = {**base, "final_status": BasisTailStatus.INCONCLUSIVE.value, "results": results,
                "note": "No archive/anchor data; sample count = 0; INCONCLUSIVE"}
    _write_json(run_dir / "offhours_basis_tail_probe.json", artifact)
    return GateResult(phase="G", status=BasisTailStatus.INCONCLUSIVE.value,
                      reason="No data; underpowered", computed_from_real_data=False,
                      measurements={"symbols": len(results)}, blocked=True), artifact


# ═══════════════════════════════════════════════════════════════════
# Phase H — Corrective registry note preview
# ═══════════════════════════════════════════════════════════════════

def phase_h_registry_note(config, gate_results, tradfi_symbols, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, "corrective_preview")
    final_status = _determine_final_gate(gate_results)
    note = f"""# Corrective Registry Note - HIP-3 Builder-DEX TradFi Scout v0

## Prior entry heading
HIP-3 Builder-Deployed Off-Hours Oracle-Basis Residual - Public Discovery Unresolved

## Correction summary
The prior discovery work queried only the default validator-operated perp DEX universe
(metaAndAssetCtxs without the dex parameter). Builder-deployed HIP-3 perps live under
separate DEX namespaces accessible via the perpDexs endpoint and the dex parameter.
The previous default-dex-only discovery result is not evidence that HIP-3
builder-deployed TradFi markets are absent.

## New API route
Builder DEX enumeration via perpDexs is required before drawing any public-discovery
conclusion. Each builder DEX universe is queried with dex parameter.

## Frontend/API consistency result
Frontend trade URLs are sanity seeds only; symbols must resolve through public API.

## New final status
{final_status}

## Old note amendment
Yes. The old note should be read as a query-design gap, not exhaustion of the surface.

## Suggested wording
This entry supersedes the prior Public Discovery Unresolved conclusion.
Registry posture: {final_status}

A pass here does not prove profitability.
A fail here does not reject HIP-3 generally.
Phase 0 drafting is not authorized unless final status is HIP3_PHASE0_PRECOMMITMENT_WARRANTED.
Single-run Phase 0 warrant is forbidden; two independent scouts required.

## No automatic registry mutation
This is a preview only. Use separate command to apply.
"""
    (run_dir / "corrective_registry_note_preview.md").write_text(note)
    return GateResult(phase="H", status="corrective_preview_generated", reason="Preview written"), base


# ═══════════════════════════════════════════════════════════════════
# Phase I — Final gate verdict
# ═══════════════════════════════════════════════════════════════════

def _determine_final_gate(gate_results):
    for g in gate_results:
        if g.blocked:
            if g.phase == "A":
                return FinalGateStatus.BUILDER_SURFACE.value
            if g.phase == "A2":
                return FinalGateStatus.FRONTEND_DESYNC.value
            if g.phase == "B":
                return FinalGateStatus.BUILDER_SURFACE.value
            if g.phase == "C":
                return FinalGateStatus.ARCHIVE_BLOCKED.value
            if g.phase == "E":
                return FinalGateStatus.ANCHOR_BLOCKED.value
            if g.phase == "F":
                return FinalGateStatus.LIQUIDITY_BLOCKED.value
            if g.phase == "G":
                return FinalGateStatus.TAIL_INCONCLUSIVE.value
    for g in gate_results:
        if "INCONCLUSIVE" in g.status:
            return FinalGateStatus.TAIL_INCONCLUSIVE.value
    has_tradfi = any(g.measurements.get("tradfi", 0) > 0 for g in gate_results if g.phase == "B")
    if has_tradfi:
        return FinalGateStatus.BUILDER_SURFACE.value
    return FinalGateStatus.TAIL_INCONCLUSIVE.value


def phase_i_verdict(config, gate_results, run_dir, git_sha, git_dirty, branch):
    base = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch, "")
    final_status = _determine_final_gate(gate_results)
    base["final_status"] = final_status
    has_prior = (run_dir / "prior_independent_run_evidence.json").exists()
    if final_status == FinalGateStatus.PHASE0_WARRANTED.value and not has_prior:
        final_status = FinalGateStatus.PENDING_SECOND_RUN.value
        base["final_status"] = final_status
    gate_decisions = {**base, "final_status": final_status,
                      "phase_results": [{"phase": g.phase, "status": g.status,
                                         "reason": g.reason, "blocked": g.blocked} for g in gate_results],
                      "prior_independent_run_evidence": has_prior}
    _write_json(run_dir / "gate_decisions.json", gate_decisions)
    return GateResult(phase="I", status=final_status, reason="Final verdict",
                      computed_from_real_data=any(g.computed_from_real_data for g in gate_results)), gate_decisions


# ═══════════════════════════════════════════════════════════════════
# run_scout — Main orchestrator
# ═══════════════════════════════════════════════════════════════════

def run_scout(config):
    """Run the full Phase -1 scout DAG. Returns final status dict."""
    git_sha, git_dirty = _get_git_info()
    branch = ""

    try:
        git_dir = Path(__file__).resolve().parent.parent.parent / ".git"
        head_ref = (git_dir / "HEAD").read_text().strip()
        if head_ref.startswith("ref: "):
            branch = head_ref[5:].split("/")[-1]
            ref_file = git_dir / head_ref[5:]
            if ref_file.exists():
                branch = "/".join(head_ref[5:].split("/")[2:])
        else:
            branch = "detached"
    except Exception:
        branch = "unknown"





        branch = "unknown"

    if not config.run_id:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        config.run_id = f"{ts}_{_config_hash(config)[:8]}"

    run_dir = Path(config.out_root) / config.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    gate_results = []
    all_artifacts = {}
    builder_meta = []
    builder_ctxs = []
    tradfi = []

    # Phase A
    gate_a, art_a = phase_a_discovery(config, PublicInfoChokepoint(config.allow_network_public),
                                       run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_a)
    all_artifacts["phase_a"] = art_a
    if gate_a.blocked:
        # Still run A2 for frontend check if builder DEXs found elsewhere
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_a.status, "blocked_at": "A", "run_dir": str(run_dir)}

    # Extract builder meta/ctxs from phase A artifact
    if isinstance(art_a, dict):
        builder_meta_raw = art_a.get("builder_meta", {})
        if isinstance(builder_meta_raw, dict):
            for d in builder_meta_raw.get("dexes", []):
                builder_meta.append(DexMetaResult(dex_name=d.get("dex_name", ""), universe_count=d.get("universe_count", 0)))
        # Re-query for actual universe data
        chokepoint = PublicInfoChokepoint(config.allow_network_public)
        for entry_info in art_a.get("inventory", {}).get("entries", []):
            if entry_info.get("classification") == "builder_dex":
                dex_name = entry_info.get("dex_name")
                try:
                    meta_resp = chokepoint.post_info({"type": "meta", "dex": dex_name})
                    universe = meta_resp.get("universe", []) if isinstance(meta_resp, dict) else []
                    builder_meta.append(DexMetaResult(dex_name=dex_name, universe=universe, universe_count=len(universe)))
                except Exception:
                    pass
                try:
                    ctxs_resp = chokepoint.post_info({"type": "metaAndAssetCtxs", "dex": dex_name})
                    u2, ac = [], []
                    if isinstance(ctxs_resp, (list, tuple)) and len(ctxs_resp) >= 1:
                        if isinstance(ctxs_resp[0], dict):
                            u2 = ctxs_resp[0].get("universe", [])
                        if len(ctxs_resp) >= 2 and isinstance(ctxs_resp[1], list):
                            ac = ctxs_resp[1]
                    builder_ctxs.append(DexAssetCtxsResult(dex_name=dex_name, universe=u2, asset_ctxs=ac))
                except Exception:
                    pass

    # Phase A2
    gate_a2, art_a2 = phase_a2_frontend_api(config, PublicInfoChokepoint(config.allow_network_public),
                                              builder_meta, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_a2)
    all_artifacts["phase_a2"] = art_a2
    if gate_a2.blocked:
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_a2.status, "blocked_at": "A2", "run_dir": str(run_dir)}

    # Phase B
    gate_b, art_b = phase_b_resolution(config, builder_meta, builder_ctxs,
                                         run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_b)
    all_artifacts["phase_b"] = art_b
    if isinstance(art_b, dict):
        tradfi = art_b.get("tradfi", [])
    if gate_b.blocked:
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_b.status, "blocked_at": "B", "run_dir": str(run_dir)}

    # Phase C
    archive_cp = ArchiveChokepoint(config.allow_s3_archive_read, config.download_budget_bytes)
    gate_c, art_c = phase_c_archive(config, archive_cp, tradfi, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_c)
    all_artifacts["phase_c"] = art_c
    if gate_c.blocked:
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_c.status, "blocked_at": "C", "run_dir": str(run_dir)}

    # Phase D
    gate_d, art_d = phase_d_fees(config, tradfi, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_d)
    all_artifacts["phase_d"] = art_d

    # Phase E
    gate_e, art_e = phase_e_oracle(config, tradfi, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_e)
    all_artifacts["phase_e"] = art_e
    if gate_e.blocked:
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_e.status, "blocked_at": "E", "run_dir": str(run_dir)}

    # Phase F
    gate_f, art_f = phase_f_liquidity(config, tradfi, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_f)
    all_artifacts["phase_f"] = art_f
    if gate_f.blocked:
        _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
        return {"status": gate_f.status, "blocked_at": "F", "run_dir": str(run_dir)}

    # Phase G
    gate_g, art_g = phase_g_basis_tail(config, tradfi, run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_g)
    all_artifacts["phase_g"] = art_g

    _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi)
    return {"status": _determine_final_gate(gate_results), "run_dir": str(run_dir)}


def _finish_run(config, gate_results, all_artifacts, run_dir, git_sha, git_dirty, branch, tradfi):
    """Write summary artifacts and registry note preview."""
    # Phase H — registry note preview
    gate_h, _ = phase_h_registry_note(config, gate_results, tradfi,
                                       run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_h)

    # Phase I — final verdict
    gate_i, gate_decisions = phase_i_verdict(config, gate_results,
                                              run_dir, git_sha, git_dirty, branch)
    gate_results.append(gate_i)

    # Run manifest
    prior_correction_text = (
        "This run supersedes prior conclusions about HIP-3 builder discovery from: "
        "feat/hip3-builder-discovery-gap-audit-v0, feat/hip3-builder-deployment-event-discovery-v0, "
        "feat/hip3-universe-delta-discovery-v0. Prior no-findings results were based on querying "
        "the wrong/default API slice (metaAndAssetCtxs without dex parameter) and must not be "
        "read as exhausting the builder DEX public surface."
    )
    manifest = _make_artifact_base(config.run_id, config, git_sha, git_dirty, branch,
                                    gate_decisions.get("final_status", "unknown"))
    manifest["prior_session_correction"] = {"enabled": True, "text": prior_correction_text}
    manifest["config"] = {k: v for k, v in asdict(config).items()}
    manifest["final_status"] = gate_decisions.get("final_status", "unknown")
    manifest["phase_results"] = gate_decisions.get("phase_results", [])
    _write_json(run_dir / "run_manifest.json", manifest)

    # Summary
    summary = {"study_id": STUDY_ID, "run_id": config.run_id,
               "final_status": gate_decisions.get("final_status", "unknown"),
               "phases_completed": len(gate_results),
               "blocked_phases": [g.phase for g in gate_results if g.blocked],
               "tradfi_symbols_found": len(tradfi),
               "no_orders_no_auth_no_live_confirmation": True}
    _write_json(run_dir / "summary.json", summary)

    # Summary markdown
    md_lines = [f"# Scout Summary — {config.run_id}", "",
                f"**Final status:** `{gate_decisions.get('final_status', 'unknown')}`", ""]
    for g in gate_results:
        block = " [BLOCKED]" if g.blocked else ""
        md_lines.append(f"- Phase {g.phase}: `{g.status}`{block} — {g.reason}")
    md_lines.extend(["", f"TradFi symbols: {len(tradfi)}", "",
                     "A pass here does not prove profitability.",
                     "A fail here does not reject HIP-3 generally."])
    (run_dir / "summary.md").write_text("\n".join(md_lines))


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="HIP-3 Builder-DEX TradFi Off-Hours Scout v0")
    parser.add_argument("--out-root", default="reports/hip3_builder_dex_tradfi_offhours_scout_v0")
    parser.add_argument("--seed-tickers", default=",".join(ALL_SEED_TICKERS))
    parser.add_argument("--start-date", default="2025-10-13")
    parser.add_argument("--end-date", default="latest")
    parser.add_argument("--max-symbols", type=int, default=12)
    parser.add_argument("--max-archive-days-per-symbol", type=int, default=7)
    parser.add_argument("--max-l2-hours-per-symbol", type=int, default=72)
    parser.add_argument("--download-budget-bytes", type=int, default=500_000_000)
    parser.add_argument("--l2-budget-bytes", type=int, default=250_000_000)
    parser.add_argument("--require-sanity-seeds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-network-public", action="store_true")
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = ScoutConfig(
        out_root=args.out_root,
        seed_tickers=args.seed_tickers.split(","),
        start_date=args.start_date,
        end_date=args.end_date,
        max_symbols=args.max_symbols,
        max_archive_days_per_symbol=args.max_archive_days_per_symbol,
        max_l2_hours_per_symbol=args.max_l2_hours_per_symbol,
        download_budget_bytes=args.download_budget_bytes,
        l2_budget_bytes=args.l2_budget_bytes,
        require_sanity_seeds=args.require_sanity_seeds,
        allow_network_public=args.allow_network_public,
        allow_s3_archive_read=args.allow_s3_archive_read,
        dry_run=args.dry_run,
    )

    if config.dry_run:
        git_sha, git_dirty = _get_git_info()
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        config.run_id = f"{ts}_{_config_hash(config)[:8]}"
        run_dir = Path(config.out_root) / config.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        preview = _make_artifact_base(config.run_id, config, git_sha, git_dirty, "dry_run",
                                       DiscoveryStatus.SCOUT_READY.value)
        preview["frontend_api_consistency_check_skipped"] = True
        preview["config_hash"] = _config_hash(config)
        _write_json(run_dir / "dry_run_preview.json", preview)
        print(json.dumps({"status": "HIP3_BUILDER_DEX_SCOUT_READY", "run_dir": str(run_dir)}, indent=2))
        return

    result = run_scout(config)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
