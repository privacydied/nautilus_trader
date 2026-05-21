#!/usr/bin/env python3
"""
Stage 2 readiness checker — confirm the repo is ready for Stage 2 collection.

Checks:
- V1 run artifact module imports
- campaign runner imports
- capture validator imports
- run_report_corpus imports
- quarantine convention/helper exists
- burn convention/helper exists
- STAGE2_PRECOMMITMENT.md exists and is tracked
- stage2_precommitment.json exists and is tracked
- STAGE2_THRESHOLDS_RATIONALE.md exists and is tracked
- markdown and JSON values match
- p-value source is native_permutation
- minimum_valid_events_per_config is pinned
- collection_lock consistency (if exists)
- working tree status
- current git SHA
- Python path is repo venv
- Hermes job status (if relevant)

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .run_artifacts import atomic_write_json
from .run_artifacts import atomic_write_text
from .stage2_precommitment_utils import CollectionLock
from .stage2_precommitment_utils import _get_git_sha
from .stage2_precommitment_utils import load_precommitment
from .stage2_precommitment_utils import validate_markdown_json_match


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _module_imports_ok(module_path: str) -> tuple[bool, str]:
    """Try to import a module path, return (ok, reason)."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return True, f"imported {module_path} (has {len(dir(mod))} attrs)"
    except ImportError as e:
        return False, f"ImportError: {e}"
    except Exception as e:
        return False, f"Exception: {e}"


def _python_path_ok(expected_prefix: str) -> tuple[bool, str]:
    """Check if the running Python is from the expected venv path."""
    import sys as _sys
    actual = _sys.executable
    if expected_prefix in actual:
        return True, f"Python: {actual}"
    return False, f"Python path mismatch: expected prefix '{expected_prefix}', got '{actual}'"


def _file_exists_and_tracked(path: Path, repo_root: Path) -> tuple[bool, str]:
    """Check a file exists and is tracked by git."""
    if not path.exists():
        return False, f"File not found: {path}"
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path.relative_to(repo_root))],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_root),
            check=False,
        )
        if result.returncode == 0:
            return True, f"File exists and is git-tracked: {path}"
        return False, f"File exists but is NOT git-tracked: {path}"
    except Exception as e:
        return False, f"Cannot check git tracking: {e}"


def _hermes_gateway_status() -> tuple[bool, str]:
    """Check if Hermes gateway is running."""
    try:
        result = subprocess.run(
            ["hermes", "gateway", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            if "running" in result.stdout.lower() or "active" in result.stdout.lower():
                return True, "Gateway is running"
            return False, f"Gateway not running: {result.stdout[:200]}"
        return False, f"hermes gateway status returned {result.returncode}"
    except FileNotFoundError:
        return False, "hermes CLI not found"
    except Exception as e:
        return False, f"hermes check error: {e}"


def _hermes_scheduled_job_check(job_id: str = "85a4d56145fe") -> dict[str, Any]:
    """Check if the Stage 2 Hermes scheduled job exists and its state."""
    result: dict[str, Any] = {"job_id": job_id, "exists": False}
    try:
        # Check Hermes cron jobs via CLI or file
        cron_file = Path.home() / ".hermes" / "cron" / "jobs.json"
        if cron_file.exists():
            with open(cron_file, encoding="utf-8") as f:
                data = json.load(f)
            for job in data.get("jobs", []):
                if job.get("id") == job_id:
                    result["exists"] = True
                    result["name"] = job.get("name")
                    result["enabled"] = job.get("enabled", False)
                    result["state"] = job.get("state")
                    result["schedule"] = job.get("schedule", {})
                    result["workdir"] = job.get("workdir")
                    result["next_run_at"] = job.get("next_run_at")
                    break
    except Exception as e:
        result["error"] = str(e)
    return result


def check_readiness(
    *,
    hermex_job_id: str = "85a4d56145fe",
    expected_python_prefix: str = ".venv",
) -> dict[str, Any]:
    """
    Run all Stage 2 readiness checks.

    Returns a readiness dict with keys:
        ready (bool), hard_blockers (list), warnings (list),
        summary (dict), checks (list of per-check results).
    """
    checks: list[dict[str, Any]] = []
    hard_blockers: list[str] = []
    warnings: list[str] = []

    repo_root = Path.cwd().resolve()
    root_reports = repo_root / "reports"

    # --- Core module imports ---
    modules_to_check = [
        "examples.strategies.venue_agnostic_signal_observer.run_artifacts",
        "examples.strategies.venue_agnostic_signal_observer.run_derivatives_capture_campaign",
        "examples.strategies.venue_agnostic_signal_observer.validate_capture",
        "examples.strategies.venue_agnostic_signal_observer.run_report_corpus",
        "examples.strategies.venue_agnostic_signal_observer.run_index",
        "examples.strategies.venue_agnostic_signal_observer.quarantine",
        "examples.strategies.venue_agnostic_signal_observer.burn",
    ]

    for mod_path in modules_to_check:
        ok, reason = _module_imports_ok(mod_path)
        checks.append({
            "check": f"module_import:{mod_path}",
            "ok": ok,
            "reason": reason,
        })
        if not ok:
            hard_blockers.append(f"Module import failed: {mod_path} — {reason}")

    # --- Precommitment files ---
    precommit_files = [
        ("stage2_precommitment.json", repo_root / "stage2_precommitment.json"),
        ("STAGE2_PRECOMMITMENT.md", repo_root / "STAGE2_PRECOMMITMENT.md"),
        ("STAGE2_THRESHOLDS_RATIONALE.md", repo_root / "STAGE2_THRESHOLDS_RATIONALE.md"),
    ]

    for name, path in precommit_files:
        exists_ok, tracked_ok = False, False
        exists_reason = f"File not found: {path}"

        if path.exists():
            exists_ok = True
            exists_reason = f"File exists: {path}"
            # Check git tracked
            try:
                rp = path.relative_to(repo_root)
                git_result = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", str(rp)],
                    capture_output=True, text=True, timeout=5, cwd=str(repo_root), check=False,
                )
                if git_result.returncode == 0:
                    tracked_ok = True
                else:
                    exists_reason = f"File exists but NOT git-tracked: {path}"
                    hard_blockers.append(f"{name} exists but is not git-tracked")
            except Exception as e:
                exists_reason = f"Git check error: {e}"

        checks.append({
            "check": f"precommitment_file:{name}",
            "ok": exists_ok and tracked_ok,
            "reason": exists_reason,
        })
        if not exists_ok:
            hard_blockers.append(f"{name} is missing")

    # --- Markdown/JSON value match ---
    try:
        precommit = load_precommitment()
        md_json_ok, md_json_reason = validate_markdown_json_match(precommit)
        checks.append({
            "check": "markdown_json_match",
            "ok": md_json_ok,
            "reason": md_json_reason,
        })
        if not md_json_ok:
            hard_blockers.append(f"Markdown/JSON mismatch: {md_json_reason}")
    except Exception as e:
        checks.append({
            "check": "markdown_json_match",
            "ok": False,
            "reason": f"Failed to load/check: {e}",
        })
        hard_blockers.append(f"Cannot validate markdown/JSON match: {e}")

    # --- p-value source check ---
    try:
        primary_fdr = precommit.get("primary_fdr", {})
        pv_source = primary_fdr.get("pvalue_source", "")
        pv_ok = pv_source == "native_permutation"
        checks.append({
            "check": "pvalue_source",
            "ok": pv_ok,
            "reason": f"p-value source: {pv_source}" if pv_ok else f"Expected native_permutation, got {pv_source}",
        })
        if not pv_ok:
            hard_blockers.append(f"p-value source is {pv_source}, expected native_permutation")
    except Exception as e:
        checks.append({"check": "pvalue_source", "ok": False, "reason": str(e)})
        hard_blockers.append(f"Cannot check p-value source: {e}")

    # --- minimum_valid_events_per_config check ---
    try:
        min_events = precommit.get("discovery_acceptance", {}).get("minimum_valid_events_per_config", 0)
        events_ok = min_events == 50
        checks.append({
            "check": "minimum_valid_events_pinned",
            "ok": events_ok,
            "reason": f"min_valid_events = {min_events}" if events_ok else f"Expected 50, found {min_events}",
        })
        if not events_ok:
            hard_blockers.append(f"minimum_valid_events_per_config is {min_events}, expected 50")
    except Exception as e:
        checks.append({"check": "minimum_valid_events_pinned", "ok": False, "reason": str(e)})
        hard_blockers.append(f"Cannot check min_events: {e}")

    # --- Collection lock ---
    lock = CollectionLock()
    lock_ok = True
    if lock.exists():
        lock_ok, lock_reason = lock.verify()
        checks.append({
            "check": "collection_lock_verify",
            "ok": lock_ok,
            "reason": lock_reason,
        })
        if not lock_ok:
            hard_blockers.append(f"Collection lock hash mismatch: {lock_reason}")
    else:
        checks.append({
            "check": "collection_lock_exists",
            "ok": True,
            "reason": "Lock does not exist yet — will be created before first FULL_ACTIVE capture attempt",
        })

    # --- Python path ---
    py_ok, py_reason = _python_path_ok(expected_python_prefix)
    checks.append({
        "check": "python_path",
        "ok": py_ok,
        "reason": py_reason,
    })
    if not py_ok:
        warnings.append(f"Python path: {py_reason}")

    # --- Git state ---
    git_sha = _get_git_sha()
    try:
        git_status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_root), check=False,
        )
        is_dirty = bool(git_status.stdout.strip())
        checks.append({
            "check": "git_status",
            "ok": True,
            "reason": f"SHA: {git_sha}, dirty: {is_dirty}",
        })
    except Exception as e:
        checks.append({"check": "git_status", "ok": False, "reason": str(e)})
        warnings.append(f"Git status error: {e}")

    # --- Hermes gateway ---
    gateway_ok, gateway_reason = _hermes_gateway_status()
    checks.append({
        "check": "hermes_gateway",
        "ok": gateway_ok,
        "reason": gateway_reason,
    })

    # --- Hermes scheduled job ---
    job_info = _hermes_scheduled_job_check(hermex_job_id)
    if job_info.get("exists"):
        if not gateway_ok:
            hard_blockers.append(
                "A relevant Hermes scheduled job exists for this workflow "
                "and Hermes gateway is down. "
                "Job is updated but will not fire automatically until the "
                "Hermes gateway is restarted."
            )
        checks.append({
            "check": "hermes_scheduled_job",
            "ok": gateway_ok,
            "reason": (
                f"Job '{job_info.get('name')}' (id={job_info['job_id']}) "
                f"enabled={job_info.get('enabled')}, state={job_info.get('state')}"
                if job_info.get("exists") else "No relevant job found"
            ),
        })
    else:
        checks.append({
            "check": "hermes_scheduled_job",
            "ok": True,
            "reason": "No relevant Hermes scheduled job found for this workflow",
        })

    # --- Quarantine helper ---
    qpath = root_reports / "research_run_quarantine.jsonl"
    q_ok = qpath.parent.exists()
    checks.append({
        "check": "quarantine_convention",
        "ok": q_ok,
        "reason": f"Quarantine dir exists: {qpath.parent}" if q_ok else f"Reports dir missing: {qpath.parent}",
    })

    # --- Burn helper ---
    bpath = root_reports / "research_run_burned.jsonl"
    b_ok = bpath.parent.exists()
    checks.append({
        "check": "burn_convention",
        "ok": b_ok,
        "reason": f"Burn dir exists: {bpath.parent}" if b_ok else f"Reports dir missing: {bpath.parent}",
    })

    # --- Verdict ---
    ready = len(hard_blockers) == 0
    return {
        "ready": ready,
        "readiness_checked_at": _ts_now_iso(),
        "git_sha": git_sha,
        "workdir": str(repo_root),
        "hard_blockers": hard_blockers,
        "warnings": warnings,
        "checks": checks,
        "hermes_gateway_running": gateway_ok,
        "hermes_job_info": job_info if job_info.get("exists") else None,
        "lock_exists": lock.exists(),
        "lock_ok": lock_ok,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Stage 2 readiness check — verify repo is ready "
                    "for Stage 2 collection."
    )
    p.add_argument(
        "--out-dir", type=str, default=None,
        help="Output directory (default: reports/stage2_readiness)",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    root = Path.cwd().resolve()
    out_dir = (
        Path(args.out_dir).resolve()
        if args.out_dir
        else root / "reports" / "stage2_readiness"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    result = check_readiness()

    # Write JSON summary
    summary_path = out_dir / "readiness_summary.json"
    atomic_write_json(summary_path, result)

    # Write markdown report
    report_lines = [
        "# Stage 2 Readiness Report",
        "",
        f"- **Ready for collection:** {'YES' if result['ready'] else 'NO'}",
        f"- **Checked at:** {result.get('readiness_checked_at', '?')}",
        f"- **Workdir:** {result.get('workdir', '?')}",
        f"- **Git SHA:** {result.get('git_sha', '?')}",
        "",
        "## Blockers",
        "",
    ]
    if result["hard_blockers"]:
        for b in result["hard_blockers"]:
            report_lines.append(f"- 🔴 {b}")
    else:
        report_lines.append("- None")
    report_lines.append("")

    report_lines.append("## Warnings")
    report_lines.append("")
    if result["warnings"]:
        for w in result["warnings"]:
            report_lines.append(f"- ⚠️ {w}")
    else:
        report_lines.append("- None")
    report_lines.append("")

    # Summary of checks
    report_lines.append("## Checks")
    report_lines.append("")
    report_lines.append("| Check | OK | Reason |")
    report_lines.append("|-------|----|--------|")
    for c in result.get("checks", []):
        icon = "✅" if c["ok"] else "❌"
        report_lines.append(
            f"| {c['check']} | {icon} | {c['reason']} |"
        )
    report_lines.append("")

    report_path = out_dir / "readiness_report.md"
    atomic_write_text(report_path, "\n".join(report_lines))

    print(f"  Readiness:     {'READY' if result['ready'] else 'BLOCKED'}")
    print(f"  Blockers:      {len(result['hard_blockers'])}")
    print(f"  Warnings:      {len(result['warnings'])}")
    print(f"  Gateway:       {'RUNNING' if result.get('hermes_gateway_running') else 'DOWN'}")

    if result["hard_blockers"]:
        for b in result["hard_blockers"]:
            print(f"  BLOCKER: {b}")

    if not result.get("hermes_gateway_running"):
        print()
        print("  Job is updated but will not fire automatically until the")
        print("  Hermes gateway is restarted.")

    sys.exit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
