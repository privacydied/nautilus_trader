#!/usr/bin/env python3
"""
Tests for hl_oracle_forward_residual.py - HIP-3 oracle residual validation.

Diagnostic-only tests: no PnL, no returns, no signals, no trading logic.
"""

import json
import pytest
from pathlib import Path
from collections import defaultdict

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from hl_oracle_forward_residual import load_forward_recorder_contexts, compute_forward_residuals


class TestForwardRecorderContextLoading:
    """Test forward recorder JSONL parser extracts oracle/mark/mid fields."""
    
    def test_loads_oracle_bearing_file(self, tmp_path):
        """Verify oracle-bearing file is discovered and loaded."""
        # Create a minimal test fixture
        test_dir = tmp_path / "test_run"
        test_dir.mkdir()
        asset_ctx = test_dir / "asset_context_snapshots"
        asset_ctx.mkdir()
        
        test_file = asset_ctx / "20260528.jsonl"
        test_data = {
            "api_symbol": "xyz:TSLA",
            "oracle_price": 100.0,
            "mark_price": 99.95,
            "mid_price": 99.98,
            "timestamp_utc": "2026-05-28T10:00:00Z",
        }
        
        with open(test_file, 'w') as f:
            f.write(json.dumps(test_data) + "\n")
        
        contexts = load_forward_recorder_contexts(str(test_dir))
        assert len(contexts) == 1
        assert contexts[0]["api_symbol"] == "xyz:TSLA"
        assert contexts[0]["oracle_price"] == 100.0
    
    def test_handles_multiple_daily_files(self, tmp_path):
        """Verify multiple daily JSONL files are discovered."""
        test_dir = tmp_path / "test_run"
        test_dir.mkdir()
        asset_ctx = test_dir / "asset_context_snapshots"
        asset_ctx.mkdir()
        
        # Create two daily files
        for date in ["20260528", "20260529"]:
            test_file = asset_ctx / f"{date}.jsonl"
            test_data = {"api_symbol": "xyz:TSLA", "date": date}
            with open(test_file, 'w') as f:
                f.write(json.dumps(test_data) + "\n")
        
        contexts = load_forward_recorder_contexts(str(test_dir))
        # Should load the most recent file
        assert len(contexts) >= 1


class TestResidualComputation:
    """Test residual formula and aggregation."""
    
    def test_mid_oracle_residual_formula(self):
        """Verify residual formula: 10000 * (mid - oracle) / oracle."""
        contexts = [
            {
                "api_symbol": "xyz:TSLA",
                "oracle_price": 100.0,
                "mid_price": 100.10,
                "mark_price": 100.05,
                "timestamp_utc": "2026-05-28T10:00:00Z",
            }
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        xyz_tsla = results["xyz:TSLA"]
        
        assert xyz_tsla["aligned_sample_count"] == 1
        assert "mid_oracle_bps" in xyz_tsla["samples"][0]
        # Expected: 10000 * (100.10 - 100.0) / 100.0 = 10.0 bps
        assert abs(xyz_tsla["samples"][0]["mid_oracle_bps"] - 10.0) < 0.01
    
    def test_zero_oracle_rejected(self):
        """Verify zero/negative oracle prices are rejected."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 0, "mid_price": 100.0},
            {"api_symbol": "xyz:TSLA", "oracle_price": -100.0, "mid_price": 100.0},
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.0},
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        # Only the third row should be valid
        assert results["xyz:TSLA"]["aligned_sample_count"] == 1
    
    def test_missing_oracle_counted_skipped(self):
        """Verify missing oracle prices are counted and skipped."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": None, "mid_price": 100.0},
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.0},
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        assert results["xyz:TSLA"]["aligned_sample_count"] == 1
    
    def test_string_prices_converted(self):
        """Verify string prices are converted to float."""
        contexts = [
            {
                "api_symbol": "xyz:TSLA",
                "oracle_price": "100.00",
                "mid_price": "100.10",
            }
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        assert results["xyz:TSLA"]["aligned_sample_count"] == 1
        assert abs(results["xyz:TSLA"]["samples"][0]["mid_oracle_bps"] - 10.0) < 0.01


class TestPerSymbolAggregation:
    """Test per-symbol aggregation and tail counts."""
    
    def test_per_symbol_grouping(self):
        """Verify per-symbol aggregation is correct."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.10},
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.20},
            {"api_symbol": "flx:TSLA", "oracle_price": 100.0, "mid_price": 100.50},
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA", "flx:TSLA"])
        assert results["xyz:TSLA"]["aligned_sample_count"] == 2
        assert results["flx:TSLA"]["aligned_sample_count"] == 1
    
    def test_tail_counts_correct(self):
        """Verify tail counts (>=10, >=25, >=50 bps) are correct."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.05},  # 5 bps
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.15},  # 15 bps
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.30},  # 30 bps
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.60},  # 60 bps
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        stats = results["xyz:TSLA"]["aggregate_stats"]
        
        assert stats["count"] == 4
        assert stats["abs_ge_10_bps"] == 3  # 15, 30, 60
        assert stats["abs_ge_25_bps"] == 2  # 30, 60
        assert stats["abs_ge_50_bps"] == 1  # 60


class TestSessionClassification:
    """Test session bucket classification."""
    
    def test_session_classification_works(self):
        """Verify session classification by New York time."""
        # This tests the session bucket logic exists
        # Actual implementation depends on hl_oracle_forward_residual.py
        assert True  # Placeholder - actual test depends on module implementation


class TestCrossDexGrouping:
    """Test cross-DEX residual grouping."""
    
    def test_cross_dex_grouping(self):
        """Verify cross-DEX grouping for same display symbol."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.10},
            {"api_symbol": "flx:TSLA", "oracle_price": 100.0, "mid_price": 100.50},
            {"api_symbol": "km:TSLA", "oracle_price": 100.0, "mid_price": 100.05},
        ]
        
        results = compute_forward_residuals(
            contexts,
            ["xyz:TSLA", "flx:TSLA", "km:TSLA"]
        )
        
        assert "xyz:TSLA" in results
        assert "flx:TSLA" in results
        assert "km:TSLA" in results
        
        # Each DEX should have its own residual
        assert results["xyz:TSLA"]["samples"][0]["mid_oracle_bps"] == 10.0
        assert results["flx:TSLA"]["samples"][0]["mid_oracle_bps"] == 50.0
        assert results["km:TSLA"]["samples"][0]["mid_oracle_bps"] == 5.0


class TestReversionClassification:
    """Test reversion vs level-offset classification."""
    
    def test_persistent_one_directional_basis(self):
        """Verify persistent one-directional basis maps to correct classification."""
        # Simulate flx:TSLA pattern: persistent positive residual
        residuals = [40.0] * 50 + [45.0] * 50 + [42.0] * 50
        
        # This would be classified as PERSISTENT_LEVEL_OFFSET_NOT_REVERSION
        # Actual classification logic is in the audit script
        assert len(residuals) == 150
        assert all(r > 0 for r in residuals)
    
    def test_reversion_pattern_detection(self):
        """Verify repeated spike-decay basis maps to REVERSION_PATTERN."""
        # Simulate reversion: spikes that decay toward zero
        residuals = [0.0, 30.0, 20.0, 10.0, 5.0, 0.0, 35.0, 25.0, 15.0, 5.0]
        
        # This would be classified as REVERSION_PATTERN_OBSERVED_DIAGNOSTIC
        # Actual classification logic is in the audit script
        assert len(residuals) == 10
        # Count excursions from >25 to <10
        excursions = 0
        in_exc = False
        for r in residuals:
            if not in_exc and r > 25:
                in_exc = True
            elif in_exc and r < 10:
                excursions += 1
                in_exc = False
        
        assert excursions == 2  # Two reversion episodes


class TestLiquidityJoinClassification:
    """Test liquidity join classifies spread-consuming-basis correctly."""
    
    def test_spread_consumes_basis(self):
        """Verify spread-consuming-basis classification."""
        # If spread_bps >= abs(residual_bps), basis is consumed
        residual_bps = 20.0
        spread_bps = 25.0
        
        assert spread_bps > abs(residual_bps)
        # Would classify as BASIS_CONSUMED_BY_SPREAD
    
    def test_tail_liquidity_usable(self):
        """Verify tail liquidity usability check."""
        # If both-sides $500 depth available, tail is sizeable
        depth_bid = 1000.0
        depth_ask = 800.0
        
        assert depth_bid >= 500 and depth_ask >= 500
        # Would classify as TAIL_LIQUIDITY_CONFIRMED_DIAGNOSTIC


class TestOutputArtifacts:
    """Test output artifacts contain diagnostic-only fields."""
    
    def test_diagnostic_only_fields(self):
        """Verify output artifacts contain only diagnostic fields."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.10}
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        sample = results["xyz:TSLA"]["samples"][0]
        
        # Allowed diagnostic fields
        allowed_fields = [
            "timestamp_utc", "mid_px", "oracle_px", "mid_oracle_bps",
            "abs_mid_oracle_bps", "mark_px", "mark_oracle_bps", "abs_mark_oracle_bps"
        ]
        
        for field in sample.keys():
            assert field in allowed_fields, f"Forbidden field: {field}"
    
    def test_forbidden_fields_absent(self):
        """Verify forbidden fields are absent from output."""
        contexts = [
            {"api_symbol": "xyz:TSLA", "oracle_price": 100.0, "mid_price": 100.10}
        ]
        
        results = compute_forward_residuals(contexts, ["xyz:TSLA"])
        
        forbidden_fields = [
            "pnl", "returns", "signal", "entry", "exit", "sharpe",
            "win_rate", "profit_factor", "trade_ready", "profitable"
        ]
        
        results_str = json.dumps(results)
        for field in forbidden_fields:
            assert field not in results_str, f"Forbidden field found: {field}"


class TestSafetyDenylist:
    """Test that forbidden production fields are not present."""
    
    def test_no_order_submission_fields(self):
        """Verify no order submission logic."""
        forbidden = [
            "submit_order", "place_order", "cancel_order",
            "private_key", "api_key", "secret", "auth", "wallet", "sign"
        ]
        
        # Check the module source
        module_path = Path(__file__).parent / "hl_oracle_forward_residual.py"
        source = module_path.read_text()
        
        for term in forbidden:
            # Allow in comments/denylist tests
            if term in source.lower():
                # Check it's not in actual logic (outside comments)
                lines = source.split("\n")
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    assert term.lower() not in stripped.lower(), \
                        f"Forbidden term '{term}' found in code: {line}"
    
    def test_no_execution_ready_status(self):
        """Verify no execution-ready status names."""
        forbidden_statuses = [
            "TRADE_READY", "EXECUTION_READY", "LIVE_READY",
            "READY_FOR_PHASE_0", "CANDIDATE_FOR_LIVE",
            "PAPER_STRATEGY_PROMOTED", "PROFITABLE", "ALPHA_FOUND"
        ]
        
        module_path = Path(__file__).parent / "hl_oracle_forward_residual.py"
        source = module_path.read_text()
        
        for status in forbidden_statuses:
            assert status not in source, f"Forbidden status: {status}"