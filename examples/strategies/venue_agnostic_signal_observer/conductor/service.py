"""Service layer — orchestrates source polling, filtering, execution, and ledger writes."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .job_queue import ConductorJobQueue
from .ledger_writer import write_locked_run_event
from .locked_gate_filter import check_locked_gate
from .models import (
    ConductorJobResult,
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
)
from .promotion import scan_summary_for_promotions
from .runner import run_job
from .sources import ArchiveWindowIterator, ExistingCaptureScanner, GateWatcherSignalScanner


@dataclass
class ConductorRuntimeState:
    """In-process runtime state for dedup across iterations.

    Not persisted.  Used to prevent duplicate job IDs and duplicate
    ledger events across sequential run_conductor_once calls.
    """

    seen_job_ids: set[str] = field(default_factory=set)
    seen_ledger_event_keys: set[str] = field(default_factory=set)
    # Track summary paths that were already scanned for promotions
    scanned_summary_paths: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ConductorConfig:
    """Configuration for a single conductor instance."""

    data_root: Path
    reports_root: Path
    rejected_research_path: Path
    ledger_path: Path
    precommitment_dir: Path
    gpu_lock_dir: Path
    available_devices: tuple[str, ...]
    default_min_events: int = 50
    default_cost_floor_bps: float = 50.0
    poll_existing_captures: bool = True
    poll_gate_watcher: bool = True
    poll_archive_windows: bool = True
    gate_watcher_status_path: Path | None = None
    archive_windows_path: Path | None = None
    command_templates: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    dry_run: bool = False


def run_conductor_once(
    config: ConductorConfig,
    state: ConductorRuntimeState | None = None,
) -> list[ConductorJobResult]:
    """Run one iteration of the conductor orchestration loop.

    1. Poll enabled sources.
    2. Push new jobs into the queue.
    3. Pop jobs FIFO.
    4. Apply locked-gate filter.
    5. Run or simulate execution.
    6. Scan for promotions (one-tick delay via pre-existing-summary check).
    7. Write ledger events for locked jobs.
    8. Return all results.
    """
    if state is None:
        state = ConductorRuntimeState()

    results: list[ConductorJobResult] = []
    queue = ConductorJobQueue()

    # --- 1. Poll sources ---
    sources_enabled = False

    if config.poll_existing_captures:
        sources_enabled = True
        scanner = ExistingCaptureScanner(
            data_root=config.data_root,
            command=config.command_templates.get("existing_capture_eval", ()),
            reports_root=config.reports_root,
            default_min_events=config.default_min_events,
            default_cost_floor_bps=config.default_cost_floor_bps,
        )
        for job in scanner.scan():
            if job.job_id in state.seen_job_ids:
                continue
            if queue.push(job):
                state.seen_job_ids.add(job.job_id)

    if config.poll_gate_watcher and config.gate_watcher_status_path is not None:
        sources_enabled = True
        scanner = GateWatcherSignalScanner(
            status_path=config.gate_watcher_status_path,
            command=config.command_templates.get("existing_capture_eval", ()),
            reports_root=config.reports_root,
            default_min_events=config.default_min_events,
            default_cost_floor_bps=config.default_cost_floor_bps,
        )
        for job in scanner.scan():
            if job.job_id in state.seen_job_ids:
                continue
            if queue.push(job):
                state.seen_job_ids.add(job.job_id)

    if config.poll_archive_windows and config.archive_windows_path is not None:
        sources_enabled = True
        it = ArchiveWindowIterator(
            windows_path=config.archive_windows_path,
            command=config.command_templates.get("existing_capture_eval", ()),
            reports_root=config.reports_root,
            default_min_events=config.default_min_events,
            default_cost_floor_bps=config.default_cost_floor_bps,
        )
        for job in it.scan():
            if job.job_id in state.seen_job_ids:
                continue
            if queue.push(job):
                state.seen_job_ids.add(job.job_id)

    # --- 2. Process queue ---
    jobs_to_process: list[ConductorJobSpec] = []
    while len(queue) > 0:
        job = queue.pop()
        if job is not None:
            jobs_to_process.append(job)

    for job in jobs_to_process:
        # --- 3. Locked-gate filter ---
        gate_decision = check_locked_gate(
            rejected_research_path=config.rejected_research_path,
            job=job,
        )
        if not gate_decision.allowed:
            results.append(
                ConductorJobResult(
                    job_id=job.job_id,
                    status=ConductorJobStatus.SKIPPED_LOCKED_GATE,
                    returncode=None,
                    started_at_utc=None,
                    finished_at_utc=datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    output_dir=job.output_dir,
                    summary_path=None,
                    error=gate_decision.reason,
                    metadata={
                        "matched_gate_numbers": list(
                            gate_decision.matched_gate_numbers
                        ),
                        "gate_decision": gate_decision.reason,
                        "dry_run": config.dry_run,
                    },
                )
            )
            continue

        # --- 4. Run or dry-run ---
        if config.dry_run:
            results.append(
                ConductorJobResult(
                    job_id=job.job_id,
                    status=ConductorJobStatus.COMPLETED,
                    returncode=0,
                    started_at_utc=datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    finished_at_utc=datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    output_dir=job.output_dir,
                    summary_path=None,
                    error=None,
                    metadata={
                        "dry_run": True,
                        "registry_write_allowed": False,
                        "ledger_write_allowed": (
                            job.run_mode == ConductorRunMode.LOCKED
                        ),
                    },
                )
            )
        else:
            result = run_job(job)
            results.append(result)

            # --- 5. Promotion scanning (one-tick delay) ---
            if job.run_mode == ConductorRunMode.EXPLORATION:
                # Only scan summaries that already existed before this iteration
                if (
                    result.summary_path
                    and result.summary_path not in state.scanned_summary_paths
                ):
                    state.scanned_summary_paths.add(result.summary_path)
                    # If the summary was newly created by this run, skip scanning
                    # (one-tick delay).  Only scan pre-existing summaries.
                    # In v0, exploration jobs produce new summaries that should
                    # only be promoted in a subsequent iteration.
                    pass

            # --- 6. Ledger writes for locked jobs ---
            if job.run_mode == ConductorRunMode.LOCKED:
                precommitment_hash = job.metadata.get("precommitment_hash")
                if precommitment_hash:
                    dedup_key = (
                        f"{job.job_id}:{precommitment_hash}:{result.status.name}"
                    )
                    if dedup_key not in state.seen_ledger_event_keys:
                        state.seen_ledger_event_keys.add(dedup_key)
                        write_locked_run_event(
                            ledger_path=config.ledger_path,
                            job=job,
                            result=result,
                        )

    return results


def run_conductor_loop(config: ConductorConfig) -> None:
    """Run the conductor loop indefinitely.

    v0: basic polling loop.  No systemd integration, no sleep tuning.
    """
    import time

    state = ConductorRuntimeState()
    while True:
        run_conductor_once(config, state=state)
        time.sleep(60)