from __future__ import annotations

from dataclasses import dataclass

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import HypothesisRunner, HypothesisSpec


@dataclass(frozen=True)
class RegisteredRunner:
    name: str
    runner: HypothesisRunner

    @property
    def study_id(self) -> str:
        return self.runner.spec.study_id

    @property
    def spec(self) -> HypothesisSpec:
        return self.runner.spec
