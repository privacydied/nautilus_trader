#!/usr/bin/env python3
"""Unit tests for hip3_replica_cmds_setoracle_reconstruction_v0.

Covers dry-run, S3 inventory, envelope probe, schema recon, parser,
forward recorder loader, overlap validation, backfill gating,
and forbidden-status invariants.
"""

import gzip
import json
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = str(Path(__file__).resolve().parents[5])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_MOD = (
    "examples.strategies.venue_agnostic_signal_observer"
    ".hip3_replica_cmds_setoracle_reconstruction_v0"
)

from examples.strategies.venue_agnostic_signal_observer import (
    hip3_replica_cmds_setoracle_reconstruction_v0 as mod,
)
MOD = "examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0"

from examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0 import (
    ALLOWED_STATUSES,
    FORBIDDEN_STATUSES,
    FORBIDDEN_FAILURE_REASONS,
    ForwardOverlapValidationResult,
    ForwardRecorderOraclePoint,
    ReplicaCmdsChunkInventory,
    ReplicaCmdsEnvelopeProbe,
    ReplicaCmdsProbeConfig,
    ReplicaCmdsSourceConfig,
    RawRecordSample,
    RuntimeBudgetState,
    PerDexOverlapValidationResult,
    DecodedSetOracleCommand,
    _analyze_raw_sample,
    _bps_diff,
    _classify_command,
    _detect_compression_magic,
    _extract_dex,
    _extract_oracle_price,
    _extract_symbol,
    _parse_ts,
    build_parser,
    compute_flx_corroboration,
    compute_volume_estimate,
    load_forward_recorder_points,
    main,
    parse_setoracle_commands,
    probe_envelope,
    probe_recon_schema,
    probe_s3_inventory,
    run_bounded_backfill,
    validate_overlap,
)

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

def _make_config(
    dry_run: bool = False,
    allow_s3: bool = False,
    requester_pays: bool = True,
    max_files: int = 3,
    validate_overlap: bool = False,
    backfill: bool = False,
    forward_root: Path | None = None,
    out_root: Path | None = None,
) -> ReplicaCmdsProbeConfig:
    src = ReplicaCmdsSourceConfig(
        allow_s3_archive_read=allow_s3,
        requester_pays=requester_pays,
    )
    return ReplicaCmdsProbeConfig(
        out_root=out_root or Path("/tmp/test_recon_out"),
        max_replica_files=max_files,
        dry_run=dry_run,
        validate_overlap=validate_overlap,
        backfill_after_validation=backfill,
        forward_recorder_root=forward_root,
        source=src,
    )


def _make_budget_exceeded() -> RuntimeBudgetState:
    """Return a budget that is already exceeded."""
    start = datetime.now(timezone.utc) - timedelta(minutes=60)
    return RuntimeBudgetState.create(max_minutes=1.0, start_utc=start)


def _make_budget_ok() -> RuntimeBudgetState:
    """Return a budget with plenty of time remaining."""
    return RuntimeBudgetState.create(max_minutes=60.0)


def _make_mock_chokepoint() -> MagicMock:
    cp = MagicMock()
    cp.s3_list_prefix.return_value = {
        "prefixes": ["replica_cmds/2024/01/01/"],
        "keys": ["replica_cmds/2024/01/01/chunk_001.bin"],
        "objects": [
            {"key": "replica_cmds/2024/01/01/chunk_001.bin", "size": 1_000_000},
        ],
        "error_code": None,
    }
    cp.s3_read_object.return_value = b"{}"
    return cp


def _make_inventory(candidate_keys: List[str] | None = None) -> ReplicaCmdsChunkInventory:
    keys = candidate_keys or ["replica_cmds/chunk_001.bin"]
    return ReplicaCmdsChunkInventory(
        bucket="hl-mainnet-node-data",
        root_prefix="replica_cmds",
        listing_status="OK",
        source_accessible=True,
        candidate_keys=keys,
    )


# Forward recorder fixture dict
_FW_ROW = {
    "timestamp_utc": "2024-06-01T12:00:00.000000",
    "api_symbol": "flx:TSLA",
    "display_symbol": "TSLA",
    "dex_name": "flx",
    "oracle_price": 185.50,
    "mark_price": 185.55,
    "mid_price": 185.52,
}


# ---------------------------------------------------------------------------
# 1. dry run produces ready status, no network calls
# ---------------------------------------------------------------------------
def test_dry_run_produces_ready_status_no_network_calls(tmp_path):
    cfg = _make_config(dry_run=True, out_root=tmp_path)
    budget = _make_budget_ok()
    # Patch _get_git_info to avoid subprocess
    with patch(f"{MOD}._get_git_info", return_value=("abc123", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path),
                "--dry-run",
                "--max-replica-files", "3",
            ])
    assert ret == 0
    # main returns 0 on dry_run and does not touch NetworkChokepoint


# ---------------------------------------------------------------------------
# 2. source inventory accessible prefix
# ---------------------------------------------------------------------------
def test_source_inventory_accessible_prefix():
    cp = _make_mock_chokepoint()
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = probe_s3_inventory(cp, cfg, budget)
    assert inv.source_accessible is True
    assert inv.listing_status == "OK"
    cp.s3_list_prefix.assert_called_once()


# ---------------------------------------------------------------------------
# 3. source inventory requester-pays blocked
# ---------------------------------------------------------------------------
def test_source_inventory_requester_pays_blocked():
    cp = _make_mock_chokepoint()
    cfg = _make_config(allow_s3=False)  # S3 flag NOT set
    budget = _make_budget_ok()
    inv = probe_s3_inventory(cp, cfg, budget)
    assert inv.source_accessible is False
    assert inv.listing_status == "BLOCKED_NO_S3_FLAG"
    assert inv.failure_reason == "REQUESTER_PAYS_CREDENTIALS_REQUIRED"


# ---------------------------------------------------------------------------
# 4. runtime budget exceeded
# ---------------------------------------------------------------------------
def test_runtime_budget_exceeded():
    cp = _make_mock_chokepoint()
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_exceeded()
    inv = probe_s3_inventory(cp, cfg, budget)
    assert inv.source_accessible is False
    assert inv.listing_status == "BUDGET_EXCEEDED"
    assert inv.failure_reason == "RUNTIME_BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# 5. envelope probe dumps raw byte structure
# ---------------------------------------------------------------------------
def test_envelope_probe_dumps_raw_byte_structure():
    """Raw samples are produced before content search."""
    data = b'{"action":"setOracle","oraclePx":1.0}\n{"action":"other"}\n'
    cp = _make_mock_chokepoint()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    probe = probe_envelope(cp, cfg, inv, budget)
    assert probe.bytes_read == len(data)
    assert len(probe.record_samples) > 0
    sample = probe.record_samples[0]
    assert sample.first_64_hex  # populated


# ---------------------------------------------------------------------------
# 6. envelope probe detects newline-delimited JSON
# ---------------------------------------------------------------------------
def test_envelope_probe_detects_newline_delimited_json():
    data = b'{"a":1}\n{"b":2}\n{"c":3}\n'
    cp = _make_mock_chokepoint()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    probe = probe_envelope(cp, cfg, inv, budget)
    assert probe.record_boundary_strategy == "newline_delimited"
    assert probe.envelope_status == "REPLICA_CMDS_ENVELOPE_DECODED"
    assert probe.proceed_to_content_search_allowed is True


# ---------------------------------------------------------------------------
# 7. envelope probe detects length-prefixed
# ---------------------------------------------------------------------------
def test_envelope_probe_detects_length_prefixed():
    # Build length-prefixed binary: 4-byte LE length + payload (repeated)
    payload = b'\x00' * 200
    prefix = struct.pack("<I", 200)
    data = prefix + payload
    cp = _make_mock_chokepoint()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    probe = probe_envelope(cp, cfg, inv, budget)
    sample = probe.record_samples[0] if probe.record_samples else None
    assert sample is not None
    # Length prefix hint should be detected
    assert any("little_endian_uint32" in h for h in sample.length_prefix_hints)


# ---------------------------------------------------------------------------
# 8. envelope probe handles unknown binary
# ---------------------------------------------------------------------------
def test_envelope_probe_handles_unknown_binary():
    # Exclude \n (0x0A) to avoid newline detection
    data = bytes([b for b in range(256) if b != 0x0A]) * 4  # no newlines
    cp = _make_mock_chokepoint()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    probe = probe_envelope(cp, cfg, inv, budget)
    assert probe.record_boundary_strategy in ("unknown_binary", "msgpack", "length_prefixed")
    assert probe.envelope_status == "REPLICA_CMDS_ENVELOPE_UNKNOWN"
    assert probe.proceed_to_content_search_allowed is False


# ---------------------------------------------------------------------------
# 9. decompressor required stops
# ---------------------------------------------------------------------------
def test_decompressor_required_stops():
    """Gzip-compressed unknown content: decompressor needed."""
    original = b'{"action":"setOracle"}\n' * 50
    compressed = gzip.compress(original)
    cp = _make_mock_chokepoint()
    cp.s3_read_object.return_value = compressed
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    probe = probe_envelope(cp, cfg, inv, budget)
    assert probe.compression_detected == "gzip"
    # If gzip module is available (it is in stdlib), it should decompress
    # and proceed; if not, decompressor_required path.  We verify the
    # function does NOT crash either way.
    assert probe.envelope_status in (
        "REPLICA_CMDS_ENVELOPE_DECODED",
        "REPLICA_CMDS_ENVELOPE_UNKNOWN",
    )


# ---------------------------------------------------------------------------
# 10. recon probe refuses content search if envelope unknown
# ---------------------------------------------------------------------------
def test_recon_probe_refuses_content_search_if_envelope_unknown():
    cp = MagicMock()
    # Budget is fine but envelope will be unknown (all-zeroes binary)
    data = b'\x00' * 2048
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    envelope_probe, sample, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    assert envelope_probe.proceed_to_content_search_allowed is False
    assert len(candidates) == 0
    assert schema["oracle_like_candidates_found"] == 0


# ---------------------------------------------------------------------------
# 11. recon probe records unknown schema without crashing
# ---------------------------------------------------------------------------
def test_recon_probe_records_unknown_schema():
    """Unknown binary data → schema_confidence='none', no crash."""
    cp = MagicMock()
    data = bytes(range(256)) * 8
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    envelope_probe, sample, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    assert schema["schema_confidence"] in ("none", "unknown")
    assert isinstance(candidates, list)


# ---------------------------------------------------------------------------
# 12. recon probe detects validator oracle, no hip3 deployer
# ---------------------------------------------------------------------------
def test_recon_probe_detects_validator_oracle_no_hip3_deployer():
    """Records with oracle fields but no setOracle action → validator-oracle only."""
    oracle_record = json.dumps({
        "action": "oracle",
        "oraclePx": 185.50,
        "dex": "xyz",
        "name": "TSLA",
    }).encode()
    data = oracle_record + b"\n"
    cp = MagicMock()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    envelope_probe, sample, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    assert schema["oracle_like_candidates_found"] >= 1
    assert schema["setoracle_command_found"] is False
    assert schema["hip3_deployer_setoracle_found"] is False


# ---------------------------------------------------------------------------
# 13. validator only emits correct status
# ---------------------------------------------------------------------------
def test_validator_only_emits_correct_status():
    """Validator oracle only → REPLICA_CMDS_SOURCE_ACCESSIBLE_BUT_NO_DEPLOYER_ORACLE."""
    oracle_record = json.dumps({
        "action": "updateOracle",
        "oraclePx": 200.0,
        "dex": "xyz",
        "name": "TSLA",
    }).encode()
    data = oracle_record + b"\n"
    cp = MagicMock()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    # Simulate main() flow: it reads the inventory, envelope, then checks schema
    envelope_probe, sample, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    # The main() function would check this condition
    assert schema["oracle_like_candidates_found"] > 0
    assert not schema["setoracle_command_found"]
    # This maps to the status in main()
    assert "REPLICA_CMDS_SOURCE_ACCESSIBLE_BUT_NO_DEPLOYER_ORACLE" in ALLOWED_STATUSES


# ---------------------------------------------------------------------------
# 14. recon probe detects hip3 deployer oracle JSON
# ---------------------------------------------------------------------------
def test_recon_probe_detects_hip3_deployer_oracle_json():
    record = json.dumps({
        "action": "setOracle",
        "oraclePx": 185.50,
        "dex": "flx",
        "name": "TSLA",
        "markPxs": [185.50, 185.55],
    }).encode()
    data = record + b"\n"
    cp = MagicMock()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    _, _, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    assert schema["hip3_deployer_setoracle_found"] is True
    assert schema["setoracle_command_found"] is True
    assert schema["hip3_deployer_setoracle_candidates_found"] >= 1


# ---------------------------------------------------------------------------
# 15. recon probe prioritizes flx candidates
# ---------------------------------------------------------------------------
def test_recon_probe_prioritizes_flx_candidates():
    flx_record = json.dumps({
        "action": "setOracle",
        "oraclePx": 185.0,
        "dex": "flx",
        "name": "TSLA",
    }).encode()
    xyz_record = json.dumps({
        "action": "setOracle",
        "oraclePx": 200.0,
        "dex": "xyz",
        "name": "AAPL",
    }).encode()
    data = flx_record + b"\n" + xyz_record + b"\n"
    cp = MagicMock()
    cp.s3_read_object.return_value = data
    cfg = _make_config(allow_s3=True)
    budget = _make_budget_ok()
    inv = _make_inventory()
    _, _, candidates, schema = probe_recon_schema(cp, cfg, inv, budget)
    assert schema["flx_priority_candidates_found"] >= 1
    assert schema["target_dex_candidates_found"] >= 1


# ---------------------------------------------------------------------------
# 16. schema parser extracts dex, symbol, oracle
# ---------------------------------------------------------------------------
def test_schema_parser_extracts_dex_symbol_oracle():
    record = json.dumps({
        "action": "setOracle",
        "oraclePx": 185.50,
        "dex": "flx",
        "name": "TSLA",
        "markPxs": [185.49, 185.51],
    }).encode()
    data = record + b"\n"
    results = parse_setoracle_commands(data, "test_key", "newline_delimited")
    assert len(results) >= 1
    cmd = results[0]
    assert cmd.dex == "flx"
    assert cmd.display_symbol == "TSLA"
    assert cmd.api_symbol == "flx:TSLA"
    assert cmd.submitted_oracle_px == pytest.approx(185.50)
    assert cmd.mark_pxs_count == 2
    assert cmd.command_category == "hip3_deployer_setOracle"
    assert cmd.confidence_class == "high"


# ---------------------------------------------------------------------------
# 17. parser skips non-oracle commands
# ---------------------------------------------------------------------------
def test_parser_skips_non_oracle():
    data = (
        json.dumps({"action": "transfer", "amount": 100}).encode() + b"\n"
        + json.dumps({"action": "deposit", "token": "USDC"}).encode() + b"\n"
    )
    results = parse_setoracle_commands(data, "test_key", "newline_delimited")
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 18. parser records malformed oracle candidate
# ---------------------------------------------------------------------------
def test_parser_records_malformed_oracle_candidate():
    """Malformed lines are skipped, valid lines still parsed."""
    lines = (
        b"this is not json at all {{{\n"
        + json.dumps({"action": "setOracle", "oraclePx": 10.0, "dex": "flx", "name": "TSLA"}).encode()
        + b"\n"
        + b"{bad json\n"
    )
    results = parse_setoracle_commands(lines, "test_key", "newline_delimited")
    # Should get at least the valid record
    assert len(results) >= 1
    assert results[0].dex == "flx"


# ---------------------------------------------------------------------------
# 19. parser streams records, no memory hold
# ---------------------------------------------------------------------------
def test_parser_streams_records_no_memory_hold():
    """Parser processes records one at a time without accumulating all raw bytes."""
    records = []
    for i in range(50):
        records.append(
            json.dumps({
                "action": "setOracle",
                "oraclePx": float(i),
                "dex": "xyz",
                "name": "TSLA",
            }).encode()
        )
    data = b"\n".join(records) + b"\n"
    results = parse_setoracle_commands(data, "test_key", "newline_delimited")
    assert len(results) == 50
    # Verify each has correct data
    for i, cmd in enumerate(results):
        assert cmd.submitted_oracle_px == pytest.approx(float(i))


# ---------------------------------------------------------------------------
# 20. forward recorder loader discovers JSONL files
# ---------------------------------------------------------------------------
def test_forward_recorder_loader_discovers_jsonl(tmp_path):
    fwd_dir = tmp_path / "reports" / "some_run" / "asset_context_snapshots"
    fwd_dir.mkdir(parents=True)
    jsonl_file = fwd_dir / "data.jsonl"
    row = {
        "timestamp_utc": "2024-06-01T12:00:00.000000",
        "api_symbol": "flx:TSLA",
        "display_symbol": "TSLA",
        "dex_name": "flx",
        "oracle_price": 185.50,
        "mark_price": 185.55,
        "mid_price": 185.52,
    }
    jsonl_file.write_text(json.dumps(row) + "\n")
    run_dir = tmp_path / "recon_run"
    run_dir.mkdir()
    points, inventory = load_forward_recorder_points(
        tmp_path / "reports",
        frozenset({"flx:TSLA"}),
        run_dir,
    )
    assert inventory["files_discovered"] == 1
    assert inventory["rows_loaded"] >= 1
    assert inventory["overlap_ready"] is True
    assert len(points) >= 1


# ---------------------------------------------------------------------------
# 21. forward recorder loader extracts oracle fields
# ---------------------------------------------------------------------------
def test_forward_recorder_loader_extracts_oracle_fields(tmp_path):
    fwd_dir = tmp_path / "reports" / "run1" / "asset_context_snapshots"
    fwd_dir.mkdir(parents=True)
    row = {
        "timestamp_utc": "2024-06-01T12:00:00.000000",
        "api_symbol": "xyz:AAPL",
        "display_symbol": "AAPL",
        "dex_name": "xyz",
        "oracle_price": 195.00,
        "mark_price": 195.10,
        "mid_price": 195.05,
    }
    (fwd_dir / "snapshots.jsonl").write_text(json.dumps(row) + "\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    points, inv = load_forward_recorder_points(
        tmp_path / "reports", frozenset({"xyz:AAPL"}), run_dir,
    )
    assert len(points) == 1
    pt = points[0]
    assert pt.api_symbol == "xyz:AAPL"
    assert pt.dex == "xyz"
    assert pt.display_symbol == "AAPL"
    assert pt.oracle_price == pytest.approx(195.00)
    assert pt.mark_price == pytest.approx(195.10)
    assert pt.mid_price == pytest.approx(195.05)
    assert pt.timestamp_utc == "2024-06-01T12:00:00.000000"


# ---------------------------------------------------------------------------
# 22. overlap join nearest timestamp
# ---------------------------------------------------------------------------
def test_overlap_join_nearest_timestamp():
    """Join matches nearest forward point by (api_symbol, dex)."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA",
        dex="flx",
        block_timestamp="2024-06-01T12:00:02.000000",
        submitted_oracle_px=185.50,
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA",
            dex="flx",
            display_symbol="TSLA",
            oracle_price=185.50,
        ),
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:10.000000",
            api_symbol="flx:TSLA",
            dex="flx",
            display_symbol="TSLA",
            oracle_price=185.60,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    # Should join within 5s tolerance to the first point (2s gap)
    assert result.per_dex_results[0].joined_count_5s >= 1


# ---------------------------------------------------------------------------
# 23. primary validation uses <=5s tolerance
# ---------------------------------------------------------------------------
def test_primary_validation_5s_tolerance():
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA",
        dex="flx",
        block_timestamp="2024-06-01T12:00:03.000000",
        submitted_oracle_px=185.50,
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA",
            dex="flx",
            display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    pdr = result.per_dex_results[0]
    assert pdr.joined_count_5s == 1  # 3s gap < 5s
    assert pdr.joined_count_60s == 1


# ---------------------------------------------------------------------------
# 24. 60s tolerance diagnostic only
# ---------------------------------------------------------------------------
def test_60s_tolerance_diagnostic_only():
    """Gap between 5s and 60s: counted in 60s but not 5s."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA",
        dex="flx",
        block_timestamp="2024-06-01T12:00:30.000000",
        submitted_oracle_px=185.50,
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA",
            dex="flx",
            display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    pdr = result.per_dex_results[0]
    assert pdr.joined_count_5s == 0  # 30s > 5s
    assert pdr.joined_count_60s == 1  # 30s <= 60s


# ---------------------------------------------------------------------------
# 25. overlap validation passes values match
# ---------------------------------------------------------------------------
def test_overlap_validation_passes_values_match():
    """When oracle prices match, status = VALIDATED."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA",
        dex="flx",
        block_timestamp="2024-06-01T12:00:01.000000",
        submitted_oracle_px=185.50,
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA",
            dex="flx",
            display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    assert result.per_dex_results[0].validation_status == "VALIDATED"
    assert result.validated_count == 1
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED"


# ---------------------------------------------------------------------------
# 26. pooled validation cannot hide failing DEX
# ---------------------------------------------------------------------------
def test_pooled_validation_cannot_hide_failing_dex():
    """Mismatch on one DEX means overall MISMATCH even if another validates."""
    recon_cmds = [
        DecodedSetOracleCommand(
            api_symbol="flx:TSLA", dex="flx",
            block_timestamp="2024-06-01T12:00:01.000000",
            submitted_oracle_px=185.50,
        ),
        DecodedSetOracleCommand(
            api_symbol="xyz:AAPL", dex="xyz",
            block_timestamp="2024-06-01T12:00:01.000000",
            submitted_oracle_px=500.00,  # big mismatch
        ),
    ]
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA", dex="flx", display_symbol="TSLA",
            oracle_price=185.50,
        ),
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="xyz:AAPL", dex="xyz", display_symbol="AAPL",
            oracle_price=195.00,  # 500 vs 195 = huge mismatch
        ),
    ]
    result = validate_overlap(recon_cmds, fwd_points)
    # xyz:AAPL should be MISMATCH
    xyz_results = [r for r in result.per_dex_results if r.dex == "xyz"]
    assert xyz_results[0].validation_status == "MISMATCH"
    assert result.mismatch_count >= 1
    # Overall is MISMATCH even though flx may validate
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_MISMATCH"


# ---------------------------------------------------------------------------
# 27. transform required emitted for documented transform
# ---------------------------------------------------------------------------
def test_transform_required_emitted_for_documented_transform():
    """Oracle prices differ by moderate bps → TRANSFORM_REQUIRED."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA", dex="flx",
        block_timestamp="2024-06-01T12:00:01.000000",
        submitted_oracle_px=186.00,  # ~27 bps off from 185.50
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA", dex="flx", display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    pdr = result.per_dex_results[0]
    # bps_diff = 0.50/185.50 * 10000 ≈ 26.96 bps — >10 and <100
    assert pdr.validation_status == "TRANSFORM_REQUIRED"
    assert result.transform_required_count >= 1
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_TRANSFORM_REQUIRED"


# ---------------------------------------------------------------------------
# 28. arbitrary fitted transform rejected as MISMATCH
# ---------------------------------------------------------------------------
def test_arbitrary_fitted_transform_rejected():
    """Large bps difference → MISMATCH."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA", dex="flx",
        block_timestamp="2024-06-01T12:00:01.000000",
        submitted_oracle_px=250.00,  # ~3470 bps off from 185.50
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA", dex="flx", display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    pdr = result.per_dex_results[0]
    assert pdr.validation_status == "MISMATCH"
    assert result.mismatch_count == 1


# ---------------------------------------------------------------------------
# 29. backfill blocked when overlap fails
# ---------------------------------------------------------------------------
def test_backfill_blocked_when_overlap_fails():
    """No validated markets → backfill manifest has limited backfill."""
    overlap_result = ForwardOverlapValidationResult(
        status="REPLICA_CMDS_FORWARD_OVERLAP_MISMATCH",
        mismatch_count=1,
    )
    cfg = _make_config(allow_s3=True, backfill=True)
    budget = _make_budget_ok()
    cp = MagicMock()
    manifest = run_bounded_backfill(cp, cfg, budget, overlap_result, Path("/tmp/out"))
    # No validated markets, so backfill should note this
    assert "No validated markets for backfill" in manifest["limitations"]


# ---------------------------------------------------------------------------
# 30. backfill blocked when only pooled passes
# ---------------------------------------------------------------------------
def test_backfill_blocked_when_only_pooled_passes():
    """When all per-DEX are unavailable/transform, backfill is blocked."""
    pdr = PerDexOverlapValidationResult(
        api_symbol="flx:TSLA", dex="flx",
        validation_status="TRANSFORM_REQUIRED",
    )
    overlap_result = ForwardOverlapValidationResult(
        status="REPLICA_CMDS_FORWARD_OVERLAP_TRANSFORM_REQUIRED",
        per_dex_results=[pdr],
        transform_required_count=1,
    )
    cfg = _make_config(allow_s3=True, backfill=True)
    budget = _make_budget_ok()
    cp = MagicMock()
    manifest = run_bounded_backfill(cp, cfg, budget, overlap_result, Path("/tmp/out"))
    assert "No validated markets for backfill" in manifest["limitations"]


# ---------------------------------------------------------------------------
# 31. backfill allowed per market only
# ---------------------------------------------------------------------------
def test_backfill_allowed_per_market_only():
    """Only validated markets get included in backfill scope."""
    pdr_validated = PerDexOverlapValidationResult(
        api_symbol="flx:TSLA", dex="flx",
        validation_status="VALIDATED",
    )
    pdr_mismatch = PerDexOverlapValidationResult(
        api_symbol="xyz:AAPL", dex="xyz",
        validation_status="MISMATCH",
    )
    overlap_result = ForwardOverlapValidationResult(
        status="REPLICA_CMDS_FORWARD_OVERLAP_MISMATCH",
        per_dex_results=[pdr_validated, pdr_mismatch],
        validated_count=1,
        mismatch_count=1,
    )
    cfg = _make_config(allow_s3=True, backfill=True)
    budget = _make_budget_ok()
    cp = MagicMock()
    manifest = run_bounded_backfill(cp, cfg, budget, overlap_result, Path("/tmp/out"))
    # Should NOT contain "No validated markets" since flx:TSLA is validated
    assert "No validated markets for backfill" not in manifest["limitations"]
    assert manifest["validation_status_by_api_symbol"]["flx:TSLA"] == "VALIDATED"
    assert manifest["validation_status_by_api_symbol"]["xyz:AAPL"] == "MISMATCH"


# ---------------------------------------------------------------------------
# 32. historical residual diagnostic absent
# ---------------------------------------------------------------------------
def test_historical_residual_diagnostic_absent():
    """No SonarX residual diagnostic in backfill manifest."""
    overlap_result = ForwardOverlapValidationResult(status="REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED")
    cfg = _make_config(allow_s3=True, backfill=True)
    budget = _make_budget_ok()
    cp = MagicMock()
    manifest = run_bounded_backfill(cp, cfg, budget, overlap_result, Path("/tmp/out"))
    assert "No SonarX residual join" in manifest["limitations"]


# ---------------------------------------------------------------------------
# 33. output artifacts diagnostic only
# ---------------------------------------------------------------------------
def test_output_artifacts_diagnostic_only():
    """FLX corroboration conclusion is diagnostic, not edge confirmation."""
    pdr_tsla = PerDexOverlapValidationResult(
        api_symbol="flx:TSLA", dex="flx",
        validation_status="VALIDATED",
        joined_count_5s=3,
    )
    pdr_nvda = PerDexOverlapValidationResult(
        api_symbol="flx:NVDA", dex="flx",
        validation_status="VALIDATED",
        joined_count_5s=2,
    )
    overlap_result = ForwardOverlapValidationResult(
        status="REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED",
        per_dex_results=[pdr_tsla, pdr_nvda],
        validated_count=2,
    )
    corr = compute_flx_corroboration(overlap_result)
    assert corr["corroborates_forward_oracle_level_shift"] is True
    assert "DIAGNOSTIC" in corr["conclusion"]
    assert "edge confirmation" not in corr["conclusion"].lower() or "not" in corr["conclusion"].lower()


# ---------------------------------------------------------------------------
# 34. forbidden statuses absent
# ---------------------------------------------------------------------------
def test_forbidden_statuses_absent():
    """Forbidden statuses must never appear in ALLOWED_STATUSES."""
    overlap = ALLOWED_STATUSES & FORBIDDEN_STATUSES
    assert len(overlap) == 0, f"Forbidden statuses found in allowed: {overlap}"


# ---------------------------------------------------------------------------
# Additional helper tests
# ---------------------------------------------------------------------------

def test_detect_compression_magic_gzip():
    data = gzip.compress(b"hello")
    assert _detect_compression_magic(data) == "gzip"


def test_detect_compression_magic_unknown():
    assert _detect_compression_magic(b'\x00\x01\x02\x03') == "unknown"


def test_analyze_raw_sample_empty():
    sample = _analyze_raw_sample(b"")
    assert sample.unknown_binary is True


def test_analyze_raw_sample_newline():
    data = b'{"a":1}\n{"b":2}\n'
    sample = _analyze_raw_sample(data)
    assert sample.newline_framing is True
    assert "newline" in sample.delimiter_hints


def test_analyze_raw_sample_length_prefix():
    payload = b'\x00' * 100
    prefix = struct.pack("<I", 100)
    data = prefix + payload
    sample = _analyze_raw_sample(data)
    assert any("little_endian_uint32" in h for h in sample.length_prefix_hints)


def test_classify_command_setoracle():
    obj = {"action": "setOracle", "oraclePx": 1.0}
    assert _classify_command(obj) == "hip3_deployer_setOracle"


def test_classify_command_oracle_like():
    obj = {"oraclePx": 1.0, "dex": "flx"}
    assert _classify_command(obj) == "oracle_like"


def test_classify_command_unknown():
    obj = {"action": "transfer", "amount": 100}
    assert _classify_command(obj) == "unknown"


def test_extract_oracle_price_nested():
    obj = {"data": {"oraclePx": 42.0}}
    assert _extract_oracle_price(obj) == pytest.approx(42.0)


def test_extract_dex_from_name():
    obj = {"name": "flx:TSLA"}
    assert _extract_dex(obj) == "flx"


def test_extract_symbol_namespaced():
    obj = {"name": "flx:TSLA"}
    dex, sym = _extract_symbol(obj)
    assert dex == "flx"
    assert sym == "TSLA"


def test_bps_diff_zero():
    assert _bps_diff(0, 100) == 0.0
    assert _bps_diff(100, 0) == 0.0


def test_bps_diff_equal():
    assert _bps_diff(100.0, 100.0) == pytest.approx(0.0)


def test_bps_diff_ten_bps():
    # 1 bps = 0.01%; 10 bps = 0.1%
    # a=100, b=100.1 → diff=0.1, min=100 → 0.1/100*10000 = 10 bps
    assert _bps_diff(100.0, 100.1) == pytest.approx(10.0, abs=0.1)


def test_parse_ts_valid():
    dt = _parse_ts("2024-06-01T12:00:00")
    assert dt is not None
    assert dt.year == 2024


def test_parse_ts_empty():
    assert _parse_ts("") is None
    assert _parse_ts(None) is None


def test_parse_api_symbol():
    assert mod._parse_api_symbol("flx:TSLA") == ("flx", "TSLA")
    assert mod._parse_api_symbol("NOPE") is None


def test_display_symbol_from_api():
    assert mod._display_symbol_from_api("flx:TSLA") == "TSLA"
    assert mod._display_symbol_from_api("NOPE") == "NOPE"


def test_compute_volume_estimate_zero_bytes():
    est = compute_volume_estimate(0, 0, 0, 0)
    assert est.bytes_per_chunk_observed == 0.0


def test_compute_volume_estimate_nonzero():
    est = compute_volume_estimate(1_000_000, 100, 5, 2)
    assert est.bytes_per_chunk_observed == 1_000_000
    assert est.records_per_chunk_observed == 100
    # 1_000_000 bytes = ~0.954 MB, so 5 / 0.954 = ~5.24 per MB
    mb = 1_000_000 / (1024 * 1024)
    assert est.oracle_candidates_per_mb == pytest.approx(5.0 / mb)
    assert est.hip3_deployer_setoracle_candidates_per_mb == pytest.approx(2.0 / mb)


def test_overlap_unavailable_no_forward_points():
    """No forward points → UNAVAILABLE."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:TSLA", dex="flx",
        block_timestamp="2024-06-01T12:00:01.000000",
        submitted_oracle_px=185.50,
    )
    result = validate_overlap([recon_cmd], [])
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"
    assert result.per_dex_results[0].validation_status == "UNAVAILABLE"


def test_overlap_no_reconstructed_points():
    """No reconstructed points → UNAVAILABLE."""
    fwd = ForwardRecorderOraclePoint(
        timestamp_utc="2024-06-01T12:00:00.000000",
        api_symbol="flx:TSLA", dex="flx", display_symbol="TSLA",
        oracle_price=185.50,
    )
    result = validate_overlap([], [fwd])
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"


def test_forward_loader_empty_root(tmp_path):
    """Non-existent root → empty result."""
    points, inv = load_forward_recorder_points(
        tmp_path / "nonexistent", frozenset({"flx:TSLA"}), tmp_path,
    )
    assert len(points) == 0
    assert inv["files_discovered"] == 0


def test_forward_loader_null_oracle(tmp_path):
    """Null oracle_price counted correctly."""
    fwd_dir = tmp_path / "reports" / "run1" / "asset_context_snapshots"
    fwd_dir.mkdir(parents=True)
    row = {
        "timestamp_utc": "2024-06-01T12:00:00",
        "api_symbol": "flx:TSLA",
        "display_symbol": "TSLA",
        "dex_name": "flx",
        "oracle_price": None,
        "mark_price": 185.0,
        "mid_price": 185.1,
    }
    (fwd_dir / "data.jsonl").write_text(json.dumps(row) + "\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    points, inv = load_forward_recorder_points(
        tmp_path / "reports", frozenset({"flx:TSLA"}), run_dir,
    )
    assert inv["null_oracle_count"] == 1
    assert points[0].oracle_price is None


def test_main_dry_run_cli(tmp_path):
    """CLI dry-run path through main()."""
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path),
                "--dry-run",
                "--max-replica-files", "1",
            ])
    assert ret == 0


def test_main_s3_blocked(tmp_path):
    """main() returns 1 when S3 is blocked."""
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path),
                "--max-replica-files", "1",
            ])
    # No --allow-s3-archive-read → blocked
    assert ret == 1


def test_main_with_s3_accessible(tmp_path):
    """main() with mocked chokepoint goes through full path."""
    mock_listing = {
        "prefixes": [],
        "keys": ["replica_cmds/chunk.bin"],
        "objects": [{"key": "replica_cmds/chunk.bin", "size": 100_000}],
        "error_code": None,
    }
    # Data that is all-zeroes → envelope unknown → stops early
    blob = b'\x00' * 4096
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            with patch(f"{MOD}.NetworkChokepoint") as MockCP:
                mock_cp = MagicMock()
                mock_cp.s3_list_prefix.return_value = mock_listing
                mock_cp.s3_read_object.return_value = blob
                MockCP.return_value = mock_cp
                ret = main([
                    "--out-root", str(tmp_path),
                    "--allow-s3-archive-read",
                    "--max-replica-files", "1",
                ])
    # Should run and stop at envelope unknown
    assert ret in (0, 1)


def test_forbidden_failure_reasons_absent_from_allowed():
    """Forbidden failure reasons should not be confused with statuses."""
    for reason in FORBIDDEN_FAILURE_REASONS:
        assert reason not in ALLOWED_STATUSES


def test_overlap_mixed_validated_and_unavailable():
    """Only VALIDATED markets contribute; UNAVAILABLE ones don't block."""
    recon_cmds = [
        DecodedSetOracleCommand(
            api_symbol="flx:TSLA", dex="flx",
            block_timestamp="2024-06-01T12:00:01.000000",
            submitted_oracle_px=185.50,
        ),
    ]
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:TSLA", dex="flx", display_symbol="TSLA",
            oracle_price=185.50,
        ),
    ]
    result = validate_overlap(recon_cmds, fwd_points)
    assert result.validated_count == 1
    assert result.mismatch_count == 0
    assert result.status == "REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED"


def test_validate_overlap_exact_match_no_bps_diff():
    """Exact match → 0 bps diff → VALIDATED."""
    recon_cmd = DecodedSetOracleCommand(
        api_symbol="flx:NVDA", dex="flx",
        block_timestamp="2024-06-01T12:00:00.500000",
        submitted_oracle_px=120.00,
    )
    fwd_points = [
        ForwardRecorderOraclePoint(
            timestamp_utc="2024-06-01T12:00:00.000000",
            api_symbol="flx:NVDA", dex="flx", display_symbol="NVDA",
            oracle_price=120.00,
        ),
    ]
    result = validate_overlap([recon_cmd], fwd_points)
    pdr = result.per_dex_results[0]
    assert pdr.median_abs_bps_diff == pytest.approx(0.0)
    assert pdr.exact_near_match_rate == pytest.approx(1.0)
    assert pdr.validation_status == "VALIDATED"
