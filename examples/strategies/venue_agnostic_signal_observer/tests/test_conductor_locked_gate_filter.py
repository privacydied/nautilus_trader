"""Tests for the locked-gate filter."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..conductor.locked_gate_filter import check_locked_gate
from ..conductor.models import ConductorJobSpec, ConductorRunMode, ConductorSourceKind


_LOCKED_GATES_FIXTURE = """\
# Other section

## Locked Gates — Do Not Revisit Without Structural Change

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.

2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH). HFT-dominated, sub-second decay. Rejected.

6. **Derivatives flow impulse → spot lead-lag** (Binance USD-M perp → Kraken/Coinbase spot). REJECTED after full-empirical capture (May 15 FULL_ACTIVE). Best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable groups at any cost level. 0 recurring groups across two separate captures. Closed under this execution stack. Do not revisit without materially different execution assumptions: HFT-grade colocated execution, much lower fees, different venue microstructure, or a genuinely different signal family. Do not reopen by merely changing lookbacks, horizons, or adding OI filters.

9. **Family 2 funding crowding reversal** (Binance BTCUSDT funding extremes → spot BTC forward returns). 0/60 cells survived the frozen design. Signal absent at 50 bps cost; 6 bps diagnostic also shows no consistent edge. Do not revisit without a materially different signal definition (tick-level funding, cross-exchange, multi-asset, or funding+OI conditioning). Do not reopen by merely changing thresholds, horizons, windows, or null iterations.

## Still Open

- Some open item.
"""


@pytest.fixture
def registry_with_gates(tmp_path: Path) -> Path:
    p = tmp_path / "REJECTED_RESEARCH.md"
    p.write_text(_LOCKED_GATES_FIXTURE)
    return p


def _make_job(
    signal_family: str = "flow_impulse",
    study_id: str = "lead_lag_v6",
    metadata: dict | None = None,
    structural_change_rationale: str | None = None,
) -> ConductorJobSpec:
    if metadata is None:
        metadata = {}
    return ConductorJobSpec(
        job_id="test_job",
        source_kind=ConductorSourceKind.EXISTING_CAPTURE,
        signal_family=signal_family,
        study_id=study_id,
        command=("python", "-c", "pass"),
        capture_dir=None,
        report_dir=None,
        output_dir="/tmp/conductor/exploration/test",
        run_mode=ConductorRunMode.EXPLORATION,
        min_events=50,
        cost_floor_bps=50.0,
        structural_change_rationale=structural_change_rationale,
        requested_devices=(),
        metadata=metadata,
    )


class TestLockedGateFilter:
    def test_matching_job_blocked(self, registry_with_gates: Path) -> None:
        job = _make_job(
            signal_family="flow_impulse",
            metadata={
                "source_venue": "binance_perp",
                "target_venue": "kraken",
            },
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert not decision.allowed
        assert "LOCKED_GATE_OVERLAP_NO_STRUCTURAL_RATIONALE" in decision.reason

    def test_matching_job_allowed_with_rationale(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="flow_impulse",
            metadata={
                "source_venue": "binance_perp",
                "target_venue": "kraken",
            },
            structural_change_rationale="New colocated execution setup",
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert decision.allowed
        assert decision.metadata.get("locked_gate_overlap_acknowledged") is True

    def test_matched_gate_numbers_recorded(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="flow_impulse",
            metadata={
                "source_venue": "binance_perp",
                "target_venue": "kraken",
            },
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert not decision.allowed
        assert 6 in decision.matched_gate_numbers

    def test_non_overlapping_job_allowed(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="cross_asset_beta_lag",
            study_id="v1",
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert decision.allowed

    def test_partial_family_match_requires_two_field_conjunction(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="family_2",
            study_id="v1",
            metadata={"irrelevant_field": "binance"},
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert decision.allowed

    def test_short_symbol_not_broad_blocking(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="btc_signal",
            study_id="study",
            metadata={"source_symbol": "BTC"},
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert decision.allowed

    def test_long_token_substring_match(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="funding_crowding_reversal",
            study_id="study_v1",
            metadata={"source_symbol": "BTCUSDT"},
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert not decision.allowed

    def test_empty_rationale_counts_as_missing(
        self, registry_with_gates: Path
    ) -> None:
        job = _make_job(
            signal_family="flow_impulse",
            metadata={
                "source_venue": "binance_perp",
                "target_venue": "kraken",
            },
            structural_change_rationale="",
        )
        decision = check_locked_gate(registry_with_gates, job)
        assert not decision.allowed
        assert "LOCKED_GATE_OVERLAP_NO_STRUCTURAL_RATIONALE" in decision.reason