from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import orjson
import pytest

from examples.strategies.venue_agnostic_signal_observer.paper.dry_run_report import (
    STATUS_BLOCKED,
    STATUS_READY,
    DryRunReport,
    DryRunReportInput,
    build_dry_run_report,
    report_as_dict,
    write_dry_run_report,
)

_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "paper"
    / "dry_run_report.py"
)


def _ready_input() -> DryRunReportInput:
    return DryRunReportInput(
        strategy_id="synthetic_strategy_v0",
        registry_key="synthetic_registry_key",
        cli_spec_key="synthetic_cli_spec",
        gates_passed=True,
        refalsification_passed=True,
        policy_allowed=True,
        reasons=("all_synthetic_checks_passed",),
    )


def test_ready_input_produces_dry_run_ready_only() -> None:
    report = build_dry_run_report(_ready_input())
    assert report.status == STATUS_READY
    assert report.ready_for_paper_simulation is True
    # Never enabled/live/ordering/trading.
    for forbidden in ("ENABLED", "LIVE", "ORDERING", "TRADING"):
        assert forbidden not in report.status


def test_blocked_when_any_gate_fails() -> None:
    for field in ("gates_passed", "refalsification_passed", "policy_allowed"):
        kwargs = {
            "strategy_id": "s",
            "registry_key": "r",
            "cli_spec_key": None,
            "gates_passed": True,
            "refalsification_passed": True,
            "policy_allowed": True,
            "reasons": (),
        }
        kwargs[field] = False
        report = build_dry_run_report(DryRunReportInput(**kwargs))
        assert report.status == STATUS_BLOCKED, field
        assert report.ready_for_paper_simulation is False, field


def test_report_contains_registry_cli_and_gate_fields() -> None:
    report = build_dry_run_report(_ready_input())
    payload = report_as_dict(report)
    assert payload["registry_key"] == "synthetic_registry_key"
    assert payload["cli_spec_key"] == "synthetic_cli_spec"
    assert payload["strategy_id"] == "synthetic_strategy_v0"
    assert payload["status"] == STATUS_READY
    assert payload["ready_for_paper_simulation"] is True


def test_reasons_preserved_and_derived_appended() -> None:
    report = build_dry_run_report(
        DryRunReportInput(
            strategy_id="s",
            registry_key="r",
            cli_spec_key=None,
            gates_passed=False,
            refalsification_passed=True,
            policy_allowed=False,
            reasons=("custom_reason",),
        )
    )
    assert report.reasons[0] == "custom_reason"
    assert "gates_not_passed" in report.reasons
    assert "policy_not_allowed" in report.reasons
    assert "refalsification_not_passed" not in report.reasons


def test_json_writer_writes_only_to_tmp_path(tmp_path: Path) -> None:
    report = build_dry_run_report(_ready_input())
    out = tmp_path / "synthetic_dry_run.json"
    written = write_dry_run_report(report, out)
    assert written == out
    assert out.is_file()
    loaded = orjson.loads(out.read_bytes())
    assert loaded["status"] == STATUS_READY
    assert loaded["registry_key"] == "synthetic_registry_key"
    # Only the caller-provided file exists in tmp_path.
    assert [p.name for p in tmp_path.iterdir()] == ["synthetic_dry_run.json"]


def test_writer_fails_closed_when_parent_missing(tmp_path: Path) -> None:
    report = build_dry_run_report(_ready_input())
    missing = tmp_path / "does_not_exist" / "report.json"
    with pytest.raises(FileNotFoundError):
        write_dry_run_report(report, missing)
    assert not missing.exists()


def test_no_real_reports_data_or_local_data_paths_written(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[7]
    roots = [
        repo_root / "reports",
        repo_root / "data",
        repo_root / ".local_data",
        repo_root / "examples/strategies/venue_agnostic_signal_observer/reports",
        repo_root / "examples/strategies/venue_agnostic_signal_observer/data",
    ]

    def snapshot() -> set[str]:
        out: set[str] = set()
        for root in roots:
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file() and "__pycache__" not in path.parts:
                        out.add(str(path))
        return out

    before = snapshot()
    report = build_dry_run_report(_ready_input())
    write_dry_run_report(report, tmp_path / "r.json")
    after = snapshot()
    assert before == after


def test_importing_module_pulls_in_no_live_broker_execution_modules() -> None:
    # The pre-existing paper package __init__ eagerly imports paper-layer
    # siblings; that is out of scope here. What matters is that nothing live /
    # broker / execution / order is dragged in transitively.
    code = """
import sys
before = set(sys.modules)
import examples.strategies.venue_agnostic_signal_observer.paper.dry_run_report  # noqa: F401
after = set(sys.modules)
new = sorted(after - before)
forbidden = (
    "nautilus_trader.live",
    "nautilus_trader.execution",
    "nautilus_trader.adapters",
    "TradingNode",
    "ExecutionClient",
    "broker",
    "submit_order",
    "place_order",
)
hits = [n for n in new if any(f in n for f in forbidden)]
for h in hits:
    print(h)
"""
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout


def test_module_own_imports_are_stdlib_plus_orjson_only() -> None:
    # The module itself must not import paper siblings, nautilus, or any
    # broker/live/order machinery — only stdlib + orjson.
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    allowed = {"__future__", "dataclasses", "pathlib", "orjson"}
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise AssertionError("module must not use relative sibling imports")
            if node.module:
                imported_roots.add(node.module.split(".")[0])
    assert imported_roots <= allowed, imported_roots - allowed


def test_module_has_no_auth_wallet_or_private_key_fields() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    field_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_names.append(node.target.id.lower())
    for forbidden in ("private_key", "wallet", "api_secret", "auth", "signing"):
        assert all(forbidden not in name for name in field_names), forbidden
    lowered = source.lower()
    for forbidden in ("submit_order", "place_order", "private_key", "api_secret"):
        assert forbidden not in lowered, forbidden
