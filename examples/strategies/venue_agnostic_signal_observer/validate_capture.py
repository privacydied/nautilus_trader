#!/usr/bin/env python3
"""
Capture validator smoke tool.

Reads a capture directory produced by ``run_derivatives_spot_capture.py``,
walks all JSONL stream files, re-computes tick counts, timestamps, and
overlap windows from raw data, and emits a validation report comparing
the recomputed values against the capture manifest.

Safe for read-only use: never modifies capture files or manifest.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_metadata import check_schema_version
from .run_artifacts import atomic_write_json
from .run_artifacts import atomic_write_text
from .run_artifacts import safe_output_dir
from .runners.legacy_cli.run_index import append_run_index_row
from .runners.legacy_cli.run_index import build_run_index_row


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERDICT_PASSED = "CAPTURE_VALIDATION_PASSED"
VERDICT_WARNINGS = "CAPTURE_VALIDATION_WARNINGS"
VERDICT_FAILED = "CAPTURE_VALIDATION_FAILED"
VERDICT_MANIFEST_MISSING = "CAPTURE_MANIFEST_MISSING"
VERDICT_SCHEMA_UNSUPPORTED = "CAPTURE_SCHEMA_UNSUPPORTED"
VERDICT_JSONL_MALFORMED = "CAPTURE_JSONL_MALFORMED"

_VERDICTS = frozenset({
    VERDICT_PASSED,
    VERDICT_WARNINGS,
    VERDICT_FAILED,
    VERDICT_MANIFEST_MISSING,
    VERDICT_SCHEMA_UNSUPPORTED,
    VERDICT_JSONL_MALFORMED,
})

_FILENAME_PATTERN = re.compile(
    r"^trades_(?P<venue>[a-z_]+)_(?P<symbol>[A-Z]+-[A-Z]+)_[^_]+\.jsonl$"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_ts_ns(line: str) -> int | None:
    """Parse ``ts_event`` (nanoseconds) from a JSONL trade line."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    ts = obj.get("ts_event")
    if isinstance(ts, int):
        return ts
    if isinstance(ts, str):
        try:
            return int(ts)
        except (ValueError, TypeError):
            return None
    return None


def _filename_to_stream_name(filename: str) -> str | None:
    """
    Convert a trades_*.jsonl filename back to the manifest stream name.

    Example: ``trades_binance_perp_BTC-USDT_12345.jsonl``
             -> ``binance_perp_BTC/USDT``
    """
    m = _FILENAME_PATTERN.match(filename)
    if not m:
        return None
    venue = m.group("venue")
    symbol = m.group("symbol").replace("-", "/")
    return f"{venue}_{symbol}"


def _map_jsonl_to_stream(
    jsonl_paths: list[Path],
) -> tuple[dict[str, Path], list[str]]:
    """
    Map JSONL file paths to stream names.

    Returns (stream_map, unparsed_paths) where stream_map is
    ``{stream_name: Path}`` and unparsed_paths lists paths whose
    filename didn't match the expected pattern.
    """
    stream_map: dict[str, Path] = {}
    unparsed: list[str] = []
    for p in jsonl_paths:
        name = _filename_to_stream_name(p.name)
        if name:
            stream_map[name] = p
        else:
            unparsed.append(str(p))
    return stream_map, unparsed


def _read_jsonl_counts(
    path: Path,
) -> dict[str, Any]:
    """
    Walk a JSONL file, count ticks and malformed lines, track times.

    Returns dict with:
        tick_count, malformed_count, first_ts_ns, last_ts_ns
    """
    tick_count = 0
    malformed_count = 0
    first_ts_ns: int | None = None
    last_ts_ns: int | None = None

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                ts = _parse_ts_ns(stripped)
                if ts is None:
                    malformed_count += 1
                    continue
                tick_count += 1
                if first_ts_ns is None or ts < first_ts_ns:
                    first_ts_ns = ts
                if last_ts_ns is None or ts > last_ts_ns:
                    last_ts_ns = ts
    except OSError as exc:
        return {
            "tick_count": 0,
            "malformed_count": 0,
            "first_ts_ns": None,
            "last_ts_ns": None,
            "error": str(exc),
        }

    return {
        "tick_count": tick_count,
        "malformed_count": malformed_count,
        "first_ts_ns": first_ts_ns,
        "last_ts_ns": last_ts_ns,
        "error": None,
    }


def _ns_to_iso(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# Overlap recomputation
# ---------------------------------------------------------------------------


def _recompute_overlap(
    raw_streams: dict[str, dict[str, Any]],
    source_venue: str = "binance_perp",
    target_venues: tuple[str, ...] = ("kraken", "coinbase"),
) -> dict[str, Any]:
    """
    Recompute overlap windows from raw stream data, mimicking
    ``compute_overlap_windows`` in the capture script.

    Groups streams by base asset and recomputes per-pair + global overlap.
    """
    # Infer assets from stream keys
    assets: set[str] = set()
    source_prefix = f"{source_venue}_"
    for key in raw_streams:
        if key.startswith(source_prefix):
            parts = key[len(source_prefix):].split("/")
            if parts and parts[0]:
                assets.add(parts[0])

    per_pair: dict[str, Any] = {}

    for asset in sorted(assets):
        source_key = f"{source_venue}_{asset}/USDT"
        source_data = raw_streams.get(source_key)

        if not source_data or source_data.get("first_ts_ns") is None:
            per_pair[asset] = {"error": "no_source_data"}
            continue

        source_min = source_data["first_ts_ns"]
        source_max = source_data["last_ts_ns"]

        target_mins: list[int] = []
        target_maxs: list[int] = []
        target_tick_count = 0

        for tv in target_venues:
            tk = f"{tv}_{asset}/USD"
            td = raw_streams.get(tk)
            if td and td.get("first_ts_ns") is not None:
                target_mins.append(td["first_ts_ns"])
                target_maxs.append(td["last_ts_ns"])
                target_tick_count += td["tick_count"]

        if not target_mins:
            per_pair[asset] = {"error": "no_target_data"}
            continue

        target_min = min(target_mins)
        target_max = max(target_maxs)

        overlap_start = max(source_min, target_min)
        overlap_end = min(source_max, target_max)
        overlap_seconds = max(
            0, (overlap_end - overlap_start) / 1e9
        ) if overlap_end > overlap_start else 0

        per_pair[asset] = {
            "source_start": _ns_to_iso(source_min),
            "source_end": _ns_to_iso(source_max),
            "source_start_ns": source_min,
            "source_end_ns": source_max,
            "source_tick_count": source_data["tick_count"],
            "target_start": _ns_to_iso(target_min),
            "target_end": _ns_to_iso(target_max),
            "target_start_ns": target_min,
            "target_end_ns": target_max,
            "target_tick_count": target_tick_count,
            "overlap_start": _ns_to_iso(overlap_start) if overlap_start <= overlap_end else None,
            "overlap_end": _ns_to_iso(overlap_end) if overlap_start <= overlap_end else None,
            "overlap_start_ns": overlap_start if overlap_start <= overlap_end else None,
            "overlap_end_ns": overlap_end if overlap_start <= overlap_end else None,
            "overlap_duration_seconds": round(overlap_seconds, 2),
        }

    # Global overlap
    overlap_starts: list[int] = []
    overlap_ends: list[int] = []
    for v in per_pair.values():
        if isinstance(v, dict) and v.get("overlap_start_ns") is not None:
            overlap_starts.append(v["overlap_start_ns"])
            overlap_ends.append(v["overlap_end_ns"])

    if overlap_starts:
        global_start = max(overlap_starts)
        global_end = min(overlap_ends)
        global_duration = max(0, (global_end - global_start) / 1e9)
    else:
        global_start = global_end = None
        global_duration = 0.0

    return {
        "global_overlap_start": _ns_to_iso(global_start) if global_start is not None else None,
        "global_overlap_start_ns": global_start,
        "global_overlap_end": _ns_to_iso(global_end) if global_end is not None else None,
        "global_overlap_end_ns": global_end,
        "global_overlap_duration_seconds": round(global_duration, 2),
        "per_pair": per_pair,
    }


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def _determine_verdict(
    schema_ok: bool,
    schema_reason: str,
    manifest: dict[str, Any] | None,
    stream_comparisons: list[dict[str, Any]],
    overlap_delta: float | None,
    malformed_totals: dict[str, int],
    unparsed_files: list[str],
) -> tuple[str, str]:
    """
    Determine validation verdict and a human-readable summary reason.

    Returns (verdict, reason_string).
    """
    if manifest is None:
        return VERDICT_MANIFEST_MISSING, "capture_manifest.json not found"

    if not schema_ok:
        return VERDICT_SCHEMA_UNSUPPORTED, schema_reason

    # Check for malformed lines
    total_malformed = sum(malformed_totals.values())
    if total_malformed > 0:
        return VERDICT_JSONL_MALFORMED, f"{total_malformed} malformed JSONL line(s) found"

    if unparsed_files:
        return VERDICT_FAILED, (
            f"{len(unparsed_files)} file(s) did not match expected trades_*.jsonl pattern; "
            "cannot map to streams"
        )

    # Check stream comparisons
    mismatches = [c for c in stream_comparisons if c.get("tick_count_delta", 0) != 0]
    status_mismatches = [c for c in stream_comparisons if c.get("status_mismatch")]
    file_missing = [c for c in stream_comparisons if c.get("raw_tick_count") is None]
    file_errors = [c for c in stream_comparisons if c.get("file_error")]

    if mismatches or status_mismatches or file_missing or file_errors:
        issues = []
        if file_missing:
            for c in file_missing:
                issues.append(f"stream '{c['stream_name']}' missing JSONL file")
        if file_errors:
            for c in file_errors:
                issues.append(f"stream '{c['stream_name']}' file error: {c['file_error']}")
        for c in mismatches:
            issues.append(
                f"stream '{c['stream_name']}' tick count delta: "
                f"manifest={c['manifest_tick_count']} raw={c['raw_tick_count']} "
                f"delta={c['tick_count_delta']:+d}"
            )
        for c in status_mismatches:
            issues.append(
                f"stream '{c['stream_name']}' status mismatch: "
                f"manifest='{c['manifest_status']}' raw_present={c['raw_tick_count'] is not None}"
            )
        reason = "; ".join(issues[:10])
        if len(issues) > 10:
            reason += f" (and {len(issues) - 10} more)"
        return VERDICT_FAILED, reason

    # Check overlap
    if overlap_delta is not None and abs(overlap_delta) > 0.5:
        return VERDICT_WARNINGS, (
            f"overlap delta {overlap_delta:.2f}s exceeds 0.5s threshold"
        )

    return VERDICT_PASSED, "all checks passed"


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------


def validate_capture(
    capture_dir: str,
    quarantine_file: str | None = None,
    quarantine_on_failure: bool = False,
) -> dict[str, Any]:
    """
    Run validation against a capture directory.

    Returns the validation result dict (also written to disk).
    """
    capture_path = Path(capture_dir).resolve()
    manifest_path = capture_path / "capture_manifest.json"
    generated_at = _ts_now_iso()

    # ------------------------------------------------------------------
    # 1. Read manifest
    # ------------------------------------------------------------------
    manifest: dict[str, Any] | None = None
    manifest_read_error: str | None = None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest_read_error = "File not found"
    except json.JSONDecodeError as exc:
        manifest_read_error = f"Invalid JSON: {exc}"
    except OSError as exc:
        manifest_read_error = str(exc)

    if manifest is None:
        return {
            "verdict": VERDICT_MANIFEST_MISSING,
            "verdict_reason": manifest_read_error or "unknown",
            "generated_at": generated_at,
            "capture_dir": str(capture_path),
            "manifest_path": str(manifest_path),
            "git_sha": "",
            "schema_version_ok": False,
            "schema_version_reason": manifest_read_error or "no_manifest",
            "run_id": None,
            "streams": [],
            "overlap_comparison": None,
            "total_manifest_ticks": 0,
            "total_raw_ticks": 0,
            "total_malformed": 0,
            "unparsed_files": [],
        }

    # ------------------------------------------------------------------
    # 2. Schema version check
    # ------------------------------------------------------------------
    schema_ok, schema_reason = check_schema_version(
        manifest, allow_missing=True
    )

    # Extract run_id and git_sha from manifest metadata
    run_id = manifest.get("run_id")
    meta = manifest.get("_metadata", {}) or {}
    git_sha = (
        meta.get("git_sha")
        or manifest.get("git_sha")
        or ""
    )

    # ------------------------------------------------------------------
    # 3. Discover JSONL trade files
    # ------------------------------------------------------------------
    # First check if manifest has per-stream file listing
    streams_in_manifest = manifest.get("streams", {})
    jsonl_paths: list[Path] = []

    # Glob for trades_*.jsonl files in capture_dir
    jsonl_paths = sorted(capture_path.glob("trades_*.jsonl"))

    # Open interest files are not trade streams — skip them
    oi_paths = sorted(capture_path.glob("open_interest_*.jsonl"))

    stream_map, unparsed_files = _map_jsonl_to_stream(jsonl_paths)

    # ------------------------------------------------------------------
    # 4. Walk each JSONL stream file
    # ------------------------------------------------------------------
    raw_streams: dict[str, dict[str, Any]] = {}
    for stream_name, filepath in stream_map.items():
        raw_streams[stream_name] = _read_jsonl_counts(filepath)

    # ------------------------------------------------------------------
    # 5. Compare with manifest per-stream
    # ------------------------------------------------------------------
    stream_comparisons: list[dict[str, Any]] = []
    total_manifest_ticks = 0
    total_raw_ticks = 0
    total_malformed = 0

    for stream_name in sorted(streams_in_manifest.keys()):
        manifest_s = streams_in_manifest[stream_name]
        manifest_ticks = manifest_s.get("tick_count", 0)
        manifest_first_ns = manifest_s.get("first_tick_ts_ns")
        manifest_last_ns = manifest_s.get("last_tick_ts_ns")
        manifest_status = manifest_s.get("status", "unknown")
        total_manifest_ticks += manifest_ticks

        raw_s = raw_streams.get(stream_name)
        if raw_s is None:
            stream_comparisons.append({
                "stream_name": stream_name,
                "manifest_tick_count": manifest_ticks,
                "raw_tick_count": None,
                "tick_count_delta": None,
                "manifest_first_ts": _ns_to_iso(manifest_first_ns),
                "manifest_last_ts": _ns_to_iso(manifest_last_ns),
                "raw_first_ts": None,
                "raw_last_ts": None,
                "malformed_line_count": 0,
                "manifest_status": manifest_status,
                "status_mismatch": True,
                "file_error": "jsonl_file_not_found",
            })
            continue

        raw_ticks = raw_s["tick_count"]
        raw_first = raw_s["first_ts_ns"]
        raw_last = raw_s["last_ts_ns"]
        malformed = raw_s["malformed_count"]
        file_error = raw_s["error"]
        total_raw_ticks += raw_ticks
        total_malformed += malformed

        delta = raw_ticks - manifest_ticks

        status_mismatch = False
        if manifest_status in ("ok", "missing", "failed"):
            if manifest_status == "ok" and raw_ticks == 0:
                status_mismatch = True
            if manifest_status == "missing" and raw_ticks > 0:
                status_mismatch = True

        stream_comparisons.append({
            "stream_name": stream_name,
            "manifest_tick_count": manifest_ticks,
            "raw_tick_count": raw_ticks,
            "tick_count_delta": delta,
            "manifest_first_ts": _ns_to_iso(manifest_first_ns),
            "manifest_last_ts": _ns_to_iso(manifest_last_ns),
            "raw_first_ts": _ns_to_iso(raw_first),
            "raw_last_ts": _ns_to_iso(raw_last),
            "malformed_line_count": malformed,
            "manifest_status": manifest_status,
            "status_mismatch": status_mismatch,
            "file_error": file_error,
        })

    # Check for raw streams not in manifest (orphan files)
    orphan_streams: list[str] = []
    for stream_name in sorted(raw_streams.keys()):
        if stream_name not in streams_in_manifest:
            orphan_streams.append(stream_name)
            raw_s = raw_streams[stream_name]
            stream_comparisons.append({
                "stream_name": stream_name,
                "manifest_tick_count": 0,
                "raw_tick_count": raw_s["tick_count"],
                "tick_count_delta": raw_s["tick_count"],
                "manifest_first_ts": None,
                "manifest_last_ts": None,
                "raw_first_ts": _ns_to_iso(raw_s["first_ts_ns"]),
                "raw_last_ts": _ns_to_iso(raw_s["last_ts_ns"]),
                "malformed_line_count": raw_s["malformed_count"],
                "manifest_status": "not_in_manifest",
                "status_mismatch": True,
                "file_error": raw_s["error"],
            })
            # Count orphan ticks in aggregate totals
            total_raw_ticks += raw_s["tick_count"]
            total_malformed += raw_s["malformed_count"]

    # ------------------------------------------------------------------
    # 6. Recompute overlap from raw data
    # ------------------------------------------------------------------
    manifest_overlap = manifest.get("overlap", {})
    recomputed_overlap = _recompute_overlap(raw_streams)

    manifest_global_duration = manifest_overlap.get(
        "global_overlap_duration_seconds", 0.0
    )
    recomputed_global_duration = recomputed_overlap.get(
        "global_overlap_duration_seconds", 0.0
    )
    overlap_delta = round(
        recomputed_global_duration - manifest_global_duration, 4
    )

    overlap_comparison = {
        "manifest_global_overlap_duration_seconds": manifest_global_duration,
        "recomputed_global_overlap_duration_seconds": recomputed_global_duration,
        "overlap_delta_seconds": overlap_delta,
    }

    # ------------------------------------------------------------------
    # 7. Determine verdict
    # ------------------------------------------------------------------
    verdict, verdict_reason = _determine_verdict(
        schema_ok=schema_ok,
        schema_reason=schema_reason,
        manifest=manifest,
        stream_comparisons=stream_comparisons,
        overlap_delta=overlap_delta,
        malformed_totals={
            c["stream_name"]: c["malformed_line_count"]
            for c in stream_comparisons
        },
        unparsed_files=unparsed_files,
    )

    # ------------------------------------------------------------------
    # 8. Build result
    # ------------------------------------------------------------------
    result: dict[str, Any] = {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "generated_at": generated_at,
        "capture_dir": str(capture_path),
        "manifest_path": str(manifest_path),
        "run_id": run_id,
        "git_sha": git_sha,
        "schema_version_ok": schema_ok,
        "schema_version_reason": schema_reason,
        "total_manifest_ticks": total_manifest_ticks,
        "total_raw_ticks": total_raw_ticks,
        "total_malformed": total_malformed,
        "unparsed_files": unparsed_files,
        "streams": stream_comparisons,
        "overlap_comparison": overlap_comparison,
        "manifest_overlap": manifest_overlap,
        "recomputed_overlap": recomputed_overlap,
        "orphan_streams": orphan_streams,
        "oi_file_count": len(oi_paths),
    }

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _format_stream_row(c: dict[str, Any]) -> str:
    name = c["stream_name"]
    m_ticks = c["manifest_tick_count"]
    r_ticks = c["raw_tick_count"]
    delta = c["tick_count_delta"]
    mal = c["malformed_line_count"]
    status = c["manifest_status"]
    status_mm = " **STATUS MISMATCH**" if c.get("status_mismatch") else ""
    error = f" [ERROR: {c['file_error']}]" if c.get("file_error") else ""
    missing = " [FILE NOT FOUND]" if r_ticks is None else ""

    if status == "not_in_manifest":
        return (
            f"| {name:<40} | {r_ticks:>8} | (orphan) | N/A | {mal:>4} | "
            f"not_in_manifest{status_mm}{error} |"
        )

    delta_str = f"{delta:+d}" if delta is not None else "N/A"
    r_str = f"{r_ticks:>8}" if r_ticks is not None else "  (none)"

    return (
        f"| {name:<40} | {m_ticks:>8} | {r_str} | {delta_str:>6} | {mal:>4} | "
        f"{status}{status_mm}{error}{missing} |"
    )


def _format_overlap_table(
    manifest_overlap: dict[str, Any],
    recomputed_overlap: dict[str, Any],
) -> str:
    lines = []
    lines.append("### Overlap: Per-Pair")
    lines.append("")
    lines.append(
        "| Asset | Manifest (s) | Recomputed (s) | Delta (s) | Status |"
    )
    lines.append(
        "|-------|-------------|----------------|-----------|--------|"
    )

    manifest_pairs = manifest_overlap.get("per_pair", {})
    recomputed_pairs = recomputed_overlap.get("per_pair", {})

    all_assets = sorted(
        set(manifest_pairs.keys()) | set(recomputed_pairs.keys())
    )

    for asset in all_assets:
        mp = manifest_pairs.get(asset, {})
        rp = recomputed_pairs.get(asset, {})

        m_dur = mp.get("overlap_duration_seconds", "N/A")
        r_dur = rp.get("overlap_duration_seconds", "N/A")

        if isinstance(m_dur, (int, float)) and isinstance(r_dur, (int, float)):
            delta = round(r_dur - m_dur, 4)
            delta_str = f"{delta:+g}"
            ok = "OK" if abs(delta) < 0.5 else "WARN"
        else:
            delta_str = "N/A"
            ok = "N/A" if "error" in mp or "error" in rp else "OK"

        lines.append(
            f"| {asset:<5} | {m_dur!s:>11} | {r_dur!s:>12} | "
            f"{delta_str:>9} | {ok} |"
        )

    lines.append("")
    lines.append("### Overlap: Global")
    lines.append("")

    m_global = manifest_overlap.get("global_overlap_duration_seconds", 0.0)
    r_global = recomputed_overlap.get("global_overlap_duration_seconds", 0.0)
    delta_global = round(r_global - m_global, 4)

    lines.append(
        f"- **Manifest global overlap:** {m_global}s"
    )
    lines.append(
        f"- **Recomputed global overlap:** {r_global}s"
    )
    lines.append(
        f"- **Delta:** {delta_global:+g}s"
    )

    return "\n".join(lines)


def build_report(result: dict[str, Any]) -> str:
    """Build a human-readable Markdown validation report."""
    verdict = result["verdict"]
    lines: list[str] = []

    # Title
    lines.append("# Capture Validation Report")
    lines.append("")
    lines.append(f"- **Verdict:** `{verdict}`")
    lines.append(f"- **Reason:** {result['verdict_reason']}")
    lines.append(f"- **Generated at:** {result['generated_at']}")
    lines.append(f"- **Capture dir:** {result['capture_dir']}")
    lines.append(f"- **Run ID:** {result.get('run_id', 'N/A')}")
    lines.append(f"- **Git SHA:** {result.get('git_sha', 'N/A')}")
    lines.append("")

    # Schema info
    lines.append("## Schema Version")
    lines.append("")
    lines.append(
        f"- **OK:** {result['schema_version_ok']}"
    )
    lines.append(
        f"- **Reason:** {result['schema_version_reason']}"
    )
    lines.append("")

    # Summary counts
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total manifest ticks:** {result['total_manifest_ticks']}")
    lines.append(f"- **Total raw ticks:** {result['total_raw_ticks']}")
    lines.append(f"- **Total malformed lines:** {result['total_malformed']}")
    lines.append(f"- **Unparsed files:** {len(result.get('unparsed_files', []))}")
    lines.append(f"- **Orphan streams:** {len(result.get('orphan_streams', []))}")
    lines.append("")

    # Per-stream table
    lines.append("## Per-Stream Comparison")
    lines.append("")
    lines.append(
        "| Stream | Manifest Ticks | Raw Ticks | Delta | Malformed | Status |"
    )
    lines.append(
        "|--------|---------------:|----------:|------:|----------:|--------|"
    )
    for c in result.get("streams", []):
        lines.append(_format_stream_row(c))
    lines.append("")

    # Overlap
    lines.append(
        _format_overlap_table(
            result.get("manifest_overlap", {}),
            result.get("recomputed_overlap", {}),
        )
    )
    lines.append("")

    # Orphan streams
    orphans = result.get("orphan_streams", [])
    if orphans:
        lines.append("## Orphan Streams (in files, not in manifest)")
        lines.append("")
        for o in orphans:
            lines.append(f"- `{o}`")
        lines.append("")

    # Unparsed files
    unparsed = result.get("unparsed_files", [])
    if unparsed:
        lines.append("## Unparsed Files")
        lines.append("")
        for u in unparsed:
            lines.append(f"- `{u}`")
        lines.append("")

    # OI count
    oi_count = result.get("oi_file_count", 0)
    lines.append("## Open Interest Files")
    lines.append("")
    lines.append(f"- **Count:** {oi_count}")
    lines.append("")

    # Verdict details
    lines.append("## Verdict Details")
    lines.append("")
    lines.append(f"- **Verdict:** `{verdict}`")
    lines.append(f"- **Reason:** {result['verdict_reason']}")
    lines.append("")

    lines.append("---")
    lines.append(f"*Report generated at {result['generated_at']}*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quarantine helper
# ---------------------------------------------------------------------------


def _append_quarantine(
    result: dict[str, Any],
    quarantine_file: str,
) -> None:
    """Append a quarantine row for runs that failed validation."""
    quarantine_path = Path(quarantine_file).resolve()
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "run_id": result.get("run_id"),
        "verdict": result["verdict"],
        "verdict_reason": result["verdict_reason"],
        "capture_dir": result["capture_dir"],
        "generated_at": result["generated_at"],
        "total_malformed": result["total_malformed"],
        "total_manifest_ticks": result["total_manifest_ticks"],
        "total_raw_ticks": result["total_raw_ticks"],
    }

    with open(quarantine_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Validate a capture directory against its manifest. "
                    "Read-only — never modifies capture files."
    )
    p.add_argument(
        "--capture-dir",
        type=str,
        required=True,
        help="Path to capture directory (containing capture_manifest.json)",
    )
    p.add_argument(
        "--out",
        type=str,
        default="reports/capture_validation",
        help="Output directory for validation results "
             "(default: reports/capture_validation)",
    )
    p.add_argument(
        "--quarantine-file",
        type=str,
        default=None,
        help="Path to quarantine JSONL file "
             "(default: reports/research_run_quarantine.jsonl)",
    )
    p.add_argument(
        "--quarantine-on-failure",
        action="store_true",
        default=False,
        help="Auto-quarantine the run on FAILED/MISSING/UNSUPPORTED verdicts",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Determine quarantine file path (default relative to cwd)
    quarantine_file = args.quarantine_file
    if quarantine_file is None:
        quarantine_file = str(Path.cwd() / "reports" / "research_run_quarantine.jsonl")

    # Run validation
    result = validate_capture(
        capture_dir=args.capture_dir,
        quarantine_file=quarantine_file,
        quarantine_on_failure=args.quarantine_on_failure,
    )

    # Prepare output directory
    out_dir = safe_output_dir(Path(args.out), allow_existing=True)

    # Write validation_summary.json
    summary_path = out_dir / "validation_summary.json"
    atomic_write_json(summary_path, result)

    # Write validation_report.md
    report_text = build_report(result)
    report_path = out_dir / "validation_report.md"
    atomic_write_text(report_path, report_text)

    print(f"  Validation summary: {summary_path}")
    print(f"  Validation report:  {report_path}")
    print(f"  Verdict:            {result['verdict']}")
    print(f"  Reason:             {result['verdict_reason']}")

    # Append run-index row
    run_id = result.get("run_id")
    if run_id:
        command_args = " ".join(sys.argv[1:])
        row = build_run_index_row(
            run_id=run_id,
            run_type="validation",
            status="completed",
            command_args=command_args,
            capture_dir=result["capture_dir"],
            report_dir=str(out_dir.resolve()),
            schema_version=(
                result.get("git_sha")
                or None
            ),
            summary_path=str(summary_path.resolve()),
            manifest_path=result.get("manifest_path"),
            notes=(
                f"verdict={result['verdict']}: {result['verdict_reason']}"
            ),
        )
        append_run_index_row(row)

    # Auto-quarantine on failure
    quarantine_verdicts = {
        VERDICT_FAILED,
        VERDICT_MANIFEST_MISSING,
        VERDICT_SCHEMA_UNSUPPORTED,
    }
    if args.quarantine_on_failure and result["verdict"] in quarantine_verdicts:
        _append_quarantine(result, quarantine_file)
        print(f"  Quarantined to:     {quarantine_file}")

    # Exit with appropriate code
    if result["verdict"] == VERDICT_PASSED:
        sys.exit(0)
    elif result["verdict"] == VERDICT_WARNINGS:
        sys.exit(0)  # warnings are informational
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
