"""
Estimator identity and version metadata.

Every estimator output must carry this metadata. Outputs are append-only —
a changed estimator version produces a new evidence event, never an in-place
replacement.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any


@dataclass
class EstimatorMetadata:
    estimator_name: str
    estimator_version: str
    estimator_config_hash: str
    generated_at_utc: str
    code_git_sha: str | None = None
    input_dataset_hash: str | None = None
    parent_grid_hash: str | None = None
    candidate_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _config_hash(config: dict[str, Any]) -> str:
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_metadata(
    estimator_name: str,
    estimator_version: str,
    config: dict[str, Any] | None = None,
    input_dataset_hash: str | None = None,
    parent_grid_hash: str | None = None,
    candidate_hash: str | None = None,
) -> EstimatorMetadata:
    cfg = config or {}
    return EstimatorMetadata(
        estimator_name=estimator_name,
        estimator_version=estimator_version,
        estimator_config_hash=_config_hash(cfg),
        generated_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        code_git_sha=_git_sha(),
        input_dataset_hash=input_dataset_hash,
        parent_grid_hash=parent_grid_hash,
        candidate_hash=candidate_hash,
    )
