"""Ledger writer — appends locked-run events to evidence_ledger.jsonl.

Idempotent: identical events are detected and skipped.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from .atomic_io import append_jsonl_durable, sha256_canonical_json
from .models import ConductorJobResult, ConductorJobSpec, ConductorRunMode


def write_locked_run_event(
    ledger_path: Path,
    job: ConductorJobSpec,
    result: ConductorJobResult,
) -> bool:
    """Write a locked-run event to the evidence ledger.

    Returns True if an event was written, False if an identical event
    already exists (dedup by event_type + job_id + precommitment_hash
    + result_status + summary_path).

    Refuses:
    - Exploration jobs (run_mode != LOCKED).
    - Locked jobs without a ``precommitment_hash`` in metadata.
    """
    if job.run_mode != ConductorRunMode.LOCKED:
        return False

    precommitment_hash = job.metadata.get("precommitment_hash")
    if not precommitment_hash or not isinstance(precommitment_hash, str):
        return False

    # Build event payload (without event_hash)
    event_payload_base = {
        "event_type": "CONDUCTOR_LOCKED_RUN_COMPLETED",
        "job_id": job.job_id,
        "signal_family": job.signal_family,
        "study_id": job.study_id,
        "precommitment_hash": precommitment_hash,
        "result_status": result.status.name,
        "returncode": result.returncode,
        "output_dir": result.output_dir,
        "summary_path": result.summary_path,
        "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    event_hash = sha256_canonical_json(event_payload_base)
    event_payload = dict(event_payload_base)
    event_payload["event_hash"] = event_hash

    # Check for duplicates
    if ledger_path.is_file():
        try:
            existing_lines = ledger_path.read_text().strip().split("\n")
        except OSError:
            existing_lines = []

        for line in existing_lines:
            if not line.strip():
                continue
            # Simple dedup: compare required fields
            if all(
                f'"{k}":"{v}"' in line
                for k, v in (
                    ("event_type", "CONDUCTOR_LOCKED_RUN_COMPLETED"),
                    ("job_id", job.job_id),
                    ("precommitment_hash", precommitment_hash),
                    ("result_status", result.status.name),
                )
            ):
                return False

    append_jsonl_durable(ledger_path, event_payload)
    return True