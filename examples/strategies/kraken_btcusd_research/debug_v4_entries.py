#!/usr/bin/env python3
"""Debug V4 entry conditions on 1h aggregated data."""
import sys
sys.path.insert(0, "/volume1/py/nautilus_trader")

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.indicators import DonchianChannel, ExponentialMovingAverage, AverageTrueRange

iid = InstrumentId.from_str("BTC/USD.KRAKEN")
cat = ParquetDataCatalog(path="data/catalog/kraken_btcusd_15m_2024h1")
bars_15m = cat.bars(instrument_ids=[str(iid)])
print(f"Loaded {len(bars_15m)} 15m bars")

spec_1h = BarSpecification(1, BarAggregation.HOUR, PriceType.LAST)
bt_1h = BarType(iid, spec_1h)
bars_1h = []
group = []
for b in bars_15m:
    group.append(b)
    if len(group) == 4:
        o = float(group[0].open)
        hi = max(float(x.high) for x in group)
        lo = min(float(x.low) for x in group)
        c = float(group[-1].close)
        vol = sum(float(x.volume.as_decimal()) for x in group)
        ts = group[0].ts_event
        bars_1h.append(Bar(
            bar_type=bt_1h, open=Price(o, 2), high=Price(hi, 2),
            low=Price(lo, 2), close=Price(c, 2), volume=Quantity(vol, 8),
            ts_event=ts, ts_init=ts,
        ))
        group = []
print(f"Aggregated to {len(bars_1h)} 1h bars")

donch = DonchianChannel(100)
ema50 = ExponentialMovingAverage(50)
ema200 = ExponentialMovingAverage(200)
atr = AverageTrueRange(20)

ema_trend = donch_break = atr_expansion = all_pass = 0
atr_vals = []
samples = []
first_entry = None

for i, b in enumerate(bars_1h):
    c = float(b.close)
    h = float(b.high)
    l = float(b.low)
    ema50.update_raw(c)
    ema200.update_raw(c)
    donch.update_raw(h, l)
    v = atr.update_raw(h, l, c)
    
    if donch.initialized and ema50.initialized and ema200.initialized and atr.initialized:
        cond1 = ema50.value > ema200.value
        cond2 = c > donch.upper
        
        a = (float(v) if not isinstance(v, float) else v) if v is not None else atr_vals[-1] if atr_vals else 0
        atr_vals.append(a)
        if len(atr_vals) > 101:
            atr_vals.pop(0)
        med = sorted(atr_vals)[len(atr_vals)//2]
        cond3 = a > med and med > 0
        
        if cond1:
            ema_trend += 1
        if cond2:
            donch_break += 1
        if cond3:
            atr_expansion += 1
        if cond1 and cond2 and cond3:
            all_pass += 1
            if first_entry is None:
                first_entry = i
            if len(samples) < 3:
                samples.append((i, c, ema50.value, ema200.value, donch.upper, a, med))

print(f"\nAfter warmup ({len(bars_1h)-200} bars checked):")
print(f"  EMA trend (50>200):   {ema_trend}")
print(f"  Donchian breakout:    {donch_break}")
print(f"  ATR expansion:        {atr_expansion}")
print(f"  ALL conditions met:   {all_pass}")
if first_entry is not None:
    print(f"\n  First entry at bar {first_entry}")
    for i, c, e50, e200, dh, a, m in samples:
        print(f"    bar={i} close={c:.2f} ema50={e50:.2f} ema200={e200:.2f} donchHi={dh:.2f} atr={a:.2f} med={m:.2f}")
