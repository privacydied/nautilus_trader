from __future__ import annotations

from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
    list_actual_run_py_files,
    validate_legacy_entrypoint_catalog,
)


def test_legacy_entrypoints_imports_dependency_light() -> None:
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints as module

    for forbidden in (
        "node_fills_liq_reconstruction.runner",
        "oi_velocity_compression_phase0.runner",
        "cost_feasibility.runner",
        "run_hyperliquid_oi_velocity_compression_phase0",
        "run_hyperliquid_cost_feasibility",
        "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        ".registry_cli",
    ):
        assert forbidden not in module.__file__, module.__file__


def test_catalog_covers_every_actual_root_run_py() -> None:
    actual = set(list_actual_run_py_files())
    cataloged = {entry.path for entry in iter_legacy_entrypoints()}
    assert not actual - cataloged, sorted(actual - cataloged)


def test_catalog_has_no_stale_run_py() -> None:
    validation = validate_legacy_entrypoint_catalog()
    assert not validation.stale, validation.stale


def test_catalog_keys_are_unique_and_sorted() -> None:
    keys = [entry.key for entry in iter_legacy_entrypoints()]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


def test_catalog_module_paths_match_file_paths() -> None:
    for entry in iter_legacy_entrypoints():
        expected = entry.path.replace("/", ".").removesuffix(".py")
        assert entry.module == expected, (entry.key, entry.module, expected)


def test_cli_spec_entries_have_matching_legacy_catalog_entries() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        get_runner_cli_spec,
        iter_runner_cli_specs,
    )

    by_key = {entry.key: entry for entry in iter_legacy_entrypoints()}
    for spec in iter_runner_cli_specs():
        for candidate_key in {f"run_{spec.key}", spec.key}:
            if candidate_key in by_key:
                break
        else:
            pytest.fail(f"missing catalog entry for CLI spec {spec.key}")


def test_known_packaged_cli_entries_map_to_packaged_and_spec() -> None:
    expected_specs = {
        "hyperliquid_oi_velocity_compression_phase0": "hyperliquid_oi_velocity_compression_phase0",
        "hyperliquid_cost_feasibility": "hyperliquid_cost_feasibility",
    }
    by_key = {entry.key: entry for entry in iter_legacy_entrypoints()}
    for key, cli_spec_key in expected_specs.items():
        entry = by_key[f"run_{key}"]
        assert entry.packaged is True, key
        assert entry.cli_spec_key == cli_spec_key, key


def test_node_fills_catalog_entry_has_no_cli_spec() -> None:
    entry = get_legacy_entrypoint("hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0")
    assert entry is not None
    assert entry.cli_spec_key is None
    assert entry.packaged is True


def test_unpackaged_run_py_files_remain_canonical() -> None:
    # Some run_*.py files are support/infrastructure entrypoints kept at root
    # rather than hypothesis CLIs; they can legitimately carry statuses like
    # keep_root/manual_review_required alongside canonical_legacy_cli.
    allowed_unpackaged = {
        "canonical_legacy_cli",
        "manual_review_required",
        "keep_root",
    }
    for run_path in list_actual_run_py_files():
        key = Path(run_path).name.removesuffix(".py")
        entry = get_legacy_entrypoint(key)
        assert entry is not None, key
        if not entry.packaged:
            assert entry.status in allowed_unpackaged, key


def test_catalog_import_does_not_import_hypothesis_runners() -> None:
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints as module

    path_text = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "run_hyperliquid_oi_velocity_compression_phase0",
        "run_hyperliquid_cost_feasibility",
        "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        "import registry_cli",
        "registry_cli import",
    ):
        assert forbidden not in path_text, forbidden


def test_no_automatic_discovery_or_admission_behavior() -> None:
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints as module

    assert not hasattr(module, "register_legacy_entrypoint")
    assert not hasattr(module, "auto_discover_legacy_entrypoints")
