#!/usr/bin/env python3
"""Smoke tests for V5 multi-asset momentum portfolio."""
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarSpecification, BarAggregation, BarType
from nautilus_trader.model.enums import PriceType, OmsType, AccountType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

from examples.strategies.kraken_v5_portfolio.config_v5 import (
    LIQUID_PAIRS, KRAKEN_VENUE, BAR_SPEC_STEP,
    EMA_TREND_PERIOD, MAX_POSITIONS, MAX_NOTIONAL_PCT,
    STARTING_BALANCE_USD, TAKER_FEE,
)
from examples.strategies.kraken_v5_portfolio.strategy_v5 import (
    KrakenV5PortfolioConfig,
    KrakenV5PortfolioStrategy,
    DEFAULT_UNIVERSE,
    _AssetState,
)


class TestV5Config:
    def test_default_universe_is_ten_pairs(self):
        assert len(DEFAULT_UNIVERSE) == 10

    def test_all_contain_kraken_venue(self):
        for p in DEFAULT_UNIVERSE:
            assert p.endswith(".KRAKEN")

    def test_config_defaults(self):
        c = KrakenV5PortfolioConfig(universe=DEFAULT_UNIVERSE)
        assert c.max_positions == 3
        assert c.max_notional_pct == 0.50
        assert c.taker_fee == 0.0040
        assert c.ema_trend_period == 200
        assert c.trailing_stop_atr_mult == 3.0
        assert c.rebalance_bars == 6


class TestV5Synthetic:
    """Engine-backed test with synthetic 4h bars for BTC+ETH uptrend."""

    def _build_engine(self, warmup_period=50, rebalance=12):
        btc = CurrencyPair(
            instrument_id=InstrumentId.from_str("BTC/USD.KRAKEN"),
            raw_symbol=Symbol("BTCUSD"), base_currency=BTC, quote_currency=USD,
            price_precision=2, size_precision=8,
            price_increment=Price(0.01, 2),
            size_increment=Quantity(0.00000001, 8),
            multiplier=Quantity(1, 0),
            maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
            margin_init=Decimal(0), margin_maint=Decimal(0), ts_event=0, ts_init=0,
        )
        eth = CurrencyPair(
            instrument_id=InstrumentId.from_str("ETH/USD.KRAKEN"),
            raw_symbol=Symbol("ETHUSD"), base_currency=BTC, quote_currency=USD,
            price_precision=2, size_precision=8,
            price_increment=Price(0.01, 2),
            size_increment=Quantity(0.00000001, 8),
            multiplier=Quantity(1, 0),
            maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
            margin_init=Decimal(0), margin_maint=Decimal(0), ts_event=0, ts_init=0,
        )

        spec = BarSpecification(BAR_SPEC_STEP, BarAggregation.HOUR, PriceType.LAST)
        bars_btc, bars_eth = [], []
        base_btc, base_eth = 50000.0, 2500.0
        ts = 1704067200_000_000_000

        for i in range(300):
            close_btc = base_btc + i * 30
            close_eth = base_eth + i * 1.5
            tts = ts + i * 4 * 3600 * 1_000_000_000
            bars_btc.append(Bar(
                bar_type=BarType(btc.id, spec),
                open=Price(close_btc - 10, 2), high=Price(close_btc + 20, 2),
                low=Price(close_btc - 20, 2), close=Price(close_btc, 2),
                volume=Quantity(1.0, 8), ts_event=tts, ts_init=tts,
            ))
            bars_eth.append(Bar(
                bar_type=BarType(eth.id, spec),
                open=Price(close_eth - 5, 2), high=Price(close_eth + 10, 2),
                low=Price(close_eth - 10, 2), close=Price(close_eth, 2),
                volume=Quantity(10.0, 8), ts_event=tts, ts_init=tts,
            ))

        engine = BacktestEngine(config=BacktestEngineConfig(
            trader_id="V5-SMOKE",
            logging=LoggingConfig(bypass_logging=True),
        ))
        engine.add_venue(
            btc.id.venue,
            OmsType.NETTING, AccountType.CASH,
            starting_balances=[Money(STARTING_BALANCE_USD, USD)],
        )
        engine.add_instrument(btc)
        engine.add_instrument(eth)
        engine.add_data(bars_btc)
        engine.add_data(bars_eth)

        cfg = KrakenV5PortfolioConfig(
            universe=("BTC/USD.KRAKEN", "ETH/USD.KRAKEN"),
            bar_type=BarType(btc.id, spec),
            ema_trend_period=warmup_period,
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

    def test_engine_starts_without_error(self):
        strategy, result, pos_df = self._build_engine()
        assert strategy is not None
        assert len(strategy.assets) == 2
        assert "BTC/USD.KRAKEN" in strategy.assets
        assert "ETH/USD.KRAKEN" in strategy.assets

    def test_multi_asset_subscriptions(self):
        strategy, result, pos_df = self._build_engine()
        assert len(strategy.assets) == 2
        assert strategy.assets["BTC/USD.KRAKEN"].instrument_id == InstrumentId.from_str("BTC/USD.KRAKEN")
        assert strategy.assets["ETH/USD.KRAKEN"].instrument_id == InstrumentId.from_str("ETH/USD.KRAKEN")

    def test_strategy_tracks_state(self):
        """After running, asset states should have price history."""
        strategy, result, pos_df = self._build_engine()
        for id_str, state in strategy.assets.items():
            assert len(state.price_history) == 300
