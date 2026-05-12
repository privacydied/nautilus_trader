#!/usr/bin/env python3
"""Debug V4: count bar-by-bar which entry conditions fail."""
import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent.parent))

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.indicators import ExponentialMovingAverage as EMA, DonchianChannel as Donchian, AverageTrueRange as ATR

iid = InstrumentId.from_str("BTC/USD.KRAKEN")
cat = ParquetDataCatalog(path="data/catalog/kraken_btcusd_15m_2024h1")
bars_15m = cat.bars(instrument_ids=[str(iid)])
print(f"15m bars: {len(bars_15m)}")

# Aggregate to 1h
spec_1h = BarSpecification(1, BarAggregation.HOUR, PriceType.LAST)
bt_1h = BarType(iid, spec_1h)
bars_1h = []
group = []
for b in bars_15m:
    group.append(b)
    if len(group) == 4:
        o, h, l, c = float(group[0].open), max(float(x.high) for x in group), min(float(x.low) for x in group), float(group[-1].close)
        vol = sum(float(x.volume.as_decimal()) for x in group)
        ts = group[0].ts_event
        bars_1h.append(Bar(bar_type=bt_1h, open=Price(o,2), high=Price(h,2), low=Price(l,2), close=Price(c,2), volume=Quantity(vol,8), ts_event=ts, ts_init=ts))
        group = []
print(f"1h bars: {len(bars_1h)}")

ema50 = EMA(50)
ema200 = EMA(200)
donch = Donchian(100)
atr = ATR(20)
atr_med = []

ema_cross_bars = donch_break_bars = atr_exp_bars = all_pass = 0

for i, bar in enumerate(bars_1h):
    c = float(bar.close); h = float(bar.high); l = float(bar.low)

    # Check Donchian breakout BEFORE updating (avoid self-fulfilling comparison)
    prior_upper = donch.upper if donch.initialized else None
    
    ema50.update_raw(c)
    ema200.update_raw(c)
    donch.update_raw(h, l)
    v = atr.update_raw(h, l, c)
    if v is not None:
        atr_med.append(float(v) if not isinstance(v, float) else v)

    if i < 200:
        continue

    cond1 = ema50.initialized and ema200.initialized and ema50.value > ema200.value
    cond2 = prior_upper is not None and c > prior_upper
    cond3 = atr.initialized and atr.value > 0
    med = 0
    if len(atr_med) >= 10:
        s = sorted(atr_med)
        med = s[len(s)//2]
    cond4 = med > 0 and (float(atr.value) if not isinstance(atr.value, float) else atr.value) > med

    if cond1: ema_cross_bars += 1
    if cond2: donch_break_bars += 1
    if cond3 and cond4: atr_exp_bars += 1
    if cond1 and cond2 and cond3 and cond4:
        all_pass += 1
        if all_pass <= 3:
            print(f"  BAR {i}: close={c:.2f} EMA50={ema50.value:.2f} EMA200={ema200.value:.2f} DonchHI={prior_upper:.2f} ATR={atr.value:.2f} MedATR={med:.2f}")

print(f"\nSummary (after warmup):")
print(f"  EMA(50)>EMA(200): {ema_cross_bars} bars")
print(f"  Close>Donchian(100) high: {donch_break_bars} bars")
print(f"  ATR>0 and ATR expansion: {atr_exp_bars} bars")
print(f"  ALL 4 conditions: {all_pass} bars")
