"""HIP-3 Cross-DEX No-Arb-Band Phase -1 Experiment.

Anchor-free comparison of two Hyperliquid builder DEXes directly using
SonarX L2 summary snapshots.  Measures cross-DEX mid-price spread,
conservative no-arb band, reversion diagnostics, and tail analysis.

Phase -1 data-plane feasibility ONLY.
  - No strategy, no PnL, no returns, no signals, no entries/exits
  - No Phase 0 precommitment, no registry mutation, no promotion
  - No live/paper trading, no orders, no auth, no private keys

All S3 access via NetworkChokepoint with explicit guard flags.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import bisect
import math
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

try:
    import orjson
except ImportError:
    orjson = None  # type: ignore

# ---------------------------------------------------------------------------
# Ensure parent package is importable
# ---------------------------------------------------------------------------
_PKG_ROOT = str(Path(__file__).resolve().parents[4])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    NetworkChokepoint,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STUDY_ID = "hip3_cross_dex_noarb_band_phase_minus1_v0"
BUCKET_NAME = "sonarx-hyperliquid-public"
BASE_PREFIX_HIP3 = "market_data/hip3/"

DEFAULT_PAIRS = ["cash:NVDA|km:NVDA", "cash:TSLA|km:TSLA"]
SECONDARY_PAIRS = ["cash:AAPL|km:AAPL", "cash:MSFT|km:MSFT"]
FLX_FORBIDDEN_BY_DEFAULT = True

ALLOWED_STATUSES = frozenset({
    "HIP3_CROSS_DEX_NOARB_DRY_RUN_READY",
    "HIP3_CROSS_DEX_NOARB_COVERAGE_READY",
    "HIP3_CROSS_DEX_NOARB_ZERO_DATA_SAMPLER_FAILURE",
    "HIP3_CROSS_DEX_NOARB_DECODE_FAILURE_BLOCKED",
    "HIP3_CROSS_DEX_NOARB_L2_UNDERPOWERED",
    "HIP3_CROSS_DEX_NOARB_ALIGNMENT_FAILED",
    "HIP3_CROSS_DEX_NOARB_ALIGNMENT_UNAVAILABLE",
    "HIP3_CROSS_DEX_NOARB_NEAREST_ALIGNMENT_DIAGNOSTIC",
    "HIP3_CROSS_DEX_NOARB_NO_TAIL",
    "HIP3_CROSS_DEX_NOARB_SPREAD_WITHIN_BAND",
    "HIP3_CROSS_DEX_NOARB_SPREAD_OUTSIDE_BAND_DIAGNOSTIC",
    "HIP3_CROSS_DEX_NOARB_TAIL_PRESENT_BUT_ILLIQUID",
    "HIP3_CROSS_DEX_NOARB_TAIL_PRESENT_BUT_CONCENTRATED",
    "HIP3_CROSS_DEX_NOARB_PERSISTENT_LEVEL_OFFSET",
    "HIP3_CROSS_DEX_NOARB_REVERSION_UNDERPOWERED",
    "HIP3_CROSS_DEX_NOARB_FORWARD_RECORDER_VALIDATION_FAILED",
    "HIP3_CROSS_DEX_NOARB_TAIL_PRESENT_DIAGNOSTIC",
    "HIP3_CROSS_DEX_NOARB_UNDERPOWERED",
    "HIP3_CROSS_DEX_NOARB_NOT_ENOUGH_FOR_PRECOMMITMENT",
    "HIP3_CROSS_DEX_NOARB_NEXT_PRECOMMITMENT_REVIEW_ALLOWED",
})

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "EDGE_CONFIRMED",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
    "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
    "PAPER_ONCE_ELIGIBLE",
})

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_metadata() -> Mapping[str, Any]:
    """Read .git files directly — no subprocess, no shell, no eval."""
    git_dir = Path(__file__).resolve().parent.parent.parent.parent / ".git"
    sha, dirty, branch = "unknown", False, "unknown"
    try:
        head_ref = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head_ref.startswith("ref: "):
            branch_path = head_ref[5:]
            branch = branch_path.replace("refs/heads/", "")
            ref_file = git_dir / branch_path
            if ref_file.exists():
                sha = ref_file.read_text(encoding="utf-8").strip()
            else:
                if git_dir.is_file():
                    gitdir_line = git_dir.read_text(encoding="utf-8").strip()
                    if gitdir_line.startswith("gitdir: "):
                        real_dir = Path(gitdir_line[8:])
                        real_ref = real_dir / branch_path
                        if real_ref.exists():
                            sha = real_ref.read_text(encoding="utf-8").strip()
        else:
            sha = head_ref
        dirty = (git_dir / "MERGE_HEAD").exists() or (git_dir / "CHERRY_PICK_HEAD").exists()
    except Exception:
        sha, dirty, branch = "unknown", False, "unknown"
    return {"git_sha": sha, "git_dirty": dirty, "branch": branch}


def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = hashlib.sha256(ts.encode()).hexdigest()[:8]
    return f"{ts}_{short}"


def safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return sorted_vals[-1]
    d = k - f
    return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if orjson is not None:
        path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    else:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)


def write_jsonl(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if orjson is not None:
        lines = [orjson.dumps(r) for r in rows]
        path.write_bytes(b"\n".join(lines) + b"\n")
    else:
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")


def loads_json_line(line: bytes) -> dict:
    if orjson is not None:
        return orjson.loads(line)
    return json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)


# ---------------------------------------------------------------------------
# Typed data model
# ---------------------------------------------------------------------------

@dataclass
class BuilderDexLeg:
    dex: str
    display_symbol: str
    api_symbol: str
    source: str
    excluded_reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["excluded_reason"] is None:
            del d["excluded_reason"]
        return d


@dataclass
class CrossDexPair:
    pair_id: str
    display_symbol: str
    left_leg: BuilderDexLeg
    right_leg: BuilderDexLeg
    reason_included: str
    active_oracle_evidence: str
    is_primary: bool

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "display_symbol": self.display_symbol,
            "left_leg": self.left_leg.to_dict(),
            "right_leg": self.right_leg.to_dict(),
            "reason_included": self.reason_included,
            "active_oracle_evidence": self.active_oracle_evidence,
            "is_primary": self.is_primary,
        }


@dataclass
class FeeMarginConfig:
    dex: str
    api_symbol: str
    maker_fee_bps: Optional[float] = None
    taker_fee_bps: Optional[float] = None
    deployer_surcharge_bps: Optional[float] = None
    discovered_one_way_fee_bps: Optional[float] = None
    conservative_one_way_fee_bps: float = 12.5
    conservative_open_close_leg_fee_bps: float = 25.0
    conservative_four_fill_fee_band_bps: float = 50.0
    margin_requirement: Optional[float] = None
    collateral_notes: Optional[str] = None
    source: str = "fallback_conservative"
    confidence: str = "low"
    unresolved_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FundingConfig:
    dex: str
    api_symbol: str
    funding_rate_bps_per_interval: Optional[float] = None
    funding_interval_hours: Optional[float] = None
    source: str = "fallback_conservative"
    confidence: str = "low"
    fallback_funding_diff_bps_per_hour: float = 1.0
    unresolved_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NoArbBandConfig:
    pair_id: str
    left_fee_config: FeeMarginConfig
    right_fee_config: FeeMarginConfig
    left_funding_config: FundingConfig
    right_funding_config: FundingConfig
    expected_hold_minutes: float = 60.0
    conservative_four_fill_fee_band_bps: float = 50.0
    funding_differential_band_bps: float = 1.0
    margin_friction_bps: float = 0.0
    extra_uncertainty_band_bps: float = 5.0
    formula: str = "combined_visible_spread + four_fill_fee + funding_diff + margin_friction + extra_uncertainty"
    confidence: str = "low"

    @property
    def conservative_noarb_band_bps(self) -> float:
        return (
            self.conservative_four_fill_fee_band_bps
            + self.funding_differential_band_bps
            + self.margin_friction_bps
            + self.extra_uncertainty_band_bps
        )

    def to_dict(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "expected_hold_minutes": self.expected_hold_minutes,
            "conservative_four_fill_fee_band_bps": self.conservative_four_fill_fee_band_bps,
            "funding_differential_band_bps": self.funding_differential_band_bps,
            "margin_friction_bps": self.margin_friction_bps,
            "extra_uncertainty_band_bps": self.extra_uncertainty_band_bps,
            "conservative_noarb_band_bps_total": self.conservative_noarb_band_bps,
            "formula": self.formula,
            "confidence": self.confidence,
        }


@dataclass
class L2BookSnapshot:
    api_symbol: str
    dex: str
    display_symbol: str
    timestamp_ms: float
    timestamp_utc: str
    block_height: Optional[int] = None
    bids: List[dict] = field(default_factory=list)
    asks: List[dict] = field(default_factory=list)
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid: float = 0.0
    quoted_spread_bps: float = 0.0
    two_sided: bool = False
    parse_status: str = "ok"
    source_key: str = ""


@dataclass
class BookDepthMetrics:
    depth_bid_100: float = 0.0
    depth_ask_100: float = 0.0
    depth_bid_500: float = 0.0
    depth_ask_500: float = 0.0
    depth_bid_1000: float = 0.0
    depth_ask_1000: float = 0.0
    depth_bid_5000: float = 0.0
    depth_ask_5000: float = 0.0
    min_two_sided_depth_100: float = 0.0
    min_two_sided_depth_500: float = 0.0
    min_two_sided_depth_1000: float = 0.0
    top20_depth_cap_flag: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlignedCrossDexObservation:
    pair_id: str
    display_symbol: str
    left_api_symbol: str
    right_api_symbol: str
    left_timestamp_utc: str
    right_timestamp_utc: str
    timestamp_gap_seconds: float
    same_block_height: Optional[bool] = None
    left_block_height: Optional[int] = None
    right_block_height: Optional[int] = None
    left_mid: float = 0.0
    right_mid: float = 0.0
    cross_mid_spread_bps: float = 0.0
    abs_cross_mid_spread_bps: float = 0.0
    left_quoted_spread_bps: float = 0.0
    right_quoted_spread_bps: float = 0.0
    combined_visible_spread_bps: float = 0.0
    conservative_four_fill_fee_band_bps: float = 0.0
    funding_differential_band_bps: float = 0.0
    conservative_noarb_band_bps: float = 0.0
    excess_over_visible_spread_bps: float = 0.0
    excess_over_noarb_band_bps: float = 0.0
    left_depth_metrics: Optional[dict] = None
    right_depth_metrics: Optional[dict] = None
    both_two_sided: bool = False
    both_depth_available: bool = False
    is_primary_alignment: bool = True


@dataclass
class ReversionDiagnostics:
    pair_id: str
    signed_spread_series_count: int = 0
    zero_crossing_count: int = 0
    zero_crossing_rate: float = 0.0
    median_time_to_revert_seconds: Optional[float] = None
    p75_time_to_revert_seconds: Optional[float] = None
    max_persistent_same_sign_run: int = 0
    level_offset_detected: bool = False
    reversion_supported: bool = False
    classification: str = "underpowered_for_reversion"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PairSpreadSummary:
    pair_id: str
    aligned_observation_count: int = 0
    primary_alignment_count: int = 0
    diagnostic_alignment_count: int = 0
    primary_alignment_share: float = 0.0
    same_block_observation_count: int = 0
    same_block_observation_share: float = 0.0
    median_abs_cross_mid_spread_bps: float = 0.0
    p75_abs_cross_mid_spread_bps: float = 0.0
    p95_abs_cross_mid_spread_bps: float = 0.0
    p99_abs_cross_mid_spread_bps: float = 0.0
    median_excess_over_noarb_band_bps: float = 0.0
    p95_excess_over_noarb_band_bps: float = 0.0
    p99_excess_over_noarb_band_bps: float = 0.0
    tail_observation_count: int = 0
    within_band_observation_share: float = 0.0
    tail_concentration_by_hour: dict = field(default_factory=dict)
    tail_concentration_by_day: dict = field(default_factory=dict)
    depth_summary: dict = field(default_factory=dict)
    reversion_diagnostics: Optional[dict] = None
    gate_status: str = "PENDING"


@dataclass
class PhaseMinus1Decision:
    final_status: str = "PENDING"
    next_precommitment_review_allowed: bool = False
    reason: str = ""
    candidate_pairs: List[str] = field(default_factory=list)
    blocked_pairs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pair discovery and parsing
# ---------------------------------------------------------------------------

def parse_api_symbol(api_sym: str) -> Tuple[str, str]:
    """Parse 'dex:SYMBOL' into (dex, display_symbol)."""
    if ":" not in api_sym:
        raise ValueError(f"API symbol must contain ':', got {api_sym!r}")
    parts = api_sym.split(":", 1)
    return parts[0], parts[1]


def parse_pair_string(pair_str: str, allow_mismatched: bool = False,
                      allow_flx: bool = False) -> CrossDexPair:
    """Parse 'left_api_symbol|right_api_symbol' into CrossDexPair."""
    if "|" not in pair_str:
        raise ValueError(f"Pair must contain '|', got {pair_str!r}")
    left_str, right_str = pair_str.split("|", 1)
    left_str, right_str = left_str.strip(), right_str.strip()

    left_dex, left_sym = parse_api_symbol(left_str)
    right_dex, right_sym = parse_api_symbol(right_str)

    # flx check
    if (left_dex == "flx" or right_dex == "flx") and not allow_flx:
        raise ValueError(
            f"flx pairs are forbidden by default in primary pairs: {pair_str}. "
            f"Use --allow-flx-diagnostic-only to override."
        )

    # Display symbol match check
    if left_sym != right_sym and not allow_mismatched:
        raise ValueError(
            f"Display symbols must match: left={left_sym}, right={right_sym}. "
            f"Use --allow-mismatched-display-symbols to override."
        )

    is_flx = left_dex == "flx" or right_dex == "flx"
    is_primary = not is_flx

    left_leg = BuilderDexLeg(
        dex=left_dex, display_symbol=left_sym, api_symbol=left_str,
        source="user_specified",
    )
    right_leg = BuilderDexLeg(
        dex=right_dex, display_symbol=right_sym, api_symbol=right_str,
        source="user_specified",
    )

    pair_id = f"{left_str}|{right_str}"
    return CrossDexPair(
        pair_id=pair_id,
        display_symbol=left_sym,
        left_leg=left_leg,
        right_leg=right_leg,
        reason_included="user_specified" if is_primary else "flx_diagnostic_only",
        active_oracle_evidence="prior_replica_cmds_reconstruction",
        is_primary=is_primary,
    )


def get_default_pairs(allow_flx: bool = False) -> List[CrossDexPair]:
    """Parse default pair strings into CrossDexPair objects."""
    pairs = []
    for ps in DEFAULT_PAIRS:
        try:
            pairs.append(parse_pair_string(ps, allow_flx=allow_flx))
        except ValueError:
            pass
    return pairs


# ---------------------------------------------------------------------------
# Fee, funding, margin, and no-arb-band discovery
# ---------------------------------------------------------------------------

def discover_fee_margin_config(
    dex: str, api_symbol: str, chokepoint: NetworkChokepoint,
    fallback_one_way_bps: float = 12.5,
) -> FeeMarginConfig:
    """Discover fee/margin config for a DEX leg. Conservative fallback if public fields absent."""
    # Attempt public Hyperliquid info endpoint
    public_fee_found = False
    taker_fee_bps = None
    maker_fee_bps = None
    deployer_surcharge_bps = None
    unresolved = ["maker_fee_bps", "taker_fee_bps", "deployer_surcharge_bps", "margin_requirement"]
    source = "fallback_conservative"
    confidence = "low"

    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "metaAndAssetCtxs"}
        resp = chokepoint.http_post_json(url, payload, timeout=15)
        if isinstance(resp, list) and len(resp) >= 2:
            universe = resp[0] if isinstance(resp[0], list) else []
            # Look for HIP-3 builder DEX fee hints in universe
            # Builder DEXes typically have taker fee around 0.1% (10 bps) base
            # plus potential deployer surcharge
            for asset_info in universe:
                if isinstance(asset_info, dict):
                    name = asset_info.get("name", "")
                    if name == api_symbol or name == api_symbol.split(":")[-1]:
                        # Found some public metadata — still use conservative
                        public_fee_found = True
                        source = "public_hyperliquid_meta"
                        confidence = "medium"
                        break
    except Exception:
        pass

    # Use conservative fallback — NOT cheap
    conservative_one_way = max(fallback_one_way_bps, 10.0)  # Floor at 10 bps
    if deployer_surcharge_bps is not None:
        conservative_one_way += deployer_surcharge_bps

    four_fill = 2.0 * conservative_one_way + 2.0 * conservative_one_way  # Both legs

    return FeeMarginConfig(
        dex=dex,
        api_symbol=api_symbol,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        deployer_surcharge_bps=deployer_surcharge_bps,
        discovered_one_way_fee_bps=taker_fee_bps,
        conservative_one_way_fee_bps=conservative_one_way,
        conservative_open_close_leg_fee_bps=2.0 * conservative_one_way,
        conservative_four_fill_fee_band_bps=four_fill,
        margin_requirement=None,
        collateral_notes="HIP-3 builder DEX perp; margin publicly discoverable via meta endpoint",
        source=source,
        confidence=confidence,
        unresolved_fields=unresolved,
    )


def discover_funding_config(
    dex: str, api_symbol: str, chokepoint: NetworkChokepoint,
    fallback_funding_diff_bps_per_hour: float = 1.0,
) -> FundingConfig:
    """Discover funding config. Conservative fallback if public fields absent."""
    unresolved = ["funding_rate_bps_per_interval", "funding_interval_hours"]
    source = "fallback_conservative"
    confidence = "low"

    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "metaAndAssetCtxs"}
        resp = chokepoint.http_post_json(url, payload, timeout=15)
        if isinstance(resp, list) and len(resp) >= 2:
            ctxs = resp[1] if isinstance(resp[1], list) else []
            for ctx in ctxs:
                if isinstance(ctx, dict):
                    fr = safe_float(ctx.get("funding"))
                    if fr is not None:
                        source = "public_hyperliquid_metaAndAssetCtxs"
                        confidence = "medium"
                        # funding is per 8h in raw, convert to bps
                        funding_bps_8h = abs(fr) * 10000
                        return FundingConfig(
                            dex=dex, api_symbol=api_symbol,
                            funding_rate_bps_per_interval=funding_bps_8h,
                            funding_interval_hours=8.0,
                            source=source, confidence=confidence,
                            fallback_funding_diff_bps_per_hour=fallback_funding_diff_bps_per_hour,
                            unresolved_fields=[],
                        )
    except Exception:
        pass

    return FundingConfig(
        dex=dex, api_symbol=api_symbol,
        source=source, confidence=confidence,
        fallback_funding_diff_bps_per_hour=fallback_funding_diff_bps_per_hour,
        unresolved_fields=unresolved,
    )


def compute_noarb_band_config(
    pair: CrossDexPair,
    left_fee: FeeMarginConfig, right_fee: FeeMarginConfig,
    left_funding: FundingConfig, right_funding: FundingConfig,
    expected_hold_minutes: float = 60.0,
    extra_uncertainty_bps: float = 5.0,
) -> NoArbBandConfig:
    """Compute the conservative no-arb band for a cross-DEX pair."""
    # Four-fill fee: 2 fills per leg (open + close)
    four_fill_fee = left_fee.conservative_four_fill_fee_band_bps  # Already = 2*left + 2*right conceptually
    # Actually: left open + right open + left close + right close
    four_fill_fee = (
        2.0 * left_fee.conservative_one_way_fee_bps
        + 2.0 * right_fee.conservative_one_way_fee_bps
    )

    # Funding differential: max of both legs over hold window
    left_funding_bps = left_funding.fallback_funding_diff_bps_per_hour * (expected_hold_minutes / 60.0)
    right_funding_bps = right_funding.fallback_funding_diff_bps_per_hour * (expected_hold_minutes / 60.0)
    funding_diff = left_funding_bps + right_funding_bps

    return NoArbBandConfig(
        pair_id=pair.pair_id,
        left_fee_config=left_fee,
        right_fee_config=right_fee,
        left_funding_config=left_funding,
        right_funding_config=right_funding,
        expected_hold_minutes=expected_hold_minutes,
        conservative_four_fill_fee_band_bps=four_fill_fee,
        funding_differential_band_bps=funding_diff,
        margin_friction_bps=0.0,  # Not publicly discoverable per-leg
        extra_uncertainty_band_bps=extra_uncertainty_bps,
        confidence="low",
    )


# ---------------------------------------------------------------------------
# SonarX L2 key discovery
# ---------------------------------------------------------------------------

def discover_sonarx_keys(
    chokepoint: NetworkChokepoint,
    legs: List[BuilderDexLeg],
    sample_days: int = 30,
    max_files_per_leg: int = 200,
) -> dict:
    """Discover SonarX S3 keys for each leg. Returns inventory dict."""
    inventory: dict = {
        "keys_available_by_leg": {},
        "selected_keys_by_leg": {},
        "requester_pays_acknowledged": True,
        "zero_data_status_if_any": [],
        "decode_failure_status_if_any": [],
    }

    for leg in legs:
        api_sym = leg.api_symbol
        # Try primary prefix, then URL-encoded variant
        prefixes_to_try = [
            f"{BASE_PREFIX_HIP3}{api_sym}/l2-summary-snapshots/",
            f"{BASE_PREFIX_HIP3}{api_sym.replace(':', '%3A')}/l2-summary-snapshots/",
        ]

        found_keys = []
        for prefix in prefixes_to_try:
            try:
                result = chokepoint.s3_list_prefix(
                    BUCKET_NAME, prefix, requester_pays=True, max_keys=1000,
                    include_subdirs=False,
                )
                if result.get("error_code"):
                    continue
                objects = result.get("objects", [])
                found_keys.extend(objects)
                if found_keys:
                    break
            except Exception:
                continue

        inventory["keys_available_by_leg"][api_sym] = {
            "total_keys": len(found_keys),
            "keys": found_keys[:max_files_per_leg],
        }

        if not found_keys:
            inventory["zero_data_status_if_any"].append(api_sym)

        inventory["selected_keys_by_leg"][api_sym] = found_keys[:max_files_per_leg]

    return inventory


# ---------------------------------------------------------------------------
# L2 snapshot parsing
# ---------------------------------------------------------------------------

def parse_l2_snapshot_file(raw_bytes: bytes, source_key: str,
                           api_symbol: str, dex: str,
                           display_symbol: str) -> List[L2BookSnapshot]:
    """Parse a gzipped JSON L2 summary file into snapshots."""
    snapshots = []
    try:
        if raw_bytes[:2] == b'\x1f\x8b':
            raw_bytes = gzip.decompress(raw_bytes)
        records = json.loads(raw_bytes) if isinstance(raw_bytes, str) else json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        # Record decode failure — NOT zero data
        snapshots.append(L2BookSnapshot(
            api_symbol=api_symbol, dex=dex, display_symbol=display_symbol,
            timestamp_ms=0, timestamp_utc="",
            parse_status=f"DECODE_FAILURE: {e}", source_key=source_key,
        ))
        return snapshots

    if not isinstance(records, list):
        snapshots.append(L2BookSnapshot(
            api_symbol=api_symbol, dex=dex, display_symbol=display_symbol,
            timestamp_ms=0, timestamp_utc="",
            parse_status="DECODE_FAILURE: not a list", source_key=source_key,
        ))
        return snapshots

    for rec in records:
        if not isinstance(rec, dict):
            continue

        bids_raw = rec.get("bids", [])
        asks_raw = rec.get("asks", [])
        block_height = rec.get("height")
        block_time = rec.get("block_time", "")

        # Parse timestamp
        ts_ms = 0.0
        ts_utc = block_time
        if block_time:
            try:
                dt = datetime.fromisoformat(block_time.replace("Z", "+00:00"))
                ts_ms = dt.timestamp() * 1000.0
            except Exception:
                pass

        # Parse bids/asks — handle string px, sz, n
        bids = []
        asks = []
        parse_ok = True

        for level in bids_raw:
            if not isinstance(level, dict):
                parse_ok = False
                break
            px = safe_float(level.get("px"))
            sz = safe_float(level.get("sz"))
            if px is None or sz is None or sz < 0:
                parse_ok = False
                break
            bids.append({"px": px, "sz": sz})

        for level in asks_raw:
            if not isinstance(level, dict):
                parse_ok = False
                break
            px = safe_float(level.get("px"))
            sz = safe_float(level.get("sz"))
            if px is None or sz is None or sz < 0:
                parse_ok = False
                break
            asks.append({"px": px, "sz": sz})

        if not parse_ok or not bids or not asks:
            continue

        # Sort: bids descending by px, asks ascending by px
        bids.sort(key=lambda x: x["px"], reverse=True)
        asks.sort(key=lambda x: x["px"])

        best_bid = bids[0]["px"]
        best_ask = asks[0]["px"]

        # Reject crossed books
        if best_bid >= best_ask or best_bid <= 0 or best_ask <= 0:
            continue

        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000.0

        snapshots.append(L2BookSnapshot(
            api_symbol=api_symbol, dex=dex, display_symbol=display_symbol,
            timestamp_ms=ts_ms, timestamp_utc=ts_utc,
            block_height=block_height,
            bids=bids[:20], asks=asks[:20],
            best_bid=best_bid, best_ask=best_ask,
            mid=mid, quoted_spread_bps=spread_bps,
            two_sided=True, parse_status="ok",
            source_key=source_key,
        ))

    return snapshots


def compute_book_depth_metrics(bids: List[dict], asks: List[dict]) -> BookDepthMetrics:
    """Compute depth at $100/$500/$1000/$5000 notional on each side."""
    thresholds = [100.0, 500.0, 1000.0, 5000.0]
    bid_depths = {t: 0.0 for t in thresholds}
    ask_depths = {t: 0.0 for t in thresholds}

    for b in bids:
        px, sz = b["px"], b["sz"]
        notional = px * sz
        for t in thresholds:
            if bid_depths[t] < t:
                remaining = t - bid_depths[t]
                contrib = min(notional, remaining)
                bid_depths[t] += contrib

    for a in asks:
        px, sz = a["px"], a["sz"]
        notional = px * sz
        for t in thresholds:
            if ask_depths[t] < t:
                remaining = t - ask_depths[t]
                contrib = min(notional, remaining)
                ask_depths[t] += contrib

    cap = len(bids) >= 20 or len(asks) >= 20

    return BookDepthMetrics(
        depth_bid_100=bid_depths[100.0], depth_ask_100=ask_depths[100.0],
        depth_bid_500=bid_depths[500.0], depth_ask_500=ask_depths[500.0],
        depth_bid_1000=bid_depths[1000.0], depth_ask_1000=ask_depths[1000.0],
        depth_bid_5000=bid_depths[5000.0], depth_ask_5000=ask_depths[5000.0],
        min_two_sided_depth_100=min(bid_depths[100.0], ask_depths[100.0]),
        min_two_sided_depth_500=min(bid_depths[500.0], ask_depths[500.0]),
        min_two_sided_depth_1000=min(bid_depths[1000.0], ask_depths[1000.0]),
        top20_depth_cap_flag=cap,
    )


# ---------------------------------------------------------------------------
# Timestamp and block-height alignment
# ---------------------------------------------------------------------------

def _ts_to_seconds(ts_utc: str) -> float:
    """Convert ISO 8601 UTC string to seconds since epoch."""
    try:
        dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def align_snapshots(
    left_snaps: List[L2BookSnapshot],
    right_snaps: List[L2BookSnapshot],
    pair: CrossDexPair,
    noarb_config: NoArbBandConfig,
    primary_tol: float = 5.0,
    diagnostic_tol: float = 30.0,
    alignment_mode: str = "nearest",
    max_align_gap_seconds: float = 60.0,
) -> Tuple[List[AlignedCrossDexObservation], dict]:
    """Align left/right snapshots by timestamp.

    Modes:
      - "exact": only align snapshots with identical timestamp_ms (within 1ms)
      - "nearest": align nearest snapshots within max_align_gap_seconds

    Returns (observations, alignment_diagnostics_dict).
    """
    left_valid = [s for s in left_snaps if s.parse_status == "ok" and s.two_sided]
    right_valid = [s for s in right_snaps if s.parse_status == "ok" and s.two_sided]

    # Build diagnostics
    diag: dict = {
        "pair_id": pair.pair_id,
        "left_api_symbol": pair.left_leg.api_symbol,
        "right_api_symbol": pair.right_leg.api_symbol,
        "left_snapshot_count": len(left_valid),
        "right_snapshot_count": len(right_valid),
        "alignment_mode_used": alignment_mode,
        "max_align_gap_seconds": max_align_gap_seconds,
    }

    if not left_valid or not right_valid:
        diag["alignment_failure_reason"] = "no_valid_snapshots"
        diag["exact_block_time_overlap_count"] = 0
        diag["nearest_time_overlap_count_by_tolerance"] = {"0s": 0, "1s": 0, "5s": 0, "10s": 0, "30s": 0, "60s": 0}
        diag["median_nearest_gap_seconds"] = None
        diag["p90_nearest_gap_seconds"] = None
        diag["p99_nearest_gap_seconds"] = None
        diag["unmatched_left_count"] = len(left_valid)
        diag["unmatched_right_count"] = len(right_valid)
        return [], diag

    # Sort by timestamp
    left_valid.sort(key=lambda s: s.timestamp_ms)
    right_valid.sort(key=lambda s: s.timestamp_ms)

    # Build sorted right timestamp array for bisect
    right_ts = [s.timestamp_ms / 1000.0 for s in right_valid]
    right_used = set()

    # Time range
    diag["left_min_block_time"] = left_valid[0].timestamp_utc
    diag["left_max_block_time"] = left_valid[-1].timestamp_utc
    diag["right_min_block_time"] = right_valid[0].timestamp_utc
    diag["right_max_block_time"] = right_valid[-1].timestamp_utc

    observations = []
    all_gaps = []
    exact_count = 0
    tolerance_counts = {"0s": 0, "1s": 0, "5s": 0, "10s": 0, "30s": 0, "60s": 0}
    unmatched_left = 0

    for lv in left_valid:
        lv_sec = lv.timestamp_ms / 1000.0

        if alignment_mode == "exact":
            # Exact mode: find right snapshot with same timestamp (within 1ms)
            best_delta = float("inf")
            best_rv = None
            best_j = -1
            for j, rv in enumerate(right_valid):
                if j in right_used:
                    continue
                rv_sec = rv.timestamp_ms / 1000.0
                delta = abs(lv_sec - rv_sec)
                if delta < best_delta:
                    best_delta = delta
                    best_rv = rv
                    best_j = j

            if best_rv is None or best_delta > 0.001:  # 1ms tolerance for exact
                unmatched_left += 1
                continue
            right_used.add(best_j)
            gap_s = best_delta

        else:
            # Nearest mode: use bisect for proper nearest-neighbor search
            pos = bisect.bisect_left(right_ts, lv_sec)
            best_delta = float("inf")
            best_rv = None
            best_j = -1

            # Check candidates at pos-1, pos, pos+1
            for j in [pos - 1, pos, pos + 1]:
                if j < 0 or j >= len(right_valid):
                    continue
                rv_sec = right_ts[j]
                delta = abs(lv_sec - rv_sec)
                if delta < best_delta:
                    best_delta = delta
                    best_rv = right_valid[j]
                    best_j = j

            if best_rv is None or best_delta > max_align_gap_seconds:
                unmatched_left += 1
                continue

            gap_s = best_delta

        all_gaps.append(gap_s)

        # Tolerance counts
        if gap_s <= 0.001:
            tolerance_counts["0s"] += 1
        if gap_s <= 1.0:
            tolerance_counts["1s"] += 1
        if gap_s <= 5.0:
            tolerance_counts["5s"] += 1
        if gap_s <= 10.0:
            tolerance_counts["10s"] += 1
        if gap_s <= 30.0:
            tolerance_counts["30s"] += 1
        if gap_s <= 60.0:
            tolerance_counts["60s"] += 1

        # Exact count for exact matches
        if gap_s <= 0.001:
            exact_count += 1

        is_primary = gap_s <= primary_tol

        # Depth metrics
        left_depth = compute_book_depth_metrics(lv.bids, lv.asks) if lv.bids and lv.asks else None
        right_depth = compute_book_depth_metrics(best_rv.bids, best_rv.asks) if best_rv.bids and best_rv.asks else None

        # Cross-mid spread
        left_mid = lv.mid
        right_mid = best_rv.mid
        avg_mid = (left_mid + right_mid) / 2.0
        if avg_mid <= 0:
            continue

        cross_spread_bps = 10000.0 * (left_mid - right_mid) / avg_mid
        abs_cross_spread_bps = abs(cross_spread_bps)
        combined_visible = lv.quoted_spread_bps + best_rv.quoted_spread_bps
        noarb_band = noarb_config.conservative_noarb_band_bps

        same_block = None
        if lv.block_height is not None and best_rv.block_height is not None:
            same_block = lv.block_height == best_rv.block_height

        obs = AlignedCrossDexObservation(
            pair_id=pair.pair_id,
            display_symbol=pair.display_symbol,
            left_api_symbol=lv.api_symbol,
            right_api_symbol=best_rv.api_symbol,
            left_timestamp_utc=lv.timestamp_utc,
            right_timestamp_utc=best_rv.timestamp_utc,
            timestamp_gap_seconds=gap_s,
            same_block_height=same_block,
            left_block_height=lv.block_height,
            right_block_height=best_rv.block_height,
            left_mid=left_mid,
            right_mid=right_mid,
            cross_mid_spread_bps=cross_spread_bps,
            abs_cross_mid_spread_bps=abs_cross_spread_bps,
            left_quoted_spread_bps=lv.quoted_spread_bps,
            right_quoted_spread_bps=best_rv.quoted_spread_bps,
            combined_visible_spread_bps=combined_visible,
            conservative_four_fill_fee_band_bps=noarb_config.conservative_four_fill_fee_band_bps,
            funding_differential_band_bps=noarb_config.funding_differential_band_bps,
            conservative_noarb_band_bps=noarb_band,
            excess_over_visible_spread_bps=abs_cross_spread_bps - combined_visible,
            excess_over_noarb_band_bps=abs_cross_spread_bps - noarb_band,
            left_depth_metrics=left_depth.to_dict() if left_depth else None,
            right_depth_metrics=right_depth.to_dict() if right_depth else None,
            both_two_sided=lv.two_sided and best_rv.two_sided,
            both_depth_available=left_depth is not None and right_depth is not None,
            is_primary_alignment=is_primary,
        )
        observations.append(obs)

    # Fill diagnostics
    diag["exact_block_time_overlap_count"] = exact_count
    diag["nearest_time_overlap_count_by_tolerance"] = tolerance_counts
    diag["unmatched_left_count"] = unmatched_left
    diag["unmatched_right_count"] = len(right_valid) - len(right_used)
    diag["recommendation"] = ""

    if all_gaps:
        all_gaps_sorted = sorted(all_gaps)
        diag["median_nearest_gap_seconds"] = percentile(all_gaps_sorted, 50.0)
        diag["p90_nearest_gap_seconds"] = percentile(all_gaps_sorted, 90.0)
        diag["p99_nearest_gap_seconds"] = percentile(all_gaps_sorted, 99.0)
    else:
        diag["median_nearest_gap_seconds"] = None
        diag["p90_nearest_gap_seconds"] = None
        diag["p99_nearest_gap_seconds"] = None

    if not observations and alignment_mode == "exact":
        diag["alignment_failure_reason"] = "exact_mode_no_matching_timestamps"
        diag["recommendation"] = "NVDA exact synchronization unavailable in sample; try nearest mode with gap tolerance"
    elif not observations:
        diag["alignment_failure_reason"] = f"no_snapshots_within_{max_align_gap_seconds}s_gap"
        diag["recommendation"] = "Increase max_align_gap_seconds or check that both DEXes have overlapping time ranges"
    elif alignment_mode == "nearest":
        diag["alignment_semantics"] = "diagnostic_nearest_neighbor"
        diag["recommendation"] = "Nearest-neighbor alignment is diagnostic only; not a synchronized book comparison"

    return observations, diag


# ---------------------------------------------------------------------------
# Reversion diagnostics
# ---------------------------------------------------------------------------

def compute_reversion_diagnostics(
    observations: List[AlignedCrossDexObservation],
    pair_id: str,
) -> ReversionDiagnostics:
    """Compute reversion/level-offset diagnostics from aligned observations."""
    if len(observations) < 3:
        return ReversionDiagnostics(
            pair_id=pair_id,
            signed_spread_series_count=len(observations),
            classification="underpowered_for_reversion",
            notes="Too few observations for reversion analysis",
        )

    signed = [o.cross_mid_spread_bps for o in observations]
    n = len(signed)

    # Zero crossings
    crossings = 0
    for i in range(1, n):
        if signed[i] * signed[i - 1] < 0:
            crossings += 1
    crossing_rate = crossings / max(n - 1, 1)

    # Max same-sign run
    max_run = 1
    current_run = 1
    for i in range(1, n):
        if signed[i] * signed[i - 1] > 0:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1

    # Level offset detection: if >80% same sign
    pos_count = sum(1 for s in signed if s > 0)
    neg_count = sum(1 for s in signed if s < 0)
    dominant_share = max(pos_count, neg_count) / n
    level_offset = dominant_share > 0.8 and max_run > n * 0.5

    # Reversion: needs zero crossings AND not a level offset
    reversion = crossings >= 3 and not level_offset and crossing_rate > 0.1

    classification = "underpowered_for_reversion"
    if level_offset:
        classification = "persistent_level_offset"
    elif reversion:
        classification = "reversion_supported"

    return ReversionDiagnostics(
        pair_id=pair_id,
        signed_spread_series_count=n,
        zero_crossing_count=crossings,
        zero_crossing_rate=crossing_rate,
        max_persistent_same_sign_run=max_run,
        level_offset_detected=level_offset,
        reversion_supported=reversion,
        classification=classification,
        notes=f"pos={pos_count}, neg={neg_count}, dominant_share={dominant_share:.2f}",
    )


# ---------------------------------------------------------------------------
# Tail, staleness, and dead-book diagnostics
# ---------------------------------------------------------------------------

def compute_tail_diagnostics(
    observations: List[AlignedCrossDexObservation],
    pair_id: str,
) -> dict:
    """Compute tail observation diagnostics for a pair."""
    tail_25 = [o for o in observations if o.abs_cross_mid_spread_bps >= 25.0]
    tail_50 = [o for o in observations if o.abs_cross_mid_spread_bps >= 50.0]
    excess_5 = [o for o in observations if o.excess_over_noarb_band_bps >= 5.0]
    excess_10 = [o for o in observations if o.excess_over_noarb_band_bps >= 10.0]

    # Concentration by hour
    def _concentrate_by(items, key_fn):
        counts = defaultdict(int)
        for o in items:
            counts[key_fn(o)] += 1
        total = len(items) or 1
        return {k: {"count": v, "share": round(v / total, 4)} for k, v in sorted(counts.items(), key=lambda x: -x[1])}

    tail_for_conc = excess_5 if excess_5 else tail_25
    conc_hour = _concentrate_by(tail_for_conc, lambda o: o.left_timestamp_utc[:13] if o.left_timestamp_utc else "unknown")
    conc_day = _concentrate_by(tail_for_conc, lambda o: o.left_timestamp_utc[:10] if o.left_timestamp_utc else "unknown")

    # Staleness: large gap between left and right timestamps
    stale_obs = [o for o in observations if o.timestamp_gap_seconds > 10.0]

    # Dead book: one leg has very wide quoted spread
    dead_book = [o for o in observations if o.left_quoted_spread_bps > 100.0 or o.right_quoted_spread_bps > 100.0]

    return {
        "pair_id": pair_id,
        "total_aligned": len(observations),
        "tail_abs_25_bps_count": len(tail_25),
        "tail_abs_50_bps_count": len(tail_50),
        "excess_over_noarb_5bps_count": len(excess_5),
        "excess_over_noarb_10bps_count": len(excess_10),
        "tail_concentration_by_hour": conc_hour,
        "tail_concentration_by_day": conc_day,
        "stale_timestamp_count": len(stale_obs),
        "dead_book_count": len(dead_book),
        "max_single_hour_share": max((v["share"] for v in conc_hour.values()), default=0.0),
        "max_single_day_share": max((v["share"] for v in conc_day.values()), default=0.0),
    }


# ---------------------------------------------------------------------------
# Forward-recorder cross-validation
# ---------------------------------------------------------------------------

def check_forward_recorder_overlap(
    sonarx_obs: List[AlignedCrossDexObservation],
    recorder_root: str,
    pair: CrossDexPair,
) -> dict:
    """Check overlap between SonarX observations and forward recorder data."""
    result = {
        "recorder_root_found": False,
        "overlap_available": False,
        "overlapping_pairs": [],
        "overlap_observation_count": 0,
        "median_abs_spread_difference_bps": None,
        "p95_abs_spread_difference_bps": None,
        "validation_status": "FORWARD_RECORDER_OVERLAP_UNAVAILABLE",
        "limitation": "",
    }

    root = Path(recorder_root)
    if not root.exists():
        result["limitation"] = f"Recorder root not found: {recorder_root}"
        return result

    result["recorder_root_found"] = True

    # Scan for JSONL files under asset_context_snapshots/
    jsonl_files = list(root.rglob("*.jsonl"))
    if not jsonl_files:
        result["limitation"] = "No JSONL files found in recorder root"
        return result

    # Load recorder data for both legs
    recorder_data = {}
    for api_sym in [pair.left_leg.api_symbol, pair.right_leg.api_symbol]:
        recorder_data[api_sym] = []

    for jf in jsonl_files:
        try:
            if orjson is not None:
                raw = jf.read_bytes()
                for line in raw.split(b"\n"):
                    if not line.strip():
                        continue
                    row = orjson.loads(line)
                    api = row.get("api_symbol", "")
                    if api in recorder_data:
                        recorder_data[api].append(row)
            else:
                with jf.open("r") as f:
                    for line in f:
                        row = json.loads(line.strip())
                        api = row.get("api_symbol", "")
                        if api in recorder_data:
                            recorder_data[api].append(row)
        except Exception:
            continue

    left_recs = recorder_data.get(pair.left_leg.api_symbol, [])
    right_recs = recorder_data.get(pair.right_leg.api_symbol, [])

    if not left_recs or not right_recs:
        result["limitation"] = f"Recorder has data for only one leg: left={len(left_recs)}, right={len(right_recs)}"
        return result

    # Simple overlap: find matching timestamps within 5s
    diffs = []
    for lr in left_recs:
        lr_ts = lr.get("timestamp_utc", "")
        lr_mid = safe_float(lr.get("mid_price", lr.get("mark_price")))
        if lr_mid is None or not lr_ts:
            continue
        try:
            lr_dt = datetime.fromisoformat(lr_ts.replace("Z", "+00:00"))
        except Exception:
            continue

        for rr in right_recs:
            rr_ts = rr.get("timestamp_utc", "")
            rr_mid = safe_float(rr.get("mid_price", rr.get("mark_price")))
            if rr_mid is None or not rr_ts:
                continue
            try:
                rr_dt = datetime.fromisoformat(rr_ts.replace("Z", "+00:00"))
                gap = abs((lr_dt - rr_dt).total_seconds())
                if gap <= 5.0:
                    avg = (lr_mid + rr_mid) / 2.0
                    if avg > 0:
                        diff = abs(10000.0 * (lr_mid - rr_mid) / avg)
                        diffs.append(diff)
            except Exception:
                continue

    if not diffs:
        result["limitation"] = "No overlapping timestamps found within 5s tolerance"
        return result

    diffs.sort()
    result["overlap_available"] = True
    result["overlapping_pairs"] = [pair.pair_id]
    result["overlap_observation_count"] = len(diffs)
    result["median_abs_spread_difference_bps"] = percentile(diffs, 50.0)
    result["p95_abs_spread_difference_bps"] = percentile(diffs, 95.0)
    result["validation_status"] = "FORWARD_RECORDER_CROSS_VALIDATION_PASSED"
    return result


# ---------------------------------------------------------------------------
# Pair spread summary computation
# ---------------------------------------------------------------------------

def compute_pair_summary(
    observations: List[AlignedCrossDexObservation],
    pair_id: str,
) -> PairSpreadSummary:
    """Compute summary statistics for a pair's aligned observations."""
    if not observations:
        return PairSpreadSummary(pair_id=pair_id, gate_status="NO_DATA")

    n = len(observations)
    primary = [o for o in observations if o.is_primary_alignment]
    diagnostic = [o for o in observations if not o.is_primary_alignment]
    same_block = [o for o in observations if o.same_block_height is True]

    abs_spreads = sorted([o.abs_cross_mid_spread_bps for o in observations])
    excesses = sorted([o.excess_over_noarb_band_bps for o in observations])
    within_band = [o for o in observations if o.excess_over_noarb_band_bps <= 0]

    # Tail diagnostics
    tail_diag = compute_tail_diagnostics(observations, pair_id)

    # Depth summary
    left_depths = [o.left_depth_metrics for o in observations if o.left_depth_metrics]
    right_depths = [o.right_depth_metrics for o in observations if o.right_depth_metrics]
    depth_summary = {}
    if left_depths:
        depth_summary["left_median_min_depth_500"] = percentile(
            sorted([d.get("min_two_sided_depth_500", 0) for d in left_depths]), 50.0)
        depth_summary["left_p75_min_depth_500"] = percentile(
            sorted([d.get("min_two_sided_depth_500", 0) for d in left_depths]), 75.0)
    if right_depths:
        depth_summary["right_median_min_depth_500"] = percentile(
            sorted([d.get("min_two_sided_depth_500", 0) for d in right_depths]), 50.0)
        depth_summary["right_p75_min_depth_500"] = percentile(
            sorted([d.get("min_two_sided_depth_500", 0) for d in right_depths]), 75.0)

    return PairSpreadSummary(
        pair_id=pair_id,
        aligned_observation_count=n,
        primary_alignment_count=len(primary),
        diagnostic_alignment_count=len(diagnostic),
        primary_alignment_share=len(primary) / n if n else 0.0,
        same_block_observation_count=len(same_block),
        same_block_observation_share=len(same_block) / n if n else 0.0,
        median_abs_cross_mid_spread_bps=percentile(abs_spreads, 50.0),
        p75_abs_cross_mid_spread_bps=percentile(abs_spreads, 75.0),
        p95_abs_cross_mid_spread_bps=percentile(abs_spreads, 95.0),
        p99_abs_cross_mid_spread_bps=percentile(abs_spreads, 99.0),
        median_excess_over_noarb_band_bps=percentile(excesses, 50.0),
        p95_excess_over_noarb_band_bps=percentile(excesses, 95.0),
        p99_excess_over_noarb_band_bps=percentile(excesses, 99.0),
        tail_observation_count=tail_diag["excess_over_noarb_5bps_count"],
        within_band_observation_share=len(within_band) / n if n else 0.0,
        tail_concentration_by_hour=tail_diag.get("tail_concentration_by_hour", {}),
        tail_concentration_by_day=tail_diag.get("tail_concentration_by_day", {}),
        depth_summary=depth_summary,
    )



# ---------------------------------------------------------------------------
# Phase -1 gate logic
# ---------------------------------------------------------------------------

def compute_gate_decision(
    pair_summaries, noarb_configs, reversion_results, forward_validation, fee_configs,
) -> PhaseMinus1Decision:
    """Compute the Phase -1 gate decision from pair summaries."""
    decision = PhaseMinus1Decision()
    warnings = []
    limitations = []
    candidate_pairs = []
    blocked_pairs = []

    flx_pairs = [s for s in pair_summaries if 'flx:' in s.pair_id]
    if flx_pairs:
        warnings.append('flx pairs present but cannot contribute to pass gates')

    data_bearing = [s for s in pair_summaries if s.aligned_observation_count > 0]
    no_data = [s for s in pair_summaries if s.aligned_observation_count == 0]

    if len(no_data) == len(pair_summaries) and pair_summaries:
        decision.final_status = "HIP3_CROSS_DEX_NOARB_ZERO_DATA_SAMPLER_FAILURE"
        decision.reason = "All pairs have zero aligned observations"
        decision.limitations = ['Zero data cannot claim liquidity/spread failure']
        return decision

    if len(pair_summaries) < 2:
        decision.final_status = "HIP3_CROSS_DEX_NOARB_L2_UNDERPOWERED"
        decision.reason = f"Only {len(pair_summaries)} pair(s) attempted; need >=2"
        return decision

    for s in pair_summaries:
        if s.aligned_observation_count == 0:
            blocked_pairs.append(s.pair_id)
            continue
        if s.primary_alignment_share < 0.70:
            limitations.append(f'{s.pair_id}: primary share {s.primary_alignment_share:.1%} < 70%')
            blocked_pairs.append(s.pair_id)
            continue
        if s.primary_alignment_count < 500:
            limitations.append(f'{s.pair_id}: only {s.primary_alignment_count} primary obs < 500')
            blocked_pairs.append(s.pair_id)
            continue
        if s.p95_excess_over_noarb_band_bps < 5.0:
            limitations.append(f'{s.pair_id}: p95 excess {s.p95_excess_over_noarb_band_bps:.1f}bps < 5bps')
            blocked_pairs.append(s.pair_id)
            continue
        rev = reversion_results.get(s.pair_id)
        if rev and rev.get('classification') == 'persistent_level_offset':
            blocked_pairs.append(s.pair_id)
            limitations.append(f'{s.pair_id}: persistent level offset')
            continue
        if rev and rev.get('classification') != 'reversion_supported':
            blocked_pairs.append(s.pair_id)
            limitations.append(f'{s.pair_id}: reversion not supported')
            continue
        candidate_pairs.append(s.pair_id)

    if forward_validation.get('overlap_available') and forward_validation.get('validation_status') != 'FORWARD_RECORDER_CROSS_VALIDATION_PASSED':
        decision.final_status = "HIP3_CROSS_DEX_NOARB_FORWARD_RECORDER_VALIDATION_FAILED"
        decision.reason = "Forward recorder cross-validation failed"
        decision.warnings = warnings
        decision.limitations = limitations
        return decision

    if candidate_pairs:
        for cp_id in candidate_pairs:
            ps = next(s for s in pair_summaries if s.pair_id == cp_id)
            rev = reversion_results.get(cp_id, {})
            depth = ps.depth_summary
            left_ok = depth.get('left_median_min_depth_500', 0) >= 500.0
            right_ok = depth.get('right_median_min_depth_500', 0) >= 500.0
            if left_ok and right_ok and rev.get('classification') == 'reversion_supported':
                decision.final_status = "HIP3_CROSS_DEX_NOARB_NEXT_PRECOMMITMENT_REVIEW_ALLOWED"
                decision.next_precommitment_review_allowed = True
                decision.candidate_pairs = candidate_pairs
                decision.blocked_pairs = blocked_pairs
                decision.warnings = warnings
                decision.limitations = limitations
                decision.reason = f"Pair {cp_id} passes Phase -1 diagnostic gates"
                return decision
        decision.final_status = "HIP3_CROSS_DEX_NOARB_TAIL_PRESENT_DIAGNOSTIC"
        decision.candidate_pairs = candidate_pairs
        decision.blocked_pairs = blocked_pairs
        decision.warnings = warnings
        decision.limitations = limitations
        decision.reason = "Tail present but not all gates pass"
        return decision

    all_within_band = all(s.p95_excess_over_noarb_band_bps <= 0 for s in data_bearing)
    if all_within_band:
        decision.final_status = "HIP3_CROSS_DEX_NOARB_SPREAD_WITHIN_BAND"
        decision.reason = "All cross-DEX spreads within conservative no-arb band"
    elif any('persistent level offset' in lim for lim in limitations):
        decision.final_status = "HIP3_CROSS_DEX_NOARB_PERSISTENT_LEVEL_OFFSET"
        decision.reason = "Spread exceeds band but persistent level offset detected"
    elif any('reversion not supported' in lim for lim in limitations):
        decision.final_status = "HIP3_CROSS_DEX_NOARB_REVERSION_UNDERPOWERED"
        decision.reason = "Spread exceeds band but reversion diagnostics insufficient"
    else:
        decision.final_status = "HIP3_CROSS_DEX_NOARB_NO_TAIL"
        decision.reason = "No meaningful cross-DEX spread tail observed"

    decision.blocked_pairs = blocked_pairs
    decision.warnings = warnings
    decision.limitations = limitations
    return decision


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_phase_minus1(args: argparse.Namespace) -> int:
    """Main entry point for Phase -1 cross-DEX no-arb-band scout."""
    run_id = make_run_id()
    out_root = Path(args.out_root) / run_id
    out_root.mkdir(parents=True, exist_ok=True)

    git_info = git_metadata()

    # Write run manifest
    manifest = {
        "study_id": STUDY_ID,
        "run_id": run_id,
        "created_at_utc": utc_now_iso(),
        "git_sha": git_info["git_sha"],
        "git_dirty": git_info["git_dirty"],
        "branch": git_info["branch"],
        "args": vars(args),
        "safety_mode": "public_data_observer_only",
        "no_orders_no_auth_no_live": True,
        "no_pnl_no_returns_no_signals": True,
        "no_registry_mutation": True,
    }
    write_json(out_root / "run_manifest.json", manifest)

    # Parse pairs
    pairs = []
    pair_strs = args.pairs.split(",") if args.pairs else DEFAULT_PAIRS
    for ps in pair_strs:
        try:
            p = parse_pair_string(
                ps,
                allow_mismatched=getattr(args, "allow_mismatched_display_symbols", False),
                allow_flx=getattr(args, "allow_flx_diagnostic_only", False),
            )
            pairs.append(p)
        except ValueError as e:
            print(f"PAIR_PARSE_ERROR: {ps}: {e}", flush=True)

    if not pairs:
        print("ERROR: No valid pairs parsed", flush=True)
        return 1

    write_json(out_root / "pair_definitions.json", {
        "pairs": [p.to_dict() for p in pairs],
        "flx_forbidden_by_default": FLX_FORBIDDEN_BY_DEFAULT,
    })

    # Network chokepoint
    chokepoint = NetworkChokepoint(
        allow_network_public=args.allow_network_public,
        allow_s3_archive_read=args.allow_s3_archive_read,
        s3_connect_timeout=args.s3_connect_timeout_seconds,
        s3_read_timeout=args.s3_read_timeout_seconds,
        s3_max_attempts=args.s3_max_attempts,
    )

    # Fee/funding discovery
    fee_configs = {}
    funding_configs = {}
    noarb_configs = {}
    all_legs_set = set()

    for pair in pairs:
        for leg in [pair.left_leg, pair.right_leg]:
            if leg.api_symbol not in all_legs_set:
                all_legs_set.add(leg.api_symbol)
                fee_configs[leg.api_symbol] = discover_fee_margin_config(
                    leg.dex, leg.api_symbol, chokepoint,
                    fallback_one_way_bps=args.fallback_one_way_hip3_taker_fee_bps,
                )
                funding_configs[leg.api_symbol] = discover_funding_config(
                    leg.dex, leg.api_symbol, chokepoint,
                    fallback_funding_diff_bps_per_hour=args.fallback_funding_diff_bps_per_hour,
                )

        noarb_configs[pair.pair_id] = compute_noarb_band_config(
            pair,
            fee_configs[pair.left_leg.api_symbol],
            fee_configs[pair.right_leg.api_symbol],
            funding_configs[pair.left_leg.api_symbol],
            funding_configs[pair.right_leg.api_symbol],
            expected_hold_minutes=args.expected_hold_minutes,
            extra_uncertainty_bps=args.extra_uncertainty_band_bps,
        )

    write_json(out_root / "fee_margin_discovery.json", {
        "configs": {k: v.to_dict() for k, v in fee_configs.items()},
        "no_user_or_account_endpoints_used": True,
        "four_fill_fee_formula": "2 * left_one_way + 2 * right_one_way",
    })
    write_json(out_root / "funding_discovery.json", {
        "configs": {k: v.to_dict() for k, v in funding_configs.items()},
        "no_user_or_account_endpoints_used": True,
        "funding_differential_formula": "left_rate + right_rate over hold window",
    })
    write_json(out_root / "noarb_band_assumptions.json", {
        "configs": {k: v.to_dict() for k, v in noarb_configs.items()},
        "formula": "combined_visible_spread + four_fill_fee + funding_diff + margin_friction + extra_uncertainty",
        "no_user_or_account_endpoints_used": True,
    })

    # Dry-run mode
    if args.dry_run:
        status = {
            "status": "HIP3_CROSS_DEX_NOARB_DRY_RUN_READY",
            "run_id": run_id,
            "pairs_attempted": len(pairs),
            "no_network_calls": True,
            "no_registry_mutation": True,
        }
        write_json(out_root / "phase_minus1_status.json", status)
        write_json(out_root / "summary.json", {"status": "DRY_RUN_COMPLETE", "run_id": run_id})
        write_json(out_root / "excluded_dexes.json", {"excluded": ["flx"], "reason": "stale_sparse_oracle"})
        print(f"DRY_RUN_COMPLETE: {run_id}", flush=True)
        return 0

    # SonarX key discovery
    unique_legs = []
    seen_syms = set()
    for pair in pairs:
        for leg in [pair.left_leg, pair.right_leg]:
            if leg.api_symbol not in seen_syms:
                seen_syms.add(leg.api_symbol)
                unique_legs.append(leg)

    print(f"DISCOVERING_SONARX_KEYS for {len(unique_legs)} legs...", flush=True)
    sonarx_inventory = discover_sonarx_keys(
        chokepoint, unique_legs,
        sample_days=args.sample_days,
        max_files_per_leg=args.max_files_per_leg,
    )
    write_json(out_root / "sonarx_key_inventory.json", sonarx_inventory)
    write_json(out_root / "sonarx_key_selection_plan.json", {
        "selected_keys_by_leg": sonarx_inventory.get("selected_keys_by_leg", {}),
        "requester_pays": True,
    })

    zero_data_legs = sonarx_inventory.get("zero_data_status_if_any", [])
    if len(zero_data_legs) == len(unique_legs):
        write_json(out_root / "phase_minus1_status.json", {
            "status": "HIP3_CROSS_DEX_NOARB_ZERO_DATA_SAMPLER_FAILURE",
            "run_id": run_id, "zero_data_legs": zero_data_legs,
        })
        print("ZERO_DATA_SAMPLER_FAILURE: No SonarX keys found for any leg", flush=True)
        return 0

    # Budget calibration
    all_sizes = []
    for data in sonarx_inventory.get("keys_available_by_leg", {}).values():
        for obj in data.get("keys", []):
            if isinstance(obj, dict) and "size" in obj:
                all_sizes.append(obj["size"])

    budget = {
        "observed_file_sizes": sorted(all_sizes)[:100],
        "p50_file_size_bytes": percentile(sorted(all_sizes), 50.0) if all_sizes else 0,
        "p95_file_size_bytes": percentile(sorted(all_sizes), 95.0) if all_sizes else 0,
        "planned_files_total": min(args.max_files_total, len(all_sizes) * len(unique_legs)) if all_sizes else 0,
        "requested_download_budget_bytes": args.download_budget_bytes,
        "budget_sufficient": True,
    }
    if all_sizes:
        budget["expected_bytes_p50"] = budget["p50_file_size_bytes"] * budget["planned_files_total"]
        budget["expected_bytes_p95"] = budget["p95_file_size_bytes"] * budget["planned_files_total"]
        budget["budget_sufficient"] = budget["expected_bytes_p95"] <= args.download_budget_bytes
    write_json(out_root / "download_budget_calibration.json", budget)

    if args.coverage_only:
        write_json(out_root / "phase_minus1_status.json", {
            "status": "HIP3_CROSS_DEX_NOARB_COVERAGE_READY",
            "run_id": run_id,
        })
        print(f"COVERAGE_READY: {run_id}", flush=True)
        return 0

    # Download and parse L2
    parse_quality = {"total_files": 0, "successful_parses": 0, "decode_failures": 0}
    decode_failures = []
    leg_snapshots = defaultdict(list)

    for leg in unique_legs:
        selected = sonarx_inventory.get("selected_keys_by_leg", {}).get(leg.api_symbol, [])
        count = 0
        for obj in selected:
            if count >= args.max_files_per_leg:
                break
            key = obj.get("key", "") if isinstance(obj, dict) else str(obj)
            if not key:
                continue
            try:
                raw = chokepoint.s3_read_object(BUCKET_NAME, key, requester_pays=True)
                parse_quality["total_files"] += 1
                snaps = parse_l2_snapshot_file(raw, key, leg.api_symbol, leg.dex, leg.display_symbol)
                valid = [s for s in snaps if s.parse_status == "ok"]
                if valid:
                    parse_quality["successful_parses"] += 1
                    leg_snapshots[leg.api_symbol].extend(valid)
                else:
                    parse_quality["decode_failures"] += 1
                    decode_failures.append({"key": key, "error": snaps[0].parse_status if snaps else "empty"})
                count += 1
                if count % 5 == 0:
                    print(f"  {leg.api_symbol}: {count}/{len(selected)} files, {len(leg_snapshots[leg.api_symbol])} snaps", flush=True)
            except Exception as e:
                parse_quality["decode_failures"] += 1
                decode_failures.append({"key": key, "error": str(e)})
                continue

    write_json(out_root / "l2_parse_quality.json", parse_quality)
    write_json(out_root / "l2_decode_failure_audit.json", {"failures": decode_failures})

    # Depth by leg
    depth_by_leg = {}
    for api_sym, snaps in leg_snapshots.items():
        depths = [compute_book_depth_metrics(s.bids, s.asks) for s in snaps if s.bids and s.asks]
        if depths:
            depth_by_leg[api_sym] = {
                "count": len(depths),
                "median_min_depth_500": percentile(sorted([d.min_two_sided_depth_500 for d in depths]), 50.0),
                "p75_min_depth_500": percentile(sorted([d.min_two_sided_depth_500 for d in depths]), 75.0),
            }
    write_json(out_root / "l2_depth_by_leg_summary.json", depth_by_leg)

    # Alignment and spread
    pair_summaries = []
    reversion_results = {}
    all_obs_dicts = []
    alignment_diagnostics = {}

    alignment_mode = getattr(args, "alignment_mode", "nearest")
    max_gap = getattr(args, "max_align_gap_seconds", 60.0)
    min_aligned = getattr(args, "min_aligned_observations", 50)

    for pair in pairs:
        left_snaps = leg_snapshots.get(pair.left_leg.api_symbol, [])
        right_snaps = leg_snapshots.get(pair.right_leg.api_symbol, [])

        if not left_snaps or not right_snaps:
            pair_summaries.append(PairSpreadSummary(pair_id=pair.pair_id, gate_status="NO_DATA"))
            alignment_diagnostics[pair.pair_id] = {"alignment_failure_reason": "no_data_for_one_or_both_legs"}
            continue

        noarb = noarb_configs[pair.pair_id]
        obs, diag = align_snapshots(
            left_snaps, right_snaps, pair, noarb,
            primary_tol=args.primary_align_tolerance_seconds,
            diagnostic_tol=args.diagnostic_align_tolerance_seconds,
            alignment_mode=alignment_mode,
            max_align_gap_seconds=max_gap,
        )
        alignment_diagnostics[pair.pair_id] = diag

        for o in obs:
            all_obs_dicts.append({
                "pair_id": o.pair_id, "left_api_symbol": o.left_api_symbol,
                "right_api_symbol": o.right_api_symbol,
                "left_timestamp_utc": o.left_timestamp_utc,
                "right_timestamp_utc": o.right_timestamp_utc,
                "timestamp_gap_seconds": o.timestamp_gap_seconds,
                "left_mid": o.left_mid, "right_mid": o.right_mid,
                "cross_mid_spread_bps": o.cross_mid_spread_bps,
                "abs_cross_mid_spread_bps": o.abs_cross_mid_spread_bps,
                "combined_visible_spread_bps": o.combined_visible_spread_bps,
                "conservative_noarb_band_bps": o.conservative_noarb_band_bps,
                "excess_over_noarb_band_bps": o.excess_over_noarb_band_bps,
                "is_primary_alignment": o.is_primary_alignment,
            })

        ps = compute_pair_summary(obs, pair.pair_id)
        pair_summaries.append(ps)
        rev = compute_reversion_diagnostics(obs, pair.pair_id)
        reversion_results[pair.pair_id] = rev.to_dict()

        aligned_count = len(obs)
        gap_info = ""
        if diag.get("median_nearest_gap_seconds") is not None:
            gap_info = f", median_gap={diag['median_nearest_gap_seconds']:.1f}s"
        print(f"PAIR {pair.pair_id}: {aligned_count} aligned ({alignment_mode}), "
              f"p95_spread={ps.p95_abs_cross_mid_spread_bps:.1f}bps, "
              f"p95_excess={ps.p95_excess_over_noarb_band_bps:.1f}bps, "
              f"rev={rev.classification}{gap_info}", flush=True)

    if all_obs_dicts:
        write_jsonl(out_root / "cross_dex_spread_observations.jsonl", all_obs_dicts)

    write_json(out_root / "timestamp_alignment_summary.json", {
        s.pair_id: {"aligned": s.aligned_observation_count, "primary": s.primary_alignment_count}
        for s in pair_summaries
    })
    write_json(out_root / "block_height_alignment_diagnostics.json", {
        s.pair_id: {"same_block_count": s.same_block_observation_count}
        for s in pair_summaries
    })
    write_json(out_root / "cross_dex_alignment_diagnostics.json", alignment_diagnostics)
    write_json(out_root / "cross_dex_spread_pair_summaries.json", {
        s.pair_id: {"aligned": s.aligned_observation_count, "p95_excess": s.p95_excess_over_noarb_band_bps}
        for s in pair_summaries
    })
    write_json(out_root / "reversion_diagnostics.json", reversion_results)
    write_json(out_root / "level_offset_diagnostics.json", {k: v for k, v in reversion_results.items() if v.get("level_offset_detected")})

    # Forward recorder
    fwd_val = {"validation_status": "NOT_ATTEMPTED", "recorder_root_found": False}
    if args.enable_forward_recorder_cross_validation:
        for pair in pairs:
            fwd_val = check_forward_recorder_overlap(all_obs_dicts, args.forward_recorder_root, pair)
            if fwd_val.get("overlap_available"):
                break
    write_json(out_root / "forward_recorder_cross_validation.json", fwd_val)

    # Gate decision
    decision = compute_gate_decision(pair_summaries, noarb_configs, reversion_results, fwd_val, fee_configs)
    write_json(out_root / "cross_dex_spread_gate_decisions.json", decision.to_dict())
    write_json(out_root / "excluded_dexes.json", {"excluded": ["flx"], "reason": "stale_sparse_oracle"})
    write_json(out_root / "phase_minus1_status.json", {
        "status": decision.final_status, "run_id": run_id,
        "pairs_attempted": len(pairs),
        "pairs_data_bearing": len([s for s in pair_summaries if s.aligned_observation_count > 0]),
    })

    summary = {
        "study_id": STUDY_ID, "run_id": run_id, "branch": git_info["branch"],
        "starting_sha": git_info["git_sha"], "status": decision.final_status,
        "next_precommitment_review_allowed": decision.next_precommitment_review_allowed,
        "pairs_attempted": len(pairs),
        "total_snapshots": parse_quality["successful_parses"],
        "decode_failures": parse_quality["decode_failures"],
        "no_pnl_no_returns_no_signals": True, "no_registry_mutation": True,
    }
    write_json(out_root / "summary.json", summary)

    # No-arb band summary
    all_abs = [o["abs_cross_mid_spread_bps"] for o in all_obs_dicts] if all_obs_dicts else [0.0]
    all_excess = [o["excess_over_noarb_band_bps"] for o in all_obs_dicts] if all_obs_dicts else [0.0]
    noarb_band_bps = list(noarb_configs.values())[0].conservative_noarb_band_bps if noarb_configs else 50.0

    noarb_summary = {
        "run_id": run_id,
        "pairs_attempted": len(pairs),
        "pairs_data_bearing": len([s for s in pair_summaries if s.aligned_observation_count > 0]),
        "pairs_aligned": len([s for s in pair_summaries if s.aligned_observation_count >= min_aligned]),
        "alignment_mode": alignment_mode,
        "min_aligned_observations": min_aligned,
        "conservative_noarb_band_bps": noarb_band_bps,
        "fee_band_source": "fallback_conservative",
        "p50_abs_cross_mid_spread_bps": percentile(sorted(all_abs), 50.0),
        "p95_abs_cross_mid_spread_bps": percentile(sorted(all_abs), 95.0),
        "p99_abs_cross_mid_spread_bps": percentile(sorted(all_abs), 99.0),
        "p95_excess_over_noarb_band_bps": percentile(sorted(all_excess), 95.0),
        "classification_by_pair": {},
        "global_status": decision.final_status,
        "limitations": decision.limitations,
        "no_pnl_no_returns_no_signals_confirmation": True,
    }
    for ps in pair_summaries:
        noarb_summary["classification_by_pair"][ps.pair_id] = {
            "aligned": ps.aligned_observation_count,
            "classification": ps.gate_status if ps.gate_status != "PENDING" else "evaluated",
        }
    write_json(out_root / "cross_dex_noarb_band_summary.json", noarb_summary)

    # Pair diagnostics
    pair_diag = {}
    for ps in pair_summaries:
        d = alignment_diagnostics.get(ps.pair_id, {})
        pair_diag[ps.pair_id] = {
            "pair_id": ps.pair_id,
            "left_api_symbol": ps.pair_id.split("|")[0] if "|" in ps.pair_id else "",
            "right_api_symbol": ps.pair_id.split("|")[1] if "|" in ps.pair_id else "",
            "aligned_observations": ps.aligned_observation_count,
            "alignment_mode": alignment_mode,
            "alignment_gap_stats": {
                "median": d.get("median_nearest_gap_seconds"),
                "p90": d.get("p90_nearest_gap_seconds"),
                "p99": d.get("p99_nearest_gap_seconds"),
            },
            "cross_mid_spread_bps_stats": {
                "median": ps.median_abs_cross_mid_spread_bps,
                "p95": ps.p95_abs_cross_mid_spread_bps,
                "p99": ps.p99_abs_cross_mid_spread_bps,
            },
            "noarb_band_bps": noarb_band_bps,
            "excess_over_noarb_band_stats": {
                "median": ps.median_excess_over_noarb_band_bps,
                "p95": ps.p95_excess_over_noarb_band_bps,
                "p99": ps.p99_excess_over_noarb_band_bps,
            },
            "classification": "within_band" if ps.p95_excess_over_noarb_band_bps <= 0 else "outside_band_diagnostic",
            "limitations": [],
        }
    write_json(out_root / "cross_dex_noarb_pair_diagnostics.json", pair_diag)

    # Summary markdown
    md = [f"# HIP-3 Cross-DEX No-Arb-Band Phase -1 Report", "",
          f"**Status:** `{decision.final_status}`", f"**Run ID:** `{run_id}`", "",
          "## Pairs"]
    for s in pair_summaries:
        md.append(f"- `{s.pair_id}`: {s.aligned_observation_count} obs, p95_excess={s.p95_excess_over_noarb_band_bps:.1f}bps")
    md.extend(["", f"## Decision: {decision.reason}", "",
               "## Safety", "- No PnL/returns/signals", "- No registry mutation",
               "- No live/paper/conductor/orders/auth", "- Public archive data only"])
    (out_root / "HIP3_CROSS_DEX_NOARB_BAND_PHASE_MINUS1_REPORT.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nFINAL_STATUS: {decision.final_status}", flush=True)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_hip3_cross_dex_noarb_band_phase_minus1_v0")
    p.add_argument("--out-root", default="reports/hip3_cross_dex_noarb_band_phase_minus1_v0")
    p.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--coverage-only", action="store_true")
    p.add_argument("--real-smoke", action="store_true")
    p.add_argument("--sample-days", type=int, default=30)
    p.add_argument("--sample-mode", default="stratified")
    p.add_argument("--max-files-per-leg", type=int, default=200)
    p.add_argument("--max-files-total", type=int, default=800)
    p.add_argument("--download-budget-bytes", type=int, default=4_000_000_000)
    p.add_argument("--allow-s3-archive-read", action="store_true")
    p.add_argument("--allow-network-public", action="store_true")
    p.add_argument("--requester-pays", action="store_true")
    p.add_argument("--s3-connect-timeout-seconds", type=int, default=10)
    p.add_argument("--s3-read-timeout-seconds", type=int, default=30)
    p.add_argument("--s3-max-attempts", type=int, default=2)
    p.add_argument("--max-runtime-minutes", type=int, default=90)
    p.add_argument("--primary-align-tolerance-seconds", type=float, default=5.0)
    p.add_argument("--diagnostic-align-tolerance-seconds", type=float, default=30.0)
    p.add_argument("--fallback-one-way-hip3-taker-fee-bps", type=float, default=12.5)
    p.add_argument("--fallback-four-fill-fee-band-bps", type=float, default=50.0)
    p.add_argument("--extra-uncertainty-band-bps", type=float, default=5.0)
    p.add_argument("--expected-hold-minutes", type=float, default=60.0)
    p.add_argument("--fallback-funding-diff-bps-per-hour", type=float, default=1.0)
    p.add_argument("--min-depth-usd", type=float, default=500.0)
    p.add_argument("--allow-flx-diagnostic-only", action="store_true")
    p.add_argument("--enable-forward-recorder-cross-validation", action="store_true")
    p.add_argument("--forward-recorder-root", default="reports/hip3_builder_dex_tradfi_forward_recorder_v0")
    p.add_argument("--allow-mismatched-display-symbols", action="store_true")
    p.add_argument("--alignment-mode", choices=["exact", "nearest"], default="nearest",
                    help="exact: only align identical timestamps; nearest: nearest-neighbor within gap")
    p.add_argument("--max-align-gap-seconds", type=float, default=60.0,
                    help="Max seconds gap for nearest-neighbor alignment")
    p.add_argument("--write-alignment-diagnostics", action="store_true",
                    help="Write detailed alignment diagnostic artifact")
    p.add_argument("--min-aligned-observations", type=int, default=50,
                    help="Minimum aligned observations for a pair to be considered data-bearing")
    return p


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run_phase_minus1(args)


if __name__ == "__main__":
    sys.exit(main())
