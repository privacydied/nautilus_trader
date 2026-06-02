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
PACKAGE_INIT_PATH = PACKAGE_DIR / "__init__.py"
PACKAGE_COMPAT_PATH = PACKAGE_DIR / "compat.py"
PACKAGE_RUNNER_PATH = PACKAGE_DIR / "runner.py"
LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
)
PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.hypotheses."
    "hyperliquid.node_fills_liq_reconstruction"
)
RUNNER_MODULE = f"{PACKAGE_MODULE}.runner"
STATUSES_MODULE = f"{PACKAGE_MODULE}.statuses"
CONFIG_MODULE = f"{PACKAGE_MODULE}.config"
MODELS_MODULE = f"{PACKAGE_MODULE}.models"
MOVED_STATUS_CONSTANTS = (
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_REQUESTER_PAYS_AUTH_EXPIRED',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_REPLICA_CMDS_NAMESPACE_NOT_FOUND',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_DECODER_UNVERIFIED',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_NOT_OBSERVED_IN_REPLICA_CMDS_SAMPLE',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_SOURCE_TOO_SPARSE_SAMPLE',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_REPLICA_CMDS_OBJECT_DISCOVERY_EMPTY',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_BACKSCAN_RUNTIME_INTERRUPTED',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_BACKSCAN_OOM_OR_PROCESS_KILLED',
    'NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_BACKSCAN_CHECKPOINT_MISMATCH',
    'NODE_FILLS_LIQ_PHASE_MINUS1_ERROR_INVALID_OUTPUT',
)
MOVED_MODEL_NAMES = (
    'StudyConfig',
    'TargetedBackwardLookupCheckpoint',
    'TargetedBackwardLookupSummary',
)
MODEL_FIELD_EXPECTATIONS = {
    'StudyConfig': {
        'study_id': ('hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0', False),
        'target_top_n': (30, False),
        'progress_every_objects': (1, False),
    },
    'TargetedBackwardLookupCheckpoint': {
        'objects_processed_keys': (None, True),
        'target_notional_resolved': (None, True),
        'safe_to_resume': (True, False),
    },
    'TargetedBackwardLookupSummary': {
        'selected_target_notional': (None, True),
        'identity_join_verdict': ('UNVERIFIED', False),
        'safe_to_resume': (False, False),
    },
}

MOVED_CONFIG_CONSTANTS = (
    'FORBIDDEN_STATUSES',
    'STUDY_SALT',
    'CANDIDATE_NAMESPACES',
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


def _required_symbols() -> tuple[str, ...]:
    required_symbols = _fixture()['required_symbols']
    assert isinstance(required_symbols, list)
    return tuple(str(name) for name in required_symbols)


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


def test_package_initializer_is_small_and_compat_only():
    text = PACKAGE_INIT_PATH.read_text()
    tree = ast.parse(text)
    function_defs = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    class_defs = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert not function_defs, function_defs
    assert not class_defs, class_defs
    assert len(text.splitlines()) <= 20


def test_wrapper_reexports_all_required_symbols_via_package_and_runner():
    legacy = importlib.import_module(LEGACY_MODULE)
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    for name in _required_symbols():
        assert hasattr(legacy, name), f'legacy missing {name}'
        assert hasattr(pkg, name), f'package missing {name}'
        assert hasattr(runner, name), f'runner missing {name}'


def test_package_all_matches_runner_exports_for_all_names():
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    exported = tuple(getattr(pkg, '__all__', ()))
    assert exported
    for name in exported:
        assert hasattr(pkg, name), f'package missing exported symbol {name}'
        assert hasattr(runner, name), f'runner missing exported symbol {name}'
        assert getattr(pkg, name) is getattr(runner, name), name


def test_forbidden_import_scan_passes_for_wrapper_and_package():
    paths = [LEGACY_PATH, *sorted(PACKAGE_DIR.glob('*.py'))]
    hit_map = {str(path): _forbidden_hits(path) for path in paths}
    offenders = {path: hits for path, hits in hit_map.items() if hits}
    assert not offenders, offenders


def test_package_files_exist():
    expected = {
        '__init__.py',
        'compat.py',
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


def test_compat_module_exports_required_fixture_symbols():
    compat = importlib.import_module(f'{PACKAGE_MODULE}.compat')
    required = _required_symbols()
    assert compat.REQUIRED_LEGACY_SYMBOLS == required
    exported = set(compat.__all__)
    missing = sorted(set(required) - exported)
    assert not missing, missing


def test_compat_export_namespace_resolves_runner_objects():
    compat = importlib.import_module(f'{PACKAGE_MODULE}.compat')
    runner = importlib.import_module(RUNNER_MODULE)
    namespace = compat.export_namespace()
    assert set(namespace) == set(compat.__all__)
    for name, value in namespace.items():
        assert value is getattr(runner, name), name


def test_wall2_source_probe_method_resolves_runner_globals() -> None:
    runner = importlib.import_module(RUNNER_MODULE)
    method = runner.NodeFillsLiqReconstructionProbe.run_wall2_update_leverage_source_probe
    assert method.__globals__['__name__'] == runner.__name__


def test_statuses_module_exports_moved_status_constants():
    statuses = importlib.import_module(STATUSES_MODULE)
    assert tuple(getattr(statuses, '__all__', ())) == MOVED_STATUS_CONSTANTS
    for name in MOVED_STATUS_CONSTANTS:
        assert hasattr(statuses, name), name


def test_config_module_exports_moved_config_constants():
    config = importlib.import_module(CONFIG_MODULE)
    assert tuple(getattr(config, '__all__', ())) == MOVED_CONFIG_CONSTANTS
    for name in MOVED_CONFIG_CONSTANTS:
        assert hasattr(config, name), name


def test_moved_constants_remain_exposed_via_runner_package_and_legacy_wrapper():
    legacy = importlib.import_module(LEGACY_MODULE)
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    for name in (*MOVED_STATUS_CONSTANTS, *MOVED_CONFIG_CONSTANTS):
        assert hasattr(runner, name), f'runner missing {name}'
        assert hasattr(pkg, name), f'package missing {name}'
        assert hasattr(legacy, name), f'legacy missing {name}'


def test_moved_constants_resolve_to_same_objects_from_source_modules():
    legacy = importlib.import_module(LEGACY_MODULE)
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    statuses = importlib.import_module(STATUSES_MODULE)
    config = importlib.import_module(CONFIG_MODULE)

    for name in MOVED_STATUS_CONSTANTS:
        assert getattr(runner, name) is getattr(statuses, name), name
        assert getattr(pkg, name) is getattr(runner, name), name
        assert getattr(legacy, name) is getattr(runner, name), name

    for name in MOVED_CONFIG_CONSTANTS:
        assert getattr(runner, name) is getattr(config, name), name
        assert getattr(pkg, name) is getattr(runner, name), name
        assert getattr(legacy, name) is getattr(runner, name), name


def test_models_module_exports_moved_models():
    models = importlib.import_module(MODELS_MODULE)
    assert tuple(getattr(models, '__all__', ())) == MOVED_MODEL_NAMES
    for name in MOVED_MODEL_NAMES:
        assert hasattr(models, name), name


def test_moved_models_remain_exposed_via_runner_package_and_legacy_wrapper():
    legacy = importlib.import_module(LEGACY_MODULE)
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    for name in MOVED_MODEL_NAMES:
        assert hasattr(runner, name), f'runner missing {name}'
        assert hasattr(pkg, name), f'package missing {name}'
        assert hasattr(legacy, name), f'legacy missing {name}'


def test_moved_models_resolve_to_same_objects_from_models_module():
    legacy = importlib.import_module(LEGACY_MODULE)
    pkg = importlib.import_module(PACKAGE_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    models = importlib.import_module(MODELS_MODULE)
    for name in MOVED_MODEL_NAMES:
        assert getattr(runner, name) is getattr(models, name), name
        assert getattr(pkg, name) is getattr(runner, name), name
        assert getattr(legacy, name) is getattr(runner, name), name


def test_moved_model_dataclass_fields_match_expected_defaults_and_factories():
    import dataclasses

    models = importlib.import_module(MODELS_MODULE)
    for name, expectations in MODEL_FIELD_EXPECTATIONS.items():
        cls = getattr(models, name)
        fields = {field.name: field for field in dataclasses.fields(cls)}
        for field_name, (expected_default, expect_factory) in expectations.items():
            field = fields[field_name]
            if expect_factory:
                assert field.default is dataclasses.MISSING
                assert field.default_factory is not dataclasses.MISSING
            else:
                assert field.default == expected_default
                assert field.default_factory is dataclasses.MISSING
