#!/usr/bin/env python3
"""
Registry correction writer — SEPARATE from the scout.

Reads an existing completed report directory and appends the corrective
registry note to REJECTED_RESEARCH.md. Requires explicit --authorize flag.
Must NOT import the scout module or run any discovery/API/archive logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Write HIP-3 builder DEX registry correction to REJECTED_RESEARCH.md")
    parser.add_argument("--from-report", required=True, help="Path to completed report directory")
    parser.add_argument("--authorize", action="store_true", default=False,
                        help="Required to actually write to REJECTED_RESEARCH.md")
    args = parser.parse_args()

    report_dir = Path(args.from_report)
    if not report_dir.exists():
        print(f"ERROR: Report directory not found: {report_dir}", file=sys.stderr)
        sys.exit(1)

    # Read corrective preview
    preview_path = report_dir / "corrective_registry_note_preview.md"
    if not preview_path.exists():
        print(f"ERROR: corrective_registry_note_preview.md not found in {report_dir}", file=sys.stderr)
        sys.exit(1)
    preview_text = preview_path.read_text()

    # Read gate decisions for status
    gate_path = report_dir / "gate_decisions.json"
    if gate_path.exists():
        gate_decisions = json.loads(gate_path.read_text())
        final_status = gate_decisions.get("final_status", "unknown")
    else:
        final_status = "unknown"

    print(f"Registry correction preview:")
    print(f"  Report directory: {report_dir}")
    print(f"  Final status: {final_status}")
    print(f"  Preview length: {len(preview_text)} chars")
    print()

    if not args.authorize:
        print("DRY RUN: --authorize not set. Would append the following to REJECTED_RESEARCH.md:")
        print("=" * 72)
        print(preview_text[:2000])
        if len(preview_text) > 2000:
            print(f"... [{len(preview_text) - 2000} more chars]")
        print("=" * 72)
        print()
        print("To apply, re-run with --authorize")
        return

    # Find REJECTED_RESEARCH.md
    rejected_path = (Path(__file__).resolve().parent / "docs" / "REJECTED_RESEARCH.md")
    if not rejected_path.exists():
        print(f"ERROR: REJECTED_RESEARCH.md not found at {rejected_path}", file=sys.stderr)
        sys.exit(1)

    # Append
    with open(rejected_path, "a") as f:
        f.write("\n\n---\n\n")
        f.write(preview_text)

    print(f"SUCCESS: Appended corrective note to {rejected_path}")
    print(f"Final status recorded: {final_status}")


if __name__ == "__main__":
    main()
