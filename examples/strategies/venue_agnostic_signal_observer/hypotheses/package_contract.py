from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True, slots=True)
class HypothesisPackageMetadata:
    key: str
    study_id: str
    family: str
    venue: str
    phase: str
    description: str
    tags: tuple[str, ...]
    cli_module: str | None = None
    implementation_module: str | None = None
    package_module: str | None = None
    legacy_module: str | None = None
    default_report_subdir: str | None = None
    precommitment_path: str | None = None
    doc_path: str | None = None


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise TypeError("tags must contain only strings")
        normalized_tag = tag.strip().lower()
        if not normalized_tag:
            raise ValueError("empty tag is not allowed")
        if normalized_tag in seen:
            raise ValueError(f"duplicate tag after normalization: {normalized_tag}")
        seen.add(normalized_tag)
        normalized.append(normalized_tag)
    return tuple(normalized)


def _required_str(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must be a non-empty string")
    return stripped


def _optional_str(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string when provided")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must be a non-empty string when provided")
    return stripped


def _relative_path(value: str | None, field_name: str) -> str | None:
    stripped = _optional_str(value, field_name)
    if stripped is None:
        return None
    path = PurePosixPath(stripped)
    if path.is_absolute():
        raise ValueError(f"{field_name} must be a relative path")
    if ".." in path.parts:
        raise ValueError(f"{field_name} must not contain parent traversal")
    return stripped


def validate_hypothesis_metadata(
    metadata: HypothesisPackageMetadata,
) -> HypothesisPackageMetadata:
    if not isinstance(metadata, HypothesisPackageMetadata):
        raise TypeError("metadata must be a HypothesisPackageMetadata")

    return replace(
        metadata,
        key=_required_str(metadata.key, "key"),
        study_id=_required_str(metadata.study_id, "study_id"),
        family=_required_str(metadata.family, "family"),
        venue=_required_str(metadata.venue, "venue"),
        phase=_required_str(metadata.phase, "phase"),
        description=_required_str(metadata.description, "description"),
        tags=normalize_tags(metadata.tags),
        cli_module=_optional_str(metadata.cli_module, "cli_module"),
        implementation_module=_optional_str(
            metadata.implementation_module,
            "implementation_module",
        ),
        package_module=_optional_str(metadata.package_module, "package_module"),
        legacy_module=_optional_str(metadata.legacy_module, "legacy_module"),
        default_report_subdir=_relative_path(
            metadata.default_report_subdir,
            "default_report_subdir",
        ),
        precommitment_path=_relative_path(metadata.precommitment_path, "precommitment_path"),
        doc_path=_relative_path(metadata.doc_path, "doc_path"),
    )


__all__ = (
    "HypothesisPackageMetadata",
    "normalize_tags",
    "validate_hypothesis_metadata",
)
