"""V6: Market Structure Scanner — polling loop."""

import json
import time
from pathlib import Path

from .config import ScannerConfig, FeeConfig
from .symbols import get_spec
from .venues import FETCHERS, Ticker
from .opportunity import calculate_opportunity


class Scanner:
    def __init__(self, cfg: ScannerConfig, fees: FeeConfig):
        self.cfg = cfg
        self.fees = fees
        self.stats = {
            "polls": 0, "errors": 0, "opportunities": 0,
            "opportunities_by_symbol": {},
            "opportunities_by_pair": {},
            "errors_by_venue": {},
        }

    def _fetch_tickers(self):
        result = {}  # symbol -> {venue: Ticker}
        for symbol in self.cfg.symbols:
            spec = get_spec(symbol)
            result[symbol] = {}
            for venue in self.cfg.venues:
                fetcher = FETCHERS.get(venue)
                if fetcher is None:
                    continue
                api_sym = getattr(spec, venue, None)
                if api_sym is None:
                    continue
                ticker = fetcher(api_sym, symbol, spec.quote)
                if ticker:
                    result[symbol][venue] = ticker
                else:
                    self.stats["errors_by_venue"][venue] = 1 + self.stats["errors_by_venue"].get(venue, 0)
                    self.stats["errors"] += 1
        return result

    def _check_opportunities(self, tickers_by_sym):
        opportunities = []
        fee_map = {"kraken": self.fees.kraken, "coinbase": self.fees.coinbase, "binance": self.fees.binance}
        for symbol, venues_tickers in tickers_by_sym.items():
            venue_list = list(venues_tickers.keys())
            for i in range(len(venue_list)):
                for j in range(i + 1, len(venue_list)):
                    t1 = venues_tickers[venue_list[i]]
                    t2 = venues_tickers[venue_list[j]]
                    fee1 = fee_map.get(t1.venue, 40.0)
                    fee2 = fee_map.get(t2.venue, 40.0)
                    opp = calculate_opportunity(t1, t2, fee1, fee2, self.cfg.latency_buffer_bps)
                    if opp:
                        opportunities.append(opp)
                        self.stats["opportunities"] += 1
                        self.stats["opportunities_by_symbol"][symbol] = 1 + self.stats["opportunities_by_symbol"].get(symbol, 0)
                        pair_key = f"{min(t1.venue, t2.venue)}-{max(t1.venue, t2.venue)}"
                        self.stats["opportunities_by_pair"][pair_key] = 1 + self.stats["opportunities_by_pair"].get(pair_key, 0)
        return opportunities

    def run(self, out_dir: str, max_runtime: float = None, log_all: bool = False):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        opp_log = out / "opportunities.jsonl"

        start = time.time()
        deadline = start + max_runtime if max_runtime else float("inf")

        with open(opp_log, "w") as log_f:
            while time.time() < deadline:
                self.stats["polls"] += 1
                tickers = self._fetch_tickers()
                opps = self._check_opportunities(tickers)

                for o in opps:
                    row = {k: v for k, v in o.__dict__.items()}
                    log_f.write(json.dumps(row) + "\n")
                    log_f.flush()

                if log_all and opps:
                    for o in opps:
                        print(f"  OPP: {o.symbol} {o.buy_venue}->{o.sell_venue} "
                              f"edge={o.gross_edge_bps:.2f}bps net={o.net_edge_bps:.2f}bps")

                time.sleep(self.cfg.poll_interval_seconds)

        # Write summary
        best_net = None
        nets = []
        with open(opp_log) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    nets.append(r["net_edge_bps"])
        if nets:
            best_net = max(nets)

        summary = {
            "scan_start": start,
            "scan_duration_seconds": round(time.time() - start, 1),
            "venues_scanned": self.cfg.venues,
            "symbols_scanned": self.cfg.symbols,
            "total_polls": self.stats["polls"],
            "total_opportunities": self.stats["opportunities"],
            "total_data_errors": self.stats["errors"],
            "errors_by_venue": self.stats["errors_by_venue"],
            "opportunities_by_symbol": self.stats["opportunities_by_symbol"],
            "opportunities_by_pair": self.stats["opportunities_by_pair"],
            "max_net_edge_bps": best_net,
            "max_is_profitable": best_net and best_net > 0,
        }
        with open(out / "summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        return summary
