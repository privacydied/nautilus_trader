"""CLI entrypoint for the conductor orchestration layer.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_conductor \\
        --config conductor_config.json \\
        [--once] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .conductor.service import ConductorConfig, ConductorRuntimeState, run_conductor_once


def _normalize_config_path(p: str) -> str:
    """Resolve a config path relative to the Nautilus repo root."""
    raw = Path(p)
    if raw.is_absolute():
        return str(raw)
    return str(raw.absolute())


def load_config(config_path: str) -> dict[str, Any]:
    """Load and return a conductor config dict from JSON."""
    with open(_normalize_config_path(config_path), "r") as f:
        return json.load(f)


def build_conductor_config(raw: dict[str, Any]) -> ConductorConfig:
    """Build a ConductorConfig from a raw dict."""
    return ConductorConfig(
        data_root=Path(raw["data_root"]),
        reports_root=Path(raw["reports_root"]),
        rejected_research_path=Path(raw["rejected_research_path"]),
        ledger_path=Path(raw["ledger_path"]),
        precommitment_dir=Path(raw["precommitment_dir"]),
        gpu_lock_dir=Path(raw.get("gpu_lock_dir", "/tmp/va_signal_observer_gpu_locks")),
        available_devices=tuple(raw.get("available_devices", [])),
        default_min_events=int(raw.get("default_min_events", 50)),
        default_cost_floor_bps=float(raw.get("default_cost_floor_bps", 50.0)),
        poll_existing_captures=bool(raw.get("poll_existing_captures", True)),
        poll_gate_watcher=bool(raw.get("poll_gate_watcher", True)),
        poll_archive_windows=bool(raw.get("poll_archive_windows", True)),
        gate_watcher_status_path=(
            Path(raw["gate_watcher_status_path"])
            if raw.get("gate_watcher_status_path")
            else None
        ),
        archive_windows_path=(
            Path(raw["archive_windows_path"])
            if raw.get("archive_windows_path")
            else None
        ),
        command_templates={
            k: tuple(v) for k, v in raw.get("command_templates", {}).items()
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conductor orchestration for venue-agnostic signal observer"
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to conductor config JSON",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run one conductor iteration and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Poll sources, apply locked-gate filter, simulate promotion "
        "logic against existing summaries.  Skip subprocess execution "
        "and ledger writes.",
    )
    args = parser.parse_args()

    raw_config = load_config(args.config)
    if args.dry_run:
        raw_config["dry_run"] = True

    config = build_conductor_config(raw_config)

    state = ConductorRuntimeState()
    results = run_conductor_once(config, state=state)

    print(f"Conductor iteration complete: {len(results)} result(s)")
    for r in results:
        print(f"  {r.job_id}: {r.status.name} (returncode={r.returncode})")


if __name__ == "__main__":
    main()