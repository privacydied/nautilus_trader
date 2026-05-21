"""
Cross-exchange funding dispersion carry — Phase 0 precommitment constants.

Study ID: cross-exchange-funding-dispersion-carry-v1

This module contains ONLY frozen precommitment constants, data contracts,
and the verdict guard. No data fetching, no evaluation, no report writing,
no execution code. The precommitment document
(docs/CROSS_EXCHANGE_FUNDING_DISPERSION_PRECOMMITMENT.md) is the single
source of truth for all values defined here.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

SAFETY_MODE = "public_data_observer_only"

# ---------------------------------------------------------------------------
# Study identity
# ---------------------------------------------------------------------------

STUDY_ID = "cross-exchange-funding-dispersion-carry-v1"
SCHEMA_VERSION = "1"

# ---------------------------------------------------------------------------
# Frozen constants — all from the precommitment document
# ---------------------------------------------------------------------------

# Section 6: Cell grid
ASSETS: tuple[str, ...] = ("BTC", "ETH")
THRESHOLDS_BPS: tuple[int, ...] = (5, 10, 20, 40)
HOLD_LENGTHS: tuple[int, ...] = (3, 6, 12)  # settlements
FROZEN_CELL_COUNT = len(ASSETS) * len(THRESHOLDS_BPS) * len(HOLD_LENGTHS)
assert FROZEN_CELL_COUNT == 24, f"Grid must be exactly 24 cells, got {FROZEN_CELL_COUNT}"

# Section 10: Cost model
PRIMARY_CAMPAIGN_COST_BPS: float = 50.0
DIAGNOSTIC_COST_BPS: float = 6.0
# Cost decomposition (frozen, named for transparency)
COST_DECOMPOSITION: tuple[tuple[str, float], ...] = (
    ("open_leg_1", 12.5),
    ("open_leg_2", 12.5),
    ("close_leg_1", 12.5),
    ("close_leg_2", 12.5),
    ("venue_mismatch_risk_haircut", 0.0),  # scoped out per Section 2, named for transparency
)
assert sum(v for _, v in COST_DECOMPOSITION) == PRIMARY_CAMPAIGN_COST_BPS, (
    "Cost decomposition must sum to primary campaign cost"
)

# Section 8: Gate A — minimum events per threshold
GATE_A_MIN_EVENTS = 50

# Section 9: Gate B — minimum non-overlapping events for powered combo
GATE_B_MIN_EVENTS = 50  # must match Gate A minimum

# Section 12.4: Holdout confirmation minimum events
HOLDOUT_MIN_EVENTS = 20

# Section 12.1: Stage 4 — Pre-null economic gate thresholds
PRE_NULL_MIN_EVENTS = 50
PRE_NULL_MEDIAN_NET_CARRY_GT = 0.0  # strictly > 0
PRE_NULL_MEAN_NET_CARRY_GT = 0.0
PRE_NULL_MIN_WIN_RATE = 0.55
PRE_NULL_WORST_DECILE_FLOOR_BPS = -50.0  # worst decile must be > -50

# Section 12.2: Null test
NULL_ITERATIONS = 1000
NULL_ALPHA = 0.05
NULL_SEED = 42

# Section 12.3: FDR
FDR_METHOD = "BY"  # Benjamini–Yekutieli
FDR_ALPHA = 0.05
FDR_FAMILY_SIZE = 24  # frozen denominator — never depends on how many cells reach null

# Section 7: Funding unit normalization
FUNDING_SANITY_BAND_BPS = 300.0  # ±300 bps per settlement sanity band

# Section 7: Minimum window size
MIN_SETTLEMENTS_IN_WINDOW = 200

# Section 0 → 7: Train/holdout split
TRAIN_FRACTION = 0.70

# Venues
VENUES: tuple[str, ...] = ("binance", "bybit")

# Invariant labels (precommitment anchors — not to be changed without v2)
INVARIANT_ASSETS = f"ASSETS = {ASSETS}"
INVARIANT_THRESHOLDS = f"THRESHOLDS_BPS = {THRESHOLDS_BPS}"
INVARIANT_HOLDS = f"HOLD_LENGTHS = {HOLD_LENGTHS}"
INVARIANT_CELL_COUNT = f"FROZEN_CELL_COUNT = {FROZEN_CELL_COUNT}"
INVARIANT_PRIMARY_COST = f"PRIMARY_CAMPAIGN_COST_BPS = {PRIMARY_CAMPAIGN_COST_BPS}"
INVARIANT_FDR_METHOD = "FDR_METHOD = BY (Benjamini-Yekutieli)"
INVARIANT_FDR_FAMILY_SIZE = f"FDR_FAMILY_SIZE = {FDR_FAMILY_SIZE}"
INVARIANT_NULL_METHOD = "NULL_METHOD = event-vector circular shift"
INVARIANT_NULL_ITERATIONS = f"NULL_ITERATIONS = {NULL_ITERATIONS}"

# ---------------------------------------------------------------------------
# Verdicts — exactly Section 14
# ---------------------------------------------------------------------------

VERDICT_FUNDING_UNIT_AMBIGUOUS = "FUNDING_UNIT_AMBIGUOUS"
VERDICT_DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
VERDICT_NEEDS_MORE_DATA = "NEEDS_MORE_DATA_OR_NO_TAIL"
VERDICT_REJECTED_COST_WALL = "REJECTED_COST_WALL"
VERDICT_REJECTED = "REJECTED"
VERDICT_NULL_REJECTED = "NULL_REJECTED_DIAGNOSTIC"
VERDICT_FDR_BLOCKED = "FDR_BLOCKED_DIAGNOSTIC"
VERDICT_HOLDOUT_FAILED = "HOLDOUT_FAILED_DIAGNOSTIC"
VERDICT_ARCHIVE_CANDIDATE = "ARCHIVE_CANDIDATE_FOR_LONGER_OBSERVATION"

# Per-cell intermediate labels (not final verdicts)
LABEL_PASS_PRE_NULL = "PASS_PRE_NULL"
LABEL_PASS_NULL = "PASS_NULL"

ALLOWED_VERDICTS: frozenset[str] = frozenset({
    VERDICT_FUNDING_UNIT_AMBIGUOUS,
    VERDICT_DATA_INSUFFICIENT,
    VERDICT_NEEDS_MORE_DATA,
    VERDICT_REJECTED_COST_WALL,
    VERDICT_REJECTED,
    VERDICT_NULL_REJECTED,
    VERDICT_FDR_BLOCKED,
    VERDICT_HOLDOUT_FAILED,
    VERDICT_ARCHIVE_CANDIDATE,
})

FORBIDDEN_VERDICTS: frozenset[str] = frozenset({
    "CANDIDATE_FOR_LIVE",
    "EXECUTION_READY",
    "TRADE_READY",
    "ARCHIVE_CANDIDATE_FOR_EXECUTION_MODELING",
})

# Intermediate cell-level labels used during pipeline processing.
# Not final verdicts — but must pass CellRecord validation.
CELL_LEVEL_LABELS: frozenset[str] = frozenset({
    LABEL_PASS_PRE_NULL,
    LABEL_PASS_NULL,
})

# The full set of strings allowed as CellRecord.cell_verdict
VALID_CELL_VERDICTS: frozenset[str] = ALLOWED_VERDICTS | CELL_LEVEL_LABELS


def validate_verdict(verdict: str, allow_intermediate: bool = False) -> None:
    """
    Raise ValueError if a forbidden or unsupported verdict appears.

    When allow_intermediate=True, cell-level intermediate labels
    (PASS_PRE_NULL, PASS_NULL) are also accepted.
    """
    if verdict in FORBIDDEN_VERDICTS:
        raise ValueError(
            f"Forbidden verdict '{verdict}'. "
            f"This study cannot produce live-trading claims. "
            f"Allowed: {sorted(ALLOWED_VERDICTS)}"
        )
    valid = VALID_CELL_VERDICTS if allow_intermediate else ALLOWED_VERDICTS
    if verdict not in valid:
        raise ValueError(
            f"Unsupported verdict '{verdict}'. "
            f"Allowed: {sorted(valid)}"
        )


# ---------------------------------------------------------------------------
# Data contracts — frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettlementRecord:
    """One aligned funding-rate settlement observation for a venue-asset pair."""

    timestamp_ns: int
    funding_rate_bps: float


@dataclass(frozen=True)
class FundingSeries:
    """Per-venue per-asset aligned funding series after unit normalization."""

    venue: str      # "binance" or "bybit"
    asset: str      # "BTC" or "ETH"
    unit_detected: str        # e.g. "decimal", "percent", "unknown"
    unit_normalized_to: str  # "bps_per_settlement"
    records: tuple[SettlementRecord, ...]

    def content_hash(self) -> str:
        """Deterministic content hash for reproducibility record."""
        import hashlib
        h = hashlib.sha256()
        h.update(self.venue.encode())
        h.update(self.asset.encode())
        h.update(self.unit_detected.encode())
        h.update(self.unit_normalized_to.encode())
        for r in self.records:
            h.update(r.timestamp_ns.to_bytes(8, "little"))
            # Float as hex for exact binary representation
            h.update(r.funding_rate_bps.hex().encode())
        return h.hexdigest()


@dataclass(frozen=True)
class WindowResolution:
    """Resolved common window and split information."""

    window_start_ns: int
    window_end_ns: int
    split_date_ns: int  # 70/30 split boundary
    train_start_ns: int
    train_end_ns: int
    holdout_start_ns: int
    holdout_end_ns: int
    total_settlements: int
    train_settlements: int
    holdout_settlements: int
    dropped_unaligned_settlements: int


@dataclass(frozen=True)
class DispersionEvent:
    """A dispersion event at settlement time t."""

    timestamp_ns: int
    asset: str
    dispersion_bps: float        # signed: binance - bybit
    funding_binance_bps: float
    funding_bybit_bps: float
    direction: str  # "short_binance" or "short_bybit"


@dataclass(frozen=True)
class CampaignResult:
    """One non-overlapping campaign's realized carry."""

    entry_timestamp_ns: int
    exit_timestamp_ns: int     # timestamp of settlement t+N (the last settlement in the hold)
    asset: str
    threshold_bps: int
    hold_length: int           # N
    direction: str
    realized_carry_bps: float
    campaign_cost_bps: float
    net_carry_bps: float
    settlement_carry_details: tuple[float, ...]  # per-settlement carry for t+1..t+N


@dataclass(frozen=True)
class CellIdentifier:
    """Identifies one cell in the 2x4x3 frozen grid."""

    asset: str
    threshold_bps: int
    hold_length: int

    @property
    def cell_id(self) -> str:
        return f"{self.asset}_t{self.threshold_bps}_h{self.hold_length}"

    def __post_init__(self) -> None:
        assert self.asset in ASSETS, f"Unknown asset {self.asset}"
        assert self.threshold_bps in THRESHOLDS_BPS, f"Unknown threshold {self.threshold_bps}"
        assert self.hold_length in HOLD_LENGTHS, f"Unknown hold length {self.hold_length}"


@dataclass(frozen=True)
class CellRecord:
    """Complete evaluation record for one cell."""

    cell_id: str
    asset: str
    threshold_bps: int
    hold_length: int
    valid_count: int
    overlapping_events_suppressed: int
    truncated_events: int
    mean_net_carry_bps: float
    median_net_carry_bps: float
    win_rate: float
    worst_decile_net_carry_bps: float
    convergence_rate: float  # diagnostic only
    cell_verdict: str
    null_p_value: float | None = None
    null_iterations: int | None = None
    fdr_adjusted_p: float | None = None
    fdr_survived: bool | None = None
    holdout_valid_count: int | None = None
    holdout_mean_net_carry_bps: float | None = None
    holdout_median_net_carry_bps: float | None = None
    holdout_win_rate: float | None = None
    holdout_worst_decile_net_carry_bps: float | None = None
    reached_pass_pre_null: bool = False
    reached_pass_null: bool = False

    def __post_init__(self) -> None:
        # Cell-level verdicts include intermediate pipeline labels;
        # study-level verdicts are the frozen ALLOWED_VERDICTS set only.
        validate_verdict(self.cell_verdict, allow_intermediate=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "asset": self.asset,
            "threshold_bps": self.threshold_bps,
            "hold_length": self.hold_length,
            "valid_count": self.valid_count,
            "overlapping_events_suppressed": self.overlapping_events_suppressed,
            "truncated_events": self.truncated_events,
            "mean_net_carry_bps": self.mean_net_carry_bps,
            "median_net_carry_bps": self.median_net_carry_bps,
            "win_rate": self.win_rate,
            "worst_decile_net_carry_bps": self.worst_decile_net_carry_bps,
            "convergence_rate": self.convergence_rate,
            "cell_verdict": self.cell_verdict,
            "null_p_value": self.null_p_value,
            "null_iterations": self.null_iterations,
            "fdr_adjusted_p": self.fdr_adjusted_p,
            "fdr_survived": self.fdr_survived,
            "holdout_valid_count": self.holdout_valid_count,
            "holdout_mean_net_carry_bps": self.holdout_mean_net_carry_bps,
            "holdout_median_net_carry_bps": self.holdout_median_net_carry_bps,
            "holdout_win_rate": self.holdout_win_rate,
            "holdout_worst_decile_net_carry_bps": self.holdout_worst_decile_net_carry_bps,
            "reached_pass_pre_null": self.reached_pass_pre_null,
            "reached_pass_null": self.reached_pass_null,
        }


@dataclass(frozen=True)
class GateAResult:
    """Output of Stage 1 — distribution sizing."""

    asset: str
    threshold_bps: int
    event_count: int
    frequency: float  # events / total_settlements


@dataclass(frozen=True)
class GateBComboResult:
    """One (threshold, N) combo from Stage 2."""

    threshold_bps: int
    hold_length: int
    non_overlapping_count: int
    median_net_carry_bps: float


@dataclass(frozen=True)
class RunMetadata:
    """Full reproducibility metadata (Section 16)."""

    study_id: str
    schema_version: str
    git_sha: str
    seed: int
    generated_at: str
    python_version: str
    run_args: dict[str, Any]
    window_start: str   # ISO 8601
    window_end: str     # ISO 8601
    split_date: str     # ISO 8601
    content_hashes: dict[str, str]  # series_label → hash
    funding_rate_unit_detected: dict[str, str]  # series_label → detected unit
    funding_rate_unit_normalized_to: dict[str, str]  # series_label → "bps"
    dropped_unaligned_settlements: int
    truncated_events: int
    overlapping_events_suppressed: int
    gate_a_output: list[dict[str, Any]]
    gate_b_output: list[dict[str, Any]]
    package_versions: dict[str, str]
    safety_mode: str = SAFETY_MODE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_all_cell_identifiers() -> list[CellIdentifier]:
    """Construct the frozen 24-cell grid."""
    cells: list[CellIdentifier] = []
    for asset in ASSETS:
        for threshold in THRESHOLDS_BPS:
            for hold in HOLD_LENGTHS:
                cells.append(CellIdentifier(
                    asset=asset,
                    threshold_bps=threshold,
                    hold_length=hold,
                ))
    assert len(cells) == FROZEN_CELL_COUNT, (
        f"Expected {FROZEN_CELL_COUNT} cells, built {len(cells)}"
    )
    return cells


def compute_settlement_carry_bps(
    f_short_bps: float,
    f_long_bps: float,
) -> float:
    """
    Compute per-settlement carry in bps per the frozen algebra (Section 4.4).

    settlement_carry_bps(s) = f_short(s) - f_long(s)

    Under the standard perp convention (positive = longs pay shorts),
    short_leg_pnl_bps(s) = +f_short(s)
    long_leg_pnl_bps(s)  = -f_long(s)
    settlement_carry_bps  = f_short(s) - f_long(s)
    """
    return f_short_bps - f_long_bps


def compute_realized_carry_bps(
    short_venue_rates: dict[int, float],
    long_venue_rates: dict[int, float],
    settlement_indices: range,
) -> float:
    """
    Sum settlement_carry_bps over the hold window t+1..t+N.

    Implements the frozen algebra exactly:
        realized_carry_bps = sum(f_short(s) - f_long(s) for s in t+1..t+N)

    The funding value at entry settlement t is excluded (no-lookahead, Section 4.2).
    """
    total = 0.0
    for s in settlement_indices:
        f_short = short_venue_rates.get(s, 0.0)
        f_long = long_venue_rates.get(s, 0.0)
        total += compute_settlement_carry_bps(f_short, f_long)
    return total
