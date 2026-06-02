from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_runner_registry_cli_list_empty_registry() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.runners.cli",
            "--list",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip() == "no registered runners"
