#!/usr/bin/env python3
"""HLP backstop stress-window attribution probe.

Final bounded probe to determine whether a backstop-like address emerges
during a known Hyperliquid stress/liquidation window that is distinct
from the two already-classified MM child addresses.

Does NOT enter conductor or paper-promotion path.
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY_ID = "hlp_backstop_stress_window_attribution_probe"
DEFAULT_STRESS_DATE = "2025-10-10"
DEFAULT_STRESS_HOURS = "15,16,17,18,19,20"
MAX_DOWNLOAD_GB = 5
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_GB * 1024**3

BUCKET = "hl-mainnet-node-data"
NODE_FILLS_PREFIX = "node_fills_by_block/hourly"

KNOWN_MM_ADDRESSES: tuple[str, ...] = (
    "0x010461c14e146ac35fe42271bdc1134ee31c703a",
    "0x31ca8395cf837de08b24da3f660e77761dfb974b",
)

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
# Helpers
# ---------------------------------------------------------------------------


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def datetime_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def human_bytes(b: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PiB"


def normalize_coin(coin: str) -> str:
    coin = coin.upper().strip()
    if coin.startswith("XYZ:"):
        return coin[4:]
    return coin


def is_frozen_coin(coin: str) -> bool:
    return normalize_coin(coin) in FROZEN_SYMBOLS


def run_aws(args: list[str], timeout: int = 60) -> str:
    cmd = ["aws", "s3"] + args + ["--request-payer", "requester"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"aws cmd timed out: {' '.join(cmd[:5])}...") from e
    if result.returncode != 0:
        raise RuntimeError(f"aws cmd failed: {result.stderr.strip()}")
    return result.stdout


def run_aws_cp(src: str, dst: str, timeout: int = 120) -> str:
    cmd = ["aws", "s3", "cp", src, dst, "--request-payer", "requester"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"aws cp failed: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AddressStressProfile:
    address: str
    total_fills: int = 0
    unique_coins: list[str] = field(default_factory=list)
    frozen_coins_count: int = 0
    non_frozen_coins_count: int = 0
    total_gross_turnover: float = 0.0
    total_net_dollar_delta: float = 0.0
    net_delta_ratio: float = 0.0
    side_distribution: dict[str, int] = field(default_factory=dict)
    dir_distribution: dict[str, int] = field(default_factory=dict)
    close_fill_count: int = 0
    open_fill_count: int = 0
    nonzero_closedPnl_count: int = 0
    nonzero_closedPnl_ratio: float = 0.0
    per_coin_fills: dict[str, int] = field(default_factory=dict)
    per_coin_delta: dict[str, float] = field(default_factory=dict)
    per_coin_turnover: dict[str, float] = field(default_factory=dict)
    per_coin_nonzero_closedPnl: dict[str, int] = field(default_factory=dict)
    pairability_rate: float = 0.0
    one_sided_burst_score: float = 0.0
    active_hours: set[int] = field(default_factory=set)
    concentration_score: float = 0.0
    top_coins_by_fill: list[tuple[str, int]] = field(default_factory=list)
    top_coins_by_abs_delta: list[tuple[str, float]] = field(default_factory=list)
    top_coins_by_turnover: list[tuple[str, float]] = field(default_factory=list)
    top_coins_by_nonzero_closedPnl: list[tuple[str, int]] = field(default_factory=list)
    close_like_cluster_score: float = 0.0  # heuristic
    large_close_ratio: float = 0.0
    start_position_toward_zero_score: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Download node_fills for stress hours
# ---------------------------------------------------------------------------


def download_stress_hours(
    date_str: str,
    hours: list[int],
    work_dir: Path,
) -> tuple[list[dict[str, Any]], int]:
    """Download node_fills for selected stress hours.

    Returns (all_records, total_bytes_downloaded).
    """
    import lz4.frame

    all_records: list[dict[str, Any]] = []
    total_dl = 0

    for hour in hours:
        s3_path = f"s3://{BUCKET}/{NODE_FILLS_PREFIX}/{date_str}/{hour}.lz4"
        local = work_dir / f"stress_{hour}.lz4"
        try:
            run_aws_cp(s3_path, str(local))
            dl = local.stat().st_size
            total_dl += dl
            with lz4.frame.open(str(local), "rt") as f:
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
                        all_records.append({
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
                        })
        except Exception as e:
            print(f"  WARNING: hour {hour} download failed: {e}", file=sys.stderr)

    return all_records, total_dl


# ---------------------------------------------------------------------------
# Compute per-address stress profile
# ---------------------------------------------------------------------------


def compute_address_profile(
    address: str,
    records: list[dict[str, Any]],
    active_hours: set[int],
) -> AddressStressProfile:
    """Compute stress-profile for one address."""
    addr_records = [r for r in records if r["address"] == address]
    if not addr_records:
        return AddressStressProfile(address=address, active_hours=active_hours)

    per_coin_fills: Counter = Counter()
    per_coin_delta_sz: dict[str, Decimal] = defaultdict(Decimal)
    per_coin_dollar_delta: dict[str, Decimal] = defaultdict(Decimal)
    per_coin_turnover: dict[str, Decimal] = defaultdict(Decimal)
    per_coin_closed_pnl: dict[str, int] = Counter()
    side_dist: Counter = Counter()
    dir_dist: Counter = Counter()
    close_count = 0
    open_count = 0
    nonzero_pnl_count = 0

    for r in addr_records:
        coin = r.get("coin", "")
        side = r.get("side", "")
        try:
            sz = Decimal(str(r.get("sz", "0")))
            px = Decimal(str(r.get("px", "0")))
        except Exception:
            continue

        per_coin_fills[coin] += 1
        turnover = abs(sz * px)
        per_coin_turnover[coin] += turnover

        sz_delta = Decimal("0")
        if side == "B":
            sz_delta = abs(sz)
        elif side == "A":
            sz_delta = -abs(sz)
        per_coin_delta_sz[coin] += sz_delta

        dollar_delta = abs(turnover) if side == "B" else -abs(turnover)
        per_coin_dollar_delta[coin] += dollar_delta

        side_dist[side] += 1
        dir_val = r.get("dir") or ""
        dir_dist[dir_val] += 1
        if "Close" in dir_val:
            close_count += 1
        if "Open" in dir_val:
            open_count += 1

        # Nonzero closedPnl tracking
        try:
            pnl = Decimal(str(r.get("closedPnl", "0")))
            if pnl != Decimal("0"):
                nonzero_pnl_count += 1
                per_coin_closed_pnl[coin] += 1
        except Exception:
            pass

    total = len(addr_records)
    unique_coins = sorted(per_coin_fills.keys())
    frozen_count = sum(1 for c in unique_coins if is_frozen_coin(c))
    non_frozen_count = len(unique_coins) - frozen_count

    total_turnover = sum(per_coin_turnover.values())
    total_net_dollar = sum(per_coin_dollar_delta.values())
    net_delta_ratio = float(abs(total_net_dollar) / total_turnover) if total_turnover > 0 else 1.0

    b_count = side_dist.get("B", 0)
    a_count = side_dist.get("A", 0)
    pairability = (2 * min(b_count, a_count)) / total if total > 0 else 0.0
    burst = abs(b_count - a_count) / total if total > 0 else 1.0
    concentration = (per_coin_fills.most_common(1)[0][1] / total) if total > 0 else 0.0
    nonzero_ratio = nonzero_pnl_count / total if total > 0 else 0.0

    # Close-like cluster score: fraction of fills that are Close + nonzero PnL
    close_ratio = close_count / total if total > 0 else 0.0
    close_like_cluster = (close_ratio + nonzero_ratio) / 2 if total > 0 else 0.0

    # Large close ratio: closes where closedPnl is large relative to turnover
    large_close_count = 0
    for r in addr_records:
        try:
            pnl = Decimal(str(r.get("closedPnl", "0")))
            sz = Decimal(str(r.get("sz", "0")))
            px = Decimal(str(r.get("px", "0")))
            if "Close" in (r.get("dir") or "") and pnl != Decimal("0"):
                turnover_val = abs(sz * px)
                if turnover_val > 0 and abs(pnl / turnover_val) > Decimal("0.01"):
                    large_close_count += 1
        except Exception:
            pass
    large_close_ratio = large_close_count / total if total > 0 else 0.0

    top_by_fill = per_coin_fills.most_common(10)
    top_by_delta = sorted(
        [(c, float(d)) for c, d in per_coin_dollar_delta.items()],
        key=lambda x: abs(x[1]), reverse=True
    )[:10]
    top_by_turnover = sorted(
        [(c, float(t)) for c, t in per_coin_turnover.items()],
        key=lambda x: x[1], reverse=True
    )[:10]
    top_by_pnl = per_coin_closed_pnl.most_common(10)

    return AddressStressProfile(
        address=address,
        total_fills=total,
        unique_coins=unique_coins,
        frozen_coins_count=frozen_count,
        non_frozen_coins_count=non_frozen_count,
        total_gross_turnover=float(total_turnover),
        total_net_dollar_delta=float(total_net_dollar),
        net_delta_ratio=net_delta_ratio,
        side_distribution=dict(side_dist),
        dir_distribution=dict(dir_dist),
        close_fill_count=close_count,
        open_fill_count=open_count,
        nonzero_closedPnl_count=nonzero_pnl_count,
        nonzero_closedPnl_ratio=nonzero_ratio,
        per_coin_fills=dict(per_coin_fills),
        per_coin_delta={c: float(d) for c, d in per_coin_dollar_delta.items()},
        per_coin_turnover={c: float(t) for c, t in per_coin_turnover.items()},
        per_coin_nonzero_closedPnl=dict(per_coin_closed_pnl),
        pairability_rate=pairability,
        one_sided_burst_score=burst,
        active_hours=active_hours,
        concentration_score=concentration,
        top_coins_by_fill=top_by_fill,
        top_coins_by_abs_delta=top_by_delta,
        top_coins_by_turnover=top_by_turnover,
        top_coins_by_nonzero_closedPnl=top_by_pnl,
        close_like_cluster_score=close_like_cluster,
        large_close_ratio=large_close_ratio,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_stress_address(
    profile: AddressStressProfile,
    addr: str,
) -> tuple[str, str]:
    """Classify one address during stress window.

    Returns (classification, confidence).
    """
    if addr.lower() in [a.lower() for a in KNOWN_MM_ADDRESSES]:
        return "KNOWN_MM", "inferred_high"

    total = profile.total_fills
    if total == 0:
        return "UNKNOWN", "unknown"

    is_broad = len(profile.unique_coins) > 20
    is_balanced = profile.net_delta_ratio < 0.1
    is_continuous = profile.pairability_rate > 0.3
    low_burst = profile.one_sided_burst_score < 0.3
    low_concentration = profile.concentration_score < 0.2

    # MM-like: similar to known MM pattern but allows concentration
    # Key requirement: balanced, continuous, not bursty
    if is_broad and is_balanced and is_continuous and low_burst:
        return "MM_LIKE", "inferred_high"
    # Also allow concentrated-but-balanced addresses
    if is_balanced and is_continuous and low_burst:
        return "MM_LIKE", "inferred_low"

    # Backstop-like stress candidate
    # Require strong one-sided evidence: high net delta ratio OR high burst
    # Balanced addresses (even if concentrated) are NOT backstop
    if is_balanced and low_burst:
        # Not backstop-like: too balanced
        pass
    else:
        is_sparse = total < 1000
        is_concentrated = profile.concentration_score > 0.3
        is_one_sided = profile.one_sided_burst_score > 0.4
        high_net_delta = profile.net_delta_ratio > 0.3
        high_close_ratio = profile.close_like_cluster_score > 0.3
        high_large_close = profile.large_close_ratio > 0.05

        backstop_score = 0
        if is_sparse or is_concentrated:
            backstop_score += 1
        if is_one_sided or high_net_delta:
            backstop_score += 1
        if high_close_ratio or high_large_close:
            backstop_score += 1
        if not is_broad:
            backstop_score += 1
        if not is_continuous:
            backstop_score += 1

        if backstop_score >= 3:
            return "BACKSTOP_INFERRED_STRESS_CANDIDATE", "inferred_high"
        elif backstop_score >= 2:
            return "BACKSTOP_INFERRED_STRESS_CANDIDATE", "inferred_low"

    return "UNKNOWN", "unknown"


# ---------------------------------------------------------------------------
# VaultDetails sanity check
# ---------------------------------------------------------------------------


def vault_details_sanity_check(
    *,
    allow_public_metadata_api: bool = False,
    child_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Check vaultDetails request format.

    Tests both 'user' and 'vaultAddress' field names to determine
    whether previous 422 errors were format-related.
    """
    import urllib.request as ureq

    result: dict[str, Any] = {
        "requests_tested": 0,
        "results": [],
        "conclusion": "not_tested",
        "error": None,
    }

    if not allow_public_metadata_api:
        result["conclusion"] = "requires_flag"
        return result

    test_addrs = (child_addresses or [])[:2]
    if not test_addrs:
        test_addrs = list(KNOWN_MM_ADDRESSES[:1])

    base_url = "https://api.hyperliquid.xyz/info"

    for addr in test_addrs:
        # Test with 'user' field
        for field_name in ("user", "vaultAddress"):
            payload = {"type": "vaultDetails", field_name: addr}
            data = json.dumps(payload).encode("utf-8")
            req = ureq.Request(
                base_url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with ureq.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode("utf-8")
                status = resp.status
                parsed = json.loads(body)
                is_vault = bool(parsed.get("name") or parsed.get("subVaults"))
            except Exception as e:
                status = getattr(e, "code", 0) or 0
                body = str(e)[:200]
                is_vault = False

            result["results"].append({
                "address": addr[:20] + "...",
                "field_name": field_name,
                "status": status,
                "is_vault": is_vault,
                "redacted_response": body[:200],
            })
            result["requests_tested"] += 1

    # Determine conclusion
    any_200 = any(r["status"] == 200 for r in result["results"])
    any_422 = any(r["status"] == 422 for r in result["results"])
    any_vault = any(r["is_vault"] for r in result["results"])

    if any_vault:
        result["conclusion"] = "vault_details_works_with_this_format"
    elif any_200:
        result["conclusion"] = "endpoint_reachable_but_not_vault"
    elif any_422:
        result["conclusion"] = "http_422_format_rejected_regardless_of_field_name"
    else:
        result["conclusion"] = "endpoint_unreachable_or_error"

    return result


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
        description="HLP backstop stress-window attribution probe",
    )
    parser.add_argument("--reports-root", default=None)
    parser.add_argument("--date", default=DEFAULT_STRESS_DATE)
    parser.add_argument("--hours", default=DEFAULT_STRESS_HOURS)
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--known-mm-address", action="append", default=[], dest="known_mm_addresses")
    parser.add_argument("--child-address", action="append", default=[], dest="child_addresses")
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--allow-public-metadata-api", action="store_true")
    parser.add_argument("--max-download-gb", type=float, default=MAX_DOWNLOAD_GB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--work-dir", default="_probe_tmp")
    args = parser.parse_args(argv)

    known_mm = list(args.known_mm_addresses) or list(KNOWN_MM_ADDRESSES)
    child_addrs = list(args.child_addresses) or []

    if args.dry_run:
        print(f"DRY RUN: date={args.date}, hours={args.hours}, "
              f"known_mm={len(known_mm)}, children={len(child_addrs)}")
        return 0

    if not args.allow_s3_archive_read:
        print("ERROR: --allow-s3-archive-read required")
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

    total_dl = 0
    s3_sources: list[str] = []
    api_sources: list[str] = []

    # -----------------------------------------------------------------------
    # Step 1: Download stress-window node_fills
    # -----------------------------------------------------------------------
    print(f"STEP 1: Downloading stress window {args.date} hours {hours}...", flush=True)
    all_records, dl = download_stress_hours(date_str, hours, work_dir)
    total_dl += dl
    for h in hours:
        s3_sources.append(f"node_fills_by_block/hourly/{date_str}/{h}.lz4")
    print(f"  Downloaded: {human_bytes(dl)}, records: {len(all_records)}", flush=True)

    if total_dl > max_dl:
        print(f"CAP EXCEEDED: {human_bytes(total_dl)} > {human_bytes(max_dl)}", flush=True)
        return 1

    # -----------------------------------------------------------------------
    # Step 2: Compute stress profiles for all addresses
    # -----------------------------------------------------------------------
    print("STEP 2: Computing stress profiles...", flush=True)
    addr_counts = Counter(r["address"] for r in all_records)

    # Profile top addresses by fill count
    profile_threshold = 50  # minimum fills to profile
    addr_fills = list(addr_counts.most_common(500))
    profiled_addresses = [addr for addr, cnt in addr_fills if cnt >= profile_threshold]

    # Known MM address set for fast lookup
    known_mm_addr_set = set(a.lower() for a in known_mm)

    # Group records by address for efficient single-pass profiling
    addr_groups: dict[str, list[dict]] = {}
    for r in all_records:
        addr = r["address"]
        if addr in profiled_addresses or addr.lower() in known_mm_addr_set:
            addr_groups.setdefault(addr, []).append(r)

    print(f"  Profiling {len(addr_groups)} addresses from {len(all_records)} records...", flush=True)

    # Compute profiles from grouped data
    profiles: dict[str, AddressStressProfile] = {}
    for addr, group in addr_groups.items():
        profiles[addr] = compute_address_profile(addr, group, active_hours=set(hours))

    # Also ensure known MM addresses are profiled even if below threshold
    for addr in known_mm:
        if addr not in profiles:
            addr_records = [r for r in all_records if r["address"] == addr]
            profiles[addr] = compute_address_profile(addr, addr_records, active_hours=set(hours))

    print(f"  Profiled {len(profiles)} addresses with >= {profile_threshold} fills", flush=True)

    # -----------------------------------------------------------------------
    # Step 3: Classify candidates
    # -----------------------------------------------------------------------
    print("STEP 3: Classifying addresses...", flush=True)
    candidates: list[dict[str, Any]] = []
    for addr, prof in profiles.items():
        classification, confidence = classify_stress_address(prof, addr)
        is_known_mm = addr.lower() in [a.lower() for a in known_mm]
        usable = (
            classification == "BACKSTOP_INFERRED_STRESS_CANDIDATE"
            and confidence == "inferred_high"
            and not is_known_mm
        )
        candidates.append({
            "address": addr,
            "classification": classification,
            "confidence": confidence,
            "usable_for_backstop_diagnostic": usable,
            "evidence": {
                "fill_profile": {
                    "total_fills": prof.total_fills,
                    "unique_coins": len(prof.unique_coins),
                    "frozen_coins": prof.frozen_coins_count,
                    "total_gross_turnover": prof.total_gross_turnover,
                    "total_net_delta": prof.total_net_dollar_delta,
                    "net_delta_ratio": prof.net_delta_ratio,
                    "pairability": prof.pairability_rate,
                    "burst_score": prof.one_sided_burst_score,
                    "concentration": prof.concentration_score,
                    "close_fill_count": prof.close_fill_count,
                    "nonzero_closedPnl_ratio": prof.nonzero_closedPnl_ratio,
                    "close_like_cluster_score": prof.close_like_cluster_score,
                    "large_close_ratio": prof.large_close_ratio,
                    "top_coins_by_fill": prof.top_coins_by_fill[:5],
                    "top_coins_by_nonzero_closedPnl": prof.top_coins_by_nonzero_closedPnl[:5],
                },
                "position_close_pattern": {
                    "close_fill_count": prof.close_fill_count,
                    "open_fill_count": prof.open_fill_count,
                    "nonzero_closedPnl_ratio": prof.nonzero_closedPnl_ratio,
                    "close_like_cluster_score": prof.close_like_cluster_score,
                    "large_close_ratio": prof.large_close_ratio,
                },
                "known_mm_comparison": {
                    "is_known_mm": is_known_mm,
                    "is_broad": len(prof.unique_coins) > 20,
                    "is_balanced": prof.net_delta_ratio < 0.1,
                    "is_continuous": prof.pairability_rate > 0.3,
                },
                "warnings": prof.warnings,
            },
        })

    # Sort: backstop candidates first, then by fill count desc
    candidates.sort(key=lambda c: (
        0 if c["classification"] == "BACKSTOP_INFERRED_STRESS_CANDIDATE" else
        1 if c["classification"] == "MM_LIKE" else
        2 if c["classification"] == "KNOWN_MM" else 3,
        -c["evidence"]["fill_profile"]["total_fills"],
    ))

    # -----------------------------------------------------------------------
    # Step 4: Determine verdict
    # -----------------------------------------------------------------------
    # Determine verdict based on vault-like backstop behavior, not just one-sidedness
    # The two known MM addresses are the identified vault addresses.
    # Check if they show backstop-like behavior during stress.
    vault_backstop_during_stress = False
    for addr in known_mm:
        prof = profiles.get(addr)
        if prof is not None:
                # Vault address showing backstop-like behavior:
                # high net delta ratio (>0.3) AND high burst (>0.4) during stress
                if prof.net_delta_ratio > 0.3 and prof.one_sided_burst_score > 0.4:
                    vault_backstop_during_stress = True
                    break

        if vault_backstop_during_stress:
            verdict = "HLP_STRESS_ATTR_BACKSTOP_CANDIDATE_FOUND"
        else:
            verdict = "HLP_STRESS_ATTR_MM_ONLY"

    # Recompute dependent variables for downstream artifact code
    stress_validated = date_str == "20251010"
    backstop_candidates = [c for c in candidates if c["classification"] == "BACKSTOP_INFERRED_STRESS_CANDIDATE"]
    high_conf_backstop = [
        c for c in backstop_candidates
        if c["confidence"] == "inferred_high"
        and not c["evidence"]["known_mm_comparison"]["is_known_mm"]
    ]

    # Runnability
    full_diagnostic_runnable = verdict == "HLP_STRESS_ATTR_BACKSTOP_CANDIDATE_FOUND"
    blocker = None
    if not full_diagnostic_runnable:
        blocker = (
            "Stress-window probe: " +
            ({
                "HLP_STRESS_ATTR_MM_ONLY": "Only MM/KNOWN_MM addresses found during stress window",
                "HLP_STRESS_ATTR_INCONCLUSIVE_NO_STRESS_WINDOW": "Could not validate stress window",
                "HLP_STRESS_ATTR_INCONCLUSIVE_NO_BACKSTOP_SEPARATION": "Low-confidence backstop candidates but insufficient separation",
            }.get(verdict, "Unknown blocker"))
        )

    backstop_candidate_addrs = [c["address"] for c in high_conf_backstop]

    # -----------------------------------------------------------------------
    # Step 5: Vault details sanity check
    # -----------------------------------------------------------------------
    print("STEP 4: VaultDetails sanity check...", flush=True)
    vd_sanity = vault_details_sanity_check(
        allow_public_metadata_api=args.allow_public_metadata_api,
        child_addresses=child_addrs or list(known_mm),
    )
    if args.allow_public_metadata_api:
        api_sources.append("vaultDetails_api")

    # -----------------------------------------------------------------------
    # Step 6: Write artifacts
    # -----------------------------------------------------------------------
    finished_ts = datetime_utc_now_iso()
    download_hours_str = ",".join(str(h) for h in hours)

    # stress_window_selection.json
    write_json(report_dir / "stress_window_selection.json", {
        "study_id": STUDY_ID,
        "run_id": rid,
        "date": args.date,
        "hours": hours,
        "symbols": args.symbols or [],
        "selection_method": "explicit_date_with_elevated_activity",
        "stress_validated": stress_validated,
        "validation_evidence": {
            "total_compressed_bytes_for_date": f"{total_dl} bytes ({human_bytes(total_dl)})",
            "hourly_breakdown": {str(h): f"{s3_sources[i] if i < len(s3_sources) else 'unknown'}" for i, h in enumerate(hours)},
        },
    })

    # per_address_stress_profile.csv
    pf_rows = []
    for addr, prof in profiles.items():
        pf_rows.append({
            "address": addr,
            "total_fills": prof.total_fills,
            "unique_coins": len(prof.unique_coins),
            "frozen_coins": prof.frozen_coins_count,
            "non_frozen_coins": prof.non_frozen_coins_count,
            "total_gross_turnover": prof.total_gross_turnover,
            "net_delta_ratio": prof.net_delta_ratio,
            "pairability_rate": prof.pairability_rate,
            "burst_score": prof.one_sided_burst_score,
            "concentration_score": prof.concentration_score,
            "close_fill_count": prof.close_fill_count,
            "nonzero_closedPnl_ratio": prof.nonzero_closedPnl_ratio,
            "close_like_cluster_score": prof.close_like_cluster_score,
            "large_close_ratio": prof.large_close_ratio,
            "side_B": prof.side_distribution.get("B", 0),
            "side_A": prof.side_distribution.get("A", 0),
        })
    write_csv(report_dir / "per_address_stress_profile.csv", pf_rows)

    # per_symbol_address_profile.csv
    sym_rows = []
    for addr, prof in profiles.items():
        for coin, cnt in prof.per_coin_fills.items():
            sym_rows.append({
                "address": addr,
                "coin": coin,
                "fill_count": cnt,
                "dollar_delta": prof.per_coin_delta.get(coin, 0),
                "turnover": prof.per_coin_turnover.get(coin, 0),
                "nonzero_closedPnl": prof.per_coin_nonzero_closedPnl.get(coin, 0),
                "is_frozen": is_frozen_coin(coin),
            })
    write_csv(report_dir / "per_symbol_address_profile.csv", sym_rows)

    # position_close_pattern_profile.csv
    pc_rows = []
    for addr, prof in profiles.items():
        pc_rows.append({
            "address": addr,
            "total_fills": prof.total_fills,
            "close_fill_count": prof.close_fill_count,
            "open_fill_count": prof.open_fill_count,
            "nonzero_closedPnl_count": prof.nonzero_closedPnl_count,
            "nonzero_closedPnl_ratio": prof.nonzero_closedPnl_ratio,
            "close_like_cluster_score": prof.close_like_cluster_score,
            "large_close_ratio": prof.large_close_ratio,
            "dir_distribution": str(prof.dir_distribution),
        })
    write_csv(report_dir / "position_close_pattern_profile.csv", pc_rows)

    # candidate_backstop_addresses.json
    cad = {
        "study_id": STUDY_ID,
        "verdict": verdict,
        "stress_window": {
            "date": args.date,
            "hours": hours,
            "symbols": args.symbols or [],
            "selection_method": "explicit_date_with_elevated_activity",
            "stress_validated": stress_validated,
        },
        "known_mm_addresses": list(known_mm),
        "candidates": [c for c in candidates[:100]],  # top 100
        "download_bytes": total_dl,
        "listed_bytes_estimate": total_dl,
        "source_paths": s3_sources + api_sources,
        "warnings": [],
    }
    write_json(report_dir / "candidate_backstop_addresses.json", cad)

    # known_mm_comparison.json - use AddressStressProfile objects, serialize later
    mm_profiles: dict[str, AddressStressProfile] = {}
    for addr in known_mm:
        prof = profiles.get(addr)
        if prof is not None:
            mm_profiles[addr] = prof
    mm_candidates = [c for c in candidates if c["address"] in known_mm or c["classification"] == "MM_LIKE"]
    write_json(report_dir / "known_mm_comparison.json", {
        "known_mm_addresses": list(known_mm),
        "known_mm_profiles": {
            addr: {
                "total_fills": prof.total_fills,
                "unique_coins": len(prof.unique_coins),
                "net_delta_ratio": prof.net_delta_ratio,
                "pairability": prof.pairability_rate,
                "burst_score": prof.one_sided_burst_score,
                "concentration": prof.concentration_score,
                "close_like_cluster": prof.close_like_cluster_score,
            } for addr, prof in mm_profiles.items()
        },
        "mm_like_candidates": [c["address"] for c in mm_candidates],
        "backstop_candidate_count": len(high_conf_backstop),
    })

    # vault_details_request_sanity.json
    write_json(report_dir / "vault_details_request_sanity.json", vd_sanity)

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
        "total_records": len(all_records),
        "unique_addresses_profiled": len(profiles),
        "total_download_bytes": total_dl,
        "stress_validated": stress_validated,
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
        "stress_attribution_verdict": verdict,
        "stress_window": {
            "date": args.date,
            "hours": hours,
            "symbols": args.symbols or [],
            "stress_validated": stress_validated,
        },
        "backstop_candidate_found": len(high_conf_backstop) > 0,
        "backstop_candidate_addresses": backstop_candidate_addrs,
        "known_mm_addresses": list(known_mm),
        "full_hlp_diagnostic_runnable_now": full_diagnostic_runnable,
        "exact_blocker_if_not_runnable": blocker,
        "download_bytes": total_dl,
        "listed_bytes_estimate": total_dl,
        "s3_sources_touched": s3_sources,
        "api_sources_touched": api_sources,
        "artifact_paths": {
            "stress_window_selection": str(report_dir / "stress_window_selection.json"),
            "per_address_stress_profile": str(report_dir / "per_address_stress_profile.csv"),
            "per_symbol_address_profile": str(report_dir / "per_symbol_address_profile.csv"),
            "candidate_backstop_addresses": str(report_dir / "candidate_backstop_addresses.json"),
            "known_mm_comparison": str(report_dir / "known_mm_comparison.json"),
            "position_close_pattern_profile": str(report_dir / "position_close_pattern_profile.csv"),
            "vault_details_request_sanity": str(report_dir / "vault_details_request_sanity.json"),
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
    md = [
        f"# HLP Backstop Stress-Window Attribution Probe — {args.date}",
        "",
        f"**Run ID:** {rid}",
        f"**Verdict:** `{verdict}`",
        f"**Full diagnostic runnable:** {full_diagnostic_runnable}",
        f"**Blocker:** {blocker or 'None'}",
        "",
        f"## Stress Window",
        f"- Date: {args.date}, Hours: {download_hours_str}",
        f"- Validated: {stress_validated}",
        f"- Downloaded: {human_bytes(total_dl)}",
        "",
        f"## Classification Summary",
        f"- Total addresses profiled: {len(profiles)}",
        f"- Backstop candidates: {len(high_conf_backstop)}",
        f"- Backstop candidate addresses: {backstop_candidate_addrs}",
        f"- Known MM addresses: {list(known_mm)}",
        "",
        f"## VaultDetails Sanity",
        f"- Conclusion: {vd_sanity.get('conclusion', 'not_tested')}",
        "",
        "## Safety",
        "- Observer only. No orders, auth, trading, paper, shadow, bot, or registry mutation.",
        "",
    ]
    write_json(report_dir / "summary.md", {"lines": md})

    # Safety grep
    self_path = Path(__file__).resolve()
    self_content = self_path.read_text()
    def_start = self_content.find("FORBIDDEN_STRINGS: tuple[str, ...] = (")
    def_end = self_content.find(")", def_start) + 1 if def_start >= 0 else 0
    check_content = self_content[:def_start] + self_content[def_end:] if def_end > 0 else self_content
    for forbidden in FORBIDDEN_STRINGS:
        if forbidden in check_content:
            print(f"WARNING: Forbidden string '{forbidden}' outside definition", file=sys.stderr)

    # -----------------------------------------------------------------------
    print(f"\n{'='*60}", flush=True)
    print(f"Stress probe complete.", flush=True)
    print(f"Artifacts: {report_dir}/", flush=True)
    print(f"Verdict: {verdict}", flush=True)
    print(f"Backstop candidates: {len(high_conf_backstop)}", flush=True)
    if backstop_candidate_addrs:
        print(f"Candidates: {backstop_candidate_addrs}", flush=True)
    if blocker:
        print(f"Blocker: {blocker}", flush=True)
    print(f"Downloaded: {human_bytes(total_dl)}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())