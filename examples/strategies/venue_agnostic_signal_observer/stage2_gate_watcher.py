#!/usr/bin/env python3
# ruff: noqa: C901,D202,D213,D401,FURB162,PLW1510,S310,S603,S607,SIM105,UP017,UP024
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
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# V1/V2 infra modules
# ---------------------------------------------------------------------------
from .run_artifacts import atomic_write_json
from .run_artifacts import create_run_id
from .stage2_precommitment_utils import CollectionLock
from .stage2_precommitment_utils import _get_git_sha
from .stage2_readiness_check import check_readiness


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

_SIGNAL_FAMILY = os.environ.get(
    "NAUTILUS_STAGE2_SIGNAL_FAMILY", "cross_asset_beta_lag_v1"
)
_STRESS_V2_SIGNAL_FAMILY = "cross_asset_beta_lag_stress_v2"
_STRESS_V2_TRIGGER_NAME = "source_30bps_30s_and_crypto_native_stress"
_STRESS_V2_SOURCE_ASSETS = ("BTC", "ETH")
_STRESS_V2_TARGET_ASSETS = ("SOL", "LINK", "DOGE", "AVAX", "ADA")
_STRESS_V2_TRIGGER_THRESHOLD_BPS = 30.0
_STRESS_V2_METADATA_THRESHOLD_BPS = 150.0
_STRESS_V2_TRIGGER_WINDOW_SECONDS = 30
_STRESS_V2_TRIGGER_POLL_SECONDS = 2.0
_STRESS_V2_TRIGGER_STALE_SECONDS = 8.0
_STRESS_V2_TRIGGER_HISTORY_SECONDS = 45.0
_STRESS_V2_TRIGGER_PRICE_SOURCE = "binance_spot_rest_ticker"
_STRESS_V2_DEFAULT_CAPTURE_SECONDS = 900

# Gate verdict constants
VERDICT_ACCELERATING = "ACCELERATING"
VERDICT_MARKET_ACTIVE = "MARKET_ACTIVE"
VERDICT_MARKET_ALT_ACTIVE = "MARKET_ALT_ACTIVE"


@dataclass(frozen=True)
class StressTriggerResult:
    """Decision record for the stress-v2 trigger.

    This is trigger plumbing only. It does not evaluate profitability, costs,
    null tests, or candidate/rejected verdicts.
    """

    triggered: bool
    trigger_name: str
    trigger_threshold_bps: float
    trigger_window_seconds: int
    source_asset: str | None
    source_move_bps_30s: float | None
    stress_confirmation_status: str
    impulse_condition_met: bool
    contains_150bps_impulse: bool
    metadata_threshold_bps: float
    reason: str

    def as_status_fields(self) -> dict[str, Any]:
        return {
            "trigger_name": self.trigger_name,
            "trigger_threshold_bps": self.trigger_threshold_bps,
            "trigger_window_seconds": self.trigger_window_seconds,
            "source_asset": self.source_asset,
            "source_move_bps_30s": self.source_move_bps_30s,
            "stress_confirmation_status": self.stress_confirmation_status,
            "contains_150bps_impulse": self.contains_150bps_impulse,
            "trigger_price_source": _STRESS_V2_TRIGGER_PRICE_SOURCE,
            "trigger_poll_seconds": _STRESS_V2_TRIGGER_POLL_SECONDS,
            "trigger_stale_after_seconds": _STRESS_V2_TRIGGER_STALE_SECONDS,
            "reason": self.reason,
        }


def _tick_asset(tick: Any) -> str | None:
    if isinstance(tick, dict):
        value = tick.get("asset") or tick.get("source_asset") or tick.get("symbol")
    else:
        value = getattr(tick, "asset", None) or getattr(tick, "symbol", None)
    if not value:
        return None
    text = str(value).upper()
    if text.startswith("XBT"):
        return "BTC"
    return text.split("/")[0].split("-")[0]


def _tick_ts_seconds(tick: Any) -> float | None:
    if isinstance(tick, dict):
        for key in ("ts_seconds", "timestamp", "ts", "time"):
            if key in tick and tick[key] is not None:
                return float(tick[key])
        if tick.get("ts_event") is not None:
            return float(tick["ts_event"]) / 1_000_000_000
        return None
    for key in ("ts_seconds", "timestamp", "ts", "time"):
        value = getattr(tick, key, None)
        if value is not None:
            return float(value)
    value = getattr(tick, "ts_event", None)
    if value is not None:
        return float(value) / 1_000_000_000
    return None


def _tick_price(tick: Any) -> float | None:
    if isinstance(tick, dict):
        value = tick.get("price") or tick.get("close")
    else:
        value = getattr(tick, "price", None) or getattr(tick, "close", None)
    if value is None:
        return None
    price = float(value)
    if price <= 0:
        return None
    return price


def evaluate_cross_asset_beta_lag_stress_trigger(
    *,
    ticks: list[Any],
    stress_confirmation_active: bool,
    threshold_bps: float = _STRESS_V2_TRIGGER_THRESHOLD_BPS,
    window_seconds: int = _STRESS_V2_TRIGGER_WINDOW_SECONDS,
) -> StressTriggerResult:
    """Evaluate the precommitted stress-v2 trigger on BTC/ETH ticks.

    Path decision: this function consumes tick/sub-minute records when supplied
    by the existing observer/capture infrastructure. The watcher currently uses
    the volatility gate for regime confirmation and does not invent verdicts.
    """
    best_asset: str | None = None
    best_move: float | None = None

    rows: list[tuple[str, float, float]] = []
    for tick in ticks:
        asset = _tick_asset(tick)
        ts_seconds = _tick_ts_seconds(tick)
        price = _tick_price(tick)
        if asset not in _STRESS_V2_SOURCE_ASSETS or ts_seconds is None or price is None:
            continue
        rows.append((asset, ts_seconds, price))

    by_asset: dict[str, list[tuple[float, float]]] = {asset: [] for asset in _STRESS_V2_SOURCE_ASSETS}
    for asset, ts_seconds, price in rows:
        by_asset[asset].append((ts_seconds, price))

    for asset, series in by_asset.items():
        series.sort(key=lambda item: item[0])
        for start_ts, start_price in series:
            end_limit = start_ts + window_seconds
            for end_ts, end_price in series:
                if end_ts < start_ts or end_ts > end_limit:
                    continue
                move_bps = abs((end_price - start_price) / start_price * 10_000.0)
                if best_move is None or move_bps > best_move:
                    best_asset = asset
                    best_move = move_bps

    impulse_met = best_move is not None and best_move >= (threshold_bps - 1e-9)
    stress_status = "ACTIVE" if stress_confirmation_active else "INACTIVE"
    triggered = bool(impulse_met and stress_confirmation_active)
    contains_150 = best_move is not None and best_move >= _STRESS_V2_METADATA_THRESHOLD_BPS
    if triggered:
        reason = "STRESS_V2_GATE_PASSED"
    elif not impulse_met and stress_confirmation_active:
        reason = "STRESS_V2_STRESS_TRUE_IMPULSE_FALSE"
    elif impulse_met and not stress_confirmation_active:
        reason = "STRESS_V2_IMPULSE_TRUE_STRESS_FALSE"
    else:
        reason = "STRESS_V2_WAITING_FOR_TRIGGER"

    return StressTriggerResult(
        triggered=triggered,
        trigger_name=_STRESS_V2_TRIGGER_NAME,
        trigger_threshold_bps=threshold_bps,
        trigger_window_seconds=window_seconds,
        source_asset=best_asset,
        source_move_bps_30s=None if best_move is None else round(best_move, 6),
        stress_confirmation_status=stress_status,
        impulse_condition_met=bool(impulse_met),
        contains_150bps_impulse=contains_150,
        metadata_threshold_bps=_STRESS_V2_METADATA_THRESHOLD_BPS,
        reason=reason,
    )

class StressV2TriggerFeed:
    """Tiny BTC/ETH public-price trigger feed for capture admission only.

    Uses Binance spot's unauthenticated REST ticker endpoint at a bounded poll
    interval.  It keeps only recent in-memory observations and emits simple
    timestamped price dicts for the pure stress-v2 trigger evaluator.
    """

    _SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

    def __init__(
        self,
        *,
        poll_seconds: float = _STRESS_V2_TRIGGER_POLL_SECONDS,
        stale_after_seconds: float = _STRESS_V2_TRIGGER_STALE_SECONDS,
        history_seconds: float = _STRESS_V2_TRIGGER_HISTORY_SECONDS,
        start_background: bool = True,
    ) -> None:
        self.poll_seconds = poll_seconds
        self.stale_after_seconds = stale_after_seconds
        self.history_seconds = history_seconds
        self._lock = threading.Lock()
        self._observations: dict[str, deque[dict[str, float | str]]] = {
            asset: deque() for asset in _STRESS_V2_SOURCE_ASSETS
        }
        self._last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_background:
            self.start()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="stress-v2-trigger-feed",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.poll_seconds)

    def poll_once(self) -> None:
        now = time.time()
        try:
            prices = self._fetch_binance_spot_prices()
        except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            with self._lock:
                self._last_error = f"STRESS_V2_TRIGGER_FEED_ERROR: {exc}"
            return
        for asset, price in prices.items():
            self.record_price(asset, price, ts_seconds=now)
        with self._lock:
            self._last_error = None

    @classmethod
    def _fetch_binance_spot_prices(cls) -> dict[str, float]:
        symbols = ",".join(f'"{symbol}"' for symbol in cls._SYMBOLS.values())
        url = "https://api.binance.com/api/v3/ticker/price?symbols=" + urllib.parse.quote(f"[{symbols}]")
        request = urllib.request.Request(url, headers={"User-Agent": "nautilus-stage2-stress-v2/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        price_by_symbol = {str(row.get("symbol")): float(row["price"]) for row in payload}
        return {
            asset: price_by_symbol[symbol]
            for asset, symbol in cls._SYMBOLS.items()
            if symbol in price_by_symbol
        }

    def record_price(self, asset: str, price: float, *, ts_seconds: float) -> None:
        normalized_asset = _tick_asset({"asset": asset})
        if normalized_asset not in _STRESS_V2_SOURCE_ASSETS or price <= 0:
            return
        row: dict[str, float | str] = {
            "asset": normalized_asset,
            "symbol": f"{normalized_asset}/USDT",
            "venue": _STRESS_V2_TRIGGER_PRICE_SOURCE,
            "ts_seconds": float(ts_seconds),
            "price": float(price),
        }
        with self._lock:
            series = self._observations[normalized_asset]
            series.append(row)
            cutoff = float(ts_seconds) - self.history_seconds
            while series and float(series[0]["ts_seconds"]) < cutoff:
                series.popleft()

    def snapshot_ticks(self, *, now_seconds: float | None = None) -> tuple[list[dict[str, float | str]], str | None]:
        now = time.time() if now_seconds is None else now_seconds
        with self._lock:
            rows = [dict(row) for series in self._observations.values() for row in series]
            last_error = self._last_error
        if not rows:
            return [], last_error or "STRESS_V2_WAITING_FOR_TRIGGER"
        newest_ts = max(float(row["ts_seconds"]) for row in rows)
        if now - newest_ts > self.stale_after_seconds:
            return [], "STRESS_V2_TRIGGER_FEED_STALE"
        if not any(newest_ts - float(row["ts_seconds"]) >= _STRESS_V2_TRIGGER_WINDOW_SECONDS for row in rows):
            return [], "STRESS_V2_WAITING_FOR_TRIGGER"
        cutoff = now - self.history_seconds
        return [row for row in rows if float(row["ts_seconds"]) >= cutoff], last_error


_STRESS_V2_TRIGGER_FEED: StressV2TriggerFeed | None = None


def _get_stress_v2_trigger_feed() -> StressV2TriggerFeed:
    global _STRESS_V2_TRIGGER_FEED
    if _STRESS_V2_TRIGGER_FEED is None:
        _STRESS_V2_TRIGGER_FEED = StressV2TriggerFeed()
    return _STRESS_V2_TRIGGER_FEED


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
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}"[:3] + "Z"


# ---------------------------------------------------------------------------
# Volatility gate
# ---------------------------------------------------------------------------


def _run_gate() -> dict[str, Any]:
    """Run the volatility gate and return parsed result dict.

    ``gate_passed`` preserves the legacy v1 BTC-1h >= 150 bps + acceleration
    admission rule.  Stress-v2 capture admission must not use this field; it
    uses MARKET_ACTIVE + ACCELERATING only as crypto-native stress
    confirmation, then ANDs that with the separate 30s BTC/ETH impulse trigger.
    """
    result: dict[str, Any] = {
        "gate_available": True, "gate_passed": False,
        "btc_1h_bps": None, "market_verdict": None,
        "accel_verdict": None, "error": None,
        "results": None, "freshness": None,
    }
    try:
        from examples.strategies.volatility_gate import PAIRS
        from examples.strategies.volatility_gate import compute_hourly_gate
        from examples.strategies.volatility_gate import fetch_ohlc
        from examples.strategies.volatility_gate import hourly_bar_freshness
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


def _fetch_stress_v2_trigger_ticks() -> tuple[list[Any], str | None]:
    """Return BTC/ETH rolling 30s trigger observations for stress-v2 admission."""
    return _get_stress_v2_trigger_feed().snapshot_ticks()


def _stress_confirmation_active(gate_result: dict[str, Any]) -> bool:
    return (
        gate_result.get("market_verdict") == VERDICT_MARKET_ACTIVE
        and gate_result.get("accel_verdict") == VERDICT_ACCELERATING
    )


def _evaluate_stress_v2_capture_gate(
    gate_result: dict[str, Any],
    trigger_ticks: list[Any],
    *,
    trigger_feed_reason: str | None = None,
) -> StressTriggerResult:
    """Evaluate stress-v2 capture admission from gate confirmation + ticks."""
    stress_active = _stress_confirmation_active(gate_result)
    result = evaluate_cross_asset_beta_lag_stress_trigger(
        ticks=trigger_ticks,
        stress_confirmation_active=stress_active,
    )
    if not trigger_ticks and trigger_feed_reason:
        return StressTriggerResult(
            triggered=False,
            trigger_name=result.trigger_name,
            trigger_threshold_bps=result.trigger_threshold_bps,
            trigger_window_seconds=result.trigger_window_seconds,
            source_asset=result.source_asset,
            source_move_bps_30s=result.source_move_bps_30s,
            stress_confirmation_status=result.stress_confirmation_status,
            impulse_condition_met=result.impulse_condition_met,
            contains_150bps_impulse=result.contains_150bps_impulse,
            metadata_threshold_bps=result.metadata_threshold_bps,
            reason=trigger_feed_reason,
        )
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
    capture_seconds = _STRESS_V2_DEFAULT_CAPTURE_SECONDS if _SIGNAL_FAMILY == _STRESS_V2_SIGNAL_FAMILY else 900
    capture_dir = str(_get_data_root() / f"{_SIGNAL_FAMILY}_FULL_ACTIVE_{ts_suffix}")

    log.log("capture_start", run_id=run_id, capture_dir=capture_dir)

    target_symbols = "BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOGE/USD,AVAX/USD"
    source_symbols = "BTC/USDT,ETH/USDT,SOL/USDT,LINK/USDT,DOGE/USDT,AVAX/USDT"
    if _SIGNAL_FAMILY == _STRESS_V2_SIGNAL_FAMILY:
        target_symbols = "BTC/USD,ETH/USD,SOL/USD,LINK/USD,DOGE/USD,AVAX/USD,ADA/USD"
        source_symbols = "BTC/USDT,ETH/USDT,SOL/USDT,LINK/USDT,DOGE/USDT,AVAX/USDT,ADA/USDT"

    # Build subprocess command using the existing capture script.
    # For cross-asset beta lag we need tick data for all involved symbols.
    # The existing run_derivatives_spot_capture captures public source + spot ticks.
    cmd = [
        str(_get_python_exe()),
        "-m", "examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture",
        "--source-venue", "binance_perp",
        "--source-symbols", source_symbols,
        "--target-venues", "kraken,coinbase",
        "--target-symbols", target_symbols,
        "--duration-seconds", str(capture_seconds),
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


# Target number of corpus-eligible FULL_ACTIVE stress windows required
# before reruning frozen-grid discovery.
# Minimum usable independent stress windows required before frozen-grid
# discovery can run.  Must match MIN_READY_USABLE_WINDOWS in
# stress_corpus_accumulator.py (= 20).  Do NOT lower this without a
# human-authored precommitment change — it is the rerun gate.
_CORPUS_TARGET_WINDOWS = 20

# Diagnostic/canary threshold: enough windows to sanity-check the pipeline
# end-to-end, but does NOT unlock frozen-grid discovery.
_CORPUS_DIAGNOSTIC_WINDOWS = 10


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

    from .burn import get_burned_run_ids
    from .quarantine import get_quarantined_run_ids

    quarantined = get_quarantined_run_ids()
    burned = get_burned_run_ids()

    for d in data_root.iterdir():
        if not d.is_dir():
            continue
        # Only count captures from this signal family
        dirname = d.name
        if not dirname.startswith(f"{_SIGNAL_FAMILY}_"):
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
        # Require positive global overlap — a zero-overlap capture has no
        # usable tick data and must not count toward the rerun gate.
        overlap = m.get("overlap", {})
        if not isinstance(overlap, dict):
            continue
        global_overlap = overlap.get("global_overlap_duration_seconds") or 0
        if not (global_overlap > 0):
            continue
        count += 1

    return count


def _corpus_readiness() -> dict[str, Any]:
    """Return corpus readiness status dict for the status JSON.

    Two thresholds:
    - diagnostic_ready (>= _CORPUS_DIAGNOSTIC_WINDOWS=10): pipeline is
      functioning end-to-end; does NOT unlock frozen-grid discovery.
    - ready_for_rerun (>= _CORPUS_TARGET_WINDOWS=20): rerun gate; the only
      threshold that may unlock frozen-grid discovery (manually).
    """
    count = _count_validated_full_active()
    ready = count >= _CORPUS_TARGET_WINDOWS
    diagnostic_ready = count >= _CORPUS_DIAGNOSTIC_WINDOWS
    remaining = max(0, _CORPUS_TARGET_WINDOWS - count)
    return {
        "usable_window_count": count,
        "minimum_ready_usable_windows": _CORPUS_TARGET_WINDOWS,
        "diagnostic_minimum_windows": _CORPUS_DIAGNOSTIC_WINDOWS,
        "diagnostic_ready": diagnostic_ready,
        "ready_for_rerun": ready,
        "corpus_status": "CORPUS_READY_FOR_RERUN" if ready else "ACCUMULATING",
        "captures_remaining_before_stage2_eval": remaining,
        "next_action": (
            "CORPUS_READY — frozen-grid discovery can run manually"
            if ready
            else f"ACCUMULATING — {remaining} more stress windows needed before frozen-grid rerun"
        ),
    }


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------


def _get_git_status(repo_root: Path | None = None) -> tuple[str, bool]:
    """Return (short_sha, dirty) for the runtime repo/worktree."""
    cwd = repo_root or _REPO_ROOT or Path.cwd()
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return sha, bool(status.strip())
    except Exception:
        return _get_git_sha(), False


# ---------------------------------------------------------------------------
# Desktop notification + audio helpers
# ---------------------------------------------------------------------------

_NOTIFY_CMD = "notify-send"
_SOUND_CMD = "pw-play"
_SOUND_CAPTURE_START = "/usr/share/sounds/freedesktop/stereo/bell.oga"
_SOUND_CAPTURE_COMPLETE = "/usr/share/sounds/freedesktop/stereo/complete.oga"
_SOUND_CAPTURE_FAILED = "/usr/share/sounds/freedesktop/stereo/dialog-error.oga"


def _notify(title: str, body: str, urgency: str = "normal") -> None:
    """Send a Wayland desktop notification via notify-send. Best-effort."""
    try:
        subprocess.run(
            [_NOTIFY_CMD, "--app-name", "Nautilus Edge Miner",
             "--urgency", urgency, "--expire-time", "15000", title, body],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _play(sound_file: str) -> None:
    """Play a sound via pw-play. Best-effort."""
    try:
        if not Path(sound_file).exists():
            return
        subprocess.run([_SOUND_CMD, sound_file], capture_output=True, timeout=8)
    except Exception:
        pass


# ---------------------------------------------------------------------------


def _write_status(**kw: Any) -> None:
    """Write the watcher status JSON."""
    repo_root = (_REPO_ROOT or Path.cwd()).resolve()
    git_sha, git_dirty = _get_git_status(repo_root)

    # Corpus readiness (computed once; caller overrides may add to **kw)
    corpus_rdy = _corpus_readiness()

    data: dict[str, Any] = {
        "updated_utc": _ts_now_iso(),
        "git_sha": git_sha,
        "git_worktree_root": str(repo_root),
        "git_dirty": git_dirty,
        "signal_family": _SIGNAL_FAMILY,
        "gateway_replaced_by_systemd": True,
        "safety": "public data observer only; no orders; no private keys; observer research only",
        "runtime_repo_root": str(repo_root),
        "reports_root": str(_get_reports_root().resolve()),
        "data_root": str(_get_data_root().resolve()),
        "capture_status": "IDLE",
        "last_capture_status": None,
        "last_capture_dir": None,
        "last_report_dir": None,
        "trigger_name": None,
        "trigger_threshold_bps": None,
        "trigger_window_seconds": None,
        "source_asset": None,
        "source_move_bps_30s": None,
        "stress_confirmation_status": None,
        "contains_150bps_impulse": False,
        "trigger_price_source": _STRESS_V2_TRIGGER_PRICE_SOURCE,
        "trigger_poll_seconds": _STRESS_V2_TRIGGER_POLL_SECONDS,
        "trigger_stale_after_seconds": _STRESS_V2_TRIGGER_STALE_SECONDS,
        "cooldown_status": None,
        "next_allowed_capture_utc": None,
        "reason": None,
        # Corpus readiness defaults
        "usable_window_count": corpus_rdy["usable_window_count"],
        "minimum_ready_usable_windows": corpus_rdy["minimum_ready_usable_windows"],
        "diagnostic_minimum_windows": corpus_rdy["diagnostic_minimum_windows"],
        "diagnostic_ready": corpus_rdy["diagnostic_ready"],
        "ready_for_rerun": corpus_rdy["ready_for_rerun"],
        "corpus_status": corpus_rdy["corpus_status"],
        "captures_remaining_before_stage2_eval": corpus_rdy["captures_remaining_before_stage2_eval"],
        "next_action": corpus_rdy["next_action"],
    }
    data.update(kw)
    status_path = _get_status_path()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(status_path, data)


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------


def _startup_reconciliation(log: WatcherLogger) -> dict[str, Any]:
    """Detect and clean up state left by a previous crash.

    Runs unconditionally on every startup, before the concurrency lock.
    Never raises — all errors are logged and the watcher continues.

    Returns a dict summarising what was found and what was done, written
    into the status JSON as ``startup_recovery``.

    Checks:
    1. Stale CaptureGuard flag (cross_asset_beta_lag_capturing.flag).
       If the PID in the flag is not alive, the flag is from a crashed
       process.  Clear it so the next stress event can trigger a capture.

    2. Partial capture dirs (dirs with capture_manifest.partial.json but
       no capture_manifest.json).  These are incomplete — the capture
       process was killed before it could finalise.  They are quarantined
       so _count_validated_full_active never counts them.
    """
    recovery: dict[str, Any] = {
        "stale_capture_flag_cleared": False,
        "stale_capture_flag_pid": None,
        "partial_dirs_quarantined": [],
        "startup_status": "CLEAN",
    }

    # --- 1. Stale CaptureGuard flag ---
    flag_path = _get_in_capture_flag_path()
    if flag_path.exists():
        stale = False
        pid_in_flag: int | None = None
        try:
            pid_text = flag_path.read_text().strip()
            pid_in_flag = int(pid_text) if pid_text.isdigit() else None
        except OSError:
            pid_in_flag = None

        if pid_in_flag is None:
            stale = True
            log.log("startup_reconciliation", detail="capture_flag_unreadable_pid_treating_as_stale")
        else:
            # Check liveness: sending signal 0 to a non-existent PID raises ProcessLookupError
            try:
                os.kill(pid_in_flag, 0)
                # PID is alive — flag is legitimate (concurrent watcher or active capture)
                log.log("startup_reconciliation",
                        detail=f"capture_flag_pid_{pid_in_flag}_alive_leaving_intact")
            except ProcessLookupError:
                stale = True
                log.log("startup_reconciliation",
                        detail=f"capture_flag_pid_{pid_in_flag}_dead_clearing_stale_flag")
            except PermissionError:
                # PID exists but we can't signal it — treat as alive
                log.log("startup_reconciliation",
                        detail=f"capture_flag_pid_{pid_in_flag}_permission_denied_leaving_intact")

        if stale:
            try:
                flag_path.unlink(missing_ok=True)
                recovery["stale_capture_flag_cleared"] = True
                recovery["stale_capture_flag_pid"] = pid_in_flag
                recovery["startup_status"] = "RECOVERED"
                log.log("startup_reconciliation",
                        detail="stale_capture_flag_cleared",
                        pid=pid_in_flag)
            except OSError as e:
                log.log("startup_reconciliation",
                        detail=f"failed_to_clear_stale_flag: {e}")

    # --- 2. Partial capture dirs ---
    data_root = _get_data_root()
    if data_root.exists():
        try:
            from .quarantine import quarantine_run
            for d in data_root.iterdir():
                if not d.is_dir():
                    continue
                if not d.name.startswith(f"{_SIGNAL_FAMILY}_"):
                    continue
                partial = d / "capture_manifest.partial.json"
                canonical = d / "capture_manifest.json"
                if partial.exists() and not canonical.exists():
                    # Incomplete capture — quarantine it
                    run_id = "unknown"
                    try:
                        m = json.loads(partial.read_text())
                        run_id = m.get("run_id", "unknown")
                    except (json.JSONDecodeError, OSError):
                        pass
                    try:
                        quarantine_run(
                            run_id=run_id,
                            reason="partial_capture_on_startup_incomplete",
                            quarantined_by="stage2_gate_watcher_startup_reconciliation",
                        )
                        recovery["partial_dirs_quarantined"].append(
                            {"dir": d.name, "run_id": run_id}
                        )
                        recovery["startup_status"] = "RECOVERED"
                        log.log("startup_reconciliation",
                                detail="partial_capture_quarantined",
                                dir=d.name, run_id=run_id)
                    except Exception as e:
                        log.log("startup_reconciliation",
                                detail=f"quarantine_failed: {e}",
                                dir=d.name)
        except Exception as e:
            log.log("startup_reconciliation", detail=f"partial_scan_error: {e}")

    log.log("startup_reconciliation_complete",
            status=recovery["startup_status"],
            flag_cleared=recovery["stale_capture_flag_cleared"],
            partial_quarantined=len(recovery["partial_dirs_quarantined"]))

    return recovery


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
    p.add_argument("--signal-family", type=str,
                    default=os.environ.get(
                        "NAUTILUS_STAGE2_SIGNAL_FAMILY", _SIGNAL_FAMILY,
                    ),
                    choices=("cross_asset_beta_lag_v1", _STRESS_V2_SIGNAL_FAMILY),
                    help="Signal family to watch (default: $NAUTILUS_STAGE2_SIGNAL_FAMILY or cross_asset_beta_lag_v1)")
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
    global _REPORTS_ROOT, _DATA_ROOT, _REPO_ROOT, _STATUS_PATH_OVERRIDE, _PYTHON_EXE_OVERRIDE, _SIGNAL_FAMILY
    _REPO_ROOT = workdir
    _SIGNAL_FAMILY = args.signal_family

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

    # Startup reconciliation — runs before the concurrency lock so that a
    # previous crash does not permanently block the watcher.
    recovery = _startup_reconciliation(log)

    # Acquire watcher concurrency lock
    acquired, reason = lock.acquire()
    if not acquired:
        print(f"[FATAL] {reason}", file=sys.stderr)
        _write_status(error=f"Concurrency lock not acquired: {reason}")
        sys.exit(1)

    try:
        _run_watcher_cycle(log, args, state, recovery=recovery)
    finally:
        lock.release()
        CaptureGuard.release()
        # Stop the SSL/urllib trigger feed thread cleanly before interpreter
        # shutdown to avoid SIGSEGV in OpenSSL cleanup (Python 3.13 issue).
        if _STRESS_V2_TRIGGER_FEED is not None:
            _STRESS_V2_TRIGGER_FEED.stop()


def _run_watcher_cycle(log: WatcherLogger, args: argparse.Namespace,
                       state: WatcherState | None = None,
                       recovery: dict[str, Any] | None = None) -> None:
    """Main polling cycle."""

    log.log("startup", poll_seconds=args.poll_seconds,
            stress_bps=args.stress_bps, once=args.once)

    # --- Step 1: Readiness check ---
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

    # Surface startup recovery result in the first status write
    recovery_fields: dict[str, Any] = {}
    if recovery:
        recovery_fields["startup_recovery"] = recovery

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
        legacy_gate_passed = gate_result.get("gate_passed", False)
        btc_1h = gate_result.get("btc_1h_bps") or 0.0
        market_v = gate_result.get("market_verdict", "?")
        accel_v = gate_result.get("accel_verdict", "?")
        gate_err = gate_result.get("error")

        trigger_status: StressTriggerResult | None = None
        if _SIGNAL_FAMILY == _STRESS_V2_SIGNAL_FAMILY:
            trigger_ticks, trigger_feed_reason = _fetch_stress_v2_trigger_ticks()
            trigger_status = _evaluate_stress_v2_capture_gate(
                gate_result,
                trigger_ticks,
                trigger_feed_reason=trigger_feed_reason,
            )
            gate_passed = trigger_status.triggered
        else:
            gate_passed = legacy_gate_passed

        log.log("gate_result", gate_passed=gate_passed, btc_1h_bps=btc_1h,
                market_verdict=market_v, accel_verdict=accel_v,
                error=gate_err)

        print(f"  BTC 1h move: {btc_1h:.1f} bps  (threshold: {args.stress_bps} bps)")
        print(f"  Market verdict: {market_v}  Accel verdict: {accel_v}")
        if trigger_status is not None:
            print(
                "  Stress-v2 trigger: "
                f"asset={trigger_status.source_asset} "
                f"move_30s={trigger_status.source_move_bps_30s} "
                f"stress={trigger_status.stress_confirmation_status} "
                f"reason={trigger_status.reason}"
            )
        print(f"  Gate passed: {gate_passed}")

        status_fields = trigger_status.as_status_fields() if trigger_status is not None else {}
        if trigger_status is not None:
            if trigger_status.reason in {
                "STRESS_V2_TRIGGER_FEED_STALE",
                "STRESS_V2_WAITING_FOR_TRIGGER",
                "STRESS_V2_IMPULSE_TRUE_STRESS_FALSE",
                "STRESS_V2_STRESS_TRUE_IMPULSE_FALSE",
            }:
                status_fields["capture_status"] = trigger_status.reason
            elif trigger_status.triggered:
                status_fields["capture_status"] = "STRESS_V2_GATE_PASSED"

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
                **status_fields,
                **recovery_fields,
            )
            recovery_fields = {}  # only surface recovery on first poll

            if args.once:
                print("\n  --once mode: exiting after one poll cycle")
                break

            time.sleep(args.poll_seconds)
            continue

        # --- Stress confirmed! ---
        if _SIGNAL_FAMILY == _STRESS_V2_SIGNAL_FAMILY:
            print("  STRESS-V2 GATE PASSED — 30 bps/30s source impulse AND crypto-native stress")
        else:
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
                **status_fields,
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
                **status_fields,
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
            _notify(
                "Edge Miner: stress capture started",
                f"Signal: {_SIGNAL_FAMILY}\n"
                f"Trigger: {btc_1h:.1f} bps / 30s impulse\n"
                f"Market: {market_v} + {accel_v}\n"
                f"Duration: {_STRESS_V2_DEFAULT_CAPTURE_SECONDS}s",
                urgency="normal",
            )
            _play(_SOUND_CAPTURE_START)

            capture_dir = _run_capture(log)

            if capture_dir is None:
                log.log("capture_attempt_failed")
                print("  Capture failed — see logs for details")
                _notify(
                    "Edge Miner: capture FAILED",
                    f"Signal: {_SIGNAL_FAMILY}\nCheck logs for details.",
                    urgency="critical",
                )
                _play(_SOUND_CAPTURE_FAILED)
                _write_status(
                    readiness_status="PASSED",
                    gate_status="PASSED",
                    last_capture_status="FAILED",
                    error="capture_attempt_failed",
                    **status_fields,
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
                    **status_fields,
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
                _notify(
                    "Edge Miner: capture complete ✓",
                    f"Status: {last_capture_status}\n"
                    f"Dir: {Path(capture_dir).name}\n"
                    f"Corpus: {validated_count} / {_CORPUS_TARGET_WINDOWS} windows\n"
                    f"{remaining} more needed before frozen-grid rerun",
                    urgency="normal",
                )
            else:
                print(f"  Stage 2 evaluation READY — {_CORPUS_TARGET_WINDOWS}+ validated FULL_ACTIVE captures")
                print("  (Evaluation is not run by the watcher; run manually)")
                _notify(
                    "Edge Miner: CORPUS_READY_FOR_RERUN",
                    f"Status: {last_capture_status}\n"
                    f"Corpus: {validated_count} / {_CORPUS_TARGET_WINDOWS} windows\n"
                    "Frozen-grid discovery can now run manually.",
                    urgency="critical",
                )
            _play(_SOUND_CAPTURE_COMPLETE)

            _write_status(
                readiness_status="PASSED",
                gate_status="PASSED",
                current_btc_1h_move_bps=btc_1h,
                acceleration_status=accel_v,
                last_capture_status=last_capture_status,
                last_capture_dir=capture_dir,
                last_validation_status=verdict,
                stress_condition="FULL_ACTIVE_STRESS",
                **status_fields,
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
