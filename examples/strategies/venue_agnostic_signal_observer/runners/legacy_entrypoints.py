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


@dataclass(frozen=True, slots=True)
class LegacyEntrypointValidation:
    missing: tuple[str, ...]
    stale: tuple[str, ...]


_PACKAGE_ROOT = Path("examples/strategies/venue_agnostic_signal_observer")
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


def _package_root() -> Path:
    return _PACKAGE_ROOT


def _load_ledger() -> tuple[RootModuleLedgerEntry, ...]:
    return load_root_module_ledger()


def list_actual_run_py_files() -> tuple[str, ...]:
    prefix = str(_PACKAGE_ROOT) + "/"
    files = sorted(
        prefix + p.name
        for p in _PACKAGE_ROOT.glob("run_*.py")
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


def iter_legacy_entrypoints() -> tuple[LegacyEntrypointSpec, ...]:
    entries = _load_ledger()
    by_root = _ledger_by_root_path(entries)
    specs: list[LegacyEntrypointSpec] = []
    seen: set[str] = set()
    for run_path in list_actual_run_py_files():
        run_name = Path(run_path).name
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
                )
            )
    return tuple(sorted(specs, key=lambda item: item.key))


def get_legacy_entrypoint(key: str) -> LegacyEntrypointSpec | None:
    for spec in iter_legacy_entrypoints():
        if spec.key == key:
            return spec
    return None


def validate_legacy_entrypoint_catalog() -> LegacyEntrypointValidation:
    actual = set(list_actual_run_py_files())
    cataloged = {
        entry.root_path
        for entry in _load_ledger()
        if entry.category == "canonical_cli" and entry.root_path.endswith(".py")
    }
    missing = tuple(sorted(actual - cataloged))
    stale = tuple(sorted(cataloged - actual))
    return LegacyEntrypointValidation(missing=missing, stale=stale)
