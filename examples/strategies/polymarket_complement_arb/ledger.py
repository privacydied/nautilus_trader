"""
Append-only JSONL ledger for Polymarket complement arb strategy.

Writes structured event records to run-specific directories under
reports/polymarket_complement_arb/<run_id>/.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import LedgerEntry


class Ledger:
    """
    Append-only JSONL ledger.

    Each run writes to reports/polymarket_complement_arb/<run_id>/
    Filenames are stable per event type.
    """

    def __init__(self, run_id: str, base_dir: str | Path | None = None):
        self._run_id = run_id
        if base_dir is None:
            base_dir = os.path.join(".", "reports", "polymarket_complement_arb", run_id)
        self._base_dir = Path(str(base_dir))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, list[str]] = {
            "ledger": [],
            "opportunities": [],
            "rejected_opportunities": [],
            "passive_fill_estimates": [],
            "skipped_markets": [],
        }

    def _write_event(self, filename: str, data: dict[str, Any]) -> None:
        """Append a JSON line to a file."""
        filepath = self._base_dir / f"{filename}.jsonl"
        with open(filepath, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")

    def write_ledger_entry(self, entry: LedgerEntry) -> None:
        """Write a ledger entry."""
        self._write_event("ledger", {
            "run_id": entry.run_id,
            "timestamp_ns": entry.timestamp_ns,
            "mode": entry.mode,
            "event_type": entry.event_type,
            "condition_id": entry.condition_id,
            "market_slug": entry.market_slug,
            "question": entry.question,
            "yes_instrument_id": entry.yes_instrument_id,
            "no_instrument_id": entry.no_instrument_id,
            "state_before": entry.state_before,
            "state_after": entry.state_after,
            "prices": entry.prices,
            "sizes": entry.sizes,
            "intended_qty": entry.intended_qty,
            "filled_qty": entry.filled_qty,
            "paired_qty": entry.paired_qty,
            "residual_qty": entry.residual_qty,
            "fee_inputs": entry.fee_inputs,
            "cost_breakdown": entry.cost_breakdown,
            "net_edge": entry.net_edge,
            "order_side": entry.order_side,
            "order_type": entry.order_type,
            "time_in_force": entry.time_in_force,
            "post_only": entry.post_only,
            "quote_quantity": entry.quote_quantity,
            "config_hash": entry.config_hash,
            "details": entry.details,
        })

    def write_opportunity(self, data: dict[str, Any]) -> None:
        """Write an opportunity record."""
        self._write_event("opportunities", data)

    def write_rejected_opportunity(self, data: dict[str, Any]) -> None:
        """Write a rejected opportunity record."""
        self._write_event("rejected_opportunities", data)

    def write_passive_fill_estimate(self, data: dict[str, Any]) -> None:
        """Write a passive fill estimate record."""
        self._write_event("passive_fill_estimates", data)

    def write_skipped_market(self, condition_id: str, slug: str, reason: str) -> None:
        """Write a skipped market record."""
        self._write_event("skipped_markets", {
            "condition_id": condition_id,
            "market_slug": slug,
            "reason": reason,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        })

    def write_config(self, config: dict[str, Any]) -> None:
        """Write the run configuration snapshot."""
        filepath = self._base_dir / "config.json"
        with open(filepath, "w") as f:
            json.dump(config, f, indent=2, default=str)

    def write_health(self, data: dict[str, Any]) -> None:
        """Write health diagnostics."""
        filepath = self._base_dir / "health.json"
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def write_run_summary_json(self, data: dict[str, Any]) -> None:
        """Write the run summary as JSON."""
        filepath = self._base_dir / "run_summary.json"
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def write_run_summary_md(self, text: str) -> None:
        """Write the run summary as Markdown."""
        filepath = self._base_dir / "run_summary.md"
        with open(filepath, "w") as f:
            f.write(text)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def run_id(self) -> str:
        return self._run_id
