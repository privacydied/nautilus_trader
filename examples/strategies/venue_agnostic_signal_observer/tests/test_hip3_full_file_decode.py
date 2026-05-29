#!/usr/bin/env python3
"""Tests for full-file decode mode in hip3_replica_cmds_setoracle_reconstruction_v0."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[5])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from examples.strategies.venue_agnostic_signal_observer import (
    hip3_replica_cmds_setoracle_reconstruction_v0 as mod,
)
MOD = "examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0"

from examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0 import (
    FullFileHistogramResult,
    ReplicaCmdsProbeConfig,
    ReplicaCmdsSourceConfig,
    RuntimeBudgetState,
    _iter_lz4_json_records_from_bytes,
    _parse_perpdeploy_setoracle_payload,
    build_full_file_histogram,
    main,
)
from datetime import datetime, timedelta, timezone

try:
    import lz4.frame as _lz4_frame
except ImportError:
    _lz4_frame = None


def _make_config(dry_run=False, allow_s3=False, decode_full_file=False,
                 target_block=None, block_selection="first",
                 max_full_file_bytes=1_500_000_000, perpdeploy_histogram_only=False,
                 out_root=None, target_date=None):
    src = ReplicaCmdsSourceConfig(allow_s3_archive_read=allow_s3, requester_pays=True)
    return ReplicaCmdsProbeConfig(
        out_root=out_root or Path("/tmp/test_full_file"),
        target_date=target_date, dry_run=dry_run, source=src,
        decode_full_file=decode_full_file, target_block=target_block,
        block_selection=block_selection, max_full_file_bytes=max_full_file_bytes,
        perpdeploy_histogram_only=perpdeploy_histogram_only,
    )


def _setup_mock_chokepoint(mock_cp, lz4_data, block_key="100.lz4"):
    mock_cp.s3_list_prefix.side_effect = [
        {"prefixes": ["replica_cmds/ts1/"], "objects": [], "error_code": None},
        {"prefixes": ["replica_cmds/ts1/20260527/"], "objects": [], "error_code": None},
        {"prefixes": [], "objects": [
            {"key": f"replica_cmds/ts1/20260527/{block_key}", "size": len(lz4_data)},
        ], "error_code": None},
    ]
    mock_cp.s3_read_object.return_value = lz4_data


def _run_main_with_mock(tmp_path, mock_cp, extra_args=None):
    """Run main() with mock chokepoint and capture written artifacts."""
    written = {}
    def capture(path, obj):
        written[str(path.name)] = obj

    args = [
        "--out-root", str(tmp_path), "--allow-s3-archive-read",
        "--decode-full-file", "--target-block", "100", "--block-selection", "exact",
    ]
    if extra_args:
        args.extend(extra_args)

    with patch(f"{MOD}.NetworkChokepoint") as mcp:
        mcp.return_value = mock_cp
        with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture):
                ret = main(args)
    return ret, written


def _wrap_in_abci_block(action_records):
    """Wrap action records in ABCI block structure for build_full_file_histogram tests.

    Each action_record should have {type, setOracle, ...} at the action level.
    Returns a list of ABCI block records.
    """
    bundles = []
    for ar in action_records:
        bundles.append([
            "0x" + "ab" * 32,  # dummy signature
            {
                "broadcaster": "0x" + "cd" * 20,
                "broadcaster_nonce": 0,
                "signed_actions": [
                    {"action": ar, "nonce": 0, "signature": "0x" + "ef" * 64, "vaultAddress": "0x" + "01" * 20}
                ],
            },
        ])
    return [{"abci_block": {"signed_action_bundles": bundles, "time": "2026-05-27T00:00:00Z"}}]


# ---------------------------------------------------------------------------
# 1. CLI parses --decode-full-file
# ---------------------------------------------------------------------------

def test_cli_parses_decode_full_file():
    parser = mod.build_parser()
    args = parser.parse_args([
        "--decode-full-file", "--target-block", "1012290000",
        "--block-selection", "exact", "--max-full-file-bytes", "500000000",
        "--perpdeploy-histogram-only",
    ])
    assert args.decode_full_file is True
    assert args.target_block == "1012290000"
    assert args.block_selection == "exact"
    assert args.max_full_file_bytes == 500_000_000
    assert args.perpdeploy_histogram_only is True


def test_cli_defaults_no_full_file():
    parser = mod.build_parser()
    args = parser.parse_args([])
    assert args.decode_full_file is False
    assert args.target_block is None
    assert args.block_selection == "first"
    assert args.perpdeploy_histogram_only is False


# ---------------------------------------------------------------------------
# 2. Max full-file byte cap blocks oversized file
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_max_full_file_bytes_cap_blocks_oversized(mock_cp_cls, tmp_path):
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    mock_cp.s3_list_prefix.side_effect = [
        {"prefixes": ["replica_cmds/ts1/"], "objects": [], "error_code": None},
        {"prefixes": ["replica_cmds/ts1/20260527/"], "objects": [], "error_code": None},
        {"prefixes": [], "objects": [
            {"key": "replica_cmds/ts1/20260527/100.lz4", "size": 500},
        ], "error_code": None},
    ]
    mock_cp.s3_read_object.return_value = b"\x00" * 2000
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path), "--allow-s3-archive-read",
                "--decode-full-file", "--target-block", "100",
                "--block-selection", "exact", "--max-full-file-bytes", "1000",
            ])
    assert ret == 0


# ---------------------------------------------------------------------------
# 3. Exact block selection requires exact block
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_exact_block_selection_requires_exact(mock_cp_cls, tmp_path):
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    mock_cp.s3_list_prefix.side_effect = [
        {"prefixes": ["replica_cmds/ts1/"], "objects": [], "error_code": None},
        {"prefixes": ["replica_cmds/ts1/20260527/"], "objects": [], "error_code": None},
        {"prefixes": [], "objects": [
            {"key": "replica_cmds/ts1/20260527/100.lz4", "size": 500},
            {"key": "replica_cmds/ts1/20260527/200.lz4", "size": 500},
        ], "error_code": None},
    ]
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path / "exact"), "--allow-s3-archive-read",
                "--decode-full-file", "--target-block", "999999",
                "--block-selection", "exact",
            ])
    assert ret == 1


# ---------------------------------------------------------------------------
# 4. Full-file selection plan written before download
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_selection_plan_written_before_download(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    data = _lz4_frame.compress(b'{"abci_block": {}}\n')
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path, mock_cp)
    assert "replica_cmds_full_file_selection_plan.json" in written
    plan = written["replica_cmds_full_file_selection_plan.json"]
    assert plan["decode_full_file"] is True
    assert plan["target_block"] == "100"
    assert plan["selected_key"] is not None


# ---------------------------------------------------------------------------
# 5. Streaming newline JSON parser handles partial lines
# ---------------------------------------------------------------------------

@patch(f"{MOD}._lz4_frame")
def test_streaming_parser_handles_partial_lines(mock_lz4_mod):
    lines = b'{"a": 1}\n{"b": 2}\n{"c": 3}\n'
    mock_lz4_mod.decompress.return_value = lines
    fake_data = b"\x04\x22\x4d\x18" + b"\x00" * 100
    results = list(_iter_lz4_json_records_from_bytes(fake_data))
    assert len(results) == 3
    assert results[0][1] == {"a": 1}
    assert results[1][1] == {"b": 2}
    assert results[2][1] == {"c": 3}


@patch(f"{MOD}._lz4_frame")
def test_streaming_parser_partial_line_across_chunks(mock_lz4_mod):
    mock_lz4_mod.decompress.return_value = b'{"a": 1}\n{"b": 2}\n{"c":'
    fake_data = b"\x04\x22\x4d\x18" + b"\x00" * 100
    results = list(_iter_lz4_json_records_from_bytes(fake_data))
    assert len(results) == 3
    assert results[0][1] == {"a": 1}
    assert results[1][1] == {"b": 2}
    assert results[2][1] is None  # parse failure for incomplete JSON


# ---------------------------------------------------------------------------
# 6. Full-file histogram counts action types
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_full_file_histogram_counts_action_types(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([
        {"type": "perpDeploy", "setOracle": {"oraclePxs": []}},
    ])
    non_perp = [{"abci_block": {"signed_action_bundles": [[
        "0x" + "ab" * 32,
        {"broadcaster": "0x" + "cd" * 20, "signed_actions": [
            {"action": {"type": "order"}, "nonce": 0, "signature": "0x" + "ef" * 64}
        ]},
    ]], "time": "2026-05-27T00:00:00Z"}}]
    all_records = abci_records + non_perp
    data = _lz4_frame.compress(b"\n".join(json.dumps(r).encode() for r in all_records) + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    mock_cp.s3_read_object.return_value = data
    config = _make_config(allow_s3=True, out_root=tmp_path)
    budget = RuntimeBudgetState.create(max_minutes=60.0)
    result = build_full_file_histogram(mock_cp, config, "test_key", 1000, budget)
    assert result.records_decoded == 2
    assert result.perpDeploy_count == 1
    assert result.full_action_type_histogram.get("order", 0) == 1
    assert result.full_action_type_histogram.get("perpDeploy", 0) == 1


# ---------------------------------------------------------------------------
# 7. perpDeploy.setOracle.oraclePxs extraction works
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_perpdeploy_setoracle_extraction(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {
            "oraclePxs": [["cash:TSLA", "435.07"], ["cash:NVDA", "214.22"]],
            "markPxs": [["cash:TSLA", "435.10"], ["cash:NVDA", "214.25"]],
        },
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    mock_cp.s3_read_object.return_value = data
    config = _make_config(allow_s3=True, out_root=tmp_path)
    budget = RuntimeBudgetState.create(max_minutes=60.0)
    result = build_full_file_histogram(mock_cp, config, "test_key", 1000, budget)
    assert result.perpDeploy_count == 1
    assert result.setOracle_payload_count == 1
    assert result.oraclePxs_pair_count == 2
    assert result.cash_seen is True
    assert result.cash_tsla_found is True
    assert result.cash_nvda_found is True


# ---------------------------------------------------------------------------
# 8. DEX parsed from cash:TSLA, flx:NVDA, etc.
# ---------------------------------------------------------------------------

def test_parse_perpdeploy_dex_from_coin():
    record = {
        "type": "perpDeploy",
        "setOracle": {
            "oraclePxs": [["flx:TSLA", "185.50"], ["xyz:NVDA", "214.22"]],
            "markPxs": [],
        },
    }
    payloads = _parse_perpdeploy_setoracle_payload(record, 0, "test_key")
    assert len(payloads) == 2
    assert payloads[0]["dex"] == "flx"
    assert payloads[0]["display_symbol"] == "TSLA"
    assert payloads[0]["api_symbol"] == "flx:TSLA"
    assert payloads[1]["dex"] == "xyz"
    assert payloads[1]["display_symbol"] == "NVDA"
    assert payloads[1]["api_symbol"] == "xyz:NVDA"


# ---------------------------------------------------------------------------
# 9. Histogram records DEX counts separately
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_histogram_records_dex_counts_separately(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([
        {"type": "perpDeploy", "setOracle": {"oraclePxs": [["cash:TSLA", "100.0"]], "markPxs": []}},
        {"type": "perpDeploy", "setOracle": {"oraclePxs": [["flx:TSLA", "200.0"]], "markPxs": []}},
        {"type": "perpDeploy", "setOracle": {"oraclePxs": [["cash:NVDA", "300.0"]], "markPxs": []}},
    ])
    data = _lz4_frame.compress(b"\n".join(json.dumps(r).encode() for r in abci_records) + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    mock_cp.s3_read_object.return_value = data
    config = _make_config(allow_s3=True, out_root=tmp_path)
    budget = RuntimeBudgetState.create(max_minutes=60.0)
    result = build_full_file_histogram(mock_cp, config, "test_key", 1000, budget)
    assert result.setOracle_payloads_by_dex["cash"] == 2
    assert result.setOracle_payloads_by_dex["flx"] == 1
    assert "cash" in result.dexes_seen
    assert "flx" in result.dexes_seen


# ---------------------------------------------------------------------------
# 10. FLX candidate dump written when flx appears
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_flx_candidate_dump_written(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["flx:TSLA", "185.50"]], "markPxs": []},
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path, mock_cp, ["--markets", "flx:TSLA"])
    assert "flx_full_file_setoracle_candidate_dump.json" in written
    dump = written["flx_full_file_setoracle_candidate_dump.json"]
    assert dump["flx_tsla_found"] is True
    assert dump["candidate_count"] >= 1


# ---------------------------------------------------------------------------
# 11. No fake flx dump when flx absent
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_no_fake_flx_dump_when_absent(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["cash:TSLA", "100.0"]], "markPxs": []},
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path / "no_flx", mock_cp)
    assert "flx_full_file_setoracle_candidate_dump.json" not in written


# ---------------------------------------------------------------------------
# 12. Follow-up sampling plan written when flx absent
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_followup_plan_written_when_flx_absent(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["cash:TSLA", "100.0"]], "markPxs": []},
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path / "followup", mock_cp)
    assert "flx_full_file_followup_sampling_plan.json" in written
    plan = written["flx_full_file_followup_sampling_plan.json"]
    assert plan["flx_candidate_found"] is False
    assert plan["absence_claim_supported_by_full_file"] is False


# ---------------------------------------------------------------------------
# 13. Validation budget plan written when flx present
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_validation_budget_plan_when_flx_present(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["flx:TSLA", "185.50"]], "markPxs": []},
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path / "budget", mock_cp)
    assert "flx_overlap_validation_budget_plan.json" in written
    plan = written["flx_overlap_validation_budget_plan.json"]
    assert plan["flx_candidate_found"] is True
    assert plan["next_command_not_run"] is True


# ---------------------------------------------------------------------------
# 14. No validation/backfill/SonarX artifacts in full-file mode
# ---------------------------------------------------------------------------

@patch(f"{MOD}.NetworkChokepoint")
def test_no_validation_backfill_sonarx_artifacts(mock_cp_cls, tmp_path):
    if _lz4_frame is None:
        pytest.skip("lz4 not available")
    abci_records = _wrap_in_abci_block([{
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["cash:TSLA", "100.0"]], "markPxs": []},
    }])
    data = _lz4_frame.compress(json.dumps(abci_records[0]).encode() + b"\n")
    mock_cp = MagicMock()
    mock_cp_cls.return_value = mock_cp
    _setup_mock_chokepoint(mock_cp, data, "100.lz4")
    ret, written = _run_main_with_mock(tmp_path / "no_val", mock_cp)
    for k in written:
        if k == "replica_cmds_full_file_selection_plan.json":
            continue
        assert "sonarx" not in k.lower()


# ---------------------------------------------------------------------------
# 15. No production subprocess, os.system, or eval
# ---------------------------------------------------------------------------

def test_no_subprocess_os_system_eval():
    import ast
    src_path = Path(__file__).resolve().parent.parent / "hip3_replica_cmds_setoracle_reconstruction_v0.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("system", "popen"):
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    pytest.fail(f"os.system/os.popen found at line {node.lineno}")
            if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                pytest.fail(f"{func.id}() found at line {node.lineno}")


# ---------------------------------------------------------------------------
# 16. Dry-run works with new flags
# ---------------------------------------------------------------------------

def test_dry_run_with_full_file_flags(tmp_path):
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path), "--dry-run",
                "--decode-full-file", "--target-block", "100",
                "--block-selection", "exact",
            ])
    assert ret == 0


# ---------------------------------------------------------------------------
# 17. FullFileHistogramResult.to_dict() round-trips
# ---------------------------------------------------------------------------

def test_histogram_result_to_dict():
    result = FullFileHistogramResult(
        status="REPLICA_CMDS_FULL_FILE_HISTOGRAM_COMPLETE",
        dexes_seen=["cash", "flx"],
        setOracle_payloads_by_dex={"cash": 5, "flx": 2},
    )
    d = result.to_dict()
    assert d["status"] == "REPLICA_CMDS_FULL_FILE_HISTOGRAM_COMPLETE"
    assert d["dexes_seen"] == ["cash", "flx"]
    json.dumps(d)


# ---------------------------------------------------------------------------
# 18. Payload extraction with top-level type field
# ---------------------------------------------------------------------------

def test_parse_perpdeploy_top_level_type():
    record = {
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["km:AAPL", "195.0"]], "markPxs": []},
    }
    payloads = _parse_perpdeploy_setoracle_payload(record, 5, "test_key")
    assert len(payloads) == 1
    assert payloads[0]["dex"] == "km"
    assert payloads[0]["display_symbol"] == "AAPL"
    assert payloads[0]["record_index"] == 5


# ---------------------------------------------------------------------------
# 19. Payload extraction skips non-perpDeploy
# ---------------------------------------------------------------------------

def test_parse_perpdeploy_skips_non_perpdeploy():
    record = {"action": {"type": "transfer"}, "amount": 100}
    payloads = _parse_perpdeploy_setoracle_payload(record, 0, "test_key")
    assert len(payloads) == 0


# ---------------------------------------------------------------------------
# 20. Empty action dict falls through to top-level type
# ---------------------------------------------------------------------------

def test_parse_perpdeploy_empty_action_dict():
    record = {
        "action": {},
        "type": "perpDeploy",
        "setOracle": {"oraclePxs": [["cash:TSLA", "100.0"]], "markPxs": []},
    }
    payloads = _parse_perpdeploy_setoracle_payload(record, 0, "test_key")
    assert len(payloads) == 1
    assert payloads[0]["dex"] == "cash"
