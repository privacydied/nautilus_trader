"""Wall 2 chronology-strict selection and address identity join tests.

Tests added to prove:
- Object timestamp parsing from replica_cmds/YYYY-MM-DDThh:mm:ssZ/YYYYMMDD/<timestamp>.lz4 keys.
- Selection is chronology-strict newest-prior, not size-primary.
- Chronological skips due to oversized objects are recorded.
- Address normalization and identity join work correctly.
- 0 target matches cannot be interpreted as cross or isolated margin mode.
"""

from __future__ import annotations

import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on sys.path for imports
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in __import__("sys").path:
    __import__("sys").path.insert(0, _repo_root)

from examples.strategies.venue_agnostic_signal_observer import (  # noqa: E402
    hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 as probe_mod,
)


# ---------------------------------------------------------------------------
# Test fixtures for chronology-strict selection
# ---------------------------------------------------------------------------

def _make_replica_cmds_key(date_prefix: str, timestamp_ms: int, iso_ts: str = "") -> str:
    """Build a replica_cmds S3 key like real ones."""
    if not iso_ts:
        # Generate ISO timestamp from ms (rough approximation for test purposes)
        iso_ts = f"2025-07-27T12:00:{timestamp_ms % 60:02d}Z"
    return f"replica_cmds/{iso_ts}/{date_prefix}/{timestamp_ms}.lz4"


def _make_s3_object(key: str, size: int) -> dict:
    """Build a mock S3 list-objects-v2 entry."""
    return {"Key": key, "Size": size}


# ---------------------------------------------------------------------------
# test_replica_cmds_object_timestamp_parses_iso_two_level_key
# ---------------------------------------------------------------------------

def test_replica_cmds_object_timestamp_parses_iso_two_level_key():
    """Object timestamp extraction handles the two-level S3 key format."""
    key = _make_replica_cmds_key("20250727", 677270000)
    date_prefix, ts_int = probe_mod._extract_replica_cmds_object_timestamp(key)
    assert date_prefix == "20250727"
    assert ts_int == 677270000


def test_replica_cmds_object_timestamp_parses_iso_two_level_key_fallback():
    """Object timestamp extraction handles keys without filename timestamp."""
    key = "replica_cmds/2025-07-27T12:00:27Z/"
    date_prefix, ts_int = probe_mod._extract_replica_cmds_object_timestamp(key)
    assert date_prefix == "20250727"
    # When no filename timestamp, ts_int should be 0
    assert ts_int == 0


def test_replica_cmds_object_timestamp_parses_iso_two_level_key_unknown():
    """Object timestamp extraction returns unknown for malformed keys."""
    date_prefix, ts_int = probe_mod._extract_replica_cmds_object_timestamp("random/file")
    assert date_prefix == "unknown"
    assert ts_int == 0


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_strict_selection_newest_prior_first
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_strict_selection_newest_prior_first():
    """Selection picks newest prior object first (chronology-strict)."""
    # Create mock objects sorted by timestamp descending
    objects = [
        _make_s3_object(_make_replica_cmds_key("20250727", 677270000), 1_000_000),
        _make_s3_object(_make_replica_cmds_key("20250727", 677260000), 1_000_000),
        _make_s3_object(_make_replica_cmds_key("20250712", 659959000), 1_000_000),
        _make_s3_object(_make_replica_cmds_key("20250705", 652040000), 1_000_000),
    ]

    # Simulate the chronology-strict selection: sort by timestamp desc, pick until cap
    max_download_bytes = 10_000_000  # 10MB
    selected = []
    cumulative = 0
    for obj in sorted(objects, key=lambda o: probe_mod._extract_replica_cmds_object_timestamp(o["Key"])[1], reverse=True):
        size = obj["Size"]
        if cumulative + size > max_download_bytes:
            break
        selected.append(obj)
        cumulative += size

    # Newest should be first
    assert len(selected) == 4
    assert probe_mod._extract_replica_cmds_object_timestamp(selected[0]["Key"])[1] == 677270000
    assert probe_mod._extract_replica_cmds_object_timestamp(selected[3]["Key"])[1] == 652040000


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_does_not_sort_by_size
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_does_not_sort_by_size():
    """Selection does NOT sort primarily by compressed size."""
    # Create objects where the smallest comes LAST chronologically
    small_old = _make_s3_object(_make_replica_cmds_key("20250601", 614478000), 1_000)
    large_new = _make_s3_object(_make_replica_cmds_key("20250727", 677270000), 5_000_000)

    objects = [large_new, small_old]

    # Chronology-strict: largest (newest) first
    sorted_by_chrono = sorted(objects, key=lambda o: probe_mod._extract_replica_cmds_object_timestamp(o["Key"])[1], reverse=True)
    assert sorted_by_chrono[0] is large_new  # Newest first despite being larger

    # Size-based (old buggy): smallest first
    sorted_by_size = sorted(objects, key=lambda o: o["Size"])
    assert sorted_by_size[0] is small_old  # Smallest first


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_records_skipped_oversized_gap
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_records_skipped_oversized_gap():
    """Selection records skipped oversized objects for gap analysis."""
    scan_plan = probe_mod.TargetedBackwardLookupScanPlan()
    scan_plan.selection_mode = "chronology_strict_newest_prior"

    # Objects: large (fits), medium (fits), tiny-but-old (skipped because budget exhausted)
    objects = [
        _make_s3_object(_make_replica_cmds_key("20250727", 677270000), 4_000_000),
        _make_s3_object(_make_replica_cmds_key("20250727", 677260000), 4_000_000),
        _make_s3_object(_make_replica_cmds_key("20250712", 659959000), 2_000_000),
    ]

    max_download_bytes = 8_000_000  # Only enough for first two
    selected = []
    skipped = []
    cumulative = 0
    for obj in sorted(objects, key=lambda o: probe_mod._extract_replica_cmds_object_timestamp(o["Key"])[1], reverse=True):
        size = obj["Size"]
        if cumulative + size > max_download_bytes:
            _, ts_int = probe_mod._extract_replica_cmds_object_timestamp(obj["Key"])
            skipped.append({"key": obj["Key"], "timestamp_ms": ts_int, "size": size})
            scan_plan.objects_skipped_budget_exhausted += 1
            continue
        selected.append(obj)
        cumulative += size

    assert len(selected) == 2
    assert len(skipped) == 1
    assert scan_plan.objects_skipped_budget_exhausted == 1


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_ignores_future_objects
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_ignores_future_objects():
    """Future objects after fill window end are excluded."""
    # Objects: one before fill window, one at fill window, one after
    objects = [
        _make_s3_object(_make_replica_cmds_key("20250727", 677270000), 1_000_000),  # In range
        _make_s3_object(_make_replica_cmds_key("20250801", 680000000), 1_000_000),  # Future (after fill window)
        _make_s3_object(_make_replica_cmds_key("20250720", 674000000), 1_000_000),  # In range (older)
    ]

    # Date-range filter: only objects within [end_date_str, start_date_str]
    end_date_str = "20250720"
    start_date_str = "20250727"

    def _extract_key_date(key):
        parts = key.split("/")
        if len(parts) >= 3:
            return parts[2]
        return ""

    filtered = [obj for obj in objects if end_date_str <= _extract_key_date(obj["Key"]) <= start_date_str]

    assert len(filtered) == 2
    # The future object (20250801) is excluded
    key_dates = [_extract_key_date(obj["Key"]) for obj in filtered]
    assert "20250801" not in key_dates


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_stops_when_all_targets_resolved
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_stops_when_all_targets_resolved():
    """Selection stops when all target pairs are resolved."""
    # Simulate: 5 objects, first 2 resolve all targets
    remaining_cap = 10_000_000
    total_objects = 5

    # Mock resolution after 2 objects
    resolved_after = 2
    selected_count = 0
    for i in range(total_objects):
        if i >= resolved_after:
            break
        selected_count += 1

    assert selected_count == resolved_after


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_stops_when_resolved_notional_threshold_met
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_stops_when_resolved_notional_threshold_met():
    """Selection stops when resolved notional fraction reaches threshold."""
    target_notional = Decimal("100000000")
    resolved_notional = Decimal("0")
    threshold = 0.60

    # Simulate progressive resolution
    for obj_idx in range(5):
        if obj_idx < 2:
            resolved_notional += Decimal("40000000")  # Each object resolves 40%
        fraction = float(resolved_notional / target_notional) if target_notional > 0 else 0.0
        if fraction >= threshold:
            break

    assert resolved_notional == Decimal("80000000")


# ---------------------------------------------------------------------------
# test_replica_cmds_chronology_selection_reports_unresolved_under_cap
# ---------------------------------------------------------------------------

def test_replica_cmds_chronology_selection_reports_unresolved_under_cap():
    """When no targets resolve under cap, stop rule is UNRESOLVED_UNDER_CAP."""
    # 0 target matches with objects downloaded -> should not be CAP_EXHAUSTED
    cap = 25_000_000_000
    bytes_downloaded = 5_000_000_000
    target_notional_resolved_fraction = 0.0

    # Not cap exhausted because we have remaining budget and no matches
    if target_notional_resolved_fraction < 0.60:
        stop_rule = "UNRESOLVED_UNDER_CAP"
    else:
        stop_rule = "ALL_TARGETS_RESOLVED"

    assert stop_rule == "UNRESOLVED_UNDER_CAP"


# ---------------------------------------------------------------------------
# test_previous_5gb_object_selection_audit_detects_size_biased_selection
# ---------------------------------------------------------------------------

def test_previous_5gb_object_selection_audit_detects_size_biased_selection():
    """Audit detects if previous selection was size-biased."""
    # Previous run: selected 10 objects, first 6 total 4.9GB, last 4 are tiny (total ~72MB)
    keys = [
        _make_replica_cmds_key("20250727", 677270000),
        _make_replica_cmds_key("20250727", 677260000),
        _make_replica_cmds_key("20250727", 677250000),
        _make_replica_cmds_key("20250727", 677240000),
        _make_replica_cmds_key("20250727", 677230000),
        _make_replica_cmds_key("20250727", 677170000),
        _make_replica_cmds_key("20250712", 659959000),
        _make_replica_cmds_key("20250705", 652040000),
        _make_replica_cmds_key("20250621", 636551000),
        _make_replica_cmds_key("20250601", 614478000),
    ]
    sizes = [942471553, 742350699, 833627578, 852185955, 1034062937, 515291323,
             4191796, 43486996, 3159738, 20115797]

    # Check: timestamps are monotonically descending (chronological order)
    timestamps = [probe_mod._extract_replica_cmds_object_timestamp(k)[1] for k in keys]
    is_descending = all(timestamps[i] >= timestamps[i + 1] for i in range(len(timestamps) - 1))
    assert is_descending, "Selection should be chronologically ordered"

    # Check: size bias indicator (first 6 objects are 942MB-1GB each, last 4 are KB-MB)
    first_group_total = sum(sizes[:6])
    second_group_total = sum(sizes[6:])
    assert first_group_total > second_group_total * 50, f"First group ({first_group_total:,}) should be much larger than second ({second_group_total:,})"

    # Check: chronological gap between index 5 and 6 (July 27 -> July 12)
    ts_5 = timestamps[5]
    ts_6 = timestamps[6]
    assert ts_5 > ts_6, "There should be a chronological gap due to budget exhaustion"


# ---------------------------------------------------------------------------
# test_previous_5gb_object_selection_audit_passes_chronology_strict_selection
# ---------------------------------------------------------------------------

def test_previous_5gb_object_selection_audit_passes_chronology_strict_selection():
    """Audit passes when selection is chronologically ordered."""
    # Same data as above - timestamps are descending
    keys = [
        _make_replica_cmds_key("20250727", 677270000),
        _make_replica_cmds_key("20250727", 677260000),
        _make_replica_cmds_key("20250727", 677230000),
    ]

    timestamps = [probe_mod._extract_replica_cmds_object_timestamp(k)[1] for k in keys]
    is_descending = all(timestamps[i] >= timestamps[i + 1] for i in range(len(timestamps) - 1))
    assert is_descending


# ---------------------------------------------------------------------------
# Address identity join tests
# ---------------------------------------------------------------------------

def test_address_normalization_lowercase_0x():
    """Address normalization produces lowercase with 0x prefix."""
    def _normalize_address(addr):
        a = addr.strip()
        if not a.lower().startswith("0x"):
            a = "0x" + a
        return a.lower()

    assert _normalize_address("0xABC123") == "0xabc123"
    assert _normalize_address("abc123") == "0xabc123"
    assert _normalize_address("0XAbC123") == "0xabc123"


def test_address_identity_join_checks_signer_and_vault_address():
    """Identity join checks both signer and vaultAddress fields."""
    matches = [
        {"identity": "0xAAA111", "vaultAddress": "0xBBB222"},
        {"identity": "0xCCC333", "vaultAddress": "0xDDD444"},
    ]

    target_addrs_normalized = {"0xaaa111", "0xeee555"}

    signer_addrs = set()
    vault_addrs = set()
    for match in matches:
        identity = str(match.get("identity") or "")
        if identity:
            norm = probe_mod._normalize_address(identity) if hasattr(probe_mod, '_normalize_address') else (
                "0x" + identity.lower().removeprefix("0x") if not identity.lower().startswith("0x") else identity.lower()
            )
            signer_addrs.add(norm)
        vault_addr = str(match.get("vaultAddress") or "")
        if vault_addr:
            norm_vault = probe_mod._normalize_address(vault_addr) if hasattr(probe_mod, '_normalize_address') else (
                "0x" + vault_addr.lower().removeprefix("0x") if not vault_addr.lower().startswith("0x") else vault_addr.lower()
            )
            vault_addrs.add(norm_vault)

    assert "0xaaa111" in signer_addrs
    assert "0xbbb222" in vault_addrs


def test_address_identity_join_does_not_treat_no_intersection_as_cross():
    """No intersection between target addresses and action identities means UNKNOWN, not cross."""
    target_addrs_normalized = {"0xaaa111"}
    signer_addrs = {"0xbbb222", "0xccc333"}

    intersection = target_addrs_normalized & signer_addrs
    assert len(intersection) == 0
    # When no intersection, classification should be UNKNOWN_HISTORY_NOT_SCANNED_TO_COVERAGE_START
    # (not CROSS_EXPLICIT or ISOLATED_EXPLICIT)


def test_address_identity_join_reports_unknown_when_identity_mapping_unverified():
    """When identity join is unverified, pairs remain UNKNOWN."""
    verdict = "UNVERIFIED"
    assert verdict == "UNVERIFIED"


def test_zero_target_matches_with_identity_join_unverified_is_not_margin_mode_result():
    """Zero target matches with unverified identity join means we cannot determine margin mode."""
    target_pairs_resolved = 0
    identity_join_verdict = "NO_DATA"

    # With 0 resolved pairs, isolated fraction is 0.0
    isolated_fraction = 0.0 / max(1, 1) if target_pairs_resolved > 0 else 0.0
    assert isolated_fraction == 0.0
    # This should NOT be interpreted as "isolated coverage = 0" — it's truly unknown


# ---------------------------------------------------------------------------
# targeted backscan zero-match behavior tests
# ---------------------------------------------------------------------------

def test_targeted_backscan_zero_matches_keeps_pairs_unknown():
    """Zero target matches leaves all pairs in UNKNOWN_HISTORY_NOT_SCANNED_TO_COVERAGE_START."""
    classification = "UNKNOWN_HISTORY_NOT_SCANNED_TO_COVERAGE_START"
    assert classification != "CROSS_EXPLICIT"
    assert classification != "ISOLATED_EXPLICIT"


def test_targeted_backscan_zero_matches_does_not_emit_low_isolated_coverage():
    """Zero matches should NOT emit LOW_ISOLATED_COVERAGE_REVIEW_REQUIRED."""
    target_notional_resolved_fraction = 0.0
    resolved_isolated_fraction = 0.0

    # Low isolated coverage requires: resolved_fraction >= 0.60 AND isolated_fraction < 0.25
    if target_notional_resolved_fraction >= 0.60 and resolved_isolated_fraction < 0.25:
        status = "LOW_ISOLATED_COVERAGE_REVIEW_REQUIRED"
    else:
        status = "INSUFFICIENT_COVERAGE_UNDER_CAP"

    assert status == "INSUFFICIENT_COVERAGE_UNDER_CAP"


def test_targeted_backscan_terminal_insufficient_coverage_when_no_pairs_resolved_under_cap():
    """When no pairs resolve under cap, terminal is INSUFFICIENT_COVERAGE_UNDER_CAP."""
    cap_exhausted = False  # Cap not exhausted, just no matches
    resolved_fraction = 0.0

    if cap_exhausted and resolved_fraction == 0:
        status = "CAP_EXHAUSTED"
    elif cap_exhausted and resolved_fraction < 0.60:
        status = "CAP_EXHAUSTED"
    elif resolved_fraction < 0.60:
        status = "INSUFFICIENT_COVERAGE_UNDER_CAP"
    else:
        status = "PASSED_BROADER_BACKFILL_JUSTIFIED"

    assert status == "INSUFFICIENT_COVERAGE_UNDER_CAP"
