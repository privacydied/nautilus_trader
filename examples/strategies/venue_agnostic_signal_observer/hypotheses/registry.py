from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import HypothesisSpec

_HYPOTHESIS_REGISTRY: dict[str, HypothesisSpec] = {}


def register_hypothesis(spec: HypothesisSpec) -> None:
    if spec.study_id in _HYPOTHESIS_REGISTRY:
        raise ValueError(f"Duplicate study_id: {spec.study_id}")
    _HYPOTHESIS_REGISTRY[spec.study_id] = spec


def get_hypothesis(study_id: str) -> HypothesisSpec:
    try:
        return _HYPOTHESIS_REGISTRY[study_id]
    except KeyError as exc:
        raise KeyError(f"Unknown hypothesis study_id: {study_id}") from exc


def list_hypotheses() -> list[HypothesisSpec]:
    return [_HYPOTHESIS_REGISTRY[key] for key in sorted(_HYPOTHESIS_REGISTRY)]


def clear_hypothesis_registry_for_tests() -> None:
    _HYPOTHESIS_REGISTRY.clear()
