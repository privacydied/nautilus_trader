#!/usr/bin/env python3
"""
Campaign runner — repeated derivatives-source -> spot-target captures via subprocess isolation.

Spawns ``run_derivatives_spot_capture`` as a subprocess for each attempt.
One failed child process will not crash the whole campaign.
If ``--use-volatility-gate`` is set, the volatility gate is evaluated before
each attempt; if the gate fails the attempt is marked ``SKIPPED_LOW_VOLATILITY``
and consecutive skips are tracked.

Volatility gate caveat
----------------------
The gate uses recent-past OHLC information only (Kraken hourly bars).
It is NOT forward-looking — it measures recent volatility as a proxy
for whether the current market regime is worth capturing.  There is
no guarantee that past volatility predicts future activity.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_metadata import build_metadata
from .run_artifacts import atomic_write_json
from .run_artifacts import create_run_id
from .run_artifacts import safe_output_dir
from .run_index import append_run_index_row
from .run_index import build_run_index_row


# ---------------------------------------------------------------------------
# Import volatility gate — may fail if dependencies (urllib etc.) are missing
# ---------------------------------------------------------------------------
try:
    from examples.strategies.volatility_gate import compute_hourly_gate
    from examples.strategies.volatility_gate import hourly_bar_freshness

    _VOLATILITY_GATE_AVAILABLE = True
except ImportError:
    _VOLATILITY_GATE_AVAILABLE = False

# Volatility gate verdict constants (string literals matching the gate module)
MARKET_ACTIVE = "MARKET_ACTIVE"
MARKET_ALT_ACTIVE = "MARKET_ALT_ACTIVE"
ACCELERATING = "ACCELERATING"

# Campaign status constants
CAMPAIGN_RUNNING = "RUNNING"
CAMPAIGN_COMPLETED = "COMPLETED"
CAMPAIGN_ABORTED_LOW_VOLATILITY = "CAMPAIGN_ABORTED_LOW_VOLATILITY"
CAMPAIGN_FAILED = "FAILED"

# Attempt status constants
ATTEMPT_STARTED = "started"
ATTEMPT_COMPLETED = "completed"
ATTEMPT_FAILED = "failed"
ATTEMPT_SKIPPED_LOW_VOLATILITY = "SKIPPED_LOW_VOLATILITY"


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the campaign runner."""
    p = argparse.ArgumentParser(
        description="Run repeated derivatives-source -> spot-target captures as a campaign. "
                    "Each attempt is a subprocess. One failure does not crash the campaign.",
    )
    p.add_argument("--campaign-id", type=str, required=True,
                    help="Unique campaign identifier (used in output directory name).")
    p.add_argument("--captures", type=int, required=True,
                    help="Number of capture attempts to run in this campaign.")
    p.add_argument("--duration-seconds", type=int, default=1800,
                    help="Duration per capture attempt in seconds (default: 1800).")
    p.add_argument("--sleep-seconds", type=int, default=0,
                    help="Seconds to sleep between attempts (default: 0).")
    p.add_argument("--out-base", type=str, default="data",
                    help="Base directory for attempt output data (default: data).")
    p.add_argument("--reports-base", type=str, default="reports",
                    help="Base directory for campaign reports (default: reports).")
    p.add_argument("--use-volatility-gate", action="store_true", default=False,
                    help="Enable volatility gate check before each attempt.")
    p.add_argument("--volatility-threshold-bps", type=float, default=30.0,
                    help="Volatility threshold in bps for gate pass (default: 30).")
    p.add_argument("--max-skip-streak", type=int, default=5,
                    help="Max consecutive skipped attempts before aborting campaign (default: 5).")
    p.add_argument("--symbols", type=str, default="BTC/USDT,ETH/USDT,SOL/USDT",
                    help="Comma-separated list of canonical source symbols (default: BTC/USDT,ETH/USDT,SOL/USDT).")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase",
                    help="Comma-separated target spot venues (default: kraken,coinbase).")
    p.add_argument("--capture-open-interest", action="store_true", default=False,
                    help="Enable open interest capture during attempts.")
    p.add_argument("--open-interest-interval-seconds", type=int, default=5,
                    help="Open interest polling interval in seconds (default: 5).")
    p.add_argument("--python-executable", type=str, default=sys.executable,
                    help="Python executable to use for subprocess (default: sys.executable).")
    p.add_argument("--allow-existing-output", action="store_true", default=False,
                    help="Allow reuse of non-empty output directories.")
    return p


def _run_volatility_gate(
    threshold_bps: float,
) -> dict[str, Any]:
    """
    Run the volatility gate and return a structured result dict.

    Returns a dict with keys:
        gate_available : bool
        gate_passed : bool | None
        market_verdict : str | None
        accel_verdict : str | None
        btc_3h_bps : float | None
        threshold_bps : float
        error : str | None
        results : dict | None
        freshness : dict | None
        caveat : str
    """
    result: dict[str, Any] = {
        "gate_available": _VOLATILITY_GATE_AVAILABLE,
        "gate_passed": None,
        "market_verdict": None,
        "accel_verdict": None,
        "btc_3h_bps": None,
        "threshold_bps": threshold_bps,
        "error": None,
        "results": None,
        "freshness": None,
        "caveat": (
            "Volatility gate uses recent-past OHLC data (Kraken hourly bars) "
            "as a proxy for market regime. This is NOT forward-looking and does "
            "NOT guarantee future volatility or capture quality."
        ),
    }

    if not _VOLATILITY_GATE_AVAILABLE:
        result["error"] = "volatility_gate module not importable"
        result["gate_passed"] = False
        return result

    try:
        gate_results, market_verdict, accel_verdict = compute_hourly_gate()
    except Exception as exc:
        result["error"] = f"compute_hourly_gate raised: {exc}"
        result["gate_passed"] = False
        return result

    btc_3h = gate_results.get("BTC/USD", {}).get("range_3h_bps") or 0.0

    result["market_verdict"] = market_verdict
    result["accel_verdict"] = accel_verdict
    result["btc_3h_bps"] = round(btc_3h, 2)
    result["results"] = gate_results

    # Freshness
    try:
        from examples.strategies.volatility_gate import PAIRS
        from examples.strategies.volatility_gate import fetch_ohlc

        btc_bars = fetch_ohlc(PAIRS["BTC/USD"], interval=60, count=10)
        result["freshness"] = hourly_bar_freshness(btc_bars)
    except Exception:
        result["freshness"] = {"gate_data_stale_or_unchanged": True, "error": "freshness_check_failed"}

    # Gate pass logic: two independent pass conditions
    # 1) market_verdict is MARKET_ACTIVE or MARKET_ALT_ACTIVE AND accel_verdict is ACCELERATING
    # 2) btc_3h bps >= threshold_bps
    condition_1 = market_verdict in (MARKET_ACTIVE, MARKET_ALT_ACTIVE) and accel_verdict == ACCELERATING
    condition_2 = btc_3h >= threshold_bps

    result["condition_main_gate_active_and_accelerating"] = condition_1
    result["condition_btc_volatility_threshold_met"] = condition_2

    if condition_1 or condition_2:
        result["gate_passed"] = True
    else:
        result["gate_passed"] = False

    return result


def _build_attempt_dir(campaign_dir: Path, attempt_index: int) -> Path:
    """Build and return the output directory for a single attempt."""
    attempt_dir = campaign_dir / f"attempt_{attempt_index:04d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    return attempt_dir


def _run_single_attempt(
    attempt_index: int,
    args: argparse.Namespace,
    campaign_dir: Path,
    campaign_id: str,
    run_id: str,
    gate_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Run a single capture attempt as a subprocess.

    Returns an attempt result dict with keys:
        attempt_index
        run_id
        status
        command
        log_path
        returncode
        stdout
        stderr
        gate_snapshot (if gate was run)
        started_at
        ended_at
    """
    attempt_run_id = create_run_id(prefix=f"campaign_{campaign_id}_attempt_{attempt_index:04d}")
    attempt_dir = _build_attempt_dir(campaign_dir, attempt_index)
    attempt_log = attempt_dir / f"attempt_{attempt_index:04d}.log"

    # Map source symbols (e.g. BTC/USDT) to USDT pair for Binance perp
    source_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    # Target symbols: replace USDT with USD (Kraken/Coinbase quote)
    target_symbols = [s.replace("USDT", "USD") for s in source_symbols]

    # Build the subprocess command
    cmd = [
        str(args.python_executable),
        "-m",
        "examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture",
        "--source-venue", "binance_perp",
        "--source-symbols", args.symbols,
        "--target-venues", args.target_venues,
        "--target-symbols", ",".join(target_symbols),
        "--duration-seconds", str(args.duration_seconds),
        "--out", str(attempt_dir / "data"),
    ]

    if args.capture_open_interest:
        cmd.append("--capture-open-interest")
        cmd.append("--open-interest-interval-seconds")
        cmd.append(str(args.open_interest_interval_seconds))

    # Capture mode from volatility gate
    if gate_result and gate_result.get("gate_passed"):
        mv = gate_result.get("market_verdict", "")
        if mv in (MARKET_ACTIVE, MARKET_ALT_ACTIVE):
            cmd.extend(["--capture-mode", "FULL_ACTIVE"])
        else:
            cmd.extend(["--capture-mode", "FAST_DIAGNOSTIC"])
    else:
        cmd.extend(["--capture-mode", ""])

    command_str = " ".join(cmd)

    # --- Run the subprocess -------------------------------------------------
    started_at = _ts_now_iso()
    print(f"  [ATTEMPT {attempt_index:04d}] Starting: {command_str}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=args.duration_seconds + 120,  # grace period
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        ended_at = _ts_now_iso()
        result = {
            "attempt_index": attempt_index,
            "run_id": attempt_run_id,
            "status": ATTEMPT_FAILED,
            "command": command_str,
            "log_path": str(attempt_log.resolve()),
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "gate_snapshot": gate_result,
            "started_at": started_at,
            "ended_at": ended_at,
            "error": "subprocess_timeout",
        }
        _write_attempt_log(attempt_log, result)
        print(f"  [ATTEMPT {attempt_index:04d}] FAILED (timeout)")
        return result

    except Exception as exc:
        ended_at = _ts_now_iso()
        result = {
            "attempt_index": attempt_index,
            "run_id": attempt_run_id,
            "status": ATTEMPT_FAILED,
            "command": command_str,
            "log_path": str(attempt_log.resolve()),
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "gate_snapshot": gate_result,
            "started_at": started_at,
            "ended_at": ended_at,
            "error": f"subprocess_spawn_failed: {exc}",
        }
        _write_attempt_log(attempt_log, result)
        print(f"  [ATTEMPT {attempt_index:04d}] FAILED (spawn error: {exc})")
        return result

    ended_at = _ts_now_iso()

    if proc.returncode == 0:
        status = ATTEMPT_COMPLETED
        print(f"  [ATTEMPT {attempt_index:04d}] COMPLETED (rc={proc.returncode})")
    else:
        status = ATTEMPT_FAILED
        print(f"  [ATTEMPT {attempt_index:04d}] FAILED (rc={proc.returncode})")

    result = {
        "attempt_index": attempt_index,
        "run_id": attempt_run_id,
        "status": status,
        "command": command_str,
        "log_path": str(attempt_log.resolve()),
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "gate_snapshot": gate_result,
        "started_at": started_at,
        "ended_at": ended_at,
        "error": None if proc.returncode == 0 else f"subprocess_exit_{proc.returncode}",
    }

    _write_attempt_log(attempt_log, result)
    return result


def _write_attempt_log(log_path: Path, attempt_result: dict[str, Any]) -> None:
    """Write the attempt result (including stdout/stderr) to a log file."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"ATTEMPT {attempt_result['attempt_index']:04d}")
    lines.append(f"Run ID:    {attempt_result['run_id']}")
    lines.append(f"Status:    {attempt_result['status']}")
    lines.append(f"Start:     {attempt_result['started_at']}")
    lines.append(f"End:       {attempt_result['ended_at']}")
    lines.append(f"Command:   {attempt_result['command']}")
    lines.append(f"Return:    {attempt_result['returncode']}")
    if attempt_result.get("error"):
        lines.append(f"Error:     {attempt_result['error']}")
    lines.append("")
    lines.append("--- STDOUT ---")
    lines.append(attempt_result.get("stdout", "") or "")
    lines.append("--- STDERR ---")
    lines.append(attempt_result.get("stderr", "") or "")
    lines.append("=" * 70)
    lines.append("")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    campaign_id: str = args.campaign_id
    num_captures: int = args.captures
    reports_base = Path(args.reports_base).resolve()
    out_base = Path(args.out_base).resolve()

    # -----------------------------------------------------------------------
    # Campaign directory
    # -----------------------------------------------------------------------
    campaign_dir_name = f"derivatives_capture_campaign_{campaign_id}"
    campaign_dir = reports_base / campaign_dir_name

    try:
        safe_output_dir(campaign_dir, allow_existing=args.allow_existing_output)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Use --allow-existing-output to reuse an existing campaign directory.", file=sys.stderr)
        sys.exit(1)

    # Campaign-wide run ID
    campaign_run_id = create_run_id(prefix=f"campaign_{campaign_id}")

    print("=" * 70)
    print("DERIVATIVES CAPTURE CAMPAIGN")
    print("=" * 70)
    print(f"  campaign_id:          {campaign_id}")
    print(f"  campaign_run_id:      {campaign_run_id}")
    print(f"  captures:             {num_captures}")
    print(f"  duration_seconds:     {args.duration_seconds}")
    print(f"  sleep_seconds:        {args.sleep_seconds}")
    print(f"  symbols:              {args.symbols}")
    print(f"  target_venues:        {args.target_venues}")
    print(f"  use_volatility_gate:  {args.use_volatility_gate}")
    if args.use_volatility_gate:
        print(f"  volatility_threshold: {args.volatility_threshold_bps} bps")
        print(f"  max_skip_streak:      {args.max_skip_streak}")
    print(f"  capture_open_interest: {args.capture_open_interest}")
    print(f"  output base:          {out_base}")
    print(f"  campaign dir:         {campaign_dir}")
    print(f"  python executable:    {args.python_executable}")
    print()

    # -----------------------------------------------------------------------
    # Campaign state
    # -----------------------------------------------------------------------
    campaign_started_at = _ts_now_iso()
    campaign_status = CAMPAIGN_RUNNING
    attempts: list[dict[str, Any]] = []
    consecutive_skips = 0
    campaign_end_reason: str | None = None

    # -----------------------------------------------------------------------
    # Attempt loop
    # -----------------------------------------------------------------------
    for attempt_index in range(1, num_captures + 1):
        print(f"--- Attempt {attempt_index}/{num_captures} ---")

        # --- Volatility gate check -----------------------------------------
        gate_result: dict[str, Any] | None = None
        skip_attempt = False

        if args.use_volatility_gate:
            print(f"  [GATE] Running volatility gate (threshold={args.volatility_threshold_bps} bps)...")
            gate_result = _run_volatility_gate(args.volatility_threshold_bps)

            if not gate_result.get("gate_passed", False):
                mv = gate_result.get("market_verdict", "?")
                av = gate_result.get("accel_verdict", "?")
                btc_3h = gate_result.get("btc_3h_bps", 0)
                err = gate_result.get("error")
                if err:
                    print(f"  [GATE] Gate error: {err}")
                else:
                    print(f"  [GATE] Gate FAILED — market_verdict={mv}, accel_verdict={av}, btc_3h={btc_3h} bps")
                skip_attempt = True
            else:
                mv = gate_result.get("market_verdict", "?")
                av = gate_result.get("accel_verdict", "?")
                btc_3h = gate_result.get("btc_3h_bps", 0)
                print(f"  [GATE] Gate PASSED — market_verdict={mv}, accel_verdict={av}, btc_3h={btc_3h} bps")
        else:
            print("  [GATE] Volatility gate disabled, proceeding with capture")

        # --- Handle skip vs. run -------------------------------------------
        if skip_attempt:
            # Record skipped attempt
            skip_run_id = create_run_id(prefix=f"campaign_{campaign_id}_skip_{attempt_index:04d}")
            skipped_at = _ts_now_iso()
            attempt_result = {
                "attempt_index": attempt_index,
                "run_id": skip_run_id,
                "status": ATTEMPT_SKIPPED_LOW_VOLATILITY,
                "command": "",
                "log_path": str(campaign_dir / f"attempt_{attempt_index:04d}" / f"attempt_{attempt_index:04d}.log"),
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "gate_snapshot": gate_result,
                "started_at": skipped_at,
                "ended_at": skipped_at,
                "error": "volatility_gate_not_passed",
            }
            attempts.append(attempt_result)
            consecutive_skips += 1

            print(f"  [ATTEMPT {attempt_index:04d}] SKIPPED (volatility gate not passed, "
                  f"consecutive_skips={consecutive_skips}/{args.max_skip_streak})")

            # Check abort condition
            if consecutive_skips >= args.max_skip_streak:
                campaign_status = CAMPAIGN_ABORTED_LOW_VOLATILITY
                campaign_end_reason = (
                    f"Aborted after {consecutive_skips} consecutive skips "
                    f"(max_skip_streak={args.max_skip_streak}). "
                    f"Volatility gate did not pass for {consecutive_skips} consecutive attempts."
                )
                print(f"  [CAMPAIGN] {campaign_status}: {campaign_end_reason}")
                break
        else:
            # Reset skip counter on a successful run attempt
            consecutive_skips = 0

            attempt_result = _run_single_attempt(
                attempt_index=attempt_index,
                args=args,
                campaign_dir=campaign_dir,
                campaign_id=campaign_id,
                run_id=campaign_run_id,
                gate_result=gate_result,
            )
            attempts.append(attempt_result)

        # --- Sleep between attempts ----------------------------------------
        if attempt_index < num_captures and not skip_attempt and args.sleep_seconds > 0:
            print(f"  Sleeping {args.sleep_seconds}s before next attempt...")
            time.sleep(args.sleep_seconds)

        print()

    # -----------------------------------------------------------------------
    # Determine final campaign status
    # -----------------------------------------------------------------------
    campaign_ended_at = _ts_now_iso()
    if campaign_status == CAMPAIGN_RUNNING:
        # Check if we completed all or if some failed
        completed_count = sum(1 for a in attempts if a["status"] == ATTEMPT_COMPLETED)
        sum(1 for a in attempts if a["status"] == ATTEMPT_FAILED)
        skipped_count = sum(1 for a in attempts if a["status"] == ATTEMPT_SKIPPED_LOW_VOLATILITY)

        if completed_count + skipped_count == len(attempts):
            campaign_status = CAMPAIGN_COMPLETED
        else:
            campaign_status = CAMPAIGN_FAILED

    if campaign_end_reason is None:
        campaign_end_reason = f"Campaign finished with {sum(1 for a in attempts if a['status'] == ATTEMPT_COMPLETED)} completed, {sum(1 for a in attempts if a['status'] == ATTEMPT_FAILED)} failed, {sum(1 for a in attempts if a['status'] == ATTEMPT_SKIPPED_LOW_VOLATILITY)} skipped out of {len(attempts)} total attempts."

    # -----------------------------------------------------------------------
    # Build campaign manifest
    # -----------------------------------------------------------------------
    manifest: dict[str, Any] = {
        "campaign_id": campaign_id,
        "campaign_run_id": campaign_run_id,
        "status": campaign_status,
        "started_at": campaign_started_at,
        "ended_at": campaign_ended_at,
        "total_attempts_requested": num_captures,
        "total_attempts_executed": len(attempts),
        "completed_count": sum(1 for a in attempts if a["status"] == ATTEMPT_COMPLETED),
        "failed_count": sum(1 for a in attempts if a["status"] == ATTEMPT_FAILED),
        "skipped_count": sum(1 for a in attempts if a["status"] == ATTEMPT_SKIPPED_LOW_VOLATILITY),
        "end_reason": campaign_end_reason,
        "configuration": {
            "duration_seconds": args.duration_seconds,
            "sleep_seconds": args.sleep_seconds,
            "symbols": args.symbols,
            "target_venues": args.target_venues,
            "use_volatility_gate": args.use_volatility_gate,
            "volatility_threshold_bps": args.volatility_threshold_bps,
            "max_skip_streak": args.max_skip_streak,
            "capture_open_interest": args.capture_open_interest,
            "open_interest_interval_seconds": args.open_interest_interval_seconds,
            "python_executable": str(args.python_executable),
            "allow_existing_output": args.allow_existing_output,
        },
        "attempts": [],
        "methodology": {
            "pattern": "repeated_derivatives_source_to_spot_target_subprocess_capture",
            "isolation": "subprocess (subprocess.run with capture_output)",
            "child_failure_isolation": "one failed child process does not crash the campaign",
            "volatility_gate": {
                "enabled": args.use_volatility_gate,
                "source": "Kraken OHLC hourly bars (recent past only)",
                "caveat": (
                    "Volatility gate uses recent-past OHLC data (Kraken hourly bars) "
                    "as a proxy for market regime. This is NOT forward-looking and does "
                    "NOT guarantee future volatility or capture quality. Gate pass conditions: "
                    "(1) market_verdict in MARKET_ACTIVE/MARKET_ALT_ACTIVE AND accel_verdict == ACCELERATING, "
                    "or (2) BTC/USD 3h range >= volatility_threshold_bps."
                ),
                "consecutive_skip_abort": {
                    "max_skip_streak": args.max_skip_streak,
                    "behavior": f"Campaign aborts with {CAMPAIGN_ABORTED_LOW_VOLATILITY} status when consecutive skips exceed max_skip_streak.",
                },
            },
            "capture_script": "examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture",
        },
    }

    # Add attempt details (strip large stdout/stderr from manifest — keep in log files)
    for a in attempts:
        attempt_entry = {k: v for k, v in a.items() if k not in ("stdout", "stderr")}
        manifest["attempts"].append(attempt_entry)

    # Inject metadata block
    manifest["_metadata"] = build_metadata(
        capture_mode="",
        run_args=args,
        volatility_gate_snapshot=None,  # Per-attempt snapshots are in the attempt entries
        preflight_summary=None,
    )

    # -----------------------------------------------------------------------
    # Write campaign manifest atomically
    # -----------------------------------------------------------------------
    manifest_path = campaign_dir / "campaign_manifest.json"
    atomic_write_json(manifest_path, manifest)
    print(f"Campaign manifest written to: {manifest_path}")

    # -----------------------------------------------------------------------
    # Write run index rows
    # -----------------------------------------------------------------------
    # 1) Campaign-level started row
    campaign_start_row = build_run_index_row(
        run_id=campaign_run_id,
        run_type="campaign",
        status="started",
        command_args=f"{sys.executable} {' '.join(sys.argv)}",
        report_dir=str(campaign_dir),
        campaign_id=campaign_id,
        notes=f"Campaign {campaign_id} started at {campaign_started_at}",
    )
    append_run_index_row(campaign_start_row)

    # 2) Campaign-level final status row
    campaign_status_row = build_run_index_row(
        run_id=campaign_run_id,
        run_type="campaign",
        status=campaign_status.split("_LOW_VOLATILITY")[0].lower() if campaign_status == CAMPAIGN_ABORTED_LOW_VOLATILITY else campaign_status.lower(),
        command_args=f"{sys.executable} {' '.join(sys.argv)}",
        report_dir=str(campaign_dir),
        manifest_path=str(manifest_path),
        campaign_id=campaign_id,
        notes=campaign_end_reason,
    )
    append_run_index_row(campaign_status_row)

    # 3) Per-attempt rows
    for a in attempts:
        attempt_status = a["status"]
        # Map custom statuses to valid run-index statuses
        if attempt_status == ATTEMPT_SKIPPED_LOW_VOLATILITY:
            mapped_status = "skipped"
        else:
            mapped_status = attempt_status

        attempt_row = build_run_index_row(
            run_id=a["run_id"],
            run_type="capture",
            status=mapped_status,
            command_args=a.get("command") or "",
            report_dir=str(campaign_dir),
            output_dir=str(Path(a.get("log_path", "")).parent.resolve()) if a.get("log_path") else None,
            parent_run_id=campaign_run_id,
            campaign_id=campaign_id,
            notes=f"Attempt {a['attempt_index']}: {attempt_status}"
                  + (f" (error: {a.get('error', '')})" if a.get("error") else ""),
            errors=a.get("error") if attempt_status == ATTEMPT_FAILED else None,
            extra={
                "attempt_index": a["attempt_index"],
                "gate_snapshot": a.get("gate_snapshot"),
            } if a.get("gate_snapshot") else {
                "attempt_index": a["attempt_index"],
            },
        )
        append_run_index_row(attempt_row)

    # -----------------------------------------------------------------------
    # Print summary
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("CAMPAIGN SUMMARY")
    print("=" * 70)
    print(f"  Campaign ID:       {campaign_id}")
    print(f"  Campaign Run ID:   {campaign_run_id}")
    print(f"  Status:            {campaign_status}")
    print(f"  Started:           {campaign_started_at}")
    print(f"  Ended:             {campaign_ended_at}")
    print(f"  Attempts:          {len(attempts)} / {num_captures} requested")
    print(f"    Completed:       {sum(1 for a in attempts if a['status'] == ATTEMPT_COMPLETED)}")
    print(f"    Failed:          {sum(1 for a in attempts if a['status'] == ATTEMPT_FAILED)}")
    print(f"    Skipped:         {sum(1 for a in attempts if a['status'] == ATTEMPT_SKIPPED_LOW_VOLATILITY)}")
    print(f"  Manifest:          {manifest_path}")
    print(f"  Max consecutive skips: {consecutive_skips}")
    print()

    for a in attempts:
        icon = {
            ATTEMPT_COMPLETED: "OK",
            ATTEMPT_FAILED: "FAIL",
            ATTEMPT_SKIPPED_LOW_VOLATILITY: "SKIP",
        }.get(a["status"], "?")
        print(f"  [{icon}] Attempt {a['attempt_index']:04d}: {a['status']}  "
              f"(run_id={a['run_id']})")
        if a.get("error"):
            print(f"         error={a['error']}")

    print()
    print(f"Campaign manifest: {manifest_path}")


if __name__ == "__main__":
    main()
