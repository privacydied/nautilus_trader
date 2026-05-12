"""Signal observer — ties together signals, forward returns, and reporting."""
import json
import time
from pathlib import Path
from typing import List, Optional

from .config import ObserverConfig, Horizon
from .models import SignalEvent, ForwardReturnResult, SignalEvaluationSummary, HorizonSummary
from .signals import load_signals_from_csv, CrossMarketSignalGenerator
from .forward_returns import evaluate_signal
from .data_loading import load_bars_from_csv, generate_synthetic_data
from .reports import write_outputs


class SignalObserver:
    """Venue-agnostic signal forward-return observer."""

    def __init__(self, cfg: ObserverConfig):
        self.cfg = cfg

    def run(
        self,
        signals: Optional[List[SignalEvent]] = None,
        signals_csv: Optional[str] = None,
        bars_csv: Optional[str] = None,
        source_timestamps: Optional[List[float]] = None,
        source_prices: Optional[List[float]] = None,
        target_timestamps: Optional[List[float]] = None,
        target_prices: Optional[List[float]] = None,
    ):
        """Run the observer.

        Signal sources are mutually exclusive in priority:
        1. ``signals`` list passed directly
        2. ``signals_csv`` path
        3. ``source_timestamps`` + ``source_prices`` (cross-market generator)

        Price data sources are mutually exclusive:
        1. ``target_timestamps`` + ``target_prices`` passed directly
        2. ``bars_csv`` path
        """
        run_start = time.time()

        # --- Load signals ---
        if signals is not None:
            all_signals = list(signals)
        elif signals_csv:
            all_signals = load_signals_from_csv(signals_csv)
            print(f"Loaded {len(all_signals)} signals from {signals_csv}")
        else:
            # Generate signals from cross-market source
            if source_timestamps is None or source_prices is None:
                raise ValueError("Cross-market signals require source_timestamps + source_prices")
            gen = CrossMarketSignalGenerator(self.cfg.signal_source)
            all_signals = gen.generate(source_timestamps, source_prices)
            print(f"Generated {len(all_signals)} cross-market signals")

        # --- Load target price data ---
        if target_timestamps is not None and target_prices is not None:
            ts, prices = target_timestamps, target_prices
        elif bars_csv:
            ts, prices = load_bars_from_csv(bars_csv)
            print(f"Loaded {len(ts)} bars from {bars_csv}")
        else:
            raise ValueError("Must provide target_timestamps + target_prices or bars_csv")

        # --- Check quote currency mismatch ---
        quote_mismatch = (
            self.cfg.apply_quote_mismatch_buffer
            and self.cfg.quote_source_currency != self.cfg.quote_target_currency
        )

        # --- Evaluate signals ---
        all_results: List[ForwardReturnResult] = []
        all_signal_dicts = []

        for sig in all_signals:
            all_signal_dicts.append(sig.to_dict())
            results = evaluate_signal(
                sig, ts, prices,
                self.cfg.horizons,
                self.cfg.fee_model,
                quote_mismatch,
            )
            all_results.extend(results)

        run_end = time.time()

        # --- Summarise ---
        summary = _build_summary(
            all_signals, all_results, self.cfg, run_start, run_end, quote_mismatch,
        )

        return all_signals, all_results, summary


def _build_summary(
    signals: List[SignalEvent],
    results: List[ForwardReturnResult],
    cfg: ObserverConfig,
    run_start: float,
    run_end: float,
    quote_mismatch: bool,
) -> SignalEvaluationSummary:
    """Aggregate results into a SignalEvaluationSummary."""
    import statistics

    summary = SignalEvaluationSummary(
        run_start=run_start,
        run_end=run_end,
        source_venues=list(set(s.source_venue for s in signals)),
        target_venues=list(set(s.target_venue for s in signals)),
        instruments=list(set(s.target_instrument for s in signals)),
        horizons=[h.name for h in cfg.horizons],
        total_signals=len(signals),
        fee_bps=cfg.fee_model.fee_bps,
        slippage_bps=cfg.fee_model.slippage_bps,
        quote_mismatch_buffer_bps=cfg.fee_model.quote_mismatch_buffer_bps if quote_mismatch else 0.0,
    )

    valid = [r for r in results if r.valid]
    rejected = [r for r in results if not r.valid]
    summary.valid_evaluations = len(valid)
    summary.rejected_evaluations = len(rejected)

    # By horizon
    horizon_stats = []
    for h in cfg.horizons:
        h_results = [r for r in valid if r.horizon == h.name]
        net_returns = [r.net_return_bps for r in h_results if r.net_return_bps is not None]
        raw_returns = [r.raw_return_bps for r in h_results if r.raw_return_bps is not None]
        if net_returns:
            net_returns.sort()
            win_rate = sum(1 for x in net_returns if x > 0) / len(net_returns)
            hs = HorizonSummary(
                horizon=h.name,
                event_count=len([r for r in results if r.horizon == h.name]),
                valid_count=len(h_results),
                rejected_count=len([r for r in rejected if r.horizon == h.name]),
                mean_raw_return_bps=round(statistics.mean(raw_returns), 4) if raw_returns else None,
                median_raw_return_bps=round(statistics.median(raw_returns), 4) if raw_returns else None,
                mean_net_return_bps=round(statistics.mean(net_returns), 4),
                median_net_return_bps=round(statistics.median(net_returns), 4),
                win_rate_after_fees=round(win_rate, 4),
                p25=round(net_returns[len(net_returns) // 4], 4),
                p50=round(net_returns[len(net_returns) // 2], 4),
                p75=round(net_returns[3 * len(net_returns) // 4], 4),
                p90=round(net_returns[9 * len(net_returns) // 10], 4),
                best_return=round(max(net_returns), 4),
                worst_return=round(min(net_returns), 4),
            )
        else:
            hs = HorizonSummary(
                horizon=h.name,
                event_count=len([r for r in results if r.horizon == h.name]),
                valid_count=0,
                rejected_count=len([r for r in rejected if r.horizon == h.name]),
            )
        horizon_stats.append(hs.__dict__)

    summary.results_by_horizon = horizon_stats

    # By signal type
    by_type = {}
    for r in valid:
        by_type.setdefault(r.signal_type, []).append(r)
    for stype, type_results in by_type.items():
        nets = [r.net_return_bps for r in type_results if r.net_return_bps is not None]
        if nets:
            win_rate = sum(1 for x in nets if x > 0) / len(nets)
            summary.results_by_signal_type.append({
                "signal_type": stype,
                "event_count": len(type_results),
                "mean_net_return_bps": round(statistics.mean(nets), 4),
                "win_rate_after_fees": round(win_rate, 4),
            })
        else:
            summary.results_by_signal_type.append({
                "signal_type": stype,
                "event_count": len(type_results),
            })

    # Best/worst signal group
    groups = {s["signal_type"]: s.get("mean_net_return_bps")
              for s in summary.results_by_signal_type
              if s.get("mean_net_return_bps") is not None}
    if groups:
        summary.best_signal_group = max(groups, key=groups.get)
        summary.worst_signal_group = min(groups, key=groups.get)

    # Final recommendation
    overall_nets = [r.net_return_bps for r in valid if r.net_return_bps is not None]
    if overall_nets:
        mean_net = statistics.mean(overall_nets)
        if mean_net > 0:
            summary.final_recommendation = (
                f"Positive exploratory mean net return ({mean_net:.2f} bps). "
                "Requires deeper analysis before considering execution."
            )
        else:
            summary.final_recommendation = (
                f"Negative mean net return ({mean_net:.2f} bps). "
                "Signal does not show forward expectancy after costs."
            )
    else:
        summary.final_recommendation = "No valid evaluations. Check data quality."

    return summary
