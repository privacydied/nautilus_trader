from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import json

try:
    import orjson
except ImportError:  # pragma: no cover
    orjson = None

_ALLOWED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "package_marker",
        "canonical_cli",
        "legacy_root_canonical",
        "legacy_compat_wrapper",
        "shared_infrastructure",
        "core_utility_candidate",
        "venue_adapter_candidate",
        "data_adapter_candidate",
        "hypothesis_module",
        "runner_or_entrypoint_support",
        "paper_governance_or_conductor",
        "bot_or_service",
        "test_or_validation_support",
        "quarantine_candidate",
        "unknown_needs_review",
    }
)
_ALLOWED_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "keep_root",
        "move_to_core_candidate",
        "move_to_venues_candidate",
        "move_to_data_candidate",
        "move_to_hypothesis_candidate",
        "already_packaged_wrapper",
        "canonical_legacy_cli",
        "manual_review_required",
        "do_not_move_without_separate_audit",
        "quarantine_review_candidate",
    }
)
_PACKAGE_ROOT_RELATIVE = Path("examples/strategies/venue_agnostic_signal_observer")
_LEDGER_FILE_NAME = "root_module_ledger.v0.json"


@dataclass(frozen=True, slots=True)
class RootModuleLedgerEntry:
    root_path: str
    module_name: str
    category: str
    status: str
    target_package: str | None
    migration_unit: str | None
    compatibility_wrapper_required: bool
    paired_cli: str | None
    registry_key: str | None
    cli_spec_key: str | None
    notes: str


@dataclass(frozen=True, slots=True)
class RootModuleLedgerValidation:
    actual_root_paths: tuple[str, ...]
    ledger_root_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    stale_paths: tuple[str, ...]
    duplicate_paths: tuple[str, ...]
    invalid_categories: tuple[str, ...]
    invalid_statuses: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def package_root() -> Path:
    return _repo_root() / _PACKAGE_ROOT_RELATIVE


def ledger_path() -> Path:
    return package_root() / "structure" / _LEDGER_FILE_NAME


def _loads_json_bytes(payload: bytes) -> dict[str, object]:
    if orjson is not None:
        return orjson.loads(payload)
    return json.loads(payload.decode("utf-8"))


def load_root_module_ledger() -> tuple[RootModuleLedgerEntry, ...]:
    payload = _loads_json_bytes(ledger_path().read_bytes())
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("root module ledger entries must be a list")
    return tuple(RootModuleLedgerEntry(**entry) for entry in entries)


def list_actual_root_py_files() -> tuple[str, ...]:
    root = package_root()
    return tuple(
        sorted(
            str(path.relative_to(_repo_root()))
            for path in root.iterdir()
            if path.is_file() and path.suffix == ".py"
        )
    )


def validate_root_module_ledger() -> RootModuleLedgerValidation:
    entries = load_root_module_ledger()
    actual_paths = list_actual_root_py_files()
    ledger_paths = tuple(entry.root_path for entry in entries)

    seen: set[str] = set()
    duplicates: list[str] = []
    for path in ledger_paths:
        if path in seen and path not in duplicates:
            duplicates.append(path)
        seen.add(path)

    actual_set = set(actual_paths)
    ledger_set = set(ledger_paths)
    invalid_categories = tuple(sorted(entry.root_path for entry in entries if entry.category not in _ALLOWED_CATEGORIES))
    invalid_statuses = tuple(sorted(entry.root_path for entry in entries if entry.status not in _ALLOWED_STATUSES))
    return RootModuleLedgerValidation(
        actual_root_paths=actual_paths,
        ledger_root_paths=ledger_paths,
        missing_paths=tuple(sorted(actual_set - ledger_set)),
        stale_paths=tuple(sorted(ledger_set - actual_set)),
        duplicate_paths=tuple(sorted(duplicates)),
        invalid_categories=invalid_categories,
        invalid_statuses=invalid_statuses,
    )


def iter_entries_by_status(status: str) -> tuple[RootModuleLedgerEntry, ...]:
    return tuple(entry for entry in load_root_module_ledger() if entry.status == status)


def iter_entries_by_category(category: str) -> tuple[RootModuleLedgerEntry, ...]:
    return tuple(entry for entry in load_root_module_ledger() if entry.category == category)
