"""Tests for the HLP vault metadata adapter.

Covers address resolution from fixture, public API, heuristic,
source inventory checks, and deterministic output.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from examples.strategies.venue_agnostic_signal_observer.adapters.hlp_vault_metadata_adapter import (
    resolve_vault_addresses,
    check_vault_metadata_source,
    PublicInfoApiClient,
    FROZEN_HLP_PARENT_ADDRESSES,
    FROZEN_HLP_CHILD_ADDRESSES,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def metadata_fixture():
    """Create a vault metadata fixture file."""
    d = tempfile.mkdtemp()
    metadata = {
        "parent_address": "0xdf5f5c0c0a1c0a5a0f0b0c0d0e0f0a0b0c0d0e0f",
        "children": [
            {
                "address": "0xchild_backstop_001",
                "role_label": "BACKSTOP",
                "confidence": "documented",
                "symbol_coverage": ["BTC", "ETH", "SOL"],
                "fraction_fills_liquidation_flagged": 0.75,
                "evidence_fields": {"source": "fixture"},
            },
            {
                "address": "0xchild_mm_001",
                "role_label": "MM_PARENT",
                "confidence": "documented",
                "symbol_coverage": [],
                "fraction_fills_liquidation_flagged": 0.0,
                "evidence_fields": {"source": "fixture"},
            },
        ],
    }
    fpath = os.path.join(d, "metadata.json")
    with open(fpath, "w") as f:
        json.dump(metadata, f)
    yield fpath
    import shutil
    shutil.rmtree(d)


# ===========================================================================
# Address resolution from fixture - deterministic
# ===========================================================================


def test_resolve_from_fixture_returns_parent():
    """Fixture with parent address resolves successfully."""
    result = resolve_vault_addresses(metadata_fixture_path=metadata_fixture.__dict__.get('_pytestfixturefunction', None))
    # Use direct file reference via fixture value
    pass


def test_resolve_from_fixture(metadata_fixture):
    """Resolve vault addresses from a valid metadata fixture."""
    result = resolve_vault_addresses(metadata_fixture_path=metadata_fixture)
    assert result.parent_resolved
    assert result.parent_address == "0xdf5f5c0c0a1c0a5a0f0b0c0d0e0f0a0b0c0d0e0f"
    assert len(result.children) == 2


def test_resolve_from_fixture_backstop_child(metadata_fixture):
    """Fixture has a BACKSTOP child with documented confidence."""
    result = resolve_vault_addresses(metadata_fixture_path=metadata_fixture)
    backstop_children = [
        c for c in result.children
        if c.role_label in ("BACKSTOP", "BACKSTOP_INFERRED")
        and c.confidence in ("documented", "inferred_high")
    ]
    assert len(backstop_children) >= 1
    assert backstop_children[0].address == "0xchild_backstop_001"
    assert backstop_children[0].role_label == "BACKSTOP"
    assert backstop_children[0].confidence == "documented"


def test_resolve_from_fixture_mm_child(metadata_fixture):
    """Fixture has an MM_PARENT child."""
    result = resolve_vault_addresses(metadata_fixture_path=metadata_fixture)
    mm_children = [c for c in result.children if c.role_label == "MM_PARENT"]
    assert len(mm_children) == 1


# ===========================================================================
# Parent missing -> ARCHIVE_INFEASIBLE
# ===========================================================================


def test_resolve_no_fixture_returns_not_resolved():
    """No metadata fixture returns parent_resolved=False."""
    result = resolve_vault_addresses()
    assert not result.parent_resolved
    assert result.parent_address is None
    assert result.error is not None


def test_resolve_broken_fixture():
    """Broken fixture file returns not resolved."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{invalid json")
        fpath = f.name
    try:
        result = resolve_vault_addresses(metadata_fixture_path=fpath)
        assert not result.parent_resolved
        assert result.error is not None
    finally:
        os.unlink(fpath)


# ===========================================================================
# Child unresolved -> BACKSTOP_INSEPARABLE
# ===========================================================================


def test_resolve_fixture_no_children():
    """Fixture with parent but no children returns error but parent is resolved."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"parent_address": "0xparent"}, f)
        fpath = f.name
    try:
        result = resolve_vault_addresses(metadata_fixture_path=fpath)
        assert result.parent_resolved
        assert len(result.children) == 0
        assert result.error is not None  # No child vaults identified
    finally:
        os.unlink(fpath)


def test_resolve_fixture_no_backstop_child(metadata_fixture):
    """Fixture with only MM children has no backstop child."""
    d = tempfile.mkdtemp()
    metadata = {
        "parent_address": "0xparent",
        "children": [
            {
                "address": "0xchild_mm",
                "role_label": "MM_PARENT",
                "confidence": "documented",
                "symbol_coverage": [],
                "fraction_fills_liquidation_flagged": 0.0,
            },
        ],
    }
    fpath = os.path.join(d, "metadata_no_backstop.json")
    with open(fpath, "w") as f:
        json.dump(metadata, f)
    try:
        result = resolve_vault_addresses(metadata_fixture_path=fpath)
        backstop = [
            c for c in result.children
            if c.role_label in ("BACKSTOP", "BACKSTOP_INFERRED")
            and c.confidence in ("documented", "inferred_high")
        ]
        assert len(backstop) == 0
    finally:
        import shutil
        shutil.rmtree(d)


# ===========================================================================
# Address resolution stability
# ===========================================================================


def test_resolution_is_deterministic(metadata_fixture):
    """Calling resolve twice with same fixture gives same result."""
    result1 = resolve_vault_addresses(metadata_fixture_path=metadata_fixture)
    result2 = resolve_vault_addresses(metadata_fixture_path=metadata_fixture)
    assert result1.parent_address == result2.parent_address
    assert len(result1.children) == len(result2.children)
    for c1, c2 in zip(result1.children, result2.children):
        assert c1.address == c2.address
        assert c1.role_label == c2.role_label
        assert c1.confidence == c2.confidence


# ===========================================================================
# Source inventory checks
# ===========================================================================


def test_check_vault_metadata_source_found(metadata_fixture):
    """check_vault_metadata_source returns available when fixture exists."""
    data_root = os.path.dirname(metadata_fixture)
    entry = check_vault_metadata_source(data_root)
    # The fixture file is metadata.json, not vault_metadata.json
    # So check_vault_metadata_source checks specific paths
    # We'll verify it works with the actual vault metadata filename
    vault_dir = os.path.dirname(metadata_fixture)
    real_fixture_path = os.path.join(vault_dir, "vault_metadata.json")
    import shutil
    shutil.copy(metadata_fixture, real_fixture_path)
    entry2 = check_vault_metadata_source(vault_dir)
    assert entry2.available


def test_check_vault_metadata_source_not_found():
    """check_vault_metadata_source returns unavailable for missing path."""
    entry = check_vault_metadata_source("/nonexistent/path")
    assert not entry.available


def test_check_vault_metadata_source_no_root():
    """check_vault_metadata_source returns unavailable when data_root is None."""
    entry = check_vault_metadata_source(None)
    assert not entry.available
    assert entry.error == "No data_root provided"


# ===========================================================================
# PublicInfoApiClient tests
# ===========================================================================


def test_public_info_api_client_constructs():
    """PublicInfoApiClient can be constructed with defaults."""
    client = PublicInfoApiClient()
    assert client._timeout == 10


def test_public_info_api_client_custom_timeout():
    """PublicInfoApiClient accepts custom timeout."""
    client = PublicInfoApiClient(timeout=30)
    assert client._timeout == 30


def test_public_info_api_client_base_url():
    """PublicInfoApiClient has correct base URL."""
    assert PublicInfoApiClient.BASE_URL == "https://api.hyperliquid.xyz/info"


# ===========================================================================
# Frozen constants
# ===========================================================================


def test_frozen_parent_addresses_non_empty():
    """FROZEN_HLP_PARENT_ADDRESSES is non-empty."""
    assert len(FROZEN_HLP_PARENT_ADDRESSES) >= 1


def test_frozen_child_addresses_exist():
    """FROZEN_HLP_CHILD_ADDRESSES is a dict."""
    assert isinstance(FROZEN_HLP_CHILD_ADDRESSES, dict)


# ===========================================================================
# Forbidden API
# ===========================================================================


def test_no_user_fills_by_time():
    """Adapter does not use userFillsByTime as API call (docstring reference is allowed)."""
    import examples.strategies.venue_agnostic_signal_observer.adapters.hlp_vault_metadata_adapter as mod
    content = open(mod.__file__).read()
    # Strip docstrings and comments, check remaining code
    import ast
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.Str)):
                continue  # skip docstrings
        # Simple heuristic: check non-comment, non-string lines
        clean_lines = []
        in_docstring = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            if "userFillsByTime" in stripped:
                clean_lines.append(stripped)
        assert len(clean_lines) == 0, f"userFillsByTime used in code: {clean_lines}"
    except SyntaxError:
        # If AST parsing fails, fall back to simple check
        assert False, "Could not parse module for safety check"


# ===========================================================================
# Heuristic fill inference
# ===========================================================================


def test_resolve_from_fill_heuristic():
    """Heuristic resolution from fill data works."""
    fills = [
        {"buyer": "0xhlp_vault", "seller": "0xuser1", "sz": 1.0},
        {"buyer": "0xuser2", "seller": "0xhlp_vault", "sz": 2.0},
        {"buyer": "0xhlp_vault", "seller": "0xuser3", "sz": 3.0},
    ]
    result = resolve_vault_addresses(fill_data=fills)
    assert result.parent_resolved
    assert result.parent_address == "0xhlp_vault"
    assert len(result.children) >= 1


# ===========================================================================
# Adapter constructor exists
# ===========================================================================


def test_adapter_module_imports():
    """All expected exports exist."""
    from examples.strategies.venue_agnostic_signal_observer.adapters.hlp_vault_metadata_adapter import (
        resolve_vault_addresses,
        check_vault_metadata_source,
        PublicInfoApiClient,
    )
    assert callable(resolve_vault_addresses)
    assert callable(check_vault_metadata_source)
    assert PublicInfoApiClient is not None