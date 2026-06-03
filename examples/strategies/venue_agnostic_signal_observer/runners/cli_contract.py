from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterable


class RunnerCliLevel(str, Enum):
    LEVEL_0_METADATA_ONLY = "level_0_metadata_only"
    LEVEL_1_HELP_SAFE_METADATA = "level_1_help_safe_metadata"
    LEVEL_2_EXPOSES_CALLABLES = "level_2_exposes_callables"
    LEVEL_3_COMMON_CLI_HELPER = "level_3_common_cli_helper"


class RunnerCliRisk(str, Enum):
    PURE_WRAPPER_READY = "pure_wrapper_ready"
    THIN_CLI_WITH_SAFE_HELP_ONLY = "thin_cli_with_safe_help_only"
    ARGPARSE_BUT_HAS_IO_OR_ARTIFACT_WRITES = "argparse_but_has_io_or_artifact_writes"
    RUNNER_CONTAINS_RESEARCH_LOGIC = "runner_contains_research_logic"
    RUNNER_CONTAINS_DATA_FETCH_OR_ARCHIVE_IO = "runner_contains_data_fetch_or_archive_io"
    RUNNER_TOUCHES_PAPER_GOVERNANCE_CONDUCTOR = (
        "runner_touches_paper_governance_conductor"
    )
    RUNNER_TOUCHES_LIVE_OR_OBSERVER_SERVICE = "runner_touches_live_or_observer_service"
    KEEP_LEGACY_FOR_NOW = "keep_legacy_for_now"
    NEEDS_DEEPER_REVIEW = "needs_deeper_review"


@dataclass(frozen=True, slots=True)
class RunnerCliSpec:
    key: str
    description: str
    entry_module: str
    implementation_module: str | None = None
    metadata_module: str | None = None
    build_parser_callable: str | None = None
    main_callable: str | None = None
    level: RunnerCliLevel = RunnerCliLevel.LEVEL_0_METADATA_ONLY
    risk: RunnerCliRisk = RunnerCliRisk.NEEDS_DEEPER_REVIEW
    supports_help_only: bool = False
    import_safe: bool = False
    writes_artifacts: bool = False
    requires_network: bool = False
    requires_archive_data: bool = False
    requires_reports_dir: bool = False
    observer_only: bool = True
    paper_or_governance_sensitive: bool = False
    live_or_service_sensitive: bool = False
    registered: bool = False
    legacy_entrypoint_path: str | None = None
    test_modules: tuple[str, ...] = ()


def normalize_test_modules(test_modules: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in test_modules:
        value = raw_value.strip()
        if not value:
            raise ValueError("test_modules entries must be non-empty strings")
        if value in seen:
            raise ValueError(f"duplicate test_modules entry: {value}")
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _require_non_empty_string(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _normalize_optional_string(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _require_non_empty_string(field_name, value)


def _validate_legacy_entrypoint_path(value: str | None) -> str | None:
    normalized = _normalize_optional_string("legacy_entrypoint_path", value)
    if normalized is None:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError("legacy_entrypoint_path must be relative, not absolute")
    if ".." in path.parts:
        raise ValueError("legacy_entrypoint_path must not contain parent traversal")
    return normalized


def validate_runner_cli_spec(spec: RunnerCliSpec) -> RunnerCliSpec:
    _require_non_empty_string("key", spec.key)
    _require_non_empty_string("description", spec.description)
    _require_non_empty_string("entry_module", spec.entry_module)
    _normalize_optional_string("implementation_module", spec.implementation_module)
    _normalize_optional_string("metadata_module", spec.metadata_module)
    _normalize_optional_string("build_parser_callable", spec.build_parser_callable)
    _normalize_optional_string("main_callable", spec.main_callable)
    _validate_legacy_entrypoint_path(spec.legacy_entrypoint_path)
    normalize_test_modules(spec.test_modules)

    if spec.supports_help_only and not spec.import_safe:
        raise ValueError("supports_help_only requires import_safe=True")

    if spec.paper_or_governance_sensitive and spec.risk is RunnerCliRisk.PURE_WRAPPER_READY:
        raise ValueError(
            "paper_or_governance_sensitive runners must not be PURE_WRAPPER_READY"
        )

    if spec.live_or_service_sensitive and spec.level is RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER:
        raise ValueError(
            "live_or_service_sensitive runners must not use LEVEL_3_COMMON_CLI_HELPER"
        )

    if spec.requires_network and spec.risk is RunnerCliRisk.PURE_WRAPPER_READY:
        raise ValueError("requires_network runners must not be PURE_WRAPPER_READY")

    if spec.requires_archive_data and spec.risk is RunnerCliRisk.PURE_WRAPPER_READY:
        raise ValueError(
            "requires_archive_data runners must not be PURE_WRAPPER_READY"
        )

    if spec.writes_artifacts and spec.risk is RunnerCliRisk.PURE_WRAPPER_READY and spec.level in {
        RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES,
        RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
    }:
        raise ValueError(
            "writes_artifacts PURE_WRAPPER_READY runners must remain Level 0/1"
        )

    if spec.level in {
        RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES,
        RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
    } and spec.main_callable is None:
        raise ValueError("main_callable is required for Level 2/3 runner CLI specs")

    if (
        spec.level is RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER
        and spec.build_parser_callable is None
    ):
        raise ValueError(
            "build_parser_callable is required for Level 3 runner CLI specs"
        )

    return spec


__all__ = (
    "RunnerCliLevel",
    "RunnerCliRisk",
    "RunnerCliSpec",
    "normalize_test_modules",
    "validate_runner_cli_spec",
)
