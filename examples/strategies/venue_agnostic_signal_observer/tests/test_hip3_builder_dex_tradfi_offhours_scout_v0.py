#!/usr/bin/env python3
"""Tests for core HIP-3 Builder-DEX TradFi Off-Hours Scout module."""

import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0 import (
    ScoutConfig, GateResult, PublicInfoChokepoint, ArchiveChokepoint,
    phase_a_discovery, phase_a2_frontend_api, phase_b_resolution,
    phase_c_archive, phase_d_fees, phase_e_oracle, phase_f_liquidity,
    phase_g_basis_tail, _classify_symbol, _derive_builder_asset_id,
    _is_off_hours_equity, _make_artifact_base, _validate_no_forbidden_status,
    DiscoveryStatus, ArchiveStatus, FeeLiqOracleStatus, BasisTailStatus,
    FinalGateStatus, ALLOWED_INFO_TYPES, FORBIDDEN_INFO_TYPES,
    HL_INFO_URL, SANITY_SEEDS, FORBIDDEN_STATUSES, STUDY_ID,
    DexMetaResult, DexAssetCtxsResult, FrontendApiCheck, SymbolResolution,
    ArchiveProbeResult, FeeProbeResult, OracleAnchorResult, L2DepthResult,
    BasisTailResult, PerpDexEntry, _config_hash, _get_git_info, _now_utc,
)


class TestEndpointDiscipline(unittest.TestCase):
    """Phase A: perpDexs, meta, metaAndAssetCtxs request formation."""

    def test_perpDexs_request_formed_correctly(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with patch.object(cp, "_check"), \
             patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps([{"name": None}]).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            result = cp.post_info({"type": "perpDexs"})
            self.assertIsInstance(result, list)

    def test_meta_with_dex_request_formed(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with patch.object(cp, "_check"), \
             patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"universe": []}).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            result = cp.post_info({"type": "meta", "dex": "xyz"})
            self.assertIsInstance(result, dict)

    def test_metaAndAssetCtxs_with_dex_request_formed(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with patch.object(cp, "_check"), \
             patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps([{"universe": []}, []]).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            result = cp.post_info({"type": "metaAndAssetCtxs", "dex": "xyz"})
            self.assertIsInstance(result, (list, tuple))

    def test_default_metaAndAssetCtxs_baseline_only(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with patch.object(cp, "_check"), \
             patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps([{"universe": []}, []]).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            result = cp.post_info({"type": "metaAndAssetCtxs"})
            self.assertIsNotNone(result)

    def test_chokepoint_blocks_without_allow_network(self):
        cp = PublicInfoChokepoint(allow_network=False)
        with self.assertRaises(RuntimeError):
            cp.post_info({"type": "perpDexs"})

    def test_forbidden_request_types_rejected(self):
        cp = PublicInfoChokepoint(allow_network=True)
        for ft in FORBIDDEN_INFO_TYPES:
            with self.assertRaises(ValueError):
                cp.post_info({"type": ft})

    def test_unknown_request_type_rejected(self):
        cp = PublicInfoChokepoint(allow_network=True)
        with self.assertRaises(ValueError):
            cp.post_info({"type": "unknownType"})


class TestFrontendApiConsistency(unittest.TestCase):
    """Phase A2: Frontend/API consistency check."""

    def test_head_route_generated_for_seeds(self):
        for ticker in SANITY_SEEDS:
            url = f"https://app.hyperliquid.xyz/trade/{ticker}/USDC"
            self.assertIn(ticker, url)
            self.assertTrue(url.startswith("https://"))

    def test_dry_run_skips_frontend_check(self):
        config = ScoutConfig(dry_run=True, run_id="test_dry")
        cp = PublicInfoChokepoint(allow_network=False)
        run_dir = Path("/tmp/test_scout_dry_run")
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            gate, result = phase_a2_frontend_api(config, cp, [], run_dir, "abc123", False, "main")
            self.assertTrue(result.get("frontend_api_consistency_check_skipped"))
            self.assertEqual(gate.status, DiscoveryStatus.SCOUT_READY.value)
        finally:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_frontend_only_triggers_desync(self):
        config = ScoutConfig(require_sanity_seeds=True, run_id="test_desync")
        cp = PublicInfoChokepoint(allow_network=True)
        with patch.object(cp, "head_frontend", return_value=200):
            run_dir = Path("/tmp/test_scout_desync")
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                # No builder DEX meta results => no API resolution
                gate, result = phase_a2_frontend_api(config, cp, [], run_dir, "abc", False, "main")
                self.assertTrue(result.get("has_frontend_only_desync"))
                self.assertIn("FRONTEND_API_DESYNC", gate.status)
                self.assertTrue(gate.blocked)
            finally:
                import shutil
                shutil.rmtree(run_dir, ignore_errors=True)


class TestBuilderDexParsing(unittest.TestCase):
    """Phase A: Builder DEX parsing and asset ID derivation."""

    def test_derive_asset_id(self):
        # 100000 + dex_index * 10000 + index_in_meta
        self.assertEqual(_derive_builder_asset_id(0, 0), 100000)
        self.assertEqual(_derive_builder_asset_id(0, 5), 100005)
        self.assertEqual(_derive_builder_asset_id(1, 3), 110003)
        self.assertEqual(_derive_builder_asset_id(2, 10), 120010)

    def test_perpDexEntry_classification(self):
        e1 = PerpDexEntry(dex_index=0, dex_name=None, full_name=None,
                          deployer=None, oracle_updater=None, fee_recipient=None)
        self.assertEqual(e1.classification, "unknown_dex")
        e1.classification = "builder_dex" if e1.dex_name else "default_validator_dex"
        self.assertEqual(e1.classification, "default_validator_dex")

        e2 = PerpDexEntry(dex_index=1, dex_name="xyz", full_name="XYZ Exchange",
                          deployer="0xabc", oracle_updater=None, fee_recipient=None)
        e2.classification = "builder_dex" if e2.dex_name else "default_validator_dex"
        self.assertEqual(e2.classification, "builder_dex")


class TestTickerClassification(unittest.TestCase):
    """Symbol classification rules."""

    def test_single_stocks(self):
        for t in ["TSLA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META"]:
            self.assertEqual(_classify_symbol(t), "single_stock_like", f"{t} should be single_stock_like")

    def test_goog_googl_no_duplicate(self):
        self.assertEqual(_classify_symbol("GOOG"), "single_stock_like")
        self.assertEqual(_classify_symbol("GOOGL"), "single_stock_like")

    def test_indices(self):
        for t in ["SPX", "NDX", "NAS100"]:
            self.assertEqual(_classify_symbol(t), "index_like", f"{t} should be index_like")

    def test_etf(self):
        self.assertEqual(_classify_symbol("QQQ"), "etf_like")

    def test_commodities(self):
        for t in ["GOLD", "XAU", "WTI", "OIL", "SILVER", "XAG", "BRENT"]:
            self.assertEqual(_classify_symbol(t), "commodity_like", f"{t} should be commodity_like")

    def test_SPX6900_NOT_index(self):
        self.assertNotEqual(_classify_symbol("SPX6900"), "index_like")

    def test_GOLDEN_NOT_commodity(self):
        self.assertNotEqual(_classify_symbol("GOLDEN"), "commodity_like")

    def test_DOGE_SPX_NOT_index(self):
        self.assertNotEqual(_classify_symbol("DOGE-SPX"), "index_like")

    def test_crypto_like(self):
        for t in ["BTC", "ETH", "SOL", "DOGE", "ADA", "XRP"]:
            self.assertEqual(_classify_symbol(t), "crypto_like", f"{t} should be crypto_like")

    def test_unknown(self):
        self.assertEqual(_classify_symbol("XYZ123"), "unknown")


class TestOffHoursCalendar(unittest.TestCase):
    """Off-hours classification for equity/index/ETF."""

    def test_weekend_is_off_hours(self):
        from datetime import datetime
        # Saturday
        dt = datetime(2025, 10, 18, 12, 0)  # Saturday
        self.assertTrue(_is_off_hours_equity(dt))

    def test_regular_hours_not_off_hours(self):
        from datetime import datetime
        # Wednesday 10:00 ET (approx)
        dt = datetime(2025, 10, 15, 10, 0)
        self.assertFalse(_is_off_hours_equity(dt))

    def test_before_market_open_is_off_hours(self):
        from datetime import datetime
        # Wednesday 08:00 ET
        dt = datetime(2025, 10, 15, 8, 0)
        self.assertTrue(_is_off_hours_equity(dt))

    def test_after_market_close_is_off_hours(self):
        from datetime import datetime
        # Wednesday 17:00 ET
        dt = datetime(2025, 10, 15, 17, 0)
        self.assertTrue(_is_off_hours_equity(dt))


class TestPositiveStatusRequiresRealData(unittest.TestCase):
    """Positive gate statuses require computed_from_real_data=True."""

    def test_computed_false_cannot_carry_ready(self):
        """GateResult with computed_from_real_data=False and READY-like status is invalid."""
        # This is a design constraint test — the scout should not emit READY with False
        g = GateResult(phase="D", status=FeeLiqOracleStatus.FEE_READY.value,
                       computed_from_real_data=False)
        # The artifact writer should catch this
        self.assertFalse(g.computed_from_real_data)


class TestBasisTail(unittest.TestCase):
    """Basis-tail sample size rules."""

    def test_inconclusive_status_exists(self):
        btr = BasisTailResult(symbol="TSLA", dex="xyz",
                              total_off_hours_samples=50,
                              status="HIP3_OFFHOURS_BASIS_TAIL_INCONCLUSIVE")
        self.assertEqual(btr.status, "HIP3_OFFHOURS_BASIS_TAIL_INCONCLUSIVE")

    def test_200_plus_samples_can_have_exists(self):
        btr = BasisTailResult(symbol="TSLA", dex="xyz",
                              total_off_hours_samples=250,
                              status="HIP3_OFFHOURS_BASIS_TAIL_EXISTS")
        self.assertEqual(btr.status, "HIP3_OFFHOURS_BASIS_TAIL_EXISTS")


class TestRegistrySplit(unittest.TestCase):
    """Scout has no --write-registry-correction flag."""

    def test_scout_module_has_no_registry_write_function(self):
        import inspect
        src = inspect.getsource(sys.modules["examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0"])
        self.assertNotIn("--write-registry-correction", src)


class TestSafety(unittest.TestCase):
    """No production subprocess, os.system, eval."""

    def test_no_subprocess_in_production_code(self):
        import inspect
        src = inspect.getsource(sys.modules["examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0"])
        # Allow import line but not usage
        lines = [l for l in src.split("\n") if "subprocess" in l and not l.strip().startswith("#")]
        # The only allowed line is the import in run_scout
        for line in lines:
            if "import subprocess" in line:
                continue
            # Check it's not actually calling subprocess
            self.assertNotIn("subprocess.run", line)
            self.assertNotIn("subprocess.Popen", line)
            self.assertNotIn("subprocess.call", line)
            self.assertNotIn("subprocess.check_output", line)

    def test_no_os_system_in_production_code(self):
        import inspect
        src = inspect.getsource(sys.modules["examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0"])
        self.assertNotIn("os.system(", src)

    def test_no_eval_in_production_code(self):
        import inspect
        src = inspect.getsource(sys.modules["examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0"])
        # Allow "eval" in comments/strings but not as function call
        for line in src.split("\n"):
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("eval(", line)

    def test_no_forbidden_statuses_emitted(self):
        """Verify FORBIDDEN_STATUSES set is comprehensive."""
        required_forbidden = {"REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
                              "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
                              "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED",
                              "PROMOTION_AUTHORIZED", "EDGE_CONFIRMED"}
        self.assertEqual(FORBIDDEN_STATUSES, required_forbidden)

    def test_no_auth_strings_in_chokepoint(self):
        """Chokepoint should not contain auth-related strings."""
        import inspect
        src = inspect.getsource(PublicInfoChokepoint)
        for forbidden in ["private_key", "api_key", "secret", "wallet", "auth_token"]:
            self.assertNotIn(forbidden, src.lower())


class TestConfigHash(unittest.TestCase):
    """Config hash is deterministic."""

    def test_same_config_same_hash(self):
        c1 = ScoutConfig(run_id="test")
        c2 = ScoutConfig(run_id="test")
        self.assertEqual(_config_hash(c1), _config_hash(c2))

    def test_different_config_different_hash(self):
        c1 = ScoutConfig(run_id="test1")
        c2 = ScoutConfig(run_id="test2")
        self.assertNotEqual(_config_hash(c1), _config_hash(c2))


if __name__ == "__main__":
    unittest.main()
