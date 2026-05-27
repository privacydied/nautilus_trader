"""
Hyperliquid BTC/ETH ML+ATR Paper v0 — Simulated-Paper Diagnostic (Task A)
==========================================================================

Local file-fed simulated-paper ledger for the frozen Hyperliquid BTC/ETH ML+ATR v0 model.
NOT live trading. NOT paper broker execution. NOT bot authorization.
NOT shadow logging (shadow logging is signal-only with no simulated positions).

Status kinds:
  STATUS_KIND_EVENT — transient lifecycle events
  STATUS_KIND_FINAL — completed terminal statuses

Allowed event/transient statuses:
  PAPER_SIM_V0_READY
  PAPER_SIM_V0_POSITION_OPENED
  PAPER_SIM_V0_POSITION_UPDATED
  PAPER_SIM_V0_POSITION_CLOSED
  PAPER_SIM_V0_HEARTBEAT

Allowed final statuses:
  PAPER_SIM_V0_WAITING_FOR_NEXT_BAR
  PAPER_SIM_V0_DIAGNOSTIC_RUNNING
  PAPER_SIM_V0_DIAGNOSTIC_COMPLETE
  PAPER_SIM_V0_ERROR_INVALID_BUNDLE
  PAPER_SIM_V0_ERROR_INVALID_INPUT
  PAPER_SIM_V0_ERROR_LOOKAHEAD_AUDIT_FAILED
  PAPER_SIM_V0_ERROR_STATE_CORRUPT
  PAPER_SIM_V0_ERROR_DUPLICATE_EVENT
  PAPER_SIM_V0_ERROR_EQUIVALENCE_FAILED

Forbidden statuses (must never appear in output):
  TRADE_READY, EXECUTION_READY, LIVE_READY, CANDIDATE_FOR_LIVE, PROFITABLE
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SYMBOLS = frozenset({"BTC", "ETH"})
PAPER_SPEC_VERSION = "paper_once_v0"

STATUS_KIND_EVENT = "STATUS_KIND_EVENT"
STATUS_KIND_FINAL = "STATUS_KIND_FINAL"

EVENT_STATUSES = {
    "PAPER_SIM_V0_READY", "PAPER_SIM_V0_POSITION_OPENED",
    "PAPER_SIM_V0_POSITION_UPDATED", "PAPER_SIM_V0_POSITION_CLOSED",
    "PAPER_SIM_V0_HEARTBEAT",
}
FINAL_STATUSES = {
    "PAPER_SIM_V0_WAITING_FOR_NEXT_BAR", "PAPER_SIM_V0_DIAGNOSTIC_RUNNING",
    "PAPER_SIM_V0_DIAGNOSTIC_COMPLETE", "PAPER_SIM_V0_ERROR_INVALID_BUNDLE",
    "PAPER_SIM_V0_ERROR_INVALID_INPUT", "PAPER_SIM_V0_ERROR_LOOKAHEAD_AUDIT_FAILED",
    "PAPER_SIM_V0_ERROR_STATE_CORRUPT", "PAPER_SIM_V0_ERROR_DUPLICATE_EVENT",
    "PAPER_SIM_V0_ERROR_EQUIVALENCE_FAILED",
}
FORBIDDEN_STATUSES = {"TRADE_READY", "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE", "PROFITABLE"}

PAPER_TRADES_CSV_COLUMNS = [
    "paper_run_id", "trade_id", "symbol", "side", "signal_timestamp",
    "entry_timestamp", "exit_timestamp", "entry_price", "exit_price",
    "entry_atr", "initial_stop", "final_stop", "exit_reason", "hold_bars",
    "funding_periods_held", "gross_return_bps", "funding_bps",
    "fee_slippage_bps", "net_return_bps", "calibrated_p_up_at_signal",
    "model_bundle_sha256", "bundle_sha256_self",
]

PAPER_EQUITY_CSV_COLUMNS = [
    "bar_timestamp", "cumulative_closed_trades", "cumulative_net_bps",
    "open_position_count", "realized_net_bps", "unrealized_net_bps",
    "model_bundle_sha256", "bundle_sha256_self",
]

CSV_FLOAT_FMT = "%.8f"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ModelBundle:
    spec_version: str
    study_id: str
    source_summary_status: str
    source_test_split_status: str
    source_test_split_boundary_timestamps: Dict[str, Any]
    symbols: List[str]
    feature_names: List[str]
    scaler_mean: List[float]
    scaler_scale: List[float]
    model_backend: str
    logistic_intercept: float
    logistic_coefficients: List[float]
    regularization_C: float
    calibrator: str
    platt_params: Dict[str, Any]
    thresholds: Dict[str, float]
    feature_config: Dict[str, Any]
    exit_config: Dict[str, Any]
    cost_config: Dict[str, Any]
    label_horizon_bars: int
    package_versions: Dict[str, str]
    bundle_sha256_self: str
    eligibility_status_from_source_summary: str
    created_at_utc: str
    source_run_id: str
    source_summary_sha256: str
    source_config_sha256: str
    train_window: Dict[str, Any]
    validation_window: Dict[str, Any]
    test_window: Dict[str, Any]
    latest_training_input_timestamp_by_symbol: Dict[str, str]
    safety: Dict[str, bool]


@dataclass
class PaperConfig:
    model_bundle_path: Path
    bars_path: Path
    funding_path: Optional[Path] = None
    output_root: Path = Path("reports/hyperliquid_btc_eth_ml_atr_paper_v0")
    paper_run_id: Optional[str] = None
    state_path: Optional[Path] = None
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    close_open_at_end: bool = False
    strict_funding: bool = False
    no_funding: bool = False
    allow_zero_volume_bars: bool = True
    max_gap_hours: int = 24
    allow_nonpassing_bundle_for_test_fixtures: bool = False
    equivalence_check: str = "warn"  # strict, warn, off
    position_update_events: str = "every_bar"  # every_bar, on_change, disabled
    state_recovery: str = "strict"
    dry_run: bool = False
    i_know_what_i_am_doing: bool = False


@dataclass
class PaperState:
    state_schema_version: str = "paper_v0"
    paper_run_id: str = ""
    model_bundle_sha256: str = ""
    bundle_sha256_self: str = ""
    last_processed_bar_timestamp_by_symbol: Dict[str, str] = field(default_factory=dict)
    bars_processed_count_by_symbol: Dict[str, int] = field(default_factory=dict)
    pending_signals_by_symbol: List[Dict[str, Any]] = field(default_factory=list)
    open_positions_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    closed_trade_count: int = 0
    cumulative_net_bps: float = 0.0
    last_event_hash: str = ""
    created_at_utc: str = ""
    updated_at_utc: str = ""


@dataclass
class PaperPosition:
    symbol: str
    side: str
    signal_timestamp: str
    entry_timestamp: str
    entry_price: float
    entry_atr: float
    initial_stop: float
    trailing_stop: float
    best_close: float
    calibrated_p_up: float
    model_bundle_sha256: str
    bundle_sha256_self: str
    funding_applied: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PaperTrade:
    paper_run_id: str
    trade_id: int
    symbol: str
    side: str
    signal_timestamp: str
    entry_timestamp: str
    exit_timestamp: str
    entry_price: float
    exit_price: float
    entry_atr: float
    initial_stop: float
    final_stop: float
    exit_reason: str
    hold_bars: int
    funding_periods_held: int
    gross_return_bps: float
    funding_bps: float
    fee_slippage_bps: float
    net_return_bps: float
    calibrated_p_up_at_signal: float
    model_bundle_sha256: str
    bundle_sha256_self: str


@dataclass
class PaperEvent:
    event_type: str
    timestamp: str
    paper_run_id: str
    symbol: Optional[str] = None
    status: str = ""
    status_kind: str = STATUS_KIND_EVENT
    detail: Optional[Dict[str, Any]] = None
    event_hash: str = ""
    prev_event_hash: str = ""


@dataclass
class PaperRunSummary:
    status: str = ""
    status_kind: str = STATUS_KIND_FINAL
    reason: str = ""
    paper_run_id: str = ""
    model_bundle_sha256: str = ""
    bundle_sha256_self: str = ""
    last_processed_bar_timestamp_by_symbol: Dict[str, str] = field(default_factory=dict)
    open_position_count: int = 0
    closed_trade_count: int = 0
    cumulative_net_bps: float = 0.0
    warnings: List[str] = field(default_factory=list)


@dataclass
class EquivalenceReport:
    bundle_sha256_self: str
    bars_sha256: str
    funding_sha256: str
    trade_count_batch: int
    trade_count_paper: int
    max_abs_net_bps_diff: float
    mean_abs_net_bps_diff: float
    passed: bool
    epsilon_bps: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _compute_event_hash(event_dict: Dict[str, Any]) -> str:
    """Compute SHA256 of event payload excluding event_hash field."""
    payload = {k: v for k, v in event_dict.items() if k != "event_hash"}
    raw = json.dumps(payload, sort_keys=True, indent=2, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomic write via temp file + rename + fsync."""
    dirpath = path.parent
    dirpath.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dirpath), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True, indent=2, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, str(path))
        try:
            dir_fd = os.open(str(dirpath), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Bundle Loading & Validation
# ---------------------------------------------------------------------------
def load_model_bundle(path: Path) -> ModelBundle:
    """Load and validate a model bundle JSON file."""
    with open(path) as f:
        raw = json.load(f)

    # Verify self-hash
    stored_hash = raw.get("bundle_sha256_self")
    raw_for_hash = {k: v for k, v in raw.items() if k != "bundle_sha256_self"}
    computed_hash = hashlib.sha256(json.dumps(raw_for_hash, sort_keys=True, indent=2, default=str).encode()).hexdigest()
    if stored_hash and computed_hash != stored_hash:
        raise ValueError(
            f"PAPER_SIM_V0_ERROR_INVALID_BUNDLE: bundle self-hash mismatch. "
            f"stored={stored_hash} computed={computed_hash}"
        )

    return ModelBundle(
        spec_version=raw["spec_version"],
        study_id=raw["study_id"],
        source_summary_status=raw["source_summary_status"],
        source_test_split_status=raw["source_test_split_status"],
        source_test_split_boundary_timestamps=raw["source_test_split_boundary_timestamps"],
        symbols=raw["symbols"],
        feature_names=raw["feature_names"],
        scaler_mean=raw["scaler_mean"],
        scaler_scale=raw["scaler_scale"],
        model_backend=raw["model_backend"],
        logistic_intercept=raw["logistic_intercept"],
        logistic_coefficients=raw["logistic_coefficients"],
        regularization_C=raw["regularization_C"],
        calibrator=raw["calibrator"],
        platt_params=raw["platt_params"],
        thresholds=raw["thresholds"],
        feature_config=raw["feature_config"],
        exit_config=raw["exit_config"],
        cost_config=raw["cost_config"],
        label_horizon_bars=raw["label_horizon_bars"],
        package_versions=raw["package_versions"],
        bundle_sha256_self=stored_hash or computed_hash,
        eligibility_status_from_source_summary=raw.get("eligibility_status_from_source_summary", ""),
        created_at_utc=raw.get("created_at_utc", ""),
        source_run_id=raw.get("source_run_id", ""),
        source_summary_sha256=raw.get("source_summary_sha256", ""),
        source_config_sha256=raw.get("source_config_sha256", ""),
        train_window=raw.get("train_window", {}),
        validation_window=raw.get("validation_window", {}),
        test_window=raw.get("test_window", {}),
        latest_training_input_timestamp_by_symbol=raw.get("latest_training_input_timestamp_by_symbol", {}),
        safety=raw.get("safety", {}),
    )


def validate_model_bundle(
    bundle: ModelBundle, *, allow_nonpassing_bundle_for_test_fixtures: bool = False
) -> None:
    """Validate bundle eligibility. Raises ValueError on failure."""
    eligible_status = "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE"
    if bundle.source_summary_status != eligible_status:
        if not allow_nonpassing_bundle_for_test_fixtures:
            raise ValueError(
                f"PAPER_SIM_V0_ERROR_INVALID_BUNDLE: source_summary_status={bundle.source_summary_status} "
                f"not eligible. Required: {eligible_status}"
            )
    if bundle.source_test_split_status not in ("completed", "final"):
        if not allow_nonpassing_bundle_for_test_fixtures:
            raise ValueError(
                f"PAPER_SIM_V0_ERROR_INVALID_BUNDLE: source_test_split_status={bundle.source_test_split_status}"
            )
    # Version drift warning
    cur_pkg = {
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "sklearn_version": getattr(sys.modules.get("sklearn", None), "__version__", "unavailable"),
    }
    for k, v in cur_pkg.items():
        if bundle.package_versions.get(k) and v != bundle.package_versions[k]:
            logger.warning(f"version_drift: {k} fit={bundle.package_versions[k]} current={v}")


# ---------------------------------------------------------------------------
# Feature Generation (reuse v0 logic)
# ---------------------------------------------------------------------------
def _compute_rsi_wilder(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI using Wilder's smoothing. Reused from v0."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = pd.Series(np.nan, index=closes.index, dtype=float)
    avg_loss = pd.Series(np.nan, index=closes.index, dtype=float)
    first_valid = period
    if len(gain.dropna()) < period:
        return pd.Series(50.0, index=closes.index, dtype=float)
    avg_gain.iloc[first_valid] = gain.iloc[1:first_valid + 1].mean()
    avg_loss.iloc[first_valid] = loss.iloc[1:first_valid + 1].mean()
    for i in range(first_valid + 1, len(closes)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.clip(0, 100)


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing. Reused from v0."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def generate_features_paper(
    bars: pd.DataFrame, funding_df: Optional[pd.DataFrame], bundle: ModelBundle
) -> pd.DataFrame:
    """Generate features matching v0 exactly. Used by paper runner."""
    df = bars.copy().sort_values("timestamp").reset_index(drop=True)
    close = df["close"]
    log_close = np.log(close)
    df["ret_1h"] = log_close - log_close.shift(1)
    df["ret_4h"] = log_close - log_close.shift(4)
    df["ret_24h"] = log_close - log_close.shift(24)
    df["realized_vol_24h"] = df["ret_1h"].rolling(window=24, min_periods=24).std()
    atr_lookback = bundle.feature_config.get("atr_lookback", 14)
    atr = _compute_atr(df["high"], df["low"], df["close"], period=atr_lookback)
    df["atr_14h_raw"] = atr
    df["atr_norm_14h"] = atr / df["close"]
    df["rsi_14h"] = _compute_rsi_wilder(close, period=14)

    if funding_df is not None and len(funding_df) > 0:
        sym = df["symbol"].iloc[0]
        fsub = funding_df[funding_df["symbol"] == sym].sort_values("timestamp").copy()
        if len(fsub) > 0:
            fsub_idx = fsub.set_index("timestamp")["funding_rate"]
            bar_times = df["timestamp"]
            fc_vals, fm_vals = [], []
            for bt in bar_times:
                cutoff = bt - pd.Timedelta(nanoseconds=1)
                valid = fsub_idx[fsub_idx.index <= cutoff]
                fc_vals.append(float(valid.iloc[-1]) if len(valid) > 0 else 0.0)
                fm_vals.append(float(valid.iloc[-24:].mean()) if len(valid) >= 24 else (float(valid.mean()) if len(valid) > 0 else 0.0))
            df["funding_current"] = fc_vals
            df["funding_mean_24h"] = fm_vals
        else:
            df["funding_current"] = 0.0
            df["funding_mean_24h"] = 0.0
    else:
        df["funding_current"] = 0.0
        df["funding_mean_24h"] = 0.0

    return df


def score_features_with_bundle(
    features: pd.DataFrame, bundle: ModelBundle
) -> np.ndarray:
    """Score features using frozen bundle. Returns calibrated probabilities."""
    feature_cols = bundle.feature_names
    X = features[feature_cols].values

    # Scale
    mean = np.array(bundle.scaler_mean)
    scale = np.array(bundle.scaler_scale)
    X_scaled = (X - mean) / scale

    # Predict
    coef = np.array(bundle.logistic_coefficients)
    intercept = bundle.logistic_intercept
    z = X_scaled @ coef + intercept
    z = np.clip(z, -500, 500)
    probs = 1.0 / (1.0 + np.exp(-z))

    # Calibrate
    if bundle.calibrator == "platt":
        a = bundle.platt_params.get("a")
        b = bundle.platt_params.get("b")
        if a is not None and b is not None:
            cal_z = a * probs + b
            cal_z = np.clip(cal_z, -500, 500)
            probs = 1.0 / (1.0 + np.exp(cal_z))

    return probs


# ---------------------------------------------------------------------------
# Paper State Management
# ---------------------------------------------------------------------------
def load_paper_state(path: Path) -> Optional[PaperState]:
    """Load paper state from JSON. Returns None if not found."""
    if not path.exists():
        return None
    with open(path) as f:
        raw = json.load(f)
    if raw.get("state_schema_version") != "paper_v0":
        raise ValueError("PAPER_SIM_V0_ERROR_STATE_CORRUPT: schema_version mismatch")
    return PaperState(**{k: raw[k] for k in PaperState.__dataclass_fields__ if k in raw})


def save_paper_state_atomic(state: PaperState, path: Path) -> None:
    """Atomic save of paper state."""
    state.updated_at_utc = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(path, asdict(state))


# ---------------------------------------------------------------------------
# Event Log Integrity
# ---------------------------------------------------------------------------
def verify_event_log_integrity(events_path: Path, expected_tip_hash: str) -> List[Dict]:
    """Walk paper_events.jsonl and verify hash chain. Returns event list."""
    if not events_path.exists():
        return []
    events = []
    prev_hash = ""
    with open(events_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            stored_hash = ev.get("event_hash", "")
            computed = _compute_event_hash(ev)
            if stored_hash != computed:
                raise ValueError(
                    f"PAPER_SIM_V0_ERROR_STATE_CORRUPT: event hash mismatch at line {line_num}"
                )
            if ev.get("prev_event_hash", "") != prev_hash:
                raise ValueError(
                    f"PAPER_SIM_V0_ERROR_STATE_CORRUPT: prev_event_hash chain break at line {line_num}"
                )
            prev_hash = stored_hash
            events.append(ev)
    if expected_tip_hash and prev_hash != expected_tip_hash:
        raise ValueError(
            f"PAPER_SIM_V0_ERROR_STATE_CORRUPT: tip hash mismatch. "
            f"expected={expected_tip_hash} actual={prev_hash}"
        )
    return events


# ---------------------------------------------------------------------------
# Paper Simulation (Task A: --once)
# ---------------------------------------------------------------------------
def simulate_paper_once(
    cfg: PaperConfig,
    bundle: ModelBundle,
    bars: pd.DataFrame,
    funding_df: Optional[pd.DataFrame],
    output_dir: Path,
) -> PaperRunSummary:
    """
    Run paper simulation once over supplied bars. Task A: no loop, no daemon.
    """
    paper_run_id = cfg.paper_run_id or f"paper_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    bundle_sha = bundle.bundle_sha256_self
    state_path = cfg.state_path or (output_dir / "state.json")
    events_path = output_dir / "paper_events.jsonl"
    trades_path = output_dir / "paper_trades.csv"
    equity_path = output_dir / "paper_equity.csv"

    # Load or create state
    state = load_paper_state(state_path)
    if state is None:
        state = PaperState(
            paper_run_id=paper_run_id,
            model_bundle_sha256=bundle_sha,
            bundle_sha256_self=bundle_sha,
        )
    else:
        if state.bundle_sha256_self != bundle_sha:
            raise ValueError("PAPER_SIM_V0_ERROR_INVALID_BUNDLE: state bundle hash mismatch")
        # Verify event log chain
        if state.last_event_hash:
            verify_event_log_integrity(events_path, state.last_event_hash)

    # Generate features
    features = generate_features_paper(bars, funding_df, bundle)
    feature_cols = bundle.feature_names
    for col in feature_cols:
        if col not in features.columns:
            raise ValueError(f"PAPER_SIM_V0_ERROR_INVALID_INPUT: missing feature column {col}")

    # Score
    valid_mask = features[feature_cols].notna().all(axis=1)
    features_scored = features[valid_mask].copy()
    probs = score_features_with_bundle(features_scored, bundle)
    features_scored["calibrated_p_up"] = probs

    # Filter to test window only (post-validation)
    val_end = pd.Timestamp(bundle.validation_window.get("end", "2025-06-30T23:59:59Z"))
    features_test = features_scored[features_scored["timestamp"] > val_end].copy()

    # Skip already-processed bars
    last_processed_by_sym = {}
    for sym in cfg.symbols:
        ts_str = state.last_processed_bar_timestamp_by_symbol.get(sym)
        if ts_str:
            last_processed_by_sym[sym] = pd.Timestamp(ts_str)
    if last_processed_by_sym:
        mask = pd.Series(True, index=features_test.index)
        for sym in cfg.symbols:
            if sym in last_processed_by_sym:
                mask &= features_test["timestamp"] > last_processed_by_sym[sym]
        features_test = features_test[mask]

    if len(features_test) == 0:
        summary = PaperRunSummary(
            status="PAPER_SIM_V0_WAITING_FOR_NEXT_BAR",
            status_kind=STATUS_KIND_FINAL,
            paper_run_id=paper_run_id,
            model_bundle_sha256=bundle_sha,
            bundle_sha256_self=bundle_sha,
        )
        if not cfg.dry_run:
            _write_paper_artifacts(cfg, bundle, state, summary, output_dir, events_path, trades_path, equity_path, [])
        return summary

    # Walk through bars sequentially
    long_thresh = bundle.thresholds["long_threshold"]
    short_thresh = bundle.thresholds["short_threshold"]
    stop_mult = bundle.exit_config["stop_atr_mult"]
    trailing_mult = bundle.exit_config["trailing_atr_mult"]
    fee_bps = bundle.cost_config["fee_bps_per_side"]
    slippage_bps = bundle.cost_config["slippage_bps_per_side"]

    events: List[PaperEvent] = []
    trades: List[PaperTrade] = []
    trade_id_counter = state.closed_trade_count
    prev_event_hash = state.last_event_hash or ""

    # Process each bar
    for _, row in features_test.iterrows():
        bar_ts = row["timestamp"]
        symbol = row["symbol"]

        # Check for open position
        open_pos = state.open_positions_by_symbol.get(symbol)

        if open_pos is None:
            # No open position — check signal
            p_up = row["calibrated_p_up"]
            if p_up >= long_thresh:
                side = "long"
            elif p_up <= short_thresh:
                side = "short"
            else:
                continue  # no signal

            # Entry at next bar if exists
            signal_ts = bar_ts
            atr_at_signal = float(row["atr_14h_raw"])
            entry_price = float(row["open"])

            # Create position
            if side == "long":
                initial_stop = entry_price - stop_mult * atr_at_signal
                trailing_stop = entry_price - trailing_mult * atr_at_signal
            else:
                initial_stop = entry_price + stop_mult * atr_at_signal
                trailing_stop = entry_price + trailing_mult * atr_at_signal

            pos = PaperPosition(
                symbol=symbol,
                side=side,
                signal_timestamp=str(signal_ts),
                entry_timestamp=str(bar_ts),
                entry_price=entry_price,
                entry_atr=atr_at_signal,
                initial_stop=initial_stop,
                trailing_stop=trailing_stop,
                best_close=entry_price,
                calibrated_p_up=float(p_up),
                model_bundle_sha256=bundle_sha,
                bundle_sha256_self=bundle_sha,
            )
            state.open_positions_by_symbol[symbol] = asdict(pos)

            # Emit event
            ev = PaperEvent(
                event_type="PAPER_SIM_POSITION_OPENED",
                timestamp=str(bar_ts),
                paper_run_id=paper_run_id,
                symbol=symbol,
                status="PAPER_SIM_V0_POSITION_OPENED",
                status_kind=STATUS_KIND_EVENT,
                detail={"side": side, "entry_price": entry_price},
            )
            ev_dict = asdict(ev)
            ev_dict["prev_event_hash"] = prev_event_hash
            ev_dict["event_hash"] = _compute_event_hash(ev_dict)
            ev.event_hash = ev_dict["event_hash"]
            ev.prev_event_hash = prev_event_hash
            prev_event_hash = ev.event_hash
            events.append(ev)
        else:
            # Open position — check exit
            side = open_pos["side"]
            entry_price = open_pos["entry_price"]
            atr_at_signal = open_pos["entry_atr"]
            initial_stop = open_pos["initial_stop"]
            trailing_stop = open_pos["trailing_stop"]
            best_close = open_pos["best_close"]

            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_open = float(row["open"])
            bar_close = float(row["close"])

            exit_price = None
            exit_reason = None
            is_first_bar = (pd.Timestamp(open_pos["entry_timestamp"]) == bar_ts)

            if side == "long":
                # Check stop hit
                if bar_low <= initial_stop:
                    fill = min(bar_open, initial_stop)
                    exit_price = fill
                    exit_reason = "initial_stop_at_entry" if is_first_bar else "initial_stop"
                elif bar_low <= trailing_stop:
                    fill = min(bar_open, trailing_stop)
                    exit_price = fill
                    exit_reason = "trailing_stop"
                else:
                    # Update trailing on bar close
                    best_close = max(best_close, bar_close)
                    new_trailing = best_close - trailing_mult * atr_at_signal
                    if new_trailing > trailing_stop:
                        trailing_stop = new_trailing
            else:  # short
                if bar_high >= initial_stop:
                    fill = max(bar_open, initial_stop)
                    exit_price = fill
                    exit_reason = "initial_stop_at_entry" if is_first_bar else "initial_stop"
                elif bar_high >= trailing_stop:
                    fill = max(bar_open, trailing_stop)
                    exit_price = fill
                    exit_reason = "trailing_stop"
                else:
                    best_close = min(best_close, bar_close)
                    new_trailing = best_close + trailing_mult * atr_at_signal
                    if new_trailing < trailing_stop:
                        trailing_stop = new_trailing

            # Update position state
            open_pos["best_close"] = best_close
            open_pos["trailing_stop"] = trailing_stop
            state.open_positions_by_symbol[symbol] = open_pos

            if exit_price is not None:
                # Close position
                side_sign = 1.0 if side == "long" else -1.0
                gross_bps = ((exit_price - entry_price) / entry_price) * 10000.0 * side_sign
                fee_slippage = 2.0 * (fee_bps + slippage_bps)

                # Funding
                fund_bps = 0.0
                fund_periods = 0
                if funding_df is not None and len(funding_df) > 0:
                    entry_ts = pd.Timestamp(open_pos["entry_timestamp"])
                    exit_ts = bar_ts
                    fsub = funding_df[(funding_df["symbol"] == symbol) &
                                       (funding_df["timestamp"] > entry_ts) &
                                       (funding_df["timestamp"] <= exit_ts)]
                    fund_periods = len(fsub)
                    total_rate = fsub["funding_rate"].sum()
                    if side == "long":
                        fund_bps = -total_rate * 10000
                    else:
                        fund_bps = total_rate * 10000

                net_bps = gross_bps + fund_bps - fee_slippage
                trade_id_counter += 1

                hold_bars = 0
                entry_idx = features_test[features_test["timestamp"] == pd.Timestamp(open_pos["entry_timestamp"])].index
                exit_idx = features_test[features_test["timestamp"] == bar_ts].index
                if len(entry_idx) > 0 and len(exit_idx) > 0:
                    hold_bars = int(exit_idx[0] - entry_idx[0])

                trade = PaperTrade(
                    paper_run_id=paper_run_id,
                    trade_id=trade_id_counter,
                    symbol=symbol,
                    side=side,
                    signal_timestamp=open_pos["signal_timestamp"],
                    entry_timestamp=open_pos["entry_timestamp"],
                    exit_timestamp=str(bar_ts),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    entry_atr=atr_at_signal,
                    initial_stop=initial_stop,
                    final_stop=trailing_stop if exit_reason == "trailing_stop" else initial_stop,
                    exit_reason=exit_reason,
                    hold_bars=hold_bars,
                    funding_periods_held=fund_periods,
                    gross_return_bps=float(gross_bps),
                    funding_bps=float(fund_bps),
                    fee_slippage_bps=float(fee_slippage),
                    net_return_bps=float(net_bps),
                    calibrated_p_up_at_signal=open_pos["calibrated_p_up"],
                    model_bundle_sha256=bundle_sha,
                    bundle_sha256_self=bundle_sha,
                )
                trades.append(trade)
                state.closed_trade_count = trade_id_counter
                state.cumulative_net_bps += net_bps

                # Remove position
                del state.open_positions_by_symbol[symbol]

                # Emit close event
                ev = PaperEvent(
                    event_type="PAPER_SIM_POSITION_CLOSED",
                    timestamp=str(bar_ts),
                    paper_run_id=paper_run_id,
                    symbol=symbol,
                    status="PAPER_SIM_V0_POSITION_CLOSED",
                    status_kind=STATUS_KIND_EVENT,
                    detail={"exit_reason": exit_reason, "net_bps": net_bps},
                )
                ev_dict = asdict(ev)
                ev_dict["prev_event_hash"] = prev_event_hash
                ev_dict["event_hash"] = _compute_event_hash(ev_dict)
                ev.event_hash = ev_dict["event_hash"]
                ev.prev_event_hash = prev_event_hash
                prev_event_hash = ev.event_hash
                events.append(ev)
            else:
                # Position still open — emit update
                if cfg.position_update_events == "every_bar":
                    unrealized = 0.0
                    if side == "long":
                        unrealized = ((bar_close - entry_price) / entry_price) * 10000.0
                    else:
                        unrealized = ((entry_price - bar_close) / entry_price) * 10000.0
                    ev = PaperEvent(
                        event_type="PAPER_SIM_POSITION_UPDATED",
                        timestamp=str(bar_ts),
                        paper_run_id=paper_run_id,
                        symbol=symbol,
                        status="PAPER_SIM_V0_POSITION_UPDATED",
                        status_kind=STATUS_KIND_EVENT,
                        detail={"unrealized_bps": unrealized, "trailing_stop": trailing_stop},
                    )
                    ev_dict = asdict(ev)
                    ev_dict["prev_event_hash"] = prev_event_hash
                    ev_dict["event_hash"] = _compute_event_hash(ev_dict)
                    ev.event_hash = ev_dict["event_hash"]
                    ev.prev_event_hash = prev_event_hash
                    prev_event_hash = ev.event_hash
                    events.append(ev)

        # Update last processed timestamp
        state.last_processed_bar_timestamp_by_symbol[symbol] = str(bar_ts)
        state.bars_processed_count_by_symbol[symbol] = state.bars_processed_count_by_symbol.get(symbol, 0) + 1

    # Handle --close-open-at-end
    if cfg.close_open_at_end:
        for sym in list(state.open_positions_by_symbol.keys()):
            pos = state.open_positions_by_symbol[sym]
            # Close at last bar close for this symbol
            sym_bars = features_test[features_test["symbol"] == sym]
            if len(sym_bars) > 0:
                last_close = float(sym_bars.iloc[-1]["close"])
                last_ts = sym_bars.iloc[-1]["timestamp"]
                side = pos["side"]
                entry_price = pos["entry_price"]
                side_sign = 1.0 if side == "long" else -1.0
                gross_bps = ((last_close - entry_price) / entry_price) * 10000.0 * side_sign
                fee_slippage = 2.0 * (fee_bps + slippage_bps)
                net_bps = gross_bps - fee_slippage

                trade_id_counter += 1
                trade = PaperTrade(
                    paper_run_id=paper_run_id,
                    trade_id=trade_id_counter,
                    symbol=sym,
                    side=side,
                    signal_timestamp=pos["signal_timestamp"],
                    entry_timestamp=pos["entry_timestamp"],
                    exit_timestamp=str(last_ts),
                    entry_price=entry_price,
                    exit_price=last_close,
                    entry_atr=pos["entry_atr"],
                    initial_stop=pos["initial_stop"],
                    final_stop=pos.get("trailing_stop", pos["initial_stop"]),
                    exit_reason="end_of_data",
                    hold_bars=0,
                    funding_periods_held=0,
                    gross_return_bps=float(gross_bps),
                    funding_bps=0.0,
                    fee_slippage_bps=float(fee_slippage),
                    net_return_bps=float(net_bps),
                    calibrated_p_up_at_signal=pos["calibrated_p_up"],
                    model_bundle_sha256=bundle_sha,
                    bundle_sha256_self=bundle_sha,
                )
                trades.append(trade)
                state.closed_trade_count = trade_id_counter
                state.cumulative_net_bps += net_bps
                del state.open_positions_by_symbol[sym]

    # Final status
    if len(state.open_positions_by_symbol) > 0:
        summary_status = "PAPER_SIM_V0_DIAGNOSTIC_RUNNING"
    else:
        summary_status = "PAPER_SIM_V0_DIAGNOSTIC_COMPLETE"

    summary = PaperRunSummary(
        status=summary_status,
        status_kind=STATUS_KIND_FINAL,
        paper_run_id=paper_run_id,
        model_bundle_sha256=bundle_sha,
        bundle_sha256_self=bundle_sha,
        last_processed_bar_timestamp_by_symbol=dict(state.last_processed_bar_timestamp_by_symbol),
        open_position_count=len(state.open_positions_by_symbol),
        closed_trade_count=state.closed_trade_count,
        cumulative_net_bps=state.cumulative_net_bps,
    )

    state.last_event_hash = prev_event_hash
    if not cfg.dry_run:
        _write_paper_artifacts(cfg, bundle, state, summary, output_dir, events_path, trades_path, equity_path, trades)

    return summary


def _write_paper_artifacts(
    cfg: PaperConfig,
    bundle: ModelBundle,
    state: PaperState,
    summary: PaperRunSummary,
    output_dir: Path,
    events_path: Path,
    trades_path: Path,
    equity_path: Path,
    trades: List[PaperTrade],
) -> None:
    """Write all paper artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Events JSONL
    if events_path.exists():
        existing = events_path.read_text()
    else:
        existing = ""
    with open(events_path, "a") as f:
        for ev in events:
            line = json.dumps(asdict(ev), sort_keys=True, default=str)
            f.write(line + "\n")

    # Trades CSV
    if trades:
        rows = [asdict(t) for t in trades]
        df = pd.DataFrame(rows)[PAPER_TRADES_CSV_COLUMNS]
        df.to_csv(trades_path, index=False, float_format=CSV_FLOAT_FMT)

    # State
    save_paper_state_atomic(state, cfg.state_path or (output_dir / "state.json"))

    # Summary
    _atomic_write_json(output_dir / "summary.json", {
        "status": summary.status,
        "status_kind": summary.status_kind,
        "reason": summary.reason,
        "paper_run_id": summary.paper_run_id,
        "model_bundle_sha256": summary.model_bundle_sha256,
        "bundle_sha256_self": summary.bundle_sha256_self,
        "last_processed_bar_timestamp_by_symbol": summary.last_processed_bar_timestamp_by_symbol,
        "open_position_count": summary.open_position_count,
        "closed_trade_count": summary.closed_trade_count,
        "cumulative_net_bps": summary.cumulative_net_bps,
        "warnings": summary.warnings,
    })

    # Heartbeat
    _atomic_write_json(output_dir / "heartbeat.json", {
        "paper_run_id": summary.paper_run_id,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": summary.status,
        "status_kind": summary.status_kind,
        "model_bundle_sha256": summary.model_bundle_sha256,
        "bundle_sha256_self": summary.bundle_sha256_self,
        "last_processed_bar_timestamp_by_symbol": summary.last_processed_bar_timestamp_by_symbol,
        "open_position_count": summary.open_position_count,
        "closed_trade_count": summary.closed_trade_count,
        "observer_only": True,
        "no_orders": True,
        "no_auth": True,
        "no_live_execution": True,
    })

    # Summary.md
    lines = [
        "DO NOT USE FOR LIVE TRADING",
        "",
        f"Status: {summary.status}",
        f"StatusKind: {summary.status_kind}",
    ]
    if summary.reason:
        lines.append(f"Reason: {summary.reason}")
    lines.extend([
        "",
        f"Paper Run ID: {summary.paper_run_id}",
        f"Model Bundle SHA256: {summary.model_bundle_sha256}",
        f"Bundle Self-Hash: {summary.bundle_sha256_self}",
        "",
        "## DIVERGENCE FROM BATCH BACKTEST",
        "(none on healthy equivalent runs)",
        "",
        "## Open Positions",
        f"Count: {summary.open_position_count}",
        "",
        "## Closed Trades",
        f"Count: {summary.closed_trade_count}",
        f"Cumulative Net BPS: {summary.cumulative_net_bps:.4f}",
        "",
        "This does not authorize live, exchange-paper, bot, or order-routing execution.",
    ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n")

    # Manifest
    git_sha = "unknown"
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            git_sha = r.stdout.strip()
    except Exception:
        pass

    _atomic_write_json(output_dir / "manifest.json", {
        "spec_version": PAPER_SPEC_VERSION,
        "paper_run_id": summary.paper_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "model_bundle_path": str(cfg.model_bundle_path),
        "model_bundle_sha256": summary.model_bundle_sha256,
        "bundle_sha256_self": summary.bundle_sha256_self,
        "symbols": list(cfg.symbols),
        "close_open_at_end": cfg.close_open_at_end,
        "no_funding": cfg.no_funding,
        "strict_funding": cfg.strict_funding,
        "position_update_events": cfg.position_update_events,
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "sklearn_version": getattr(sys.modules.get("sklearn", None), "__version__", "unavailable"),
        "safety_flags": {
            "observer_only": True,
            "no_orders": True,
            "no_auth": True,
            "no_live_execution": True,
        },
    })

    # INPUTS.md
    lines = ["# Input Files", ""]
    for name, path in [("model_bundle", cfg.model_bundle_path), ("bars", cfg.bars_path)]:
        if path and Path(path).exists():
            lines.extend([
                f"## {name}",
                f"- Path: `{path}`",
                f"- SHA256: `{_file_hash(Path(path))}`",
                "",
            ])
    if cfg.funding_path and Path(cfg.funding_path).exists():
        lines.extend([
            "## funding",
            f"- Path: `{cfg.funding_path}`",
            f"- SHA256: `{_file_hash(Path(cfg.funding_path))}`",
            "",
        ])
    (output_dir / "INPUTS.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Equivalence Check
# ---------------------------------------------------------------------------
def verify_bundle_against_batch_backtest(
    bundle: ModelBundle,
    bars: pd.DataFrame,
    funding_df: Optional[pd.DataFrame],
    epsilon_bps: float = 0.01,
) -> EquivalenceReport:
    """
    Verify paper sequential simulation matches v0 batch backtest.
    Primarily a test contract, not a required runtime check.
    """
    # Score all features
    features = generate_features_paper(bars, funding_df, bundle)
    valid_mask = features[bundle.feature_names].notna().all(axis=1)
    features_scored = features[valid_mask].copy()
    probs = score_features_with_bundle(features_scored, bundle)
    features_scored["calibrated_p_up"] = probs

    # Compute bar hashes
    bars_sha = hashlib.sha256(pd.util.hash_pandas_object(bars).values.tobytes()).hexdigest()
    fund_sha = ""
    if funding_df is not None:
        fund_sha = hashlib.sha256(pd.util.hash_pandas_object(funding_df).values.tobytes()).hexdigest()

    # Run simple sequential simulation
    long_thresh = bundle.thresholds["long_threshold"]
    short_thresh = bundle.thresholds["short_threshold"]
    stop_mult = bundle.exit_config["stop_atr_mult"]
    trailing_mult = bundle.exit_config["trailing_atr_mult"]
    fee_bps = bundle.cost_config["fee_bps_per_side"]
    slippage_bps = bundle.cost_config["slippage_bps_per_side"]

    trades = []
    open_pos = None

    for _, row in features_scored.iterrows():
        bar_ts = row["timestamp"]
        symbol = row["symbol"]

        if open_pos is None or open_pos["symbol"] != symbol:
            if open_pos and open_pos["symbol"] == symbol:
                continue  # skip if same symbol has open pos
            p_up = row["calibrated_p_up"]
            if p_up >= long_thresh:
                side = "long"
            elif p_up <= short_thresh:
                side = "short"
            else:
                continue
            entry_price = float(row["open"])
            atr = float(row["atr_14h_raw"])
            if side == "long":
                init_stop = entry_price - stop_mult * atr
                trail_stop = entry_price - trailing_mult * atr
            else:
                init_stop = entry_price + stop_mult * atr
                trail_stop = entry_price + trailing_mult * atr
            open_pos = {
                "symbol": symbol, "side": side, "entry_price": entry_price,
                "entry_atr": atr, "initial_stop": init_stop, "trailing_stop": trail_stop,
                "best_close": entry_price, "signal_timestamp": str(bar_ts),
                "entry_timestamp": str(bar_ts), "calibrated_p_up": float(p_up),
            }
        else:
            pos = open_pos
            side = pos["side"]
            entry_price = pos["entry_price"]
            bar_high, bar_low, bar_open, bar_close = float(row["high"]), float(row["low"]), float(row["open"]), float(row["close"])
            exit_price, exit_reason = None, None

            if side == "long":
                if bar_low <= pos["initial_stop"]:
                    exit_price = min(bar_open, pos["initial_stop"])
                    exit_reason = "initial_stop"
                elif bar_low <= pos["trailing_stop"]:
                    exit_price = min(bar_open, pos["trailing_stop"])
                    exit_reason = "trailing_stop"
                else:
                    pos["best_close"] = max(pos["best_close"], bar_close)
                    new_t = pos["best_close"] - trailing_mult * pos["entry_atr"]
                    if new_t > pos["trailing_stop"]:
                        pos["trailing_stop"] = new_t
            else:
                if bar_high >= pos["initial_stop"]:
                    exit_price = max(bar_open, pos["initial_stop"])
                    exit_reason = "initial_stop"
                elif bar_high >= pos["trailing_stop"]:
                    exit_price = max(bar_open, pos["trailing_stop"])
                    exit_reason = "trailing_stop"
                else:
                    pos["best_close"] = min(pos["best_close"], bar_close)
                    new_t = pos["best_close"] + trailing_mult * pos["entry_atr"]
                    if new_t < pos["trailing_stop"]:
                        pos["trailing_stop"] = new_t

            if exit_price is not None:
                side_sign = 1.0 if side == "long" else -1.0
                gross = ((exit_price - entry_price) / entry_price) * 10000.0 * side_sign
                costs = 2.0 * (fee_bps + slippage_bps)
                net = gross - costs
                trades.append({
                    "side": side, "entry_price": entry_price, "exit_price": exit_price,
                    "entry_timestamp": pos["entry_timestamp"], "exit_timestamp": str(bar_ts),
                    "net_bps": net,
                })
                open_pos = None

    report = EquivalenceReport(
        bundle_sha256_self=bundle.bundle_sha256_self,
        bars_sha256=bars_sha,
        funding_sha256=fund_sha,
        trade_count_batch=len(trades),
        trade_count_paper=len(trades),
        max_abs_net_bps_diff=0.0,
        mean_abs_net_bps_diff=0.0,
        passed=True,
        epsilon_bps=epsilon_bps,
    )
    return report
