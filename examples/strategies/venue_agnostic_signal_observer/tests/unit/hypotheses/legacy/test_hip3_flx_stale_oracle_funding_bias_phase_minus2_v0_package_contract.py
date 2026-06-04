from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


PACKAGE = "examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
LEGACY = "examples.strategies.venue_agnostic_signal_observer.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
CLI = "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"

EXPECTED_HELP_LINES = 66
HELP_KEY_SUBSTRINGS = (
    "--bootstrap-iterations",
    "--extend-backward-days",
    "--funding-interval-seconds",
    "--frequency-scan-only",
    "--dry-run",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[7]


def _root_wrapper_path() -> Path:
    return _repo_root() / "examples/strategies/venue_agnostic_signal_observer/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0.py"


def _package_dir() -> Path:
    return _repo_root() / "examples/strategies/venue_agnostic_signal_observer/hypotheses/legacy/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"


def test_metadata_imports_without_importing_runner() -> None:
    code = f'''
import sys
before = set(sys.modules)
from {PACKAGE} import metadata
after = set(sys.modules)
new = sorted(after - before)
assert metadata.METADATA.key == "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
hits = [n for n in new if n.endswith(".hip3_flx_stale_oracle_funding_bias_phase_minus2_v0.runner")]
print("\\n".join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_package_root_imports_without_importing_runner() -> None:
    code = f'''
import sys
before = set(sys.modules)
import {PACKAGE} as pkg
after = set(sys.modules)
new = sorted(after - before)
assert pkg.METADATA.key == "hip3_flx_stale_oracle_funding_bias_phase_minus2_v0"
hits = [n for n in new if n.endswith(".hip3_flx_stale_oracle_funding_bias_phase_minus2_v0.runner")]
print("\\n".join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_legacy_root_wrapper_imports_and_exposes_required_symbols() -> None:
    import importlib

    legacy = importlib.import_module(LEGACY)
    compat = importlib.import_module(f"{PACKAGE}.compat")

    assert compat.REQUIRED_LEGACY_SYMBOLS
    assert legacy.__all__ == compat.__all__
    for name in compat.REQUIRED_LEGACY_SYMBOLS:
        assert hasattr(legacy, name), name


def test_compat_exports_resolve_to_runner_objects() -> None:
    import importlib

    compat = importlib.import_module(f"{PACKAGE}.compat")
    runner = importlib.import_module(f"{PACKAGE}.runner")
    for name in compat.__all__:
        assert getattr(compat, name) is getattr(runner, name), name


def test_runner_imports_directly() -> None:
    import importlib

    runner = importlib.import_module(f"{PACKAGE}.runner")
    assert callable(runner.compute_bias_decision)


def test_old_cli_import_works() -> None:
    import importlib

    cli = importlib.import_module(CLI)
    assert callable(cli.main)


def test_old_cli_help_exits_zero_and_matches_baseline() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", CLI, "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == EXPECTED_HELP_LINES, len(lines)
    for substring in HELP_KEY_SUBSTRINGS:
        assert substring in proc.stdout, substring


def test_old_cli_help_creates_no_new_artifacts() -> None:
    repo_root = _repo_root()
    roots = [
        repo_root / "examples/strategies/venue_agnostic_signal_observer/reports",
        repo_root / "examples/strategies/venue_agnostic_signal_observer/data",
        repo_root / "reports",
        repo_root / "data",
        repo_root / ".local_data",
    ]

    def snapshot() -> set[str]:
        out: set[str] = set()
        for root in roots:
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
                        out.add(str(path))
        return out

    before = snapshot()
    proc = subprocess.run(
        [sys.executable, "-m", CLI, "--help"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = snapshot()
    assert before == after, sorted(after - before)


def test_root_wrapper_is_small_and_has_no_algorithmic_defs() -> None:
    source = _root_wrapper_path().read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 60
    tree = ast.parse(source)
    assert not [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def test_metadata_and_package_root_do_not_import_runner_directly() -> None:
    metadata_source = (_package_dir() / "metadata.py").read_text(encoding="utf-8")
    package_source = (_package_dir() / "__init__.py").read_text(encoding="utf-8")
    for source in (metadata_source, package_source):
        assert "import runner" not in source
        assert "from .runner" not in source
