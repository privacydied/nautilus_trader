"""Unit 28B: legacy CLI dispatcher contract tests.

The dispatcher must stay dependency-light and help-only. It must never import
the moved CLI modules, hypothesis runners, or the runner registry at import
time, and it must refuse arbitrary execution.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import dispatcher
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    iter_legacy_entrypoints,
)

_DISPATCH_IMPORT_PROBE = r"""
import sys

before = set(sys.modules)
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import dispatcher  # noqa: F401
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import __init__ as _pkg  # noqa: F401
after = set(sys.modules)
new = sorted(after - before)

forbidden = (
    "pandas",
    "numpy",
    "runners.registry",
    "runners.cli_specs",
    "runners.callable_cli",
    "runners.legacy_cli.run_",
    "TradingNode",
    "ExecutionClient",
)
hits = [
    name
    for name in new
    if name.endswith(".runner")
    or any(fragment in name for fragment in forbidden)
]
print("HITS:" + ",".join(hits))
raise SystemExit(1 if hits else 0)
"""


def test_package_and_dispatcher_import_dependency_light() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _DISPATCH_IMPORT_PROBE],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, f"unexpected heavy imports: {proc.stdout}\n{proc.stderr}"


def test_dispatcher_list_lists_all_keys(capsys: pytest.CaptureFixture[str]) -> None:
    rc = dispatcher.main(["--list"])
    assert rc == 0
    printed = {line for line in capsys.readouterr().out.splitlines() if line}
    expected = {entry.key for entry in iter_legacy_entrypoints()}
    assert printed == expected
    assert printed  # non-empty


def test_dispatcher_requires_key_without_list() -> None:
    with pytest.raises(SystemExit):
        dispatcher.main([])


def test_dispatcher_rejects_unknown_key() -> None:
    with pytest.raises(SystemExit):
        dispatcher.main(["--key", "definitely_not_a_real_entrypoint"])


def test_dispatcher_rejects_execution_without_help_only() -> None:
    # A real key, but no --help-only: direct execution must be refused.
    key = next(iter(iter_legacy_entrypoints())).key
    with pytest.raises(SystemExit):
        dispatcher.main(["--key", key])


def test_dispatcher_rejects_arbitrary_argv() -> None:
    with pytest.raises(SystemExit):
        dispatcher.main(["--key", "hyperliquid_oi_velocity_compression_phase0", "--", "--seed", "1"])


def test_dispatcher_help_only_works_for_level2_callable_entry() -> None:
    # oi_velocity is a Level-2 callable, help-safe entry whose main() accepts argv.
    key = "hyperliquid_oi_velocity_compression_phase0"
    entry = next((e for e in iter_legacy_entrypoints() if e.key == key), None)
    assert entry is not None
    # --help-only triggers the module's argparse --help, which exits 0.
    with pytest.raises(SystemExit) as excinfo:
        dispatcher.main(["--key", key, "--help-only"])
    code = excinfo.value.code
    assert code in (0, None)


def test_dispatcher_does_not_import_moved_modules_until_dispatch() -> None:
    # Importing/using --list must not import any run_* CLI module.
    before = {name for name in sys.modules if ".runners.legacy_cli.run_" in name}
    dispatcher.main(["--list"])
    after = {name for name in sys.modules if ".runners.legacy_cli.run_" in name}
    assert before == after
