from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[7]


def _root_wrapper_path() -> Path:
    return _repo_root() / "examples/strategies/venue_agnostic_signal_observer/hyperliquid_funding_divergence_phase0.py"


def test_metadata_imports_without_importing_runner() -> None:
    code = r'''
import sys
before = set(sys.modules)
from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0 import metadata
after = set(sys.modules)
new = sorted(after - before)
assert metadata.METADATA.key == 'hyperliquid_funding_divergence_phase0'
hits = [name for name in new if name.endswith('.funding_divergence_phase0.runner')]
print('\n'.join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_package_root_imports_without_importing_runner() -> None:
    code = r'''
import sys
before = set(sys.modules)
import examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0 as pkg
after = set(sys.modules)
new = sorted(after - before)
assert pkg.METADATA.key == 'hyperliquid_funding_divergence_phase0'
hits = [name for name in new if name.endswith('.funding_divergence_phase0.runner')]
print('\n'.join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_legacy_root_wrapper_exports_runner_symbols() -> None:
    import examples.strategies.venue_agnostic_signal_observer.hyperliquid_funding_divergence_phase0 as legacy
    from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0 import compat
    from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0 import runner

    assert legacy.normalize_funding_row is runner.normalize_funding_row
    assert legacy.write_csv is runner.write_csv
    assert compat.normalize_funding_row is runner.normalize_funding_row
    assert compat.write_csv is runner.write_csv
    assert compat.__all__


def test_old_cli_help_matches_baseline_and_import_still_works() -> None:
    import examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_divergence_phase0 as cli

    assert callable(cli.main)
    baseline = Path("/tmp/hyperliquid_funding_divergence_phase0_help_before_unit12.txt").read_text(encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_divergence_phase0",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == baseline


def test_runner_imports_directly() -> None:
    from examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.funding_divergence_phase0 import runner

    assert callable(runner.pre_data_kill_criteria)


def test_root_wrapper_is_small_and_has_no_algorithmic_defs() -> None:
    source = _root_wrapper_path().read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert len(source.splitlines()) <= 60
    assert not [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def test_metadata_and_package_root_do_not_import_runner_directly() -> None:
    metadata_source = (_repo_root() / "examples/strategies/venue_agnostic_signal_observer/hypotheses/hyperliquid/funding_divergence_phase0/metadata.py").read_text(encoding="utf-8")
    package_source = (_repo_root() / "examples/strategies/venue_agnostic_signal_observer/hypotheses/hyperliquid/funding_divergence_phase0/__init__.py").read_text(encoding="utf-8")
    assert "import runner" not in metadata_source
    assert "from .runner" not in metadata_source
    assert "import runner" not in package_source
    assert "from .runner" not in package_source


def test_no_fake_init_py_files_exist() -> None:
    proc = subprocess.run(
        "git ls-files examples/strategies/venue_agnostic_signal_observer | grep -E '(^|/)init\\.py$' || true",
        shell=True,
        capture_output=True,
        text=True,
        cwd=_repo_root(),
        check=True,
    )
    assert proc.stdout.strip() == ""
