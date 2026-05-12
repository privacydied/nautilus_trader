"""V6-C: Altcoin funding/basis scanner — polling loop with persistence and depth."""
import time
from pathlib import Path
import json
import requests
from typing import Optional, Dict, Any

from .funding_config import FundingConfig, SYMBOL_MAP, QUOTE_MAP, FUNDING_INTERVAL
from .funding_models_alt import (
    FundingObservationAlt,
    CostScenario,
    PersistenceTracker,
    make_cost_scenarios,
)
from .funding_reports_alt import (
    write_observation_alt,
    write_candidate_alt,
    write_summary_alt,
    CandidateState,
)
from .funding_venues import (
    kraken_spot_ticker,
    binance_fapi_latest_funding,
    binance_perp_ticker,
    bybit_funding_history,
    bybit_perp_ticker,
)


def _recv_ms() -> int:
    return int(time.time() * 1000)


def fetch_kraken_spot_symbol(asset: str) -> Optional[str]:
    sym = SYMBOL_MAP.get(asset, {}).get("kraken_spot")
    return sym if sym else None


def fetch_kraken_spot(asset: str):
    sym = fetch_kraken_spot_symbol(asset)
    if sym is None:
        return None
    return kraken_spot_ticker(sym, f"{asset}/USD", "USD")


def fetch_perp_funding_and_ticker(asset: str, perp_venue: str):
    """Return (perp_pl, funding_rate) or (None, None)."""
    asset_map = SYMBOL_MAP.get(asset, {})
    perp_sym = asset_map.get(f"{perp_venue}_perp")
    if perp_sym is None:
        return None, None

    if perp_venue == "binance":
        frate, _ = binance_fapi_latest_funding(perp_sym)
        ticker = binance_perp_ticker(perp_sym)
        if ticker is None:
            return None, frate
        p_mid = _perp_mid(ticker)
        return {
            "venue": "binance", "symbol": perp_sym, "quote": "USDT",
            "bid": ticker.get("bid"), "ask": ticker.get("ask"),
            "mid": p_mid,
            "mark": None, "index": None,
            "ts_recv_ms": ticker.get("ts_recv_ms"), "ts_exchange_ms": None,
        }, frate

    elif perp_venue == "bybit":
        frate, _ = bybit_funding_history("linear", perp_sym)
        ticker = bybit_perp_ticker("linear", perp_sym)
        if ticker is None:
            return None, frate
        p_mid = _perp_mid(ticker)
        return {
            "venue": "bybit", "symbol": perp_sym, "quote": "USDT",
            "bid": ticker.get("bid"), "ask": ticker.get("ask"),
            "mid": p_mid,
            "mark": ticker.get("mark"), "index": ticker.get("index"),
            "ts_recv_ms": ticker.get("ts_recv_ms"), "ts_exchange_ms": None,
            "bid_size": None, "ask_size": None,
        }, frate

    elif perp_venue == "kraken":
        sym_map = SYMBOL_MAP.get(asset, {})
        perp_sym_k = sym_map.get("kraken_perp")
        if perp_sym_k is None:
            return None, None
        try:
            url = "https://futures.kraken.com/derivatives/api/v3/tickers"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            for t in data.get("tickers", []):
                if t.get("symbol") == perp_sym_k:
                    bid = t.get("bid")
                    ask = t.get("ask")
                    bid = float(bid) if bid else None
                    ask = float(ask) if ask else None
                    p_mid = (bid + ask) / 2.0 if bid and ask and bid > 0 else None
                    return {
                        "venue": "kraken", "symbol": perp_sym_k, "quote": "USD",
                        "bid": bid, "ask": ask, "mid": p_mid,
                        "mark": None, "index": None,
                        "ts_recv_ms": _recv_ms(), "ts_exchange_ms": None,
                    }, None
        except Exception:
            pass
        return None, None

    return None, None


def _perp_mid(perp_pl: Optional[dict]) -> Optional[float]:
    if perp_pl is None:
        return None
    bid, ask = perp_pl.get("bid"), perp_pl.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    mark = perp_pl.get("mark")
    if mark is not None and mark > 0:
        return mark
    return None


def _compute_net_edges(
    frate: Optional[float],
    scenarios: Dict[str, CostScenario],
    has_mismatch: bool,
) -> dict:
    """Return dict of scenario_name -> net_edge_bps for all three."""
    edges = {}
    for name, scenario in scenarios.items():
        cost = scenario.total_cost_bps(round_trip=True, has_mismatch=has_mismatch)
        expected_funding_bps = frate * 10000 if frate is not None else 0.0
        edges[name] = round(expected_funding_bps - cost, 4)
    return edges


def make_observation_alt(
    asset: str,
    spot_pl,
    perp_pl: Optional[dict],
    frate: Optional[float],
    cfg: FundingConfig,
    perp_venue: str,
    scenarios: Dict[str, CostScenario],
    stale_quote_max_age_ms: int = 30_000,
) -> Optional[FundingObservationAlt]:
    if spot_pl.mid is None or perp_pl is None:
        p_mid = _perp_mid(perp_pl)
        if p_mid is None:
            return None
    else:
        p_mid = _perp_mid(perp_pl)

    ts_ms = _recv_ms()
    spot_mid = spot_pl.mid

    # Basis
    basis_bps = None
    if p_mid is not None and p_mid > 0 and spot_mid > 0:
        basis_bps = ((p_mid - spot_mid) / spot_mid) * 10000

    # Funding APR
    funding_interval = FUNDING_INTERVAL.get(f"{perp_venue}_perp", 8.0)
    funding_apr = None
    if frate is not None and funding_interval > 0:
        funding_apr = frate * (24.0 / funding_interval) * 365.0 * 100.0

    # Quote mismatch
    spot_quote = spot_pl.quote
    perp_quote = perp_pl.get("quote", "USD")
    has_mismatch = spot_quote != perp_quote

    # Three cost scenarios
    expected_funding_bps = frate * 10000 if frate is not None else 0.0
    net_edges = _compute_net_edges(frate, scenarios, has_mismatch)

    # Cost details
    cons = scenarios["conservative_taker"]
    mixed = scenarios["mixed_maker_taker"]
    opt = scenarios["optimistic_maker"]

    # Candidate logic: check conservative scenario
    candidate = False
    rejection_reason = None
    if frate is not None and frate <= 0:
        rejection_reason = "funding_rate_not_positive"
    elif has_mismatch:
        rejection_reason = "USD_USDT_mismatch"
    elif funding_apr is not None and funding_apr < cfg.min_funding_apr:
        rejection_reason = "funding_apr_too_low"
    elif net_edges["conservative_taker"] < cfg.min_net_edge_bps:
        rejection_reason = "net_edge_too_low"
    else:
        candidate = True

    quote_age_ms = None
    if perp_pl.get("ts_exchange_ms") and spot_pl.ts_exchange_ms:
        quote_age_ms = max(
            ts_ms - perp_pl["ts_exchange_ms"],
            ts_ms - spot_pl.ts_exchange_ms,
        )
    quote_stale = (
        quote_age_ms > stale_quote_max_age_ms if quote_age_ms else False
    )

    return FundingObservationAlt(
        timestamp_ms=ts_ms,
        asset=asset,
        spot_venue="kraken",
        perp_venue=perp_venue,
        spot_symbol=spot_pl.symbol,
        perp_symbol=perp_pl.get("symbol", ""),
        quote_source=perp_quote,
        quote_mismatch=has_mismatch,
        spot_bid=spot_pl.bid,
        spot_ask=spot_pl.ask,
        spot_mid=round(spot_mid, 4),
        perp_bid=perp_pl.get("bid"),
        perp_ask=perp_pl.get("ask"),
        perp_mid=round(p_mid, 4) if p_mid else None,
        mark_price=perp_pl.get("mark"),
        index_price=perp_pl.get("index"),
        spot_bid_size=spot_pl.bid_size if hasattr(spot_pl, 'bid_size') else None,
        spot_ask_size=spot_pl.ask_size if hasattr(spot_pl, 'ask_size') else None,
        perp_bid_size=perp_pl.get("bid_size"),
        perp_ask_size=perp_pl.get("ask_size"),
        basis_bps=round(basis_bps, 4) if basis_bps is not None else None,
        funding_rate=frate,
        funding_interval_hours=funding_interval,
        funding_apr=round(funding_apr, 4) if funding_apr is not None else None,
        conservative_taker_cost_bps=round(cons.total_cost_bps(True, has_mismatch), 2),
        mixed_maker_taker_cost_bps=round(mixed.total_cost_bps(True, has_mismatch), 2),
        optimistic_maker_cost_bps=round(opt.total_cost_bps(True, has_mismatch), 2),
        conservative_net_edge_bps=net_edges["conservative_taker"],
        mixed_net_edge_bps=net_edges["mixed_maker_taker"],
        optimistic_net_edge_bps=net_edges["optimistic_maker"],
        candidate=candidate,
        durable_candidate=False,  # set later by persistence tracker
        rejection_reason=rejection_reason,
        quote_stale=quote_stale,
        quote_age_ms=quote_age_ms,
    )


class AltFundingScanner:
    """V6-C Altcoin funding anomaly scanner with persistence tracking."""

    def __init__(self, cfg: FundingConfig):
        self.cfg = cfg
        # Filter assets to only those with at least one perp venue map
        self.assets = []
        for a in cfg.assets:
            m = SYMBOL_MAP.get(a, {})
            for pv in cfg.perp_venues:
                key = f"{pv}_perp"
                if m.get(key):
                    self.assets.append(a)
                    break
        # Spot venues: only Kraken for now, but must have spot mapping
        self.assets = [
            a for a in self.assets
            if SYMBOL_MAP.get(a, {}).get("kraken_spot")
        ]

        self.scenarios = make_cost_scenarios()
        self.persistence: Dict[str, PersistenceTracker] = {}
        self.candidate_state: Dict[str, CandidateState] = {}  # key for durable tracking

        self.stats = {
            "polls": 0,
            "observations": 0,
            "candidates": 0,
            "durable_candidates": 0,
            "candidates_by_asset": {},
            "candidates_by_venue": {},
            "errors_by_venue": {},
            "rejection_reasons": {},
            "stale_quotes": 0,
            "missing_spot": 0,
            "missing_perp": 0,
            "missing_mapping": 0,
            "quote_mismatch_count": 0,
        }

    def _tracker_key(self, asset: str, perp_venue: str) -> str:
        return f"{asset}:{perp_venue}"

    def run(self):
        out_dir = Path(self.cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        obs_file = out_dir / "funding_basis_alt_observations.jsonl"
        cand_file = out_dir / "funding_basis_alt_candidates.jsonl"

        start = time.time()
        deadline = start + self.cfg.duration_seconds

        with open(obs_file, "w") as obs_fh, open(cand_file, "w") as cand_fh:
            while time.time() < deadline:
                self.stats["polls"] += 1

                for asset in self.assets:
                    spot_pl = fetch_kraken_spot(asset)
                    if spot_pl is None or spot_pl.mid is None:
                        self.stats["missing_spot"] += 1
                        self.stats["errors_by_venue"]["kraken_spot"] = \
                            1 + self.stats["errors_by_venue"].get("kraken_spot", 0)
                        continue

                    for perp_venue in self.cfg.perp_venues:
                        key = self._tracker_key(asset, perp_venue)

                        if key not in self.persistence:
                            self.persistence[key] = PersistenceTracker(
                                asset=asset, perp_venue=perp_venue,
                            )
                        tracker = self.persistence[key]

                        perp_pl, frate = fetch_perp_funding_and_ticker(asset, perp_venue)
                        if perp_pl is None:
                            tracker.poll_non_candidate()
                            self.stats["missing_perp"] += 1
                            self.stats["errors_by_venue"][perp_venue] = \
                                1 + self.stats["errors_by_venue"].get(perp_venue, 0)
                            obs = make_observation_alt(
                                asset, spot_pl, perp_pl, frate,
                                self.cfg, perp_venue, self.scenarios,
                            )
                            if obs:
                                write_observation_alt(obs, obs_fh)
                                self.stats["observations"] += 1
                            continue

                        obs = make_observation_alt(
                            asset, spot_pl, perp_pl, frate,
                            self.cfg, perp_venue, self.scenarios,
                        )
                        if obs is None:
                            self.stats["missing_perp"] += 1
                            tracker.poll_non_candidate()
                            continue

                        # Persistence tracking
                        if obs.candidate:
                            tracker.poll_candidate(obs.timestamp_ms)
                            obs = obs  # already has candidate=True
                        else:
                            tracker.poll_non_candidate(obs.timestamp_ms)

                        # Check durability
                        if tracker.is_durable(self.cfg.min_persistence_polls):
                            obs = obs.__class__(**{
                                **{k: getattr(obs, k) for k in obs.__dataclass_fields__},
                                "durable_candidate": True,
                            })

                        write_observation_alt(obs, obs_fh)
                        self.stats["observations"] += 1

                        if obs.quote_mismatch:
                            self.stats["quote_mismatch_count"] += 1
                        if obs.quote_stale:
                            self.stats["stale_quotes"] += 1

                        if obs.candidate:
                            self.stats["candidates"] += 1
                            self.stats["candidates_by_asset"][asset] = \
                                1 + self.stats["candidates_by_asset"].get(asset, 0)
                            self.stats["candidates_by_venue"][perp_venue] = \
                                1 + self.stats["candidates_by_venue"].get(perp_venue, 0)

                            # Write candidate line
                            write_candidate_alt(obs, cand_fh)

                            if obs.durable_candidate:
                                self.stats["durable_candidates"] += 1
                                print(f"  DURABLE CANDIDATE: {asset} {perp_venue} "
                                      f"frate={obs.funding_rate*100:.4f}% "
                                      f"apr={obs.funding_apr:.1f}% "
                                      f"cons_net={obs.conservative_net_edge_bps:.1f}bps "
                                      f"consecutive={tracker.consecutive_count}")

                            print(f"  CANDIDATE: {asset} {perp_venue} "
                                  f"frate={obs.funding_rate*100:.4f}% "
                                  f"apr={obs.funding_apr:.1f}% "
                                  f"cons_net={obs.conservative_net_edge_bps:.1f}bps "
                                  f"mixed_net={obs.mixed_net_edge_bps:.1f}bps "
                                  f"opt_net={obs.optimistic_net_edge_bps:.1f}bps "
                                  f"basis={obs.basis_bps:.1f}bps "
                                  f"consecutive={tracker.consecutive_count}")

                        if obs.rejection_reason:
                            r = obs.rejection_reason.split(";")[0].strip()
                            self.stats["rejection_reasons"][r] = \
                                1 + self.stats["rejection_reasons"].get(r, 0)

                time.sleep(self.cfg.poll_interval_seconds)

        summary = write_summary_alt(
            self.stats, obs_file, cand_file,
            out_dir, self.cfg,
        )
        return summary, self.stats
