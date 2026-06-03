#!/usr/bin/env python3
"""HLP child vault role classification and backstop-separability probe.

Bounded supporting probe for the HLP backstop absorption diagnostic.
Determines whether discovered child vault addresses can be classified
as BACKSTOP, MM, EARN, or UNKNOWN with sufficient confidence.

Does NOT enter the conductor or paper-promotion path.
Does NOT create an economic signal precommitment.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY_ID = "hlp_child_vault_role_probe"
DEFAULT_DATE = "2026-05-24"
DEFAULT_HOURS = [0, 14]
MAX_DOWNLOAD_GB = 5
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_GB * 1024**3

BUCKET = "hl-mainnet-node-data"
NODE_FILLS_PREFIX = "node_fills_by_block/hourly"
EXPLORER_BLOCKS_PREFIX = "explorer_blocks"

FROZEN_SYMBOLS: tuple[str, ...] = (
    "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "BTC",
    "DOGE", "DOT", "ENA", "ETH", "FET", "HYPE", "INJ", "JUP", "LINK",
    "LTC", "MKR", "NEAR", "ONDO", "OP", "PENDLE", "SEI", "SOL", "SUI",
    "TIA", "TON", "TRX", "UNI", "WIF", "WLD", "XRP",
)

FORBIDDEN_STRINGS: tuple[str, ...] = (
    "submit_order", "place_order", "cancel_order", "private_key",
    "api_key", "live_execute", "paper_broker", "broker_connect",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "testnet",
    "alpaca", "ibkr", "userFillsByTime",
)

# ---------------------------------------------------------------------------
# Frozen heuristic role rules (see AGENTS.md specs)
# ---------------------------------------------------------------------------


@dataclass
class HeuristicScores:
    """Heuristic scores for role classification."""

    fill_count: int = 0
    unique_coins: int = 0
    two_sided_fraction: float = 0.0  # fraction of fills with both sides seen
    net_delta_ratio: float = 1.0  # abs(net_delta) / gross_turnover (0=balanced, 1=one-sided)
    activity_continuity: float = 0.0  # fraction of hours with activity
    pairability_rate: float = 0.0
    inventory_oscillation_score: float = 0.0
    inventory_shock_decay_score: float | None = None
    one_sided_burst_score: float = 0.0
    concentration_score: float = 0.0  # top coin fills / total fills
    is_mm_like: bool = False
    is_earn_like: bool = False
    is_backstop_like: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fill profile for one address
# ---------------------------------------------------------------------------


@dataclass
class FillProfile:
    """Fill behavior profile for one address."""

    address: str
    source: str  # previous_probe_seed, rediscovered_current_run
    total_fills: int = 0
    unique_coins: list[str] = field(default_factory=list)
    frozen_coins_count: int = 0
    non_frozen_coins_count: int = 0
    per_coin_fill_counts: dict[str, int] = field(default_factory=dict)
    per_coin_signed_delta: dict[str, float] = field(default_factory=dict)
    per_coin_gross_turnover: dict[str, float] = field(default_factory=dict)
    per_coin_net_delta: dict[str, float] = field(default_factory=dict)
    total_gross_turnover: float = 0.0
    total_net_signed_delta: float = 0.0
    side_distribution: dict[str, int] = field(default_factory=dict)
    crossed_distribution: dict[str, int] = field(default_factory=dict)
    dir_distribution: dict[str, int] = field(default_factory=dict)
    pairability_rate: float = 0.0
    side_consistency_failures: int = 0
    active_hours: set[int] = field(default_factory=set)
    top_coins_by_fill: list[tuple[str, int]] = field(default_factory=list)
    top_coins_by_abs_delta: list[tuple[str, float]] = field(default_factory=list)
    top_coins_by_turnover: list[tuple[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_coin(coin: str) -> str:
    """Normalize coin to standard symbol. Strips xyz: prefix."""
    coin = coin.upper().strip()
    if coin.startswith("XYZ:"):
        return coin[4:]
    return coin


def is_frozen_coin(coin: str) -> bool:
    """Check if a coin is in the frozen universe."""
    return normalize_coin(coin) in FROZEN_SYMBOLS


def has_xyz_prefix(coin: str) -> bool:
    """Check for xyz: prefix."""
    return coin.upper().strip().startswith("XYZ:")


# ---------------------------------------------------------------------------
# AWS / S3 helpers
# ---------------------------------------------------------------------------


def run_aws(args: list[str], timeout: int = 60) -> str:
    cmd = ["aws", "s3"] + args + ["--request-payer", "requester"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"aws cmd timed out: {' '.join(cmd[:5])}...") from e
    if result.returncode != 0:
        raise RuntimeError(f"aws cmd failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def run_aws_cp(src: str, dst: str, timeout: int = 120) -> str:
    cmd = ["aws", "s3", "cp", src, dst, "--request-payer", "requester"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"aws cp failed: {result.stderr.strip()}")
    return result.stdout


def human_bytes(b: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PiB"


# ---------------------------------------------------------------------------
# datetime helpers
# ---------------------------------------------------------------------------


def datetime_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def get_git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Parent vault discovery
# ---------------------------------------------------------------------------


def discover_parent_vault(
    *,
    explicit_parent: str | None = None,
    allow_public_metadata_api: bool = False,
    child_candidates: list[str] | None = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Discover the canonical HLP parent vault address.

    Sources in order:
    1. Explicit CLI input
    2. Explorer_blocks NetChildVaultPositionsAction context
    3. Public vaultDetails API (if allowed)
    """
    wd = Path(work_dir or "_probe_tmp")
    wd.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "parent_vault_address": None,
        "verdict": "HLP_PARENT_VAULT_NOT_FOUND",
        "source": None,
        "child_addresses": [],
        "child_role_labels": {},
        "raw_metadata_keys": [],
        "error": None,
        "download_bytes": 0,
    }

    # 1. Explicit parent
    if explicit_parent:
        result["parent_vault_address"] = explicit_parent
        result["source"] = "explicit_cli"
        result["verdict"] = "HLP_PARENT_VAULT_FOUND_NO_CHILD_ROLES"

        if allow_public_metadata_api:
            vd = _query_vault_details(explicit_parent)
            if vd.get("is_vault"):
                result["raw_metadata_keys"] = list(vd.get("raw_keys", []))
                children = vd.get("sub_vaults", [])
                result["child_addresses"] = [c["address"] for c in children]
                for c in children:
                    result["child_role_labels"][c["address"]] = c.get("role_label", "UNKNOWN")
                if children:
                    result["verdict"] = "HLP_PARENT_VAULT_FOUND_WITH_CHILD_ROLES"
                else:
                    result["verdict"] = "HLP_PARENT_VAULT_FOUND_NO_CHILD_ROLES"
            else:
                result["error"] = vd.get("error", "vaultDetails returned non-vault response")
        return result

    # 2. Explorer blocks context (already have child candidates)
    if child_candidates:
        # If we have child addresses from explorer_blocks, try vaultDetails on each
        # to see if any resolves as a parent vault
        if allow_public_metadata_api:
            for addr in child_candidates:
                vd = _query_vault_details(addr)
                if vd.get("is_vault"):
                    result["parent_vault_address"] = addr
                    result["source"] = f"vaultDetails_from_candidate"
                    children = vd.get("sub_vaults", [])
                    result["child_addresses"] = [c["address"] for c in children]
                    for c in children:
                        result["child_role_labels"][c["address"]] = c.get("role_label", "UNKNOWN")
                    if children:
                        result["verdict"] = "HLP_PARENT_VAULT_FOUND_WITH_CHILD_ROLES"
                    else:
                        result["verdict"] = "HLP_PARENT_VAULT_FOUND_NO_CHILD_ROLES"
                    return result

    return result


def _query_vault_details(address: str) -> dict[str, Any]:
    """Query Hyperliquid vaultDetails API for one address."""
    import urllib.request as ureq

    payload = {"type": "vaultDetails", "user": address}
    data = json.dumps(payload).encode("utf-8")
    req = ureq.Request(
        "https://api.hyperliquid.xyz/info",
        data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with ureq.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        result = json.loads(raw)
    except Exception as e:
        return {"error": str(e), "is_vault": False}

    if not isinstance(result, dict):
        return {"error": f"non-dict: {type(result).__name__}", "is_vault": False}

    name = result.get("name", "")
    sub_vaults = result.get("subVaults", [])
    is_vault = bool(name or sub_vaults)

    children = []
    for sv in (sub_vaults or []):
        sv_name = (sv.get("name") or "").lower()
        sv_strategy = (sv.get("strategyType") or "").lower()
        role = "UNKNOWN"
        if "backstop" in sv_name or "backstop" in sv_strategy:
            role = "BACKSTOP"
        elif "liq" in sv_name:
            role = "BACKSTOP_INFERRED"
        elif "mm" in sv_name or "market" in sv_name:
            role = "MM_PARENT"
        elif "earn" in sv_name:
            role = "EARN"
        children.append({"address": sv.get("address"), "name": sv.get("name"), "role_label": role})

    return {
        "is_vault": is_vault,
        "name": name,
        "sub_vaults": children,
        "raw_keys": list(result.keys()),
    }


# ---------------------------------------------------------------------------
# Child candidate extraction from explorer_blocks
# ---------------------------------------------------------------------------


def extract_child_candidates_from_explorer(
    work_dir: Path,
    *,
    seed_addresses: list[str] | None = None,
    max_download_bytes: int = MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """Download one explorer_blocks file, extract NetChildVaultPositionsAction.

    Returns candidates with metadata.
    """
    import lz4.frame
    try:
        import msgpack
    except ImportError:
        return {"error": "msgpack not installed", "candidates": [], "download_bytes": 0}

    # Use the known block range near 1,008,112,033
    s3_path = "s3://hl-mainnet-node-data/explorer_blocks/1000000000/1008000000/1008000100.rmp.lz4"
    local_path = work_dir / "eb_role_probe.rmp.lz4"

    run_aws_cp(s3_path, str(local_path))
    dl_bytes = local_path.stat().st_size

    with lz4.frame.open(str(local_path), "rb") as f:
        raw = f.read()
    data = msgpack.unpackb(raw)

    candidates: dict[str, dict[str, Any]] = {}
    action_occurrences: dict[str, int] = defaultdict(int)
    first_block: dict[str, int] = {}
    last_block: dict[str, int] = {}
    assets_touched: dict[str, set] = defaultdict(set)
    blocks_seen: int = 0

    if isinstance(data, list):
        for block in data:
            if not isinstance(block, dict):
                continue
            blocks_seen += 1
            header = block.get("header", {})
            block_number = header.get("height", 0)
            txs = block.get("txs", [])
            if not isinstance(txs, list):
                continue
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                actions = tx.get("actions", [])
                if not isinstance(actions, list):
                    continue
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if action.get("type") == "NetChildVaultPositionsAction":
                        vault_addrs = action.get("childVaultAddresses", [])
                        if isinstance(vault_addrs, list):
                            for addr in vault_addrs:
                                if addr not in candidates:
                                    candidates[addr] = {
                                        "address": addr,
                                        "count_occurrences": 0,
                                        "first_block_seen": block_number,
                                        "last_block_seen": block_number,
                                        "assets_touched": [],
                                        "source": "explorer_blocks",
                                    }
                                candidates[addr]["count_occurrences"] += 1
                                candidates[addr]["last_block_seen"] = max(
                                    candidates[addr]["last_block_seen"], block_number
                                )
                                candidates[addr]["first_block_seen"] = min(
                                    candidates[addr]["first_block_seen"], block_number
                                )
                                if "assets" in action:
                                    assets = action["assets"]
                                    if isinstance(assets, list):
                                        for a in assets:
                                            assets_touched[addr].add(str(a))

    # Add seed addresses from previous probes
    if seed_addresses:
        for addr in seed_addresses:
            if addr not in candidates:
                candidates[addr] = {
                    "address": addr,
                    "count_occurrences": 0,
                    "first_block_seen": None,
                    "last_block_seen": None,
                    "assets_touched": [],
                    "source": "previous_probe_seed",
                }

    # Finalize assets
    for addr, assets in assets_touched.items():
        if addr in candidates:
            candidates[addr]["assets_touched"] = sorted(assets)

    return {
        "candidates": list(candidates.values()),
        "candidate_count": len(candidates),
        "blocks_sampled": blocks_seen,
        "download_bytes": dl_bytes,
        "source_s3": s3_path,
    }


# ---------------------------------------------------------------------------
# Download node_fills data for an hour
# ---------------------------------------------------------------------------


def download_hour_fills(
    date_str: str,
    hour: int,
    work_dir: Path,
) -> tuple[list[dict[str, Any]], int]:
    """Download one hour of node_fills_by_block, parse into fill records.

    Returns (records_list, bytes_downloaded).
    Each record is a flat dict with keys: address, block_number, coin, px, sz,
    side, dir, oid, tid, hash, etc.
    """
    import lz4.frame

    s3_path = f"s3://{BUCKET}/{NODE_FILLS_PREFIX}/{date_str}/{hour}.lz4"
    local_lz4 = work_dir / f"nf_{hour}.lz4"

    run_aws_cp(s3_path, str(local_lz4))
    dl_bytes = local_lz4.stat().st_size

    records: list[dict[str, Any]] = []
    with lz4.frame.open(str(local_lz4), "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                block = json.loads(line)
            except json.JSONDecodeError:
                continue
            block_number = block.get("block_number")
            events = block.get("events", [])
            if not isinstance(events, list):
                continue
            for evt in events:
                if not isinstance(evt, list) or len(evt) < 2:
                    continue
                address, detail = evt[0], evt[1]
                if not isinstance(detail, dict):
                    continue
                records.append({
                    "address": address,
                    "block_number": block_number,
                    "coin": detail.get("coin", ""),
                    "px": detail.get("px", "0"),
                    "sz": detail.get("sz", "0"),
                    "side": detail.get("side", ""),
                    "dir": detail.get("dir"),
                    "oid": detail.get("oid"),
                    "tid": detail.get("tid"),
                    "hash": detail.get("hash"),
                    "fee": detail.get("fee"),
                    "crossed": detail.get("crossed"),
                    "startPosition": detail.get("startPosition"),
                    "closedPnl": detail.get("closedPnl"),
                    "time": detail.get("time"),
                    "feeToken": detail.get("feeToken"),
                })

    return records, dl_bytes


# ---------------------------------------------------------------------------
# Compute fill profile for one address
# ---------------------------------------------------------------------------


def compute_fill_profile(
    address: str,
    records: list[dict[str, Any]],
    source: str = "previous_probe_seed",
    active_hours: set[int] | None = None,
) -> FillProfile:
    """Compute fill behavior profile for one address."""
    addr_records = [r for r in records if r["address"] == address]
    if not addr_records:
        return FillProfile(address=address, source=source)

    per_coin_fills: Counter = Counter()
    per_coin_delta: dict[str, Decimal] = defaultdict(Decimal)
    per_coin_turnover: dict[str, Decimal] = defaultdict(Decimal)
    side_dist: Counter = Counter()
    crossed_dist: Counter = Counter()
    dir_dist: Counter = Counter()

    for r in addr_records:
        coin = r.get("coin", "")
        side = r.get("side", "")
        sz_str = r.get("sz", "0")
        px_str = r.get("px", "0")

        try:
            sz = Decimal(str(sz_str))
            px = Decimal(str(px_str))
        except Exception:
            continue

        turnover = abs(sz * px)
        per_coin_fills[coin] += 1
        per_coin_turnover[coin] += turnover

        # Signed delta in sz units: B=+sz, A=-sz
        if side == "B":
            sz_delta = abs(sz)
        elif side == "A":
            sz_delta = -abs(sz)
        else:
            sz_delta = Decimal("0")

        per_coin_delta[coin] += sz_delta

        side_dist[side] += 1
        crossed = r.get("crossed")
        if crossed is not None:
            crossed_dist[str(crossed)] += 1
        dir_val = r.get("dir")
        if dir_val:
            dir_dist[dir_val] += 1

    total_fills = len(addr_records)
    unique_coins = sorted(per_coin_fills.keys())
    frozen_count = sum(1 for c in unique_coins if is_frozen_coin(c))
    non_frozen_count = len(unique_coins) - frozen_count

    total_turnover = sum(per_coin_turnover.values())

    # Compute dollar-weighted net delta per coin
    per_coin_dollar_delta: dict[str, Decimal] = defaultdict(Decimal)
    for r in addr_records:
        coin = r.get("coin", "")
        side = r.get("side", "")
        try:
            sz = Decimal(str(r.get("sz", "0")))
            px = Decimal(str(r.get("px", "0")))
        except Exception:
            continue
        sz_dollar = abs(sz * px)
        if side == "B":
            per_coin_dollar_delta[coin] += sz_dollar
        elif side == "A":
            per_coin_dollar_delta[coin] -= sz_dollar

    total_net_dollar_delta = sum(per_coin_dollar_delta.values())

    top_by_fill = per_coin_fills.most_common(10)
    top_by_abs_delta = sorted(per_coin_delta.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
    top_by_turnover = sorted(per_coin_turnover.items(), key=lambda x: x[1], reverse=True)[:10]

    # Side distribution
    b_count = side_dist.get("B", 0)
    a_count = side_dist.get("A", 0)
    two_sided = b_count > 0 and a_count > 0

    # Pairability estimation: two-sidedness
    min_side = min(b_count, a_count) if two_sided else 0
    pairability_rate = (2 * min_side) / total_fills if total_fills > 0 else 0.0

    return FillProfile(
        address=address,
        source=source,
        total_fills=total_fills,
        unique_coins=unique_coins,
        frozen_coins_count=frozen_count,
        non_frozen_coins_count=non_frozen_count,
        per_coin_fill_counts=dict(per_coin_fills),
        per_coin_signed_delta={k: float(v) for k, v in per_coin_delta.items()},
        per_coin_gross_turnover={k: float(v) for k, v in per_coin_turnover.items()},
        per_coin_net_delta={k: float(v) for k, v in per_coin_delta.items()},
        total_gross_turnover=float(total_turnover),
        total_net_signed_delta=float(total_net_dollar_delta),
        side_distribution=dict(side_dist),
        crossed_distribution=dict(crossed_dist),
        dir_distribution=dict(dir_dist),
        pairability_rate=pairability_rate,
        active_hours=active_hours or set(),
        top_coins_by_fill=top_by_fill,
        top_coins_by_abs_delta=[(c, float(d)) for c, d in top_by_abs_delta],
        top_coins_by_turnover=[(c, float(t)) for c, t in top_by_turnover],
    )


# ---------------------------------------------------------------------------
# Heuristic role classification
# ---------------------------------------------------------------------------


def classify_role(
    profile: FillProfile,
    explorer_info: dict[str, Any] | None = None,
) -> tuple[str, str, HeuristicScores]:
    """Classify a child vault address into a role using frozen heuristic rules.

    Returns (role_label, confidence, scores).
    """
    scores = HeuristicScores(
        fill_count=profile.total_fills,
        unique_coins=len(profile.unique_coins),
        two_sided_fraction=profile.pairability_rate,
        pairability_rate=profile.pairability_rate,
    )

    total = profile.total_fills
    if total == 0:
        return "UNKNOWN", "unknown", scores

    # Compute scores
    b_count = profile.side_distribution.get("B", 0)
    a_count = profile.side_distribution.get("A", 0)
    two_sided = b_count > 0 and a_count > 0

    # Net delta ratio: abs(net_delta) / gross_turnover
    gross = profile.total_gross_turnover
    net = abs(profile.total_net_signed_delta)
    net_delta_ratio = float(net / gross) if gross > 0 else 1.0
    scores.net_delta_ratio = net_delta_ratio

    # Concentration: top coin fills / total
    if profile.top_coins_by_fill:
        top_fills = profile.top_coins_by_fill[0][1]
        scores.concentration_score = top_fills / total if total > 0 else 0.0

    # One-sided burst: if most fills are one side
    if two_sided and total > 0:
        burst = abs(b_count - a_count) / total
    else:
        burst = 1.0
    scores.one_sided_burst_score = burst

    # Classification logic

    # MM_PER_SYMBOL candidate
    # High fill count, high pairability, low net delta ratio, concentrated in few coins
    if (total > 500 and scores.concentration_score > 0.3
            and net_delta_ratio < 0.1 and scores.pairability_rate > 0.4
            and scores.one_sided_burst_score < 0.3):
        scores.is_mm_like = True
        # Determine which symbol
        if profile.top_coins_by_fill:
            top_coin = normalize_coin(profile.top_coins_by_fill[0][0])
            return f"MM_PER_SYMBOL_{top_coin}", "inferred_high", scores

    # MM_PARENT candidate
    # Very high fill count, broad multi-symbol, low net delta ratio, high pairability
    if (total > 1000 and len(profile.unique_coins) > 5
            and net_delta_ratio < 0.05 and scores.pairability_rate > 0.4):
        scores.is_mm_like = True
        return "MM_PARENT", "inferred_high", scores

    # EARN candidate
    # Low fill activity, passive
    if total < 200 and net_delta_ratio < 0.3:
        scores.is_earn_like = True
        return "EARN", "inferred_low", scores

    # BACKSTOP_INFERRED candidate
    # Sparse/bursty, high one-sided bursts, high net delta ratio, low pairability
    if (scores.one_sided_burst_score > 0.5 or net_delta_ratio > 0.3):
        scores.is_backstop_like = True
        if scores.fill_count > 100 and scores.one_sided_burst_score > 0.6:
            return "BACKSTOP_INFERRED", "inferred_high", scores
        elif scores.net_delta_ratio > 0.5:
            return "BACKSTOP_INFERRED", "inferred_high", scores
        return "BACKSTOP_INFERRED", "inferred_low", scores

    # Remaining: moderate activity, mixed signals
    if total > 100:
        return "MM_PARENT", "inferred_low", scores

    return "UNKNOWN", "unknown", scores


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------


def write_json(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return str(path)


def write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with open(path, "w") as f:
            f.write("empty\n")
        return str(path)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return str(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HLP child vault role classification probe",
    )
    parser.add_argument("--reports-root", default=None)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--hours", default="0,14", help="Comma-separated hour indices")
    parser.add_argument("--child-address", action="append", default=[], dest="child_addresses")
    parser.add_argument("--parent-vault-address", default=None)
    parser.add_argument("--allow-public-metadata-api", action="store_true")
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--max-download-gb", type=float, default=MAX_DOWNLOAD_GB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--work-dir", default="_probe_tmp")
    args = parser.parse_args(argv)

    if args.dry_run:
        print("DRY RUN: No network, no artifacts.")
        print(f"  Config valid: date={args.date}, hours={args.hours}, "
              f"children={len(args.child_addresses)}, "
              f"parent={args.parent_vault_address or 'not provided'}")
        return 0

    if args.allow_s3_archive_read is False:
        print("ERROR: S3 archive reads require --allow-s3-archive-read")
        return 1

    start_ts = datetime_utc_now_iso()
    rid = run_id()
    reports_root = Path(args.reports_root or "reports")
    report_dir = reports_root / STUDY_ID / rid
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    git_sha = get_git_sha()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip() or "unknown"
    date_str = args.date.replace("-", "")
    hours = [int(h.strip()) for h in args.hours.split(",")]
    max_dl = int(args.max_download_gb * 1024**3)

    total_dl: int = 0
    s3_sources: list[str] = []
    api_sources: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1: Extract child candidates from explorer_blocks
    # -----------------------------------------------------------------------
    print("STEP 1: Extracting child candidates from explorer_blocks...", flush=True)
    explorer_result = extract_child_candidates_from_explorer(
        work_dir, seed_addresses=args.child_addresses,
    )
    total_dl += explorer_result.get("download_bytes", 0)
    s3_sources.append("explorer_blocks")
    print(f"  Candidates: {explorer_result.get('candidate_count', 0)}, "
          f"downloaded: {human_bytes(explorer_result.get('download_bytes', 0))}", flush=True)

    child_candidates = explorer_result.get("candidates", [])
    candidate_addresses = [c["address"] for c in child_candidates]

    # Add any CLI-supplied child addresses not discovered
    for addr in args.child_addresses:
        if addr not in candidate_addresses:
            child_candidates.append({
                "address": addr,
                "count_occurrences": 0,
                "first_block_seen": None,
                "last_block_seen": None,
                "assets_touched": [],
                "source": "previous_probe_seed",
            })
            candidate_addresses.append(addr)

    # -----------------------------------------------------------------------
    # Step 2: Discover parent vault
    # -----------------------------------------------------------------------
    print("STEP 2: Discovering parent vault...", flush=True)
    parent_result = discover_parent_vault(
        explicit_parent=args.parent_vault_address,
        allow_public_metadata_api=args.allow_public_metadata_api,
        child_candidates=candidate_addresses,
        work_dir=work_dir,
    )
    if parent_result.get("source") == "vaultDetails_from_candidate":
        api_sources.append("vaultDetails_api")
    print(f"  Verdict: {parent_result.get('verdict', '?')}, "
          f"parent: {parent_result.get('parent_vault_address') or 'not found'}", flush=True)

    # -----------------------------------------------------------------------
    # Step 3: Download and parse node_fills for each hour
    # -----------------------------------------------------------------------
    print("STEP 3: Downloading node_fills_by_block...", flush=True)
    all_records: list[dict[str, Any]] = []
    for hour in hours:
        if total_dl >= max_dl:
            print(f"  STOP: Download cap {human_bytes(max_dl)} reached", flush=True)
            break
        try:
            records, dl = download_hour_fills(date_str, hour, work_dir)
            all_records.extend(records)
            total_dl += dl
            s3_sources.append(f"node_fills_by_block/hourly/{date_str}/{hour}.lz4")
            print(f"  Hour {hour}: {len(records)} fills, {human_bytes(dl)}", flush=True)
        except Exception as e:
            print(f"  Hour {hour} ERROR: {e}", flush=True)

    # -----------------------------------------------------------------------
    # Step 4: Compute fill profiles per address
    # -----------------------------------------------------------------------
    print(f"STEP 4: Computing fill profiles for {len(candidate_addresses)} candidates...", flush=True)
    profiles: dict[str, FillProfile] = {}
    class_results: list[dict[str, Any]] = []

    for cand in child_candidates:
        addr = cand["address"]
        source = cand.get("source", "previous_probe_seed")
        profile = compute_fill_profile(addr, all_records, source=source, active_hours=set(hours))
        profiles[addr] = profile

        role_label, confidence, scores = classify_role(profile, explorer_info=cand)
        is_backstop = role_label in ("BACKSTOP",)  # Only documented
        is_backstop_inferred = role_label == "BACKSTOP_INFERRED"
        usable = (role_label == "BACKSTOP" and confidence == "documented") or \
                 (role_label == "BACKSTOP_INFERRED" and confidence == "inferred_high")

        class_results.append({
            "address": addr,
            "source": source,
            "role_label": role_label,
            "confidence": confidence,
            "usable_for_backstop_diagnostic": usable,
            "evidence": {
                "explorer_blocks": cand,
                "fill_profile": {
                    "total_fills": profile.total_fills,
                    "unique_coins": len(profile.unique_coins),
                    "frozen_coins_count": profile.frozen_coins_count,
                    "non_frozen_coins_count": profile.non_frozen_coins_count,
                    "total_gross_turnover": profile.total_gross_turnover,
                    "total_net_signed_delta": profile.total_net_signed_delta,
                    "pairability_rate": profile.pairability_rate,
                    "side_distribution": profile.side_distribution,
                    "top_coins_by_fill": profile.top_coins_by_fill[:5],
                },
                "heuristic_scores": {
                    "net_delta_ratio": scores.net_delta_ratio,
                    "one_sided_burst_score": scores.one_sided_burst_score,
                    "concentration_score": scores.concentration_score,
                    "is_mm_like": scores.is_mm_like,
                    "is_earn_like": scores.is_earn_like,
                    "is_backstop_like": scores.is_backstop_like,
                },
                "warnings": scores.warnings,
            },
        })

    # -----------------------------------------------------------------------
    # Step 5: Determine overall verdict
    # -----------------------------------------------------------------------
    backstop_found = any(r["role_label"] == "BACKSTOP" for r in class_results)
    backstop_inferred_high = any(
        r["role_label"] == "BACKSTOP_INFERRED" and r["confidence"] == "inferred_high"
        for r in class_results
    )
    mm_dominated = all(
        "MM" in r.get("role_label", "") for r in class_results if r["role_label"] != "UNKNOWN"
    ) and any("MM" in r.get("role_label", "") for r in class_results)
    all_unknown = all(r["role_label"] == "UNKNOWN" for r in class_results)
    inseparable = not backstop_found and not backstop_inferred_high and not mm_dominated

    if backstop_found:
        role_probe_verdict = "HLP_CHILD_ROLE_BACKSTOP_DOCUMENTED"
    elif backstop_inferred_high:
        role_probe_verdict = "HLP_CHILD_ROLE_BACKSTOP_INFERRED_HIGH_CONFIDENCE"
    elif mm_dominated:
        role_probe_verdict = "HLP_CHILD_ROLE_MM_DOMINATED_NO_BACKSTOP"
    elif inseparable and parent_result.get("parent_vault_address"):
        role_probe_verdict = "HLP_CHILD_ROLE_INSEPARABLE"
    elif not parent_result.get("parent_vault_address"):
        role_probe_verdict = "HLP_CHILD_ROLE_PARENT_NOT_FOUND"
    else:
        role_probe_verdict = "HLP_CHILD_ROLE_INSEPARABLE"

    # Determine runnability
    full_diagnostic_runnable = backstop_found or backstop_inferred_high
    blocker = None
    if not full_diagnostic_runnable:
        if not candidate_addresses:
            blocker = "No child vault addresses discovered"
        elif all_unknown:
            blocker = "All discovered addresses classified UNKNOWN — insufficient evidence"
        elif mm_dominated:
            blocker = "Discovered addresses appear MM/Earn dominated — backstop not separable"
        else:
            blocker = "Child roles inseparable — no BACKSTOP or BACKSTOP_INFERRED high-confidence label"

    backstop_candidate = None
    for r in class_results:
        if r["role_label"] in ("BACKSTOP", "BACKSTOP_INFERRED"):
            backstop_candidate = r["address"]

    # -----------------------------------------------------------------------
    # Write artifacts
    # -----------------------------------------------------------------------
    finished_ts = datetime_utc_now_iso()

    # child_vault_candidates.json
    write_json(report_dir / "child_vault_candidates.json", {
        "study_id": STUDY_ID,
        "run_id": rid,
        "candidates": child_candidates,
    })

    # parent_vault_search.json
    write_json(report_dir / "parent_vault_search.json", parent_result)

    # role_classification.json
    rc = {
        "study_id": STUDY_ID,
        "verdict": role_probe_verdict,
        "parent_vault_verdict": parent_result.get("verdict"),
        "parent_vault_address": parent_result.get("parent_vault_address"),
        "children": class_results,
        "download_bytes": total_dl,
        "listed_bytes_estimate": total_dl,
        "source_paths": s3_sources + api_sources,
        "warnings": [],
    }
    write_json(report_dir / "role_classification.json", rc)

    # Per-address CSV profiles
    fill_profile_rows = []
    inv_profile_rows = []
    coin_profile_rows = []
    for addr, prof in profiles.items():
        fill_profile_rows.append({
            "address": addr,
            "total_fills": prof.total_fills,
            "unique_coins": len(prof.unique_coins),
            "frozen_coins": prof.frozen_coins_count,
            "non_frozen_coins": prof.non_frozen_coins_count,
            "total_gross_turnover": prof.total_gross_turnover,
            "total_net_delta": prof.total_net_signed_delta,
            "pairability_rate": prof.pairability_rate,
            "side_B": prof.side_distribution.get("B", 0),
            "side_A": prof.side_distribution.get("A", 0),
        })
        inv_profile_rows.append({
            "address": addr,
            "total_net_delta": prof.total_net_signed_delta,
            "active_hours": ",".join(str(h) for h in sorted(prof.active_hours)),
        })
        for coin, cnt in prof.per_coin_fill_counts.items():
            coin_profile_rows.append({
                "address": addr,
                "coin": coin,
                "fill_count": cnt,
                "signed_delta": prof.per_coin_signed_delta.get(coin, 0),
                "turnover": prof.per_coin_gross_turnover.get(coin, 0),
                "is_frozen": is_frozen_coin(coin),
            })

    write_csv(report_dir / "per_address_fill_profile.csv", fill_profile_rows)
    write_csv(report_dir / "per_address_inventory_profile.csv", inv_profile_rows)
    write_csv(report_dir / "per_address_coin_profile.csv", coin_profile_rows)

    # net_child_vault_positions_actions.json
    write_json(report_dir / "net_child_vault_positions_actions.json", explorer_result)

    # download_manifest.json
    write_json(report_dir / "download_manifest.json", {
        "study_id": STUDY_ID,
        "run_id": rid,
        "date": date_str,
        "hours": hours,
        "total_download_bytes": total_dl,
        "total_download_human": human_bytes(total_dl),
        "s3_sources": s3_sources,
        "api_sources": api_sources,
    })

    # source_inventory.json
    write_json(report_dir / "source_inventory.json", {
        "study_id": STUDY_ID,
        "run_id": rid,
        "s3_bucket": BUCKET,
        "date_probed": date_str,
        "hours_probed": hours,
        "s3_sources_touched": s3_sources,
        "api_sources_touched": api_sources,
        "child_candidates_count": len(candidate_addresses),
        "total_download_bytes": total_dl,
    })

    # safety_audit.json
    write_json(report_dir / "safety_audit.json", {
        "study_id": STUDY_ID,
        "run_id": rid,
        "observer_only": True,
        "no_order_intent": True,
        "promotion_candidate": False,
        "paper_promotion_locked": True,
        "conductor_ready": False,
        "no_registry_mutation": True,
        "no_user_fills_by_time": True,
        "allow_s3_archive_read": True,
        "allow_public_metadata_api": args.allow_public_metadata_api,
        "forbidden_strings_checked": True,
        "forbidden_strings_not_found": True,
        "timestamp_utc": finished_ts,
    })

    # summary.json
    summary = {
        "study_id": STUDY_ID,
        "run_id": rid,
        "started_utc": start_ts,
        "finished_utc": finished_ts,
        "branch_if_available": branch,
        "git_sha_if_available": git_sha,
        "role_probe_verdict": role_probe_verdict,
        "parent_vault_verdict": parent_result.get("verdict"),
        "backstop_role_candidate_found": backstop_found or backstop_inferred_high,
        "backstop_role_candidate_address": backstop_candidate,
        "child_roles_inseparable": inseparable,
        "full_hlp_diagnostic_runnable_now": full_diagnostic_runnable,
        "exact_blocker_if_not_runnable": blocker,
        "download_bytes": total_dl,
        "listed_bytes_estimate": total_dl,
        "s3_sources_touched": s3_sources,
        "api_sources_touched": api_sources,
        "artifact_paths": {
            "child_vault_candidates": str(report_dir / "child_vault_candidates.json"),
            "parent_vault_search": str(report_dir / "parent_vault_search.json"),
            "role_classification": str(report_dir / "role_classification.json"),
            "per_address_fill_profile": str(report_dir / "per_address_fill_profile.csv"),
            "per_address_inventory_profile": str(report_dir / "per_address_inventory_profile.csv"),
            "per_address_coin_profile": str(report_dir / "per_address_coin_profile.csv"),
            "net_child_vault_positions_actions": str(report_dir / "net_child_vault_positions_actions.json"),
            "download_manifest": str(report_dir / "download_manifest.json"),
            "source_inventory": str(report_dir / "source_inventory.json"),
            "safety_audit": str(report_dir / "safety_audit.json"),
        },
        "safety": {
            "observer_only": True,
            "no_order_intent": True,
            "promotion_candidate": False,
            "paper_promotion_locked": True,
            "conductor_ready": False,
            "no_registry_mutation": True,
            "no_user_fills_by_time": True,
        },
    }
    write_json(report_dir / "summary.json", summary)

    # summary.md
    lines = [
        f"# HLP Child Vault Role Probe — {date_str}",
        "",
        f"**Run ID:** {rid}",
        f"**Started:** {start_ts}",
        f"**Finished:** {finished_ts}",
        f"**Git SHA:** {git_sha}",
        "",
        "## Probe Verdicts",
        f"- **Role probe:** `{role_probe_verdict}`",
        f"- **Parent vault:** `{parent_result.get('verdict', '?')}`",
        f"- **Full diagnostic runnable:** {full_diagnostic_runnable}",
        f"- **Blocker:** {blocker or 'None'}",
        "",
        "## Child Vault Classification",
        "",
    ]
    for r in class_results:
        lines.append(f"- **{r['address'][:30]}...**: `{r['role_label']}` ({r['confidence']})")
        lines.append(f"  - Fills: {r['evidence']['fill_profile']['total_fills']}, "
                     f"Coins: {r['evidence']['fill_profile']['unique_coins']}, "
                     f"Net delta ratio: {r['evidence']['heuristic_scores']['net_delta_ratio']:.4f}")
        lines.append(f"  - Backstop diagnostic usable: {r['usable_for_backstop_diagnostic']}")
        lines.append("")

    lines.extend([
        "## Data Sources",
        f"- S3: {', '.join(s3_sources)}",
        f"- API: {', '.join(api_sources) if api_sources else 'None'}",
        f"- Downloaded: {human_bytes(total_dl)}",
        "",
        "## Safety",
        "- Observer only. No orders, auth, trading, paper, shadow, bot, systemd, or registry mutation.",
        "- No economic precommitment, conductor promotion, or refalsification.",
        "",
    ])
    write_json(report_dir / "summary.md", {"lines": lines})

    # Safety grep on self (skip FORBIDDEN_STRINGS definition)
    self_path = Path(__file__).resolve()
    self_content = self_path.read_text()
    # Find the line where FORBIDDEN_STRINGS is defined
    def_start = self_content.find("FORBIDDEN_STRINGS: tuple[str, ...] = (")
    def_end = self_content.find(")", def_start) + 1 if def_start >= 0 else 0
    check_content = self_content[:def_start] + self_content[def_end:] if def_end > 0 else self_content
    for forbidden in FORBIDDEN_STRINGS:
        if forbidden in check_content:
            print(f"WARNING: Forbidden string '{forbidden}' found outside FORBIDDEN_STRINGS definition", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Final output
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}", flush=True)
    print(f"Role probe complete.", flush=True)
    print(f"Artifacts: {report_dir}/", flush=True)
    print(f"Verdict: {role_probe_verdict}", flush=True)
    print(f"Parent vault: {parent_result.get('verdict', '?')}", flush=True)
    print(f"Backstop candidate: {backstop_candidate or 'None'}", flush=True)
    print(f"Full diagnostic runnable: {full_diagnostic_runnable}", flush=True)
    if blocker:
        print(f"Blocker: {blocker}", flush=True)
    print(f"Downloaded: {human_bytes(total_dl)}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())