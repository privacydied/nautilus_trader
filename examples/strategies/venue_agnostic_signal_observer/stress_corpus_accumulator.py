"""Local-only Stress Corpus Accumulator v1 for Edge Miner.

The accumulator scans existing local public market-data captures, applies the
existing deterministic BTC/ETH stress-label rules, merges labels into independent
stress windows, verifies target time coverage only, and writes a growing corpus
manifest. It does not fetch data, connect to venues, or contain execution logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.stress_corpus import (  # noqa: E402
    DEFAULT_INPUT_DIRS,
    SOURCE_ASSETS,
    TARGET_ASSETS,
    _asset_from_filename,
    _ns_from_iso,
    _utc_iso_from_ns,
    discover_trade_files,
    load_ticks_by_asset,
)
from examples.strategies.venue_agnostic_signal_observer.stress_labels import (  # noqa: E402
    LABEL_VERSION,
    MOVE_30S_THRESHOLD_BPS,
    MOVE_60S_THRESHOLD_BPS,
    RANGE_THRESHOLD_BPS,
    StressLabel,
    build_stress_labels,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite  # noqa: E402

ACCUMULATOR_VERSION = "stress_corpus_accumulator.v1"
ACCUMULATED_CORPUS_ID = "stress_beta_lag_v1_accumulated"
CORPUS_VERSION = "1.0.0"
DEFAULT_OUTPUT_DIR = Path("examples/strategies/venue_agnostic_signal_observer/corpora/stress_beta_lag_v1_accumulated")
_NS_PER_SECOND = 1_000_000_000
INDEPENDENCE_COOLDOWN_SECONDS = 30 * 60
TARGET_PRE_ROLL_SECONDS = 60
MAX_HORIZON_SECONDS = 300
MAX_ENTRY_DELAY_SECONDS = 30
STALE_BUFFER_SECONDS = 30
MIN_READY_USABLE_WINDOWS = 20
READY_STATUSES = {"CORPUS_READY_FOR_RERUN", "STRESS_CORPUS_AVAILABLE"}
DIAGNOSTIC_STATUSES = {"ACCUMULATING", "TARGET_COVERAGE_LIMITED", "NO_NEW_STRESS_WINDOWS"}


@dataclass(frozen=True)
class AccumulatedStressWindow:
    stress_window_id: str
    window_start_utc: str
    window_end_utc: str
    source_asset: str
    trigger_reason: str
    impulse_bps: float
    range_bps: float | None
    realized_vol_bps: float | None
    stress_score: float
    required_target_start_utc: str
    required_target_end_utc: str
    target_assets_available: list[str]
    target_assets_missing: list[str]
    usable_for_edge_miner: bool
    source_label_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccumulatorResult:
    output_dir: Path
    manifest_path: Path
    status: str
    corpus_hash: str
    stress_window_count: int
    usable_window_count: int
    rejected_window_count: int
    ready_for_rerun: bool


def stable_json_hash(payload: Any, length: int = 24) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_file_manifest(files: Sequence[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda p: str(p)):
        if not path.exists():
            continue
        rows.append({
            "path": str(path),
            "asset": _asset_from_filename(path),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    return rows


def _target_has_full_coverage(ticks: Sequence[TradeTickLite], start_ns: int, end_ns: int) -> bool:
    if len(ticks) < 2:
        return False
    ordered = sorted(ticks, key=lambda t: t.ts_event)
    return ordered[0].ts_event <= start_ns and ordered[-1].ts_event >= end_ns


def _merge_reason(labels: Sequence[StressLabel]) -> str:
    reasons = sorted({label.trigger_reason for label in labels})
    return "+".join(reasons)


def _best_label(labels: Sequence[StressLabel]) -> StressLabel:
    return sorted(labels, key=lambda label: (-label.stress_score, label.window_start_utc, label.label_id))[0]


def _merge_independent_labels(labels: Sequence[StressLabel]) -> list[list[StressLabel]]:
    """Merge source labels by source asset if they overlap or occur within 30 minutes."""
    groups: list[list[StressLabel]] = []
    by_source: dict[str, list[StressLabel]] = {asset: [] for asset in SOURCE_ASSETS}
    for label in labels:
        by_source.setdefault(label.source_asset, []).append(label)
    cooldown_ns = INDEPENDENCE_COOLDOWN_SECONDS * _NS_PER_SECOND
    for source_asset in sorted(by_source):
        current: list[StressLabel] = []
        current_end_ns: int | None = None
        for label in sorted(by_source[source_asset], key=lambda x: (x.window_start_utc, x.window_end_utc, x.label_id)):
            start_ns = _ns_from_iso(label.window_start_utc)
            end_ns = _ns_from_iso(label.window_end_utc)
            if not current:
                current = [label]
                current_end_ns = end_ns
                continue
            assert current_end_ns is not None
            if start_ns <= current_end_ns + cooldown_ns:
                current.append(label)
                current_end_ns = max(current_end_ns, end_ns)
            else:
                groups.append(current)
                current = [label]
                current_end_ns = end_ns
        if current:
            groups.append(current)
    return sorted(groups, key=lambda group: (group[0].window_start_utc, group[0].source_asset))


def build_independent_windows(
    labels: Sequence[StressLabel],
    ticks_by_asset: dict[str, list[TradeTickLite]],
) -> list[AccumulatedStressWindow]:
    windows: list[AccumulatedStressWindow] = []
    for group in _merge_independent_labels(labels):
        best = _best_label(group)
        start_ns = min(_ns_from_iso(label.window_start_utc) for label in group)
        end_ns = max(_ns_from_iso(label.window_end_utc) for label in group)
        required_start_ns = start_ns - TARGET_PRE_ROLL_SECONDS * _NS_PER_SECOND
        required_end_ns = end_ns + (MAX_HORIZON_SECONDS + MAX_ENTRY_DELAY_SECONDS + STALE_BUFFER_SECONDS) * _NS_PER_SECOND
        available: list[str] = []
        missing: list[str] = []
        for target in TARGET_ASSETS:
            if _target_has_full_coverage(ticks_by_asset.get(target, []), required_start_ns, required_end_ns):
                available.append(target)
            else:
                missing.append(target)
        base = {
            "source_asset": best.source_asset,
            "window_start_utc": _utc_iso_from_ns(start_ns),
            "window_end_utc": _utc_iso_from_ns(end_ns),
            "required_target_start_utc": _utc_iso_from_ns(required_start_ns),
            "required_target_end_utc": _utc_iso_from_ns(required_end_ns),
            "source_label_ids": sorted(label.label_id for label in group),
        }
        windows.append(AccumulatedStressWindow(
            stress_window_id=stable_json_hash(base),
            window_start_utc=base["window_start_utc"],
            window_end_utc=base["window_end_utc"],
            source_asset=best.source_asset,
            trigger_reason=_merge_reason(group),
            impulse_bps=best.impulse_bps,
            range_bps=best.range_bps,
            realized_vol_bps=best.realized_vol_bps,
            stress_score=max(label.stress_score for label in group),
            required_target_start_utc=base["required_target_start_utc"],
            required_target_end_utc=base["required_target_end_utc"],
            target_assets_available=available,
            target_assets_missing=missing,
            usable_for_edge_miner=len(missing) == 0,
            source_label_ids=base["source_label_ids"],
        ))
    return windows


def _status_for(windows: Sequence[AccumulatedStressWindow]) -> str:
    if not windows:
        return "NO_NEW_STRESS_WINDOWS"
    usable = sum(1 for window in windows if window.usable_for_edge_miner)
    if usable == 0:
        return "TARGET_COVERAGE_LIMITED"
    if usable >= MIN_READY_USABLE_WINDOWS:
        return "CORPUS_READY_FOR_RERUN"
    return "ACCUMULATING"


def _capture_index(files: Sequence[Path]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for path in files:
        root = str(path.parent)
        groups.setdefault(root, {"capture_path": root, "assets": set(), "files": []})
        asset = _asset_from_filename(path)
        if asset:
            groups[root]["assets"].add(asset)
        groups[root]["files"].append(str(path))
    rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        row = groups[key]
        rows.append({
            "capture_path": row["capture_path"],
            "assets": sorted(row["assets"]),
            "files": sorted(row["files"]),
        })
    return rows


def build_accumulated_stress_corpus(
    input_dirs: Sequence[Path] = DEFAULT_INPUT_DIRS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    created_at_utc: str | None = None,
) -> AccumulatorResult:
    created = created_at_utc or datetime.now(UTC).isoformat()
    files = discover_trade_files(input_dirs)
    ticks_by_asset = load_ticks_by_asset(files)
    label_result = build_stress_labels(ticks_by_asset)
    labels = list(label_result.labels)
    windows = build_independent_windows(labels, ticks_by_asset)
    usable_windows = [window for window in windows if window.usable_for_edge_miner]
    rejected_windows = [window for window in windows if not window.usable_for_edge_miner]
    status = _status_for(windows)
    ready = status == "CORPUS_READY_FOR_RERUN"

    file_manifest = _source_file_manifest(files)
    file_manifest_hash = stable_json_hash(file_manifest)
    stress_label_payloads = [label.to_dict() for label in labels]
    stress_label_hash = stable_json_hash(stress_label_payloads)
    window_payloads = [window.to_dict() for window in windows]
    corpus_hash = stable_json_hash({
        "corpus_id": ACCUMULATED_CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "accumulator_version": ACCUMULATOR_VERSION,
        "file_manifest_hash": file_manifest_hash,
        "stress_label_hash": stress_label_hash,
        "stress_windows": window_payloads,
        "status": status,
    })
    coverage_by_target = {
        target: sum(1 for window in usable_windows if target in window.target_assets_available)
        for target in TARGET_ASSETS
    }
    source_files = [str(p) for p in files if _asset_from_filename(p) in SOURCE_ASSETS]
    target_files = [str(p) for p in files if _asset_from_filename(p) in TARGET_ASSETS]
    manifest = {
        "corpus_id": ACCUMULATED_CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "created_at_utc": created,
        "accumulator_version": ACCUMULATOR_VERSION,
        "label_version": LABEL_VERSION,
        "stress_rule_config": {
            "move_30s_threshold_bps": MOVE_30S_THRESHOLD_BPS,
            "move_60s_threshold_bps": MOVE_60S_THRESHOLD_BPS,
            "range_threshold_bps": RANGE_THRESHOLD_BPS,
        },
        "independence_rule": {
            "method": "merge_same_source_labels_within_cooldown",
            "cooldown_seconds": INDEPENDENCE_COOLDOWN_SECONDS,
            "description": "Labels for the same source asset are merged when overlapping or within 30 minutes; one independent stress window is emitted per merged group.",
        },
        "coverage_rule": {
            "target_pre_roll_seconds": TARGET_PRE_ROLL_SECONDS,
            "max_horizon_seconds": MAX_HORIZON_SECONDS,
            "max_entry_delay_seconds": MAX_ENTRY_DELAY_SECONDS,
            "staleness_buffer_seconds": STALE_BUFFER_SECONDS,
            "requires_all_targets": True,
            "target_returns_used_for_selection": False,
        },
        "minimum_ready_usable_windows": MIN_READY_USABLE_WINDOWS,
        "ready_for_rerun": ready,
        "source_assets": list(SOURCE_ASSETS),
        "target_assets": list(TARGET_ASSETS),
        "source_files": source_files,
        "target_files": target_files,
        "stress_label_count": len(labels),
        "stress_window_count": len(windows),
        "usable_window_count": len(usable_windows),
        "rejected_window_count": len(rejected_windows),
        "file_manifest_hash": file_manifest_hash,
        "stress_label_hash": stress_label_hash,
        "corpus_hash": corpus_hash,
        "status": status,
        "stress_windows": window_payloads,
        "label_builder_status": label_result.status,
        "label_builder_reason": label_result.reason,
    }
    target_coverage_summary = {
        "schema_version": "accumulated_target_coverage_summary.v1",
        "coverage_by_target": coverage_by_target,
        "target_assets": list(TARGET_ASSETS),
        "stress_window_count": len(windows),
        "usable_window_count": len(usable_windows),
        "rejected_window_count": len(rejected_windows),
        "requires_all_targets": True,
    }
    window_summary = {
        "schema_version": "accumulated_stress_window_summary.v1",
        "status": status,
        "ready_for_rerun": ready,
        "stress_label_count": len(labels),
        "stress_window_count": len(windows),
        "usable_window_count": len(usable_windows),
        "rejected_window_count": len(rejected_windows),
        "windows": window_payloads,
    }
    accumulator_state = {
        "schema_version": "accumulator_state.v1",
        "status": status,
        "ready_for_rerun": ready,
        "last_run_at_utc": created,
        "input_dirs": [str(p) for p in input_dirs],
        "capture_count": len(_capture_index(files)),
        "stress_window_count": len(windows),
        "usable_window_count": len(usable_windows),
        "corpus_hash": corpus_hash,
        "mode": "local_only",
        "public_capture_mode_implemented": False,
        "public_capture_next_step": "Add a separate public-market-data capture tool if more local stress windows are needed; do not change stress thresholds or grid dimensions.",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "corpus_manifest.json", manifest)
    _write_jsonl(output_dir / "stress_labels.jsonl", stress_label_payloads)
    _write_json(output_dir / "stress_window_summary.json", window_summary)
    _write_json(output_dir / "source_file_manifest.json", file_manifest)
    _write_json(output_dir / "target_coverage_summary.json", target_coverage_summary)
    _write_json(output_dir / "accumulator_state.json", accumulator_state)
    _write_jsonl(output_dir / "capture_index.jsonl", _capture_index(files))
    (output_dir / "corpus_report.md").write_text(
        "\n".join([
            "# Edge Miner Stress Corpus Accumulator v1",
            "",
            f"- mode: local_only",
            f"- status: {status}",
            f"- ready_for_rerun: {ready}",
            f"- corpus_id: {ACCUMULATED_CORPUS_ID}",
            f"- corpus_hash: {corpus_hash}",
            f"- stress_label_count: {len(labels)}",
            f"- independent_stress_window_count: {len(windows)}",
            f"- usable_window_count: {len(usable_windows)}",
            f"- rejected_window_count: {len(rejected_windows)}",
            f"- target_coverage: {coverage_by_target}",
            "- independence_rule: merge same-source labels that overlap or occur within 30 minutes",
            "- target_coverage_rule: require all targets from 60s before stress start through 360s after stress end",
            "- target_returns_used_for_selection: false",
            "- grid_or_thresholds_changed: false",
            "- no_orders_were_placed: true",
            "- no_private_key_flow: true",
            "- public_capture_mode_implemented: false",
        ]) + "\n"
    )
    return AccumulatorResult(
        output_dir=output_dir,
        manifest_path=output_dir / "corpus_manifest.json",
        status=status,
        corpus_hash=corpus_hash,
        stress_window_count=len(windows),
        usable_window_count=len(usable_windows),
        rejected_window_count=len(rejected_windows),
        ready_for_rerun=ready,
    )


def load_accumulated_corpus_manifest(path: Path, *, allow_diagnostic: bool = False) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid accumulated stress corpus manifest: {path}") from exc
    required = {
        "corpus_id",
        "corpus_version",
        "accumulator_version",
        "label_version",
        "source_assets",
        "target_assets",
        "stress_window_count",
        "usable_window_count",
        "corpus_hash",
        "status",
        "stress_windows",
        "ready_for_rerun",
    }
    missing = sorted(required.difference(manifest))
    if missing:
        raise ValueError(f"Invalid accumulated stress corpus manifest missing fields: {missing}")
    status = str(manifest["status"])
    if status in READY_STATUSES:
        if int(manifest["usable_window_count"]) < MIN_READY_USABLE_WINDOWS and status == "CORPUS_READY_FOR_RERUN":
            raise ValueError("Accumulated corpus claims ready with too few usable windows.")
    elif allow_diagnostic and status in DIAGNOSTIC_STATUSES:
        pass
    else:
        raise ValueError("Accumulated stress corpus is not ready for Edge Miner rerun.")
    windows = manifest.get("stress_windows")
    if not isinstance(windows, list):
        raise ValueError("Accumulated stress corpus manifest has invalid stress_windows.")
    if status in READY_STATUSES and not any(w.get("usable_for_edge_miner") for w in windows if isinstance(w, dict)):
        raise ValueError("Accumulated stress corpus manifest has no usable windows.")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Accumulate local-only Edge Miner stress corpus evidence.")
    parser.add_argument("--input-dir", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--created-at-utc", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_accumulated_stress_corpus(
        input_dirs=tuple(args.input_dir) if args.input_dir else DEFAULT_INPUT_DIRS,
        output_dir=args.out,
        created_at_utc=args.created_at_utc,
    )
    print(json.dumps({
        "manifest_path": str(result.manifest_path),
        "status": result.status,
        "ready_for_rerun": result.ready_for_rerun,
        "corpus_hash": result.corpus_hash,
        "stress_window_count": result.stress_window_count,
        "usable_window_count": result.usable_window_count,
        "rejected_window_count": result.rejected_window_count,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
