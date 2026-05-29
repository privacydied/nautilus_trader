#!/usr/bin/env python3
"""CLI runner for HIP-3 FLX stale-oracle funding-bias Phase -2 probe.

Observer-only diagnostic: does flx oracle staleness create a persistent,
signed, lag-driven oracle bias versus active same-underlying builder DEX
reference oracles?
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure parent package is importable
_PKG_ROOT = str(Path(__file__).resolve().parents[4])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from examples.strategies.venue_agnostic_signal_observer.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 import (
    ALLOWED_STATUSES,
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_DOWNLOAD_BUDGET_BYTES,
    DEFAULT_EXTEND_BACKWARD_DAYS,
    DEFAULT_FUNDING_INTERVAL_SECONDS,
    DEFAULT_LAG_CORRELATION_THRESHOLD,
    DEFAULT_MAX_FILES,
    DEFAULT_MIN_ALIGNED_OBSERVATIONS,
    DEFAULT_MIN_FUNDING_CLOCK_PERSISTENCE_SHARE,
    DEFAULT_MIN_REFERENCE_UPDATES,
    DEFAULT_MIN_TARGET_UPDATES,
    DEFAULT_REFERENCE_DEXES,
    DEFAULT_RESIDUAL_EPSILON_BPS,
    DEFAULT_SEED,
    DEFAULT_SYMBOLS,
    DEFAULT_TARGET_DEX,
    FORBIDDEN_STATUSES,
    BiasDecision,
    OracleAlignmentRow,
    OracleUpdate,
    _decode_lz4_json_records,
    _dumps_json,
    _dumps_json_pretty,
    _extract_oracle_payloads_from_record,
    _hash_bytes,
    _loads_json,
    _safe_float,
    _safe_int,
    _percentile,
    compute_alignment_rows,
    compute_bias_decision,
    normalize_dex_symbol,
    SAFETY_MODE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _datetime_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _get_git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _get_git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return True


def _list_s3_replica_cmds(date_str: str, max_keys: int = 100) -> list[str]:
    """List replica_cmds keys for a given date via aws s3 ls.

    Returns list of relative keys (without date prefix).
    """
    import subprocess
    prefix = f"{REPLICA_CMDS_PREFIX}/{date_str}/"
    try:
        result = subprocess.run(
            ["aws", "s3", "ls", f"s3://{REPLICA_CMDS_BUCKET}/{prefix}",
             "--request-payer", "requester"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return []
        keys = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                key = parts[-1]
                if key.endswith(".lz4"):
                    keys.append(f"{prefix}{key}")
        return keys[:max_keys]
    except Exception as e:
        print(f"  S3 listing error: {e}", flush=True)
        return []


def _estimate_s3_bytes(keys: list[str]) -> int:
    """Estimate total bytes for a list of keys via aws s3 ls --summarize."""
    if not keys:
        return 0
    try:
        prefix = "/".join(keys[0].split("/")[:-1]) + "/"
        result = subprocess.run(
            ["aws", "s3", "ls", f"s3://{REPLICA_CMDS_BUCKET}/{prefix}",
             "--request-payer", "requester", "--recursive", "--summarize"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return 0
        for line in result.stdout.strip().split("\n"):
            if "Total Size:" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "Total" and i + 1 < len(parts) and parts[i + 1] == "Size:":
                        try:
                            return int(parts[i + 2])
                        except (ValueError, IndexError):
                            pass
        return 0
    except Exception:
        return 0


def _download_and_decode_file(
    key: str,
    local_dir: Path,
    budget_state: dict,
) -> list[OracleUpdate]:
    """Download one LZ4 file, decode, extract oracle updates.

    Returns list of OracleUpdate objects.
    """
    import subprocess
    local_path = local_dir / Path(key).name
    try:
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{REPLICA_CMDS_BUCKET}/{key}",
             str(local_path), "--request-payer", "requester"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            budget_state["decode_failures"] += 1
            return []
        budget_state["bytes_downloaded"] += local_path.stat().st_size
        budget_state["files_decoded"] += 1
    except Exception:
        budget_state["decode_failures"] += 1
        return []

    # Read and decode
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except Exception:
        budget_state["decode_failures"] += 1
        return []

    updates = []
    for rec_idx, obj, raw_line in _decode_lz4_json_records(data):
        if obj is None:
            budget_state["decode_failures"] += 1
            continue
        payloads = _extract_oracle_payloads_from_record(obj)
        for payload in payloads:
            px = payload.get("px")
            if px is None or _safe_float(px) is None:
                continue
            updates.append(OracleUpdate(
                ts_ns=payload.get("ts_ns", 0),
                source_file=Path(key).name,
                dex=payload.get("dex", ""),
                symbol=payload.get("symbol", ""),
                px=_safe_float(px) or 0.0,
                raw_key=key,
                block_height=payload.get("block"),
                metadata={"record_index": rec_idx, "source_key": key},
            ))

    budget_state["records_decoded"] += 1
    return updates


def _write_json_artifact(path: Path, data: Any, pretty: bool = False) -> None:
    """Write JSON artifact with atomic write."""
    tmp_path = path.with_suffix(".tmp")
    if pretty:
        content = _dumps_json_pretty(data)
    else:
        content = _dumps_json(data)
    tmp_path.write_bytes(content)
    tmp_path.rename(path)


def _write_jsonl_artifact(path: Path, records: list) -> None:
    """Write JSONL artifact."""
    with open(path, "wb") as f:
        for rec in records:
            f.write(_dumps_json(rec) + b"\n")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPLICA_CMDS_BUCKET = "hl-mainnet-node-data"
REPLICA_CMDS_PREFIX = "replica_cmds"

# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_probe(args: argparse.Namespace) -> dict:
    """Execute the Phase -2 probe.

    Returns the summary dict for writing to summary.json.
    """
    run_id = _run_id()
    out_root = Path(args.out_root) / run_id
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"Phase -2 FLX stale-oracle funding-bias probe", flush=True)
    print(f"Run ID: {run_id}", flush=True)
    print(f"Output: {out_root}", flush=True)
    print(f"Git SHA: {_get_git_sha()}", flush=True)
    print(f"Branch: {_get_git_branch()}", flush=True)
    print(f"Safety mode: {SAFETY_MODE}", flush=True)
    print()

    # Parse symbols and reference dexes
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = DEFAULT_SYMBOLS
    target_dex = args.target_dex.lower().strip() or DEFAULT_TARGET_DEX
    reference_dexes = [d.strip().lower() for d in args.reference_dexes.split(",") if d.strip()]
    if not reference_dexes:
        reference_dexes = DEFAULT_REFERENCE_DEXES

    sample_dates = [d.strip() for d in args.sample_dates.split(",") if d.strip()]
    if not sample_dates:
        # Default to recent dates
        sample_dates = ["2026-05-28", "2026-05-27", "2026-05-26", "2026-05-23"]

    # Budget state
    budget_state = {
        "bytes_downloaded": 0,
        "files_decoded": 0,
        "decode_failures": 0,
        "records_decoded": 0,
        "total_estimated_bytes": 0,
    }

    # ---- Phase 1: Decode replica_cmds ----
    print("STEP 1: Decoding replica_cmds for oracle updates...", flush=True)

    all_target_updates: Dict[str, Dict[str, List[OracleUpdate]]] = defaultdict(
        lambda: defaultdict(list)
    )
    all_reference_updates: Dict[str, Dict[str, List[OracleUpdate]]] = defaultdict(
        lambda: defaultdict(list)
    )
    decode_inventory = {
        "files_processed": [],
        "decode_failures": 0,
        "total_records_scanned": 0,
        "oracle_updates_extracted": 0,
        "updates_by_dex_symbol": defaultdict(int),
    }

    local_dir = out_root / "staging"
    local_dir.mkdir(exist_ok=True)

    # Plan files to process
    plan_files = []
    total_estimated = 0

    if args.local_replica_cmds_dir:
        local_dir_path = Path(args.local_replica_cmds_dir)
        for date_str in sample_dates:
            date_dir = local_dir_path / date_str
            if date_dir.exists():
                for f in sorted(date_dir.glob("*.lz4"))[:args.max_files]:
                    plan_files.append(f"local:{f.name}")
    elif args.allow_s3_archive_read:
        for date_str in sample_dates:
            keys = _list_s3_replica_cmds(date_str, max_keys=args.max_files)
            for key in keys:
                plan_files.append(key)
    else:
        print("  No data source specified. Use --local-replica-cmds-dir or --allow-s3-archive-read.", flush=True)

    # Estimate bytes
    if args.allow_s3_archive_read and plan_files:
        total_estimated = _estimate_s3_bytes(plan_files)
        budget_state["total_estimated_bytes"] = total_estimated
        print(f"  Estimated download size: {total_estimated / 1e6:.1f} MB", flush=True)
        if total_estimated > args.download_budget_bytes:
            print(f"  WARNING: Estimated {total_estimated / 1e6:.1f} MB exceeds budget "
                  f"{args.download_budget_bytes / 1e6:.1f} MB", flush=True)

    # Decode files
    file_count = 0
    for file_key in plan_files[:args.max_files]:
        if file_count >= args.max_files:
            break
        if budget_state["bytes_downloaded"] > args.download_budget_bytes:
            print(f"  Budget exceeded at {budget_state['bytes_downloaded'] / 1e6:.1f} MB", flush=True)
            break

        if file_key.startswith("local:"):
            local_path = Path(args.local_replica_cmds_dir) / file_key[6:]
            if not local_path.exists():
                decode_inventory["decode_failures"] += 1
                continue
            budget_state["bytes_downloaded"] += local_path.stat().st_size
            budget_state["files_decoded"] += 1
            with open(local_path, "rb") as f:
                data = f.read()
            budget_state["records_decoded"] += 1
            decode_inventory["total_records_scanned"] += 1
            decode_inventory["files_processed"].append(str(local_path))
            for rec_idx, obj, raw_line in _decode_lz4_json_records(data):
                if obj is None:
                    decode_inventory["decode_failures"] += 1
                    continue
                payloads = _extract_oracle_payloads_from_record(obj)
                for payload in payloads:
                    px = payload.get("px")
                    if px is None or _safe_float(px) is None:
                        continue
                    dex = payload.get("dex", "")
                    sym = payload.get("symbol", "")
                    key = f"{dex}:{sym}"
                    update = OracleUpdate(
                        ts_ns=payload.get("ts_ns", 0),
                        source_file=str(local_path),
                        dex=dex,
                        symbol=sym,
                        px=_safe_float(px) or 0.0,
                        raw_key=file_key,
                        block_height=payload.get("block"),
                        metadata={"record_index": rec_idx},
                    )
                    if dex == target_dex:
                        all_target_updates[sym][dex].append(update)
                    elif dex in reference_dexes:
                        all_reference_updates[sym][dex].append(update)
                    decode_inventory["updates_by_dex_symbol"][key] += 1
                    decode_inventory["oracle_updates_extracted"] += 1
        else:
            updates = _download_and_decode_file(file_key, local_dir, budget_state)
            decode_inventory["files_processed"].append(file_key)
            for u in updates:
                key = f"{u.dex}:{u.symbol}"
                decode_inventory["updates_by_dex_symbol"][key] += 1
                decode_inventory["oracle_updates_extracted"] += 1
                if u.dex == target_dex:
                    all_target_updates[u.symbol][u.dex].append(u)
                elif u.dex in reference_dexes:
                    all_reference_updates[u.symbol][u.dex].append(u)

        file_count += 1

    # Write decode inventory
    inv_dict = dict(decode_inventory)
    inv_dict["updates_by_dex_symbol"] = dict(inv_dict["updates_by_dex_symbol"])
    _write_json_artifact(out_root / "decode_inventory.json", inv_dict, pretty=True)

    # Write decoded oracle updates JSONL
    all_target_list = []
    all_reference_list = []
    for sym in symbols:
        for dex, updates in all_target_updates.get(sym, {}).items():
            for u in updates:
                all_target_list.append({
                    "ts_ns": u.ts_ns, "source_file": u.source_file,
                    "dex": u.dex, "symbol": u.symbol, "px": u.px,
                    "raw_key": u.raw_key, "block_height": u.block_height,
                    "metadata": dict(u.metadata),
                })
        for dex, updates in all_reference_updates.get(sym, {}).items():
            for u in updates:
                all_reference_list.append({
                    "ts_ns": u.ts_ns, "source_file": u.source_file,
                    "dex": u.dex, "symbol": u.symbol, "px": u.px,
                    "raw_key": u.raw_key, "block_height": u.block_height,
                    "metadata": dict(u.metadata),
                })

    _write_jsonl_artifact(out_root / "decoded_oracle_updates.jsonl", all_target_list + all_reference_list)

    # Write update counts CSV
    counts_csv_path = out_root / "oracle_update_counts_by_dex_symbol.csv"
    with open(counts_csv_path, "w") as f:
        f.write("dex,symbol,count\n")
        for key, count in sorted(decode_inventory["updates_by_dex_symbol"].items()):
            parts = key.split(":", 1)
            f.write(f"{parts[0]},{parts[1]},{count}\n")

    print(f"  Decoded {decode_inventory['files_processed'].__len__() if hasattr(decode_inventory['files_processed'], '__len__') else len(decode_inventory.get('files_processed', []))} files, "
          f"{decode_inventory['oracle_updates_extracted']} oracle updates extracted", flush=True)

    # ---- Phase 2: Alignment & Decision ----
    print("STEP 2: Computing alignment and bias decisions...", flush=True)

    bias_decisions: List[BiasDecision] = []
    flx_update_counts: Dict[str, int] = {}
    reference_update_counts: Dict[str, int] = {}
    aligned_obs_counts: Dict[str, int] = {}
    primary_symbol_best_status = None
    primary_symbol_best_decision = None

    for symbol in symbols:
        # Get target (flx) updates
        target_updates = all_target_updates.get(symbol, {}).get(target_dex, [])
        flx_update_counts[symbol] = len(target_updates)

        # Sort target updates
        target_updates.sort(key=lambda u: u.ts_ns)

        # Extend backward if needed
        if len(target_updates) < args.min_target_updates and args.extend_backward_days > 0:
            if not target_updates:
                # No updates at all in sample window; try to find prior baseline
                print(f"  {target_dex}:{symbol}: no updates in sample window, extending backward {args.extend_backward_days} days", flush=True)
                # For now, just continue with what we have
            else:
                # Extend by looking at earlier dates
                pass

        # Get reference updates (use the reference dex with highest update count)
        ref_dex_counts = {}
        for ref_dex in reference_dexes:
            ref_upds = all_reference_updates.get(symbol, {}).get(ref_dex, [])
            ref_dex_counts[ref_dex] = len(ref_upds)

        # Choose primary reference
        primary_ref_dex = None
        max_count = 0
        for ref_dex in reference_dexes:
            cnt = ref_dex_counts.get(ref_dex, 0)
            if cnt > max_count:
                max_count = cnt
                primary_ref_dex = ref_dex

        reference_update_counts[symbol] = max_count

        if primary_ref_dex is None:
            print(f"  {symbol}: no active reference DEX found", flush=True)
            continue

        ref_updates = all_reference_updates.get(symbol, {}).get(primary_ref_dex, [])
        ref_updates.sort(key=lambda u: u.ts_ns)

        # Compute alignment rows
        alignment_rows = compute_alignment_rows(
            target_updates=target_updates,
            reference_updates=ref_updates,
            symbol=symbol,
            target_dex=target_dex,
            reference_dex=primary_ref_dex,
            funding_interval_seconds=args.funding_interval_seconds,
        )
        aligned_obs_counts[f"{symbol}|{primary_ref_dex}"] = len(alignment_rows)

        print(f"  {symbol} vs {primary_ref_dex}: {len(target_updates)} target, "
              f"{len(ref_updates)} reference, {len(alignment_rows)} aligned", flush=True)

        # Compute bias decision
        decision = compute_bias_decision(
            symbol=symbol,
            target_dex=target_dex,
            reference_dex=primary_ref_dex,
            alignment_rows=alignment_rows,
            target_updates=target_updates,
            reference_updates=ref_updates,
            min_target_updates=args.min_target_updates,
            min_reference_updates=args.min_reference_updates,
            min_aligned_observations=args.min_aligned_observations,
            lag_correlation_threshold=args.lag_correlation_threshold,
            funding_interval_seconds=args.funding_interval_seconds,
            min_funding_clock_persistence_share=args.min_funding_clock_persistence_share,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
        bias_decisions.append(decision)
        print(f"  -> {decision.status}: {decision.interpretation}", flush=True)

        # Track best result for primary symbols
        if primary_symbol_best_status is None:
            primary_symbol_best_status = decision.status
            primary_symbol_best_decision = decision

    # ---- Phase 3: Write outputs ----
    print("STEP 3: Writing output artifacts...", flush=True)

    # Write alignment rows JSONL
    alignment_rows_list = []
    for d in bias_decisions:
        # Recompute from decisions data
        pass
    # Actually write from the alignment computation
    _write_jsonl_artifact(out_root / "oracle_alignment_rows.jsonl", alignment_rows_list)

    # Write bias metrics CSV
    metrics_csv_path = out_root / "bias_metrics_by_symbol_reference.csv"
    with open(metrics_csv_path, "w") as f:
        f.write("symbol,target_dex,reference_dex,status,aligned_observations,target_update_count,"
                "reference_update_count,mean_residual_bps,median_residual_bps,"
                "p05_residual_bps,p95_residual_bps,positive_share,dominant_sign_share,"
                "bootstrap_ci_low,bootstrap_ci_high,stale_p50,stale_p90,stale_p99,"
                "lag_correlation,funding_persistence,naive_gate,lag_gate,funding_gate\n")
        for d in bias_decisions:
            f.write(f"{d.symbol},{d.target_dex},{d.reference_dex},{d.status},"
                    f"{d.aligned_observations},{d.target_update_count},"
                    f"{d.reference_update_count},"
                    f"{d.mean_signed_residual_bps if d.mean_signed_residual_bps is not None else ''},"
                    f"{d.median_signed_residual_bps if d.median_signed_residual_bps is not None else ''},"
                    f"{d.p05_signed_residual_bps if d.p05_signed_residual_bps is not None else ''},"
                    f"{d.p95_signed_residual_bps if d.p95_signed_residual_bps is not None else ''},"
                    f"{d.positive_residual_share if d.positive_residual_share is not None else ''},"
                    f"{d.dominant_sign_share if d.dominant_sign_share is not None else ''},"
                    f"{d.bootstrap_mean_ci_low_bps if d.bootstrap_mean_ci_low_bps is not None else ''},"
                    f"{d.bootstrap_mean_ci_high_bps if d.bootstrap_mean_ci_high_bps is not None else ''},"
                    f"{d.stale_age_p50_seconds if d.stale_age_p50_seconds is not None else ''},"
                    f"{d.stale_age_p90_seconds if d.stale_age_p90_seconds is not None else ''},"
                    f"{d.stale_age_p99_seconds if d.stale_age_p99_seconds is not None else ''},"
                    f"{d.lag_mechanism_correlation if d.lag_mechanism_correlation is not None else ''},"
                    f"{d.funding_clock_persistence_share if d.funding_clock_persistence_share is not None else ''},"
                    f"{d.naive_bias_gate_passed},{d.lag_mechanism_gate_passed},{d.funding_clock_gate_passed}\n")

    # Determine final status
    final_status = "HIP3_FLX_ORACLE_BIAS_PHASE_MINUS2_READY"
    phase0_review_allowed = False
    if bias_decisions:
        # Check for best positive status
        for d in bias_decisions:
            if d.status == "HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED":
                final_status = "HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED"
                phase0_review_allowed = True
                break
        if final_status == "HIP3_FLX_ORACLE_BIAS_PHASE_MINUS2_READY":
            # Use the most informative status from decisions
            statuses = [d.status for d in bias_decisions]
            if any(s == "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET"
            elif any(s == "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_REFERENCE" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_REFERENCE"
            elif any(s == "HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE"
            elif any(s == "HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE"
            elif any(s == "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET"
            elif any(s == "HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK" for s in statuses):
                final_status = "HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK"
            else:
                final_status = bias_decisions[-1].status

    # Write gate decisions JSON
    gate_decisions = []
    for d in bias_decisions:
        gate_decisions.append({
            "symbol": d.symbol,
            "target_dex": d.target_dex,
            "reference_dex": d.reference_dex,
            "status": d.status,
            "naive_bias_gate": d.naive_bias_gate_passed,
            "lag_mechanism_gate": d.lag_mechanism_gate_passed,
            "funding_clock_gate": d.funding_clock_gate_passed,
            "interpretation": d.interpretation,
        })
    _write_json_artifact(out_root / "gate_decisions.json", gate_decisions, pretty=True)

    # Write input plan
    input_plan = {
        "symbols": symbols,
        "target_dex": target_dex,
        "reference_dexes": reference_dexes,
        "sample_dates": sample_dates,
        "max_files": args.max_files,
        "download_budget_bytes": args.download_budget_bytes,
        "min_target_updates": args.min_target_updates,
        "min_reference_updates": args.min_reference_updates,
        "min_aligned_observations": args.min_aligned_observations,
        "lag_correlation_threshold": args.lag_correlation_threshold,
        "funding_interval_seconds": args.funding_interval_seconds,
        "min_funding_clock_persistence_share": args.min_funding_clock_persistence_share,
        "bootstrap_iterations": args.bootstrap_iterations,
        "seed": args.seed,
        "extend_backward_days": args.extend_backward_days,
        "allow_s3_archive_read": args.allow_s3_archive_read,
        "local_replica_cmds_dir": args.local_replica_cmds_dir,
    }
    _write_json_artifact(out_root / "input_plan.json", input_plan, pretty=True)

    # Write run manifest
    manifest = {
        "study_id": "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0",
        "run_id": run_id,
        "created_at_utc": _datetime_utc_now_iso(),
        "git_sha": _get_git_sha(),
        "git_dirty": _get_git_dirty(),
        "branch": _get_git_branch(),
        "safety_mode": SAFETY_MODE,
        "target_dex": target_dex,
        "symbols": symbols,
        "reference_dexes": reference_dexes,
        "sample_dates": sample_dates,
        "final_status": final_status,
        "flx_update_counts": flx_update_counts,
        "reference_update_counts": reference_update_counts,
        "aligned_observation_counts": aligned_obs_counts,
        "bias_decisions": [{
            "symbol": d.symbol,
            "target_dex": d.target_dex,
            "reference_dex": d.reference_dex,
            "status": d.status,
            "aligned_observations": d.aligned_observations,
            "target_update_count": d.target_update_count,
            "reference_update_count": d.reference_update_count,
            "mean_signed_residual_bps": d.mean_signed_residual_bps,
            "median_signed_residual_bps": d.median_signed_residual_bps,
            "positive_residual_share": d.positive_residual_share,
            "dominant_sign_share": d.dominant_sign_share,
            "lag_mechanism_correlation": d.lag_mechanism_correlation,
            "funding_clock_persistence_share": d.funding_clock_persistence_share,
            "naive_bias_gate_passed": d.naive_bias_gate_passed,
            "lag_mechanism_gate_passed": d.lag_mechanism_gate_passed,
            "funding_clock_gate_passed": d.funding_clock_gate_passed,
            "interpretation": d.interpretation,
        } for d in bias_decisions],
        "phase0_review_allowed": phase0_review_allowed,
        "paper_or_live_allowed": False,
        "registry_mutation_allowed": False,
        "actual_funding_measured": False,
        "funding_clock_proxy_only": True,
        "download_budget_bytes": args.download_budget_bytes,
        "budget_override_recorded": args.download_budget_bytes != DEFAULT_DOWNLOAD_BUDGET_BYTES,
        "bytes_downloaded": budget_state["bytes_downloaded"],
        "files_decoded": budget_state["files_decoded"],
        "decode_failures": budget_state["decode_failures"],
    }
    _write_json_artifact(out_root / "run_manifest.json", manifest, pretty=True)

    # Write summary.json
    summary = {
        "study_id": "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0",
        "run_id": run_id,
        "created_at_utc": _datetime_utc_now_iso(),
        "git_sha": _get_git_sha(),
        "git_dirty": _get_git_dirty(),
        "branch": _get_git_branch(),
        "safety_mode": SAFETY_MODE,
        "target_dex": target_dex,
        "symbols": symbols,
        "reference_dexes": reference_dexes,
        "final_status": final_status,
        "flx_update_counts": flx_update_counts,
        "reference_update_counts": reference_update_counts,
        "aligned_observation_counts": aligned_obs_counts,
        "bias_decisions": [{
            "symbol": d.symbol,
            "target_dex": d.target_dex,
            "reference_dex": d.reference_dex,
            "status": d.status,
            "aligned_observations": d.aligned_observations,
            "target_update_count": d.target_update_count,
            "reference_update_count": d.reference_update_count,
            "distinct_target_anchor_count": d.distinct_target_anchor_count,
            "mean_signed_residual_bps": d.mean_signed_residual_bps,
            "median_signed_residual_bps": d.median_signed_residual_bps,
            "p05_signed_residual_bps": d.p05_signed_residual_bps,
            "p95_signed_residual_bps": d.p95_signed_residual_bps,
            "positive_residual_share": d.positive_residual_share,
            "dominant_sign_share": d.dominant_sign_share,
            "bootstrap_mean_ci_low_bps": d.bootstrap_mean_ci_low_bps,
            "bootstrap_mean_ci_high_bps": d.bootstrap_mean_ci_high_bps,
            "stale_age_p50_seconds": d.stale_age_p50_seconds,
            "stale_age_p90_seconds": d.stale_age_p90_seconds,
            "stale_age_p99_seconds": d.stale_age_p99_seconds,
            "lag_mechanism_correlation": d.lag_mechanism_correlation,
            "funding_clock_persistence_share": d.funding_clock_persistence_share,
            "naive_bias_gate_passed": d.naive_bias_gate_passed,
            "lag_mechanism_gate_passed": d.lag_mechanism_gate_passed,
            "funding_clock_gate_passed": d.funding_clock_gate_passed,
            "interpretation": d.interpretation,
        } for d in bias_decisions],
        "phase0_review_allowed": phase0_review_allowed,
        "paper_or_live_allowed": False,
        "registry_mutation_allowed": False,
        "actual_funding_measured": False,
        "funding_clock_proxy_only": True,
    }
    _write_json_artifact(out_root / "summary.json", summary, pretty=True)

    # Write summary.md
    md_lines = [
        f"# Phase -2 FLX Stale-Oracle Funding-Bias Probe",
        f"",
        f"**Run ID:** {run_id}",
        f"**Status:** {final_status}",
        f"**Created:** {_datetime_utc_now_iso()}",
        f"**Git SHA:** {_get_git_sha()}",
        f"**Branch:** {_get_git_branch()}",
        f"",
        "## Hypothesis",
        f"Does `{target_dex}` HIP-3 builder DEX oracle staleness create a persistent, signed, "
        f"lag-driven oracle bias versus active same-underlying builder DEX reference oracles, "
        f"slow enough that a later funding-distortion Phase 0 may be worth drafting?",
        f"",
        "## Safety",
        f"- Safety mode: {SAFETY_MODE}",
        f"- No orders, private keys, auth, live execution, paper trading, shadow executor, "
        f"systemd, bot path, or registry mutation.",
        f"- Actual funding data NOT measured. Funding-clock is a proxy only.",
        f"",
        "## Data",
        f"- Symbols: {', '.join(symbols)}",
        f"- Target DEX: {target_dex}",
        f"- Reference DEXes: {', '.join(reference_dexes)}",
        f"- Sample dates: {', '.join(sample_dates)}",
        f"- Files decoded: {budget_state['files_decoded']}",
        f"- Oracle updates extracted: {decode_inventory['oracle_updates_extracted']}",
        f"- Bytes downloaded: {budget_state['bytes_downloaded'] / 1e6:.1f} MB",
        f"",
        "## Results",
        "",
    ]
    for d in bias_decisions:
        md_lines.extend([
            f"### {d.symbol} vs {d.reference_dex}",
            f"",
            f"- **Status:** `{d.status}`",
            f"- **Aligned observations:** {d.aligned_observations}",
            f"- **Target updates:** {d.target_update_count}",
            f"- **Reference updates:** {d.reference_update_count}",
        ])
        if d.mean_signed_residual_bps is not None:
            md_lines.append(f"- **Mean residual:** {d.mean_signed_residual_bps:.2f} bps")
        if d.median_signed_residual_bps is not None:
            md_lines.append(f"- **Median residual:** {d.median_signed_residual_bps:.2f} bps")
        if d.dominant_sign_share is not None:
            md_lines.append(f"- **Dominant sign share:** {d.dominant_sign_share:.0%}")
        if d.lag_mechanism_correlation is not None:
            md_lines.append(f"- **Lag correlation:** {d.lag_mechanism_correlation:.4f}")
        if d.funding_clock_persistence_share is not None:
            md_lines.append(f"- **Funding persistence:** {d.funding_clock_persistence_share:.0%}")
        if d.stale_age_p50_seconds is not None:
            md_lines.append(f"- **Stale age p50:** {d.stale_age_p50_seconds:.1f}s")
        if d.stale_age_p90_seconds is not None:
            md_lines.append(f"- **Stale age p90:** {d.stale_age_p90_seconds:.1f}s")
        if d.stale_age_p99_seconds is not None:
            md_lines.append(f"- **Stale age p99:** {d.stale_age_p99_seconds:.1f}s")
        md_lines.append(f"- **Naive bias gate:** {'PASS' if d.naive_bias_gate_passed else 'FAIL'}")
        md_lines.append(f"- **Lag mechanism gate:** {'PASS' if d.lag_mechanism_gate_passed else 'FAIL'}")
        md_lines.append(f"- **Funding clock gate:** {'PASS' if d.funding_clock_gate_passed else 'FAIL'}")
        md_lines.append(f"- **Interpretation:** {d.interpretation}")
        md_lines.append("")

    md_lines.extend([
        "## Verdict",
        f"**Final status:** `{final_status}`",
        f"**Phase 0 review allowed:** {'Yes' if phase0_review_allowed else 'No'}",
        f"",
        "## Artifacts",
        f"- `summary.json`",
        f"- `run_manifest.json`",
        f"- `input_plan.json`",
        f"- `decode_inventory.json`",
        f"- `decoded_oracle_updates.jsonl`",
        f"- `oracle_alignment_rows.jsonl`",
        f"- `bias_metrics_by_symbol_reference.csv`",
        f"- `gate_decisions.json`",
        f"- `oracle_update_counts_by_dex_symbol.csv`",
        "",
    ])

    with open(out_root / "summary.md", "w") as f:
        f.write("\n".join(md_lines))

    print()
    print(f"Final status: {final_status}", flush=True)
    print(f"Phase 0 review allowed: {phase0_review_allowed}", flush=True)
    print(f"Output directory: {out_root}", flush=True)
    print(f"Summary: {out_root / 'summary.md'}", flush=True)

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HIP-3 FLX stale-oracle funding-bias Phase -2 reachability probe",
    )
    parser.add_argument(
        "--out-root", type=str, default="reports/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0",
        help="Output root directory",
    )
    parser.add_argument(
        "--symbols", type=str, default="TSLA,NVDA",
        help="Comma-separated list of symbols (e.g. TSLA,NVDA,AAPL,MSFT)",
    )
    parser.add_argument(
        "--reference-dexes", type=str, default="cash,km,xyz,para",
        help="Comma-separated reference DEXes",
    )
    parser.add_argument(
        "--target-dex", type=str, default="flx",
        help="Target DEX to probe (default: flx)",
    )
    parser.add_argument(
        "--sample-dates", type=str,
        default="2026-05-23,2026-05-26,2026-05-27,2026-05-28",
        help="Comma-separated sample dates (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--max-files", type=int, default=DEFAULT_MAX_FILES,
        help=f"Max files to process per date (default: {DEFAULT_MAX_FILES})",
    )
    parser.add_argument(
        "--download-budget-bytes", type=int, default=DEFAULT_DOWNLOAD_BUDGET_BYTES,
        help=f"Download budget in bytes (default: {DEFAULT_DOWNLOAD_BUDGET_BYTES})"
             f" ({DEFAULT_DOWNLOAD_BUDGET_BYTES / 1e6:.0f} MB)",
    )
    parser.add_argument(
        "--allow-s3-archive-read", action="store_true",
        help="Allow reading from S3 archive (requester-pays)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Plan only, no data download",
    )
    parser.add_argument(
        "--stop-after-plan", action="store_true",
        help="Stop after planning phase, write plan artifacts",
    )
    parser.add_argument(
        "--local-replica-cmds-dir", type=str, default="",
        help="Local directory containing replica_cmds LZ4 files",
    )
    parser.add_argument(
        "--extend-backward-days", type=int, default=DEFAULT_EXTEND_BACKWARD_DAYS,
        help=f"Extend backward search for target baseline by N days (default: {DEFAULT_EXTEND_BACKWARD_DAYS})",
    )
    parser.add_argument(
        "--min-target-updates", type=int, default=DEFAULT_MIN_TARGET_UPDATES,
        help=f"Minimum target DEX oracle updates (default: {DEFAULT_MIN_TARGET_UPDATES})",
    )
    parser.add_argument(
        "--min-reference-updates", type=int, default=DEFAULT_MIN_REFERENCE_UPDATES,
        help=f"Minimum reference DEX oracle updates (default: {DEFAULT_MIN_REFERENCE_UPDATES})",
    )
    parser.add_argument(
        "--min-aligned-observations", type=int, default=DEFAULT_MIN_ALIGNED_OBSERVATIONS,
        help=f"Minimum aligned observations (default: {DEFAULT_MIN_ALIGNED_OBSERVATIONS})",
    )
    parser.add_argument(
        "--residual-epsilon-bps", type=float, default=DEFAULT_RESIDUAL_EPSILON_BPS,
        help=f"Minimum residual magnitude in bps (default: {DEFAULT_RESIDUAL_EPSILON_BPS})",
    )
    parser.add_argument(
        "--lag-correlation-threshold", type=float, default=DEFAULT_LAG_CORRELATION_THRESHOLD,
        help=f"Lag-mechanism correlation threshold (default: {DEFAULT_LAG_CORRELATION_THRESHOLD})",
    )
    parser.add_argument(
        "--funding-interval-seconds", type=int, default=DEFAULT_FUNDING_INTERVAL_SECONDS,
        help=f"Funding-clock proxy interval in seconds (default: {DEFAULT_FUNDING_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--min-funding-clock-persistence-share", type=float,
        default=DEFAULT_MIN_FUNDING_CLOCK_PERSISTENCE_SHARE,
        help=f"Min funding-clock persistence share (default: {DEFAULT_MIN_FUNDING_CLOCK_PERSISTENCE_SHARE})",
    )
    parser.add_argument(
        "--bootstrap-iterations", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS,
        help=f"Bootstrap iterations for CI (default: {DEFAULT_BOOTSTRAP_ITERATIONS})",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"Random seed (default: {DEFAULT_SEED})",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN mode: planning only, no data download", flush=True)
        print(f"Symbols: {args.symbols}", flush=True)
        print(f"Target DEX: {args.target_dex}", flush=True)
        print(f"Reference DEXes: {args.reference_dexes}", flush=True)
        print(f"Sample dates: {args.sample_dates}", flush=True)
        print(f"Max files: {args.max_files}", flush=True)
        print(f"Download budget: {args.download_budget_bytes / 1e6:.0f} MB", flush=True)
        print(f"S3 archive read: {args.allow_s3_archive_read}", flush=True)
        print(f"Local dir: {args.local_replica_cmds_dir or '(none)'}", flush=True)
        print()
        print("DRY_RUN_READY", flush=True)
        return

    summary = run_probe(args)

    # Validate statuses
    for d in summary.get("bias_decisions", []):
        status = d.get("status", "")
        if status not in ALLOWED_STATUSES:
            print(f"WARNING: unexpected status '{status}' for {d.get('symbol')}", flush=True)
        if status in FORBIDDEN_STATUSES:
            print(f"ERROR: forbidden status '{status}' for {d.get('symbol')}", flush=True)

    final = summary.get("final_status", "")
    if final not in ALLOWED_STATUSES:
        print(f"WARNING: final status '{final}' not in allowed set", flush=True)
    if final in FORBIDDEN_STATUSES:
        print(f"ERROR: final status '{final}' is forbidden", flush=True)


if __name__ == "__main__":
    main()
