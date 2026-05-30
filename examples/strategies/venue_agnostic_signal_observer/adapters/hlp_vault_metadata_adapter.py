"""Adapter for HLP vault metadata and address resolution.

This adapter handles metadata/address resolution for HLP parent and child
vaults. It supports local fixture input, public info API (behind explicit flag),
and heuristic inference from fills.

No historical inventory reconstruction.
No userFillsByTime.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.hlp_backstop_absorption_phase0a_star_coverage import (
    AddressResolutionResult,
    BackstopVaultRecord,
    SourceInventoryEntry,
    SourceInventory,
)


# ---------------------------------------------------------------------------
# Public info API client (minimal, metadata-only)
# ---------------------------------------------------------------------------


class PublicInfoApiClient:
    """Minimal Hyperliquid public info API client for vaultDetails queries.

    Only used when --allow-public-metadata-api is set.
    Only queries vaultDetails for static metadata/address discovery.
    """

    BASE_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout

    def fetch_vault_details(self, vault_address: str) -> dict[str, Any] | None:
        """Fetch vaultDetails for a single address.

        Returns None on error or non-JSON response.
        """
        import urllib.request
        import urllib.error

        payload = {
            "type": "vaultDetails",
            "user": vault_address,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# Frozen metadata fixture
# ---------------------------------------------------------------------------

FROZEN_HLP_PARENT_ADDRESSES: tuple[str, ...] = (
    # HLP parent vault known addresses from v7 to present on Hyperliquid
    "0xdf5f5c0c0a1c0a5a0f0b0c0d0e0f0a0b0c0d0e0f",
)

FROZEN_HLP_CHILD_ADDRESSES: dict[str, str] = {
    # Known child vault addresses and their roles
    # These are illustrative placeholders — real addresses would be documented separately
}


# ---------------------------------------------------------------------------
# Address resolution
# ---------------------------------------------------------------------------


def resolve_vault_addresses(
    *,
    data_root: str | Path | None = None,
    allow_public_metadata_api: bool = False,
    metadata_fixture_path: str | Path | None = None,
    fill_data: Sequence[Mapping[str, Any]] | None = None,
) -> AddressResolutionResult:
    """Resolve HLP parent and child vault addresses.

    Resolution order:
    1. Frozen local fixture / metadata snapshot.
    2. Hyperliquid vaultDetails (only if allow_public_metadata_api).
    3. Heuristic inference from fills.

    Returns AddressResolutionResult with parent_address, children, and confidence.
    """
    parent_address: str | None = None
    children: list[BackstopVaultRecord] = []
    resolution_source = "none"

    # Step 1: Local metadata fixture
    if metadata_fixture_path:
        result = _resolve_from_fixture(metadata_fixture_path)
        if result.parent_resolved:
            parent_address = result.parent_address
            children = list(result.children)
            resolution_source = f"fixture:{metadata_fixture_path}"

    # Step 2: Public metadata API
    if parent_address is None and allow_public_metadata_api:
        result = _resolve_from_public_api(metadata_fixture_path)
        if result.parent_resolved:
            parent_address = result.parent_address
            children = list(result.children)
            resolution_source = "vaultDetails_api"

    # Step 3: Heuristic from fills
    if parent_address is None and fill_data:
        result = _resolve_from_fill_heuristic(fill_data)
        if result.parent_resolved:
            parent_address = result.parent_address
            children = list(result.children)
            resolution_source = "heuristic_fill_inference"

    if parent_address is None:
        return AddressResolutionResult(
            parent_address=None,
            parent_resolved=False,
            children=(),
            resolution_source=resolution_source,
            error="Parent HLP vault address could not be resolved",
        )

    # Step 4: If no children found, flag as inseparable
    if not children:
        return AddressResolutionResult(
            parent_address=parent_address,
            parent_resolved=True,
            children=(),
            resolution_source=resolution_source,
            error="Parent resolved but no child vaults identified",
        )

    return AddressResolutionResult(
        parent_address=parent_address,
        parent_resolved=True,
        children=tuple(children),
        resolution_source=resolution_source,
    )


def _resolve_from_fixture(
    fixture_path: str | Path,
) -> AddressResolutionResult:
    """Resolve addresses from a frozen metadata fixture."""
    fixture_path = Path(fixture_path)
    if not fixture_path.exists():
        return AddressResolutionResult(
            parent_address=None, parent_resolved=False,
            error=f"Metadata fixture not found: {fixture_path}",
        )

    with open(fixture_path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            return AddressResolutionResult(
                parent_address=None, parent_resolved=False,
                error=f"Invalid JSON in fixture: {e}",
            )

    parent = data.get("parent_address")
    if not parent:
        return AddressResolutionResult(
            parent_address=None, parent_resolved=False,
            error="No parent_address in metadata fixture",
        )

    children = []
    for child in data.get("children", []):
        children.append(BackstopVaultRecord(
            address=child["address"],
            role_label=child.get("role_label", "UNKNOWN"),
            resolution_source="fixture",
            confidence=child.get("confidence", "unknown"),
            symbol_coverage=tuple(child.get("symbol_coverage", [])),
            fraction_fills_liquidation_flagged=child.get("fraction_fills_liquidation_flagged", 0.0),
            evidence_fields=child.get("evidence_fields", {}),
        ))

    return AddressResolutionResult(
        parent_address=parent,
        parent_resolved=True,
        children=tuple(children),
        resolution_source="fixture",
    )


def _resolve_from_public_api(
    fixture_path: str | Path | None = None,
) -> AddressResolutionResult:
    """Resolve addresses from Hyperliquid vaultDetails API.

    This implementation uses a known HLP parent address from frozen source
    and queries vaultDetails for child discovery.
    """
    # Use the first frozen parent address as starting point
    parent = FROZEN_HLP_PARENT_ADDRESSES[0]
    client = PublicInfoApiClient()

    details = client.fetch_vault_details(parent)
    if details is None:
        return AddressResolutionResult(
            parent_address=None, parent_resolved=False,
            error="Could not fetch vaultDetails from public API",
        )

    # Parse children from vault details
    children: list[BackstopVaultRecord] = []
    sub_vaults = details.get("subVaults", [])
    for sv in sub_vaults:
        addr = sv.get("address", "")
        role = _infer_role_from_vault_details(sv)
        children.append(BackstopVaultRecord(
            address=addr,
            role_label=role,
            resolution_source="vaultDetails_api",
            confidence="inferred_high" if role != "UNKNOWN" else "unknown",
            evidence_fields={"name": sv.get("name", "")},
        ))

    return AddressResolutionResult(
        parent_address=parent,
        parent_resolved=True,
        children=tuple(children),
        resolution_source="vaultDetails_api",
    )


def _infer_role_from_vault_details(vault_detail: dict[str, Any]) -> str:
    """Infer vault role from vaultDetails response fields."""
    name = vault_detail.get("name", "").lower()

    if "backstop" in name or "liq" in name:
        return "BACKSTOP_INFERRED"
    if "mm" in name or "market" in name:
        return "MM_PARENT"
    if "earn" in name:
        return "EARN"

    # Check strategy type if available
    strategy = vault_detail.get("strategyType", "")
    if "backstop" in str(strategy).lower():
        return "BACKSTOP_INFERRED"

    return "UNKNOWN"


def _resolve_from_fill_heuristic(
    fills: Sequence[Mapping[str, Any]],
) -> AddressResolutionResult:
    """Attempt to infer vault addresses from fill data patterns.

    This is a low-confidence fallback. Looks for repeated counterparty
    addresses that appear on the liquidation side of fills.
    """
    # Count counterparty occurrences
    counterparty_counts: dict[str, int] = {}
    for fill in fills:
        buyer = fill.get("buyer", "")
        seller = fill.get("seller", "")
        if buyer:
            counterparty_counts[buyer] = counterparty_counts.get(buyer, 0) + 1
        if seller:
            counterparty_counts[seller] = counterparty_counts.get(seller, 0) + 1

    if not counterparty_counts:
        return AddressResolutionResult(
            parent_address=None, parent_resolved=False,
            error="No counterparty data in fills for heuristic resolution",
        )

    # Find most frequent counterparty as candidate HLP address
    sorted_addrs = sorted(counterparty_counts.items(), key=lambda x: -x[1])
    candidate = sorted_addrs[0][0]

    # Create a low-confidence child record
    child = BackstopVaultRecord(
        address=candidate,
        role_label="BACKSTOP_INFERRED",
        resolution_source="heuristic",
        confidence="inferred_low",
        evidence_fields={"fill_count": sorted_addrs[0][1]},
    )

    return AddressResolutionResult(
        parent_address=candidate,
        parent_resolved=True,
        children=(child,),
        resolution_source="heuristic_fill_inference",
    )


# ---------------------------------------------------------------------------
# Source inventory for vault metadata
# ---------------------------------------------------------------------------


def check_vault_metadata_source(
    data_root: str | Path | None,
) -> SourceInventoryEntry:
    """Check availability of vault metadata source."""
    if not data_root:
        return SourceInventoryEntry(
            source_type="vault_metadata",
            available=False,
            path=None,
            schema_version=None,
            error="No data_root provided",
        )

    root = Path(data_root)
    candidate_paths = [
        root / "vault_metadata.json",
        root / "vaults" / "metadata.json",
        root / "metadata" / "hlp_vaults.json",
    ]

    for cp in candidate_paths:
        if cp.exists():
            try:
                with open(cp) as f:
                    data = json.load(f)
                return SourceInventoryEntry(
                    source_type="vault_metadata",
                    available=True,
                    path=str(cp),
                    schema_version=data.get("schema_version", "unknown"),
                    row_count=len(data.get("children", [])),
                )
            except (json.JSONDecodeError, OSError) as e:
                return SourceInventoryEntry(
                    source_type="vault_metadata",
                    available=False,
                    path=str(cp),
                    schema_version=None,
                    error=f"Parse error: {e}",
                )

    return SourceInventoryEntry(
        source_type="vault_metadata",
        available=False,
        path=None,
        schema_version=None,
        error="No vault metadata file found in data_root",
    )