"""
Tests for Phase 3 Edge Miner.

Covers:
- FrozenGrid: hash identity, immutability
- GridCell: blocked_by_locked_rejection flag
- RejectionGuard: locked-rejection detection, structural-change rationale
- run_sweep: active/blocked cell handling, no validation/promotion
- Miner does not import live execution clients
- Miner does not produce TRADE_READY
"""

from __future__ import annotations

import pytest

from ..miner import (
    FrozenGrid,
    GridCell,
    GridSpec,
    build_grid,
    run_sweep,
    check_rejection_guard,
)


def _make_cell(cell_id, signal_type="neutral_signal", blocked=False, rationale=None):
    return GridCell(
        cell_id=cell_id,
        source_venue="binance",
        source_instrument="BTC-USDT-PERP",
        target_venue="kraken",
        target_instrument="BTC-USD",
        entry_delay_seconds=5.0,
        forward_horizon_seconds=180.0,
        signal_type=signal_type,
        blocked_by_locked_rejection=blocked,
        structural_change_rationale=rationale,
    )


class TestFrozenGrid:
    def test_build_grid_produces_hash(self):
        spec = GridSpec(name="test", version="1.0", cells=[_make_cell("c1")])
        grid = build_grid(spec)
        assert grid.grid_hash
        assert len(grid.grid_hash) == 24

    def test_same_spec_same_hash(self):
        spec_a = GridSpec(name="test", version="1.0", cells=[_make_cell("c1")])
        spec_b = GridSpec(name="test", version="1.0", cells=[_make_cell("c1")])
        assert build_grid(spec_a).grid_hash == build_grid(spec_b).grid_hash

    def test_different_spec_different_hash(self):
        spec_a = GridSpec(name="test", version="1.0", cells=[_make_cell("c1")])
        spec_b = GridSpec(name="test", version="1.0", cells=[_make_cell("c2")])
        assert build_grid(spec_a).grid_hash != build_grid(spec_b).grid_hash

    def test_active_cells_excludes_blocked(self):
        spec = GridSpec(name="t", version="1.0", cells=[
            _make_cell("c1", blocked=False),
            _make_cell("c2", blocked=True),
        ])
        grid = build_grid(spec)
        active = grid.active_cells()
        assert len(active) == 1
        assert active[0].cell_id == "c1"


class TestRejectionGuard:
    def test_clean_grid_passes_guard(self):
        spec = GridSpec(name="t", version="1.0", cells=[_make_cell("c1", "novel_cross_asset_beta")])
        grid = build_grid(spec)
        result = check_rejection_guard(grid)
        assert result.guard_passed
        assert result.n_cells_blocked == 0

    def test_locked_rejection_key_blocks_cell(self):
        spec = GridSpec(name="t", version="1.0", cells=[
            _make_cell("c1", signal_type="same_asset_cross_venue_tick_lead_lag"),
        ])
        grid = build_grid(spec)
        result = check_rejection_guard(grid)
        assert not result.guard_passed
        assert result.n_cells_blocked == 1
        assert result.blocked_cells[0].reason == "overlaps_locked_rejection_no_rationale"

    def test_structural_change_rationale_passes_guard(self):
        spec = GridSpec(
            name="t",
            version="1.0",
            cells=[
                _make_cell(
                    "c1",
                    signal_type="same_asset_cross_venue_tick_lead_lag",
                    rationale="different_venue_instrument_class: using options vs spot",
                ),
            ],
        )
        grid = build_grid(spec)
        result = check_rejection_guard(grid)
        assert result.n_cells_active == 1

    def test_explicitly_blocked_cell_always_blocked(self):
        spec = GridSpec(name="t", version="1.0", cells=[_make_cell("c1", blocked=True)])
        grid = build_grid(spec)
        result = check_rejection_guard(grid)
        assert result.n_cells_blocked == 1

    def test_additional_locked_refs(self):
        spec = GridSpec(name="t", version="1.0", cells=[
            _make_cell("c1", signal_type="my_custom_rejected_hypothesis"),
        ])
        grid = build_grid(spec)
        result = check_rejection_guard(grid, additional_locked_refs=["my_custom_rejected_hypothesis"])
        assert result.n_cells_blocked == 1


class TestSweep:
    def _make_grid(self, cells=None):
        if cells is None:
            cells = [_make_cell("c1"), _make_cell("c2")]
        spec = GridSpec(name="t", version="1.0", cells=cells)
        return build_grid(spec)

    def test_sweep_returns_per_cell_results(self):
        import random
        rng = random.Random(42)
        grid = self._make_grid()

        def obs_fn(cell):
            return [rng.gauss(0.001, 0.0005) for _ in range(50)]

        result = run_sweep(grid, obs_fn)
        assert len(result.cell_results) == 2
        assert result.grid_hash == grid.grid_hash

    def test_sweep_skips_blocked_cells(self):
        grid = self._make_grid(cells=[
            _make_cell("c1"),
            _make_cell("c2", blocked=True),
        ])

        def obs_fn(cell):
            return [0.001] * 10

        result = run_sweep(grid, obs_fn)
        active = [r for r in result.cell_results if not r.skipped]
        blocked = [r for r in result.cell_results if r.skipped]
        assert len(active) == 1
        assert len(blocked) == 1
        assert blocked[0].cell_id == "c2"

    def test_sweep_does_not_produce_trade_ready(self):
        grid = self._make_grid()

        def obs_fn(cell):
            return [1.0] * 200  # absurdly good

        result = run_sweep(grid, obs_fn)
        d = result.to_dict()
        assert "TRADE_READY" not in str(d)

    def test_sweep_does_not_import_execution_clients(self):
        import inspect
        from ..miner import sweep as sweep_mod
        src = inspect.getsource(sweep_mod)
        forbidden = ["NautilusTrader", "nautilus_trader", "ccxt", "private_key", "api_key"]
        for f in forbidden:
            assert f not in src, f"sweep.py must not reference {f}"

    def test_sweep_handles_observation_fn_error(self):
        grid = self._make_grid()

        def obs_fn(cell):
            if cell.cell_id == "c1":
                raise RuntimeError("simulated data unavailable")
            return [0.001] * 10

        result = run_sweep(grid, obs_fn)
        c1 = next(r for r in result.cell_results if r.cell_id == "c1")
        assert c1.skipped
        assert "observation_fn_error" in c1.skip_reason
