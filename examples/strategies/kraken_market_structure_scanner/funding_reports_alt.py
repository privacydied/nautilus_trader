"""V6-C: Altcoin funding/basis report writing."""
import json
from pathlib import Path
from typing import Dict, Optional
from .funding_models_alt import FundingObservationAlt, PersistenceTracker
from .funding_config import FundingConfig


def write_observation_alt(obs: FundingObservationAlt, fh) -> None:
    """Append one observation to JSONL."""
    row = {k: v for k, v in obs.__dict__.items()}
    fh.write(json.dumps(row) + "\n")
    fh.flush()


class CandidateState:
    """Tracks candidate persistence for an asset+venue combo."""

    def __init__(self, asset: str, perp_venue: str, min_polls: int):
        self.asset = asset
        self.perp_venue = perp_venue
        self.min_polls = min_polls
        self.consecutive = 0
        self.max_consecutive = 0
        self.first_seen_ms = 0
        self.last_seen_ms = 0
        self.last_observation: Optional[FundingObservationAlt] = None
        self.is_durable = False

    def poll(self, obs: FundingObservationAlt):
        is_candidate = obs.candidate
        if is_candidate:
            if self.first_seen_ms == 0:
                self.first_seen_ms = obs.timestamp_ms
                self.consecutive = 1
            else:
                self.consecutive += 1
            self.last_seen_ms = obs.timestamp_ms
            self.last_observation = obs
            if self.consecutive > self.max_consecutive:
                self.max_consecutive = self.consecutive
            if self.max_consecutive >= self.min_polls:
                self.is_durable = True
        else:
            if self.consecutive > self.max_consecutive:
                self.max_consecutive = self.consecutive
            self.consecutive = 0


def write_candidate_alt(obs: FundingObservationAlt, fh) -> None:
    """Append one candidate record to candidate JSONL."""
    row = {
        "timestamp_ms": obs.timestamp_ms,
        "asset": obs.asset,
        "spot_venue": obs.spot_venue,
        "perp_venue": obs.perp_venue,
        "spot_symbol": obs.spot_symbol,
        "perp_symbol": obs.perp_symbol,
        "funding_rate": obs.funding_rate,
        "funding_apr": obs.funding_apr,
        "funding_interval_hours": obs.funding_interval_hours,
        "basis_bps": obs.basis_bps,
        "conservative_net_edge_bps": obs.conservative_net_edge_bps,
        "mixed_net_edge_bps": obs.mixed_net_edge_bps,
        "optimistic_net_edge_bps": obs.optimistic_net_edge_bps,
        "durable_candidate": obs.durable_candidate,
    }
    fh.write(json.dumps(row) + "\n")
    fh.flush()


def write_summary_alt(
    stats: Dict,
    obs_file: Path,
    cand_file: Path,
    output_dir: Path,
    cfg: FundingConfig,
) -> Path:
    """Write funding_basis_alt_summary.json with full statistics."""
    # Compute aggregate stats from observation JSONL
    max_funding_apr = None
    median_funding_apr = None
    max_basis = None
    median_basis = None
    top10_obs = []
    aprs = []
    basis_vals = []

    all_venues_set = set()
    all_assets_set = set()
    missing_counts = {}

    if obs_file.exists():
        with open(obs_file) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r.get("funding_apr") is not None:
                        aprs.append(r["funding_apr"])
                    if r.get("basis_bps") is not None:
                        basis_vals.append(r["basis_bps"])
                    top10_obs.append(r)
                    all_venues_set.add(r.get("perp_venue"))
                    all_assets_set.add(r.get("asset"))
                    if r.get("rejection_reason"):
                        rr = r["rejection_reason"].split(";")[0].strip()
                        missing_counts[rr] = missing_counts.get(rr, 0) + 1

    # Sort for top-10 by conservative net edge (desc)
    top10_obs.sort(
        key=lambda x: x.get("conservative_net_edge_bps", -999999),
        reverse=True,
    )
    top10_obs = top10_obs[:10]

    # Top 10 durable candidates
    top10_durable = []
    if cand_file.exists():
        with open(cand_file) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    top10_durable.append(r)
        top10_durable.sort(
            key=lambda x: x.get("conservative_net_edge_bps", -999999),
            reverse=True,
        )
        top10_durable = top10_durable[:10]

    if aprs:
        aprs.sort()
        max_funding_apr = aprs[-1]
        median_funding_apr = aprs[len(aprs) // 2]
    if basis_vals:
        basis_vals.sort()
        max_basis = basis_vals[-1]
        median_basis = basis_vals[len(basis_vals) // 2]

    summary = {
        "scan_start": stats.get("scan_start"),
        "scan_end": stats.get("scan_end"),
        "assets_scanned": list(all_assets_set) if all_assets_set else cfg.assets,
        "venues_scanned": list(all_venues_set) if all_venues_set else cfg.perp_venues,
        "observations_count": stats.get("observations", 0),
        "candidate_count": stats.get("candidates", 0),
        "durable_candidate_count": stats.get("durable_candidates", 0),
        "candidates_by_asset": stats.get("candidates_by_asset", {}),
        "candidates_by_venue": stats.get("candidates_by_venue", {}),
        "max_funding_apr": max_funding_apr,
        "median_funding_apr": median_funding_apr,
        "max_basis_bps": max_basis,
        "median_basis_bps": median_basis,
        "max_conservative_net_edge_bps": stats.get("max_conservative_net", None),
        "rejected_reason_counts": stats.get("rejection_reasons", missing_counts),
        "stale_quotes_count": stats.get("stale_quotes", 0),
        "missing_spot_count": stats.get("missing_spot", 0),
        "missing_perp_count": stats.get("missing_perp", 0),
        "quote_mismatch_count": stats.get("quote_mismatch_count", 0),
        "top_10_observations_by_conservative_net": top10_obs,
        "top_10_durable_candidates": top10_durable,
    }

    out = output_dir / "funding_basis_alt_summary.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    return out
