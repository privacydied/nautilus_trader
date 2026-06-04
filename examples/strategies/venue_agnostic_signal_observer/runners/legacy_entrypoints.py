from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    RootModuleLedgerEntry,
    load_root_module_ledger,
)


@dataclass(frozen=True, slots=True)
class LegacyEntrypointSpec:
    key: str
    path: str
    module: str
    paired_root_module: str | None
    packaged: bool
    registry_key: str | None
    cli_spec_key: str | None
    help_safe: bool | None
    status: str
    notes: str
    # Unit 28 root-removal fields.
    root_removed: bool = False
    old_root_path: str | None = None
    replacement_module: str | None = None
    blocker: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyEntrypointValidation:
    missing: tuple[str, ...]
    stale: tuple[str, ...]


_PACKAGE_ROOT = Path("examples/strategies/venue_agnostic_signal_observer")
_LEGACY_CLI_ROOT = _PACKAGE_ROOT / "runners" / "legacy_cli"
_PACKAGED_PAIRED_NAMES: set[str] = {
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
}
_PACKAGED_PAIRED_REGISTRY: dict[str, str | None] = {
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0": None,
    "hyperliquid_cost_feasibility": "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0": "hyperliquid_oi_velocity_compression_phase0",
}
_PACKAGED_PAIRED_SPECS: dict[str, str | None] = {
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0": None,
    "hyperliquid_cost_feasibility": "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0": "hyperliquid_oi_velocity_compression_phase0",
}

# run_*.py files intentionally retained at root, with their blocker.
# Track A explicitly migrated the metadata-pinned flagship CLIs into
# runners/legacy_cli, so they no longer appear in this retained blocker map.
_ROOT_RETAINED_BLOCKERS: dict[str, str] = {
    # Shared infrastructure despite the run_ prefix; imported as a library by
    # many hypothesis/runner modules. Not a CLI entrypoint.
    "run_artifacts.py": "shared_infrastructure_not_cli",
    # Module name collides with a scaffold forbidden-import substring ("order"
    # inside "recorder"); migrating under runners/ trips the core safety
    # contract scan. Retained at root to avoid weakening that safety test.
    "run_hip3_builder_dex_tradfi_forward_recorder_v0.py": "scaffold_forbidden_term_collision",
    # Paper/conductor/observer behavior area. Retained because the module
    # imports psutil (unavailable here, so --help parity cannot be verified)
    # and because it is wired into a live systemd unit
    # (systemd/nautilus-hyperliquid-observer-v0.service) that must not break.
    # Track C migrated the help-safe, scaffold-clean CLIs (run_conductor,
    # run_signal_observer, run_stage2_gate_watcher) under runners/legacy_cli.
    "run_hyperliquid_observer.py": "forbidden_behavior_area",
    # Paper CLIs import paper.* modules. The scaffold safety contract
    # (test_scaffold_modules_do_not_import_forbidden_terms) forbids any module
    # under runners/ from importing a "paper"-named target, so these cannot be
    # migrated under runners/legacy_cli without weakening that safety test.
    "run_paper_promotion.py": "scaffold_forbidden_term_collision",
    "run_paper_refalsification.py": "scaffold_forbidden_term_collision",
    "run_hyperliquid_btc_eth_ml_atr_paper_v0.py": "scaffold_forbidden_term_collision",
}


def _load_ledger() -> tuple[RootModuleLedgerEntry, ...]:
    return load_root_module_ledger()


def list_actual_run_py_files() -> tuple[str, ...]:
    """Actual ``run_*.py`` files still present at the package root."""
    prefix = str(_PACKAGE_ROOT) + "/"
    files = sorted(
        prefix + p.name
        for p in _PACKAGE_ROOT.glob("run_*.py")
        if p.is_file()
    )
    return tuple(files)


def list_moved_run_py_files() -> tuple[str, ...]:
    """``run_*.py`` files migrated into ``runners/legacy_cli/`` (Unit 28)."""
    prefix = str(_LEGACY_CLI_ROOT) + "/"
    files = sorted(
        prefix + p.name
        for p in _LEGACY_CLI_ROOT.glob("run_*.py")
        if p.is_file()
    )
    return tuple(files)


def _ledger_by_root_path(entries: tuple[RootModuleLedgerEntry, ...]) -> dict[str, RootModuleLedgerEntry]:
    by_root: dict[str, RootModuleLedgerEntry] = {}
    for entry in entries:
        if entry.root_path.endswith(".py"):
            by_root[entry.root_path] = entry
    return by_root


def _paired_root_module(run_path: str) -> str | None:
    run_name = Path(run_path).name
    if not run_name.startswith("run_"):
        return None
    return str(Path(run_path).parent / run_name.removeprefix("run_"))


def _paired_root_key(run_path: str) -> str | None:
    run_name = Path(run_path).name
    if not run_name.startswith("run_"):
        return None
    return run_name.removeprefix("run_").removesuffix(".py")


def _old_root_path_for(run_path: str) -> str:
    return str(_PACKAGE_ROOT / Path(run_path).name)


def iter_legacy_entrypoints() -> tuple[LegacyEntrypointSpec, ...]:
    entries = _load_ledger()
    by_root = _ledger_by_root_path(entries)
    specs: list[LegacyEntrypointSpec] = []
    seen: set[str] = set()
    # Root-retained run files first, then files migrated into legacy_cli.
    root_run_files = list_actual_run_py_files()
    moved_run_files = list_moved_run_py_files()
    for run_path in (*root_run_files, *moved_run_files):
        run_name = Path(run_path).name
        root_removed = run_path in moved_run_files
        blocker = None if root_removed else _ROOT_RETAINED_BLOCKERS.get(run_name)
        paired_module = _paired_root_module(run_path)
        paired_key_name = Path(paired_module).name.removesuffix(".py") if paired_module else None
        ledger_entry = by_root.get(paired_module) if paired_module else None
        if ledger_entry is None and paired_key_name in _PACKAGED_PAIRED_NAMES:
            status = "already_packaged_wrapper"
            category = "legacy_compat_wrapper"
            registry_key = _PACKAGED_PAIRED_REGISTRY.get(paired_key_name)
            cli_spec_key = _PACKAGED_PAIRED_SPECS.get(paired_key_name)
            notes = "Implicit packaged CLI pairing from filesystem."
        elif ledger_entry is None:
            status = "canonical_legacy_cli"
            category = "canonical_cli"
            registry_key = None
            cli_spec_key = None
            notes = ""
        else:
            status = ledger_entry.status
            category = ledger_entry.category
            registry_key = ledger_entry.registry_key
            cli_spec_key = ledger_entry.cli_spec_key
            notes = ledger_entry.notes
            if status.startswith("move_to_") and status != "manual_review_required":
                status = "canonical_legacy_cli"
                category = "canonical_cli"
        primary_key = run_name.removesuffix(".py")
        if primary_key in seen:
            continue
        seen.add(primary_key)
        module_name = run_path.replace("/", ".").removesuffix(".py")
        packaged = status == "already_packaged_wrapper"
        replacement_module = module_name if root_removed else None
        old_root_path = _old_root_path_for(run_path) if root_removed else None
        primary = LegacyEntrypointSpec(
            key=primary_key,
            path=run_path,
            module=module_name,
            paired_root_module=paired_module,
            packaged=packaged,
            registry_key=registry_key,
            cli_spec_key=cli_spec_key,
            help_safe=status in {"already_packaged_wrapper", "canonical_legacy_cli"},
            status=status,
            notes=notes,
            root_removed=root_removed,
            old_root_path=old_root_path,
            replacement_module=replacement_module,
            blocker=blocker,
        )
        specs.append(primary)
        for alias in [
            alias
            for alias in {
                _paired_root_key(run_path),
                registry_key,
                cli_spec_key,
            }
            if alias and alias != primary_key
        ]:
            if alias in seen:
                continue
            seen.add(alias)
            specs.append(
                LegacyEntrypointSpec(
                    key=alias,
                    path=run_path,
                    module=module_name,
                    paired_root_module=paired_module,
                    packaged=packaged,
                    registry_key=registry_key,
                    cli_spec_key=cli_spec_key,
                    help_safe=primary.help_safe,
                    status=status,
                    notes=notes,
                    root_removed=root_removed,
                    old_root_path=old_root_path,
                    replacement_module=replacement_module,
                    blocker=blocker,
                )
            )
    return tuple(sorted(specs, key=lambda item: item.key))


def get_legacy_entrypoint(key: str) -> LegacyEntrypointSpec | None:
    for spec in iter_legacy_entrypoints():
        if spec.key == key:
            return spec
    return None


def iter_root_retained_entrypoints() -> tuple[LegacyEntrypointSpec, ...]:
    """Primary specs for run files intentionally retained at the package root."""
    retained: list[LegacyEntrypointSpec] = []
    moved = set(list_moved_run_py_files())
    for spec in iter_legacy_entrypoints():
        if spec.path in moved:
            continue
        if Path(spec.path).name != f"{spec.key}.py":
            continue  # alias, skip
        retained.append(spec)
    return tuple(retained)


def validate_legacy_entrypoint_catalog() -> LegacyEntrypointValidation:
    actual_root = set(list_actual_run_py_files())
    actual_moved = set(list_moved_run_py_files())
    actual = actual_root | actual_moved
    cataloged = {
        entry.root_path
        for entry in _load_ledger()
        if entry.category == "canonical_cli" and entry.root_path.endswith(".py")
    }
    moved_cataloged = {path for path in cataloged if "/runners/legacy_cli/" in path}
    missing = tuple(sorted((actual_root - cataloged) | (actual_moved - moved_cataloged)))
    stale = tuple(sorted(cataloged - actual))
    return LegacyEntrypointValidation(missing=missing, stale=stale)
