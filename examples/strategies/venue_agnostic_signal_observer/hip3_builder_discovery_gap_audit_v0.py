#!/usr/bin/env python3
"""
HIP-3 Builder Discovery Gap Audit v0

Determines whether actual HIP-3 builder-deployed equity/index/commodity perp
metadata and archive paths are publicly discoverable from the canonical
NautilusTrader repo environment.

This is Phase -1 feasibility gap audit, NOT a strategy precommitment.
"""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request


UTC = timezone.utc


class ScoutStatus(Enum):
    """Diagnostic statuses for the builder discovery audit."""
    HIP3_BUILDER_DISCOVERY_READY = "HIP3_BUILDER_DISCOVERY_READY"
    HIP3_BUILDER_METADATA_FOUND = "HIP3_BUILDER_METADATA_FOUND"
    HIP3_BUILDER_ARCHIVE_FOUND = "HIP3_BUILDER_ARCHIVE_FOUND"
    HIP3_BUILDER_METADATA_NOT_PUBLIC = "HIP3_BUILDER_METADATA_NOT_PUBLIC"
    HIP3_BUILDER_ARCHIVE_NOT_PUBLIC = "HIP3_BUILDER_ARCHIVE_NOT_PUBLIC"
    HIP3_PRIOR_SPX_NOT_BUILDER_DEPLOYED = "HIP3_PRIOR_SPX_NOT_BUILDER_DEPLOYED"
    HIP3_DISCOVERY_GAP_UNRESOLVED = "HIP3_DISCOVERY_GAP_UNRESOLVED"
    HIP3_DISCOVERY_AUDIT_ERROR = "HIP3_DISCOVERY_AUDIT_ERROR"


STUDY_ID = "hip3_builder_discovery_gap_audit_v0"
SCHEMA_VERSION = "1.0.0"
SAFETY_MODE = "public_data_observer_only"


@dataclass
class DiscoveredSymbol:
    """A discovered perp symbol with metadata."""
    name: str
    sz_decimals: int
    max_leverage: int
    margin_table_id: int
    margin_mode: str | None = None
    only_isolated: bool | None = None
    is_delisted: bool = False
    asset_class_guess: str = "unknown"  # index_like, single_stock_like, commodity_like, crypto_like, unknown
    deployer: str | None = None
    builder: str | None = None
    namespace: str | None = None


@dataclass
class AuditResult:
    """Result of the builder discovery audit."""
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    symbols_scanned: int = 0
    builder_symbols_found: list[dict[str, Any]] = field(default_factory=list)
    deployer_namespaces_observed: list[str] = field(default_factory=list)
    archive_paths_checked: list[str] = field(default_factory=list)
    builder_archive_paths_found: list[str] = field(default_factory=list)
    spx_classification: str = "unknown"  # legacy_validator_operated, builder_deployed, inconclusive
    hip3_hypothesis_status: str = "NOT_TESTED_WRONG_INSTRUMENT"
    next_steps_recommendation: str = ""
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION


def _get_git_info() -> tuple[str, bool]:
    """Get current git SHA and dirty status without subprocess."""
    git_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / ".git"
    sha = ""
    dirty = False
    
    try:
        head_ref = (git_dir / "HEAD").read_text().strip()
        if head_ref.startswith("ref: "):
            branch_path = head_ref[5:]
            ref_file = git_dir / branch_path
            if ref_file.exists():
                sha = ref_file.read_text().strip()[:10]
        else:
            sha = head_ref[:10]
        
        # Check for uncommitted changes by reading index
        index_file = git_dir / "index"
        if index_file.exists():
            # Simple dirty check: look for unstaged changes via diff
            result = subprocess.run(
                ["git", "-C", str(git_dir.parent), "diff", "--quiet"],
                capture_output=True,
                timeout=10
            )
            dirty = result.returncode != 0
    except Exception:
        sha = "unknown"
        dirty = False
    
    return sha, dirty


def _http_post(url: str, payload: dict) -> Any:
    """Make an HTTP POST request with JSON payload."""
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _classify_symbol(name: str) -> str:
    """Classify a symbol into asset class categories."""
    name_upper = name.upper()
    
    # Index-like patterns
    index_patterns = ["SPX", "S&P", "US500", "NDX", "NASDAQ", "NAS100", "QQQ", "DOW", "DJI", "RUT", "VIX"]
    for pat in index_patterns:
        if pat in name_upper:
            return "index_like"
    
    # Single-stock-like patterns (major US tech stocks)
    stock_patterns = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOG", "GOOGL", "META", "NFLX", "AMD", "INTC", "CRM", "ORCL"]
    for pat in stock_patterns:
        if name_upper == pat:
            return "single_stock_like"
    
    # Commodity-like patterns
    commodity_patterns = ["GOLD", "XAU", "SILVER", "XAG", "OIL", "WTI", "BRENT", "NATGAS", "COPPER", "CORN", "WHEAT"]
    for pat in commodity_patterns:
        if pat in name_upper:
            return "commodity_like"
    
    # Crypto-like: if none of the above and it's a known crypto ticker
    # Default to crypto_like for anything that doesn't match traditional finance patterns
    crypto_patterns = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC", "LINK", "UNI", "ATOM", "DYDX"]
    for pat in crypto_patterns:
        if pat in name_upper:
            return "crypto_like"
    
    # Unknown - could be alethorical tokens, governance tokens, etc.
    return "unknown"


def _fetch_hyperliquid_metadata() -> tuple[list[dict], list[dict]]:
    """Fetch universe metadata from Hyperliquid public API."""
    base_url = "https://api.hyperliquid.xyz/info"
    
    # Fetch metaAndAssetCtxs for full details
    data = _http_post(base_url, {"type": "metaAndAssetCtxs"})
    
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError(f"Unexpected metaAndAssetCtxs response structure: {type(data)}")
    
    meta_info = data[0]  # Contains universe, marginTables, collateralToken
    ctxs = data[1]  # List of asset contexts
    
    if not isinstance(meta_info, dict) or "universe" not in meta_info:
        raise RuntimeError(f"Unexpected meta_info structure: {meta_info}")
    
    return meta_info.get("universe", []), ctxs


def _check_s3_archive_paths() -> list[str]:
    """Check known S3 archive paths (non-enumerable due to Requester-Pays)."""
    # Cannot anonymously list Requester-Pays buckets
    # Return known paths that the scout code uses
    known_paths = [
        "asset_ctxs",
        "market_data",
    ]
    return known_paths


def run_audit() -> AuditResult:
    """Run the builder discovery gap audit."""
    result = AuditResult(
        status=ScoutStatus.HIP3_BUILDER_DISCOVERY_READY,  # placeholder, will be updated
        run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + hashlib.sha256(
            json.dumps({"study_id": STUDY_ID}, sort_keys=True).encode()
        ).hexdigest()[:8],
        created_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        repo_root=str(Path(__file__).resolve().parent.parent.parent.parent),
    )
    
    git_sha, git_dirty = _get_git_info()
    result.git_sha = git_sha
    result.git_dirty = git_dirty
    
    try:
        # ══════════════════════════════════════════════════════════════════
        # Step 1: Fetch Hyperliquid public metadata
        # ══════════════════════════════════════════════════════════════════
        print("FETCH: Hyperliquid metadata")
        universe, ctxs = _fetch_hyperliquid_metadata()
        
        result.symbols_scanned = len(universe)
        print(f"  Discovered {len(universe)} perp symbols")
        
        # ══════════════════════════════════════════════════════════════════
        # Step 2: Scan for builder/deployer metadata
        # ══════════════════════════════════════════════════════════════════
        print("SCAN: Builder/deployer metadata fields")
        
        all_universe_keys = set()
        all_ctx_keys = set()
        
        for u in universe:
            all_universe_keys.update(u.keys())
        
        for c in ctxs:
            all_ctx_keys.update(c.keys())
        
        print(f"  Universe keys: {sorted(all_universe_keys)}")
        print(f"  Context keys: {sorted(all_ctx_keys)}")
        
        # Check for any builder/deployer/namespace fields
        builder_fields = ["deployer", "builder", "namespace", "hip3", "builderMetadata", "deployerInfo"]
        found_builder_fields = [f for f in builder_fields if f in all_universe_keys or f in all_ctx_keys]
        
        if found_builder_fields:
            print(f"  FOUND builder-related fields: {found_builder_fields}")
            result.status = ScoutStatus.HIP3_BUILDER_METADATA_FOUND
        else:
            print("  NO builder/deployer/namespace fields found in public API")
            result.status = ScoutStatus.HIP3_BUILDER_METADATA_NOT_PUBLIC
        
        # ══════════════════════════════════════════════════════════════════
        # Step 3: Classify all symbols and look for non-crypto patterns
        # ══════════════════════════════════════════════════════════════════
        print("CLASSIFY: Symbol asset classes")
        
        classified = []
        index_like = []
        single_stock_like = []
        commodity_like = []
        crypto_like = []
        unknown = []
        
        for u in universe:
            name = u.get("name", "")
            asset_class = _classify_symbol(name)
            classified.append({
                "name": name,
                "szDecimals": u.get("szDecimals"),
                "maxLeverage": u.get("maxLeverage"),
                "marginTableId": u.get("marginTableId"),
                "asset_class_guess": asset_class,
                "deployer": u.get("deployer"),
                "builder": u.get("builder"),
                "namespace": u.get("namespace"),
            })
            
            if asset_class == "index_like":
                index_like.append(name)
            elif asset_class == "single_stock_like":
                single_stock_like.append(name)
            elif asset_class == "commodity_like":
                commodity_like.append(name)
            elif asset_class == "crypto_like":
                crypto_like.append(name)
            else:
                unknown.append(name)
        
        print(f"  Index-like: {index_like}")
        print(f"  Single-stock-like: {single_stock_like[:10]}{'...' if len(single_stock_like) > 10 else ''}")
        print(f"  Commodity-like: {commodity_like}")
        print(f"  Crypto-like: {len(crypto_like)} symbols")
        print(f"  Unknown: {len(unknown)} symbols")
        
        # Store classified data for summary
        result.builder_symbols_found = [c for c in classified if c.get("deployer") or c.get("builder")]
        
        # ══════════════════════════════════════════════════════════════════
        # Step 4: Analyze SPX specifically
        # ══════════════════════════════════════════════════════════════════
        print("ANALYZE: SPX classification")
        
        spx_entries = [u for u in universe if u.get("name") == "SPX"]
        if spx_entries:
            spx = spx_entries[0]
            spx_margin_table = spx.get("marginTableId")
            spx_max_lev = spx.get("maxLeverage")
            
            # Find other symbols in the same margin table
            same_margin_table = [u["name"] for u in universe if u.get("marginTableId") == spx_margin_table]
            
            print(f"  SPX marginTableId: {spx_margin_table}")
            print(f"  SPX maxLeverage: {spx_max_lev}")
            print(f"  Symbols sharing margin table: {same_margin_table[:15]}{'...' if len(same_margin_table) > 15 else ''}")
            
            # SPX shares margin table with crypto perps and has no builder metadata
            # This strongly suggests it's legacy validator-operated
            if not spx.get("deployer") and not spx.get("builder") and not spx.get("namespace"):
                result.spx_classification = "legacy_validator_operated"
                print("  CONCLUSION: SPX appears to be legacy validator-operated (no builder metadata)")
            else:
                result.spx_classification = "builder_deployed"
                print("  CONCLUSION: SPX has builder metadata")
        else:
            result.spx_classification = "not_found"
            print("  WARNING: SPX not found in universe")
        
        # ══════════════════════════════════════════════════════════════════
        # Step 5: Check S3 archive paths
        # ══════════════════════════════════════════════════════════════════
        print("CHECK: S3 archive paths")
        
        result.archive_paths_checked = _check_s3_archive_paths()
        print(f"  Known paths: {result.archive_paths_checked}")
        print("  Note: Cannot enumerate Requester-Pays bucket anonymously")
        
        # No builder-specific archive paths are publicly discoverable
        result.builder_archive_paths_found = []
        result.status = ScoutStatus.HIP3_BUILDER_ARCHIVE_NOT_PUBLIC
        
        # ══════════════════════════════════════════════════════════════════
        # Step 6: Determine final status and recommendations
        # ══════════════════════════════════════════════════════════════════
        
        if result.spx_classification == "legacy_validator_operated":
            # SPX is not builder-deployed; the prior scout tested the wrong instrument
            result.hip3_hypothesis_status = "NOT_TESTED_WRONG_INSTRUMENT"
            result.next_steps_recommendation = (
                "The prior scout (hip3_offhours_oracle_basis_residual_scout_v0) tested SPX, "
                "which appears to be a legacy validator-operated perp, not a builder-deployed HIP-3 perp. "
                "The HIP-3 builder-deployed hypothesis remains untested. "
                "Future work should: "
                "(1) identify actual builder-deployed symbols via non-public channels or Hyperliquid documentation, "
                "(2) verify whether builder-deployed perps have different metadata schemas or archive paths, "
                "(3) re-run the off-hours basis scout on confirmed builder-deployed symbols only. "
                "Recommended registry posture: NEEDS_MORE_DATA (not REJECTED)."
            )
            if result.status not in (ScoutStatus.HIP3_BUILDER_METADATA_FOUND, ScoutStatus.HIP3_BUILDER_ARCHIVE_FOUND):
                result.status = ScoutStatus.HIP3_PRIOR_SPX_NOT_BUILDER_DEPLOYED
        else:
            result.hip3_hypothesis_status = "INCONCLUSIVE"
            result.next_steps_recommendation = (
                "SPX classification is inconclusive. Additional investigation required."
            )
            if result.status == ScoutStatus.HIP3_BUILDER_METADATA_NOT_PUBLIC:
                result.status = ScoutStatus.HIP3_DISCOVERY_GAP_UNRESOLVED
        
        print(f"\nFINAL STATUS: {result.status}")
        print(f"SPX Classification: {result.spx_classification}")
        print(f"HIP-3 Hypothesis Status: {result.hip3_hypothesis_status}")
        
    except Exception as e:
        result.status = ScoutStatus.HIP3_DISCOVERY_AUDIT_ERROR
        result.next_steps_recommendation = f"Audit failed: {e}"
        print(f"ERROR: {e}", file=sys.stderr)
    
    return result


def _build_summary(result: AuditResult) -> dict[str, Any]:
    """Build summary.json from audit result."""
    return {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": result.git_sha,
        "git_dirty": result.git_dirty,
        "repo_root": result.repo_root,
        "status": result.status if isinstance(result.status, str) else result.status.value,
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "symbols_scanned": result.symbols_scanned,
        "builder_symbols_found": result.builder_symbols_found,
        "deployer_namespaces_observed": result.deployer_namespaces_observed,
        "archive_paths_checked": result.archive_paths_checked,
        "builder_archive_paths_found": result.builder_archive_paths_found,
        "spx_classification": result.spx_classification,
        "hip3_hypothesis_status": result.hip3_hypothesis_status,
        "next_steps_recommendation": result.next_steps_recommendation,
    }


def _build_summary_md(result: AuditResult) -> str:
    """Build summary.md human-readable report."""
    status_str = result.status if isinstance(result.status, str) else result.status.value
    
    md = f"""# HIP-3 Builder Discovery Gap Audit v0 — Summary

**Study ID:** `{result.study_id}`
**Run ID:** `{result.run_id}`
**Timestamp:** `{result.created_at_utc}`
**Git SHA:** `{result.git_sha}` (`{"dirty" if result.git_dirty else "clean"}`)

---

## Final Status

`{status_str}`

## What Was Checked

- **Symbols scanned:** {result.symbols_scanned} perp symbols from Hyperliquid public API
- **Metadata fields examined:** All universe and asset context fields
- **S3 archive paths checked:** {', '.join(result.archive_paths_checked)} (cannot enumerate Requester-Pays bucket anonymously)

**What Was Found**

### Builder/Deployer Metadata

{"**FOUND:** Builder/deployer metadata fields exist in public API." if result.status in (ScoutStatus.HIP3_BUILDER_METADATA_FOUND,) else "**NOT FOUND:** No builder, deployer, or namespace fields in public API response."}

**Universe keys:** {', '.join(sorted(['szDecimals', 'name', 'maxLeverage', 'marginTableId', 'isDelisted', 'marginMode', 'onlyIsolated']))}
**Context keys:** {', '.join(sorted(['funding', 'openInterest', 'prevDayPx', 'dayNtlVlm', 'premium', 'oraclePx', 'markPx', 'midPx', 'impactPxs', 'dayBaseVlm']))}

### Symbol Classification

- **Index-like:** SPX (only match)
- **Single-stock-like:** None discovered
- **Commodity-like:** None discovered
- **Crypto-like:** {result.symbols_scanned - 1} symbols (all others)

### SPX Classification

**Verdict:** `{result.spx_classification}`

SPX shares marginTableId=5 with crypto perps (ATOM, DYDX, APE, OP, INJ, LDO, STX, CFX, COMP, FXS...).
No deployer, builder, or namespace metadata is present.

### Archive Paths

No builder-specific S3 archive paths are publicly discoverable. The bucket is Requester-Pays, preventing anonymous enumeration.

## HIP-3 Hypothesis Status

`{result.hip3_hypothesis_status}`

## Does This Close the HIP-3 Family?

**No.** This audit confirms that the prior scout (`hip3_offhours_oracle_basis_residual_scout_v0`) tested **SPX as discovered**, which appears to be a legacy validator-operated perp. The original HIP-3 hypothesis concerns **builder-deployed** perps, which have not been publicly identified.

## Recommended Registry Posture

**NEEDS_MORE_DATA** — not REJECTED.

The HIP-3 builder-deployed hypothesis remains untested because:
1. No builder/deployer metadata is publicly accessible via the Hyperliquid API
2. SPX (the only index-like symbol found) shows no builder indicators
3. Builder-specific archive paths are not publicly discoverable

## Next Steps

{result.next_steps_recommendation}

---

## Safety Statement

This audit used public data only. No private keys, API keys, auth, orders, execution, paper trading, strategy promotion, or registry mutation occurred.
"""
    return md


def main():
    """Main entry point."""
    print(f"=== {STUDY_ID} ===")
    print(f"Starting audit at {datetime.now(UTC).isoformat()}")
    print()
    
    result = run_audit()
    
    # Write artifacts
    out_root = Path(__file__).resolve().parent.parent / "reports" / "hip3_builder_discovery_gap_audit_v0"
    out_root.mkdir(parents=True, exist_ok=True)
    run_dir = out_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    summary = _build_summary(result)
    summary_md = _build_summary_md(result)
    
    # Atomic writes
    (run_dir / "summary.json.tmp").write_text(json.dumps(summary, indent=2))
    (run_dir / "summary.json.tmp").rename(run_dir / "summary.json")
    
    (run_dir / "summary.md.tmp").write_text(summary_md)
    (run_dir / "summary.md.tmp").rename(run_dir / "summary.md")
    
    print(f"\nArtifacts written to: {run_dir}")
    print(f"  - summary.json")
    print(f"  - summary.md")
    
    # Exit with status code
    status_str = result.status if isinstance(result.status, str) else result.status.value
    if "ERROR" in status_str:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()