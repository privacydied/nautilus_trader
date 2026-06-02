from __future__ import annotations

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import HypothesisRunner
from examples.strategies.venue_agnostic_signal_observer.runners.base import RegisteredRunner

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
