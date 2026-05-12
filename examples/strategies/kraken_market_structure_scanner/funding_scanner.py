"""V6-B: Funding/Basis scanner — polling loop."""
import time
from pathlib import Path
import json
import requests

from .funding_config import FundingConfig, SYMBOL_MAP, QUOTE_MAP, FUNDING_INTERVAL
from .funding_models import FundingObservation
from .funding_reports import write_observation, write_summary

from .funding_venues import (
    kraken_spot_ticker,
    kraken_futures_funding_latest,
    binance_fapi_latest_funding,
    binance_perp_ticker,
    bybit_funding_history,
    bybit_perp_ticker,
)


def _recv_ms():
    return int(time.time() * 1000)


def _get_perp_fee(cfg, perp_venue):
    return cfg.venue_fees.get(perp_venue, cfg.perp_taker_fee_bps)


def fetch_spot(asset, cfg):
    sym_map = SYMBOL_MAP.get(asset, {})
    spot_sym = sym_map.get("kraken_spot", "XBTUSD")
    std_sym = f"{asset}/USD"
    return kraken_spot_ticker(spot_sym, std_sym, "USD")


def fetch_kraken_perp(asset, cfg):
    sym_map = SYMBOL_MAP.get(asset, {})
    perp_sym = sym_map.get("kraken_perp")
    if perp_sym is None:
        return None, None

    frate, fr_ts = kraken_futures_funding_latest(perp_sym)

    pl = None
    try:
        url = "https://futures.kraken.com/derivatives/api/v3/tickers"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        for t in data.get("tickers", []):
            if t.get("symbol") == perp_sym:
                bid = t.get("bid")
                ask = t.get("ask")
                bid = float(bid) if bid else None
                ask = float(ask) if ask else None
                pl = {
                    "venue": "kraken", "symbol": perp_sym, "quote": "USD",
                    "bid": bid, "ask": ask,
                    "mark": None, "index": None,
                    "ts_recv_ms": _recv_ms(), "ts_exchange_ms": None,
                }
                break
    except Exception:
        pass
    return pl, frate


def fetch_binance_perp(asset, cfg):
    sym_map = SYMBOL_MAP.get(asset, {})
    perp_sym = sym_map.get("binance_perp", "BTCUSDT")
    frate, fr_ts = binance_fapi_latest_funding(perp_sym)
    ticker = binance_perp_ticker(perp_sym)
    if ticker is None:
        return None, frate
    return {
        "venue": "binance", "symbol": perp_sym, "quote": "USDT",
        "bid": ticker.get("bid"), "ask": ticker.get("ask"),
        "mark": None, "index": None,
        "ts_recv_ms": ticker.get("ts_recv_ms"), "ts_exchange_ms": None,
    }, frate


def fetch_bybit_perp(asset, cfg):
    sym_map = SYMBOL_MAP.get(asset, {})
    perp_sym = sym_map.get("bybit_perp", "BTCUSDT")
    frate, fr_ts = bybit_funding_history("linear", perp_sym)
    ticker = bybit_perp_ticker("linear", perp_sym)
    if ticker is None:
        return None, frate
    return {
        "venue": "bybit", "symbol": perp_sym, "quote": "USDT",
        "bid": ticker.get("bid"), "ask": ticker.get("ask"),
        "mark": ticker.get("mark"), "index": ticker.get("index"),
        "ts_recv_ms": ticker.get("ts_recv_ms"), "ts_exchange_ms": None,
    }, frate


PERP_FETCHERS = {
    "kraken": fetch_kraken_perp,
    "binance": fetch_binance_perp,
    "bybit": fetch_bybit_perp,
}


def _perp_mid(perp_pl):
    if perp_pl is None:
        return None
    bid, ask = perp_pl.get("bid"), perp_pl.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    mark = perp_pl.get("mark")
    if mark is not None and mark > 0:
        return mark
    return None


def make_observation(asset, spot_pl, perp_pl, frate, cfg, perp_venue):
    if spot_pl.mid is None:
        return None

    spot_mid = spot_pl.mid
    p_mid = _perp_mid(perp_pl)

    basis_bps = None
    if p_mid is not None and p_mid > 0:
        basis_bps = ((p_mid - spot_mid) / spot_mid) * 10000

    funding_interval = FUNDING_INTERVAL.get(f"{perp_venue}_perp", 8.0)

    funding_apr = None
    if frate is not None and funding_interval > 0:
        funding_apr = frate * (24.0 / funding_interval) * 365.0 * 100.0

    expected_funding_bps = frate * 10000 if frate is not None else 0.0

    spot_quote = spot_pl.quote
    perp_quote = perp_pl.get("quote", "USD")
    quote_mismatch = spot_quote != perp_quote

    spot_fee = cfg.spot_taker_fee_bps
    perp_fee = _get_perp_fee(cfg, perp_venue)
    entry_fees = spot_fee + perp_fee
    exit_fees = spot_fee + perp_fee

    total_buffers = cfg.slippage_buffer_bps + cfg.latency_buffer_bps + cfg.basis_risk_buffer_bps
    if quote_mismatch:
        total_buffers += cfg.quote_mismatch_buffer_bps

    estimated_total_cost = entry_fees + exit_fees + total_buffers
    estimated_net_edge = expected_funding_bps - estimated_total_cost

    is_candidate = False
    reason = None
    if frate is not None and frate <= 0:
        reason = "funding_rate_not_positive"
    elif estimated_net_edge < cfg.min_net_edge_bps:
        reason = f"net_edge_too_low"
    elif funding_apr is not None and funding_apr < cfg.min_funding_apr:
        reason = "funding_apr_too_low"
    else:
        is_candidate = True

    if quote_mismatch:
        reason = (reason + "; ") if reason else ""
        reason += "USD_USDT_mismatch"

    return FundingObservation(
        timestamp_ms=_recv_ms(),
        asset=asset,
        spot_venue="kraken",
        perp_venue=perp_venue,
        spot_symbol=spot_pl.symbol,
        perp_symbol=perp_pl.get("symbol", ""),
        spot_bid=spot_pl.bid, spot_ask=spot_pl.ask,
        spot_mid=round(spot_mid, 4),
        perp_bid=perp_pl.get("bid"), perp_ask=perp_pl.get("ask"),
        perp_mid=round(p_mid, 4) if p_mid else None,
        mark_price=perp_pl.get("mark"),
        index_price=perp_pl.get("index"),
        quote_source="spot_perp_mid",
        basis_bps=round(basis_bps, 4) if basis_bps is not None else None,
        funding_rate=frate,
        funding_interval_hours=funding_interval,
        funding_apr=round(funding_apr, 4) if funding_apr is not None else None,
        estimated_entry_fees_bps=round(entry_fees, 2),
        estimated_exit_fees_bps=round(exit_fees, 2),
        slippage_buffer_bps=cfg.slippage_buffer_bps,
        basis_risk_buffer_bps=cfg.basis_risk_buffer_bps,
        latency_buffer_bps=cfg.latency_buffer_bps,
        quote_currency_mismatch=quote_mismatch,
        quote_mismatch_buffer_bps=cfg.quote_mismatch_buffer_bps if quote_mismatch else 0.0,
        estimated_total_cost_bps=round(estimated_total_cost, 2),
        expected_funding_bps=round(expected_funding_bps, 4),
        estimated_net_edge_bps=round(estimated_net_edge, 4),
        opportunity_type="cash_and_carry",
        is_candidate=is_candidate,
        reason_if_rejected=reason,
    )


def run_funding_scan(cfg):
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    obs_file = out_dir / "funding_basis_observations.jsonl"

    stats = {
        "polls": 0, "observations": 0, "candidates": 0,
        "candidates_by_asset": {}, "candidates_by_venue_pair": {},
        "errors_by_venue": {}, "rejection_reasons": {},
        "stale_quotes": 0,
    }

    start = time.time()
    deadline = start + cfg.duration_seconds

    with open(obs_file, "w") as fh:
        while time.time() < deadline:
            stats["polls"] += 1

            for asset in cfg.assets:
                spot_pl = fetch_spot(asset, cfg)
                if spot_pl is None or spot_pl.mid is None:
                    stats["errors_by_venue"]["kraken_spot"] = 1 + stats["errors_by_venue"].get("kraken_spot", 0)
                    continue

                for perp_venue in cfg.perp_venues:
                    fetcher = PERP_FETCHERS.get(perp_venue)
                    if fetcher is None:
                        continue
                    try:
                        perp_pl, frate = fetcher(asset, cfg)
                    except Exception:
                        stats["errors_by_venue"][perp_venue] = 1 + stats["errors_by_venue"].get(perp_venue, 0)
                        continue

                    if perp_pl is None:
                        stats["errors_by_venue"][perp_venue] = 1 + stats["errors_by_venue"].get(perp_venue, 0)
                        continue

                    obs = make_observation(asset, spot_pl, perp_pl, frate, cfg, perp_venue)
                    if obs is None:
                        continue

                    write_observation(obs, fh)
                    stats["observations"] += 1

                    if obs.is_candidate:
                        stats["candidates"] += 1
                        stats["candidates_by_asset"][asset] = 1 + stats["candidates_by_asset"].get(asset, 0)
                        pair_key = f"{obs.spot_venue}-{obs.perp_venue}"
                        stats["candidates_by_venue_pair"][pair_key] = 1 + stats["candidates_by_venue_pair"].get(pair_key, 0)
                        print(f"  CANDIDATE: {asset} {obs.spot_venue}->{obs.perp_venue} "
                              f"frate={obs.funding_rate*100:.4f}% apr={obs.funding_apr:.1f}% "
                              f"net={obs.estimated_net_edge_bps:.1f}bps basis={obs.basis_bps:.1f}bps")

                    if obs.reason_if_rejected:
                        key = obs.reason_if_rejected.split(";")[0].strip()
                        stats["rejection_reasons"][key] = 1 + stats["rejection_reasons"].get(key, 0)

            time.sleep(cfg.poll_interval_seconds)

    summary_path = write_summary(stats, obs_file, out_dir)
    return summary_path, stats
