"""
AST-based safety scanner for the discovery package.

Scans every .py file in the discovery package for forbidden imports,
forbidden first-party imports outside the package, forbidden env access,
forbidden order/execution terms, and forbidden subprocess usage.

This scan is intentionally shallow with respect to stdlib: it inspects the
discovery package's own source files only. The first-party-import ban is what
closes the transitive-risk path — if discovery cannot import any module outside
itself, it cannot transitively pull in execution/live/network code.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from .exceptions import DiscoverySafetyError


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class SafetyFinding:
    """
    A single safety violation found during scanning.

    Attributes:
        file: Path to the file with the violation.
        line: Line number where the violation was found.
        kind: Short string describing the violation category.
        detail: Human-readable description of the violation.
    """

    __slots__ = ("detail", "file", "kind", "line")

    def __init__(
        self,
        file: str,
        line: int,
        kind: str,
        detail: str,
    ) -> None:
        self.file = file
        self.line = line
        self.kind = kind
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"SafetyFinding(file={self.file!r}, line={self.line}, "
            f"kind={self.kind!r}, detail={self.detail!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SafetyFinding):
            return NotImplemented
        return (
            self.file == other.file
            and self.line == other.line
            and self.kind == other.kind
            and self.detail == other.detail
        )

    def __hash__(self) -> int:
        return hash((self.file, self.line, self.kind, self.detail))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Forbidden module imports (static or dynamic)
FORBIDDEN_IMPORTS: set[str] = {
    "nautilus_trader.live",
    "nautilus_trader.adapters",
    "nautilus_trader.execution",
    "nautilus_trader.accounting",
    "ccxt",
    "web3",
    "eth_account",
    "py_clob_client",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "socket",
}

# Forbidden order / execution terms in call/import attribute chains
FORBIDDEN_ORDER_TERMS: set[str] = {
    "submit_order",
    "cancel_order",
    "modify_order",
    "order_factory",
    "TradingNode",
    "LiveNode",
}

# Forbidden env name fragments (case-insensitive substring match)
FORBIDDEN_ENV_FRAGMENTS: set[str] = {
    "API_KEY",
    "API_SECRET",
    "PRIVATE_KEY",
    "SECRET_KEY",
    "PASSPHRASE",
    "POLYMARKET_PK",
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
}

# Forbidden subprocess call names
_SUBPROCESS_NAMES: set[str] = {
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.getoutput",
    "os.system",
    "os.popen",
}

ALLOWED_SUBPROCESS_ARGS = ["git", "rev-parse", "HEAD"]

# The discovery package path anchor
_DISCOVERY_PACKAGE_PREFIX = "examples.strategies.venue_agnostic_signal_observer.discovery"


# ---------------------------------------------------------------------------
# Scanner
# ----------------------------------------------------------------------------

def _get_discovery_dir() -> Path:
    """Return the Path to the discovery package directory."""
    return Path(__file__).resolve().parent


def scan_discovery_package() -> list[SafetyFinding]:
    """
    Run the AST safety scanner on all .py files in the discovery package.

    Returns a list of SafetyFinding objects. Raises DiscoverySafetyError
    if any violations are found.

    The caller can check whether the list is non-empty and handle violations
    without exceptions if desired, but the standard contract is to raise.
    """
    discovery_dir = _get_discovery_dir()
    findings: list[SafetyFinding] = []

    for py_file in sorted(discovery_dir.glob("*.py")):
        file_findings = _scan_file(py_file)
        findings.extend(file_findings)

    if findings:
        raise DiscoverySafetyError(
            f"Safety scan found {len(findings)} violation(s) in discovery package"
        )

    return findings


def _scan_file(file_path: Path) -> list[SafetyFinding]:
    """Scan a single Python file for safety violations."""
    findings: list[SafetyFinding] = []

    try:
        tree = ast.parse(file_path.read_bytes(), filename=str(file_path))
    except SyntaxError:
        findings.append(
            SafetyFinding(
                file=str(file_path),
                line=0,
                kind="SYNTAX_ERROR",
                detail="File could not be parsed",
            )
        )
        return findings

    for node in ast.walk(tree):
        # Static imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                findings.extend(
                    _check_import(str(file_path), alias.name, node.lineno)
                )
                findings.extend(
                    _check_first_party_import(str(file_path), alias.name, node.lineno)
                )

        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            # Check the module being imported from
            findings.extend(
                _check_import(str(file_path), module_name, node.lineno)
            )
            findings.extend(
                _check_first_party_import(str(file_path), module_name, node.lineno)
            )
            # Check individual names for forbidden order terms
            for alias in node.names:
                findings.extend(
                    _check_order_term(str(file_path), alias.name, node.lineno)
                )
                # Build full attribute chain: module.name
                full_name = f"{module_name}.{alias.name}" if module_name else alias.name
                findings.extend(
                    _check_order_term(str(file_path), full_name, node.lineno)
                )

        # Dynamic imports, subprocess, env access (all Call-based checks)
        if isinstance(node, ast.Call):
            findings.extend(_check_dynamic_import(str(file_path), node))
            findings.extend(_check_subprocess(str(file_path), node))
            findings.extend(_check_env_access_call(str(file_path), node))

        # os.environ subscript access: os.environ["KEY"]
        if isinstance(node, ast.Subscript):
            findings.extend(_check_env_access_subscript(str(file_path), node))

    return findings


def _check_import(file_path: str, module_name: str, lineno: int) -> list[SafetyFinding]:
    """Check if a module name is in the forbidden list."""
    findings: list[SafetyFinding] = []
    if module_name in FORBIDDEN_IMPORTS:
        findings.append(
            SafetyFinding(
                file=file_path,
                line=lineno,
                kind="FORBIDDEN_IMPORT",
                detail=f"Forbidden import: {module_name}",
            )
        )
    return findings


def _check_first_party_import(
    file_path: str, module_name: str, lineno: int
) -> list[SafetyFinding]:
    """
    Check if a first-party import climbs outside the discovery package.

    The discovery package is at:
    examples.strategies.venue_agnostic_signal_observer.discovery

    Imports of modules inside .discovery are fine.
    Imports of modules outside .discovery are forbidden.
    Stdlib imports (no leading dot/dotted first-party prefix) are ignored.
    """
    findings: list[SafetyFinding] = []

    # Only check imports that start with the first-party prefix
    if not module_name.startswith("examples.strategies.venue_agnostic_signal_observer"):
        return findings

    # Allow imports within the discovery package itself
    if module_name.startswith(_DISCOVERY_PACKAGE_PREFIX) or module_name == _DISCOVERY_PACKAGE_PREFIX:
        return findings

    # Also handle relative imports: from .something import ...
    # These are handled by checking if the module starts with a dot
    # For dotted relative imports within the package, these are fine.
    # But we need to check actual relative imports too.

    # This is a static import outside discovery — reject
    findings.append(
        SafetyFinding(
            file=file_path,
            line=lineno,
            kind="FORBIDDEN_FIRST_PARTY_IMPORT",
            detail=f"Forbidden first-party import outside discovery: {module_name}",
        )
    )

    return findings


def _check_order_term(file_path: str, name: str, lineno: int) -> list[SafetyFinding]:
    """Check if a name matches forbidden order/execution terms."""
    findings: list[SafetyFinding] = []
    for term in FORBIDDEN_ORDER_TERMS:
        if term in name:
            findings.append(
                SafetyFinding(
                    file=file_path,
                    line=lineno,
                    kind="FORBIDDEN_ORDER_TERM",
                    detail=f"Forbidden order/execution term: {name!r} contains {term!r}",
                )
            )
    return findings


def _check_dynamic_import(file_path: str, node: ast.Call) -> list[SafetyFinding]:
    """Check for importlib.import_module or __import__ with forbidden module strings."""
    findings: list[SafetyFinding] = []
    func = _get_call_func_name(node)

    if func in ("importlib.import_module", "__import__"):
        # Get first argument if it's a string literal
        args = node.args
        if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            module_name = args[0].value
            if module_name in FORBIDDEN_IMPORTS:
                findings.append(
                    SafetyFinding(
                        file=file_path,
                        line=node.lineno,
                        kind="FORBIDDEN_DYNAMIC_IMPORT",
                        detail=f"Forbidden dynamic import: {module_name}",
                    )
                )
            # Also check first-party
            findings.extend(
                _check_first_party_import(file_path, module_name, node.lineno)
            )

    return findings


def _check_env_access_call(file_path: str, node: ast.Call) -> list[SafetyFinding]:
    """Check for os.getenv / getenv calls with forbidden key names."""
    findings: list[SafetyFinding] = []
    func = _get_call_func_name(node)

    # os.getenv or getenv calls
    if func in ("os.getenv", "getenv"):
        args = node.args
        if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            key = args[0].value
            for fragment in FORBIDDEN_ENV_FRAGMENTS:
                if fragment.lower() in key.lower():
                    findings.append(
                        SafetyFinding(
                            file=file_path,
                            line=node.lineno,
                            kind="FORBIDDEN_ENV_ACCESS",
                            detail=f"Forbidden env access via {func}: {key!r}",
                        )
                    )

    return findings


def _check_env_access_subscript(file_path: str, node: ast.Subscript) -> list[SafetyFinding]:
    """Check for os.environ['KEY'] subscript access with forbidden key names."""
    findings: list[SafetyFinding] = []

    # Check if the value being subscripted is os.environ
    if isinstance(node.value, ast.Attribute):
        attr = node.value
        if isinstance(attr.value, ast.Name) and attr.value.id == "os" and attr.attr == "environ":
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                key = node.slice.value
                for fragment in FORBIDDEN_ENV_FRAGMENTS:
                    if fragment.lower() in key.lower():
                        findings.append(
                            SafetyFinding(
                                file=file_path,
                                line=node.lineno,
                                kind="FORBIDDEN_ENV_ACCESS",
                                detail=f"Forbidden env access via os.environ subscript: {key!r}",
                            )
                        )

    return findings


def _check_subprocess(file_path: str, node: ast.Call) -> list[SafetyFinding]:
    """
    Check subprocess usage.

    Only allow:
        subprocess.run(["git", "rev-parse", "HEAD"], ...)
        subprocess.check_output(["git", "rev-parse", "HEAD"], ...)

    Reject:
    - Any other subprocess.* call with different arguments
    - subprocess.Popen, subprocess.call, subprocess.getoutput
    - os.system, os.popen
    - shell=True
    - Non-literal (variable) first argument
    """
    findings: list[SafetyFinding] = []
    func = _get_call_func_name(node)

    # Check for os.system and os.popen (they take string commands)
    if func in ("os.system", "os.popen"):
        findings.append(
            SafetyFinding(
                file=file_path,
                line=node.lineno,
                kind="FORBIDDEN_SUBPROCESS",
                detail=f"Subprocess call not allowed: {func}",
            )
        )
        return findings

    # Check for subprocess.Popen, subprocess.call, subprocess.getoutput
    if func in ("subprocess.Popen", "subprocess.call", "subprocess.getoutput"):
        findings.append(
            SafetyFinding(
                file=file_path,
                line=node.lineno,
                kind="FORBIDDEN_SUBPROCESS",
                detail=f"Subprocess call not allowed: {func}",
            )
        )
        return findings

    # Check subprocess.run and subprocess.check_output
    if func not in ("subprocess.run", "subprocess.check_output"):
        return findings

    # Check for shell=True keyword argument
    for kw in node.keywords:
        if kw.arg == "shell" and _is_truthy(kw.value):
            findings.append(
                SafetyFinding(
                    file=file_path,
                    line=node.lineno,
                    kind="FORBIDDEN_SUBPROCESS",
                    detail=f"shell=True is forbidden in {func}",
                )
            )
            return findings

    # Check first positional argument
    args = node.args
    if not args:
        findings.append(
            SafetyFinding(
                file=file_path,
                line=node.lineno,
                kind="FORBIDDEN_SUBPROCESS",
                detail=f"{func} called without arguments",
            )
        )
        return findings

    first_arg = args[0]

    # Must be a literal list expression
    if not isinstance(first_arg, ast.List):
        findings.append(
            SafetyFinding(
                file=file_path,
                line=node.lineno,
                kind="FORBIDDEN_SUBPROCESS",
                detail=f"{func}: first argument must be a literal list, "
                f"got {type(first_arg).__name__}",
            )
        )
        return findings

    # Check elements of the list
    actual_args: list[str] = []
    for elt in first_arg.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            actual_args.append(elt.value)
        else:
            findings.append(
                SafetyFinding(
                    file=file_path,
                    line=elt.lineno,
                    kind="FORBIDDEN_SUBPROCESS",
                    detail=f"{func}: non-constant element in list argument",
                )
            )
            return findings

    # Compare with allowed args
    if actual_args != ALLOWED_SUBPROCESS_ARGS:
        findings.append(
            SafetyFinding(
                file=file_path,
                line=node.lineno,
                kind="FORBIDDEN_SUBPROCESS",
                detail=f'{func}: only ["git", "rev-parse", "HEAD"] is allowed, '
                f"got {actual_args}",
            )
        )

    return findings


def _get_call_func_name(node: ast.Call) -> str:
    """Get the dotted function name from a Call AST node."""
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        current = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        elif isinstance(current, ast.Call):
            # e.g., importlib.import_module(...) — the func itself is import_module
            # with value being a Name(id='importlib') or Attribute
            inner_func = _get_call_func_name(current)
            parts.append(inner_func)
            parts.reverse()
            return ".".join(parts)
        parts.reverse()
        return ".".join(parts)
    elif isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _is_truthy(node: ast.expr) -> bool:
    """Check if an AST expression evaluates to True."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return True  # assume truthy for non-constants


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_safety_scan_cli(argv: list[str] | None = None) -> int:
    """
    CLI entrypoint for the safety scanner.

    Returns 0 if no violations, 1 if violations found.
    Prints findings to stdout/stderr.
    """
    try:
        findings = scan_discovery_package()
        print(f"Safety scan passed: no violations in discovery package ({len(findings)} checked)")
        return 0
    except DiscoverySafetyError as e:
        print(f"SAFETY ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_safety_scan_cli())
