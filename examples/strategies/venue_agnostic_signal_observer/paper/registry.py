"""File-based registry for paper strategy specifications.

Each strategy is stored as an atomic JSON file in a registry directory.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..conductor.atomic_io import write_json_atomic
from .models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState


def _spec_to_dict(spec: PaperStrategySpec) -> dict:
    return {
        "strategy_id": spec.strategy_id,
        "signal_family": spec.signal_family,
        "study_id": spec.study_id,
        "precommitment_hash": spec.precommitment_hash,
        "precommitment_path": str(spec.precommitment_path),
        "promotion_rule_id": spec.promotion_rule_id,
        "execution_mode": spec.execution_mode.name,
        "group_id": spec.group_id,
        "mean_net_bps": spec.mean_net_bps,
        "valid_count": spec.valid_count,
        "win_rate": spec.win_rate,
        "cost_floor_bps": spec.cost_floor_bps,
        "min_events": spec.min_events,
        "source_venue": spec.source_venue,
        "target_venue": spec.target_venue,
        "source_symbol": spec.source_symbol,
        "target_symbol": spec.target_symbol,
        "command": list(spec.command),
        "capture_dir": spec.capture_dir,
        "artifacts_dir": str(spec.artifacts_dir),
        "output_dir": str(spec.output_dir),
        "promoter_verdict": spec.promoter_verdict,
        "promoted_at_utc": spec.promoted_at_utc,
        "last_refalsified_utc": spec.last_refalsified_utc,
        "refalsification_status": spec.refalsification_status,
        "state": spec.state.name,
        "metadata": dict(spec.metadata),
    }


def _dict_to_spec(data: dict) -> PaperStrategySpec:
    return PaperStrategySpec(
        strategy_id=data["strategy_id"],
        signal_family=data["signal_family"],
        study_id=data["study_id"],
        precommitment_hash=data["precommitment_hash"],
        precommitment_path=Path(data["precommitment_path"]),
        promotion_rule_id=data["promotion_rule_id"],
        execution_mode=PaperExecutionMode[data["execution_mode"]],
        group_id=data["group_id"],
        mean_net_bps=data["mean_net_bps"],
        valid_count=data["valid_count"],
        win_rate=data.get("win_rate"),
        cost_floor_bps=data["cost_floor_bps"],
        min_events=data["min_events"],
        source_venue=data.get("source_venue"),
        target_venue=data.get("target_venue"),
        source_symbol=data.get("source_symbol"),
        target_symbol=data.get("target_symbol"),
        command=tuple(data["command"]),
        capture_dir=data.get("capture_dir"),
        artifacts_dir=Path(data["artifacts_dir"]),
        output_dir=Path(data["output_dir"]),
        promoter_verdict=data["promoter_verdict"],
        promoted_at_utc=data["promoted_at_utc"],
        last_refalsified_utc=data.get("last_refalsified_utc"),
        refalsification_status=data.get("refalsification_status"),
        state=PaperStrategyState[data["state"]],
        metadata=data.get("metadata", {}),
    )


def _strategy_path(registry_dir: Path, strategy_id: str) -> Path:
    return registry_dir / f"{strategy_id}.json"


def save_strategy(strategy: PaperStrategySpec, registry_dir: Path) -> None:
    """Save a strategy spec to the registry using atomic write."""
    path = _strategy_path(registry_dir, strategy.strategy_id)
    write_json_atomic(path, _spec_to_dict(strategy))


def load_strategy(
    strategy_id: str, registry_dir: Path
) -> PaperStrategySpec | None:
    """Load a single strategy from the registry."""
    path = _strategy_path(registry_dir, strategy_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return _dict_to_spec(data)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def load_all_strategies(registry_dir: Path) -> list[PaperStrategySpec]:
    """Load all strategies from the registry.

    Skips non-JSON files and corrupt entries gracefully.
    """
    strategies: list[PaperStrategySpec] = []
    if not registry_dir.is_dir():
        return strategies
    for child in sorted(registry_dir.iterdir()):
        if not child.is_file() or not child.name.endswith(".json"):
            continue
        strategy_id = child.stem
        spec = load_strategy(strategy_id, registry_dir)
        if spec is not None:
            strategies.append(spec)
    return strategies


def update_strategy_state(
    strategy_id: str,
    new_state: PaperStrategyState,
    registry_dir: Path,
) -> PaperStrategySpec | None:
    """Update the state of an existing strategy in-place."""
    spec = load_strategy(strategy_id, registry_dir)
    if spec is None:
        return None
    updated = PaperStrategySpec(
        strategy_id=spec.strategy_id,
        signal_family=spec.signal_family,
        study_id=spec.study_id,
        precommitment_hash=spec.precommitment_hash,
        precommitment_path=spec.precommitment_path,
        promotion_rule_id=spec.promotion_rule_id,
        execution_mode=spec.execution_mode,
        group_id=spec.group_id,
        mean_net_bps=spec.mean_net_bps,
        valid_count=spec.valid_count,
        win_rate=spec.win_rate,
        cost_floor_bps=spec.cost_floor_bps,
        min_events=spec.min_events,
        source_venue=spec.source_venue,
        target_venue=spec.target_venue,
        source_symbol=spec.source_symbol,
        target_symbol=spec.target_symbol,
        command=spec.command,
        capture_dir=spec.capture_dir,
        artifacts_dir=spec.artifacts_dir,
        output_dir=spec.output_dir,
        promoter_verdict=spec.promoter_verdict,
        promoted_at_utc=spec.promoted_at_utc,
        last_refalsified_utc=spec.last_refalsified_utc,
        refalsification_status=spec.refalsification_status,
        state=new_state,
        metadata=spec.metadata,
    )
    save_strategy(updated, registry_dir)
    return updated


def delete_strategy(strategy_id: str, registry_dir: Path) -> bool:
    """Delete a strategy file from the registry.

    Returns True if the file existed and was removed, False otherwise.
    """
    path = _strategy_path(registry_dir, strategy_id)
    if not path.is_file():
        return False
    path.unlink()
    return True