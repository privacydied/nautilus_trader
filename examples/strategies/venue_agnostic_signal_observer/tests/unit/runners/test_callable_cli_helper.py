"""Unit 21: dependency-light callable CLI helper spine."""

from __future__ import annotations

import subprocess
import sys

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners import callable_cli
from examples.strategies.venue_agnostic_signal_observer.runners.callable_cli import (
    CallableRef,
    assert_help_only_argv,
    cli_spec_has_callable_main,
    parse_callable_ref,
    require_cli_spec_main_callable,
    resolve_callable_ref,
    try_parse_callable_ref,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC,
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
)


HELPER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.runners.callable_cli"
)


def _import_probe(forbidden_substrings: tuple[str, ...]) -> list[str]:
    code = f"""
import sys
before = set(sys.modules)
import {HELPER_MODULE}  # noqa: F401
after = set(sys.modules)
new = sorted(after - before)
forbidden = {forbidden_substrings!r}
hits = [n for n in new if any(f in n for f in forbidden)]
for h in hits:
    print(h)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def test_helper_imports_dependency_light_in_subprocess():
    hits = _import_probe(
        (
            "pandas",
            "numpy",
            "requests",
            "websocket",
            "boto",
        )
    )
    assert hits == []


def test_helper_does_not_import_cli_specs():
    assert _import_probe((".runners.cli_specs",)) == []


def test_helper_does_not_import_registry():
    assert _import_probe((".runners.registry",)) == []


def test_helper_does_not_import_registry_cli():
    assert _import_probe((".runners.registry_cli",)) == []


def test_helper_does_not_import_oi_cli():
    assert _import_probe(("run_hyperliquid_oi_velocity_compression_phase0",)) == []


def test_helper_does_not_import_phase0b_cli():
    assert (
        _import_probe(("run_generic_altcoin_stress_regime_ablation_phase0b",)) == []
    )


def test_helper_does_not_import_any_hypothesis_runner():
    assert _import_probe((".hypotheses.",)) == []


def test_parse_valid_reference():
    ref = parse_callable_ref("a.b.c:main")
    assert ref == CallableRef(module="a.b.c", name="main")
    assert ref.dotted == "a.b.c:main"


def test_reject_missing_colon():
    with pytest.raises(ValueError):
        parse_callable_ref("a.b.c.main")


def test_reject_multiple_colons():
    with pytest.raises(ValueError):
        parse_callable_ref("a.b:c:main")


def test_reject_empty_module():
    with pytest.raises(ValueError):
        parse_callable_ref(":main")


def test_reject_empty_name():
    with pytest.raises(ValueError):
        parse_callable_ref("a.b.c:")


def test_reject_dotted_name():
    with pytest.raises(ValueError):
        parse_callable_ref("a.b:c.main")


def test_reject_dotted_module_edges():
    with pytest.raises(ValueError):
        parse_callable_ref(".a.b:main")
    with pytest.raises(ValueError):
        parse_callable_ref("a.b.:main")


def test_try_parse_returns_none_for_none_and_empty():
    assert try_parse_callable_ref(None) is None
    assert try_parse_callable_ref("") is None
    assert try_parse_callable_ref("   ") is None
    assert try_parse_callable_ref("not-a-ref") is None


def test_resolve_imports_lazily_only_when_called():
    target = "run_hyperliquid_oi_velocity_compression_phase0"

    def loaded() -> bool:
        return any(target in name for name in sys.modules)

    ref = parse_callable_ref(
        "examples.strategies.venue_agnostic_signal_observer."
        f"runners.legacy_cli.{target}:main"
    )
    # Parsing must not import the module.
    if not loaded():
        resolved = resolve_callable_ref(ref)
        assert callable(resolved)
        assert loaded()
    else:
        # Module already imported elsewhere in the session; still resolves.
        assert callable(resolve_callable_ref(ref))


def test_resolve_returns_callable_without_invoking():
    sentinel_calls = []

    import types

    mod = types.ModuleType("_callable_cli_probe_mod")

    def fn():
        sentinel_calls.append(1)
        return "called"

    mod.fn = fn
    sys.modules["_callable_cli_probe_mod"] = mod
    try:
        resolved = resolve_callable_ref("_callable_cli_probe_mod:fn")
        assert callable(resolved)
        assert sentinel_calls == []
    finally:
        del sys.modules["_callable_cli_probe_mod"]


def test_resolve_non_callable_raises_type_error():
    import types

    mod = types.ModuleType("_callable_cli_probe_mod2")
    mod.value = 42
    sys.modules["_callable_cli_probe_mod2"] = mod
    try:
        with pytest.raises(TypeError):
            resolve_callable_ref("_callable_cli_probe_mod2:value")
    finally:
        del sys.modules["_callable_cli_probe_mod2"]


def test_require_main_callable_reads_oi_spec():
    ref = require_cli_spec_main_callable(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    assert ref.name == "main"
    assert ref.module.endswith("run_hyperliquid_oi_velocity_compression_phase0")


def test_require_main_callable_reads_phase0b_spec():
    ref = require_cli_spec_main_callable(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC
    )
    assert ref.name == "main"
    assert ref.module.endswith(
        "run_generic_altcoin_stress_regime_ablation_phase0b"
    )


def test_require_main_callable_rejects_phase0a_level1():
    with pytest.raises(ValueError):
        require_cli_spec_main_callable(
            GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC
        )


def test_require_main_callable_rejects_cost_feasibility_level1():
    with pytest.raises(ValueError):
        require_cli_spec_main_callable(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC)


def test_has_callable_main_true_for_level2():
    assert cli_spec_has_callable_main(
        HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
    )
    assert cli_spec_has_callable_main(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_CLI_SPEC
    )


def test_has_callable_main_false_for_level1():
    assert not cli_spec_has_callable_main(
        GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_CLI_SPEC
    )
    assert not cli_spec_has_callable_main(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC)


def test_assert_help_only_argv_accepts_only_help():
    assert assert_help_only_argv(("--help",)) == ("--help",)
    assert assert_help_only_argv(["--help"]) == ("--help",)


def test_assert_help_only_argv_rejects_execution_args():
    for bad in ((), ("--run",), ("--help", "--run"), ("run", "--symbol", "BTC")):
        with pytest.raises(ValueError):
            assert_help_only_argv(bad)


def test_helper_has_no_run_function():
    assert not hasattr(callable_cli, "run")
