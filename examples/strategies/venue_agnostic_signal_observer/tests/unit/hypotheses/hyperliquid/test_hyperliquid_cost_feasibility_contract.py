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


def test_package_init_is_small() -> None:
    assert len(PACKAGE_INIT_PATH.read_text().splitlines()) <= 10


def test_package_all_is_deterministic_tuple() -> None:
    package = _reload_module(PACKAGE_MODULE)
    assert isinstance(package.__all__, tuple)
    assert package.__all__ == (
        "PROCEED_TO_V1_EVALUATION",
        "DO_NOT_PROCEED_COST_WALL_PERSISTS",
        "INSUFFICIENT_LIVE_CAPTURE",
        "AWAITING_STRESS_WINDOWS",
        "DataSource",
        "CoinCostSummary",
        "CostFeasibilityResult",
        "compute_cost_feasibility",
        "write_cost_outputs",
    )


def test_every_package_all_symbol_exists_on_package() -> None:
    package = _reload_module(PACKAGE_MODULE)
    for name in package.__all__:
        assert hasattr(package, name)


def test_every_package_all_symbol_exists_on_runner() -> None:
    package = _reload_module(PACKAGE_MODULE)
    runner = _reload_module(RUNNER_MODULE)
    for name in package.__all__:
        assert hasattr(runner, name)


def test_every_package_all_symbol_resolves_to_same_object_as_runner() -> None:
    runner = _reload_module(RUNNER_MODULE)
    package = _reload_module(PACKAGE_MODULE)
    for name in package.__all__:
        assert getattr(package, name) is getattr(runner, name)


def test_legacy_wrapper_exposes_every_package_all_symbol() -> None:
    legacy = _reload_module(LEGACY_MODULE)
    package = _reload_module(PACKAGE_MODULE)
    for name in package.__all__:
        assert hasattr(legacy, name)


def test_legacy_wrapper_symbol_identity_matches_package_and_runner() -> None:
    runner = _reload_module(RUNNER_MODULE)
    package = _reload_module(PACKAGE_MODULE)
    legacy = _reload_module(LEGACY_MODULE)
    for name in package.__all__:
        assert getattr(legacy, name) is getattr(package, name)
        assert getattr(legacy, name) is getattr(runner, name)


def test_cli_still_imports_old_root_module() -> None:
    cli = _reload_module(CLI_MODULE)
    assert cli.compute_cost_feasibility is _reload_module(LEGACY_MODULE).compute_cost_feasibility
    assert cli.write_cost_outputs is _reload_module(LEGACY_MODULE).write_cost_outputs


def test_registry_count_and_existing_key_remain_unchanged_after_unit7c() -> None:
    specs = iter_runner_specs()
    assert len(specs) == 1
    assert specs[0].key == "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"


def test_registry_import_does_not_import_cost_feasibility_runner() -> None:
    sys.modules.pop(RUNNER_MODULE, None)
    sys.modules.pop(NODE_FILLS_RUNNER_MODULE, None)
    import examples.strategies.venue_agnostic_signal_observer.runners.registry as registry

    assert RUNNER_MODULE not in sys.modules
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules
    registry.iter_runner_specs()
    assert RUNNER_MODULE not in sys.modules
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules


def test_metadata_import_does_not_import_node_fills_runner() -> None:
    sys.modules.pop(NODE_FILLS_RUNNER_MODULE, None)
    _reload_module(METADATA_MODULE)
    assert NODE_FILLS_RUNNER_MODULE not in sys.modules
