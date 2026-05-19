"""Source-structure stress gates for cross-asset beta-lag research.

Phase 0 instrumentation only: Hawkes self-exciting volatility labels
and permutation entropy measurements on BTC/ETH source tick streams.

This is NOT a trading signal.
This is NOT an evaluation run.
This is NOT a target-return filter.

Safety mode: public_data_observer_only.
No execution, no orders, no API keys, no private keys, no live trading.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
from examples.strategies.venue_agnostic_signal_observer.artifact_metadata import build_metadata

# ---------------------------------------------------------------------------
# Verdict safety
# ---------------------------------------------------------------------------

ALLOWED_VERDICTS = frozenset({
    "HAWKES_STRESS_LABELS_READY",
    "HAWKES_INSUFFICIENT_SOURCE_EVENTS",
    "ENTROPY_MEASUREMENTS_READY",
    "ENTROPY_INSUFFICIENT_PATTERNS",
    "NO_HAWKES_STRESS_LABELS",
    "SOURCE_INPUT_UNUSABLE",
    "PHASE0_IMPLEMENTATION_READY",
})

FORBIDDEN_VERDICTS = frozenset({
    "SOURCE_STRUCTURE_STRESS_READY",
    "CANDIDATE",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
    "REJECTED",
    "TRADE_READY",
    "EXECUTION_READY",
    "BOT_READY",
    "CANDIDATE_FOR_LONGER_OBSERVATION_ARCHIVE_ONLY",
    "REJECTED_ARCHIVE_STRESS_BETA_LAG_V0",
    "SIGNAL_ABSENCE_AT_COST",
    "NEEDS_MORE_DATA_ARCHIVE_STRESS_WINDOWS",
})


def validate_verdict(verdict: str) -> str:
    """Validate a verdict string. Raises on forbidden or unknown verdicts."""
    if verdict in FORBIDDEN_VERDICTS:
        raise ValueError(
            f"Forbidden verdict '{verdict}' — Phase 0 source-structure gates "
            f"must not emit promotion or rejection verdicts. "
            f"Allowed: {sorted(ALLOWED_VERDICTS)}"
        )
    if verdict not in ALLOWED_VERDICTS:
        raise ValueError(
            f"Unknown verdict '{verdict}'. "
            f"Must be one of: {sorted(ALLOWED_VERDICTS)}"
        )
    return verdict


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceStructureStressConfig:
    """Frozen configuration for the Phase 0 source-structure stress gate."""

    # Bucket
    bucket_size_seconds: float = 1.0

    # Hawkes
    hawkes_event_threshold_bps: float = 5.0
    hawkes_tau_seconds: float = 30.0
    hawkes_branching_ratio: float = 0.5
    hawkes_min_events: int = 30
    hawkes_intensity_multiple_threshold: float = 3.0
    hawkes_prior_event_lookback_seconds: float = 60.0
    hawkes_min_prior_events: int = 3

    # Entropy
    entropy_embedding_dim: int = 3
    entropy_delay_buckets: int = 1
    entropy_window_seconds: int = 120
    entropy_min_patterns: int = 30

    # Label merge
    label_cooldown_seconds: float = 30.0
    merge_gap_seconds: float = 30.0

    def __post_init__(self):
        if self.hawkes_branching_ratio <= 0 or self.hawkes_branching_ratio >= 1:
            raise ValueError("hawkes_branching_ratio must be in (0, 1)")
        if self.hawkes_tau_seconds <= 0:
            raise ValueError("hawkes_tau_seconds must be positive")
        if self.entropy_embedding_dim < 2:
            raise ValueError("entropy_embedding_dim must be >= 2")

    @property
    def hawkes_alpha(self) -> float:
        """Derive alpha from branching ratio and tau.

        For exponential kernel alpha * exp(-dt / tau):
        integrated mass = alpha * tau, so eta = alpha * tau.
        Therefore alpha = eta / tau.
        """
        return self.hawkes_branching_ratio / self.hawkes_tau_seconds

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hawkes_alpha"] = self.hawkes_alpha
        return d

    def config_hash(self) -> str:
        """Deterministic hash of config values."""
        canonical = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceReturnBucket:
    """A single 1-second bucket of source tick returns."""
    bucket_ts_ns: int  # nanosecond timestamp of bucket end
    venue: str
    symbol: str
    last_price: float
    log_return_bps: float | None  # raw log-return from previous non-empty bucket
    signed_return_bps: float | None
    abs_return_bps: float | None


@dataclass(frozen=True)
class VolatilityEvent:
    """A source-side volatility event (abs return >= threshold)."""
    event_ts_ns: int
    venue: str
    symbol: str
    abs_return_bps: float
    signed_return_bps: float
    bucket_ts_ns: int


@dataclass(frozen=True)
class HawkesParams:
    tau_seconds: float
    branching_ratio: float
    alpha: float
    mu: float  # baseline events per second
    event_threshold_bps: float


@dataclass
class HawkesIntensityPoint:
    """Hawkes intensity at a specific timestamp."""
    ts_ns: int
    intensity_lambda: float
    mu: float
    lambda_over_mu: float
    event_count_total: int
    event_count_lookback: int
    decayed_sum: float


@dataclass
class PermutationEntropyPoint:
    """Normalized permutation entropy at a specific timestamp."""
    ts_ns: int
    normalized_entropy: float
    pattern_count: int
    status: str  # "computed" | "ENTROPY_INSUFFICIENT_PATTERNS"
    entropy_raw: float
    entropy_max: float


@dataclass(frozen=True)
class HawkesStressLabel:
    """A source-side Hawkes stress label."""
    label_id: str
    ts_ns: int
    ts_utc: str
    source_venue: str
    source_symbol: str
    hawkes_tau_seconds: float
    hawkes_branching_ratio: float
    hawkes_alpha: float
    intensity_lambda: float
    mu: float
    lambda_over_mu: float
    prior_event_count: int
    abs_return_bps: float
    signed_return_bps: float
    bucket_size_seconds: float
    event_threshold_bps: float
    entropy_value: float | None
    entropy_status: str
    reason: str
    config_hash: str


@dataclass(frozen=True)
class HawkesStressWindow:
    """A merged Hawkes stress window."""
    window_id: str
    window_start_ts_ns: int
    window_start_utc: str
    window_end_ts_ns: int
    window_end_utc: str
    source_venue: str
    source_symbol: str
    label_count: int
    label_ids: tuple[str, ...]
    # Per-label causal values preserved (NOT recomputed over merged span)
    max_lambda_over_mu: float
    min_lambda_over_mu: float
    mean_lambda_over_mu: float
    max_abs_return_bps: float
    entropy_values: tuple[float, ...]  # per-label entropy values if available


@dataclass
class SourceStructureStressSummary:
    """Summary of the full Phase 0 analysis."""
    study_id: str
    source_symbols: list[str]
    config_hash: str
    total_buckets: int
    total_volatility_events: int
    total_hawkes_labels: int
    total_hawkes_windows: int
    total_entropy_points: int
    entropy_min_normalized: float | None
    entropy_max_normalized: float | None
    entropy_mean_normalized: float | None
    hawkes_verdict: str
    entropy_verdict: str
    first_bucket_ts_ns: int | None
    last_bucket_ts_ns: int | None


# ---------------------------------------------------------------------------
# A. Build source return buckets
# ---------------------------------------------------------------------------

_NS_PER_SECOND = 1_000_000_000


def build_source_return_buckets(
    ticks: Sequence[TradeTickLite],
    bucket_size_seconds: float = 1.0,
) -> List[SourceReturnBucket]:
    """Convert source trade ticks into regular return buckets.

    Handles multiple symbols by grouping ticks per symbol and computing
    buckets independently per symbol.

    Uses last valid price in each bucket. Computes raw log-return bps
    from the previous non-empty bucket within the same symbol.
    No ATR / rolling normalization.

    Handles:
    - duplicate timestamps (last valid price wins)
    - zero/negative/non-finite prices (skipped)
    - empty tick list (returns empty list)
    - multiple symbols (buckets computed per symbol)
    """
    bucket_ns = int(bucket_size_seconds * _NS_PER_SECOND)

    # Group ticks by symbol
    by_symbol: Dict[str, List[TradeTickLite]] = {}
    for tick in ticks:
        price = tick.price
        if not (price > 0 and math.isfinite(price)):
            continue
        by_symbol.setdefault(tick.symbol, []).append(tick)

    all_buckets: List[SourceReturnBucket] = []

    for symbol in sorted(by_symbol.keys()):
        sym_ticks = by_symbol[symbol]
        sym_ticks.sort(key=lambda t: t.ts_event)
        venue = sym_ticks[0].venue

        # Group into buckets for this symbol
        bucket_prices: Dict[int, float] = {}
        for tick in sym_ticks:
            bucket_key = (tick.ts_event // bucket_ns) * bucket_ns
            bucket_prices[bucket_key] = tick.price  # last valid price wins

        if not bucket_prices:
            continue

        sorted_buckets = sorted(bucket_prices.items())
        prev_price: float | None = None

        for bucket_ts, last_price in sorted_buckets:
            if prev_price is not None and prev_price > 0:
                log_ret = math.log(last_price / prev_price) * 10_000
                signed_return_bps = log_ret
                abs_return_bps = abs(log_ret)
            else:
                log_ret = None
                signed_return_bps = None
                abs_return_bps = None

            all_buckets.append(SourceReturnBucket(
                bucket_ts_ns=bucket_ts,
                venue=venue,
                symbol=symbol,
                last_price=last_price,
                log_return_bps=log_ret,
                signed_return_bps=signed_return_bps,
                abs_return_bps=abs_return_bps,
            ))

            if log_ret is not None:
                prev_price = last_price
            elif prev_price is None:
                prev_price = last_price

    return all_buckets


# ---------------------------------------------------------------------------
# B. Build volatility events
# ---------------------------------------------------------------------------

def build_volatility_events(
    buckets: Sequence[SourceReturnBucket],
    threshold_bps: float = 5.0,
) -> List[VolatilityEvent]:
    """Convert source buckets into volatility events.

    Event rule: abs_return_bps >= threshold_bps.
    """
    events: List[VolatilityEvent] = []
    for b in buckets:
        if b.abs_return_bps is not None and b.abs_return_bps >= threshold_bps:
            events.append(VolatilityEvent(
                event_ts_ns=b.bucket_ts_ns,
                venue=b.venue,
                symbol=b.symbol,
                abs_return_bps=b.abs_return_bps,
                signed_return_bps=b.signed_return_bps or 0.0,
                bucket_ts_ns=b.bucket_ts_ns,
            ))
    return events


# ---------------------------------------------------------------------------
# C. Compute Hawkes intensity
# ---------------------------------------------------------------------------

def compute_hawkes_intensity(
    events: Sequence[VolatilityEvent],
    config: SourceStructureStressConfig,
    *,
    min_events: int | None = None,
) -> List[HawkesIntensityPoint]:
    """Compute fixed-parameter Hawkes intensity from source volatility events.

    Uses only past events (ti <= t). No future leakage.
    """
    tau = config.hawkes_tau_seconds
    eta = config.hawkes_branching_ratio
    alpha = config.hawkes_alpha  # eta / tau
    mu = config.hawkes_event_threshold_bps / config.hawkes_tau_seconds  # baseline
    min_ev = min_events if min_events is not None else config.hawkes_min_events
    prior_lookback_ns = int(config.hawkes_prior_event_lookback_seconds * _NS_PER_SECOND)
    min_prior = config.hawkes_min_prior_events

    if len(events) < min_ev:
        return []

    event_times_ns = [e.event_ts_ns for e in events]
    tau_ns = tau * _NS_PER_SECOND

    points: List[HawkesIntensityPoint] = []

    for i, t_ns in enumerate(event_times_ns):
        # Sum over past events only: j <= i
        decayed_sum = 0.0
        prior_count = 0
        for j in range(i + 1):  # j <= i means tj <= t
            dt_ns = t_ns - event_times_ns[j]
            if dt_ns < 0:
                continue
            if dt_ns <= prior_lookback_ns:
                prior_count += 1
            decayed_sum += math.exp(-dt_ns / tau_ns)

        intensity_lambda = mu + alpha * decayed_sum

        # Enforce finite non-negative
        if not math.isfinite(intensity_lambda) or intensity_lambda < 0:
            intensity_lambda = 0.0

        lambda_over_mu = intensity_lambda / mu if mu > 0 else 0.0

        points.append(HawkesIntensityPoint(
            ts_ns=t_ns,
            intensity_lambda=intensity_lambda,
            mu=mu,
            lambda_over_mu=lambda_over_mu,
            event_count_total=i + 1,
            event_count_lookback=prior_count,
            decayed_sum=decayed_sum,
        ))

    return points


# ---------------------------------------------------------------------------
# D. Compute permutation entropy
# ---------------------------------------------------------------------------

def _compute_permutation_entropy(
    values: Sequence[float],
    m: int = 3,
    delay: int = 1,
) -> tuple[float, float, float, int]:
    """Compute normalized permutation entropy for a sequence.

    Returns (entropy_raw, entropy_max, normalized_entropy, pattern_count).
    """
    n = len(values)
    needed = m + (m - 1) * (delay - 1) + 1  # minimum length for one pattern
    # Actually: need indices 0, delay, 2*delay, ..., (m-1)*delay
    # So need at least m + (m-1)*(delay-1) = m*delay - delay + m - m = m*delay - (delay-1)
    # Simpler: need last pattern at index (m-1)*delay, so n >= (m-1)*delay + m
    min_len = (m - 1) * delay + m

    if n < min_len:
        return (0.0, math.log(math.factorial(m)), 0.0, 0)

    pattern_counts: Dict[tuple[int, ...], int] = {}
    num_patterns = 0

    for i in range(n - (m - 1) * delay):
        # Extract ordinal pattern using values at [i, i+delay, i+2*delay, ...]
        vals = [values[i + j * delay] for j in range(m)]
        # Convert to rank permutation (argsort gives ordinal pattern)
        ranked = sorted(range(m), key=lambda k: vals[k])
        pattern = tuple(ranked.index(k) for k in range(m))

        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        num_patterns += 1

    if num_patterns == 0:
        return (0.0, math.log(math.factorial(m)), 0.0, 0)

    # Compute entropy
    entropy = 0.0
    for count in pattern_counts.values():
        p = count / num_patterns
        if p > 0:
            entropy -= p * math.log(p)

    entropy_max = math.log(math.factorial(m))
    normalized = entropy / entropy_max if entropy_max > 0 else 0.0

    return (entropy, entropy_max, normalized, num_patterns)


def compute_permutation_entropy_points(
    buckets: Sequence[SourceReturnBucket],
    config: SourceStructureStressConfig,
) -> List[PermutationEntropyPoint]:
    """Compute rolling normalized permutation entropy from source buckets.

    Uses only past buckets (no future leakage).
    """
    m = config.entropy_embedding_dim
    delay = config.entropy_delay_buckets
    window_ns = int(config.entropy_window_seconds * _NS_PER_SECOND)
    min_patterns = config.entropy_min_patterns

    # Extract signed returns with timestamps
    ts_vals = []
    for b in buckets:
        if b.signed_return_bps is not None:
            ts_vals.append((b.bucket_ts_ns, b.signed_return_bps))

    if not ts_vals:
        return []

    points: List[PermutationEntropyPoint] = []

    for i in range(len(ts_vals)):
        t_ns = ts_vals[i][0]
        # Window: all buckets with ts <= t_ns and within window_seconds
        window_start = t_ns - window_ns
        window_vals = [v for ts, v in ts_vals if window_start <= ts <= t_ns]

        entropy_raw, entropy_max, normalized, n_patterns = _compute_permutation_entropy(
            window_vals, m=m, delay=delay
        )

        if n_patterns < min_patterns:
            status = "ENTROPY_INSUFFICIENT_PATTERNS"
        else:
            status = "computed"

        points.append(PermutationEntropyPoint(
            ts_ns=t_ns,
            normalized_entropy=normalized,
            pattern_count=n_patterns,
            status=status,
            entropy_raw=entropy_raw,
            entropy_max=entropy_max,
        ))

    return points


# ---------------------------------------------------------------------------
# E. Build Hawkes stress labels
# ---------------------------------------------------------------------------

def build_hawkes_stress_labels(
    events: Sequence[VolatilityEvent],
    hawkes_points: Sequence[HawkesIntensityPoint],
    entropy_points: Sequence[PermutationEntropyPoint],
    config: SourceStructureStressConfig,
) -> List[HawkesStressLabel]:
    """Emit Hawkes source-stress labels where gates pass.

    Label requires:
    - source volatility event at or near label time
    - lambda_over_mu >= threshold
    - enough prior source events in lookback
    - cooldown gate passes

    Permutation entropy attached as metadata (NOT a pass/fail gate in v0).
    """
    intensity_map: Dict[int, HawkesIntensityPoint] = {}
    for pt in hawkes_points:
        intensity_map[pt.ts_ns] = pt

    entropy_map: Dict[int, PermutationEntropyPoint] = {}
    for ep in entropy_points:
        entropy_map[ep.ts_ns] = ep

    cooldown_ns = int(config.label_cooldown_seconds * _NS_PER_SECOND)
    last_label_ts: int | None = None

    labels: List[HawkesStressLabel] = []
    label_counter = 0

    for ev in events:
        # Cooldown check
        if last_label_ts is not None and (ev.event_ts_ns - last_label_ts) < cooldown_ns:
            continue

        # Get Hawkes intensity at this event time
        hpt = intensity_map.get(ev.event_ts_ns)
        if hpt is None:
            continue

        # Gate: lambda_over_mu
        if hpt.lambda_over_mu < config.hawkes_intensity_multiple_threshold:
            continue

        # Gate: prior events in lookback
        if hpt.event_count_lookback < config.hawkes_min_prior_events:
            continue

        # Entropy metadata (available if computed)
        ep = entropy_map.get(ev.event_ts_ns)
        entropy_val = ep.normalized_entropy if ep else None
        entropy_status = ep.status if ep else "not_computed"

        label_counter += 1
        ts_dt = datetime.fromtimestamp(ev.event_ts_ns / 1e9, tz=UTC)

        reason_parts = []
        if hpt.lambda_over_mu >= config.hawkes_intensity_multiple_threshold:
            reason_parts.append(f"lambda_over_mu={hpt.lambda_over_mu:.2f}")
        if hpt.event_count_lookback >= config.hawkes_min_prior_events:
            reason_parts.append(f"prior_events={hpt.event_count_lookback}")

        label = HawkesStressLabel(
            label_id=f"hawkes_stress_{ev.symbol}_{label_counter:06d}",
            ts_ns=ev.event_ts_ns,
            ts_utc=ts_dt.isoformat(),
            source_venue=ev.venue,
            source_symbol=ev.symbol,
            hawkes_tau_seconds=config.hawkes_tau_seconds,
            hawkes_branching_ratio=config.hawkes_branching_ratio,
            hawkes_alpha=config.hawkes_alpha,
            intensity_lambda=hpt.intensity_lambda,
            mu=hpt.mu,
            lambda_over_mu=hpt.lambda_over_mu,
            prior_event_count=hpt.event_count_lookback,
            abs_return_bps=ev.abs_return_bps,
            signed_return_bps=ev.signed_return_bps,
            bucket_size_seconds=config.bucket_size_seconds,
            event_threshold_bps=config.hawkes_event_threshold_bps,
            entropy_value=entropy_val,
            entropy_status=entropy_status,
            reason="; ".join(reason_parts),
            config_hash=config.config_hash(),
        )
        labels.append(label)
        last_label_ts = ev.event_ts_ns

    return labels


# ---------------------------------------------------------------------------
# F. Merge labels into windows causally
# ---------------------------------------------------------------------------

def merge_hawkes_stress_windows(
    labels: Sequence[HawkesStressLabel],
    merge_gap_seconds: float = 30.0,
) -> List[HawkesStressWindow]:
    """Merge Hawkes labels into windows with gaps <= merge_gap_seconds.

    Does NOT recompute Hawkes intensity or entropy over merged span.
    Preserves original per-label causal values.
    """
    if not labels:
        return []

    gap_ns = int(merge_gap_seconds * _NS_PER_SECOND)
    sorted_labels = sorted(labels, key=lambda l: l.ts_ns)

    windows: List[HawkesStressWindow] = []
    current_window_labels: List[HawkesStressLabel] = [sorted_labels[0]]

    for label in sorted_labels[1:]:
        if label.ts_ns - current_window_labels[-1].ts_ns <= gap_ns:
            current_window_labels.append(label)
        else:
            # Finalize current window
            windows.append(_build_window(current_window_labels))
            current_window_labels = [label]

    if current_window_labels:
        windows.append(_build_window(current_window_labels))

    return windows


def _build_window(labels: Sequence[HawkesStressLabel]) -> HawkesStressWindow:
    """Build a single HawkesStressWindow from a list of labels."""
    import uuid
    lam_oms = [l.lambda_over_mu for l in labels]
    abs_rets = [l.abs_return_bps for l in labels]
    ent_vals = [l.entropy_value for l in labels if l.entropy_value is not None]

    return HawkesStressWindow(
        window_id=f"hawkes_window_{uuid.uuid4().hex[:12]}",
        window_start_ts_ns=labels[0].ts_ns,
        window_start_utc=labels[0].ts_utc,
        window_end_ts_ns=labels[-1].ts_ns,
        window_end_utc=labels[-1].ts_utc,
        source_venue=labels[0].source_venue,
        source_symbol=labels[0].source_symbol,
        label_count=len(labels),
        label_ids=tuple(l.label_id for l in labels),
        max_lambda_over_mu=max(lam_oms),
        min_lambda_over_mu=min(lam_oms),
        mean_lambda_over_mu=sum(lam_oms) / len(lam_oms),
        max_abs_return_bps=max(abs_rets),
        entropy_values=tuple(ent_vals),
    )


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------

def write_source_structure_stress_artifacts(
    out_dir: Path,
    buckets: Sequence[SourceReturnBucket],
    events: Sequence[VolatilityEvent],
    hawkes_points: Sequence[HawkesIntensityPoint],
    entropy_points: Sequence[PermutationEntropyPoint],
    labels: Sequence[HawkesStressLabel],
    windows: Sequence[HawkesStressWindow],
    config: SourceStructureStressConfig,
    hawkes_verdict: str,
    entropy_verdict: str,
    run_args: Any | None = None,
) -> dict[str, Any]:
    """Write all Phase 0 artifacts to the output directory."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write_jsonl(path: Path, items: Sequence[Any]) -> tuple[int, int]:
        count = 0
        total_bytes = 0
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                if hasattr(item, "to_dict"):
                    line = json.dumps(item.to_dict())
                elif isinstance(item, dict):
                    line = json.dumps(item)
                else:
                    line = json.dumps(asdict(item))
                f.write(line + "\n")
                total_bytes += len(line.encode()) + 1
                count += 1
        return count, total_bytes

    def _write_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.rename(path)

    buckets_path = out_dir / "source_return_buckets.jsonl"
    events_path = out_dir / "source_volatility_events.jsonl"
    hawkes_path = out_dir / "hawkes_intensity_points.jsonl"
    entropy_path = out_dir / "permutation_entropy_points.jsonl"
    labels_path = out_dir / "hawkes_stress_labels.jsonl"
    windows_path = out_dir / "hawkes_stress_windows.jsonl"
    summary_path = out_dir / "source_structure_stress_summary.json"

    _write_jsonl(buckets_path, buckets)
    _write_jsonl(events_path, events)
    _write_jsonl(hawkes_path, hawkes_points)
    _write_jsonl(entropy_path, entropy_points)
    _write_jsonl(labels_path, labels)
    _write_jsonl(windows_path, windows)

    # Build summary
    ent_norms = [e.normalized_entropy for e in entropy_points if e.status == "computed"]
    ent_min = min(ent_norms) if ent_norms else None
    ent_max = max(ent_norms) if ent_norms else None
    ent_mean = sum(ent_norms) / len(ent_norms) if ent_norms else None

    summary = SourceStructureStressSummary(
        study_id="source_structure_stress_gates_v0",
        source_symbols=list(set(b.symbol for b in buckets)),
        config_hash=config.config_hash(),
        total_buckets=len(buckets),
        total_volatility_events=len(events),
        total_hawkes_labels=len(labels),
        total_hawkes_windows=len(windows),
        total_entropy_points=len(entropy_points),
        entropy_min_normalized=ent_min,
        entropy_max_normalized=ent_max,
        entropy_mean_normalized=ent_mean,
        hawkes_verdict=hawkes_verdict,
        entropy_verdict=entropy_verdict,
        first_bucket_ts_ns=buckets[0].bucket_ts_ns if buckets else None,
        last_bucket_ts_ns=buckets[-1].bucket_ts_ns if buckets else None,
    )

    meta = build_metadata(
        capture_mode="PHASE0_OBSERVER",
        run_args=run_args,
    )

    summary_dict = asdict(summary)
    summary_dict["_metadata"] = meta
    _write_json(summary_path, summary_dict)

    return {
        "buckets_path": str(buckets_path),
        "events_path": str(events_path),
        "hawkes_path": str(hawkes_path),
        "entropy_path": str(entropy_path),
        "labels_path": str(labels_path),
        "windows_path": str(windows_path),
        "summary_path": str(summary_path),
        "summary": asdict(summary),
    }


def load_hawkes_stress_labels(path: Path) -> List[HawkesStressLabel]:
    """Load Hawkes stress labels from a JSONL file."""
    labels = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            labels.append(HawkesStressLabel(
                label_id=d["label_id"],
                ts_ns=d["ts_ns"],
                ts_utc=d["ts_utc"],
                source_venue=d["source_venue"],
                source_symbol=d["source_symbol"],
                hawkes_tau_seconds=d["hawkes_tau_seconds"],
                hawkes_branching_ratio=d["hawkes_branching_ratio"],
                hawkes_alpha=d["hawkes_alpha"],
                intensity_lambda=d["intensity_lambda"],
                mu=d["mu"],
                lambda_over_mu=d["lambda_over_mu"],
                prior_event_count=d["prior_event_count"],
                abs_return_bps=d["abs_return_bps"],
                signed_return_bps=d["signed_return_bps"],
                bucket_size_seconds=d["bucket_size_seconds"],
                event_threshold_bps=d["event_threshold_bps"],
                entropy_value=d.get("entropy_value"),
                entropy_status=d.get("entropy_status", "not_computed"),
                reason=d["reason"],
                config_hash=d["config_hash"],
            ))
    return labels
