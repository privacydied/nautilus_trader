"""Data models for the paper promotion layer."""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

_SNAKE_CASE_RE = re.compile(r"^[a-z0-9_]+$")


class PaperExecutionMode(Enum):
    """Execution backends for paper strategies. v0 is single-mode."""
    NAUTILUS_BACKTEST_SIMULATED = auto()


class PaperStrategyState(Enum):
    PENDING = auto()
    ENABLED = auto()
    DISABLED = auto()
    KILLED = auto()
    EXPIRED = auto()


@dataclass(frozen=True)
class PaperStrategySpec:
    """Frozen specification for one paper-simulated strategy."""

    strategy_id: str
    signal_family: str
    study_id: str
    precommitment_hash: str
    precommitment_path: Path
    promotion_rule_id: str
    execution_mode: PaperExecutionMode
    group_id: str
    mean_net_bps: float
    valid_count: int
    win_rate: float | None
    cost_floor_bps: float
    min_events: int
    source_venue: str | None
    target_venue: str | None
    source_symbol: str | None
    target_symbol: str | None
    command: tuple[str, ...]
    capture_dir: str | None
    artifacts_dir: Path
    output_dir: Path
    promoter_verdict: str
    promoted_at_utc: str
    last_refalsified_utc: str | None
    refalsification_status: str | None
    state: PaperStrategyState
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id cannot be empty")
        if not self.signal_family:
            raise ValueError("signal_family cannot be empty")
        if not self.study_id:
            raise ValueError("study_id cannot be empty")
        if not _SNAKE_CASE_RE.match(self.signal_family):
            raise ValueError(
                f"signal_family must be lowercase snake_case: {self.signal_family!r}"
            )
        if not _SNAKE_CASE_RE.match(self.study_id):
            raise ValueError(
                f"study_id must be lowercase snake_case: {self.study_id!r}"
            )
        if not self.precommitment_hash:
            raise ValueError("precommitment_hash cannot be empty")
        if self.execution_mode != PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED:
            raise ValueError(
                f"execution_mode must be NAUTILUS_BACKTEST_SIMULATED, "
                f"got {self.execution_mode}"
            )
        if not self.promotion_rule_id:
            raise ValueError("promotion_rule_id cannot be empty")
        if not self.command:
            raise ValueError("command cannot be empty")
        if not self.artifacts_dir:
            raise ValueError("artifacts_dir cannot be empty")
        if not self.output_dir:
            raise ValueError("output_dir cannot be empty")
        if self.cost_floor_bps < 0:
            raise ValueError("cost_floor_bps must be >= 0")
        if self.min_events < 1:
            raise ValueError("min_events must be >= 1")
        if self.state == PaperStrategyState.ENABLED and not self.promoted_at_utc:
            raise ValueError("promoted_at_utc must be non-empty for ENABLED state")
        if self.promoted_at_utc:
            # Lightweight ISO 8601 check
            try:
                datetime.datetime.fromisoformat(self.promoted_at_utc)
            except (ValueError, TypeError):
                raise ValueError(
                    f"promoted_at_utc must be valid ISO 8601: {self.promoted_at_utc!r}"
                )