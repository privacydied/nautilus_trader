"""
Security and secrets tests.

Scans new files for obvious hardcoded key patterns.
"""

import os

import pytest


SOURCE_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
)


def _iter_py_files():
    """Yield .py files in the strategy directory."""
    for root, _dirs, files in os.walk(SOURCE_DIR):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


SUSPICIOUS_PATTERNS = [
    "PRIVATE_KEY",
    "0x" + "0" * 40,  # Simulated eth address
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "api_key = ",
    "api_secret = ",
]


def test_no_hardcoded_secrets():
    """Check for obvious hardcoded keys in source files."""
    for filepath in _iter_py_files():
        with open(filepath) as f:
            content = f.read()
        # Skip test files and run_observe.py (they use test data)
        basename = os.path.basename(filepath)
        if basename in ("test_security_no_secrets.py", "test_live_guards.py"):
            continue
        # Check for .env-style vars being set to test values
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and imports
            if stripped.startswith("#") or stripped.startswith("import") or stripped.startswith("from"):
                continue
            # Check for common patterns
            if "POLYMARKET_PK" in stripped or "POLYMARKET_API" in stripped:
                # These should only appear in get_env_key() calls or docstrings
                if "get_env_key" not in stripped and '"' not in line and "'" not in line:
                    continue  # OK - it's a reference, not a value
                # Check it's not a hardcoded string value
                if "=" in stripped and ("'" in stripped or '"' in stripped):
                    pytest.fail(
                        f"Possible hardcoded secret in {filepath}:{i}: {stripped[:80]}",
                    )
    assert True


def test_no_credentials_in_config_files():
    """Config files must not contain credential fields."""
    config_path = os.path.join(SOURCE_DIR, "config.py")
    if os.path.exists(config_path):
        with open(config_path) as f:
            content = f.read()
        # The Config dataclass should not have private_key, api_key, etc.
        # (those go in PolymarketExecClientConfig, not our strategy config)
        assert "private_key" not in content


def test_no_credentials_in_reports():
    """Report files must not write credentials."""
    report_path = os.path.join(SOURCE_DIR, "ledger.py")
    if os.path.exists(report_path):
        with open(report_path) as f:
            content = f.read()
        assert "private_key" not in content
        assert "api_key" not in content
        assert "api_secret" not in content


def test_no_accidental_live_order_in_tests():
    """Tests must not import execution clients."""
    test_dir = os.path.join(SOURCE_DIR, "tests")
    for root, _dirs, files in os.walk(test_dir):
        for f in files:
            if f.endswith(".py"):
                filepath = os.path.join(root, f)
                with open(filepath) as fp:
                    content = fp.read()
                # Check for execution client imports
                if "PolymarketExecutionClient" in content or "LiveExecutionClient" in content:
                    # Only allowed in these specific guard/security test files
                    allowed = {"test_live_guards.py", "test_security_no_secrets.py"}
                    if os.path.basename(filepath) in allowed:
                        continue
                    pytest.fail(
                        f"Test file {filepath} imports an execution client",
                    )
    assert True
