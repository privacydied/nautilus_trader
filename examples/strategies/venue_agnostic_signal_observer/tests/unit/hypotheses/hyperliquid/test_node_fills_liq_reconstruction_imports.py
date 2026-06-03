from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "node_fills_liq_reconstruction_legacy_surface.json"
)
LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
)
PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.hypotheses."
    "hyperliquid.node_fills_liq_reconstruction"
)
CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text())


def test_new_package_imports_cleanly():
    pkg = importlib.import_module(PACKAGE_MODULE)
    assert pkg.__name__ == PACKAGE_MODULE


def test_legacy_module_imports_cleanly():
    legacy = importlib.import_module(LEGACY_MODULE)
    assert legacy.__name__ == LEGACY_MODULE


def test_cli_module_imports_cleanly():
    cli = importlib.import_module(CLI_MODULE)
    assert cli.__name__ == CLI_MODULE


def test_legacy_module_exposes_required_symbols():
    legacy = importlib.import_module(LEGACY_MODULE)
    for name in _fixture()['required_symbols']:
        assert hasattr(legacy, name), f'legacy missing {name}'


def test_new_package_exposes_required_symbols():
    pkg = importlib.import_module(PACKAGE_MODULE)
    for name in _fixture()['required_symbols']:
        assert hasattr(pkg, name), f'package missing {name}'


def test_package_all_includes_required_symbols():
    pkg = importlib.import_module(PACKAGE_MODULE)
    exported = set(getattr(pkg, '__all__', ()))
    missing = sorted(set(_fixture()['required_symbols']) - exported)
    assert not missing, f'package __all__ missing {missing}'


def test_legacy_module_keeps_known_private_helpers():
    legacy = importlib.import_module(LEGACY_MODULE)
    assert hasattr(legacy, '_try_parse_start_position')
    assert hasattr(legacy, '_is_cold_start')


def test_cli_help_works_without_aws_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv('AWS_ACCESS_KEY_ID', raising=False)
    monkeypatch.delenv('AWS_SECRET_ACCESS_KEY', raising=False)
    monkeypatch.delenv('AWS_SESSION_TOKEN', raising=False)
    env = os.environ.copy()
    env.pop('AWS_ACCESS_KEY_ID', None)
    env.pop('AWS_SECRET_ACCESS_KEY', None)
    env.pop('AWS_SESSION_TOKEN', None)
    result = subprocess.run(
        [sys.executable, '-m', CLI_MODULE, '--help'],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[7],
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'Hyperliquid node fills liquidation reconstruction Phase -1 v0 probe' in result.stdout
