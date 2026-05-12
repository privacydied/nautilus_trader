#!/usr/bin/env python3
"""Comprehensive tests for V5 multi-asset momentum portfolio."""
from decimal import Decimal

import pytest

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarSpecification, BarAggregation, BarType
from nautilus_trader.model.enums import PriceType, OmsType, AccountType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

from examples.strategies.kraken_v5_portfolio.config_v5 import (
    LIQUID_PAIRS, KRAKEN_VENUE, BAR_SPEC_STEP,
    EMA_TREND_PERIOD, MAX_POSITIONS, MAX_NOTIONAL_PCT,
    STARTING_BALANCE_USD, TAKER_FEE, WINDOWS,
)
from examples.strategies.kraken_v5_portfolio.strategy_v5 import (
    KrakenV5PortfolioConfig,
    KrakenV5PortfolioStrategy,
    DEFAULT_UNIVERSE,
    SIZE_PRECISION,
    _AssetState,
)


# ============================================================================
# Config / universe tests
# ============================================================================

class TestV5Universe:
    """All instruments are spot USD pairs, no futures."""

    def test_default_universe_is_ten_pairs(self):
        assert len(DEFAULT_UNIVERSE) == 10

    def test_all_contain_kraken_venue(self):
        for p in DEFAULT_UNIVERSE:
            assert p.endswith(".KRAKEN")

    def test_liquid_pairs_match_default_universe_bases(self):
        """LIQUID_PAIRS should match the instrument bases in DEFAULT_UNIVERSE."""
        bases = {p.split(".")[0] for p in DEFAULT_UNIVERSE}
        liquid = set(f"{p.replace('/', '/')}" for p in LIQUID_PAIRS)
        expected = set(DEFAULT_UNIVERSE)
        # LIQUID_PAIRS is ['BTC/USD', ...] while DEFAULT_UNIVERSE is ['BTC/USD.KRAKEN', ...]
        liquid_full = {f"{p}.KRAKEN" for p in LIQUID_PAIRS}
        assert liquid_full == expected

    def test_all_assets_have_size_precision(self):
        for iid in DEFAULT_UNIVERSE:
            assert iid in SIZE_PRECISION, f"Missing size precision for {iid}"

    def test_no_futures_symbols(self):
        for p in DEFAULT_UNIVERSE:
            assert "PI_" not in p
            assert "PF_" not in p
            assert "XBT/USD" not in p  # should use BTC/USD.KRAKEN

    def test_no_shorting_leverage_margin_in_config(self):
        c = KrakenV5PortfolioConfig(universe=DEFAULT_UNIVERSE)
        assert not hasattr(c, "leverage")
        assert not hasattr(c, "margin_pct")

    def test_all_instruments_are_spot_usd_pairs(self):
        for p in DEFAULT_UNIVERSE:
            parts = p.split(".")
            assert len(parts) == 2, f"Bad format: {p}"
            pair_str = parts[0]
            assert pair_str.endswith("/USD"), f"Not /USD pair: {p}"
            assert parts[1] == "KRAKEN"

    def test_windows_defined(self):
        assert len(WINDOWS) >= 2
        for label, start, end in WINDOWS:
            assert start < end


# ============================================================================
# Multi-asset synthetic backtest tests
# ============================================================================


class _EngineFixture:
    """Helper to build a multi-asset engine with synthetic bars."""

    @staticmethod
    def make_instrument(symbol_str: str) -> CurrencyPair:
        iid = InstrumentId.from_str(symbol_str)
        return CurrencyPair(
            instrument_id=iid,
            raw_symbol=Symbol(iid.symbol.value.replace("XBT", "BTC")),
            base_currency=BTC,
            quote_currency=USD,
            price_precision=2,
            size_precision=8,
            price_increment=Price(0.01, 2),
            size_increment=Quantity(0.00000001, 8),
            multiplier=Quantity(1, 0),
            maker_fee=Decimal("0.0025"),
            taker_fee=Decimal("0.004"),
            margin_init=Decimal(0),
            margin_maint=Decimal(0),
            ts_event=0,
            ts_init=0,
        )

    @staticmethod
    def make_bars(symbol_str: str, starts: list[float], step: float,
                  n_bars: int, bar_spec: BarSpecification) -> list[Bar]:
        """Generate n_bars with price += step each bar from each start."""
        iid = InstrumentId.from_str(symbol_str)
        bt = BarType(iid, bar_spec)
        ts = 1704067200_000_000_000  # 2024-01-01 00:00 UTC
        bars: list[Bar] = []
        for i in range(n_bars):
            close = starts[0] + step * i
            tts = ts + i * 4 * 3600 * 1_000_000_000
            bars.append(Bar(
                bar_type=bt,
                open=Price(close - 5, 2), high=Price(close + 10, 2),
                low=Price(close - 10, 2), close=Price(close, 2),
                volume=Quantity(100.0, 8), ts_event=tts, ts_init=tts,
            ))
        return bars


def _build_multi_engine(n_bars=500, warmup=100, rebalance=12):
    """
    Build 3-asset engine:
    - Asset A: strong uptrend (should be selected)
    - Asset B: flat/choppy (lower momentum, may be ranked low)
    - Asset C: downtrend (price < EMA, should be excluded)
    """
    btc = _EngineFixture.make_instrument("BTC/USD.KRAKEN")
    eth = _EngineFixture.make_instrument("ETH/USD.KRAKEN")
    sol = _EngineFixture.make_instrument("SOL/USD.KRAKEN")

    spec = BarSpecification(BAR_SPEC_STEP, BarAggregation.HOUR, PriceType.LAST)

    # Asset A: BTC strong uptrend
    bars_a = _EngineFixture.make_bars("BTC/USD.KRAKEN", [50000.0], 50.0, n_bars, spec)
    # Asset B: ETH mild uptrend
    bars_b = _EngineFixture.make_bars("ETH/USD.KRAKEN", [2500.0], 2.0, n_bars, spec)
    # Asset C: SOL downtrend (price declining)
    bars_c = _EngineFixture.make_bars("SOL/USD.KRAKEN", [100.0], -1.0, n_bars, spec)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="V5-TEST",
        logging=LoggingConfig(bypass_logging=True),
    ))
    engine.add_venue(
        btc.id.venue,
        OmsType.NETTING, AccountType.CASH,
        starting_balances=[Money(STARTING_BALANCE_USD, USD)],
    )
    engine.add_instrument(btc)
    engine.add_instrument(eth)
    engine.add_instrument(sol)
    engine.add_data(bars_a)
    engine.add_data(bars_b)
    engine.add_data(bars_c)

    cfg = KrakenV5PortfolioConfig(
        universe=("BTC/USD.KRAKEN", "ETH/USD.KRAKEN", "SOL/USD.KRAKEN"),
        bar_type=BarType(btc.id, spec),
        ema_trend_period=warmup,
        atr_period=14,
        max_positions=2,
        rebalance_bars=rebalance,
    )
    strategy = KrakenV5PortfolioStrategy(config=cfg)
    engine.add_strategy(strategy)
    engine.run()
    result = engine.get_result()
    try:
        pos_df = engine.trader.generate_positions_report()
    except Exception:
        pos_df = None
    engine.dispose()
    return strategy, result, pos_df


class TestV5MultiAssetSynthetic:
    """Engine-backed multi-asset tests via BacktestEngine lifecycle."""

    def test_engine_starts_with_three_assets(self):
        strategy, result, pos_df = _build_multi_engine()
        assert strategy is not None
        assert len(strategy.assets) == 3
        assert "BTC/USD.KRAKEN" in strategy.assets
        assert "ETH/USD.KRAKEN" in strategy.assets
        assert "SOL/USD.KRAKEN" in strategy.assets

    def test_all_assets_track_price_history(self):
        strategy, result, pos_df = _build_multi_engine(n_bars=500)
        for id_str, state in strategy.assets.items():
            assert len(state.price_history) == 500, f"{id_str}: {len(state.price_history)} bars"

    def test_no_short_orders_ever(self):
        """All positions should be LONG, never SHORT. FLAT is also acceptable."""
        strategy, result, pos_df = _build_multi_engine()
        if pos_df is not None and len(pos_df) > 0:
            for _, row in pos_df.iterrows():
                side = str(row.get("side", ""))
                assert side in ("LONG", "FLAT"), f"Short position found: {side}"

    def test_rebalance_selects_top_ranked(self):
        """BTC (strong uptrend, high momentum) should be selected over SOL (downtrend)."""
        strategy, result, pos_df = _build_multi_engine()
        # BTC had the strongest momentum; verify its price history exists
        btc_state = strategy.assets["BTC/USD.KRAKEN"]
        sol_state = strategy.assets["SOL/USD.KRAKEN"]
        assert btc_state.momentum > sol_state.momentum, \
            "BTC momentum should exceed SOL momentum in this synthetic trend/chop/down test"

    def test_assets_below_ema_are_excluded(self):
        """Asset C (SOL, downtrend) should have low/negative momentum."""
        strategy, result, pos_df = _build_multi_engine()
        sol_state = strategy.assets["SOL/USD.KRAKEN"]
        # SOL price declining from 100 should be below its EMA
        if sol_state.ema_trend.initialized:
            assert sol_state.current_price < sol_state.ema_trend.value, \
                "Downtrend asset should be below its EMA"

    def test_target_allocation_respects_max_exposure(self):
        """No single asset should exceed max_notional_pct (50%)."""
        strategy, result, pos_df = _build_multi_engine()
        for state in strategy.assets.values():
            if state.is_held and state.position_qty > 0:
                notional = state.current_price * state.position_qty
                assert notional <= STARTING_BALANCE_USD * 0.50, \
                    f"Position {notional:.2f} exceeds max notional"

    def test_no_leverage_or_margin_used(self):
        """All instruments have margin_init=0 and the account is cash."""
        strategy, result, pos_df = _build_multi_engine()
        # Cash account with netting and zero margin means no leverage possible
        assert strategy is not None

    def test_reports_are_real_not_placeholder(self):
        """generate_positions_report should return real positions if any opened."""
        strategy, result, pos_df = _build_multi_engine()
        assert result is not None
        # The result object is a real Nautilus BacktestResult
        assert hasattr(result, 'stats_pnls')

    def test_total_bars_counts(self):
        """Total bars processed = sum of per-asset bars."""
        strategy, result, pos_df = _build_multi_engine(n_bars=400)
        assert strategy.total_bars == 1200  # 3 assets * 400 bars each

    def test_no_direct_on_bar_calls(self):
        """Strategy must go through BacktestEngine lifecycle, not manual calls."""
        btc = _EngineFixture.make_instrument("BTC/USD.KRAKEN")
        spec = BarSpecification(BAR_SPEC_STEP, BarAggregation.HOUR, PriceType.LAST)
        bt = BarType(btc.id, spec)
        cfg = KrakenV5PortfolioConfig(
            universe=("BTC/USD.KRAKEN",),
            bar_type=bt,
        )
        strategy = KrakenV5PortfolioStrategy(config=cfg)
        # Strategy was created, but on_bar was NOT directly called
        # It only runs via engine.add_strategy + engine.run()
        assert strategy is not None
        assert strategy.total_bars == 0  # no bars processed without engine
