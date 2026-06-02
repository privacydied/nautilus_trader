"""Read-only CLI for the runner metadata registry.

Exposed operations:

- list registered runner specs (optionally filtered)
- inspect a single runner spec by key
- deterministic text or JSON output

This module never imports heavy runner implementations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence

from .registry import RunnerSpec, get_runner_spec, iter_runner_specs


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

_SPEC_FIELD_ORDER: tuple[str, ...] = (
    "key",
    "family",
    "venue",
    "study_id",
    "description",
    "cli_module",
    "implementation_module",
    "package_module",
    "legacy_module",
    "tags",
)


def runner_spec_to_dict(spec: RunnerSpec) -> dict[str, object]:
    """Return a deterministic dict for a single ``RunnerSpec``."""
    return {field: getattr(spec, field) for field in _SPEC_FIELD_ORDER}


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_specs(
    specs: Iterable[RunnerSpec],
    *,
    venue: str | None = None,
    family: str | None = None,
    tag: str | None = None,
) -> tuple[RunnerSpec, ...]:
    """Return specs matching all provided filters (AND logic)."""
    result: list[RunnerSpec] = []
    for spec in specs:
        if venue is not None and spec.venue != venue:
            continue
        if family is not None and spec.family != family:
            continue
        if tag is not None and tag not in spec.tags:
            continue
        result.append(spec)
    return tuple(result)


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------


def format_runner_list_text(specs: Sequence[RunnerSpec]) -> str:
    """Return deterministic text for a list of runner specs."""
    lines: list[str] = [f"Registered runner specs: {len(specs)}", ""]
    for spec in specs:
        lines.append(f"- {spec.key}")
        lines.append(f"  venue: {spec.venue}")
        lines.append(f"  family: {spec.family}")
        lines.append(f"  study_id: {spec.study_id}")
        tags_str = ", ".join(spec.tags) if spec.tags else "(none)"
        lines.append(f"  tags: {tags_str}")
    return "\n".join(lines) + "\n"


def format_runner_detail_text(spec: RunnerSpec) -> str:
    """Return deterministic text for a single runner spec."""
    lines: list[str] = []
    for field in _SPEC_FIELD_ORDER:
        value = getattr(spec, field)
        if isinstance(value, tuple):
            value = ", ".join(value) if value else "(none)"
        lines.append(f"{field}: {value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# JSON formatting
# ---------------------------------------------------------------------------


def format_json_payload(specs: Sequence[RunnerSpec], *, single: bool = False) -> str:
    """Return deterministic JSON for one or more runner specs."""
    dicts = [runner_spec_to_dict(s) for s in specs]
    if single:
        payload: dict[str, object] = {"runner": dicts[0]}
    else:
        payload = {"runners": dicts}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the registry CLI."""
    parser = argparse.ArgumentParser(
        description="Read-only CLI for the runner metadata registry.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="List all registered runner specs (default when no mode given).",
    )
    mode.add_argument(
        "--key",
        type=str,
        default=None,
        metavar="KEY",
        help="Inspect a single runner spec by key.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Emit JSON output instead of text.",
    )
    filter_group = parser.add_argument_group("filters (list mode only)")
    filter_group.add_argument(
        "--venue",
        type=str,
        default=None,
        help="Filter by exact venue.",
    )
    filter_group.add_argument(
        "--family",
        type=str,
        default=None,
        help="Filter by exact family.",
    )
    filter_group.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Filter by exact tag membership.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the registry CLI. Returns an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # --venue/--family/--tag with --key is rejected
    if args.key is not None and any([args.venue, args.family, args.tag]):
        parser.error("filters (--venue, --family, --tag) cannot be used with --key")

    # Default to --list when no mode given
    list_mode = args.list or args.key is None

    if args.key is not None:
        spec = get_runner_spec(args.key)
        if spec is None:
            print(f"error: unknown runner key: {args.key!r}", file=sys.stderr)
            return 2
        if args.json_output:
            sys.stdout.write(format_json_payload((spec,), single=True))
        else:
            sys.stdout.write(format_runner_detail_text(spec))
        return 0

    # List mode
    specs = filter_specs(
        iter_runner_specs(),
        venue=args.venue,
        family=args.family,
        tag=args.tag,
    )
    if args.json_output:
        sys.stdout.write(format_json_payload(specs))
    else:
        sys.stdout.write(format_runner_list_text(specs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
