"""Subprocess runner for conductor jobs.

Streams stdout/stderr to files to avoid OOM on large eval jobs.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .atomic_io import write_json_atomic
from .models import (
    ConductorJobResult,
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
)


def build_runner_env(
    job: ConductorJobSpec, base_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Build the environment for a subprocess runner.

    Injects:
    - VA_SIGNAL_OBSERVER_CONDUCTOR_MODE: ``exploration`` or ``locked``.
    - VA_SIGNAL_OBSERVER_REGISTRY_WRITE_ALLOWED: always ``0`` in v0.
    - VA_SIGNAL_OBSERVER_LEDGER_WRITE_ALLOWED: ``0`` for exploration, ``1`` for locked.
    """
    env = dict(os.environ)
    if base_env is not None:
        env.update(base_env)

    mode_str = (
        "exploration"
        if job.run_mode == ConductorRunMode.EXPLORATION
        else "locked"
    )
    env["VA_SIGNAL_OBSERVER_CONDUCTOR_MODE"] = mode_str
    env["VA_SIGNAL_OBSERVER_REGISTRY_WRITE_ALLOWED"] = "0"

    if job.run_mode == ConductorRunMode.LOCKED:
        env["VA_SIGNAL_OBSERVER_LEDGER_WRITE_ALLOWED"] = "1"
    else:
        env["VA_SIGNAL_OBSERVER_LEDGER_WRITE_ALLOWED"] = "0"

    return env


def run_job(
    job: ConductorJobSpec,
    *,
    timeout_seconds: int | None = None,
    max_log_bytes: int = 100 * 1024 * 1024,
) -> ConductorJobResult:
    """Execute a ConductorJobSpec as a subprocess.

    Creates output_dir, streams stdout/stderr to files, and writes
    conductor_result.json atomically.

    The runner does NOT:
    - Apply locked-gate filtering.
    - Update REJECTED_RESEARCH.md.
    - Write evidence_ledger.jsonl.
    - Interpret verdicts.
    """
    output_dir = Path(job.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = build_runner_env(job)

    try:
        with open(stdout_path, "wb") as out_f, open(stderr_path, "wb") as err_f:
            proc = subprocess.Popen(
                list(job.command),
                stdout=out_f,
                stderr=err_f,
                env=env,
                cwd=str(output_dir),
            )
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                return ConductorJobResult(
                    job_id=job.job_id,
                    status=ConductorJobStatus.FAILED,
                    returncode=-1,
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    output_dir=str(output_dir),
                    summary_path=None,
                    error="TIMEOUT",
                    metadata={
                        "timeout_seconds": timeout_seconds,
                        "registry_write_allowed": False,
                        "ledger_write_allowed": (
                            job.run_mode == ConductorRunMode.LOCKED
                        ),
                        "stdout_log": str(stdout_path),
                        "stderr_log": str(stderr_path),
                    },
                )

    except Exception as exc:
        finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ConductorJobResult(
            job_id=job.job_id,
            status=ConductorJobStatus.FAILED,
            returncode=None,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            output_dir=str(output_dir),
            summary_path=None,
            error=str(exc),
            metadata={
                "registry_write_allowed": False,
                "ledger_write_allowed": (
                    job.run_mode == ConductorRunMode.LOCKED
                ),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            },
        )

    finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Truncation after exit
    for log_path in (stdout_path, stderr_path):
        if log_path.is_file():
            size = log_path.stat().st_size
            if size > max_log_bytes:
                with open(log_path, "rb") as f:
                    truncated = f.read(max_log_bytes)
                trunc_marker = (
                    f"\n[CONDUCTOR_LOG_TRUNCATED max_log_bytes={max_log_bytes} "
                    f"original_size_bytes={size}]\n"
                ).encode("utf-8")
                with open(log_path, "wb") as f:
                    f.write(truncated)
                    f.write(trunc_marker)

    # Check for summary
    summary_path: Path | None = None
    for candidate in output_dir.iterdir():
        if candidate.name == "summary.json":
            summary_path = candidate
            break

    status = (
        ConductorJobStatus.COMPLETED
        if proc.returncode == 0
        else ConductorJobStatus.FAILED
    )

    result = ConductorJobResult(
        job_id=job.job_id,
        status=status,
        returncode=proc.returncode,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        output_dir=str(output_dir),
        summary_path=str(summary_path) if summary_path else None,
        error=None if proc.returncode == 0 else f"exit_code={proc.returncode}",
        metadata={
            "registry_write_allowed": False,
            "ledger_write_allowed": (
                job.run_mode == ConductorRunMode.LOCKED
            ),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        },
    )

    write_json_atomic(
        output_dir / "conductor_result.json",
        {
            "job_id": job.job_id,
            "status": result.status.name,
            "returncode": result.returncode,
            "started_at_utc": result.started_at_utc,
            "finished_at_utc": result.finished_at_utc,
            "summary_path": result.summary_path,
            "error": result.error,
        },
    )

    return result