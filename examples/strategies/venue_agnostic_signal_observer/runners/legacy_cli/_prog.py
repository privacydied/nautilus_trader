from __future__ import annotations

import argparse


_LEGACY_CLI_SEGMENT = "runners.legacy_cli."


def legacy_root_module_name(module_name: str) -> str:
    """Map a moved ``runners.legacy_cli`` module name back to its old root path."""
    return module_name.replace(_LEGACY_CLI_SEGMENT, "", 1)


def set_legacy_prog(parser: argparse.ArgumentParser, module_name: str | None = None) -> None:
    """Pin argparse ``prog`` to the pre-move root module path.

    When a moved CLI is executed via ``python -m
    examples...runners.legacy_cli.run_x``, argparse derives ``prog`` from the
    new spec name. This rewrites it to the historical root path so ``--help``
    output is byte-for-byte identical to the pre-move behaviour.
    """
    parser.prog = legacy_root_module_name(parser.prog)
