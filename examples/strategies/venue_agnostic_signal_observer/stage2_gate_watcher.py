#!/usr/bin/env python3
"""
Stage 2 Gate Watcher — cross_asset_beta_lag_v1 stress polling.

Repeatedly checks the volatility gate for BTC 1h stress >= 150 bps with
acceleration.  When stress is confirmed, runs a corpus-eligible FULL_ACTIVE
cross-asset capture, validates it, and reports corpus progress.

Replaces the old Hermes polling cronjob (ca223755d1e6) as the canonical
stress detector.  Designed to run as a user-level systemd service.

Safety: public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# V1/V2 infra modules
# ---------------------------------------------------------------------------
from .stage2_readiness_check import check_readiness
from .stage2_precommitment_utils import (
    CollectionLock,
    load_precommitment,
    _get_git_sha,
)
from .run_artifacts import create_run_id, atomic_write_json
from .run_index import append_run_index_row, build_run_index_row

# ---------------------------------------------------------------------------
# Runtime path configuration
# ---------------------------------------------------------------------------
# These module-level globals are set in main() after CLI/env resolution.
# Functions that depend on paths resolve against these at call time.
_REPORTS_ROOT: Path | None = None
_DATA_ROOT: Path | None = None
_REPO_ROOT: Path | None = None
_STATUS_PATH_OVERRIDE: Path | None = None
_PYTHON_EXE_OVERRIDE: Path | None = None

# Defaults (used when no override is set)
_DEFAULT_LOG_DIR = Path("reports", "stage2_gate_watcher_logs")
_DEFAULT_STATUS_PATH = Path("reports", "stage2_gate_watcher_status.json")
_DEFAULT_CONCURRENCY_LOCK_PATH = Path("reports", "cross_asset_beta_lag_watcher.lock")
_DEFAULT_COLLECTION_LOCK_PATH = Path("reports", "stage2_collection_lock.json")
_DEFAULT_STATE_PATH = Path("reports", "cross_asset_beta_lag_watcher_state.json")
_DEFAULT_IN_CAPTURE_FLAG = Path("reports", "cross_asset_beta_lag_capturing.flag")
_DEFAULT_DATA_ROOT = Path("data")


def _get_reports_root() -> Path:
    """Return the effective reports root (override or default)."""
    if _REPORTS_ROOT is not None:
        return _REPORTS_ROOT
    return Path("reports")


def _get_data_root() -> Path:
    """Return the effective data root (override or default)."""
    if _DATA_ROOT is not None:
        return _DATA_ROOT
    return _DEFAULT_DATA_ROOT


def _get_status_path() -> Path:
    """Return the effective status JSON path."""
    if _STATUS_PATH_OVERRIDE is not None:
        return _STATUS_PATH_OVERRIDE
    return _get_reports_root() / "stage2_gate_watcher_status.json"


def _get_concurrency_lock_path() -> Path:
    return _get_reports_root() / "cross_asset_beta_lag_watcher.lock"


def _get_collection_lock_path() -> Path:
    return _get_reports_root() / "stage2_collection_lock.json"


def _get_state_path() -> Path:
    return _get_reports_root() / "cross_asset_beta_lag_watcher_state.json"


def _get_in_capture_flag_path() -> Path:
    return _get_reports_root() / "cross_asset_beta_lag_capturing.flag"


def _get_log_dir() -> Path:
    return _get_reports_root() / "stage2_gate_watcher_logs"


def _resolve_python_exe(repo_root: Path) -> Path:
    """Resolve python executable from the runtime worktree's venv.

    Falls back to legacy hardcoded path only as last resort.
    """
    candidate = repo_root / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate.resolve()
    return Path(
        "/mnt/nasirjones/py/nautilus_trader/.venv/bin/python"
    ).resolve()


def _get_python_exe() -> Path:
    if _PYTHON_EXE_OVERRIDE is not None:
        return _PYTHON_EXE_OVERRIDE
    if _REPO_ROOT is not None:
        return _resolve_python_exe(_REPO_ROOT)
    return Path(
        "/mnt/nasirjones/py/nautilus_trader/.venv/bin/python"
    ).resolve()

_SIGNAL_FAMILY = "cross_asset_beta_lag_v1"

# Gate verdict constants
VERDICT_ACCELERATING = "ACCELERATING"
VERDICT_MARKET_ACTIVE = "MARKET_ACTIVE"
VERDICT_MARKET_ALT_ACTIVE = "MARKET_ALT_ACTIVE"

# ---------------------------------------------------------------------------
# Concurrency lock
# ---------------------------------------------------------------------------


class ConcurrencyLock:
    """flock-based concurrency guard for the watcher.

    Prevents duplicate watcher instances from running simultaneously.
    Lock file at reports/cross_asset_beta_lag_watcher.lock.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = (path or _get_concurrency_lock_path()).resolve()
        self._fd: int | None = None

    def acquire(self) -> tuple[bool, str]:
        """Try to acquire the lock non-blocking.  Returns (acquired, reason)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Write PID so stale lock can be diagnosed
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            self._fd = fd
            return True, f"Lock acquired (pid={os.getpid()})"
        except (IOError, OSError) as e:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False, f"Lock held by another process: {e}"

    def release(self) -> None:
        """Release the lock."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        # Remove the lock file (best-effort)
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Captured capture guard (prevents overlapping captures within one watcher)
# ---------------------------------------------------------------------------


class CaptureGuard:
    """Process-local flag that prevents overlapping captures."""

    @staticmethod
    def _flag_path() -> Path:
        return _get_in_capture_flag_path()

    @staticmethod
    def try_acquire() -> bool:
        path = CaptureGuard._flag_path()
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()))
        return True

    @staticmethod
    def release() -> None:
        path = CaptureGuard._flag_path()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Capture spacing cooldown state
# ---------------------------------------------------------------------------


class WatcherState:
    """Persistent state for the gate watcher, including capture spacing.

    State is stored on disk at _STATE_PATH so cooldown survives restarts.
    """

    def __init__(self, min_gap_seconds: int = 3600) -> None:
        self._path = _get_state_path().resolve()
        self._min_gap = min_gap_seconds
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        default: dict[str, Any] = {
            "schema_version": 1,
            "signal_family": _SIGNAL_FAMILY,
            "min_gap_seconds": self._min_gap,
            "last_corpus_eligible_capture_utc": None,
            "last_corpus_eligible_capture_run_id": None,
            "last_corpus_eligible_capture_dir": None,
            "updated_utc": _ts_now_iso(),
        }
        if not self._path.exists():
            return default
        try:
            with open(self._path) as f:
                data: dict[str, Any] = json.load(f)
            data.setdefault("min_gap_seconds", self._min_gap)
            return data
        except (json.JSONDecodeError, OSError):
            return default

    def _save(self) -> None:
        self._data["updated_utc"] = _ts_now_iso()
        self._data["min_gap_seconds"] = self._min_gap
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, self._data)

    def record_capture(self, run_id: str, capture_dir: str) -> None:
        """Record a successful corpus-eligible FULL_ACTIVE capture."""
        self._data["last_corpus_eligible_capture_utc"] = _ts_now_iso()
        self._data["last_corpus_eligible_capture_run_id"] = run_id
        self._data["last_corpus_eligible_capture_dir"] = capture_dir
        self._save()

    def cooldown_remaining_seconds(self) -> int | None:
        """Return seconds remaining in cooldown, or None if no cooldown active."""
        last_utc_str = self._data.get("last_corpus_eligible_capture_utc")
        if not last_utc_str:
            return None  # No previous capture — no cooldown
        try:
            last_dt = datetime.fromisoformat(
                last_utc_str.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            return None
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds()
        remaining = self._min_gap - elapsed
        if remaining <= 0:
            return None  # Cooldown expired
        return int(remaining)

    @property
    def last_capture_utc(self) -> str | None:
        return self._data.get("last_corpus_eligible_capture_utc")


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


class WatcherLogger:
    """Simple stdout logger with structured JSON status."""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._ts: str | None = None

    def log(self, event: str, **kw: Any) -> None:
        entry = {"event": event, "ts": _ts_now_iso()}
        entry.update(kw)
        self._entries.append(entry)
        parts = [f"[{event.upper()}]"]
        for k, v in kw.items():
            parts.append(f"{k}={v}")
        print(" ".join(parts), flush=True)

    def get_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)


def _ts_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond:06d}"[:3] + "Z"


# ---------------------------------------------------------------------------
# Volatility gate
# ---------------------------------------------------------------------------


def _run_gate() -> dict[str, Any]:
    """Run the volatility gate and return parsed result dict."""
    result: dict[str, Any] = {
        "gate_available": True, "gate_passed": False,
        "btc_1h_bps": None, "market_verdict": None,
        "accel_verdict": None, "error": None,
        "results": None, "freshness": None,
    }
    try:
        from examples.strategies.volatility_gate import (
            compute_hourly_gate, PAIRS, fetch_ohlc, hourly_bar_freshness,
        )
    except ImportError as e:
        result["gate_available"] = False
        result["error"] = f"import_failed: {e}"
        return result

    try:
        gate_results, market_verdict, accel_verdict = compute_hourly_gate()
    except Exception as e:
        result["error"] = f"gate_failed: {e}"
        return result

    btc_data = gate_results.get("BTC/USD", {})
    btc_1h = btc_data.get("range_1h_bps") or btc_data.get("range_3h_bps") or 0.0

    result["market_verdict"] = market_verdict
    result["accel_verdict"] = accel_verdict
    result["btc_1h_bps"] = round(float(btc_1h), 2)
    result["results"] = gate_results

    # Freshness
    try:
        btc_bars = fetch_ohlc(PAIRS["BTC/USD"], interval=60, count=10)
        result["freshness"] = hourly_bar_freshness(btc_bars)
    except Exception:
        result["freshness"] = {"gate_data_stale_or_unchanged": True}

    # Stress gate: BTC 1h move >= 150 bps AND acceleration confirmed
    stress_met = float(btc_1h) >= 150.0
    accel_ok = accel_verdict == VERDICT_ACCELERATING

    result["stress_condition_btc_1h_bps_ge_150"] = stress_met
    result["stress_condition_acceleration"] = accel_ok

    if stress_met and accel_ok:
        result["gate_passed"] = True

    return result


# ---------------------------------------------------------------------------
# Readiness check wrapper
# ---------------------------------------------------------------------------


def _run_readiness() -> dict[str, Any]:
    """Run the Stage 2 readiness check.  Returns result dict."""
    return check_readiness()


# ---------------------------------------------------------------------------
# Capture runner (subprocess)
# ---------------------------------------------------------------------------


def _run_capture(log: WatcherLogger) -> str | None:
    """Run a cross-asset capture.  Returns capture_dir on success, None on failure."""
    run_id = create_run_id(prefix="cross_asset_beta_lag_cap")
    ts_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    capture_dir = f"data/cross_asset_beta_lag_v1_FULL_ACTIVE_{ts_suffix}"

    log.log("capture_start", run_id=run_id, capture_dir=capture_dir)

    # Build subprocess command using the existing capture script.
    # For cross-asset beta lag we need tick data for all involved symbols.
    # The existing run_derivatives_spot_capture captures perp + spot ticks.
    cmd = [
        str(_get_python_exe()),
        "-m", "examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture",
        "--source-venue", "binance_perp",
        "--source-symbols", "BTC/USDT,ETH/USDT,SOL/USDT,LINK/USDT,DOGE/USDT,AVAX/USDT",
        "--target-venues", "kraken,coinbase",
        "--target-symbols", "BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOGE/USD,AVAX/USD",
        "--duration-seconds", "900",
        "--capture-mode", "FULL_ACTIVE",
        "--out", capture_dir,
    ]

    log.log("capture_subprocess", command=" ".join(cmd))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=1200,  # 900s capture + 300s grace
            cwd=Path.cwd(),
        )
    except subprocess.TimeoutExpired as e:
        log.log("capture_timeout", stdout=e.stdout or "", stderr=e.stderr or "")
        return None
    except Exception as e:
        log.log("capture_spawn_error", error=str(e))
        return None

    if proc.returncode != 0:
        log.log("capture_failed", returncode=proc.returncode,
                stderr=proc.stderr[:1000])
        return None

    log.log("capture_completed", returncode=proc.returncode)
    return capture_dir


# ---------------------------------------------------------------------------
# Validation wrapper
# ---------------------------------------------------------------------------


def _run_validation(capture_dir: str, log: WatcherLogger) -> dict[str, Any]:
    """Run validate_capture on a capture directory.  Returns validation result."""
    from .validate_capture import validate_capture

    log.log("validation_start", capture_dir=capture_dir)
    result = validate_capture(
        capture_dir=capture_dir,
        quarantine_on_failure=True,
    )

    verdict = result.get("verdict", "UNKNOWN")
    log.log("validation_result", verdict=verdict, capture_dir=capture_dir)

    if verdict in ("CAPTURE_VALIDATION_FAILED", "CAPTURE_JSONL_MALFORMED",
                   "CAPTURE_MANIFEST_MISSING", "CAPTURE_SCHEMA_UNSUPPORTED"):
        # Quarantine
        run_id = result.get("run_id") or "unknown"
        from .quarantine import quarantine_run
        quarantine_run(
            run_id=run_id,
            reason=f"validation_failed_{verdict}",
            quarantined_by="stage2_gate_watcher",
        )
        log.log("quarantined", run_id=run_id, reason=verdict)

    return result


# ---------------------------------------------------------------------------
# Corpus counter
# ---------------------------------------------------------------------------

_CORPUS_TARGET_WINDOWS = 10


def _count_validated_full_active() -> int:
    """Count validated FULL_ACTIVE captures for cross_asset_beta_lag_v1.

    Only counts captures whose directory name starts with the
    cross_asset_beta_lag_v1 prefix.  Pre-lock captures from other
    signal families (e.g. derivatives v2) are excluded.
    """
    count = 0
    data_root = _get_data_root()
    if not data_root.exists():
        return 0

    from .quarantine import get_quarantined_run_ids
    from .burn import get_burned_run_ids

    quarantined = get_quarantined_run_ids()
    burned = get_burned_run_ids()

    for d in data_root.iterdir():
        if not d.is_dir():
            continue
        # Only count captures from this signal family
        dirname = d.name
        if not dirname.startswith("cross_asset_beta_lag_v1_"):
            continue
        manifest_path = d / "capture_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path) as f:
                m = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        run_id = m.get("run_id", "")
        mode = m.get("capture_mode",
                      m.get("_metadata", {}).get("capture_mode", ""))
        if mode != "FULL_ACTIVE":
            continue
        if run_id in quarantined:
            continue
        if run_id in burned:
            continue
        count += 1

    return count


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------


def _write_status(**kw: Any) -> None:
    """Write the watcher status JSON."""
    data: dict[str, Any] = {
        "updated_utc": _ts_now_iso(),
        "git_sha": _get_git_sha(),
        "signal_family": _SIGNAL_FAMILY,
        "gateway_replaced_by_systemd": True,
        "safety": "public data observer only",
        "runtime_repo_root": str(_REPO_ROOT or Path.cwd().resolve()),
        "reports_root": str(_get_reports_root().resolve()),
        "data_root": str(_get_data_root().resolve()),
    }
    data.update(kw)
    status_path = _get_status_path()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(status_path, data)


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        description="Stage 2 Gate Watcher — cross_asset_beta_lag_v1 "
                    "stress polling.  Public data observer only.",
    )
    p.add_argument("--poll-seconds", type=int, default=60,
                    help="Polling interval in seconds (default: 60)")
    p.add_argument("--stress-bps", type=float, default=150.0,
                    help="BTC 1h stress threshold in bps (default: 150)")
    p.add_argument("--require-acceleration", action="store_true",
                    default=True,
                    help="Require ACCELERATING gate verdict (default: True)")
    p.add_argument("--workdir", type=str,
                    default=os.environ.get(
                        "NAUTILUS_STAGE2_REPO_ROOT",
                        "/mnt/nasirjones/py/nautilus_trader",
                    ),
                    help="Working directory (default: $NAUTILUS_STAGE2_REPO_ROOT or /mnt/nasirjones/py/nautilus_trader)")
    p.add_argument("--reports-root", type=str,
                    default=os.environ.get("NAUTILUS_STAGE2_REPORTS_ROOT"),
                    help="Reports/status output root (default: workdir/reports). "
                         "Overrides: $NAUTILUS_STAGE2_REPORTS_ROOT")
    p.add_argument("--data-root", type=str,
                    default=os.environ.get("NAUTILUS_STAGE2_DATA_ROOT"),
                    help="Data/capture output root (default: workdir/data). "
                         "Overrides: $NAUTILUS_STAGE2_DATA_ROOT")
    p.add_argument("--status-path", type=str,
                    default=os.environ.get("NAUTILUS_STAGE2_STATUS_PATH"),
                    help="Exact status JSON path (default: reports-root/stage2_gate_watcher_status.json). "
                         "Overrides: $NAUTILUS_STAGE2_STATUS_PATH")
    p.add_argument("--python-exe", type=str,
                    default=os.environ.get("NAUTILUS_STAGE2_PYTHON_EXE"),
                    help="Path to python executable (default: worktree .venv/bin/python). "
                         "Overrides: $NAUTILUS_STAGE2_PYTHON_EXE")
    p.add_argument("--log-dir", type=str, default=None,
                    help="Log directory (default: reports/stage2_gate_watcher_logs)")
    p.add_argument("--out-base", type=str, default="data",
                    help="Data output base (default: data)")
    p.add_argument("--reports-base", type=str, default="reports",
                    help="Reports base (default: reports)")
    p.add_argument("--no-capture", action="store_true", default=False,
                    help="Test mode: do not capture, only report gate status")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Alias for --no-capture")
    p.add_argument("--once", action="store_true", default=False,
                    help="Run one poll cycle then exit")
    p.add_argument("--min-gap-seconds", type=int, default=3600,
                    help="Minimum gap between corpus-eligible FULL_ACTIVE captures "
                         "in seconds (default: 3600)")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Combine --dry-run into --no-capture
    if args.dry_run:
        args.no_capture = True

    workdir = Path(args.workdir).resolve()
    os.chdir(str(workdir))

    # Configure module-level path globals
    global _REPORTS_ROOT, _DATA_ROOT, _REPO_ROOT, _STATUS_PATH_OVERRIDE, _PYTHON_EXE_OVERRIDE
    _REPO_ROOT = workdir

    if args.reports_root:
        _REPORTS_ROOT = Path(args.reports_root).resolve()
    if args.data_root:
        _DATA_ROOT = Path(args.data_root).resolve()
    if args.status_path:
        _STATUS_PATH_OVERRIDE = Path(args.status_path).resolve()
    if args.python_exe:
        _PYTHON_EXE_OVERRIDE = Path(args.python_exe).resolve()

    log = WatcherLogger()
    state = WatcherState(min_gap_seconds=args.min_gap_seconds)
    lock = ConcurrencyLock()

    # Acquire watcher concurrency lock
    acquired, reason = lock.acquire()
    if not acquired:
        print(f"[FATAL] {reason}", file=sys.stderr)
        _write_status(error=f"Concurrency lock not acquired: {reason}")
        sys.exit(1)

    try:
        _run_watcher_cycle(log, args, state)
    finally:
        lock.release()
        CaptureGuard.release()


def _run_watcher_cycle(log: WatcherLogger, args: argparse.Namespace,
                       state: WatcherState | None = None) -> None:
    """Main polling cycle."""

    log.log("startup", poll_seconds=args.poll_seconds,
            stress_bps=args.stress_bps, once=args.once)

    # --- Step 1: Readiness check ---
    precommit = load_precommitment()
    log.log("readiness_check_start")

    readiness = _run_readiness()
    ready = readiness.get("ready", False)
    blockers = readiness.get("hard_blockers", [])

    log.log("readiness_result", ready=ready, blocker_count=len(blockers))

    if not ready:
        for b in blockers:
            log.log("readiness_blocker", blocker=b)
        print("[FATAL] Readiness check failed.  Blockers above.", file=sys.stderr)
        _write_status(
            readiness_status="FAILED",
            hard_blockers=blockers,
            gate_status="NOT_CHECKED",
            error="readiness_check_failed",
        )
        sys.exit(1)

    log.log("readiness_passed")

    # --- Polling loop ---
    iteration = 0
    while True:
        iteration += 1
        poll_ts = _ts_now_iso()
        print(f"\n{'='*60}")
        print(f"  Watcher iteration {iteration} at {poll_ts}")
        print(f"{'='*60}")

        # --- Step 2: Run gate ---
        gate_result = _run_gate()
        gate_passed = gate_result.get("gate_passed", False)
        btc_1h = gate_result.get("btc_1h_bps", 0)
        market_v = gate_result.get("market_verdict", "?")
        accel_v = gate_result.get("accel_verdict", "?")
        gate_err = gate_result.get("error")

        log.log("gate_result", gate_passed=gate_passed, btc_1h_bps=btc_1h,
                market_verdict=market_v, accel_verdict=accel_v,
                error=gate_err)

        print(f"  BTC 1h move: {btc_1h:.1f} bps  (threshold: {args.stress_bps} bps)")
        print(f"  Market verdict: {market_v}  Accel verdict: {accel_v}")
        print(f"  Gate passed: {gate_passed}")

        if not gate_passed:
            # Diagnostic only — do NOT create collection lock
            print("  INSUFFICIENT_STRESS_FOR_HYPOTHESIS_TEST — diagnostic only")

            _write_status(
                readiness_status="PASSED",
                gate_status="NOT_PASSED",
                current_btc_1h_move_bps=btc_1h,
                acceleration_status=accel_v,
                last_capture_status="SKIPPED_LOW_STRESS",
                stress_condition="INSUFFICIENT",
                validated_full_active_capture_count=_count_validated_full_active(),
                captures_remaining_before_stage2_eval=max(
                    0, 10 - _count_validated_full_active()),
            )

            if args.once:
                print("\n  --once mode: exiting after one poll cycle")
                break

            time.sleep(args.poll_seconds)
            continue

        # --- Stress confirmed! ---
        print("  STRESS GATE PASSED — BTC 1h >= 150 bps with acceleration")
        print("  This is a corpus-eligible stress window.")

        # --- Cooldown check ---
        cooldown_remaining = None
        if state is not None:
            cooldown_remaining = state.cooldown_remaining_seconds()
        if cooldown_remaining is not None and cooldown_remaining > 0:
            log.log("skipped_cooldown",
                     remaining_seconds=cooldown_remaining)
            print(f"  SKIPPED_COOLDOWN — {cooldown_remaining}s remaining until next "
                  f"corpus-eligible capture is allowed")
            print("  Lock NOT created. Capture NOT started.")
            _write_status(
                readiness_status="PASSED",
                gate_status="PASSED_BUT_COOLDOWN",
                current_btc_1h_move_bps=btc_1h,
                acceleration_status=accel_v,
                last_capture_status="SKIPPED_COOLDOWN",
                cooldown_remaining_seconds=cooldown_remaining,
                stress_condition="FULL_ACTIVE_STRESS",
                validated_full_active_capture_count=_count_validated_full_active(),
                captures_remaining_before_stage2_eval=max(
                    0, 10 - _count_validated_full_active()),
            )
            if args.once:
                break
            time.sleep(args.poll_seconds)
            continue

        if args.no_capture:
            print("  --no-capture set: skipping capture (test/dry-run mode)")
            _write_status(
                readiness_status="PASSED",
                gate_status="PASSED",
                current_btc_1h_move_bps=btc_1h,
                acceleration_status=accel_v,
                last_capture_status="SKIPPED_NO_CAPTURE_MODE",
                stress_condition="FULL_ACTIVE_STRESS",
                validated_full_active_capture_count=_count_validated_full_active(),
                captures_remaining_before_stage2_eval=max(
                    0, 10 - _count_validated_full_active()),
            )
            if args.once:
                break
            time.sleep(args.poll_seconds)
            continue

        # --- Acquire capture guard ---
        if not CaptureGuard.try_acquire():
            log.log("capture_guard_busy",
                     message="A capture is already in progress, skipping this cycle")
            print("  CAPTURE GUARD BUSY — skipping this cycle")
            if args.once:
                break
            time.sleep(args.poll_seconds)
            continue

        try:
            # --- Create collection lock if needed ---
            if not _get_collection_lock_path().exists():
                lock_mgr = CollectionLock()
                run_id = create_run_id(prefix="cross_asset_beta_lag_lock")
                lock_mgr.create(
                    run_id=run_id,
                    signal_family=_SIGNAL_FAMILY,
                    lock_reason="first corpus-eligible FULL_ACTIVE capture starting",
                )
                log.log("collection_lock_created", run_id=run_id)
                print(f"  Collection lock created for run_id={run_id}")
            else:
                # Verify existing lock
                lock_mgr = CollectionLock()
                lock_ok, lock_reason = lock_mgr.verify()
                if not lock_ok:
                    log.log("collection_lock_mismatch",
                             error=lock_reason)
                    print(f"  [FATAL] Collection lock mismatch: {lock_reason}",
                          file=sys.stderr)
                    _write_status(
                        readiness_status="PASSED",
                        gate_status="PASSED",
                        error=f"collection_lock_mismatch: {lock_reason}",
                    )
                    continue
                log.log("collection_lock_verified")

            # --- Run capture ---
            capture_dir = _run_capture(log)

            if capture_dir is None:
                log.log("capture_attempt_failed")
                print("  Capture failed — see logs for details")
                _write_status(
                    readiness_status="PASSED",
                    gate_status="PASSED",
                    last_capture_status="FAILED",
                    error="capture_attempt_failed",
                )
                continue

            # --- Validate ---
            val_result = _run_validation(capture_dir, log)
            verdict = val_result.get("verdict", "UNKNOWN")

            if verdict not in ("CAPTURE_VALIDATION_PASSED",
                               "CAPTURE_VALIDATION_WARNINGS"):
                log.log("validation_not_passed", verdict=verdict)
                print(f"  Validation NOT passed: {verdict}")
                _write_status(
                    readiness_status="PASSED",
                    gate_status="PASSED",
                    last_capture_status="VALIDATION_FAILED",
                    last_capture_dir=capture_dir,
                    last_validation_status=verdict,
                )
                continue

            # --- Record successful capture in cooldown state ---
            run_id = val_result.get("run_id") or "unknown"
            if state is not None:
                state.record_capture(run_id=run_id, capture_dir=capture_dir)
                log.log("capture_recorded_in_state", run_id=run_id,
                        capture_dir=capture_dir)

            # --- Resolve actual capture status from manifest (may be completed_with_errors) ---
            last_capture_status = "COMPLETED"
            manifest_path = Path(capture_dir) / "capture_manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path) as _mf:
                        _manifest = json.load(_mf)
                    if _manifest.get("capture_status") == "completed_with_errors":
                        last_capture_status = "COMPLETED_WITH_ERRORS"
                        log.log("capture_completed_with_errors",
                                capture_dir=capture_dir)
                except (json.JSONDecodeError, OSError):
                    pass

            # --- Count corpus ---
            validated_count = _count_validated_full_active()
            remaining = max(0, _CORPUS_TARGET_WINDOWS - validated_count)

            log.log("corpus_update",
                     validated_full_active=validated_count,
                     remaining=remaining)
            print(f"  VALIDATED FULL_ACTIVE captures: {validated_count}")
            print(f"  Remaining before Stage 2 evaluation: {remaining}")

            if remaining > 0:
                print(f"  Stage 2 evaluation BLOCKED — {remaining} more captures required")
            else:
                print(f"  Stage 2 evaluation READY — {_CORPUS_TARGET_WINDOWS}+ validated FULL_ACTIVE captures")
                print("  (Evaluation is not run by the watcher; run manually)")

            _write_status(
                readiness_status="PASSED",
                gate_status="PASSED",
                current_btc_1h_move_bps=btc_1h,
                acceleration_status=accel_v,
                last_capture_status=last_capture_status,
                last_capture_dir=capture_dir,
                last_validation_status=verdict,
                validated_full_active_capture_count=validated_count,
                captures_remaining_before_stage2_eval=remaining,
                stress_condition="FULL_ACTIVE_STRESS",
            )

        finally:
            CaptureGuard.release()

        if args.once:
            print("\n  --once mode: exiting after one capture cycle")
            break

        # Sleep before next poll
        print(f"\n  Sleeping {args.poll_seconds}s...")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
