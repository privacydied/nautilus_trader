"""
Offline stress-corpus assembly for Edge Miner beta-lag research.

The builder scans local capture files, labels source-side BTC/ETH stress windows
with the deterministic stress-label rules, checks only timestamp/file coverage for
target assets, and writes an auditable corpus manifest. It performs no network I/O
and has no execution semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Sequence


PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from examples.strategies.venue_agnostic_signal_observer.stress_labels import LABEL_VERSION
from examples.strategies.venue_agnostic_signal_observer.stress_labels import MOVE_30S_THRESHOLD_BPS
from examples.strategies.venue_agnostic_signal_observer.stress_labels import MOVE_60S_THRESHOLD_BPS
from examples.strategies.venue_agnostic_signal_observer.stress_labels import RANGE_THRESHOLD_BPS
from examples.strategies.venue_agnostic_signal_observer.stress_labels import StressLabel
from examples.strategies.venue_agnostic_signal_observer.stress_labels import build_stress_labels
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
from examples.strategies.venue_agnostic_signal_observer.tick_store import load_trades_jsonl


CORPUS_ID = "stress_beta_lag_v1"
CORPUS_VERSION = "1.0.0"
SOURCE_ASSETS = ("BTC", "ETH")
TARGET_ASSETS = ("SOL", "LINK", "DOGE", "AVAX")
DEFAULT_INPUT_DIRS = (
    Path("data"),
    Path("reports"),
    Path("examples/strategies/venue_agnostic_signal_observer/data"),
    Path("examples/strategies/venue_agnostic_signal_observer/reports"),
)
DEFAULT_OUTPUT_DIR = Path("examples/strategies/venue_agnostic_signal_observer/corpora/stress_beta_lag_v1")
_NS_PER_SECOND = 1_000_000_000
MAX_ENTRY_DELAY_SECONDS = 30
MAX_HORIZON_SECONDS = 300


@dataclass(frozen=True)
class StressWindow:
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StressCorpusBuildResult:
    output_dir: Path
    manifest_path: Path
    status: str
    corpus_hash: str
    stress_window_count: int
    usable_window_count: int
    rejected_window_count: int


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


def _asset_from_symbol(symbol: str) -> str | None:
    raw = symbol.upper().replace("UNRESOLVED:", "")
    token = re.split(r"[-/:]", raw)[0]
    aliases = {"XBT": "BTC", "XXBT": "BTC", "XDG": "DOGE"}
    return aliases.get(token, token) if token else None


def _asset_from_filename(path: Path) -> str | None:
    match = re.match(r"(?:trades|quotes)_[^_]+_(?P<symbol>.+)_\d+\.jsonl$", path.name)
    if not match:
        return None
    return _asset_from_symbol(match.group("symbol"))


def discover_trade_files(input_dirs: Sequence[Path]) -> list[Path]:
    wanted = set(SOURCE_ASSETS + TARGET_ASSETS)
    files: list[Path] = []
    for root in input_dirs:
        if root.exists():
            files.extend(root.rglob("trades_*.jsonl"))
    return sorted((p for p in files if _asset_from_filename(p) in wanted), key=lambda p: str(p))


def load_ticks_by_asset(files: Sequence[Path]) -> dict[str, list[TradeTickLite]]:
    ticks_by_asset: dict[str, list[TradeTickLite]] = {}
    for path in files:
        asset = _asset_from_filename(path)
        if asset is None:
            continue
        ticks_by_asset.setdefault(asset, [])
        ticks_by_asset[asset].extend(load_trades_jsonl(str(path)))
    for asset, ticks in list(ticks_by_asset.items()):
        seen: set[tuple[int, str, str, float, float]] = set()
        clean: list[TradeTickLite] = []
        for tick in sorted(ticks, key=lambda t: t.ts_event):
            key = (tick.ts_event, tick.venue, tick.symbol, tick.price, tick.size)
            if key in seen:
                continue
            seen.add(key)
            clean.append(tick)
        ticks_by_asset[asset] = clean
    return ticks_by_asset


def _file_manifest(files: Sequence[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(files, key=lambda p: str(p)):
        if path.exists():
            st = path.stat()
            rows.append({
                "path": str(path),
                "asset": _asset_from_filename(path),
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
            })
    return rows


def _ns_from_iso(value: str) -> int:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * _NS_PER_SECOND)


def _utc_iso_from_ns(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / _NS_PER_SECOND, tz=UTC).isoformat()


def _target_has_coverage(ticks: Sequence[TradeTickLite], start_ns: int, end_ns: int) -> bool:
    if len(ticks) < 2:
        return False
    return ticks[0].ts_event <= start_ns and ticks[-1].ts_event >= end_ns


def _window_id(payload: dict[str, Any]) -> str:
    return stable_json_hash(payload)


def build_stress_windows(labels: Sequence[StressLabel], ticks_by_asset: dict[str, list[TradeTickLite]]) -> list[StressWindow]:
    windows: list[StressWindow] = []
    for label in sorted(labels, key=lambda x: (x.window_start_utc, x.source_asset, x.label_id)):
        start_ns = _ns_from_iso(label.window_start_utc)
        end_ns = _ns_from_iso(label.window_end_utc)
        required_start_ns = start_ns
        required_end_ns = end_ns + (MAX_ENTRY_DELAY_SECONDS + MAX_HORIZON_SECONDS) * _NS_PER_SECOND
        available: list[str] = []
        missing: list[str] = []
        for asset in TARGET_ASSETS:
            ticks = ticks_by_asset.get(asset, [])
            if _target_has_coverage(ticks, required_start_ns, required_end_ns):
                available.append(asset)
            else:
                missing.append(asset)
        base = {
            "label_id": label.label_id,
            "source_asset": label.source_asset,
            "window_start_utc": label.window_start_utc,
            "window_end_utc": label.window_end_utc,
            "required_target_start_utc": _utc_iso_from_ns(required_start_ns),
            "required_target_end_utc": _utc_iso_from_ns(required_end_ns),
        }
        windows.append(StressWindow(
            stress_window_id=_window_id(base),
            window_start_utc=label.window_start_utc,
            window_end_utc=label.window_end_utc,
            source_asset=label.source_asset,
            trigger_reason=label.trigger_reason,
            impulse_bps=label.impulse_bps,
            range_bps=label.range_bps,
            realized_vol_bps=label.realized_vol_bps,
            stress_score=label.stress_score,
            required_target_start_utc=base["required_target_start_utc"],
            required_target_end_utc=base["required_target_end_utc"],
            target_assets_available=available,
            target_assets_missing=missing,
            usable_for_edge_miner=bool(available),
        ))
    return windows


def _status_for_windows(label_count: int, usable_count: int) -> str:
    if label_count == 0:
        return "NO_STRESS_WINDOWS_FOUND"
    if usable_count == 0:
        return "INSUFFICIENT_TARGET_COVERAGE"
    return "STRESS_CORPUS_AVAILABLE"


def build_stress_corpus(
    input_dirs: Sequence[Path] = DEFAULT_INPUT_DIRS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    created_at_utc: str | None = None,
) -> StressCorpusBuildResult:
    created = created_at_utc or datetime.now(UTC).isoformat()
    files = discover_trade_files(input_dirs)
    ticks_by_asset = load_ticks_by_asset(files)
    labels_result = build_stress_labels(ticks_by_asset)
    labels = list(labels_result.labels)
    windows = build_stress_windows(labels, ticks_by_asset)
    usable_count = sum(1 for window in windows if window.usable_for_edge_miner)
    status = _status_for_windows(len(labels), usable_count)

    source_files = [p for p in files if _asset_from_filename(p) in SOURCE_ASSETS]
    target_files = [p for p in files if _asset_from_filename(p) in TARGET_ASSETS]
    source_file_manifest = _file_manifest(files)
    file_manifest_hash = stable_json_hash(source_file_manifest)
    label_payloads = [label.to_dict() for label in labels]
    stress_label_hash = stable_json_hash(label_payloads)
    window_payloads = [window.to_dict() for window in windows]
    corpus_hash = stable_json_hash({
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "file_manifest_hash": file_manifest_hash,
        "stress_label_hash": stress_label_hash,
        "stress_windows": window_payloads,
        "status": status,
    })
    manifest = {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "created_at_utc": created,
        "label_version": LABEL_VERSION,
        "stress_rule_config": {
            "move_30s_threshold_bps": MOVE_30S_THRESHOLD_BPS,
            "move_60s_threshold_bps": MOVE_60S_THRESHOLD_BPS,
            "range_threshold_bps": RANGE_THRESHOLD_BPS,
            "max_entry_delay_seconds": MAX_ENTRY_DELAY_SECONDS,
            "max_horizon_seconds": MAX_HORIZON_SECONDS,
        },
        "source_assets": list(SOURCE_ASSETS),
        "target_assets": list(TARGET_ASSETS),
        "source_files": [str(p) for p in source_files],
        "target_files": [str(p) for p in target_files],
        "stress_window_count": len(windows),
        "usable_window_count": usable_count,
        "rejected_window_count": len(windows) - usable_count,
        "file_manifest_hash": file_manifest_hash,
        "stress_label_hash": stress_label_hash,
        "corpus_hash": corpus_hash,
        "status": status,
        "stress_windows": window_payloads,
        "label_builder_status": labels_result.status,
        "label_builder_reason": labels_result.reason,
    }
    target_coverage_summary = {
        "schema_version": "target_coverage_summary.v1",
        "target_assets": list(TARGET_ASSETS),
        "stress_window_count": len(windows),
        "usable_window_count": usable_count,
        "coverage_by_target": {
            asset: sum(1 for window in windows if asset in window.target_assets_available)
            for asset in TARGET_ASSETS
        },
    }
    stress_window_summary = {
        "schema_version": "stress_window_summary.v1",
        "status": status,
        "stress_window_count": len(windows),
        "usable_window_count": usable_count,
        "rejected_window_count": len(windows) - usable_count,
        "windows": window_payloads,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "corpus_manifest.json", manifest)
    _write_jsonl(output_dir / "stress_labels.jsonl", label_payloads)
    _write_json(output_dir / "stress_window_summary.json", stress_window_summary)
    _write_json(output_dir / "source_file_manifest.json", source_file_manifest)
    _write_json(output_dir / "target_coverage_summary.json", target_coverage_summary)
    (output_dir / "corpus_report.md").write_text(
        "\n".join([
            "# Edge Miner Stress Beta-Lag Corpus",
            "",
            f"- status: {status}",
            f"- corpus_id: {CORPUS_ID}",
            f"- corpus_hash: {corpus_hash}",
            f"- stress_window_count: {len(windows)}",
            f"- usable_window_count: {usable_count}",
            f"- rejected_window_count: {len(windows) - usable_count}",
            "- source_side_labels_only: true",
            "- target_returns_used_for_selection: false",
            "- no_orders_were_placed: true",
            "- no_exchange_connections_were_made: true",
        ]) + "\n"
    )
    return StressCorpusBuildResult(
        output_dir=output_dir,
        manifest_path=output_dir / "corpus_manifest.json",
        status=status,
        corpus_hash=corpus_hash,
        stress_window_count=len(windows),
        usable_window_count=usable_count,
        rejected_window_count=len(windows) - usable_count,
    )


def load_stress_corpus_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"Invalid stress corpus manifest: {path}") from exc
    required = {
        "corpus_id",
        "corpus_version",
        "label_version",
        "source_assets",
        "target_assets",
        "stress_window_count",
        "usable_window_count",
        "corpus_hash",
        "status",
        "stress_windows",
    }
    missing = sorted(required.difference(manifest))
    if missing:
        raise ValueError(f"Invalid stress corpus manifest missing fields: {missing}")
    if manifest["status"] != "STRESS_CORPUS_AVAILABLE" or int(manifest["usable_window_count"]) <= 0:
        raise ValueError("Stress corpus manifest is not usable for Edge Miner.")
    windows = manifest.get("stress_windows")
    if not isinstance(windows, list) or not any(w.get("usable_for_edge_miner") for w in windows if isinstance(w, dict)):
        raise ValueError("Stress corpus manifest has no usable stress windows.")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build offline Edge Miner stress corpus from local files.")
    parser.add_argument("--input-dir", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--created-at-utc", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = build_stress_corpus(
        input_dirs=tuple(args.input_dir) if args.input_dir else DEFAULT_INPUT_DIRS,
        output_dir=args.out,
        created_at_utc=args.created_at_utc,
    )
    print(json.dumps({
        "manifest_path": str(result.manifest_path),
        "status": result.status,
        "corpus_hash": result.corpus_hash,
        "stress_window_count": result.stress_window_count,
        "usable_window_count": result.usable_window_count,
        "rejected_window_count": result.rejected_window_count,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
