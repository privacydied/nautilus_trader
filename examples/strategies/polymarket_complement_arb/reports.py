"""
Report generation for Polymarket complement arb strategy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .config import ComplementArbConfig
from .models import ComplementMarket, MarketSkipReason, OpportunityDiagnostic, PassiveFillEstimate


def compute_config_hash(config: ComplementArbConfig) -> str:
    """Compute a stable hash of the configuration."""
    raw = json.dumps(vars(config), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def generate_run_id() -> str:
    """Generate a run ID from current timestamp."""
    return datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_%f")


def generate_run_summary(
    run_id: str,
    git_sha: str,
    mode: str,
    config: ComplementArbConfig,
    markets_discovered: list[ComplementMarket],
    skipped_reasons: list[MarketSkipReason],
    opportunities: list[OpportunityDiagnostic],
    rejected_opportunities: list[OpportunityDiagnostic],
    passive_estimates: list[PassiveFillEstimate],
    passive_summary: dict[str, Any],
    adapter_implementation: str,
    depth_mode: str,
    passive_estimate_source: str,
    run_duration_secs: float,
    **kwargs,
) -> str:
    """Generate a run summary markdown document."""

    # Count skip reasons
    skip_count: dict[str, int] = {}
    for s in skipped_reasons:
        reason = s.reason.split("(")[0].strip()
        skip_count[reason] = skip_count.get(reason, 0) + 1

    gm_pass = sum(1 for o in opportunities if o.maker_gate_pass)
    tk_pass = sum(1 for o in opportunities if o.taker_diagnostic_pass)
    eligible_count = len(markets_discovered)

    # Edge stats
    edges = [o.net_edge for o in opportunities if o.net_edge is not None]
    gross_gaps = [o.gross_gap for o in opportunities]

    lines = []
    lines.append(f"# Run Summary: Polymarket Complement Arb")
    lines.append(f"")
    lines.append(f"- **run_id**: `{run_id}`")
    lines.append(f"- **git_sha**: `{git_sha}`")
    lines.append(f"- **mode**: `{mode}`")
    lines.append(f"- **platform**: `{config.platform}`")
    lines.append(f"- **adapter**: {adapter_implementation}")
    lines.append(f"- **depth_mode**: {depth_mode}")
    lines.append(f"- **passive_fill_estimate_source**: {passive_estimate_source}")
    lines.append(f"- **duration**: {run_duration_secs:.1f}s")
    lines.append(f"- **config_hash**: `{compute_config_hash(config)}`")
    lines.append(f"")
    lines.append(f"## Market Discovery")
    lines.append(f"")
    lines.append(f"- Markets discovered: {len(markets_discovered)}")
    lines.append(f"- Eligible markets: {eligible_count}")
    lines.append(f"- Skipped reasons:")
    for reason, count in sorted(skip_count.items(), key=lambda x: -x[1]):
        lines.append(f"  - {reason}: {count}")
    lines.append(f"")
    lines.append(f"## Opportunity Detection")
    lines.append(f"")
    lines.append(f"- Opportunities detected: {len(opportunities)}")
    lines.append(f"- Maker gate pass: {gm_pass}")
    lines.append(f"- Taker diagnostic pass: {tk_pass}")
    lines.append(f"- Maker-rebate-excluded-required gate: {kwargs.get('rebate_excluded_count', 0)}")
    lines.append(f"- Gross gap range: [{min(gross_gaps):.5f}, {max(gross_gaps):.5f}]" if gross_gaps else "N/A")
    lines.append(f"- Net edge range: [{min(edges):.5f}, {max(edges):.5f}]" if edges else "N/A")
    lines.append(f"")
    lines.append(f"## Passive Fill Estimates")
    lines.append(f"")
    lines.append(f"- Total quotes recorded: {passive_summary.get('total_quotes_recorded', 0)}")
    lines.append(f"- Touches: {passive_summary.get('touches', 0)}")
    lines.append(f"- Crosses: {passive_summary.get('crosses', 0)}")
    lines.append(f"- Expired: {passive_summary.get('expired', 0)}")
    lines.append(f"- Touch rate: {passive_summary.get('touch_rate_pct', 0):.1f}%")
    lines.append(f"- Median time-to-touch: {passive_summary.get('median_time_to_touch_ms', 'N/A')}")
    lines.append(f"- P95 time-to-touch: {passive_summary.get('p95_time_to_touch_ms', 'N/A')}")
    lines.append(f"- Source: {passive_summary.get('source', 'N/A')}")
    lines.append(f"")
    lines.append(f"## Limitations")
    lines.append(f"")
    lines.append(f"- **Depth**: {depth_mode}")
    lines.append(f"- **Trade ticks**: {passive_estimate_source}")
    lines.append(f"- **Final resolution detection**: {kwargs.get('final_resolution_detection', 'unavailable')}")
    lines.append(f"- **Backtest fidelity**: Trade-history replay cannot validate maker fill rate.")
    lines.append(f"- **Signing latency**: Python py_clob_client_v2 ~1s per order.")
    lines.append(f"- **Adapter**: Python only (no Rust adapter available)")
    lines.append(f"")
    lines.append(f"## Warnings")
    lines.append(f"")
    lines.append(f"This replay validates detector/edge/sizing behavior only.")
    lines.append(f"It does not validate maker queue position, passive fill probability,")
    lines.append(f"live cancel/replace behavior, or hybrid one-leg risk.")
    lines.append(f"")

    return "\n".join(lines)
