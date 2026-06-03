from __future__ import annotations

import dataclasses
import importlib
import sys

import pytest

from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
    normalize_tags,
    validate_hypothesis_metadata,
)

_HEAVY_MODULES = (
    "examples.strategies.venue_agnostic_signal_observer.paper",
    "examples.strategies.venue_agnostic_signal_observer.governance",
    "examples.strategies.venue_agnostic_signal_observer.conductor",
    "examples.strategies.venue_agnostic_signal_observer.bot",
    "examples.strategies.venue_agnostic_signal_observer.shadow",
    "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.node_fills_liq_reconstruction.runner",
)


def _valid_metadata(**overrides: object) -> HypothesisPackageMetadata:
    values: dict[str, object] = {
        "key": "synthetic_key_v0",
        "study_id": "synthetic_study_v0",
        "family": "synthetic_family",
        "venue": "hyperliquid",
        "phase": "phase0",
        "description": "Synthetic metadata-only package contract.",
        "tags": ("observer_only", "synthetic"),
        "cli_module": "pkg.cli",
        "implementation_module": "pkg.runner",
        "package_module": "pkg",
        "legacy_module": "legacy_pkg",
        "default_report_subdir": "reports/synthetic",
        "precommitment_path": "precommitments/synthetic.md",
        "doc_path": "docs/synthetic.md",
    }
    values.update(overrides)
    return HypothesisPackageMetadata(**values)


def test_valid_metadata_object_passes_validation() -> None:
    metadata = _valid_metadata()

    validated = validate_hypothesis_metadata(metadata)

    assert validated == metadata


def test_normalize_tags_strips_whitespace() -> None:
    assert normalize_tags((" observer_only ", "Synthetic_Tag")) == (
        "observer_only",
        "synthetic_tag",
    )


def test_normalize_tags_rejects_empty_tag() -> None:
    with pytest.raises(ValueError, match="empty tag"):
        normalize_tags(("observer_only", "  "))


def test_normalize_tags_rejects_duplicate_normalized_tag() -> None:
    with pytest.raises(ValueError, match="duplicate tag"):
        normalize_tags(("Alpha", " alpha "))


@pytest.mark.parametrize("field", ("key", "study_id", "family", "venue", "phase", "description"))
def test_required_string_fields_reject_empty_string(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        validate_hypothesis_metadata(_valid_metadata(**{field: "  "}))


@pytest.mark.parametrize(
    "field",
    (
        "cli_module",
        "implementation_module",
        "package_module",
        "legacy_module",
        "default_report_subdir",
        "precommitment_path",
        "doc_path",
    ),
)
def test_optional_strings_reject_empty_string_when_provided(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        validate_hypothesis_metadata(_valid_metadata(**{field: "  "}))


def test_absolute_precommitment_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="precommitment_path"):
        validate_hypothesis_metadata(_valid_metadata(precommitment_path="/tmp/precommitment.md"))


def test_absolute_doc_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="doc_path"):
        validate_hypothesis_metadata(_valid_metadata(doc_path="/tmp/doc.md"))


@pytest.mark.parametrize("field", ("default_report_subdir", "precommitment_path", "doc_path"))
def test_parent_traversal_path_is_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        validate_hypothesis_metadata(_valid_metadata(**{field: "../outside.md"}))


def test_metadata_validation_performs_no_imports() -> None:
    fake_module = "unit7b.fake_metadata_module_should_not_import"
    sys.modules.pop(fake_module, None)

    metadata = _valid_metadata(implementation_module=fake_module)
    validate_hypothesis_metadata(metadata)

    assert fake_module not in sys.modules


def test_metadata_module_import_does_not_import_heavy_modules() -> None:
    for module_name in _HEAVY_MODULES:
        sys.modules.pop(module_name, None)

    module = importlib.import_module(
        "examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract"
    )

    assert hasattr(module, "HypothesisPackageMetadata")
    for module_name in _HEAVY_MODULES:
        assert module_name not in sys.modules


def test_metadata_object_is_frozen() -> None:
    metadata = _valid_metadata()

    with pytest.raises(dataclasses.FrozenInstanceError):
        metadata.key = "other"  # type: ignore[misc]


def test_metadata_object_has_deterministic_tags_tuple() -> None:
    metadata = validate_hypothesis_metadata(_valid_metadata(tags=[" Beta ", "alpha"]))

    assert metadata.tags == ("beta", "alpha")
    assert isinstance(metadata.tags, tuple)
