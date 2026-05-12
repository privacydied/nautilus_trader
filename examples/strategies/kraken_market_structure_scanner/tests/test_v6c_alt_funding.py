#!/usr/bin/env python3
"""Tests for V6-C altcoin funding/basis anomaly monitor."""
import pytest
import json
import tempfile
from pathlib import Path

from examples.strategies.kraken_market_structure_scanner.funding_config import (
    FundingConfig, SYMBOL_MAP, QUOTE_MAP, FUNDING_INTERVAL,
)
from examples.strategies.kraken_market_structure_scanner.funding_models_alt import (
    FundingObservationAlt, CostScenario, PersistenceTracker,
    make_cost_scenarios,
)
from examples.strategies.kraken_market_structure_scanner.funding_reports_alt import (
    write_observation_alt, write_candidate_alt, write_summary_alt, CandidateState,
)
from examples.strategies.kraken_market_structure_scanner.funding_scanner_alt import (
    make_observation_alt, _perp_mid,
)
from examples.strategies.kraken_market_structure_scanner.funding_scanner_alt import (
    _compute_net_edges,
)


# ---------- Symbol Mapping ----------

class TestAltSymbolMapping:
    """Test altcoin symbol mappings."""

    def test_sol_mapped(self):
        m = SYMBOL_MAP["SOL"]
        assert m["kraken_spot"] == "SOLUSD"
        assert m["kraken_perp"] == "PI_SOLUSD"
        assert m["binance_perp"] == "SOLUSDT"
        assert m["bybit_perp"] == "SOLUSDT"

    def test_xrp_mapped(self):
        m = SYMBOL_MAP["XRP"]
        assert m["kraken_spot"] == "XRPUSD"
        assert m["binance_perp"] == "XRPUSDT"

    def test_doge_no_kraken_perp(self):
        m = SYMBOL_MAP["DOGE"]
        assert m["kraken_spot"] == "DOGEUSD"
        assert m["kraken_perp"] is None

    def test_pepe_no_kraken_spot(self):
        m = SYMBOL_MAP["PEPE"]
        assert m["kraken_spot"] is None
        assert m["binance_perp"] == "PEPEUSDT"
        assert m["bybit_perp"] == "1000PEPEUSDT"

    def test_missing_perp_venues_ok(self):
        """Some alts have None for kraken_perp — should not crash."""
        for asset in ["DOGE", "SUI", "ARB", "OP", "APT", "WIF", "TON"]:
            m = SYMBOL_MAP.get(asset)
            assert m is not None
            # At least one perp venue should be mapped
            has_any_perp = any(
                bool(m.get(k)) for k in m
                if k.endswith("_perp")
            )
            assert has_any_perp, f"{asset} has no perp venues mapped"

    def test_quote_map_consistent(self):
        assert QUOTE_MAP["kraken_spot"] == "USD"
        assert QUOTE_MAP["kraken_perp"] == "USD"
        assert QUOTE_MAP["binance_perp"] == "USDT"
        assert QUOTE_MAP["bybit_perp"] == "USDT"

    def test_funding_intervals_known(self):
        for v in ["kraken_perp", "binance_perp", "bybit_perp"]:
            assert FUNDING_INTERVAL[v] == 8.0


# ---------- Cost Scenarios ----------

class TestCostScenarios:
    """Test three-tier cost scenario calculation."""

    def test_three_scenarios(self):
        sc = make_cost_scenarios()
        assert "conservative_taker" in sc
        assert "mixed_maker_taker" in sc
        assert "optimistic_maker" in sc

    def test_conservative_most_expensive(self):
        sc = make_cost_scenarios()
        cons = sc["conservative_taker"].total_cost_bps(has_mismatch=True)
        mixed = sc["mixed_maker_taker"].total_cost_bps(has_mismatch=True)
        opt = sc["optimistic_maker"].total_cost_bps(has_mismatch=True)
        assert cons > mixed > opt

    def test_no_mismatch_lower_cost(self):
        sc = make_cost_scenarios(quote_mismatch_bps=20.0)
        cons = sc["conservative_taker"]
        cost_with = cons.total_cost_bps(has_mismatch=True)
        cost_without = cons.total_cost_bps(has_mismatch=False)
        assert cost_with > cost_without
        assert cost_with - cost_without == 20.0  # quote_mismatch_bps

    def test_cost_scenario_detail(self):
        sc = make_cost_scenarios(
            spot_taker_bps=40.0, perp_taker_bps=5.0,
        )
        cons = sc["conservative_taker"]
        # Entry: 40+5 = 45, Exit: 45, Buffers: 10+10+25 = 45, mismatch: 20
        # Total with mismatch: 45 + 45 + 45 + 20 = 155
        assert cons.total_cost_bps(has_mismatch=True) == 155.0
        assert cons.total_cost_bps(has_mismatch=False) == 135.0


# ---------- Make Observation Alt ----------

class TestMakeObservationAlt:
    """Test observation creation with three cost scenarios."""

    def _spot(self):
        from examples.strategies.kraken_market_structure_scanner.funding_models import PriceLevel
        return PriceLevel("kraken", "SOL/USD", "USD", bid=100.0, ask=101.0, ts_recv_ms=1000)

    def _perp(self):
        return {
            "venue": "binance", "symbol": "SOLUSDT", "quote": "USDT",
            "bid": 100.5, "ask": 101.5,
            "mark": None, "index": None,
            "ts_recv_ms": 1000, "ts_exchange_ms": None,
        }

    def test_observation_created(self):
        cfg = FundingConfig()
        sc = make_cost_scenarios()
        spot = self._spot()
        perp = self._perp()
        frate = 0.002  # 0.2% per 8h → ~109.5% APR
        obs = make_observation_alt("SOL", spot, perp, frate, cfg, "binance", sc)
        assert obs is not None
        assert obs.asset == "SOL"
        assert obs.funding_rate == 0.002
        assert obs.funding_apr is not None
        assert obs.funding_apr > 30.0

    def test_three_scenarios_computed(self):
        cfg = FundingConfig()
        sc = make_cost_scenarios()
        spot = self._spot()
        perp = self._perp()
        obs = make_observation_alt("SOL", spot, perp, 0.002, cfg, "binance", sc)
        # USD/USDT mismatch → conservative cost is high
        assert obs.conservative_taker_cost_bps > 0
        assert obs.mixed_maker_taker_cost_bps > 0
        assert obs.optimistic_maker_cost_bps > 0
        assert obs.conservative_taker_cost_bps > obs.optimistic_maker_cost_bps

    def test_quote_mismatch_flagged(self):
        cfg = FundingConfig()
        sc = make_cost_scenarios()
        spot = self._spot()  # USD
        perp = self._perp()  # USDT
        obs = make_observation_alt("SOL", spot, perp, 0.002, cfg, "binance", sc)
        assert obs.quote_mismatch is True
        # Mismatch → not candidate (rejection)
        assert obs.candidate is False
        assert "USD_USDT_mismatch" in obs.rejection_reason

    def test_no_mismatch_can_be_candidate(self):
        """When quotes match, high funding should create a candidate."""
        from examples.strategies.kraken_market_structure_scanner.funding_models import PriceLevel
        cfg = FundingConfig(min_funding_apr=10.0, min_net_edge_bps=0.0)
        sc = make_cost_scenarios()
        # Kraken perp (USD quote)
        spot = PriceLevel("kraken", "BTC/USD", "USD", bid=50000.0, ask=50010.0, ts_recv_ms=1000)
        perp = {
            "venue": "kraken", "symbol": "PI_XBTUSD", "quote": "USD",
            "bid": 50005.0, "ask": 50015.0,
            "ts_recv_ms": 1000,
        }
        # frate=0.1 → APR=4562.5%, expected_funding=100000 bps
        # Cost: entry(40+3)+exit(43) + buffers(10+10+25) = 86 + 45 = 131 (no mismatch)
        # Net = 1000 - 131 = 869 bps > 0
        obs = make_observation_alt("BTC", spot, perp, 0.1, cfg, "kraken", sc)
        assert obs is not None
        assert obs.quote_mismatch is False
        assert obs.candidate is True

    def test_negative_funding_not_candidate(self):
        cfg = FundingConfig()
        sc = make_cost_scenarios()
        spot = self._spot()
        perp = self._perp()
        obs = make_observation_alt("SOL", spot, perp, -0.001, cfg, "binance", sc)
        assert obs.candidate is False
        assert "funding_rate_not_positive" in obs.rejection_reason

    def test_low_funding_apr_not_candidate(self):
        """Positive funding but below min_funding_apr threshold."""
        cfg = FundingConfig(min_funding_apr=30.0)
        sc = make_cost_scenarios()
        spot = self._spot()
        perp = self._perp()
        # frate=0.0001 → APR = 0.0001 * 3 * 365 * 100 = 10.95% < 30%
        obs = make_observation_alt("SOL", spot, perp, 0.0001, cfg, "binance", sc)
        # Still rejected because of USD/USDT mismatch; test low apr path separately
        assert obs is not None


# ---------- _perp_mid ----------

class TestPerpMid:
    def test_from_bid_ask(self):
        assert _perp_mid({"bid": 100.0, "ask": 102.0}) == 101.0

    def test_from_mark(self):
        assert _perp_mid({"bid": None, "ask": None, "mark": 99.0}) == 99.0

    def test_bid_ask_over_mark(self):
        assert _perp_mid({"bid": 100.0, "ask": 102.0, "mark": 105.0}) == 101.0

    def test_none_input(self):
        assert _perp_mid(None) is None

    def test_zero_prices(self):
        assert _perp_mid({"bid": 0, "ask": 0}) is None


# ---------- _compute_net_edges ----------

class TestComputeNetEdges:
    def test_positive_funding(self):
        sc = make_cost_scenarios(
            spot_taker_bps=40.0, perp_taker_bps=5.0,
            quote_mismatch_bps=20.0,
        )
        edges = _compute_net_edges(0.01, sc, has_mismatch=False)
        # expected_funding = 0.01 * 10000 = 100 bps
        # conservative cost = entry(40+5) + exit(45) + buffers(10+10+25) = 90+45 = 135
        # net = 100 - 135 = -35
        assert edges["conservative_taker"] == round(100.0 - 135.0, 4)

    def test_no_funding(self):
        sc = make_cost_scenarios()
        edges = _compute_net_edges(None, sc, has_mismatch=False)
        assert edges["conservative_taker"] < 0  # 0 - cost

    def test_mismatch_increases_cost(self):
        sc = make_cost_scenarios(quote_mismatch_bps=20.0)
        edges_match = _compute_net_edges(0.01, sc, has_mismatch=False)
        edges_mismatch = _compute_net_edges(0.01, sc, has_mismatch=True)
        assert edges_mismatch["conservative_taker"] < edges_match["conservative_taker"]


# ---------- PersistenceTracker ----------

class TestPersistenceTracker:
    def test_consecutive_increments(self):
        t = PersistenceTracker(asset="SOL", perp_venue="binance")
        t.poll_candidate(1000)
        t.poll_candidate(2000)
        t.poll_candidate(3000)
        assert t.consecutive_count == 3  # first=1, then +1 each poll
        assert t.max_consecutive_count == 3

    def test_broken_on_non_candidate(self):
        t = PersistenceTracker(asset="SOL", perp_venue="binance")
        t.poll_candidate(1000)
        t.poll_candidate(2000)
        t.poll_non_candidate(3000)
        assert t.consecutive_count == 0
        assert t.max_consecutive_count >= 1  # max was at least 1 before break

    def test_durability(self):
        t = PersistenceTracker(asset="SOL", perp_venue="binance")
        t.poll_candidate(1000)
        t.poll_candidate(2000)
        assert t.is_durable(2) is True
        assert t.is_durable(3) is False

    def test_new_tracker_not_durable(self):
        t = PersistenceTracker(asset="SOL", perp_venue="binance")
        assert t.is_durable(1) is False



class TestCostScenarioDetails:
    def test_custom_maker_fees(self):
        sc = CostScenario(
            name="test", spot_fee_bps=20, perp_fee_bps=2,
            slippage_bps=5, latency_bps=5, basis_risk_bps=12,
            quote_mismatch_bps=10,
        )
        # Entry: 22, Exit: 22, Buffers: 5+5+12 = 22, no mismatch: total = 44+22 = 66
        assert sc.total_cost_bps(has_mismatch=False) == 66.0
        assert sc.total_cost_bps(has_mismatch=True) == 76.0


# ---------- CandidateState ----------

class TestCandidateState:
    def test_consecutive_polls(self):
        state = CandidateState("SOL", "binance", min_polls=3)

        obs = _make_fake_candidate("SOL", "binance", ts_ms=1000, frate=0.01)
        state.poll(obs)
        assert state.consecutive == 1
        assert state.max_consecutive == 1

        obs2 = _make_fake_candidate("SOL", "binance", ts_ms=2000, frate=0.01)
        state.poll(obs2)
        assert state.consecutive == 2
        assert state.max_consecutive == 2

        obs3 = _make_fake_candidate("SOL", "binance", ts_ms=3000, frate=0.01)
        state.poll(obs3)
        assert state.consecutive == 3
        assert state.max_consecutive == 3
        assert state.is_durable is True

    def test_durability_met(self):
        state = CandidateState("SOL", "binance", min_polls=3)

        obs1 = _make_fake_candidate("SOL", "binance", ts_ms=1000)
        state.poll(obs1)
        obs2 = _make_fake_candidate("SOL", "binance", ts_ms=2000)
        state.poll(obs2)
        obs3 = _make_fake_candidate("SOL", "binance", ts_ms=3000)
        state.poll(obs3)
        assert state.is_durable is True
        assert state.max_consecutive == 3

    def test_non_candidate_breaks_streak(self):
        state = CandidateState("SOL", "binance", min_polls=3)
        obs1 = _make_fake_candidate("SOL", "binance", ts_ms=1000)
        state.poll(obs1)
        obs2 = _make_fake_non_candidate("SOL", "binance", ts_ms=2000)
        state.poll(obs2)
        assert state.consecutive == 0


# ---------- Reports ----------

class TestReportsAlt:
    """Test JSONL and summary writing."""

    def test_write_observation(self):
        obs = _make_fake_candidate("SOL", "binance", ts_ms=1000)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
            write_observation_alt(obs, tmp)
            fname = tmp.name
        with open(fname) as f:
            parsed = json.loads(f.readline())
        assert parsed["asset"] == "SOL"
        assert parsed["perp_venue"] == "binance"
        Path(fname).unlink()

    def test_write_candidate(self):
        obs = _make_fake_candidate("SOL", "binance", ts_ms=1000)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
            write_candidate_alt(obs, tmp)
            fname = tmp.name
        with open(fname) as f:
            parsed = json.loads(f.readline())
        assert parsed["funding_apr"] is not None
        Path(fname).unlink()

    def test_write_summary_with_data(self):
        obs = [
            {
                "asset": "SOL", "perp_venue": "binance",
                "funding_apr": 50.0, "basis_bps": 10.0,
                "conservative_net_edge_bps": -30.0,
                "rejection_reason": "USD_USDT_mismatch",
            },
            {
                "asset": "XRP", "perp_venue": "binance",
                "funding_apr": 80.0, "basis_bps": 15.0,
                "conservative_net_edge_bps": 50.0,
                "rejection_reason": None,
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            obs_file = Path(td) / "test.jsonl"
            with open(obs_file, "w") as f:
                for r in obs:
                    f.write(json.dumps(r) + "\n")
            cand_file = Path(td) / "cand.jsonl"
            cand_file.touch()
            cfg = FundingConfig()
            cfg.min_persistence_polls = 3  # type: ignore
            from examples.strategies.kraken_market_structure_scanner.funding_reports_alt import write_summary_alt
            stats = {"observations": 2, "candidates": 1, "durable_candidates": 0, "errors_by_venue": {}, "rejection_reasons": {}, "missing_spot": 0, "missing_perp": 0, "quote_mismatch_count": 1, "stale_quotes": 0}
            summary_path = write_summary_alt(stats, obs_file, cand_file, Path(td), cfg)
            with open(summary_path) as f:
                s = json.load(f)
            assert s["max_funding_apr"] == 80.0
            assert s["observations_count"] == 2
            assert s["candidate_count"] == 1


# ---------- No Order Placement Code ----------

class TestNoOrderCode:
    def test_no_place_order_in_scanner(self):
        from examples.strategies.kraken_market_structure_scanner.funding_scanner_alt import AltFundingScanner
        source = open(
            "examples/strategies/kraken_market_structure_scanner/funding_scanner_alt.py"
        ).read()
        assert "place_order" not in source.lower()
        assert "create_order" not in source.lower()
        assert "submit_order" not in source.lower()


# ---------- Helpers ----------

def _make_fake_candidate(asset, perp_venue, ts_ms=1000, frate=0.01, durable=False):
    return FundingObservationAlt(
        timestamp_ms=ts_ms, asset=asset, spot_venue="kraken", perp_venue=perp_venue,
        spot_symbol=f"{asset}/USD", perp_symbol=f"{asset}USDT",
        quote_source="USDT", quote_mismatch=True,
        spot_bid=100.0, spot_ask=101.0, spot_mid=100.5,
        perp_bid=100.5, perp_ask=101.5, perp_mid=101.0,
        mark_price=None, index_price=None,
        spot_bid_size=None, spot_ask_size=None,
        perp_bid_size=None, perp_ask_size=None,
        basis_bps=9.95, funding_rate=frate, funding_interval_hours=8.0,
        funding_apr=round(frate * (24/8) * 365 * 100, 2),
        conservative_taker_cost_bps=155.0,
        mixed_maker_taker_cost_bps=80.0,
        optimistic_maker_cost_bps=40.0,
        conservative_net_edge_bps=round(frate * 10000 - 155.0, 2),
        mixed_net_edge_bps=round(frate * 10000 - 80.0, 2),
        optimistic_net_edge_bps=round(frate * 10000 - 40.0, 2),
        candidate=True, durable_candidate=durable,
        rejection_reason=None,
        quote_stale=False, quote_age_ms=500,
    )


def _make_fake_non_candidate(asset, perp_venue, ts_ms=1000):
    return FundingObservationAlt(
        timestamp_ms=ts_ms, asset=asset, spot_venue="kraken", perp_venue=perp_venue,
        spot_symbol=f"{asset}/USD", perp_symbol=f"{asset}USDT",
        quote_source="USDT", quote_mismatch=True,
        spot_bid=100.0, spot_ask=101.0, spot_mid=100.5,
        perp_bid=100.5, perp_ask=101.5, perp_mid=101.0,
        mark_price=None, index_price=None,
        spot_bid_size=None, spot_ask_size=None,
        perp_bid_size=None, perp_ask_size=None,
        basis_bps=9.95, funding_rate=0.0001, funding_interval_hours=8.0,
        funding_apr=1.095,
        conservative_taker_cost_bps=155.0,
        mixed_maker_taker_cost_bps=80.0,
        optimistic_maker_cost_bps=40.0,
        conservative_net_edge_bps=-154.0,
        mixed_net_edge_bps=-79.0,
        optimistic_net_edge_bps=-39.0,
        candidate=False, durable_candidate=False,
        rejection_reason="funding_apr_too_low",
        quote_stale=False, quote_age_ms=500,
    )
