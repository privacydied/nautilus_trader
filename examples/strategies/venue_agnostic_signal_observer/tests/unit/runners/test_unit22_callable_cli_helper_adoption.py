"""Unit 22: verify the callable CLI helper against the two Level-2 specs.

The two current Level-2 callable CLI specs are exactly:
  - hyperliquid_oi_velocity_compression_phase0
  - generic_altcoin_stress_regime_ablation_phase0b

No real study execution occurs here; only lazy resolution and help-only calls.
"""

from __future__ import annotations

import contextlib
import io
import sys

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.callable_cli import (
    cli_spec_has_callable_main,
    require_cli_spec_main_callable,
    resolve_callable_ref,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC,
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
)


def test_level2_keys_are_exactly_oi_and_phase0b():
    level2 = {
        spec.key
        for spec in ALL_RUNNER_CLI_SPECS
        if spec.level == RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
    }
    assert level2 == {
        "hyperliquid_oi_velocity_compression_phase0",
        "generic_altcoin_stress_regime_ablation_phase0b",
    }


def test_helper_parses_oi_main_callable():
    ref = require_cli_spec_main_callable(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    assert ref.dotted == (
        "examples.strategies.venue_agnostic_signal_observer."
        "run_hyperliquid_oi_velocity_compression_phase0:main"
    )


def test_helper_parses_phase0b_main_callable():
    ref = require_cli_spec_main_callable(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC
    )
    assert ref.dotted == (
        "examples.strategies.venue_agnostic_signal_observer."
        "run_generic_altcoin_stress_regime_ablation_phase0b:main"
    )


def test_resolved_oi_callable_is_its_cli_main():
    ref = require_cli_spec_main_callable(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    resolved = resolve_callable_ref(ref)
    import importlib

    mod = importlib.import_module(ref.module)
    assert resolved is mod.main


def test_resolved_phase0b_callable_is_its_cli_main():
    ref = require_cli_spec_main_callable(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC
    )
    resolved = resolve_callable_ref(ref)
    import importlib

    mod = importlib.import_module(ref.module)
    assert resolved is mod.main


def _assert_help_safe(ref_module_callable) -> None:
    resolved = resolve_callable_ref(ref_module_callable)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            rc = resolved(["--help"])
        except SystemExit as exc:
            assert exc.code in (0, None)
            return
    assert rc in (0, None)


def test_oi_main_help_is_safe():
    ref = require_cli_spec_main_callable(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    _assert_help_safe(ref)


def test_phase0b_main_help_is_safe():
    ref = require_cli_spec_main_callable(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC
    )
    _assert_help_safe(ref)


def test_phase0a_remains_level1_and_rejected():
    assert (
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC.level
        == RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    )
    assert not cli_spec_has_callable_main(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC
    )
    with pytest.raises(ValueError):
        require_cli_spec_main_callable(
            GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC
        )


def test_cost_feasibility_remains_level1_and_rejected():
    assert (
        HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.level
        == RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    )
    assert not cli_spec_has_callable_main(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC)
    with pytest.raises(ValueError):
        require_cli_spec_main_callable(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC)


def test_node_fills_still_lacks_cli_spec():
    keys = {spec.key for spec in ALL_RUNNER_CLI_SPECS}
    assert (
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in keys
    )


def test_resolution_does_not_import_via_helper_module_itself():
    # The helper module never imports CLI modules at import time; resolution is
    # the only path that triggers an import. Confirm the helper does not pull in
    # the registry/cli_specs as a side-effect of resolution.
    ref = require_cli_spec_main_callable(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    resolve_callable_ref(ref)
    helper = sys.modules[
        "examples.strategies.venue_agnostic_signal_observer.runners.callable_cli"
    ]
    # The helper module namespace must not have acquired references to specs.
    assert not hasattr(helper, "ALL_RUNNER_CLI_SPECS")
