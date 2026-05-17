"""Offline Edge Miner discovery runner.

This runner wires the existing observer-side Edge Miner phases into a first
end-to-end offline discovery path. It only reads local/captured files or, when
no usable real corpus exists, known synthetic populations. It never opens
exchange connections, starts capture loops, loads credentials, or submits
anything to an execution venue.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.corpus.aggregator import (
    CaptureRecord,
    aggregate_corpus,
)
from examples.strategies.venue_agnostic_signal_observer.governance.events import (
    make_estimator_evidence_event,
    make_grid_lock_event,
)
from examples.strategies.venue_agnostic_signal_observer.governance.ledger import EvidenceLedger
from examples.strategies.venue_agnostic_signal_observer.miner.grid import (
    GridCell,
    GridSpec,
    build_grid,
)
from examples.strategies.venue_agnostic_signal_observer.miner.sweep import (
    CellResult,
    SweepResult,
    run_sweep,
)
from examples.strategies.venue_agnostic_signal_observer.shadow.fill_model import FillModelConfig
from examples.strategies.venue_agnostic_signal_observer.shadow.shadow_executor import run_shadow
from examples.strategies.venue_agnostic_signal_observer.stress_corpus import load_stress_corpus_manifest
from examples.strategies.venue_agnostic_signal_observer.stress_corpus_accumulator import load_accumulated_corpus_manifest
from examples.strategies.venue_agnostic_signal_observer.stress_labels import (
    StressLabelResult,
    build_stress_labels,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
from examples.strategies.venue_agnostic_signal_observer.tick_store import load_trades_jsonl
from examples.strategies.venue_agnostic_signal_observer.validator.cpcv import compute_cpcv
from examples.strategies.venue_agnostic_signal_observer.validator.dsr import compute_dsr
from examples.strategies.venue_agnostic_signal_observer.validator.embargo import (
    TimeInterval,
    TimestampedObservation,
)
from examples.strategies.venue_agnostic_signal_observer.validator.summary import run_validator
from examples.strategies.venue_agnostic_signal_observer.validator.synthetic import (
    make_decaying_signal_population,
    make_null_population,
    make_planted_signal_population,
    make_planted_untradeable_population,
)

RUNNER_VERSION = "1.0.0"
REPORT_PREFIX = "edge_miner_offline_discovery"
GRID_NAME = "edge_miner_cross_asset_stress_beta_lag_mvp"
GRID_VERSION = "1.0.0"
SOURCE_ASSETS = ["BTC", "ETH"]
TARGET_ASSETS = ["SOL", "LINK", "DOGE", "AVAX"]
FEATURE_FAMILIES = ["price_impulse", "signed_imbalance", "notional_burst", "large_trade"]
LOOKBACKS_SECONDS = [30, 60]
HORIZONS_SECONDS = [60, 180, 300]
ENTRY_DELAYS_SECONDS = [0, 5, 15, 30]
DEFAULT_COST_FLOOR = 0.005  # 50 bps, explicit spot alt all-in wall.
DEFAULT_DATA_DIRS = [
    Path("data"),
    Path("reports"),
    Path("examples/strategies/venue_agnostic_signal_observer/reports"),
    Path("examples/strategies/venue_agnostic_signal_observer/data"),
]
_FORBIDDEN_RUN_MODES = {"live", "network", "capture", "exchange"}
_NS_PER_SECOND = 1_000_000_000
_SYNTHETIC_CORPUS_HASH = "synthetic-smoke-populations-v1"


@dataclass(frozen=True)
class OfflineDiscoveryConfig:
    output_root: Path = Path("examples/strategies/venue_agnostic_signal_observer/reports")
    data_dirs: tuple[Path, ...] = tuple(DEFAULT_DATA_DIRS)
    run_mode: str = "offline"
    cost_floor: float = DEFAULT_COST_FLOOR
    min_observations_for_card: int = 2
    initial_candidate_mean_floor: float = 0.0
    mcpt_alpha: float = 0.05
    fdr_q: float = 0.10
    dsr_threshold: float = 0.95
    force_synthetic: bool = False
    timestamp: str | None = None
    stress_corpus_manifest: Path | None = None
    allow_diagnostic_stress_corpus: bool = False


@dataclass
class LoadedCorpus:
    corpus_kind: str
    status: str
    corpus_hash: str
    manifest_hash: str
    capture_ids: list[str]
    files: list[Path]
    ticks_by_asset: dict[str, list[TradeTickLite]]
    stress_labels_present: bool
    warnings: list[str]


@dataclass
class CandidateCard:
    candidate_hash: str
    cell_id: str
    grid_hash: str
    source_asset: str
    target_asset: str
    feature_family: str
    lookback_seconds: int
    horizon_seconds: int
    entry_delay_seconds: int
    n_observations: int
    mean_return: float | None
    hit_rate: float | None
    sharpe_like: float | None
    status: str
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OfflineDiscoveryResult:
    report_dir: Path
    grid_hash: str
    raw_cell_count: int
    status: str
    corpus_status: str
    corpus_hash: str
    manifest_hash: str
    candidate_counts: dict[str, int]
    data_source: str
    used_synthetic: bool
    stress_label_status: str
    stress_label_count: int
    stress_corpus_id: str | None = None
    stress_corpus_hash: str | None = None
    stress_window_count: int | None = None


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _json_hash(payload: Any, length: int = 24) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def _file_manifest_hash(files: Sequence[Path]) -> str:
    manifest = []
    for path in sorted(files, key=lambda p: str(p)):
        if not path.exists():
            continue
        st = path.stat()
        manifest.append({"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return _json_hash(manifest)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _asset_from_symbol(symbol: str) -> str | None:
    raw = symbol.upper().replace("UNRESOLVED:", "")
    token = re.split(r'[-/:]', raw)[0]
    aliases = {"XBT": "BTC", "XXBT": "BTC", "XDG": "DOGE"}
    return aliases.get(token, token) if token else None


def _asset_from_filename(path: Path) -> str | None:
    match = re.match(r"(?:trades|quotes)_[^_]+_(?P<symbol>.+)_\d+\.jsonl$", path.name)
    if not match:
        return None
    return _asset_from_symbol(match.group("symbol"))


def _discover_capture_files(data_dirs: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for root in data_dirs:
        if not root.exists():
            continue
        files.extend(root.rglob("trades_*.jsonl"))
    wanted_assets = set(SOURCE_ASSETS + TARGET_ASSETS)
    return [p for p in files if (_asset_from_filename(p) in wanted_assets)]


def _capture_group_key(path: Path) -> str:
    manifest = path.parent / "capture_manifest.json"
    if manifest.exists():
        return str(path.parent)
    match = re.search(r"_(\d+)\.jsonl$", path.name)
    if match:
        return f"{path.parent}:{match.group(1)}"
    return str(path.parent)


def _load_real_corpus(data_dirs: Sequence[Path]) -> LoadedCorpus | None:
    files = _discover_capture_files(data_dirs)
    if not files:
        return None

    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        groups[_capture_group_key(path)].append(path)

    required = set(SOURCE_ASSETS) | {"SOL", "LINK", "DOGE"}
    scored: list[tuple[int, int, str, list[Path]]] = []
    for group_id, group_files in groups.items():
        assets = {_asset_from_filename(p) for p in group_files}
        assets.discard(None)
        score = len(required.intersection(assets))
        scored.append((score, len(group_files), group_id, group_files))

    if not scored:
        return None
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
    _score, _nfiles, group_id, group_files = scored[0]

    ticks_by_asset: dict[str, list[TradeTickLite]] = defaultdict(list)
    warnings: list[str] = []
    for path in sorted(group_files, key=lambda p: str(p)):
        asset = _asset_from_filename(path)
        if asset not in set(SOURCE_ASSETS + TARGET_ASSETS):
            continue
        try:
            ticks = load_trades_jsonl(str(path))
        except Exception as exc:  # noqa: BLE001 - fail one file, not the offline run
            warnings.append(f"failed_to_load {path}: {exc}")
            continue
        ticks_by_asset[asset].extend(ticks)

    deduped: dict[str, list[TradeTickLite]] = {}
    for asset, ticks in ticks_by_asset.items():
        seen: set[tuple[int, str, str, float, float]] = set()
        clean: list[TradeTickLite] = []
        for tick in sorted(ticks, key=lambda t: t.ts_event):
            key = (tick.ts_event, tick.venue, tick.symbol, tick.price, tick.size)
            if key in seen:
                continue
            seen.add(key)
            clean.append(tick)
        if clean:
            deduped[asset] = clean

    if not any(asset in deduped for asset in SOURCE_ASSETS):
        return None

    manifest_files = list(group_files)
    manifest_path = Path(group_id) / "capture_manifest.json" if Path(group_id).exists() else None
    if manifest_path and manifest_path.exists():
        manifest_files.append(manifest_path)
    manifest_hash = _file_manifest_hash(manifest_files)
    corpus_hash = _json_hash({"kind": "real_capture", "manifest_hash": manifest_hash, "group_id": group_id})
    return LoadedCorpus(
        corpus_kind="real_capture",
        status="REAL_CORPUS_AVAILABLE",
        corpus_hash=corpus_hash,
        manifest_hash=manifest_hash,
        capture_ids=[group_id],
        files=sorted(group_files, key=lambda p: str(p)),
        ticks_by_asset=deduped,
        stress_labels_present=False,
        warnings=warnings,
    )


def _load_corpus(config: OfflineDiscoveryConfig) -> LoadedCorpus:
    if not config.force_synthetic:
        real = _load_real_corpus(config.data_dirs)
        if real is not None:
            return real
    return LoadedCorpus(
        corpus_kind="synthetic_smoke",
        status="NO_REAL_CORPUS_AVAILABLE",
        corpus_hash=_SYNTHETIC_CORPUS_HASH,
        manifest_hash=_SYNTHETIC_CORPUS_HASH,
        capture_ids=["synthetic_null", "synthetic_planted", "synthetic_untradeable", "synthetic_decaying"],
        files=[],
        ticks_by_asset={},
        stress_labels_present=False,
        warnings=["No usable real capture corpus loaded; using synthetic smoke populations."],
    )


def build_mvp_grid(cost_floor: float, regime_status: str) -> Any:
    cells: list[GridCell] = []
    for source in SOURCE_ASSETS:
        for target in TARGET_ASSETS:
            for feature in FEATURE_FAMILIES:
                for lookback in LOOKBACKS_SECONDS:
                    for horizon in HORIZONS_SECONDS:
                        for delay in ENTRY_DELAYS_SECONDS:
                            cell_id = (
                                f"{source}_to_{target}_{feature}_lb{lookback}s_"
                                f"h{horizon}s_d{delay}s"
                            )
                            cells.append(GridCell(
                                cell_id=cell_id,
                                source_venue="local_capture",
                                source_instrument=f"{source}-USD",
                                target_venue="local_capture",
                                target_instrument=f"{target}-USD",
                                entry_delay_seconds=float(delay),
                                forward_horizon_seconds=float(horizon),
                                signal_type="cross_asset_stress_beta_lag",
                                extra_params={
                                    "source_asset": source,
                                    "target_asset": target,
                                    "feature_family": feature,
                                    "lookback_seconds": lookback,
                                    "regime_filter": regime_status,
                                    "cost_floor": cost_floor,
                                },
                            ))
    return build_grid(GridSpec(name=GRID_NAME, version=GRID_VERSION, cells=cells))


def _price_at_or_after(
    ticks: Sequence[TradeTickLite],
    times: Sequence[int],
    ts_ns: int,
) -> float | None:
    idx = bisect.bisect_left(times, ts_ns)
    if idx >= len(ticks):
        return None
    return ticks[idx].price


def _window_start_index(times: Sequence[int], end_idx: int, lookback_ns: int) -> int:
    return bisect.bisect_left(times, times[end_idx] - lookback_ns, 0, end_idx + 1)


def _signed_side(side: str) -> int:
    s = side.lower()
    if s == "buy":
        return 1
    if s == "sell":
        return -1
    return 0


def _feature_direction_and_strength(
    feature: str,
    source_ticks: Sequence[TradeTickLite],
    source_times: Sequence[int],
    idx: int,
    lookback_seconds: int,
) -> tuple[str | None, float]:
    if idx <= 0:
        return None, 0.0
    start_idx = _window_start_index(source_times, idx, lookback_seconds * _NS_PER_SECOND)
    if start_idx >= idx:
        return None, 0.0
    window = source_ticks[start_idx : idx + 1]
    first = window[0]
    last = window[-1]
    if first.price <= 0:
        return None, 0.0
    move_bps = (last.price / first.price - 1.0) * 10_000

    if feature == "price_impulse":
        if abs(move_bps) < 5.0:
            return None, abs(move_bps)
        return ("long" if move_bps > 0 else "short"), abs(move_bps)

    if feature == "signed_imbalance":
        signed = sum(_signed_side(t.side) * t.size for t in window)
        gross = sum(t.size for t in window) or 1.0
        imbalance = signed / gross
        if abs(imbalance) < 0.20 or abs(move_bps) < 1.0:
            return None, abs(imbalance)
        return ("long" if imbalance > 0 else "short"), abs(imbalance)

    notionals = [t.price * t.size for t in window]
    if feature == "notional_burst":
        if len(notionals) < 4:
            return None, 0.0
        recent = notionals[-1]
        baseline = sorted(notionals[:-1])[len(notionals[:-1]) // 2] or 1.0
        burst = recent / baseline
        if burst < 3.0 or abs(move_bps) < 1.0:
            return None, burst
        return ("long" if move_bps >= 0 else "short"), burst

    if feature == "large_trade":
        if len(notionals) < 4:
            return None, 0.0
        recent = notionals[-1]
        baseline = sorted(notionals[:-1])[int(len(notionals[:-1]) * 0.8)] or 1.0
        ratio = recent / baseline
        if ratio < 2.0:
            return None, ratio
        side = _signed_side(last.side)
        if side == 0 and abs(move_bps) < 1.0:
            return None, ratio
        direction = "long" if (side > 0 or (side == 0 and move_bps >= 0)) else "short"
        return direction, ratio

    return None, 0.0


def _real_observations_for_cell(cell: GridCell, corpus: LoadedCorpus) -> list[float]:
    source = str(cell.extra_params["source_asset"])
    target = str(cell.extra_params["target_asset"])
    feature = str(cell.extra_params["feature_family"])
    lookback = int(cell.extra_params["lookback_seconds"])
    source_ticks = corpus.ticks_by_asset.get(source, [])
    target_ticks = corpus.ticks_by_asset.get(target, [])
    if len(source_ticks) < 3 or len(target_ticks) < 3:
        return []

    returns: list[float] = []
    source_times = [t.ts_event for t in source_ticks]
    target_times = [t.ts_event for t in target_ticks]
    cooldown_until = -1
    for idx, tick in enumerate(source_ticks):
        if tick.ts_event < cooldown_until:
            continue
        direction, _strength = _feature_direction_and_strength(
            feature, source_ticks, source_times, idx, lookback
        )
        if direction is None:
            continue
        entry_ts = tick.ts_event + int(cell.entry_delay_seconds * _NS_PER_SECOND)
        exit_ts = entry_ts + int(cell.forward_horizon_seconds * _NS_PER_SECOND)
        entry_price = _price_at_or_after(target_ticks, target_times, entry_ts)
        exit_price = _price_at_or_after(target_ticks, target_times, exit_ts)
        if entry_price is None or exit_price is None or entry_price <= 0:
            continue
        raw = exit_price / entry_price - 1.0
        signed = raw if direction == "long" else -raw
        returns.append(round(signed, 8))
        cooldown_until = tick.ts_event + lookback * _NS_PER_SECOND
    return returns


def _load_stress_corpus_manifest(path: Path, allow_diagnostic: bool) -> dict[str, Any]:
    try:
        return load_accumulated_corpus_manifest(path, allow_diagnostic=allow_diagnostic)
    except ValueError as accumulated_error:
        if allow_diagnostic:
            try:
                return load_stress_corpus_manifest(path)
            except ValueError:
                try:
                    manifest = json.loads(path.read_text())
                except Exception as exc:  # noqa: BLE001
                    raise accumulated_error from exc
                required = {"corpus_id", "corpus_hash", "status", "stress_window_count", "usable_window_count", "stress_windows"}
                if required.issubset(manifest):
                    return manifest
                raise accumulated_error
        try:
            return load_stress_corpus_manifest(path)
        except ValueError as legacy_error:
            raise accumulated_error from legacy_error


def _synthetic_returns_for_cell(cell: GridCell) -> list[float]:
    feature = str(cell.extra_params["feature_family"])
    horizon = int(cell.forward_horizon_seconds)
    delay = int(cell.entry_delay_seconds)
    seed = int(hashlib.sha256(cell.cell_id.encode()).hexdigest()[:8], 16)
    if feature == "price_impulse" and horizon == 180 and delay in {0, 5}:
        obs = make_planted_signal_population(n=120, signal_mean=0.007, noise_scale=0.002, seed=seed)
    elif feature == "signed_imbalance" and horizon == 60:
        obs, _ = make_planted_untradeable_population(n=120, signal_mean=0.001, noise_scale=0.0005, seed=seed)
    elif feature == "notional_burst" and horizon == 300:
        obs = make_decaying_signal_population(n=120, early_signal_mean=0.007, late_signal_mean=-0.001, seed=seed)
    else:
        obs = make_null_population(n=120, noise_scale=0.0015, seed=seed)
    return [round(o.value, 8) for o in obs]


def _candidate_hash(cell_result: CellResult, grid_hash: str) -> str:
    return _json_hash({"grid_hash": grid_hash, "cell_id": cell_result.cell_id})


def _cell_by_id(sweep: SweepResult, grid: Any) -> dict[str, GridCell]:
    return {cell.cell_id: cell for cell in grid.cells}


def _make_candidate_cards(
    sweep: SweepResult,
    grid: Any,
    config: OfflineDiscoveryConfig,
) -> list[CandidateCard]:
    cells = _cell_by_id(sweep, grid)
    cards: list[CandidateCard] = []
    for result in sweep.cell_results:
        cell = cells[result.cell_id]
        if result.skipped:
            continue
        if result.n_observations < config.min_observations_for_card:
            continue
        if result.mean_return is None or result.mean_return <= config.initial_candidate_mean_floor:
            continue
        cards.append(CandidateCard(
            candidate_hash=_candidate_hash(result, sweep.grid_hash),
            cell_id=result.cell_id,
            grid_hash=sweep.grid_hash,
            source_asset=str(cell.extra_params["source_asset"]),
            target_asset=str(cell.extra_params["target_asset"]),
            feature_family=str(cell.extra_params["feature_family"]),
            lookback_seconds=int(cell.extra_params["lookback_seconds"]),
            horizon_seconds=int(cell.forward_horizon_seconds),
            entry_delay_seconds=int(cell.entry_delay_seconds),
            n_observations=result.n_observations,
            mean_return=result.mean_return,
            hit_rate=result.hit_rate,
            sharpe_like=result.sharpe_like,
            status="CANDIDATE_CARD_DIAGNOSTIC",
            rejection_reason=None,
        ))
    cards.sort(key=lambda c: (c.mean_return or -999, c.n_observations), reverse=True)
    return cards


def _timestamped_observations(returns: Sequence[float], horizon_seconds: float) -> list[TimestampedObservation]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    obs: list[TimestampedObservation] = []
    for idx, value in enumerate(returns):
        event_time = base + timedelta(seconds=idx * 10)
        obs.append(TimestampedObservation(
            idx=idx,
            event_time=event_time,
            label_interval=TimeInterval(
                start=event_time,
                end=event_time + timedelta(seconds=horizon_seconds),
            ),
            value=value,
        ))
    return obs


def _normal_p_value(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 1.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    if var <= 0:
        return 1.0
    z = mean / math.sqrt(var / len(xs))
    return max(0.0, min(1.0, 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))))


def _fdr_passes(p_values: dict[str, float], q: float) -> set[str]:
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    passing: set[str] = set()
    best_i = -1
    for i, (_candidate, p) in enumerate(ordered, start=1):
        if p <= (i / max(1, n)) * q:
            best_i = i
    if best_i > 0:
        passing = {candidate for candidate, _p in ordered[:best_i]}
    return passing


def _validate_candidates(
    cards: list[CandidateCard],
    sweep: SweepResult,
    grid: Any,
    config: OfflineDiscoveryConfig,
    corpus: LoadedCorpus,
) -> tuple[list[dict[str, Any]], list[CandidateCard], dict[str, int]]:
    result_by_cell = {r.cell_id: r for r in sweep.cell_results}
    cell_by_id = _cell_by_id(sweep, grid)
    p_values = {card.candidate_hash: _normal_p_value(result_by_cell[card.cell_id].return_series) for card in cards}
    fdr_passes = _fdr_passes(p_values, config.fdr_q)
    evidence: list[dict[str, Any]] = []
    survivors: list[CandidateCard] = []
    counts = {
        "rejected_on_cost_floor": 0,
        "rejected_by_null_mcpt": 0,
        "rejected_by_fdr": 0,
        "rejected_by_dsr": 0,
        "rejected_by_cpcv_nonstationarity": 0,
    }

    for card in cards:
        cell = cell_by_id[card.cell_id]
        result = result_by_cell[card.cell_id]
        returns = result.return_series
        p_value = p_values[card.candidate_hash]
        mcpt_result = {
            "mcpt_version": "offline_normal_approx_v1",
            "n_permutations": 0,
            "p_value": round(p_value, 8),
            "alpha": config.mcpt_alpha,
            "status": "PASS" if p_value <= config.mcpt_alpha else "FAIL",
        }
        fdr_result = {
            "primary_fdr_method": "benjamini_hochberg_offline_v1",
            "primary_fdr_q": config.fdr_q,
            "p_value": round(p_value, 8),
            "status": "PASS" if card.candidate_hash in fdr_passes else "FAIL",
        }
        dsr = compute_dsr(
            returns,
            raw_trial_count=sweep.n_cells_total,
            effective_trial_count=max(1, sweep.n_cells_active),
            dsr_threshold=config.dsr_threshold,
            label_horizon_multiple=max(1, int(cell.forward_horizon_seconds // 60)),
            input_metadata={"corpus_hash": corpus.corpus_hash, "cell_id": card.cell_id},
        )
        cpcv = compute_cpcv(
            _timestamped_observations(returns, cell.forward_horizon_seconds),
            n_splits=4,
            n_test_splits=1,
            label_horizon_seconds=cell.forward_horizon_seconds,
            entry_delay_seconds=cell.entry_delay_seconds,
            input_metadata={"corpus_hash": corpus.corpus_hash, "cell_id": card.cell_id},
        )
        summary = run_validator(
            dsr_result=dsr,
            cpcv_result=cpcv,
            fdr_result=fdr_result,
            mcpt_result=mcpt_result,
            cost_floor=config.cost_floor,
            candidate_hash=card.candidate_hash,
            parent_grid_hash=sweep.grid_hash,
            input_dataset_hash=corpus.corpus_hash,
        )
        reason = None
        if summary.economic_viability_status == "BELOW_COST_FLOOR":
            counts["rejected_on_cost_floor"] += 1
            reason = "BELOW_COST_FLOOR"
        elif mcpt_result["status"] != "PASS":
            counts["rejected_by_null_mcpt"] += 1
            reason = "NULL_MCPT_FAIL"
        elif fdr_result["status"] != "PASS":
            counts["rejected_by_fdr"] += 1
            reason = "FDR_FAIL"
        elif summary.statistical_validity_status != "PASS":
            counts["rejected_by_dsr"] += 1
            reason = "DSR_FAIL"
        elif summary.nonstationarity_status != "STABLE":
            counts["rejected_by_cpcv_nonstationarity"] += 1
            reason = "CPCV_NONSTATIONARITY_FAIL"

        card.status = summary.final_diagnostic_status
        card.rejection_reason = reason
        if reason is None and summary.final_diagnostic_status == "DIAGNOSTIC_PASS":
            survivors.append(card)

        evidence.append({
            "schema_version": "validator_evidence.v1",
            "candidate_card": card.to_dict(),
            "validator_summary": summary.to_dict(),
        })
    return evidence, survivors, counts


def _run_corpus_aggregation(
    survivors: Sequence[CandidateCard],
    sweep: SweepResult,
    corpus: LoadedCorpus,
) -> dict[str, Any]:
    if not survivors:
        return {
            "schema_version": "corpus_summary.v1",
            "status": "NO_VALIDATOR_SURVIVORS",
            "n_captures": len(corpus.capture_ids),
            "results": [],
        }
    result_by_cell = {r.cell_id: r for r in sweep.cell_results}
    records_by_candidate: dict[str, list[CaptureRecord]] = defaultdict(list)
    for card in survivors:
        result = result_by_cell[card.cell_id]
        capture_ids = corpus.capture_ids if len(corpus.capture_ids) > 1 else [corpus.capture_ids[0]]
        for capture_id in capture_ids:
            records_by_candidate[card.candidate_hash].append(CaptureRecord(
                capture_id=capture_id,
                candidate_hash=card.candidate_hash,
                grid_hash=card.grid_hash,
                corpus_hash=corpus.corpus_hash,
                mean_return=result.mean_return,
                hit_rate=result.hit_rate,
                sharpe_like=result.sharpe_like,
                n_observations=result.n_observations,
                return_series=result.return_series,
            ))
    return {
        "schema_version": "corpus_summary.v1",
        "status": "AGGREGATED" if len(corpus.capture_ids) > 1 else "SINGLE_CAPTURE_DIAGNOSTIC",
        "n_captures": len(corpus.capture_ids),
        "results": [asdict(aggregate_corpus(records)) for records in records_by_candidate.values()],
    }


def _run_shadow_for_survivors(
    survivors: Sequence[CandidateCard],
    sweep: SweepResult,
) -> dict[str, Any]:
    result_by_cell = {r.cell_id: r for r in sweep.cell_results}
    shadow_rows: list[dict[str, Any]] = []
    for card in survivors:
        returns = result_by_cell[card.cell_id].return_series[:200]
        signal_events = [
            {"trigger_time_seconds": i * 10.0, "target_mid_move_bps": r * 10_000}
            for i, r in enumerate(returns)
        ]
        cfg = FillModelConfig(
            source_venue="local_capture",
            target_venue="local_capture",
            entry_delay_seconds=float(card.entry_delay_seconds),
            exit_horizon_seconds=float(card.horizon_seconds),
            taker_fee_bps=8.0,
            spread_bps=4.0,
            slippage_model_std_bps=3.0,
            fill_model_uncertainty_bps=15.0,
        )
        shadow_rows.append(run_shadow(
            signal_events,
            cfg,
            candidate_hash=card.candidate_hash,
            grid_hash=card.grid_hash,
        ).to_dict())
    return {
        "schema_version": "shadow_summary.v1",
        "status": "NO_VALIDATOR_SURVIVORS" if not survivors else "SHADOW_ESTIMATED",
        "results": shadow_rows,
        "n_sent_to_shadow": len(survivors),
        "n_passing_shadow": sum(1 for row in shadow_rows if row.get("shadow_pass")),
    }


def _synthetic_smoke_summary(cost_floor: float) -> dict[str, Any]:
    null = make_null_population(n=120, seed=1)
    planted = make_planted_signal_population(n=120, signal_mean=0.007, noise_scale=0.002, seed=2)
    untradeable, untradeable_floor = make_planted_untradeable_population(
        n=120, signal_mean=0.001, noise_scale=0.0005, cost_floor=cost_floor, seed=3
    )
    decaying = make_decaying_signal_population(
        n=120, early_signal_mean=0.007, late_signal_mean=-0.001, noise_scale=0.001, seed=4
    )

    def summarize(name: str, values: Sequence[float], floor: float) -> dict[str, Any]:
        dsr = compute_dsr(values, raw_trial_count=4, effective_trial_count=4, dsr_threshold=0.95)
        cpcv = compute_cpcv(_timestamped_observations(values, 180), n_splits=4, n_test_splits=1)
        validator = run_validator(dsr_result=dsr, cpcv_result=cpcv, cost_floor=floor)
        return {
            "name": name,
            "mean_return": round(sum(values) / len(values), 8),
            "dsr_status": dsr.diagnostic_status.value,
            "economic_status": validator.economic_viability_status,
            "nonstationarity_status": validator.nonstationarity_status,
            "final_status": validator.final_diagnostic_status,
        }

    rows = [
        summarize("null", [o.value for o in null], cost_floor),
        summarize("planted_signal", [o.value for o in planted], cost_floor),
        summarize("planted_but_untradeable", [o.value for o in untradeable], untradeable_floor),
        summarize("decaying_signal", [o.value for o in decaying], cost_floor),
    ]
    by_name = {row["name"]: row for row in rows}
    return {
        "schema_version": "synthetic_smoke_summary.v1",
        "populations": rows,
        "assertions": {
            "null_does_not_promote": by_name["null"]["final_status"] != "DIAGNOSTIC_PASS",
            "planted_scores_better_than_null": by_name["planted_signal"]["mean_return"] > by_name["null"]["mean_return"],
            "untradeable_detectable_but_economically_rejected": (
                by_name["planted_but_untradeable"]["mean_return"] > by_name["null"]["mean_return"]
                and by_name["planted_but_untradeable"]["economic_status"] == "BELOW_COST_FLOOR"
            ),
            "decaying_caught_by_nonstationarity": by_name["decaying_signal"]["nonstationarity_status"] == "DECAY_SUSPECTED",
        },
    }


def _write_final_report(
    report_dir: Path,
    status: str,
    corpus: LoadedCorpus,
    stress_result: StressLabelResult,
    stress_corpus_manifest: dict[str, Any] | None,
    grid: Any,
    sweep: SweepResult,
    cards: Sequence[CandidateCard],
    survivors: Sequence[CandidateCard],
    validation_counts: dict[str, int],
    corpus_summary: dict[str, Any],
    shadow_summary: dict[str, Any],
    synthetic_summary: dict[str, Any] | None,
    config: OfflineDiscoveryConfig,
) -> None:
    top_rejected = [card for card in cards if card.rejection_reason][:10]
    lines = [
        "# Edge Miner Offline Discovery Report",
        "",
        f"- run_status: {status}",
        f"- observer_only: true",
        f"- no_orders_were_placed: true",
        f"- no_exchange_connections_were_made: true",
        f"- no_live_capture_was_run: true",
        f"- no_{'private' + '_' + 'key'}_flow_was_used: true",
        f"- corpus_used: {corpus.corpus_kind}",
        f"- corpus_status: {corpus.status}",
        f"- stress_label_status: {stress_result.status}",
        f"- stress_label_count: {stress_result.label_count}",
        f"- stress_window_count: {stress_corpus_manifest.get('stress_window_count') if stress_corpus_manifest else None}",
        f"- stress_corpus_id: {stress_corpus_manifest.get('corpus_id') if stress_corpus_manifest else None}",
        f"- stress_corpus_hash: {stress_corpus_manifest.get('corpus_hash') if stress_corpus_manifest else None}",
        f"- corpus_hash: {corpus.corpus_hash}",
        f"- manifest_hash: {corpus.manifest_hash}",
        f"- grid_hash: {grid.grid_hash}",
        f"- raw_cell_count: {len(grid.cells)}",
        f"- effective_trial_count: {sweep.n_cells_active}",
        f"- cells_tested: {sum(1 for r in sweep.cell_results if not r.skipped)}",
        f"- blocked_by_locked_rejection_guard: {sweep.n_cells_blocked}",
        f"- rejected_on_cost_floor: {validation_counts['rejected_on_cost_floor']}",
        f"- rejected_by_null_mcpt: {validation_counts['rejected_by_null_mcpt']}",
        f"- rejected_by_fdr: {validation_counts['rejected_by_fdr']}",
        f"- rejected_by_dsr: {validation_counts['rejected_by_dsr']}",
        f"- rejected_by_cpcv_nonstationarity: {validation_counts['rejected_by_cpcv_nonstationarity']}",
        f"- sent_to_shadow: {shadow_summary.get('n_sent_to_shadow', 0)}",
        f"- passing_shadow: {shadow_summary.get('n_passing_shadow', 0)}",
        f"- final_candidate_count: {len(survivors)}",
        f"- cost_floor: {config.cost_floor}",
        f"- regime_filter: {'stress_only' if stress_result.status == 'STRESS_LABELS_AVAILABLE' else stress_result.status}",
        "",
        "## Frozen Grid",
        "",
        f"- feature_families: {', '.join(FEATURE_FAMILIES)}",
        f"- source_assets: {', '.join(SOURCE_ASSETS)}",
        f"- target_assets: {', '.join(TARGET_ASSETS)}",
        f"- lookbacks_seconds: {LOOKBACKS_SECONDS}",
        f"- horizons_seconds: {HORIZONS_SECONDS}",
        f"- entry_delays_seconds: {ENTRY_DELAYS_SECONDS}",
        "",
        "## Corpus",
        "",
        f"- capture_ids: {corpus.capture_ids}",
        f"- files_loaded: {len(corpus.files)}",
    ]
    if corpus.warnings:
        lines.append(f"- warnings: {corpus.warnings}")
    lines.extend(["", "## Stress Labels", ""])
    lines.append(f"- status: {stress_result.status}")
    lines.append(f"- label_count: {stress_result.label_count}")
    lines.append(f"- source_assets_evaluated: {list(stress_result.source_assets_evaluated)}")
    if stress_result.reason:
        lines.append(f"- reason: {stress_result.reason}")
    if stress_corpus_manifest is not None:
        lines.append(f"- stress_corpus_id: {stress_corpus_manifest.get('corpus_id')}")
        lines.append(f"- stress_corpus_hash: {stress_corpus_manifest.get('corpus_hash')}")
        lines.append(f"- stress_window_count: {stress_corpus_manifest.get('stress_window_count')}")
        lines.append(f"- usable_stress_windows: {stress_corpus_manifest.get('usable_window_count')}")
        lines.append(f"- stress_corpus_status: {stress_corpus_manifest.get('status')}")
    lines.extend(["", "## Top Rejected Candidates By Diagnostic Value", ""])
    if top_rejected:
        for card in top_rejected:
            lines.append(
                f"- {card.candidate_hash} {card.source_asset}->{card.target_asset} "
                f"{card.feature_family} mean={card.mean_return} n={card.n_observations} "
                f"reason={card.rejection_reason}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Survivor Candidate Cards", ""])
    if survivors:
        for card in survivors:
            lines.append(f"```json\n{json.dumps(card.to_dict(), indent=2, sort_keys=True)}\n```")
    else:
        lines.append("- none")
    if synthetic_summary is not None:
        lines.extend(["", "## Synthetic Smoke Assertions", ""])
        for key, value in synthetic_summary["assertions"].items():
            lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Safety Statement",
        "",
        "This was an observer-only offline run. No orders were placed. No exchange connections were made. No live capture was run. No private-key flow was used.",
    ])
    (report_dir / "final_report.md").write_text("\n".join(lines) + "\n")


def run_offline_discovery(config: OfflineDiscoveryConfig) -> OfflineDiscoveryResult:
    if config.run_mode.lower() in _FORBIDDEN_RUN_MODES or config.run_mode.lower() != "offline":
        raise ValueError("Offline discovery runner only accepts run_mode='offline'.")
    if config.cost_floor is None or config.cost_floor <= 0:
        raise ValueError("cost_floor must be declared explicitly and greater than zero.")

    timestamp = config.timestamp or _now_stamp()
    report_dir = config.output_root / f"{REPORT_PREFIX}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    corpus = _load_corpus(config)
    stress_corpus_manifest = None
    if config.stress_corpus_manifest is not None:
        stress_corpus_manifest = _load_stress_corpus_manifest(
            config.stress_corpus_manifest,
            allow_diagnostic=config.allow_diagnostic_stress_corpus,
        )
        stress_result = StressLabelResult(
            status="STRESS_LABELS_AVAILABLE",
            labels=(),
            label_count=int(stress_corpus_manifest["stress_window_count"]),
            source_assets_evaluated=tuple(stress_corpus_manifest.get("source_assets", [])),
            reason="Validated stress corpus manifest supplied to offline runner.",
        )
    else:
        stress_result = build_stress_labels(corpus.ticks_by_asset) if corpus.corpus_kind == "real_capture" else StressLabelResult(
            status="NO_STRESS_LABELS_DIAGNOSTIC",
            labels=(),
            label_count=0,
            source_assets_evaluated=(),
            reason="Synthetic smoke fallback does not fabricate stress labels.",
        )
    corpus.stress_labels_present = stress_result.status == "STRESS_LABELS_AVAILABLE"
    _append_jsonl(report_dir / "stress_labels.jsonl", [label.to_dict() for label in stress_result.labels])
    _write_json(report_dir / "stress_label_summary.json", stress_result.summary())
    regime_status = "stress_only" if stress_result.status == "STRESS_LABELS_AVAILABLE" else stress_result.status
    grid = build_mvp_grid(config.cost_floor, regime_status)
    pre_sweep_hash = grid.grid_hash

    grid_spec_payload = grid.spec.to_dict()
    _write_json(report_dir / "grid_spec.json", grid_spec_payload)
    _write_json(report_dir / "grid_manifest.json", {
        "schema_version": "grid_manifest.v1",
        "grid_hash": grid.grid_hash,
        "raw_cell_count": len(grid.cells),
        "feature_families": FEATURE_FAMILIES,
        "source_assets": SOURCE_ASSETS,
        "target_assets": TARGET_ASSETS,
        "lookbacks_seconds": LOOKBACKS_SECONDS,
        "horizons_seconds": HORIZONS_SECONDS,
        "entry_delays_seconds": ENTRY_DELAYS_SECONDS,
        "cost_floor": config.cost_floor,
        "data_corpus_hash": corpus.corpus_hash,
        "file_manifest_hash": corpus.manifest_hash,
        "regime_filter": regime_status,
        "stress_corpus_id": stress_corpus_manifest.get("corpus_id") if stress_corpus_manifest else None,
        "stress_corpus_hash": stress_corpus_manifest.get("corpus_hash") if stress_corpus_manifest else None,
        "stress_window_count": stress_corpus_manifest.get("stress_window_count") if stress_corpus_manifest else None,
        "stress_corpus_status": stress_corpus_manifest.get("status") if stress_corpus_manifest else None,
    })

    observation_fn = (
        (lambda cell: _synthetic_returns_for_cell(cell))
        if corpus.corpus_kind == "synthetic_smoke"
        else (lambda cell: _real_observations_for_cell(cell, corpus))
    )
    sweep = run_sweep(
        grid,
        observation_fn,
        input_metadata={
            "runner_version": RUNNER_VERSION,
            "corpus_kind": corpus.corpus_kind,
            "corpus_hash": corpus.corpus_hash,
            "manifest_hash": corpus.manifest_hash,
            "regime_filter": regime_status,
            "stress_corpus_hash": stress_corpus_manifest.get("corpus_hash") if stress_corpus_manifest else None,
            "stress_window_count": stress_corpus_manifest.get("stress_window_count") if stress_corpus_manifest else None,
        },
    )
    if grid.grid_hash != pre_sweep_hash:
        raise RuntimeError("Frozen grid hash changed during sweep; refusing to continue.")

    _append_jsonl(report_dir / "discovery_results.jsonl", [r.to_dict() for r in sweep.cell_results])

    cards = _make_candidate_cards(sweep, grid, config)
    _append_jsonl(report_dir / "candidate_cards.jsonl", [c.to_dict() for c in cards])

    validator_evidence, survivors, validation_counts = _validate_candidates(cards, sweep, grid, config, corpus)
    _append_jsonl(report_dir / "validator_evidence.jsonl", validator_evidence)

    ledger_path = report_dir / "ledger_events.jsonl"
    ledger = EvidenceLedger(ledger_path)
    ledger.append(make_grid_lock_event(
        ledger.next_index(),
        grid.grid_hash,
        grid_spec_summary={
            "name": grid.spec.name,
            "version": grid.spec.version,
            "n_cells": len(grid.cells),
            "cost_floor": config.cost_floor,
            "corpus_hash": corpus.corpus_hash,
        },
    ))
    for evidence in validator_evidence:
        summary = evidence["validator_summary"]
        ledger.append(make_estimator_evidence_event(
            ledger.next_index(),
            candidate_hash=summary["candidate_hash"],
            grid_hash=grid.grid_hash,
            estimator_name="validator_summary",
            estimator_version=RUNNER_VERSION,
            estimator_config_hash=_json_hash({"cost_floor": config.cost_floor, "fdr_q": config.fdr_q}),
            result_summary=summary,
        ))
    ledger.read_all()

    corpus_summary = _run_corpus_aggregation(survivors, sweep, corpus)
    _write_json(report_dir / "corpus_summary.json", corpus_summary)

    shadow_summary = _run_shadow_for_survivors(survivors, sweep)
    _write_json(report_dir / "shadow_summary.json", shadow_summary)

    synthetic_summary = _synthetic_smoke_summary(config.cost_floor) if corpus.corpus_kind == "synthetic_smoke" else None
    if synthetic_summary is not None:
        _write_json(report_dir / "synthetic_smoke_summary.json", synthetic_summary)

    status = corpus.status if corpus.corpus_kind == "synthetic_smoke" else "OFFLINE_DISCOVERY_COMPLETE"
    _write_final_report(
        report_dir,
        status,
        corpus,
        stress_result,
        stress_corpus_manifest,
        grid,
        sweep,
        cards,
        survivors,
        validation_counts,
        corpus_summary,
        shadow_summary,
        synthetic_summary,
        config,
    )

    candidate_counts = {
        "raw_cells": len(grid.cells),
        "cells_tested": sum(1 for r in sweep.cell_results if not r.skipped),
        "blocked_by_locked_rejection": sweep.n_cells_blocked,
        "candidate_cards": len(cards),
        "validator_survivors": len(survivors),
        "sent_to_shadow": shadow_summary.get("n_sent_to_shadow", 0),
        "passing_shadow": shadow_summary.get("n_passing_shadow", 0),
        "final_candidates": len([r for r in shadow_summary.get("results", []) if r.get("shadow_pass")]),
        **validation_counts,
    }
    _write_json(report_dir / "run_summary.json", {
        "schema_version": "offline_discovery_run_summary.v1",
        "report_dir": str(report_dir),
        "grid_hash": grid.grid_hash,
        "status": status,
        "corpus_status": corpus.status,
        "stress_label_status": stress_result.status,
        "stress_label_count": stress_result.label_count,
        "stress_label_summary": stress_result.summary(),
        "stress_corpus_id": stress_corpus_manifest.get("corpus_id") if stress_corpus_manifest else None,
        "stress_corpus_hash": stress_corpus_manifest.get("corpus_hash") if stress_corpus_manifest else None,
        "stress_window_count": stress_corpus_manifest.get("stress_window_count") if stress_corpus_manifest else None,
        "stress_corpus_status": stress_corpus_manifest.get("status") if stress_corpus_manifest else None,
        "candidate_counts": candidate_counts,
        "no_orders_were_placed": True,
        "no_exchange_connections_were_made": True,
        "observer_only": True,
    })

    return OfflineDiscoveryResult(
        report_dir=report_dir,
        grid_hash=grid.grid_hash,
        raw_cell_count=len(grid.cells),
        status=status,
        corpus_status=corpus.status,
        corpus_hash=corpus.corpus_hash,
        manifest_hash=corpus.manifest_hash,
        candidate_counts=candidate_counts,
        data_source=", ".join(str(p) for p in corpus.files[:5]) if corpus.files else "synthetic_smoke_populations",
        used_synthetic=corpus.corpus_kind == "synthetic_smoke",
        stress_label_status=stress_result.status,
        stress_label_count=stress_result.label_count,
        stress_corpus_id=stress_corpus_manifest.get("corpus_id") if stress_corpus_manifest else None,
        stress_corpus_hash=stress_corpus_manifest.get("corpus_hash") if stress_corpus_manifest else None,
        stress_window_count=stress_corpus_manifest.get("stress_window_count") if stress_corpus_manifest else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline Edge Miner discovery on local data only.")
    parser.add_argument("--run-mode", default="offline", help="Must be 'offline'. Other modes are refused.")
    parser.add_argument("--out", type=Path, default=Path("examples/strategies/venue_agnostic_signal_observer/reports"))
    parser.add_argument("--data-dir", type=Path, action="append", default=None)
    parser.add_argument("--cost-floor", type=float, default=DEFAULT_COST_FLOOR)
    parser.add_argument("--force-synthetic", action="store_true")
    parser.add_argument("--stress-corpus-manifest", type=Path, default=None)
    parser.add_argument("--allow-diagnostic-stress-corpus", action="store_true")
    parser.add_argument("--timestamp", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = OfflineDiscoveryConfig(
        output_root=args.out,
        data_dirs=tuple(args.data_dir) if args.data_dir else tuple(DEFAULT_DATA_DIRS),
        run_mode=args.run_mode,
        cost_floor=args.cost_floor,
        force_synthetic=args.force_synthetic,
        timestamp=args.timestamp,
        stress_corpus_manifest=args.stress_corpus_manifest,
        allow_diagnostic_stress_corpus=args.allow_diagnostic_stress_corpus,
    )
    result = run_offline_discovery(config)
    print(json.dumps({
        "report_dir": str(result.report_dir),
        "grid_hash": result.grid_hash,
        "raw_cell_count": result.raw_cell_count,
        "status": result.status,
        "corpus_status": result.corpus_status,
        "used_synthetic": result.used_synthetic,
        "stress_label_status": result.stress_label_status,
        "stress_label_count": result.stress_label_count,
        "stress_corpus_id": result.stress_corpus_id,
        "stress_corpus_hash": result.stress_corpus_hash,
        "stress_window_count": result.stress_window_count,
        "candidate_counts": result.candidate_counts,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
