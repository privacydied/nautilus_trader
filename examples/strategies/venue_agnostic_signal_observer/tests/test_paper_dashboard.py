"""Tests for the paper dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..paper.models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
from ..paper.registry import save_strategy


def _make_spec(strategy_id: str = "paper_test") -> PaperStrategySpec:
    return PaperStrategySpec(
        strategy_id=strategy_id,
        signal_family="test_family",
        study_id="test_study",
        precommitment_hash="abc",
        precommitment_path=Path("/tmp/p.json"),
        promotion_rule_id="v0",
        execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
        group_id="g1",
        mean_net_bps=10.0,
        valid_count=50,
        win_rate=0.55,
        cost_floor_bps=50.0,
        min_events=10,
        source_venue=None,
        target_venue=None,
        source_symbol=None,
        target_symbol=None,
        command=("echo",),
        capture_dir=None,
        artifacts_dir=Path("/tmp/a"),
        output_dir=Path("/tmp/o"),
        promoter_verdict="PROMOTED",
        promoted_at_utc="2026-01-01T00:00:00",
        last_refalsified_utc=None,
        refalsification_status=None,
        state=PaperStrategyState.ENABLED,
        metadata={},
    )


class TestDashboard:
    def test_create_app_no_flask(self) -> None:
        """Without Flask, create_app returns None."""
        from ..paper_dashboard.server import _flask_available

        if not _flask_available:
            from ..paper_dashboard.server import create_app

            app = create_app("/tmp")
            assert app is None
        else:
            pytest.skip("Flask is available; this test requires Flask to be absent")

    def test_dashboard_no_nautilus_import(self) -> None:
        """Verify paper_dashboard does NOT import nautilus_trader."""
        import ast
        import sys

        server_path = Path(__file__).parent.parent / "paper_dashboard" / "server.py"
        assert server_path.is_file()
        tree = ast.parse(server_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "nautilus_trader" not in alias.name, (
                        f"Dashboard imports nautilus_trader: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and "nautilus_trader" in node.module:
                    raise AssertionError(
                        f"Dashboard imports nautilus_trader: {node.module}"
                    )

    def test_dashboard_render(self, tmp_path: Path) -> None:
        """Verify dashboard renders with strategies."""
        from ..paper_dashboard.server import _flask_available

        if not _flask_available:
            pytest.skip("Flask not installed")
        from ..paper_dashboard.server import create_app

        spec = _make_spec()
        save_strategy(spec, tmp_path)
        app = create_app(tmp_path)
        assert app is not None

        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "Paper Strategy Dashboard" in resp.text

            resp2 = client.get("/strategy/paper_test")
            assert resp2.status_code == 200
            assert "paper_test" in resp2.text

            resp3 = client.get("/strategy/nonexistent")
            assert resp3.status_code == 404

    def test_dashboard_no_write_back(self, tmp_path: Path) -> None:
        """Dashboard must not write to registry or ledger."""
        from ..paper_dashboard.server import _flask_available

        if not _flask_available:
            pytest.skip("Flask not installed")
        from ..paper_dashboard.server import create_app

        spec = _make_spec()
        save_strategy(spec, tmp_path)
        registry_mtime_before = (tmp_path / "paper_test.json").stat().st_mtime

        app = create_app(tmp_path)
        with app.test_client() as client:
            client.get("/")
            client.get("/strategy/paper_test")

        registry_mtime_after = (tmp_path / "paper_test.json").stat().st_mtime
        assert registry_mtime_after == registry_mtime_before

    def test_disabled_strategy_no_reenable(self, tmp_path: Path) -> None:
        """Verify there is no endpoint to re-enable a disabled strategy."""
        from ..paper_dashboard.server import _flask_available

        if not _flask_available:
            pytest.skip("Flask not installed")
        from ..paper_dashboard.server import create_app

        spec = PaperStrategySpec(
            strategy_id="paper_disabled",
            signal_family="test_family",
            study_id="test_study",
            precommitment_hash="abc",
            precommitment_path=Path("/tmp/p.json"),
            promotion_rule_id="v0",
            execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
            group_id="g1",
            mean_net_bps=10.0,
            valid_count=50,
            win_rate=0.55,
            cost_floor_bps=50.0,
            min_events=10,
            source_venue=None,
            target_venue=None,
            source_symbol=None,
            target_symbol=None,
            command=("echo",),
            capture_dir=None,
            artifacts_dir=Path("/tmp/a"),
            output_dir=Path("/tmp/o"),
            promoter_verdict="PROMOTED",
            promoted_at_utc="2026-01-01T00:00:00",
            last_refalsified_utc=None,
            refalsification_status=None,
            state=PaperStrategyState.DISABLED,
            metadata={},
        )
        save_strategy(spec, tmp_path)

        app = create_app(tmp_path)
        with app.test_client() as client:
            # Verify there's no POST/PUT endpoint for re-enabling
            resp = client.post("/strategy/paper_disabled/enable")
            assert resp.status_code in (404, 405)
            resp = client.put("/strategy/paper_disabled/state")
            assert resp.status_code in (404, 405)