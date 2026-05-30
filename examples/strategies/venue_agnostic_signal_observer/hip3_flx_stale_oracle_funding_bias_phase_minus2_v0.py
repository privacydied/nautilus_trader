"""HIP-3 FLX stale-oracle funding-bias Phase -2 reachability probe.

Observer-only diagnostic: does flx oracle staleness create a persistent,
signed, lag-driven oracle bias versus active same-underlying builder DEX
reference oracles, slow enough that a later funding-distortion Phase 0
may be worth drafting?

NOT a strategy, NOT a PnL evaluator, NOT a return backtest, NOT paper
trading, NOT live trading, NOT a registry mutation.

Decode path: action.perpDeploy.setOracle.oraclePxs from replica_cmds LZ4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import orjson
except ImportError:
    orjson = None  # type: ignore[assignment]

try:
    import lz4.frame as _lz4_frame
except ImportError:
    _lz4_frame = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _loads_json(data: bytes | str) -> Any:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if orjson is not None:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def _dumps_json(obj: Any) -> bytes:
    if orjson is not None:
        return orjson.dumps(obj)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _dumps_json_pretty(obj: Any) -> bytes:
    if orjson is not None:
        return orjson.dumps(obj, option=orjson.OPT_INDENT_2)
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAFETY_MODE = "public_archive_observer_only"
DEFAULT_TARGET_DEX = "flx"
DEFAULT_SYMBOLS = ["TSLA", "NVDA"]
DEFAULT_REFERENCE_DEXES = ["cash", "km", "xyz", "para"]
DEFAULT_MIN_TARGET_UPDATES = 30
DEFAULT_MIN_REFERENCE_UPDATES = 1000
DEFAULT_MIN_ALIGNED_OBSERVATIONS = 500
DEFAULT_RESIDUAL_EPSILON_BPS = 1.0
DEFAULT_LAG_CORRELATION_THRESHOLD = 0.30
DEFAULT_FUNDING_INTERVAL_SECONDS = 3600
DEFAULT_MIN_FUNDING_CLOCK_PERSISTENCE_SHARE = 0.25
DEFAULT_BOOTSTRAP_ITERATIONS = 1000
DEFAULT_SEED = 20260529
DEFAULT_EXTEND_BACKWARD_DAYS = 14
DEFAULT_DOWNLOAD_BUDGET_BYTES = 3 * 1024**3  # 3 GB
DEFAULT_MAX_FILES = 8

REPLICA_CMDS_BUCKET = "hl-mainnet-node-data"
REPLICA_CMDS_PREFIX = "replica_cmds"

# ---------------------------------------------------------------------------
# Allowed / forbidden statuses
# ---------------------------------------------------------------------------

ALLOWED_STATUSES = frozenset({
    "HIP3_FLX_ORACLE_BIAS_PHASE_MINUS2_READY",
    "HIP3_FLX_ORACLE_BIAS_PLAN_READY",
    "HIP3_FLX_ORACLE_BIAS_DRY_RUN_READY",
    "HIP3_FLX_ORACLE_BIAS_REFERENCE_ACTIVE_FLX_STALE_CONFIRMED",
    "HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED",
    "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET",
    "HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK",
    "HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE",
    "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET",
    "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_REFERENCE",
    "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_ALIGNMENT",
    "HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE",
    "HIP3_FLX_ORACLE_BIAS_BLOCKED_REPLICA_CMDS_ACCESS",
    "HIP3_FLX_ORACLE_BIAS_BLOCKED_COST_OR_SIZE_CAP",
    "HIP3_FLX_ORACLE_BIAS_BLOCKED_SCHEMA_UNRECOGNIZED",
    "HIP3_FLX_ORACLE_BIAS_ERROR_INVALID_OUTPUT",
    # Frequency scan statuses (diagnostic-only, never authorize Phase 0)
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_READY",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_FOUND",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_UNDERPOWERED",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_ABSENT",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_BLOCKED_COST_OR_SIZE_CAP",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_BLOCKED_S3_ACCESS",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_ERROR_INVALID_OUTPUT",
    # Positive-control validation statuses
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_PASSED",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_FAILED",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_CONTROL_BLOCK_NOT_FOUND",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_ABSENT_WITH_CONTROL",
    "HIP3_FLX_ORACLE_FREQUENCY_SCAN_RESULT_INVALIDATED_CONTROL_FAILED",
})

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "CANDIDATE", "CANDIDATE_FOR_LIVE",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY",
    "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
    "EDGE_CONFIRMED", "PROFITABLE", "ALPHA_FOUND",
    "READY_FOR_PHASE_0",
})

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OracleUpdate:
    ts_ns: int
    source_file: str
    dex: str
    symbol: str
    px: float
    raw_key: str | None
    block_height: int | None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OracleAlignmentRow:
    symbol: str
    target_dex: str
    reference_dex: str
    reference_ts_ns: int
    reference_px: float
    target_last_ts_ns: int
    target_last_px: float
    stale_age_seconds: float
    signed_residual_bps: float
    reference_px_at_or_after_target_update: float | None
    reference_return_since_target_update_bps: float | None
    stale_gap_persisted_across_funding_clock: bool


@dataclass(frozen=True)
class BiasDecision:
    symbol: str
    target_dex: str
    reference_dex: str
    status: str
    aligned_observations: int
    target_update_count: int
    reference_update_count: int
    distinct_target_anchor_count: int
    mean_signed_residual_bps: float | None
    median_signed_residual_bps: float | None
    p05_signed_residual_bps: float | None
    p95_signed_residual_bps: float | None
    positive_residual_share: float | None
    dominant_sign_share: float | None
    bootstrap_mean_ci_low_bps: float | None
    bootstrap_mean_ci_high_bps: float | None
    stale_age_p50_seconds: float | None
    stale_age_p90_seconds: float | None
    stale_age_p99_seconds: float | None
    lag_mechanism_correlation: float | None
    funding_clock_persistence_share: float | None
    naive_bias_gate_passed: bool
    lag_mechanism_gate_passed: bool
    funding_clock_gate_passed: bool
    interpretation: str


# ---------------------------------------------------------------------------
# Decoder: extract perpDeploy.setOracle.oraclePxs from replica_cmds records
# ---------------------------------------------------------------------------

def _decode_lz4_json_records(data: bytes):
    """Yield (record_index, parsed_dict, raw_line_bytes) from LZ4-decompressed data."""
    if _lz4_frame is None:
        return
    try:
        decompressed = _lz4_frame.decompress(data)
    except Exception:
        return
    lines = decompressed.split(b"\n")
    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = _loads_json(line)
            if isinstance(obj, dict):
                yield (idx, obj, line)
        except Exception:
            continue


def _extract_oracle_payloads_from_record(record: dict) -> List[dict]:
    """Extract perpDeploy.setOracle.oraclePxs payloads from a decoded record.

    Handles the validated structure:
      obj -> abci_block -> signed_action_bundles[i] -> [sig, {signed_actions}]
        -> signed_actions[j] -> {action: {type: "perpDeploy", setOracle: {...}}}

    Also handles the multiSig envelope structure observed in practice:
      signed_actions[j] -> {action: {type: "multiSig", payload: {action: {type: "perpDeploy", setOracle: {...}}}}}

    Also handles simpler top-level formats as fallback.

    Returns list of dicts with keys: dex, symbol, px, ts_ns, block, source_key
    """
    results = []

    def _process_action(action: dict, source_key: str, ts_ns: int, block: int):
        if not isinstance(action, dict):
            return
        action_type = str(action.get("type", action.get("actionType", "")))
        if action_type != "perpDeploy":
            # Fallback: check if setOracle is directly present
            set_oracle = action.get("setOracle")
            if not isinstance(set_oracle, dict):
                return
        else:
            set_oracle = action.get("setOracle", {})
        if not isinstance(set_oracle, dict):
            return
        oracle_pxs = set_oracle.get("oraclePxs", [])
        if not isinstance(oracle_pxs, list):
            return
        for entry in oracle_pxs:
            coin = ""
            px = None
            if isinstance(entry, list) and len(entry) >= 2:
                coin = str(entry[0])
                px = _safe_float(entry[1])
            elif isinstance(entry, dict):
                coin = entry.get("coin", entry.get("name", entry.get("symbol", "")))
                px = _safe_float(entry.get("px", entry.get("price", entry.get("oraclePx"))))
            else:
                continue
            if not coin:
                continue
            dex = ""
            symbol = ""
            if ":" in str(coin):
                parts = str(coin).split(":", 1)
                dex = parts[0].lower().strip()
                symbol = parts[1].upper().strip()
            else:
                symbol = str(coin).upper().strip()
            results.append({
                "dex": dex,
                "symbol": symbol,
                "px": px,
                "ts_ns": ts_ns,
                "block": block,
                "source_key": source_key,
            })

    def _process_action_with_payload_fallback(action: dict, source_key: str, ts_ns: int, block: int):
        """Process action, and if it has a payload.action, process that too."""
        _process_action(action, source_key, ts_ns, block)
        # Handle multiSig envelope: action -> payload -> action -> setOracle
        if isinstance(action, dict):
            payload = action.get("payload")
            if isinstance(payload, dict):
                inner_action = payload.get("action")
                if isinstance(inner_action, dict):
                    _process_action(inner_action, source_key, ts_ns, block)

    # Path 1: ABCI block structure (primary validated path)
    abci = record.get("abci_block", {})
    if isinstance(abci, dict):
        bundles = abci.get("signed_action_bundles", [])
        if isinstance(bundles, list):
            ts_ns = _safe_int(abci.get("timestamp", abci.get("ts", 0))) or 0
            block = _safe_int(abci.get("block", abci.get("blockNumber", 0))) or 0
            for b in bundles:
                if not isinstance(b, list) or len(b) < 2:
                    continue
                action_data = b[1]
                if not isinstance(action_data, dict):
                    continue
                signed_actions = action_data.get("signed_actions", [])
                if not isinstance(signed_actions, list):
                    continue
                for sa in signed_actions:
                    if not isinstance(sa, dict):
                        continue
                    action = sa.get("action", {})
                    _process_action_with_payload_fallback(action, abci.get("source_key", ""), ts_ns, block)
            if results:
                return results

    # Path 2: top-level action
    action = record.get("action", {})
    if isinstance(action, dict):
        ts_ns = _safe_int(record.get("timestamp", record.get("ts", 0))) or 0
        block = _safe_int(record.get("block", record.get("blockNumber", 0))) or 0
        _process_action_with_payload_fallback(action, record.get("source_key", ""), ts_ns, block)

    # Path 3: top-level perpDeploy
    if not results:
        action_type = str(record.get("type", record.get("actionType", "")))
        if action_type == "perpDeploy":
            ts_ns = _safe_int(record.get("timestamp", record.get("ts", 0))) or 0
            block = _safe_int(record.get("block", record.get("blockNumber", 0))) or 0
            _process_action(record, record.get("source_key", ""), ts_ns, block)

    return results


# ---------------------------------------------------------------------------
# Frequency scan helpers
# ---------------------------------------------------------------------------

# Target DEX/symbol pairs to watch during frequency scan
FREQUENCY_SCAN_TARGET_DEX = "flx"
FREQUENCY_SCAN_TARGET_SYMBOLS = frozenset({"TSLA", "NVDA", "AAPL", "MSFT"})

# Reference DEXes for context during frequency scan
FREQUENCY_SCAN_REFERENCE_DEXES = frozenset({"cash", "km", "xyz", "para"})


def _compute_frequency_scan_status(
    flx_target_update_counts: dict[str, int],
    reference_update_counts: dict[str, int],
    decoded_file_count: int,
    total_oracle_updates: int,
    min_target_updates: int,
    selected_compressed_bytes: int,
) -> tuple[str, dict]:
    """Compute frequency scan status and projection metadata.

    Returns (status, projection_dict).
    """
    total_flx_target = sum(flx_target_update_counts.values())
    total_ref = sum(reference_update_counts.values())

    projection = {
        "updates_per_compressed_gb": {},
        "projected_updates_for_4_date_8_file_run": {},
        "projected_files_needed_for_30_target_updates": {},
        "projected_bytes_needed_for_30_target_updates": {},
    }

    if selected_compressed_bytes > 0:
        selected_gb = selected_compressed_bytes / (1024 ** 3)
        for key, count in flx_target_update_counts.items():
            rate = count / selected_gb if selected_gb > 0 else 0
            projection["updates_per_compressed_gb"][key] = round(rate, 4)
            # Project over 4 dates x 8 files = 32 files (4x current if 1 file)
            files_factor = 32.0  # 4 dates * 8 files
            projected = round(count * files_factor, 0)
            projection["projected_updates_for_4_date_8_file_run"][key] = int(projected)
            # Bytes needed for 30 updates
            if count > 0:
                bytes_per_update = selected_compressed_bytes / count
                bytes_for_30 = int(bytes_per_update * 30)
                projection["projected_bytes_needed_for_30_target_updates"][key] = bytes_for_30
                files_for_30 = 30 / count
                projection["projected_files_needed_for_30_target_updates"][key] = round(files_for_30, 1)

    # Determine status
    if decoded_file_count == 0 and total_oracle_updates == 0:
        return "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_ABSENT", projection

    if total_flx_target == 0:
        return "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_ABSENT", projection

    # Check if underpowered (any non-zero target projected below min_target_updates)
    underpowered = False
    for key, count in flx_target_update_counts.items():
        if count == 0:
            continue
        projected = projection.get("projected_updates_for_4_date_8_file_run", {}).get(key, 0)
        if projected < min_target_updates:
            underpowered = True
            break

    # If total target updates exist but are very sparse
    if total_flx_target > 0 and underpowered:
        return "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_UNDERPOWERED", projection

    if total_flx_target > 0:
        return "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_FOUND", projection

    return "HIP3_FLX_ORACLE_FREQUENCY_SCAN_TARGET_ABSENT", projection


# ---------------------------------------------------------------------------
# Symbol / dex normalization
# ---------------------------------------------------------------------------

def normalize_dex_symbol(raw_key: str) -> Tuple[str, str]:
    """Parse 'dex:SYMBOL' into (dex, symbol).

    Examples:
        cash:TSLA -> ('cash', 'TSLA')
        flx:NVDA -> ('flx', 'NVDA')
        km -> ('km', '')
    """
    raw_key = raw_key.strip()
    if ":" in raw_key:
        parts = raw_key.split(":", 1)
        return parts[0].lower().strip(), parts[1].upper().strip()
    return raw_key.lower().strip(), ""


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def compute_alignment_rows(
    target_updates: List[OracleUpdate],
    reference_updates: List[OracleUpdate],
    symbol: str,
    target_dex: str,
    reference_dex: str,
    funding_interval_seconds: int,
) -> List[OracleAlignmentRow]:
    """Align reference oracle updates against last-known target oracle price.

    At each reference update timestamp t_ref:
      - find last target update with t_flx <= t_ref
      - compute stale_age_seconds, signed_residual_bps, etc.
    """
    rows = []
    target_updates_sorted = sorted(target_updates, key=lambda u: u.ts_ns)
    reference_updates_sorted = sorted(reference_updates, key=lambda u: u.ts_ns)

    if not target_updates_sorted or not reference_updates_sorted:
        return rows

    target_idx = 0
    for ref in reference_updates_sorted:
        # Advance target_idx to last target update at or before reference timestamp
        while target_idx < len(target_updates_sorted) - 1 and \
                target_updates_sorted[target_idx + 1].ts_ns <= ref.ts_ns:
            target_idx += 1

        # Check if the current target update is at or before the reference
        if target_updates_sorted[target_idx].ts_ns > ref.ts_ns:
            if target_idx > 0:
                target_idx -= 1
            else:
                continue  # No target update before this reference

        tgt = target_updates_sorted[target_idx]
        stale_age_ns = ref.ts_ns - tgt.ts_ns
        stale_age_seconds = stale_age_ns / 1e9
        if stale_age_seconds < 0:
            continue

        if tgt.px <= 0 or ref.px <= 0:
            continue

        signed_residual_bps = ((tgt.px / ref.px) - 1.0) * 10000.0

        # Reference return since target update: look up reference price at or after target update
        reference_px_at_or_after_target = None
        reference_return_since_target_update_bps = None
        for ref_after in reference_updates_sorted:
            if ref_after.ts_ns >= tgt.ts_ns:
                reference_px_at_or_after_target = ref_after.px
                break

        if reference_px_at_or_after_target is not None and reference_px_at_or_after_target > 0:
            reference_return_since_target_update_bps = \
                ((reference_px_at_or_after_target / ref.px) - 1.0) * 10000.0

        stale_gap_persisted = stale_age_seconds >= funding_interval_seconds

        rows.append(OracleAlignmentRow(
            symbol=symbol,
            target_dex=target_dex,
            reference_dex=reference_dex,
            reference_ts_ns=ref.ts_ns,
            reference_px=ref.px,
            target_last_ts_ns=tgt.ts_ns,
            target_last_px=tgt.px,
            stale_age_seconds=stale_age_seconds,
            signed_residual_bps=signed_residual_bps,
            reference_px_at_or_after_target_update=reference_px_at_or_after_target,
            reference_return_since_target_update_bps=reference_return_since_target_update_bps,
            stale_gap_persisted_across_funding_clock=stale_gap_persisted,
        ))

    return rows


# ---------------------------------------------------------------------------
# Decision logic / gates
# ---------------------------------------------------------------------------

def _bootstrap_ci(values: List[float], iterations: int, seed: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Compute bootstrap confidence interval for the mean."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low_idx = int(alpha / 2 * len(means))
    high_idx = int((1 - alpha / 2) * len(means))
    low_idx = max(0, min(low_idx, len(means) - 1))
    high_idx = max(0, min(high_idx, len(means) - 1))
    return (means[low_idx], means[high_idx])


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


def _pearson_correlation(x: List[float], y: List[float]) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def compute_bias_decision(
    symbol: str,
    target_dex: str,
    reference_dex: str,
    alignment_rows: List[OracleAlignmentRow],
    target_updates: List[OracleUpdate],
    reference_updates: List[OracleUpdate],
    min_target_updates: int,
    min_reference_updates: int,
    min_aligned_observations: int,
    lag_correlation_threshold: float,
    funding_interval_seconds: int,
    min_funding_clock_persistence_share: float,
    bootstrap_iterations: int,
    seed: int,
) -> BiasDecision:
    """Compute bias decision with frozen gates."""
    target_update_count = len(target_updates)
    reference_update_count = len(reference_updates)
    aligned_observations = len(alignment_rows)

    # Distinct target anchors (unique ts_ns values)
    distinct_target_anchors = len(set(u.ts_ns for u in target_updates))

    # Check underpowered gates first
    if target_update_count == 0:
        # No target baseline at all
        return BiasDecision(
            symbol=symbol, target_dex=target_dex, reference_dex=reference_dex,
            status="HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE",
            aligned_observations=0, target_update_count=0,
            reference_update_count=reference_update_count,
            distinct_target_anchor_count=0,
            mean_signed_residual_bps=None, median_signed_residual_bps=None,
            p05_signed_residual_bps=None, p95_signed_residual_bps=None,
            positive_residual_share=None, dominant_sign_share=None,
            bootstrap_mean_ci_low_bps=None, bootstrap_mean_ci_high_bps=None,
            stale_age_p50_seconds=None, stale_age_p90_seconds=None,
            stale_age_p99_seconds=None, lag_mechanism_correlation=None,
            funding_clock_persistence_share=None,
            naive_bias_gate_passed=False, lag_mechanism_gate_passed=False,
            funding_clock_gate_passed=False,
            interpretation="No flx oracle updates found; cannot measure bias.",
        )

    if target_update_count < min_target_updates:
        return BiasDecision(
            symbol=symbol, target_dex=target_dex, reference_dex=reference_dex,
            status="HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET",
            aligned_observations=aligned_observations,
            target_update_count=target_update_count,
            reference_update_count=reference_update_count,
            distinct_target_anchor_count=distinct_target_anchors,
            mean_signed_residual_bps=None, median_signed_residual_bps=None,
            p05_signed_residual_bps=None, p95_signed_residual_bps=None,
            positive_residual_share=None, dominant_sign_share=None,
            bootstrap_mean_ci_low_bps=None, bootstrap_mean_ci_high_bps=None,
            stale_age_p50_seconds=None, stale_age_p90_seconds=None,
            stale_age_p99_seconds=None, lag_mechanism_correlation=None,
            funding_clock_persistence_share=None,
            naive_bias_gate_passed=False, lag_mechanism_gate_passed=False,
            funding_clock_gate_passed=False,
            interpretation=f"Target ({target_dex}:{symbol}) has {target_update_count} updates "
                           f"(below threshold {min_target_updates}). Underpowered.",
        )

    if reference_update_count < min_reference_updates:
        return BiasDecision(
            symbol=symbol, target_dex=target_dex, reference_dex=reference_dex,
            status="HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_REFERENCE",
            aligned_observations=aligned_observations,
            target_update_count=target_update_count,
            reference_update_count=reference_update_count,
            distinct_target_anchor_count=distinct_target_anchors,
            mean_signed_residual_bps=None, median_signed_residual_bps=None,
            p05_signed_residual_bps=None, p95_signed_residual_bps=None,
            positive_residual_share=None, dominant_sign_share=None,
            bootstrap_mean_ci_low_bps=None, bootstrap_mean_ci_high_bps=None,
            stale_age_p50_seconds=None, stale_age_p90_seconds=None,
            stale_age_p99_seconds=None, lag_mechanism_correlation=None,
            funding_clock_persistence_share=None,
            naive_bias_gate_passed=False, lag_mechanism_gate_passed=False,
            funding_clock_gate_passed=False,
            interpretation=f"Reference ({reference_dex}:{symbol}) has {reference_update_count} "
                           f"updates (below threshold {min_reference_updates}). Underpowered.",
        )

    if aligned_observations < min_aligned_observations:
        return BiasDecision(
            symbol=symbol, target_dex=target_dex, reference_dex=reference_dex,
            status="HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_ALIGNMENT",
            aligned_observations=aligned_observations,
            target_update_count=target_update_count,
            reference_update_count=reference_update_count,
            distinct_target_anchor_count=distinct_target_anchors,
            mean_signed_residual_bps=None, median_signed_residual_bps=None,
            p05_signed_residual_bps=None, p95_signed_residual_bps=None,
            positive_residual_share=None, dominant_sign_share=None,
            bootstrap_mean_ci_low_bps=None, bootstrap_mean_ci_high_bps=None,
            stale_age_p50_seconds=None, stale_age_p90_seconds=None,
            stale_age_p99_seconds=None, lag_mechanism_correlation=None,
            funding_clock_persistence_share=None,
            naive_bias_gate_passed=False, lag_mechanism_gate_passed=False,
            funding_clock_gate_passed=False,
            interpretation=f"Only {aligned_observations} aligned observations "
                           f"(below threshold {min_aligned_observations}). Underpowered.",
        )

    # Compute residual statistics
    residuals = [r.signed_residual_bps for r in alignment_rows]
    mean_residual = sum(residuals) / len(residuals)
    sorted_residuals = sorted(residuals)
    median_residual = _percentile(residuals, 0.5)
    p05_residual = _percentile(residuals, 0.05)
    p95_residual = _percentile(residuals, 0.95)

    positive_count = sum(1 for r in residuals if r > 0)
    positive_share = positive_count / len(residuals)
    dominant_sign_share = max(positive_share, 1.0 - positive_share)

    # Bootstrap CI
    ci_low, ci_high = _bootstrap_ci(residuals, bootstrap_iterations, seed)

    # Stale age stats
    stale_ages = [r.stale_age_seconds for r in alignment_rows]
    stale_p50 = _percentile(stale_ages, 0.5)
    stale_p90 = _percentile(stale_ages, 0.90)
    stale_p99 = _percentile(stale_ages, 0.99)

    # Lag mechanism correlation
    lag_x = []
    lag_y = []
    for r in alignment_rows:
        if r.reference_return_since_target_update_bps is not None and \
                math.isfinite(r.reference_return_since_target_update_bps):
            lag_x.append(r.signed_residual_bps)
            lag_y.append(r.reference_return_since_target_update_bps)
    lag_corr = _pearson_correlation(lag_x, lag_y) if lag_x and lag_y else None

    # Funding-clock persistence share
    large_residuals = [r for r in alignment_rows if abs(r.signed_residual_bps) >= 10.0]
    if large_residuals:
        persisted = sum(1 for r in large_residuals if r.stale_gap_persisted_across_funding_clock)
        funding_persistence_share = persisted / len(large_residuals)
    else:
        funding_persistence_share = None

    # ---- Naive signed-bias gate ----
    naive_gate = (
        aligned_observations >= min_aligned_observations and
        target_update_count >= min_target_updates and
        reference_update_count >= min_reference_updates and
        abs(mean_residual) >= 10.0 and
        abs(median_residual) >= 5.0 and
        dominant_sign_share >= 0.65 and
        (ci_low < 0 < ci_high) is False and  # CI excludes 0
        stale_p90 >= funding_interval_seconds
    )

    # ---- Lag-mechanism gate ----
    lag_gate = (lag_corr is not None and lag_corr >= lag_correlation_threshold)

    # ---- Funding-clock persistence gate ----
    funding_gate = (
        funding_persistence_share is not None and
        funding_persistence_share >= min_funding_clock_persistence_share
    )

    # ---- Determine status ----
    if naive_gate and lag_gate and funding_gate:
        status = "HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED"
        interpretation = (
            f"Signed bias ({mean_residual:.1f} bps mean, {dominant_sign_share:.0%} dominant sign), "
            f"lag-driven (corr={lag_corr:.3f}), slow enough (funding persistence "
            f"{funding_persistence_share:.0%}). A future Phase 0 review may be worth drafting."
        )
    elif not naive_gate:
        # Check if symmetric
        if abs(mean_residual) < 5.0 and ci_low < 0 < ci_high:
            status = "HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE"
            interpretation = (
                f"Mean residual {mean_residual:.1f} bps, CI [{ci_low:.1f}, {ci_high:.1f}]. "
                f"No stable signed effect."
            )
        elif 0.45 <= positive_share <= 0.55:
            status = "HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE"
            interpretation = (
                f"Positive residual share {positive_share:.2%} is near 50%. "
                f"No stable signed effect."
            )
        else:
            # Naive gate failed for other reasons
            if lag_gate and not funding_gate:
                status = "HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK"
                interpretation = (
                    f"Lag mechanism detected (corr={lag_corr:.3f}) but funding-clock "
                    f"persistence {funding_persistence_share:.0%} below threshold "
                    f"{min_funding_clock_persistence_share:.0%}. Likely B-fast/latency-only."
                )
            elif naive_gate and not lag_gate:
                status = "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET"
                interpretation = (
                    f"Persistent signed bias ({mean_residual:.1f} bps mean) but "
                    f"lag correlation {lag_corr:.3f} below threshold "
                    f"{lag_correlation_threshold}. Likely methodology offset, not stale lag."
                )
            else:
                status = "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET"
                interpretation = (
                    f"Naive bias gate failed: mean={mean_residual:.1f} bps, "
                    f"median={median_residual:.1f} bps, dominant_sign={dominant_sign_share:.0%}, "
                    f"stale_p90={stale_p90:.0f}s. Residual may be methodology offset."
                )
    else:
        # Naive passed but lag or funding failed
        if not lag_gate:
            status = "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET"
            interpretation = (
                f"Naive bias gate passed but lag correlation {lag_corr:.3f} below "
                f"threshold {lag_correlation_threshold}. Likely methodology offset."
            )
        elif not funding_gate:
            status = "HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK"
            interpretation = (
                f"Lag mechanism detected (corr={lag_corr:.3f}) but funding-clock "
                f"persistence {funding_persistence_share:.0%} below threshold "
                f"{min_funding_clock_persistence_share:.0%}. Likely B-fast."
            )
        else:
            status = "HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED"
            interpretation = "All gates passed."

    return BiasDecision(
        symbol=symbol, target_dex=target_dex, reference_dex=reference_dex,
        status=status,
        aligned_observations=aligned_observations,
        target_update_count=target_update_count,
        reference_update_count=reference_update_count,
        distinct_target_anchor_count=distinct_target_anchors,
        mean_signed_residual_bps=round(mean_residual, 4),
        median_signed_residual_bps=round(median_residual, 4),
        p05_signed_residual_bps=round(p05_residual, 4),
        p95_signed_residual_bps=round(p95_residual, 4),
        positive_residual_share=round(positive_share, 4),
        dominant_sign_share=round(dominant_sign_share, 4),
        bootstrap_mean_ci_low_bps=round(ci_low, 4),
        bootstrap_mean_ci_high_bps=round(ci_high, 4),
        stale_age_p50_seconds=round(stale_p50, 2),
        stale_age_p90_seconds=round(stale_p90, 2),
        stale_age_p99_seconds=round(stale_p99, 2),
        lag_mechanism_correlation=round(lag_corr, 4) if lag_corr is not None else None,
        funding_clock_persistence_share=round(funding_persistence_share, 4) if funding_persistence_share is not None else None,
        naive_bias_gate_passed=naive_gate,
        lag_mechanism_gate_passed=lag_gate,
        funding_clock_gate_passed=funding_gate,
        interpretation=interpretation,
    )
