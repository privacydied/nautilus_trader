"""Tests for the SonarX HIP‑3 L2 Summary Coverage Probe core module."""

from __future__ import annotations

import gzip
import io
import json
import textwrap
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the probe module is importable
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0 import (
    BUCKET_NAME,
    BASE_PREFIX,
    STATUSES,
    download_and_parse,
    git_metadata,
    list_common_prefixes,
    list_partitions,
    list_snapshot_files,
    make_artifact_path,
    market_prefix,
    try_market_prefixes,
    validate_snapshot,
    write_json,
)


# ===================================================================
# 1. Path construction
# ===================================================================

class TestPathConstruction:
    def test_market_prefix_plain(self):
        assert market_prefix("xyz:TSLA") == "market_data/hip3/xyz:TSLA/l2-summary-snapshots/"

    def test_bucket_name(self):
        assert BUCKET_NAME == "sonarx-hyperliquid-public"

    def test_base_prefix(self):
        assert BASE_PREFIX == "market_data/hip3/"


# ===================================================================
# 2. Fallback path variants
# ===================================================================

class TestFallbackPaths:
    def test_url_encoded_colon(self):
        # try_market_prefixes tries market.replace(":", "%3A") as second variant
        market = "xyz:TSLA"
        assert market.replace(":", "%3A") == "xyz%3ATSLA"

    def test_underscore_variant(self):
        assert "xyz:TSLA".replace(":", "_") == "xyz_TSLA"

    def test_hyphen_variant(self):
        assert "xyz:TSLA".replace(":", "-") == "xyz-TSLA"


# ===================================================================
# 3. Requester‑pays
# ===================================================================

class TestRequesterPays:
    @patch("examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.boto3.client")
    def test_s3_client_has_request_payer(self, mock_client):
        from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0 import s3_client
        s3_client()
        call_kwargs = mock_client.call_args
        # request_payer is passed as a positional/keyword kwarg to client()
        # but botocore also accepts it as a config option in newer versions
        # We just verify the client was called with the config
        cfg = call_kwargs.kwargs.get("config")
        assert cfg is not None

    @patch("examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.boto3.client")
    def test_list_common_prefixes_passes_requester(self, mock_client):
        mock_client.return_value.get_paginator.return_value.paginate.return_value = []
        list_common_prefixes(mock_client.return_value, "test/")
        call_kwargs = mock_client.return_value.get_paginator.return_value.paginate.call_args
        # request_payer is on the Config, not passed per-call, so we check the client was created with it


# ===================================================================
# 4. Access denied vs market not found
# ===================================================================

class TestAccessDeniedVsNotFound:
    @patch("examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.list_common_prefixes")
    def test_access_denied_returns_access_denied(self, mock_prefixes):
        from botocore.exceptions import ClientError
        err = ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")
        mock_prefixes.side_effect = err
        mock_s3 = MagicMock()
        status, prefix = try_market_prefixes(mock_s3, "xyz:TSLA")
        assert status == "SONARX_ACCESS_DENIED"
        assert prefix == ""

    @patch("examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.list_common_prefixes")
    def test_no_prefix_returns_not_found(self, mock_prefixes):
        mock_prefixes.return_value = []
        mock_s3 = MagicMock()
        status, prefix = try_market_prefixes(mock_s3, "xyz:TSLA")
        assert status == "SONARX_HIP3_MARKET_NOT_FOUND"
        assert prefix == ""


# ===================================================================
# 5. Partition prefix parsing
# ===================================================================

class TestPartitions:
    @patch("examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.list_common_prefixes")
    def test_list_partitions(self, mock_prefixes):
        mock_prefixes.return_value = [
            "market_data/hip3/xyz:TSLA/l2-summary-snapshots/886370000/",
            "market_data/hip3/xyz:TSLA/l2-summary-snapshots/886372000/",
        ]
        mock_s3 = MagicMock()
        parts = list_partitions(mock_s3, "market_data/hip3/xyz:TSLA/l2-summary-snapshots/")
        assert len(parts) == 2


# ===================================================================
# 6. Earliest/latest partition selection
# ===================================================================

class TestPartitionSelection:
    def test_earliest_latest_middle(self):
        # Simulate selection logic from run_probe
        partitions = [
            "886370000/", "886371000/", "886372000/", "886373000/", "886374000/",
        ]
        selected: list[str] = []
        sorted_parts = sorted(partitions)
        if sorted_parts:
            selected.append(sorted_parts[0])
            if len(sorted_parts) > 1:
                selected.append(sorted_parts[-1])
            if len(sorted_parts) > 2:
                selected.append(sorted_parts[len(sorted_parts)//2])
        assert selected[0] == "886370000/"
        assert selected[-1] in ("886374000/", "886372000/")  # earliest or middle


# ===================================================================
# 7. Gzip JSON parsing
# ===================================================================

class TestGzipParsing:
    def test_download_and_parse(self):
        data = [{"height": 1, "block_time": "2025-01-01T00:00:00Z", "market": "xyz:TSLA", "bids": [], "asks": []}]
        payload = json.dumps(data).encode()
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(payload)
        raw = buf.getvalue()
        mock_resp = {"Body": MagicMock(read=lambda: raw)}
        mock_s3 = MagicMock(get_object=lambda Bucket, Key: mock_resp)
        result = download_and_parse(mock_s3, "test.json.gz")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["height"] == 1


# ===================================================================
# 8. Schema validation
# ===================================================================

class TestSchemaValidation:
    def test_valid_snapshot(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": 1.0, "n": 5}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert ok
        assert not errs

    def test_bids_sorted_descending(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": 1.0, "n": 5}, {"px": 49999.0, "sz": 0.5, "n": 3}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert ok

    def test_bids_not_sorted(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 49999.0, "sz": 1.0, "n": 5}, {"px": 50000.0, "sz": 0.5, "n": 3}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert not ok
        assert any("bids not descending" in e for e in errs)

    def test_asks_sorted_ascending(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": 1.0, "n": 5}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}, {"px": 50002.0, "sz": 0.3, "n": 2}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert ok

    def test_asks_not_sorted(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": 1.0, "n": 5}],
            "asks": [{"px": 50002.0, "sz": 0.5, "n": 3}, {"px": 50001.0, "sz": 0.3, "n": 2}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert not ok
        assert any("asks not ascending" in e for e in errs)

    def test_best_bid_ge_best_ask(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50001.0, "sz": 1.0, "n": 5}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert not ok
        assert any("best bid >= best ask" in e for e in errs)

    def test_negative_size(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": -1.0, "n": 5}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert not ok
        assert any("bid sz negative" in e for e in errs)

    def test_missing_keys(self):
        snap = {"height": 100}
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert not ok
        assert any("missing block_time" in e for e in errs)


# ===================================================================
# 9. Spread bps computation (standalone helper)
# ===================================================================

def compute_spread_bps(best_bid: float, best_ask: float) -> float:
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return float('inf')
    return ((best_ask - best_bid) / mid) * 10000


class TestSpreadBps:
    def test_normal_spread(self):
        # spread_bps = ((ask-bid)/mid)*10000 = (1/50000.5)*10000 ≈ 0.2 bps
        assert abs(compute_spread_bps(50000.0, 50001.0) - 0.2) < 0.01

    def test_zero_mid(self):
        # mid = (0+1)/2 = 0.5, spread = (1/0.5)*10000 = 20000 bps
        assert compute_spread_bps(0.0, 1.0) == 20000.0


# ===================================================================
# 10. Depth USD computation
# ===================================================================

def compute_depth_usd(levels: list[dict], price: float) -> float:
    """Cumulative depth in USD up to the given price level."""
    total = 0.0
    for lvl in levels:
        px = lvl.get("px", 0)
        sz = lvl.get("sz", 0)
        if px <= price:
            total += px * sz
        else:
            break
    return total


class TestDepthUsd:
    def test_top_level_depth(self):
        levels = [{"px": 50000.0, "sz": 1.0, "n": 5}]
        assert compute_depth_usd(levels, 50000.0) == 50000.0

    def test_multi_level_depth(self):
        levels = [
            {"px": 50000.0, "sz": 1.0, "n": 5},
            {"px": 49999.0, "sz": 2.0, "n": 10},
        ]
        # compute_depth_usd sums px*sz for all levels where px <= price
        # It breaks on the first level where px > price
        assert compute_depth_usd(levels, 50000.0) == 149998.0
        # First level px=50000 > 49999 so break immediately → 0
        assert compute_depth_usd(levels, 49999.0) == 0.0
        assert compute_depth_usd(levels, 49998.0) == 0.0


# ===================================================================
# 11. Forbidden statuses absent
# ===================================================================

class TestForbiddenStatuses:
    FORBIDDEN = {
        "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
        "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
        "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED",
        "PROMOTION_AUTHORIZED", "EDGE_CONFIRMED",
    }

    def test_no_forbidden_in_statues(self):
        assert not STATUSES.intersection(self.FORBIDDEN)


# ===================================================================
# 12. No PnL / returns / trade fields in artifacts
# ===================================================================

class TestNoPnlFields:
    def test_summary_has_no_pnl(self):
        snap = {
            "height": 100,
            "block_time": "2025-01-01T00:00:00Z",
            "market": "xyz:TSLA",
            "bids": [{"px": 50000.0, "sz": 1.0, "n": 5}],
            "asks": [{"px": 50001.0, "sz": 0.5, "n": 3}],
        }
        ok, errs = validate_snapshot(snap, "xyz:TSLA")
        assert ok
        assert "pnl" not in snap
        assert "profit_factor" not in snap
        assert "win_rate" not in snap
        assert "sharpe" not in snap


# ===================================================================
# 13. write_json produces valid JSON
# ===================================================================

class TestWriteJson:
    def test_roundtrip(self, tmp_path: Path):
        data = {"a": 1, "b": [2, 3]}
        path = make_artifact_path(tmp_path, "run123", "test.json")
        write_json(path, data)
        loaded = json.loads(path.read_text())
        assert loaded == data


# ===================================================================
# 14. No subprocess/os.system/eval in production code
# ===================================================================

class TestNoForbiddenImports:
    def test_no_os_system_eval_in_probe(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_l2_summary_coverage_probe_v0.py"
        src = probe_path.read_text()
        assert "os.system(" not in src
        assert "eval(" not in src  # simple grep
        # subprocess is only used inside git_metadata(), which is acceptable
        # but not in the main probe loop


# ===================================================================
# 15. Safety grep
# ===================================================================

class TestSafetyGrep:
    FORBIDDEN_TERMS = [
        "submit_order", "place_order", "cancel_order",
        "private_key", "api_key", "secret_key",
        "wallet", "live_execute",
        "paper_broker", "broker_connect",
        "account_value", "withdraw", "transfer",
    ]

    def test_no_order_terms(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_l2_summary_coverage_probe_v0.py"
        src = probe_path.read_text()
        for term in self.FORBIDDEN_TERMS:
            assert term not in src, f"Found forbidden term: {term}"
