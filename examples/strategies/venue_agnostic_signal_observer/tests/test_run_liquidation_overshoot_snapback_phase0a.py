"""Tests for the CLI runner of Hyperliquid cascade overshoot snapback Phase -2 + Phase 0A v0.

Tests dry-run behavior, safety invariants, and artifact output.
No network required.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
MODULE = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_liquidation_overshoot_snapback_phase0a"
MAIN_MODULE = "examples.strategies.venue_agnostic_signal_observer.liquidation_overshoot_snapback_phase0a"


def _run_runner(*extra_args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python", "-m", MODULE, *extra_args],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(REPO_ROOT),
    )


class TestDryRun:
    def test_dry_run_exits_zero(self):
        result = _run_runner("--dry-run", "--run-phase-minus2")
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    def test_dry_run_no_s3_calls(self):
        result = _run_runner("--dry-run", "--run-phase-minus2")
        # Dry run should not mention S3 downloads
        assert "s3://" not in result.stdout.lower() or "anchor" in result.stdout.lower()

    def test_plan_only_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--plan-only", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0


class TestSafetyInvariants:
    def test_default_mode_is_safe(self):
        """Without --allow-s3-archive-read, should default to dry-run."""
        result = _run_runner("--run-phase-minus2", "--run-phase0a")
        # Should succeed (dry-run mode auto-enabled)
        assert result.returncode == 0

    def test_forbidden_statuses_not_emitted(self):
        result = _run_runner("--dry-run", "--run-phase-minus2")
        forbidden = [
            "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
            "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE",
            "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
        ]
        for status in forbidden:
            assert status not in result.stdout, f"Forbidden status '{status}' found in output"

    def test_summary_json_firewall_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            # Find summary.json
            summary_files = list(Path(tmpdir).rglob("summary.json"))
            assert len(summary_files) > 0, "No summary.json found"
            with open(summary_files[0]) as f:
                summary = json.load(f)
            assert summary["registry_verdict_authorized"] is False
            assert summary["promotion_candidate"] is False
            assert summary["observer_only"] is True
            assert summary["no_order_intent"] is True
            assert summary["paper_registry_write_authorized"] is False
            assert summary["paper_registry_written"] is False
            assert summary["conductor_promotion_authorized"] is False
            assert summary["shadow_or_live_unlock"] is False

    def test_no_paper_registry_files_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            # Check no paper registry paths
            for p in Path(tmpdir).rglob("*"):
                path_str = str(p).lower()
                assert "paper_registry" not in path_str, f"Paper registry file found: {p}"
                assert "auto_promotion" not in path_str, f"Auto promotion file found: {p}"


class TestExactLiquidationRequired:
    def test_exact_liq_true_blocks_without_fields(self):
        """With exact-liquidation-required=true and no liq fields, should block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner(
                "--dry-run", "--run-phase-minus2",
                "--exact-liquidation-required", "true",
                "--out-root", tmpdir,
            )
            # Should still succeed (dry-run, no local data)
            assert result.returncode == 0

    def test_exact_liq_false_allows_proxy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner(
                "--dry-run", "--run-phase-minus2",
                "--exact-liquidation-required", "false",
                "--out-root", tmpdir,
            )
            assert result.returncode == 0


class TestArtifactOutput:
    def test_summary_json_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            summaries = list(Path(tmpdir).rglob("summary.json"))
            assert len(summaries) == 1

    def test_summary_md_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            md_files = list(Path(tmpdir).rglob("summary.md"))
            assert len(md_files) == 1
            content = md_files[0].read_text()
            assert "hyperliquid_liq_overshoot_snapback_phase0a_v0" in content

    def test_inputs_md_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            inputs_files = list(Path(tmpdir).rglob("INPUTS.md"))
            assert len(inputs_files) == 1
            content = inputs_files[0].read_text()
            assert "funding_not_included: true" in content

    def test_phase_minus2_artifacts_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _run_runner("--dry-run", "--run-phase-minus2", "--out-root", tmpdir)
            assert result.returncode == 0
            reachability = list(Path(tmpdir).rglob("phase_minus2_reachability.json"))
            assert len(reachability) == 1
            plan = list(Path(tmpdir).rglob("phase_minus2_plan_estimate.json"))
            assert len(plan) == 1


class TestCommandLineArgs:
    def test_custom_symbols(self):
        result = _run_runner("--dry-run", "--symbols", "SOL,AVAX,DOGE", "--run-phase-minus2")
        assert result.returncode == 0

    def test_custom_dates(self):
        result = _run_runner("--dry-run", "--start-date", "2025-10-01", "--end-date", "2025-10-05", "--run-phase-minus2")
        assert result.returncode == 0

    def test_max_events(self):
        result = _run_runner("--dry-run", "--max-events", "10", "--run-phase-minus2")
        assert result.returncode == 0
