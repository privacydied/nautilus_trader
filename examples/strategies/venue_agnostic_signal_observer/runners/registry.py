from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import HypothesisRunner
from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
    validate_hypothesis_metadata,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.cost_feasibility.metadata import (
    METADATA as HYPERLIQUID_COST_FEASIBILITY_METADATA,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.oi_velocity_compression_phase0.metadata import (
    METADATA as HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_METADATA,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.metadata import (
    METADATA as GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA,
)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b.metadata import (
    METADATA as GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA,
)
from examples.strategies.venue_agnostic_signal_observer.runners.base import RegisteredRunner

# ---------------------------------------------------------------------------
# Runtime runner registry (HypothesisRunner instances)
# ---------------------------------------------------------------------------

_RUNNERS_BY_NAME: dict[str, RegisteredRunner] = {}
_RUNNER_NAMES_BY_STUDY_ID: dict[str, str] = {}


def register_runner(name: str, runner: HypothesisRunner) -> RegisteredRunner:
    if name in _RUNNERS_BY_NAME:
        raise ValueError(f"Duplicate runner name: {name}")
    study_id = runner.spec.study_id
    if study_id in _RUNNER_NAMES_BY_STUDY_ID:
        raise ValueError(f"Duplicate study_id: {study_id}")
    registered = RegisteredRunner(name=name, runner=runner)
    _RUNNERS_BY_NAME[name] = registered
    _RUNNER_NAMES_BY_STUDY_ID[study_id] = name
    return registered


def get_runner(name: str) -> RegisteredRunner:
    try:
        return _RUNNERS_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown runner: {name}") from exc


def list_runners() -> list[RegisteredRunner]:
    return [_RUNNERS_BY_NAME[name] for name in sorted(_RUNNERS_BY_NAME)]


def clear_runner_registry_for_tests() -> None:
    _RUNNERS_BY_NAME.clear()
    _RUNNER_NAMES_BY_STUDY_ID.clear()


# ---------------------------------------------------------------------------
# Metadata-only runner spec registry (lazy import, no heavy imports at load)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunnerSpec:
    """Lightweight, import-time-safe descriptor for a runner.

    Holds only strings — no module imports happen until an explicit
    ``import_*`` helper is called.
    """

    key: str
    family: str
    venue: str
    study_id: str
    description: str
    cli_module: str
    implementation_module: str
    package_module: str
    legacy_module: str
    tags: tuple[str, ...] = ()

    @classmethod
    def from_hypothesis_metadata(
        cls,
        metadata: HypothesisPackageMetadata,
    ) -> RunnerSpec:
        """Build a runner spec from validated metadata-only package metadata."""
        validated = validate_hypothesis_metadata(metadata)
        cli_module = validated.cli_module
        implementation_module = validated.implementation_module
        package_module = validated.package_module
        legacy_module = validated.legacy_module
        required_modules = {
            "cli_module": cli_module,
            "implementation_module": implementation_module,
            "package_module": package_module,
            "legacy_module": legacy_module,
        }
        missing = [name for name, value in required_modules.items() if value is None]
        if missing:
            raise ValueError(
                "RunnerSpec conversion requires module fields: " + ", ".join(missing)
            )
        assert cli_module is not None
        assert implementation_module is not None
        assert package_module is not None
        assert legacy_module is not None
        return cls(
            key=validated.key,
            family=validated.family,
            venue=validated.venue,
            study_id=validated.study_id,
            description=validated.description,
            cli_module=cli_module,
            implementation_module=implementation_module,
            package_module=package_module,
            legacy_module=legacy_module,
            tags=validated.tags,
        )

    def import_cli_module(self) -> ModuleType:
        """Lazily import the CLI entrypoint module."""
        return import_module(self.cli_module)

    def import_implementation_module(self) -> ModuleType:
        """Lazily import the heavy implementation module."""
        return import_module(self.implementation_module)

    def import_package_module(self) -> ModuleType:
        """Lazily import the package ``__init__`` module."""
        return import_module(self.package_module)

    def import_legacy_module(self) -> ModuleType:
        """Lazily import the legacy wrapper module."""
        return import_module(self.legacy_module)


# ---------------------------------------------------------------------------
# Single registered spec — node fills liquidation reconstruction
# ---------------------------------------------------------------------------

_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"

NODE_FILLS_LIQ_RECONSTRUCTION = RunnerSpec(
    key=_NODE_FILLS_KEY,
    family="node_fills_liq_reconstruction",
    venue="hyperliquid",
    study_id=_NODE_FILLS_KEY,
    description=(
        "Hyperliquid node-fills liquidation reconstruction "
        "Phase -1 compatibility runner."
    ),
    cli_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "runners.legacy_cli.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
    ),
    implementation_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
    ),
    package_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hypotheses.hyperliquid.node_fills_liq_reconstruction"
    ),
    legacy_module=(
        "examples.strategies.venue_agnostic_signal_observer."
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
    ),
    tags=(
        "observer_only",
        "hyperliquid",
        "node_fills",
        "liquidation_reconstruction",
        "phase_minus1",
        "compatibility_cli",
    ),
)

HYPERLIQUID_COST_FEASIBILITY = RunnerSpec.from_hypothesis_metadata(
    HYPERLIQUID_COST_FEASIBILITY_METADATA
)

HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0 = RunnerSpec.from_hypothesis_metadata(
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_METADATA
)

# The packaged Phase 0B study uses a dedicated metadata dataclass (no ``phase``
# field, not a ``HypothesisPackageMetadata``), so its spec is built directly from
# the metadata string fields rather than via ``from_hypothesis_metadata``.
GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B = RunnerSpec(
    key=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.key,
    family=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.family,
    venue=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.venue,
    study_id=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.study_id,
    description=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.description,
    cli_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.cli_module,
    implementation_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.implementation_module,
    package_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.package_module,
    legacy_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.legacy_module,
    tags=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_METADATA.tags,
)


# The packaged Phase 0A study uses the same dedicated metadata dataclass style as
# Phase 0B (no ``phase`` field), so its spec is built directly from the metadata
# string fields rather than via ``from_hypothesis_metadata``.
GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A = RunnerSpec(
    key=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.key,
    family=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.family,
    venue=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.venue,
    study_id=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.study_id,
    description=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.description,
    cli_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.cli_module,
    implementation_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.implementation_module,
    package_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.package_module,
    legacy_module=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.legacy_module,
    tags=GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A_METADATA.tags,
)


# ---------------------------------------------------------------------------
# Registry internals
# ---------------------------------------------------------------------------

_REGISTERED_SPECS: tuple[RunnerSpec, ...] = (
    NODE_FILLS_LIQ_RECONSTRUCTION,
    HYPERLIQUID_COST_FEASIBILITY,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B,
    GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0A,
)


def _build_registry_by_key(specs: tuple[RunnerSpec, ...]) -> dict[str, RunnerSpec]:
    by_key: dict[str, RunnerSpec] = {}
    for spec in specs:
        if spec.key in by_key:
            msg = f"duplicate runner registry key: {spec.key}"
            raise ValueError(msg)
        by_key[spec.key] = spec
    return by_key


_REGISTRY_BY_KEY: dict[str, RunnerSpec] = _build_registry_by_key(_REGISTERED_SPECS)


# ---------------------------------------------------------------------------
# Public query helpers
# ---------------------------------------------------------------------------

def iter_runner_specs() -> tuple[RunnerSpec, ...]:
    """Return all registered ``RunnerSpec`` instances in deterministic order."""
    return _REGISTERED_SPECS


def get_runner_spec(key: str) -> RunnerSpec | None:
    """Return the spec for *key*, or ``None`` if not registered."""
    return _REGISTRY_BY_KEY.get(key)


def require_runner_spec(key: str) -> RunnerSpec:
    """Return the spec for *key*, raising ``KeyError`` if missing."""
    spec = _REGISTRY_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"Unknown runner spec: {key!r}")
    return spec
