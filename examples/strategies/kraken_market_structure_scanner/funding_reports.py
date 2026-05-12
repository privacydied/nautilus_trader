"""V6-B: Funding/Basis report writing."""
import json
from pathlib import Path
from typing import List, Dict
from .funding_models import FundingObservation


def write_observation(opp: FundingObservation, fh) -> None:
    """Append one observation as JSON."""
    row = {k: v for k, v in opp.__dict__.items()}
    fh.write(json.dumps(row) + "\n")
    fh.flush()


def write_summary(stats: Dict, obs_file: Path, output_dir: Path) -> Path:
    """Write funding_basis_summary.json."""
    # Compute additional stats from JSONL if file exists
    max_funding_apr = None
    median_funding_apr = None
    max_basis = None
    median_basis = None
    max_net_edge = None
    median_net_edge = None
    aprs = []
    basis_vals = []
    net_edges = []

    if obs_file.exists():
        with open(obs_file) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r.get("funding_apr") is not None:
                        aprs.append(r["funding_apr"])
                    if r.get("basis_bps") is not None:
                        basis_vals.append(r["basis_bps"])
                    if r.get("estimated_net_edge_bps") is not None:
                        net_edges.append(r["estimated_net_edge_bps"])

    if aprs:
        aprs.sort()
        max_funding_apr = aprs[-1]
        median_funding_apr = aprs[len(aprs) // 2]
    if basis_vals:
        basis_vals.sort()
        max_basis = basis_vals[-1]
        median_basis = basis_vals[len(basis_vals) // 2]
    if net_edges:
        net_edges.sort()
        max_net_edge = net_edges[-1]
        median_net_edge = net_edges[len(net_edges) // 2]

    summary = {
        **stats,
        "max_funding_apr": max_funding_apr,
        "median_funding_apr": median_funding_apr,
        "max_basis_bps": max_basis,
        "median_basis_bps": median_basis,
        "max_estimated_net_edge_bps": max_net_edge,
        "median_estimated_net_edge_bps": median_net_edge,
    }
    out = output_dir / "funding_basis_summary.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    return out
