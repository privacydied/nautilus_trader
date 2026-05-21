"""
Tests for the AST-based safety scanner.

Covers tests 103-114 from the discovery freeze specification.
"""

import ast
import textwrap
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan import (
    FORBIDDEN_IMPORTS,
)
from examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan import SafetyFinding
from examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan import (
    scan_discovery_package,
)


# ===================================================================
# SAFETY TESTS (103-114)
# ===================================================================

class TestSafetyScan:
    """Tests 103-114: AST safety scanner."""

    def test_103_scanner_passes_on_clean_package(self):
        """AST safety scanner passes on the discovery package as delivered."""
        # The discovery package should be clean by construction
        # scan_discovery_package should not raise
        try:
            findings = scan_discovery_package()
            assert len(findings) == 0
        except Exception as e:
            # If it raised DiscoverySafetyError, that means violations were found
            # This could happen if the implementation itself has issues
            # For now, just check it doesn't crash with unexpected errors
            from examples.strategies.venue_agnostic_signal_observer.discovery.exceptions import (
                DiscoverySafetyError,
            )
            # If there ARE violations in the discovery package, that's a code problem
            # Let's surface them for debugging
            if not isinstance(e, DiscoverySafetyError):
                raise
            # DiscoverySafetyError means violations found - list them
            # This test should pass if the package is clean
            pytest.fail(f"Discovery package has safety violations: {e}")

    def _create_fixture_file(self, tmp_path, name, content):
        """Create a temporary Python file for scanner testing."""
        path = tmp_path / name
        path.write_text(textwrap.dedent(content))
        return path

    def _run_scanner_on_file(self, tmp_path, name, content):
        """Create a temp file and run the scanner's internal scan on it."""
        file_path = self._create_fixture_file(tmp_path, name, content)
        # We'll manually invoke the AST scanner logic
        return self._scan_ast(content, str(file_path))

    def _scan_ast(self, source, filepath="<test>"):
        """Parse source and find violations using the same logic as safety_scan."""
        from examples.strategies.venue_agnostic_signal_observer.discovery.safety_scan import (
            _scan_file,
        )
        temp_path = Path(filepath)
        temp_path.write_text(source)
        return _scan_file(temp_path)

    def test_104_detects_forbidden_static_imports(self, tmp_path):
        """AST safety scanner detects forbidden static imports."""
        content = """
import nautilus_trader.live
import ccxt
"""
        violations = self._scan_ast(content, str(tmp_path / "bad_imports.py"))
        violation_kinds = [v.kind for v in violations]
        assert "FORBIDDEN_IMPORT" in violation_kinds
        assert len([v for v in violations if v.kind == "FORBIDDEN_IMPORT"]) >= 2

    def test_104b_detects_forbidden_from_imports(self, tmp_path):
        """Detects 'from nautilus_trader.live import X' style imports."""
        content = "from nautilus_trader.live import LiveNode\n"
        violations = self._scan_ast(content, str(tmp_path / "bad_from_import.py"))
        assert any(v.kind == "FORBIDDEN_IMPORT" for v in violations)

    def test_105_detects_forbidden_dynamic_imports(self, tmp_path):
        """Detects forbidden importlib.import_module calls."""
        content = 'import importlib\nimportlib.import_module("nautilus_trader.live")\n'
        violations = self._scan_ast(content, str(tmp_path / "bad_dynamic.py"))
        assert any(v.kind == "FORBIDDEN_DYNAMIC_IMPORT" for v in violations)

    def test_106_detects_forbidden___import__(self, tmp_path):
        """Detects forbidden __import__ usage."""
        content = '__import__("nautilus_trader.live")\n'
        violations = self._scan_ast(content, str(tmp_path / "bad_dunder.py"))
        assert any(v.kind == "FORBIDDEN_DYNAMIC_IMPORT" for v in violations)

    def test_107_detects_env_access(self, tmp_path):
        """Detects private-key/API-key env access."""
        content = textwrap.dedent("""
        import os
        api_key = os.environ["API_KEY"]
        secret = os.getenv("PRIVATE_KEY")
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_env.py"))
        env_violations = [v for v in violations if v.kind == "FORBIDDEN_ENV_ACCESS"]
        assert len(env_violations) >= 2

    def test_108_detects_order_terms(self, tmp_path):
        """Detects order/execution terms in import attribute chains."""
        content = textwrap.dedent("""
        from some_module import submit_order
        from trading import TradingNode
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_terms.py"))
        order_violations = [v for v in violations if v.kind == "FORBIDDEN_ORDER_TERM"]
        assert len(order_violations) >= 2

    def test_109_rejects_first_party_imports_outside_discovery(self, tmp_path):
        """Rejects imports from outside the discovery package."""
        content = textwrap.dedent("""
        from examples.strategies.venue_agnostic_signal_observer import signals
        import examples.strategies.venue_agnostic_signal_observer.observer
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_first_party.py"))
        first_party = [v for v in violations if v.kind == "FORBIDDEN_FIRST_PARTY_IMPORT"]
        assert len(first_party) >= 2

    def test_110_rejects_non_allowlisted_subprocess(self, tmp_path):
        """Rejects subprocess calls other than the allowlisted git rev-parse HEAD."""
        content = textwrap.dedent("""
        import subprocess
        subprocess.run(["git", "status"])
        subprocess.check_output(["ls", "-la"])
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_subprocess.py"))
        subprocess_violations = [v for v in violations if v.kind == "FORBIDDEN_SUBPROCESS"]
        assert len(subprocess_violations) >= 2

    def test_111_rejects_popen_call_os_system(self, tmp_path):
        """Rejects subprocess.Popen / subprocess.call / os.system / os.popen / shell=True."""
        content = textwrap.dedent("""
        import subprocess
        import os
        subprocess.Popen(["git", "rev-parse", "HEAD"])
        subprocess.call(["git", "rev-parse", "HEAD"])
        os.system("git rev-parse HEAD")
        os.popen("git rev-parse HEAD")
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_popen.py"))
        subprocess_violations = [v for v in violations if v.kind == "FORBIDDEN_SUBPROCESS"]
        assert len(subprocess_violations) >= 4

    def test_111b_rejects_shell_true(self, tmp_path):
        """Rejects subprocess.run with shell=True."""
        content = textwrap.dedent("""
        import subprocess
        subprocess.run(["git", "rev-parse", "HEAD"], shell=True)
        """)
        violations = self._scan_ast(content, str(tmp_path / "bad_shell.py"))
        shell_violations = [v for v in violations if v.kind == "FORBIDDEN_SUBPROCESS"]
        assert len(shell_violations) >= 1

    def test_112_accepts_allowlisted_git_rev_parse(self, tmp_path):
        """Accepts the allowlisted git rev-parse HEAD call shape."""
        content = textwrap.dedent("""
        import subprocess
        subprocess.run(["git", "rev-parse", "HEAD"])
        subprocess.check_output(["git", "rev-parse", "HEAD"])
        """)
        violations = self._scan_ast(content, str(tmp_path / "good_git.py"))
        subprocess_violations = [v for v in violations if v.kind == "FORBIDDEN_SUBPROCESS"]
        assert len(subprocess_violations) == 0

    def test_113_clis_dont_import_network_libraries(self):
        """Lock/validation CLIs do not import network libraries per AST scan."""
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent
        cli_files = [
            base / "run_lock_discovery_grid.py",
            base / "run_validate_discovery_grid_lock.py",
            base / "run_lock_discovery_candidate.py",
        ]

        for cli_file in cli_files:
            if not cli_file.exists():
                continue
            tree = ast.parse(cli_file.read_bytes(), filename=str(cli_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in FORBIDDEN_IMPORTS:
                            assert forbidden not in alias.name, (
                                f"{cli_file.name}: forbidden import {forbidden}"
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in FORBIDDEN_IMPORTS:
                        assert forbidden not in node.module, (
                            f"{cli_file.name}: forbidden import {forbidden} from {node.module}"
                        )

    def test_114_fixture_based_assertions(self, tmp_path):
        """Scanner tests use fixture files with known violations and assert specific findings."""
        # Create a fixture with a specific known violation at a specific line
        content_lines = [
            "import os",
            "import subprocess",
            "",
            'api_key = os.environ["API_KEY"]',  # line 4 - FORBIDDEN_ENV_ACCESS
            "",
            'subprocess.run(["git", "status"])',  # line 6 - FORBIDDEN_SUBPROCESS
        ]
        content = "\n".join(content_lines)
        violations = self._scan_ast(content, str(tmp_path / "fixture_test.py"))

        # Check env access at line 4
        env_violations = [v for v in violations if v.kind == "FORBIDDEN_ENV_ACCESS"
                          and v.line == 4]
        assert len(env_violations) >= 1, "Expected env access violation at line 4"

        # Check subprocess at line 6
        sp_violations = [v for v in violations if v.kind == "FORBIDDEN_SUBPROCESS"
                         and v.line == 6]
        assert len(sp_violations) >= 1, "Expected subprocess violation at line 6"

    def test_safety_finding_equality(self):
        """SafetyFinding supports equality comparison."""
        a = SafetyFinding("file.py", 10, "FORBIDDEN_IMPORT", "test")
        b = SafetyFinding("file.py", 10, "FORBIDDEN_IMPORT", "test")
        c = SafetyFinding("file.py", 20, "FORBIDDEN_IMPORT", "test")
        assert a == b
        assert a != c

    def test_safety_finding_hashable(self):
        """SafetyFinding is hashable."""
        a = SafetyFinding("file.py", 10, "FORBIDDEN_IMPORT", "test")
        s = {a}
        assert len(s) == 1
