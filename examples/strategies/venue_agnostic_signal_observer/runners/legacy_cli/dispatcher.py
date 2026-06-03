"""Help-only dispatcher for migrated legacy ``run_*`` entrypoints.

Unit 28 intentionally keeps this dispatcher dependency-light and help-only:

* It never imports the moved CLI modules at import time. Modules are imported
  lazily, only when an explicit ``--key`` dispatch is requested.
* It never imports hypothesis runners, the runner registry, CLI specs, or any
  paper/governance/conductor/bot/shadow/live module.
* It refuses to execute anything other than ``--help``. Direct study execution
  through the dispatcher remains intentionally disabled in this unit.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
from collections.abc import Sequence

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy_cli",
        description="Dispatch migrated legacy run_* entrypoints by key (help-only).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available legacy CLI entrypoints and exit.",
    )
    parser.add_argument(
        "--key",
        help="Legacy entrypoint key to dispatch.",
    )
    parser.add_argument(
        "--help-only",
        action="store_true",
        help="Run the selected entrypoint with --help only (the only supported execution).",
    )
    return parser


def _main_accepts_argv(main_callable: object) -> bool:
    if not callable(main_callable):
        return False
    try:
        signature = inspect.signature(main_callable)
    except (TypeError, ValueError):  # pragma: no cover - builtins without signatures
        return False
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        for entry in iter_legacy_entrypoints():
            print(entry.key)
        return 0

    if not args.key:
        parser.error("--key is required unless --list is used")

    entry = get_legacy_entrypoint(args.key)
    if entry is None:
        parser.error(f"unknown legacy entrypoint key: {args.key}")

    if not args.help_only:
        parser.error(
            "Unit 28 dispatcher only supports --help-only; direct execution remains "
            "intentionally disabled",
        )

    module = importlib.import_module(entry.module)
    main_callable = getattr(module, "main", None)
    if main_callable is None:
        raise SystemExit(f"legacy entrypoint {args.key!r} does not expose main()")
    if not _main_accepts_argv(main_callable):
        raise SystemExit(
            f"legacy entrypoint {args.key!r} main() does not accept argv; "
            "help-only dispatch is unsupported for this entrypoint",
        )

    result = main_callable(["--help"])
    return 0 if result is None else int(result)
