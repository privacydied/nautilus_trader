#!/usr/bin/env python3
"""Tests for V6-B funding/basis scanner."""
import pytest
import json
import tempfile
from pathlib import Path

from examples.strategies.kraken_market_structure_scanner.funding_config import (
    FundingConfig, SYMBOL_MAP, QUOTE_MAP, FUNDING_INTERVAL,
)
from examples.strategies.kraken_market_structure_scanner.funding_models import (
    PriceLevel, FundingObservation,
)
from examples.strategies.kraken_market_structure_scanner.funding_reports import (
    write_observation, write_summary,
)


class TestSymbolMapping:

    def test_all_assets_mapped(self):
        for asset, venues in SYMBOL_MAP.items():
            assert "kraken_spot" in venues, f"{asset} missing kraken_spot mapping"
            for v in ["kraken_perp", "binance_perp", "bybit_perp"]:
                assert v in venues, f"{asset} missing {v} mapping"

    def test_quote_map_consistent(self):
        # Kraken should be USD, Binance/Bybit should be USDT
        assert QUOTE_MAP["kraken_spot"] == "USD"
        assert QUOTE_MAP["kraken_perp"] == "USD"
        assert QUOTE_MAP["binance_perp"] == "USDT"
        assert QUOTE_MAP["bybit_perp"] == "USDT"

    def test_funding_interval_known(self):
        for v in ["kraken_perp", "binance_perp", "bybit_perp"]:
            assert FUNDING_INTERVAL[v] == 8.0, f"{v} should have 8h funding interval"


class TestPriceLevel:

    def test_mid_price(self):
        pl = PriceLevel("test", "BTC/USD", "USD", bid=100.0, ask=101.0)
        assert pl.mid == 100.5

    def test_mid_from_mark(self):
        pl = PriceLevel("test", "BTC/USD", "USD", mark=100.0)
        assert pl.mid == 100.0

    def test_mid_preference_bid_ask_over_mark(self):
        pl = PriceLevel("test", "BTC/USD", "USD", bid=100.0, ask=101.0, mark=99.0)
        assert pl.mid == 100.5  # bid/ask takes priority

    def test_is_stale(self):
        pl = PriceLevel("test", "BTC/USD", "USD", bid=100.0, ask=101.0,
                         ts_exchange_ms=1000, ts_recv_ms=1000 + 60_000)
        assert pl.is_stale is True

    def test_not_stale(self):
        pl = PriceLevel("test", "BTC/USD", "USD", bid=100.0, ask=101.0,
                         ts_exchange_ms=1000, ts_recv_ms=1000 + 5_000)
        assert pl.is_stale is False

    def test_stale_returns_false_when_no_exchange_ts(self):
        pl = PriceLevel("test", "BTC/USD", "USD", bid=100.0, ask=101.0)
        assert pl.is_stale is False  # unknown, cannot judge


class TestOpportunityLogic:
    """Test the scoring logic from funding_scanner.make_observation."""

    def test_positive_funding_creates_candidate_when_high_enough(self):
        """When funding apr is very high, should create a candidate even with fees."""
        from examples.strategies.kraken_market_structure_scanner.funding_scanner import make_observation
        cfg = FundingConfig()

        spot = PriceLevel("kraken", "BTC/USD", "USD", bid=50000.0, ask=50010.0,
                           ts_recv_ms=1000)
        perp = {
            "venue": "binance", "symbol": "BTCUSDT", "quote": "USDT",
            "bid": 50020.0, "ask": 50030.0,
            "mark": None, "index": None,
            "ts_recv_ms": 1000, "ts_exchange_ms": None,
        }
        # With frate=0.01 (1%), APR = 0.01 * (24/8) * 365 * 100 = 1095%
        obs = make_observation("BTC", spot, perp, 0.001, cfg, "binance")
        assert obs is not None
        assert obs.funding_apr is not None
        assert obs.expected_funding_bps == 10.0  # 0.001 * 10000
        # Total cost: 40(spot) + 5(binance perp) * 2(entry+exit) + 10(slippage) + 10(latency) + 25(basis risk) + 20(USD/USDT mismatch) = 215
        assert obs.estimated_total_cost_bps > 100
        assert obs.quote_currency_mismatch is True

    def test_negative_funding_not_candidate(self):
        from examples.strategies.kraken_market_structure_scanner.funding_scanner import make_observation
        cfg = FundingConfig()
        spot = PriceLevel("kraken", "BTC/USD", "USD", bid=50000.0, ask=50010.0, ts_recv_ms=1000)
        perp = {
            "venue": "kraken", "symbol": "PI_XBTUSD", "quote": "USD",
            "bid": 50005.0, "ask": 50015.0,
            "mark": None, "index": None,
            "ts_recv_ms": 1000,
        }
        obs = make_observation("BTC", spot, perp, -0.001, cfg, "kraken")
        assert obs is not None
        assert obs.is_candidate is False
        assert obs.reason_if_rejected is not None
        assert "funding_rate_not_positive" in obs.reason_if_rejected

    def test_zero_funding_not_candidate(self):
        from examples.strategies.kraken_market_structure_scanner.funding_scanner import make_observation
        cfg = FundingConfig()
        spot = PriceLevel("kraken", "BTC/USD", "USD", bid=50000.0, ask=50010.0, ts_recv_ms=1000)
        perp = {"venue": "binance", "symbol": "BTCUSDT", "quote": "USDT",
                "bid": 50005.0, "ask": 50015.0, "mark": None, "index": None,
                "ts_recv_ms": 1000}
        obs = make_observation("BTC", spot, perp, 0.0, cfg, "binance")
        assert obs.is_candidate is False

    def test_fee_calculation(self):
        from examples.strategies.kraken_market_structure_scanner.funding_scanner import make_observation
        cfg = FundingConfig()
        spot = PriceLevel("kraken", "BTC/USD", "USD", bid=50000.0, ask=50010.0, ts_recv_ms=1000)
        perp = {"venue": "kraken", "symbol": "PI_XBTUSD", "quote": "USD",
                "bid": 50005.0, "ask": 50015.0, "mark": None, "index": None,
                "ts_recv_ms": 1000}
        obs = make_observation("BTC", spot, perp, 0.001, cfg, "kraken")
        assert obs.estimated_entry_fees_bps == 43.0  # 40 spot + 3 kraken
        assert obs.estimated_exit_fees_bps == 43.0
        assert obs.quote_currency_mismatch is False


class TestReports:

    def test_write_observation_jsonl(self):
        obs = FundingObservation(
            timestamp_ms=1000, asset="BTC", spot_venue="kraken", perp_venue="binance",
            spot_symbol="BTC/USD", perp_symbol="BTCUSDT",
            spot_bid=50000, spot_ask=50010, spot_mid=50005.0,
            perp_bid=50020, perp_ask=50030, perp_mid=50025.0,
            mark_price=None, index_price=None, quote_source="spot_perp_mid",
            basis_bps=4.0, funding_rate=0.001, funding_interval_hours=8.0,
            funding_apr=438.0, estimated_entry_fees_bps=45.0,
            estimated_exit_fees_bps=45.0, slippage_buffer_bps=10.0,
            basis_risk_buffer_bps=25.0, latency_buffer_bps=10.0,
            quote_currency_mismatch=True, quote_mismatch_buffer_bps=20.0,
            estimated_total_cost_bps=230.0, expected_funding_bps=10.0,
            estimated_net_edge_bps=-220.0, opportunity_type="cash_and_carry",
            is_candidate=False, reason_if_rejected="net_edge_too_low",
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
            write_observation(obs, tmp)
            fname = tmp.name
        with open(fname) as f:
            line = f.readline()
        parsed = json.loads(line)
        assert parsed["asset"] == "BTC"
        assert parsed["is_candidate"] is False
        Path(fname).unlink()

    def test_write_summary_json(self):
        obs_content = [
            {"asset": "BTC", "funding_apr": 100.0, "basis_bps": 5.0, "estimated_net_edge_bps": -50.0},
            {"asset": "ETH", "funding_apr": 200.0, "basis_bps": 10.0, "estimated_net_edge_bps": 30.0},
        ]
        with tempfile.TemporaryDirectory() as td:
            obs_file = Path(td) / "test.jsonl"
            with open(obs_file, "w") as f:
                for r in obs_content:
                    f.write(json.dumps(r) + "\n")
            stats = {
                "polls": 2, "observations": 2, "candidates": 1,
                "candidates_by_asset": {"ETH": 1},
                "candidates_by_venue_pair": {"kraken-binance": 1},
                "errors_by_venue": {}, "rejection_reasons": {"net_edge_too_low": 1},
            }
            summary_path = write_summary(stats, obs_file, Path(td))
            with open(summary_path) as f:
                s = json.load(f)
            assert s["max_funding_apr"] == 200.0
            assert s["median_funding_apr"] == 200.0  # upper median for even-length list: index 1
            assert s["max_basis_bps"] == 10.0
            assert s["max_estimated_net_edge_bps"] == 30.0
            assert s["polls"] == 2
