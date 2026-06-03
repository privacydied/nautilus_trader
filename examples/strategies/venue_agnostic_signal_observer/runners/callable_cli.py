"""Dependency-light helper for callable runner CLI metadata.

This module supports Level-2 callable CLI specs safely and consistently. It is
metadata/resolution only and deliberately dependency-light.

Importing this module must not:
  - import the runner registry, cli_specs, or registry_cli;
  - import hypothesis runners or legacy run_*.py modules;
  - import paper/governance/conductor/bot/shadow/live execution code;
  - run any CLI module or call any resolved callable.

Callable references use the ``"module.path:callable_name"`` syntax. They are
resolved lazily via :func:`importlib.import_module` only when explicitly
requested, and the resolved target is never invoked by this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import importlib


@dataclass(frozen=True, slots=True)
class CallableRef:
    """A parsed ``module:callable`` reference."""

    module: str
    name: str

    @property
    def dotted(self) -> str:
        return f"{self.module}:{self.name}"


def parse_callable_ref(value: str) -> CallableRef:
    """Parse a ``"module.path:callable_name"`` reference.

    Requires exactly one colon, a non-empty module that does not start or end
    with a dot, and a non-empty callable name that contains neither dots nor
    colons. Raises ``ValueError`` on invalid input.
    """

    if not isinstance(value, str):
        raise ValueError(f"callable reference must be a string, got {type(value)!r}")
    if value.count(":") != 1:
        raise ValueError(
            f"callable reference must contain exactly one ':', got {value!r}"
        )
    module, name = value.split(":")
    if not module:
        raise ValueError(f"callable reference has empty module: {value!r}")
    if not name:
        raise ValueError(f"callable reference has empty callable name: {value!r}")
    if module.startswith(".") or module.endswith("."):
        raise ValueError(
            f"callable reference module must not start/end with '.': {value!r}"
        )
    if "." in name:
        raise ValueError(
            f"callable reference name must not contain '.': {value!r}"
        )
    return CallableRef(module=module, name=name)


def try_parse_callable_ref(value: str | None) -> CallableRef | None:
    """Parse ``value`` if possible, returning ``None`` for ``None``/empty input."""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return parse_callable_ref(value)
    except ValueError:
        return None


def resolve_callable_ref(ref: CallableRef | str) -> Callable[..., object]:
    """Resolve ``ref`` to a callable, importing its module lazily.

    Accepts a :class:`CallableRef` or a string (parsed via
    :func:`parse_callable_ref`). The target module is imported lazily via
    ``importlib.import_module``. A missing module/attribute naturally raises
    ``ImportError``/``AttributeError``; a non-callable target raises
    ``TypeError``. The resolved callable is never invoked here.
    """

    if isinstance(ref, str):
        ref = parse_callable_ref(ref)
    module = importlib.import_module(ref.module)
    target = getattr(module, ref.name)
    if not callable(target):
        raise TypeError(
            f"resolved attribute {ref.dotted!r} is not callable: {type(target)!r}"
        )
    return target


def require_cli_spec_main_callable(spec: object) -> CallableRef:
    """Return the parsed ``spec.main_callable`` reference.

    Raises ``ValueError`` if the spec has no ``main_callable`` (absent, ``None``,
    or empty). Does not import the target module or call the target.
    """

    raw = getattr(spec, "main_callable", None)
    if raw is None:
        raise ValueError("CLI spec has no main_callable")
    if isinstance(raw, str) and not raw.strip():
        raise ValueError("CLI spec has empty main_callable")
    return parse_callable_ref(raw)


def cli_spec_has_callable_main(spec: object) -> bool:
    """Return whether ``spec`` exposes a parseable ``main_callable``.

    Does not import the target module.
    """

    try:
        require_cli_spec_main_callable(spec)
    except ValueError:
        return False
    return True


def assert_help_only_argv(argv: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return ``("--help",)`` iff ``argv`` is exactly that, else ``ValueError``."""

    as_tuple = tuple(argv)
    if as_tuple != ("--help",):
        raise ValueError(f"only ('--help',) is permitted, got {as_tuple!r}")
    return ("--help",)


__all__ = (
    "CallableRef",
    "assert_help_only_argv",
    "cli_spec_has_callable_main",
    "parse_callable_ref",
    "require_cli_spec_main_callable",
    "resolve_callable_ref",
    "try_parse_callable_ref",
)
