from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    iter_runner_specs,
)

LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.hyperliquid_cost_feasibility"
)
PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility"
)
RUNNER_MODULE = f"{PACKAGE_MODULE}.runner"
METADATA_MODULE = f"{PACKAGE_MODULE}.metadata"
CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_cost_feasibility"
)
NODE_FILLS_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
)
REPO_ROOT = Path(__file__).resolve().parents[7]
LEGACY_PATH = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/hyperliquid_cost_feasibility.py"
PACKAGE_INIT_PATH = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/hypotheses/hyperliquid/cost_feasibility/__init__.py"


def _reload_module(module_name: str):
    if module_name == RUNNER_MODULE:
        for name in (LEGACY_MODULE, PACKAGE_MODULE, f"{PACKAGE_MODULE}.compat"):
            sys.modules.pop(name, None)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_legacy_root_wrapper_imports_cleanly() -> None:
    legacy = _reload_module(LEGACY_MODULE)
    assert legacy.__name__ == LEGACY_MODULE


def test_package_imports_cleanly() -> None:
    package = _reload_module(PACKAGE_MODULE)
    assert package.__name__ == PACKAGE_MODULE


def test_runner_imports_cleanly() -> None:
    runner = _reload_module(RUNNER_MODULE)
    assert runner.__name__ == RUNNER_MODULE


def test_cli_imports_cleanly() -> None:
    cli = _reload_module(CLI_MODULE)
    assert cli.__name__ == CLI_MODULE


def test_metadata_imports_cleanly() -> None:
    metadata_mod = _reload_module(METADATA_MODULE)
    assert metadata_mod.__name__ == METADATA_MODULE


def test_metadata_validates_contract() -> None:
    metadata_mod = _reload_module(METADATA_MODULE)
    assert isinstance(metadata_mod.METADATA, HypothesisPackageMetadata)
    assert metadata_mod.METADATA.key == "hyperliquid_cost_feasibility"


def test_old_root_wrapper_is_small() -> None:
    assert len(LEGACY_PATH.read_text().splitlines()) <= 12


def test_old_root_wrapper_has_no_algorithmic_defs() -> None:
    tree = ast.parse(LEGACY_PATH.read_text())
    defs = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert defs == []


def test_package_init_remains_metadata_only_small() -> None:
    assert len(PACKAGE_INIT_PATH.read_text().splitlines()) <= 5


def test_package_all_is_metadata_only_tuple() -> None:
    package = _reload_module(PACKAGE_MODULE)
    assert isinstance(package.__all__, tuple)
    assert package.__all__ == ("METADATA",)


def test_package_exposes_metadata_object() -> None:
    package = _reload_module(PACKAGE_MODULE)
    metadata_mod = _reload_module(METADATA_MODULE)
    assert hasattr(package, "METADATA")
    assert package.METADATA == metadata_mod.METADATA


def test_cli_still_imports_old_root_module() -> None:
    cli = _reload_module(CLI_MODULE)
    assert cli.compute_cost_feasibility is _reload_module(LEGACY_MODULE).compute_cost_feasibility
    assert cli.write_cost_outputs is _reload_module(LEGACY_MODULE).write_cost_outputs


def test_registry_count_and_existing_keys_remain_strict_after_unit9c() -> None:
    specs = iter_runner_specs()
    assert len(specs) == 3
    assert tuple(spec.key for spec in specs) == (
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        "hyperliquid_cost_feasibility",
        "hyperliquid_oi_velocity_compression_phase0",
    )


def test_registry_import_does_not_import_cost_feasibility_runner() -> None:
    sys.modules.pop(RUNNER_MODULE, None)
    sys.modules.pop(NODE_FILLS_RUNNER_MODULE, None)
    import examples.strategies.venue_agnostic_signal_observer.runners.registry as registry

    assert RUNNER_MODULE not in sys.modules
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules
    registry.iter_runner_specs()
    assert RUNNER_MODULE not in sys.modules
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules


def test_metadata_import_does_not_import_cost_feasibility_runner_or_node_fills_runner() -> None:
    sys.modules.pop(RUNNER_MODULE, None)
    sys.modules.pop(NODE_FILLS_RUNNER_MODULE, None)
    _reload_module(METADATA_MODULE)
    assert RUNNER_MODULE not in sys.modules
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules
