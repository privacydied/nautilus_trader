from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "node_fills_liq_reconstruction_legacy_surface.json"
)
LEGACY_PATH = (
    Path(__file__).resolve().parents[4]
    / "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0.py"
)
PACKAGE_DIR = (
    Path(__file__).resolve().parents[4]
    / "hypotheses"
    / "hyperliquid"
    / "node_fills_liq_reconstruction"
)
PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.hypotheses."
    "hyperliquid.node_fills_liq_reconstruction"
)
FORBIDDEN_TERMS = {
    'paper',
    'paper_dashboard',
    'governance',
    'bot',
    'broker',
    'orders',
    'order',
    'execution',
    'private_key',
    'signing',
    'wallet',
    'auth',
    'live',
}
ALLOWED_FORBIDDEN_IMPORT_SUBSTRINGS = (
    'venue_agnostic_signal_observer.adapters.',
    'collections',
    'datetime',
    'decimal',
    'dataclasses',
    'enum',
    'hashlib',
    'io',
    'json',
    'lz4',
    'orjson',
    'os',
    'pathlib',
    're',
    'resource',
    'subprocess',
    'sys',
    'time',
    'typing',
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text())


def _iter_import_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            names.append(module)
    return names


def _forbidden_hits(path: Path) -> list[str]:
    hits: list[str] = []
    for name in _iter_import_names(path):
        lowered = name.lower()
        if any(term in lowered for term in FORBIDDEN_TERMS):
            if lowered.startswith(ALLOWED_FORBIDDEN_IMPORT_SUBSTRINGS):
                continue
            hits.append(name)
    return sorted(set(hits))


def test_fixture_captures_full_top_level_surface():
    fixture = _fixture()
    assert fixture['legacy_module'].endswith('hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0')
    assert fixture['required_symbols']
    assert fixture['top_level_symbols_before_split']


def test_wrapper_has_no_business_logic_defs_and_is_small():
    text = LEGACY_PATH.read_text()
    tree = ast.parse(text)
    function_defs = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    class_defs = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert len(function_defs) <= 1, function_defs
    assert len(class_defs) == 0, class_defs
    assert len(text.splitlines()) <= 80


def test_wrapper_reexports_all_required_symbols_via_package():
    legacy = importlib.import_module(
        'examples.strategies.venue_agnostic_signal_observer.hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0'
    )
    pkg = importlib.import_module(PACKAGE_MODULE)
    for name in _fixture()['required_symbols']:
        assert hasattr(legacy, name), f'legacy missing {name}'
        assert hasattr(pkg, name), f'package missing {name}'


def test_forbidden_import_scan_passes_for_wrapper_and_package():
    paths = [LEGACY_PATH, *sorted(PACKAGE_DIR.glob('*.py'))]
    hit_map = {str(path): _forbidden_hits(path) for path in paths}
    offenders = {path: hits for path, hits in hit_map.items() if hits}
    assert not offenders, offenders


def test_package_files_exist():
    expected = {
        '__init__.py',
        'runner.py',
        'statuses.py',
        'config.py',
        'models.py',
        's3_probe.py',
        'parsing.py',
        'bars.py',
        'overlap.py',
        'artifacts.py',
    }
    assert expected.issubset({path.name for path in PACKAGE_DIR.glob('*.py')})
