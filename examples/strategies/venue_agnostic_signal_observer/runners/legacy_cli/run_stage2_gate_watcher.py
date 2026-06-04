#!/usr/bin/env python3
"""Stage 2 Gate Watcher for derivatives-source -> spot-target research.

Repeatedly runs the volatility gate and automatically starts the correct
derivatives v2 workflow only when capture is explicitly permitted.

Public-data observer only. No auth, no orders, no private keys, no execution.
"""

from __future__ import annotations

import argparse
from ._prog import set_legacy_prog
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path("reports", "stage2_gate_watcher_logs")
_LOCK_PATH = Path("reports", "stage2_collection.lock")
_VOLATILITY_GATE_MODULE = "examples.strategies.volatility_gate"
_CAPTURE_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_derivatives_spot_capture"
_EVALUATION_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_derivatives_spot_lead_lag"
_COST_SENSITIVITY_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_cost_sensitivity"
_HEATMAP_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_lead_lag_heatmap"
_PERMUTATION_NULL_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_permutation_null"
_MCPT_EXPORT_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_mcpt_export"
_CONSISTENCY_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_cross_capture_consistency"
_FALSIFICATION_MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_candidate_falsification"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

class WatcherLogger:
    """Dual-output logger: structured plain log + JSON summary."""

    def __init__(self, log_dir: Path, ts: str):
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"stage2_gate_watcher_{ts}.log"
        self._summary_path = log_dir / f"stage2_gate_watcher_{ts}_summary.json"
        self._handle = open(self._log_path, "w", encoding="utf-8")  # noqa: SIM115
        self._entries: list[dict[str, Any]] = []
        self._final_data: dict[str, Any] | None = None
        self._closed = False

    def _write(self, msg: str) -> None:
        print(msg, file=self._handle, flush=True)

    def env(self, key: str, value: str) -> None:
        data = {"event": "env", "key": key, "value": value}
        self._entries.append(data)
        self._write(f"[ENV] {key}={value}")

    def gate_check(self, check_num: int, permission: str, reason: str) -> None:
        data = {"event": "gate_check", "check_num": check_num,
                "permission": permission, "reason": reason}
        self._entries.append(data)
        self._write(f"[GATE #{check_num}] permission={permission} reason={reason}")

    def action(self, msg: str) -> None:
        data = {"event": "action", "message": msg}
        self._entries.append(data)
        self._write(f"[ACTION] {msg}")

    def cmd(self, description: str, cmd: list[str], rc: int | None,
            out_dir: str | None = None) -> None:
        data = {"event": "cmd", "description": description, "command": " ".join(cmd),
                "return_code": rc, "output_dir": out_dir}
        self._entries.append(data)
        self._write(f"[CMD] {description}: rc={rc} out={out_dir or 'N/A'}")

    def warning(self, msg: str) -> None:
        data = {"event": "warning", "message": msg}
        self._entries.append(data)
        self._write(f"[WARN] {msg}")

    def finalize(self, verdict: str, data: dict[str, Any]) -> None:
        combined = {"final_verdict": verdict, "environment": data, "log_entries": self._entries}
        self._final_data = combined
        self._write(f"[FINAL] verdict={verdict}")

    def close(self) -> Path | None:
        if self._closed:
            return None
        self._closed = True
        self._handle.close()
        if self._final_data:
            with open(self._summary_path, "w", encoding="utf-8") as f:
                json.dump(self._final_data, f, indent=2, default=str)
            return self._summary_path
        return None


# ---------------------------------------------------------------------------
# Volatility gate runner
# ---------------------------------------------------------------------------

def run_volatility_gate(python_exe: str) -> dict[str, Any]:
    """Run volatility_gate.py as a subprocess and return parsed JSON."""
    result = subprocess.run(
        [python_exe, "-m", _VOLATILITY_GATE_MODULE],
        capture_output=True, text=True, timeout=30,
        cwd=Path.cwd(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"volatility_gate failed (rc={result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    # Parse JSON from stdout (the last JSON block)
    lines = result.stdout.strip().split("\n")
    json_lines = [l for l in lines if l.startswith("{") or l.startswith("  ") or l.startswith("}")]
    raw = "\n".join(json_lines)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Preflight validation
# ---------------------------------------------------------------------------

def validate_preflight(capture_dir: str) -> bool:
    """Inspect preflight capture output for minimum viability.

    Pass criteria:
    - All three Binance perp source streams have non-zero ticks
    - At least one target venue per symbol has non-zero ticks
    - OI snapshots exist for BTCUSDT, ETHUSDT, SOLUSDT
    - global overlap > 0
    - no full subscription failure
    """
    capture_path = Path(capture_dir)
    manifest_path = capture_path / "capture_manifest.json"
    if not manifest_path.exists():
        return False

    with open(manifest_path) as f:
        manifest = json.load(f)

    overlap = manifest.get("global_timestamp_overlap_seconds", 0)
    if not (overlap or 0) > 0:
        return False

    streams = manifest.get("streams", [])
    binance_source_streams = [s for s in streams
                              if s.get("source") == "binance_perp"
                              and s.get("type") == "trade"]
    target_streams = [s for s in streams
                      if s.get("source") in ("kraken", "coinbase")
                      and s.get("type") == "trade"]

    # All three Binance perp streams must have non-zero ticks
    binance_symbols_ok = any(s.get("symbol", "").startswith("BTC") and (s.get("count", 0) or 0) > 0
                              for s in binance_source_streams)
    binance_eth_ok = any(s.get("symbol", "").startswith("ETH") and (s.get("count", 0) or 0) > 0
                          for s in binance_source_streams)
    binance_sol_ok = any(s.get("symbol", "").startswith("SOL") and (s.get("count", 0) or 0) > 0
                          for s in binance_source_streams)

    if not (binance_symbols_ok and binance_eth_ok and binance_sol_ok):
        return False

    # At least one target venue per symbol has ticks
    target_by_symbol: dict[str, list] = {}
    for s in target_streams:
        sym = s.get("symbol", "")
        target_by_symbol.setdefault(sym, []).append(s)
    needed = {"BTC", "ETH", "SOL"}
    for prefix in needed:
        matched = False
        for sym, tstreams in target_by_symbol.items():
            if sym.startswith(prefix) and any((ts.get("count", 0) or 0) > 0 for ts in tstreams):
                matched = True
                break
        if not matched:
            return False

    # OI snapshots
    oi_streams = [s for s in streams if s.get("type") == "open_interest"]
    oi_symbols = {s.get("symbol", "") for s in oi_streams}
    for oi_sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        if not any(oi_sym in s for s in oi_symbols):
            return False

    # Check for subscription failure indicator
    for s in streams:
        if s.get("error") and "subscription" in str(s.get("error", "")).lower():
            return False

    return True


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------

def _run_module(python_exe: str, module: str, args: list[str],
                description: str, timeout: int | None = None) -> tuple[int, str]:
    """Run a module subprocess and return (returncode, combined_stdout)."""
    cmd = [python_exe, "-m", module] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=Path.cwd(),
        )
    except subprocess.TimeoutExpired as e:
        return -1, f"TIMEOUT after {timeout}s"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _check_cuda(python_exe: str) -> bool:
    """Check if CUDA is available via subprocess."""
    try:
        result = subprocess.run(
            [python_exe, "-c", "import torch; print(torch.cuda.is_available())"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "True"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Lock management
# ---------------------------------------------------------------------------

def check_lock(log: WatcherLogger) -> tuple[bool, str]:
    """Check if collection lock exists. Returns (locked, message)."""
    if _LOCK_PATH.exists():
        try:
            with open(_LOCK_PATH) as f:
                lock_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            lock_data = {}
        pid = lock_data.get("pid", "unknown")
        mode = lock_data.get("mode", "unknown")
        ts = lock_data.get("created_at_utc", "unknown")
        return True, (
            f"COLLECTION_LOCK_EXISTS: pid={pid} mode={mode} created={ts}. "
            f"Remove {_LOCK_PATH} to force re-collection."
        )
    return False, ""


def create_lock(log: WatcherLogger, permission: str, reason: str,
                mode: str) -> None:
    """Create the collection lock file."""
    import subprocess as _sp
    git_sha = "unknown"
    try:
        git_sha = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path.cwd(),
        ).stdout.strip()
    except Exception:
        pass

    lock_data = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pid": os.getpid(),
        "capture_permission": permission,
        "fast_capture_reason": reason,
        "git_sha": git_sha,
        "mode": mode,
    }
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_PATH, "w") as f:
        json.dump(lock_data, f, indent=2)
    log.action(f"Created lock: {_LOCK_PATH} (mode={mode})")


# ---------------------------------------------------------------------------
# Desktop notifications
# ---------------------------------------------------------------------------


def send_desktop_notification(
    *,
    enabled: bool,
    command: str,
    app_name: str,
    title: str,
    body: str,
    urgency: str,
    timeout_ms: int,
    log: WatcherLogger,
) -> bool:
    """Send a Wayland desktop notification via notify-send.

    Best-effort only. Returns True on success, False if disabled, missing,
    timed out, or non-zero exit. Never raises.
    """
    if not enabled:
        return False
    try:
        result = subprocess.run(
            [
                command,
                "--app-name", app_name,
                "--urgency", urgency,
                "--expire-time", str(timeout_ms),
                title,
                body,
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            log.warning(
                f"Notification command returned non-zero ({result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
            return False
        return True
    except FileNotFoundError:
        log.warning(
            f"Notification command '{command}' not found. "
            f"Install libnotify: sudo pacman -S libnotify"
        )
        return False
    except subprocess.TimeoutExpired:
        log.warning(f"Notification command '{command}' timed out after 5s")
        return False
    except Exception as e:
        log.warning(f"Notification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Audio alerts
# ---------------------------------------------------------------------------


def play_sound(
    *,
    enabled: bool,
    command: str,
    sound_file: str,
    log: WatcherLogger,
) -> bool:
    """Play an audio file via pw-play/paplay/aplay.

    Best-effort only. Returns True on success, False if disabled, file missing,
    command missing, or non-zero exit. Never raises.
    """
    if not enabled:
        return False
    if not os.path.isfile(sound_file):
        log.warning(f"Sound file not found: {sound_file}")
        return False
    try:
        result = subprocess.run(
            [command, sound_file],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            log.warning(f"Sound command returned non-zero ({result.returncode}): {result.stderr or result.stdout}")
            return False
        return True
    except FileNotFoundError:
        log.warning(f"Sound command '{command}' not found (install pw-play/paplay or set --sound-command)")
        return False
    except subprocess.TimeoutExpired:
        log.warning(f"Sound command '{command}' timed out after 5s")
        return False
    except Exception as e:
        log.warning(f"Sound playback failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

def _parse_capture_mode_dir(mode: str, ts: str) -> str:
    """Return the output directory name for a capture run."""
    if mode == "FULL_ACTIVE":
        return f"data/derivatives_spot_capture_v2_ACTIVE_{ts}"
    return f"data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_{ts}"


def _parse_evaluation_mode_dir(mode: str, ts: str) -> str:
    """Return the evaluation output directory name."""
    if mode == "FULL_ACTIVE":
        return f"reports/derivatives_spot_lead_lag_v2_ACTIVE_{ts}"
    return f"reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_{ts}"


def run_preflight(python_exe: str, log: WatcherLogger, ts: str,
                  args: argparse.Namespace) -> str | None:
    """Run a 60s preflight capture. Returns capture_dir on success, None on failure."""
    preflight_dir = f"data/derivatives_spot_capture_v2_PREFLIGHT_{ts}"
    capture_args = [
        "--source-venue", "binance_perp",
        "--source-symbols", "BTC/USDT,ETH/USDT,SOL/USDT",
        "--target-venues", "kraken,coinbase",
        "--target-symbols", "BTC/USD,ETH/USD,SOL/USD",
        "--duration-seconds", str(args.preflight_seconds),
        "--capture-open-interest",
        "--open-interest-interval-seconds", str(args.oi_interval_seconds),
        "--capture-mode", "FAST_DIAGNOSTIC",  # no PREFLIGHT mode exists; use FAST_DIAGNOSTIC
        "--out", preflight_dir,
    ]

    log.action(f"Starting preflight: {preflight_dir}")
    rc, output = _run_module(
        python_exe, _CAPTURE_MODULE, capture_args,
        "preflight capture", timeout=args.preflight_seconds + 60,
    )
    log.cmd("preflight capture", capture_args, rc, preflight_dir)

    if rc != 0:
        log.action("Preflight FAILED (non-zero return code)")
        return None

    # Validate the preflight output
    if not validate_preflight(preflight_dir):
        log.action("Preflight FAILED (validation criteria not met)")
        return None

    log.action("Preflight PASSED")
    return preflight_dir


def run_capture(python_exe: str, log: WatcherLogger, ts: str,
                mode: str, duration: int,
                args: argparse.Namespace) -> str | None:
    """Run the real capture. Returns capture_dir on success, None on failure."""
    capture_dir = _parse_capture_mode_dir(mode, ts)
    capture_args = [
        "--source-venue", "binance_perp",
        "--source-symbols", "BTC/USDT,ETH/USDT,SOL/USDT",
        "--target-venues", "kraken,coinbase",
        "--target-symbols", "BTC/USD,ETH/USD,SOL/USD",
        "--duration-seconds", str(duration),
        "--capture-open-interest",
        "--open-interest-interval-seconds", str(args.oi_interval_seconds),
        "--capture-mode", mode,
        "--out", capture_dir,
    ]

    log.action(f"Starting capture (mode={mode}): {capture_dir}")
    rc, output = _run_module(
        python_exe, _CAPTURE_MODULE, capture_args,
        f"{mode} capture", timeout=duration + 120,
    )
    log.cmd(f"{mode} capture", capture_args, rc, capture_dir)

    if rc != 0:
        log.action("Capture FAILED")
        return None
    log.action("Capture completed successfully")
    return capture_dir


def run_evaluation(python_exe: str, log: WatcherLogger, ts: str,
                   capture_dir: str, mode: str,
                   args: argparse.Namespace) -> str | None:
    """Run lead-lag evaluation on captured data. Returns report_dir on success."""
    report_dir = _parse_evaluation_mode_dir(mode, ts)
    cuda_ok = _check_cuda(python_exe)
    forward_engine = "gpu" if (cuda_ok and args.prefer_gpu) else "cpu"
    forward_device = args.gpu_device if forward_engine == "gpu" else ""

    eval_args = [
        "--capture-dir", capture_dir,
        "--source-venues", "binance_perp",
        "--target-venues", "kraken,coinbase",
        "--symbols", "BTC/USD,ETH/USD,SOL/USD",
        "--signal-types", "notional_burst,large_trade,signed_imbalance",
        "--lookbacks-ms", "1000,5000,10000,30000",
        "--baseline-window-ms", "60000",
        "--horizons-ms", "1000,2000,5000,10000,30000,60000,300000",
        "--cooldown-ms", "10000",
        "--fee-bps", "40",
        "--slippage-bps", "5",
        "--quote-mismatch-buffer-bps", "5",
        "--min-events", "50",
        "--capture-mode", mode,
        "--forward-engine", forward_engine,
        "--out", report_dir,
    ]
    if forward_device:
        eval_args.extend(["--forward-device", forward_device])

    log.action(f"Starting evaluation: {report_dir} (engine={forward_engine})")
    rc, output = _run_module(
        python_exe, _EVALUATION_MODULE, eval_args,
        f"{mode} evaluation", timeout=3600,
    )
    log.cmd(f"{mode} evaluation", eval_args, rc, report_dir)

    if rc != 0:
        log.action("Evaluation FAILED")
        return None
    log.action("Evaluation completed successfully")
    return report_dir


def run_post_evaluation_diagnostics(python_exe: str, log: WatcherLogger,
                                     capture_dir: str, report_dir: str,
                                     mode: str,
                                     args: argparse.Namespace) -> None:
    """Run diagnostic tools after evaluation completes successfully."""
    cuda_ok = _check_cuda(python_exe) and args.prefer_gpu
    gpu_device = args.gpu_device

    # 1. Cost sensitivity
    cost_out = f"{report_dir}/cost_sensitivity"
    cost_args = [
        "--report-dir", report_dir,
        "--out", cost_out,
    ]
    log.action(f"Running cost sensitivity: {cost_out}")
    rc, _ = _run_module(
        python_exe, _COST_SENSITIVITY_MODULE, cost_args,
        "cost sensitivity", timeout=600,
    )
    log.cmd("cost sensitivity", cost_args, rc, cost_out)
    if rc != 0:
        log.warning("cost sensitivity reported a non-zero exit code")

    # 2. Lead/lag heatmap
    heatmap_engine = "gpu" if cuda_ok else "cpu"
    heatmap_device = gpu_device if heatmap_engine == "gpu" else ""
    heatmap_out = f"{report_dir}/lead_lag_heatmap_{heatmap_engine}"
    heatmap_args = [
        "--capture-dir", capture_dir,
        "--out", heatmap_out,
        "--engine", heatmap_engine,
        "--device", heatmap_device,
    ]
    log.action(f"Running heatmap (engine={heatmap_engine}): {heatmap_out}")
    rc, _ = _run_module(
        python_exe, _HEATMAP_MODULE, heatmap_args,
        f"lead/lag heatmap ({heatmap_engine})", timeout=1800,
    )
    log.cmd(f"lead/lag heatmap ({heatmap_engine})", heatmap_args, rc, heatmap_out)
    if rc != 0:
        log.warning("heatmap reported a non-zero exit code")

    # 3. Permutation null
    perm_engine = "gpu" if cuda_ok else "cpu"
    perm_out = f"{report_dir}/permutation_null_{perm_engine}"
    perm_args = [
        "--capture-dir", capture_dir,
        "--report-dir", report_dir,
        "--out", perm_out,
        "--iterations", "10000",
        "--seed", "42",
        "--shift-mode", "circular_time_shift",
        "--engine", perm_engine,
        "--device", gpu_device if perm_engine == "gpu" else "",
        "--batch-size", "512",
        "--min-events", "50",
        "--cost-floor-bps", "50",
    ]
    log.action(f"Running permutation null (engine={perm_engine}): {perm_out}")
    rc, _ = _run_module(
        python_exe, _PERMUTATION_NULL_MODULE, perm_args,
        f"permutation null ({perm_engine})", timeout=3600,
    )
    log.cmd(f"permutation null ({perm_engine})", perm_args, rc, perm_out)
    if rc != 0:
        log.warning("permutation null reported a non-zero exit code")

    # 4. MCPT export (if inputs exist)
    mcpt_out = f"{report_dir}/mcpt_inputs"
    mcpt_args = [
        "--report-dir", report_dir,
        "--out", mcpt_out,
        "--min-events", "50",
        "--cost-floor-bps", "50",
    ]
    log.action(f"Running MCPT export: {mcpt_out}")
    rc, _ = _run_module(
        python_exe, _MCPT_EXPORT_MODULE, mcpt_args,
        "MCPT export", timeout=300,
    )
    log.cmd("MCPT export", mcpt_args, rc, mcpt_out)
    if rc != 0:
        log.warning("MCPT export reported a non-zero exit code (likely no candidates)")

    # 5. Cross-capture consistency (if at least one previous report exists)
    previous_reports = sorted(
        Path("reports").glob("derivatives_spot_lead_lag_v2_*"),
        reverse=True,
    )
    previous = [str(p) for p in previous_reports if str(p) != report_dir]
    if previous:
        all_dirs = previous[:3] + [report_dir]
        consistency_out = f"{report_dir}/cross_capture_consistency"
        consistency_args = [
            "--report-dirs"] + all_dirs + [
            "--out", consistency_out,
            "--min-captures", "2",
        ]
        log.action(f"Running cross-capture consistency: {consistency_out}")
        rc, _ = _run_module(
            python_exe, _CONSISTENCY_MODULE, consistency_args,
            "cross-capture consistency", timeout=600,
        )
        log.cmd("cross-capture consistency", consistency_args, rc, consistency_out)
        if rc != 0:
            log.warning("cross-capture consistency reported a non-zero exit code")
    else:
        log.warning("cross-capture consistency: no previous reports to compare with (SKIPPED)")

    # 6. Candidate falsification (if required inputs exist)
    falsification_args = [
        "--evaluated-report-dir", report_dir,
        "--cost-sensitivity-dir", cost_out,
        "--permutation-null-dir", perm_out,
        "--heatmap-dir", heatmap_out,
        "--consistency-dir", f"{report_dir}/cross_capture_consistency",
        "--out", f"{report_dir}/candidate_falsification",
        "--viability-cost-bps", "50",
        "--min-events", "50",
    ]
    log.action(f"Running candidate falsification: {report_dir}/candidate_falsification")
    rc, _ = _run_module(
        python_exe, _FALSIFICATION_MODULE, falsification_args,
        "candidate falsification", timeout=600,
    )
    log.cmd("candidate falsification", falsification_args, rc,
            f"{report_dir}/candidate_falsification")
    if rc != 0:
        log.warning("candidate falsification reported a non-zero exit code (missing inputs?)")


# ---------------------------------------------------------------------------
# Environment snapshot
# ---------------------------------------------------------------------------

def _env_snapshot(log: WatcherLogger) -> dict[str, Any]:
    """Record environment metadata."""
    import subprocess as _sp
    data: dict[str, Any] = {
        "pwd": str(Path.cwd()),
        "start_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python_exe": sys.executable,
    }
    log.env("pwd", data["pwd"])
    log.env("start_utc", data["start_utc"])
    log.env("python_exe", sys.executable)

    try:
        branch = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        data["git_branch"] = branch
        log.env("git_branch", branch)
    except Exception:
        data["git_branch"] = "unknown"

    try:
        sha = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        data["git_sha"] = sha
        log.env("git_sha", sha)
    except Exception:
        data["git_sha"] = "unknown"

    try:
        status = _sp.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        data["git_dirty"] = bool(status)
        log.env("git_dirty", str(bool(status)))
    except Exception:
        data["git_dirty"] = True

    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 2 Gate Watcher: poll volatility gate and auto-start derivatives v2 pipeline.",
    )
    set_legacy_prog(p)
    p.add_argument("--interval-seconds", type=int, default=30,
                    help="Seconds between gate checks. Default: 30.")
    p.add_argument("--max-runtime-seconds", type=int, default=0,
                    help="Maximum runtime before forced exit. 0 = run indefinitely. Default: 0.")
    p.add_argument("--preflight-seconds", type=int, default=60,
                    help="Preflight capture duration. Default: 60.")
    p.add_argument("--fast-duration-seconds", type=int, default=900,
                    help="FAST_DIAGNOSTIC capture duration. Default: 900 (15m).")
    p.add_argument("--full-duration-seconds", type=int, default=1800,
                    help="FULL_ACTIVE capture duration. Default: 1800 (30m).")
    p.add_argument("--oi-interval-seconds", type=int, default=5,
                    help="Open interest polling interval. Default: 5.")
    p.add_argument("--gpu-device", type=str, default="cuda:0",
                    help="CUDA device for GPU-forwarded operations. Default: cuda:0.")
    p.add_argument("--prefer-gpu", action="store_true", default=False,
                    help="Use GPU when available (CUDA required). Default: False.")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Run gate loop and print intended actions, but don't execute or lock.")
    p.add_argument("--notify", action="store_true", default=False,
                    help="Enable Wayland desktop notifications via notify-send.")
    p.add_argument("--notify-command", type=str, default="notify-send",
                    help="Notification command. Default: notify-send.")
    p.add_argument("--notify-app-name", type=str, default="Nautilus Stage2",
                    help="Notification app name. Default: Nautilus Stage2.")
    p.add_argument("--notify-timeout-ms", type=int, default=10000,
                    help="Notification display timeout in ms. Default: 10000.")
    p.add_argument("--sound", action="store_true", default=False,
                    help="Play audio alerts on capture start/complete/fail via pw-play.")
    p.add_argument("--sound-command", type=str, default="pw-play",
                    help="Audio player command. Default: pw-play (PipeWire). Also accepts: paplay, aplay.")
    p.add_argument("--sound-capture-start", type=str,
                    default="/usr/share/sounds/freedesktop/stereo/complete.oga",
                    help="Sound file played when capture starts. Default: freedesktop complete.oga.")
    p.add_argument("--sound-workflow-complete", type=str,
                    default="/usr/share/sounds/freedesktop/stereo/complete.oga",
                    help="Sound played when workflow completes. Default: freedesktop complete.oga.")
    p.add_argument("--sound-workflow-fail", type=str,
                    default="/usr/share/sounds/freedesktop/stereo/dialog-error.oga",
                    help="Sound played when workflow fails. Default: freedesktop dialog-error.oga.")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    start_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log = WatcherLogger(_LOG_DIR, start_ts)

    try:
        _main_impl(args, log, start_ts)
    except KeyboardInterrupt:
        log.action("Interrupted by user (SIGINT)")
        log.finalize("INTERRUPTED", _env_snapshot(log))
    except Exception as e:
        log.action(f"Fatal error: {e}")
        log.finalize("ERROR", _env_snapshot(log))
        raise
    finally:
        summary_path = log.close()
        if summary_path:
            print(f"\nWatcher log: {log._log_path}")
            print(f"Summary: {summary_path}")


def _notify_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _main_impl(args: argparse.Namespace, log: WatcherLogger,
               start_ts: str) -> None:
    env_data = _env_snapshot(log)
    python_exe = sys.executable
    start_time = time.time()
    deadline: float | None = None
    if args.max_runtime_seconds > 0:
        deadline = start_time + args.max_runtime_seconds
    check_num = 0

    log.action(f"Stage 2 Gate Watcher starting (interval={args.interval_seconds}s, "
               f"max_runtime={'indefinite' if args.max_runtime_seconds <= 0 else str(args.max_runtime_seconds) + 's'}, "
               f"dry_run={args.dry_run})")

    while True:
        now = time.time()
        if deadline is not None and now >= deadline:
            log.action("Max runtime reached; exiting.")
            log.finalize("MAX_RUNTIME_REACHED", env_data)
            return

        check_num += 1

        # Check gate
        try:
            gate_data = run_volatility_gate(python_exe)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            log.warning(f"Gate check #{check_num} FAILED: {e}")
            time.sleep(args.interval_seconds)
            continue

        permission = gate_data.get("capture_permission", "NO_CAPTURE")
        reason = gate_data.get("fast_capture_reason", "unknown")
        log.gate_check(check_num, permission, reason)

        if permission == "NO_CAPTURE":
            # No notification on ordinary polling loops
            time.sleep(args.interval_seconds)
            continue

        # Unknown permission → safe fail
        if permission not in ("FAST_DIAGNOSTIC_CAPTURE_ONLY", "FULL_ACTIVE_CAPTURE"):
            log.warning(f"Unknown capture_permission: {permission}. Standing down.")
            time.sleep(args.interval_seconds)
            continue

        # Permission granted — check lock
        if args.dry_run:
            mode = "FAST_DIAGNOSTIC" if permission == "FAST_DIAGNOSTIC_CAPTURE_ONLY" else "FULL_ACTIVE"
            capture_dir = _parse_capture_mode_dir(mode, datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
            log.action(f"[DRY RUN] Would run capture (mode={mode}), "
                       f"capture_dir={capture_dir}, would then evaluate + diagnostics")
            log.finalize("DRY_RUN_COMPLETE", env_data)
            return

        locked, lock_msg = check_lock(log)
        if locked:
            log.action(lock_msg)
            log.finalize("COLLECTION_LOCK_EXISTS", env_data)
            return

        # Determine mode
        if permission == "FAST_DIAGNOSTIC_CAPTURE_ONLY":
            mode = "FAST_DIAGNOSTIC"
            duration = args.fast_duration_seconds
        else:
            mode = "FULL_ACTIVE"
            duration = args.full_duration_seconds

        log.action(f"Gate opened. Proceeding with {mode} pipeline.")
        create_lock(log, permission, reason, mode)

        # Timestamp for this collection run
        collection_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Step 1: Preflight
        preflight_dir = run_preflight(python_exe, log, collection_ts, args)
        if preflight_dir is None:
            log.finalize("STREAM_PREFLIGHT_FAILED", env_data)
            send_desktop_notification(
                enabled=args.notify, command=args.notify_command,
                app_name=args.notify_app_name,
                title="Nautilus Stage2 workflow failed",
                body=(
                    f"Failure reason: preflight failed\n"
                    f"Current phase: preflight\n"
                    f"Capture mode: {mode}\n"
                    f"Log path: {log._log_path}\n"
                    f"UTC: {_notify_ts()}"
                ),
                urgency="critical", timeout_ms=args.notify_timeout_ms,
                log=log,
            )
            play_sound(
                enabled=args.sound, command=args.sound_command,
                sound_file=args.sound_workflow_fail, log=log,
            )
            return

        # Step 2: Real capture — notify + sound before starting
        capture_dir = _parse_capture_mode_dir(mode, collection_ts)
        report_dir = _parse_evaluation_mode_dir(mode, collection_ts)
        send_desktop_notification(
            enabled=args.notify, command=args.notify_command,
            app_name=args.notify_app_name,
            title="Nautilus Stage2 capture started",
            body=(
                f"Capture mode: {mode}\n"
                f"Capture duration: {duration}s\n"
                f"Capture directory: {capture_dir}\n"
                f"Report directory: {report_dir}\n"
                f"UTC: {_notify_ts()}"
            ),
            urgency="normal", timeout_ms=args.notify_timeout_ms,
            log=log,
        )
        play_sound(
            enabled=args.sound, command=args.sound_command,
            sound_file=args.sound_capture_start, log=log,
        )

        capture_dir = run_capture(python_exe, log, collection_ts, mode, duration, args)
        if capture_dir is None:
            log.finalize("CAPTURE_FAILED", env_data)
            send_desktop_notification(
                enabled=args.notify, command=args.notify_command,
                app_name=args.notify_app_name,
                title="Nautilus Stage2 workflow failed",
                body=(
                    f"Failure reason: capture exited non-zero\n"
                    f"Current phase: capture\n"
                    f"Capture mode: {mode}\n"
                    f"Log path: {log._log_path}\n"
                    f"UTC: {_notify_ts()}"
                ),
                urgency="critical", timeout_ms=args.notify_timeout_ms,
                log=log,
            )
            play_sound(
                enabled=args.sound, command=args.sound_command,
                sound_file=args.sound_workflow_fail, log=log,
            )
            return

        # Step 3: Evaluation
        report_dir = run_evaluation(python_exe, log, collection_ts, capture_dir, mode, args)
        if report_dir is None:
            log.finalize("EVALUATION_FAILED", env_data)
            send_desktop_notification(
                enabled=args.notify, command=args.notify_command,
                app_name=args.notify_app_name,
                title="Nautilus Stage2 workflow failed",
                body=(
                    f"Failure reason: evaluation exited non-zero\n"
                    f"Current phase: evaluation\n"
                    f"Capture mode: {mode}\n"
                    f"Log path: {log._log_path}\n"
                    f"UTC: {_notify_ts()}"
                ),
                urgency="critical", timeout_ms=args.notify_timeout_ms,
                log=log,
            )
            play_sound(
                enabled=args.sound, command=args.sound_command,
                sound_file=args.sound_workflow_fail, log=log,
            )
            return

        # Step 4: Post-evaluation diagnostics
        run_post_evaluation_diagnostics(python_exe, log, capture_dir, report_dir, mode, args)

        log.finalize("COLLECTION_COMPLETE", env_data)
        send_desktop_notification(
            enabled=args.notify, command=args.notify_command,
            app_name=args.notify_app_name,
            title="Nautilus Stage2 workflow complete",
            body=(
                f"Final verdict: COLLECTION_COMPLETE\n"
                f"Capture mode: {mode}\n"
                f"Capture directory: {capture_dir}\n"
                f"Report directory: {report_dir}\n"
                f"Summary: {log._summary_path}\n"
                f"UTC: {_notify_ts()}"
            ),
            urgency="normal", timeout_ms=args.notify_timeout_ms,
            log=log,
        )
        play_sound(
            enabled=args.sound, command=args.sound_command,
            sound_file=args.sound_workflow_complete, log=log,
        )
        return


if __name__ == "__main__":
    main()
