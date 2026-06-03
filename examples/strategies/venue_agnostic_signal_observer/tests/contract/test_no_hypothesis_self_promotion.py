from __future__ import annotations

import ast
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.core.safety import (
    FORBIDDEN_HYPOTHESIS_IMPORT_TERMS,
)

SCAFFOLD_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = (
    SCAFFOLD_ROOT / "hypotheses",
    SCAFFOLD_ROOT / "venues",
    SCAFFOLD_ROOT / "runners",
    SCAFFOLD_ROOT / "core",
)


def _iter_import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
    return targets


def test_scaffold_modules_do_not_import_forbidden_terms() -> None:
    scanned: list[Path] = []
    violations: list[tuple[Path, str]] = []
    for root in SCAN_DIRS:
        for path in sorted(root.rglob("*.py")):
            scanned.append(path)
            for target in _iter_import_targets(path):
                lowered = target.lower()
                if any(term in lowered for term in FORBIDDEN_HYPOTHESIS_IMPORT_TERMS):
                    violations.append((path, target))
    assert scanned
    assert not violations


def test_runner_registry_entries_do_not_imply_direct_paper_promotion() -> None:
    registry_path = SCAFFOLD_ROOT / "runners" / "registry.py"
    content = registry_path.read_text(encoding="utf-8")
    assert "allows_paper_promotion = True" not in content
