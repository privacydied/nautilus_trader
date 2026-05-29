"""CLI tests for hip3_replica_cmds_setoracle_reconstruction_v0 runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the core main (accepts argv list directly)
# ---------------------------------------------------------------------------
from examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0 import (
    ReplicaCmdsChunkInventory,
    main,
)

_MOD = (
    "examples.strategies.venue_agnostic_signal_observer"
    ".hip3_replica_cmds_setoracle_reconstruction_v0"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _out_root(tmp_path: Path) -> str:
    """Return a unique out-root inside tmp_path."""
    return str(tmp_path / "reports")


def _make_ok_inventory() -> ReplicaCmdsChunkInventory:
    """Return a minimally-successful inventory result."""
    inv = ReplicaCmdsChunkInventory(
        bucket="hl-mainnet-node-data",
        root_prefix="replica_cmds",
        listing_status="OK",
        requester_pays_acknowledged=True,
        source_accessible=True,
    )
    return inv


def _ok_s3_inventory_side_effect(chokepoint, config, budget):
    """Return a callable that yields an OK inventory."""
    return _make_ok_inventory()


# ---------------------------------------------------------------------------
# 1. --dry-run returns exit code 0
# ---------------------------------------------------------------------------

class TestDryRunExits0:
    """--dry-run must return exit code 0 and produce no network calls."""

    def test_dry_run_exits_0(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main(["--dry-run", "--out-root", out])
        assert rc == 0

    def test_dry_run_produces_artifacts(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main(["--dry-run", "--out-root", out])
        assert rc == 0
        run_dirs = list(Path(out).iterdir())
        assert len(run_dirs) == 1, f"Expected exactly one run dir, got {run_dirs}"
        run_dir = run_dirs[0]
        assert (run_dir / "run_manifest.json").exists()
        assert (run_dir / "config.json").exists()
        # final_status written for dry-run
        final = json.loads((run_dir / "final_status.json").read_text())
        assert final["status"] == "REPLICA_CMDS_RECON_DRY_RUN_READY"


# ---------------------------------------------------------------------------
# 2. Without --allow-s3-archive-read, no S3 attempt is made
# ---------------------------------------------------------------------------

class TestMissingNetworkFlagsNoS3:
    """When --allow-s3-archive-read is absent the code must not call S3."""

    @patch(f"{_MOD}.NetworkChokepoint")
    def test_no_s3_attempt_when_flag_absent(self, mock_cp_cls, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main([
            "--out-root", out,
            # deliberately omit --allow-s3-archive-read
        ])
        # The probe_s3_inventory returns BLOCKED_NO_S3_FLAG before calling
        # any chokepoint S3 method, so NetworkChokepoint is created but its
        # s3_list_prefix is never called.
        mock_instance = mock_cp_cls.return_value
        mock_instance.s3_list_prefix.assert_not_called()
        # Exit code 1 because source is blocked
        assert rc == 1

    @patch(f"{_MOD}.NetworkChokepoint")
    def test_inventory_blocked_status(self, mock_cp_cls, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main(["--out-root", out])
        run_dirs = list(Path(out).iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        final = json.loads((run_dir / "final_status.json").read_text())
        assert final["status"] == "REPLICA_CMDS_SOURCE_BLOCKED"
        assert final["failure_reason"] == "REQUESTER_PAYS_CREDENTIALS_REQUIRED"


# ---------------------------------------------------------------------------
# 3. --backfill-after-validation without a validation artifact fails closed
# ---------------------------------------------------------------------------

class TestBackfillAfterValidationFailsClosed:
    """--backfill-after-validation + --validate-overlap but no forward
    recorder root must fail closed (exit 1, OVERLAP_UNAVAILABLE)."""

    @patch(f"{_MOD}.NetworkChokepoint")
    @patch(f"{_MOD}.probe_s3_inventory", side_effect=_ok_s3_inventory_side_effect)
    @patch(f"{_MOD}.probe_recon_schema")
    def test_backfill_without_artifact_fails_closed(
        self, mock_schema, mock_inv, mock_cp_cls, tmp_path: Path
    ) -> None:
        # Set up a minimal schema probe result so we get past envelope check
        mock_schema.return_value = (
            MagicMock(source_key="k", bytes_read=100, raw_prefix_hex="aa",
                      compression_detected="none", decompression_attempts=[],
                      decompressor_required=False, decompressor_available=False,
                      record_boundary_strategy="newline",
                      envelope_status="REPLICA_CMDS_ENVELOPE_DECODED",
                      proceed_to_content_search_allowed=True,
                      failure_reason=None),
            MagicMock(),  # first_sample
            [],           # candidates
            {             # schema_result
                "oracle_like_candidates_found": 1,
                "setoracle_command_found": True,
                "schema_confidence": "high",
                "flx_priority_candidates_found": 1,
                "bytes_read": 1000,
                "decoded_records_seen": 10,
                "hip3_deployer_setoracle_candidates_found": 1,
            },
        )
        out = _out_root(tmp_path)
        rc = main([
            "--out-root", out,
            "--allow-s3-archive-read",
            "--validate-overlap",
            "--backfill-after-validation",
            # no --forward-recorder-root
        ])
        # Without a forward recorder, overlap is unavailable -> exit 1
        assert rc == 1
        run_dirs = list(Path(out).iterdir())
        run_dir = run_dirs[0]
        final = json.loads((run_dir / "final_status.json").read_text())
        assert final["status"] == "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 4. Invalid market format is handled cleanly
# ---------------------------------------------------------------------------

class TestInvalidMarketFormatFailsCleanly:
    """Markets without ':' should not crash the runner."""

    def test_invalid_market_format_exits_cleanly(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        # "NOCOLON" has no colon -> api_symbol_dex returns None
        # This should not crash; the market is simply unparseable
        rc = main([
            "--out-root", out,
            "--markets", "NOCOLON",
            "--dry-run",
        ])
        # dry-run returns 0 regardless of market format
        assert rc == 0

    @patch(f"{_MOD}.NetworkChokepoint")
    def test_mixed_valid_invalid_markets(self, mock_cp_cls, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        # Mix of valid and invalid market formats
        rc = main([
            "--out-root", out,
            "--markets", "flx:TSLA,INVALID,xyz:AAPL",
            "--allow-s3-archive-read",
        ])
        # Without --dry-run, goes to S3 probe which blocks -> exit 1
        # The point is no crash from invalid market format
        assert rc in (0, 1)

    def test_empty_market_string(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main([
            "--out-root", out,
            "--markets", "",
            "--dry-run",
        ])
        assert rc == 0


# ---------------------------------------------------------------------------
# 5. --max-runtime-minutes is accepted and honored
# ---------------------------------------------------------------------------

class TestMaxRuntimeMinutesAccepted:
    """The --max-runtime-minutes flag should be parsed and stored."""

    def test_max_runtime_minutes_in_manifest(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main([
            "--dry-run",
            "--out-root", out,
            "--max-runtime-minutes", "5",
        ])
        assert rc == 0
        run_dirs = list(Path(out).iterdir())
        run_dir = run_dirs[0]
        manifest = json.loads((run_dir / "run_manifest.json").read_text())
        assert manifest["max_runtime_minutes"] == 5.0

    def test_max_runtime_minutes_in_config(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main([
            "--dry-run",
            "--out-root", out,
            "--max-runtime-minutes", "15.5",
        ])
        assert rc == 0
        run_dirs = list(Path(out).iterdir())
        run_dir = run_dirs[0]
        cfg = json.loads((run_dir / "config.json").read_text())
        assert cfg["max_runtime_minutes"] == 15.5

    def test_default_max_runtime_minutes(self, tmp_path: Path) -> None:
        out = _out_root(tmp_path)
        rc = main(["--dry-run", "--out-root", out])
        assert rc == 0
        run_dirs = list(Path(out).iterdir())
        run_dir = run_dirs[0]
        manifest = json.loads((run_dir / "run_manifest.json").read_text())
        assert manifest["max_runtime_minutes"] == 30.0


# ---------------------------------------------------------------------------
# 6. Report dir and artifacts are created before network calls
# ---------------------------------------------------------------------------

class TestReportDirCreatedEarly:
    """run_dir, run_manifest.json, and config.json must exist before any
    network call (chokepoint creation / S3 probe)."""

    @patch(f"{_MOD}.NetworkChokepoint")
    def test_dir_and_artifacts_exist_before_network(
        self, mock_cp_cls, tmp_path: Path
    ) -> None:
        """Verify artifacts are written before probe_s3_inventory is invoked."""
        artifacts_before_probe = {}

        original_probe = None
        import examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0 as mod

        original_inv = mod.probe_s3_inventory

        def capturing_probe(chokepoint, config, budget):
            """Capture filesystem state at the point probe_s3_inventory is called."""
            run_dirs = list(Path(config.out_root).iterdir())
            for rd in run_dirs:
                artifacts_before_probe["run_dir_exists"] = rd.is_dir()
                artifacts_before_probe["manifest_exists"] = (rd / "run_manifest.json").exists()
                artifacts_before_probe["config_exists"] = (rd / "config.json").exists()
            # Return blocked inventory so we exit cleanly
            inv = ReplicaCmdsChunkInventory(
                bucket="test", root_prefix="test",
                listing_status="BLOCKED_NO_S3_FLAG",
                source_accessible=False,
                failure_reason="REQUESTER_PAYS_CREDENTIALS_REQUIRED",
            )
            return inv

        with patch.object(mod, "probe_s3_inventory", side_effect=capturing_probe):
            out = _out_root(tmp_path)
            rc = main([
                "--out-root", out,
                "--allow-s3-archive-read",
            ])

        # Artifacts must have been written before probe_s3_inventory ran
        assert artifacts_before_probe.get("run_dir_exists") is True
        assert artifacts_before_probe.get("manifest_exists") is True
        assert artifacts_before_probe.get("config_exists") is True
        # Overall run returns 1 (blocked) but artifacts were created early
        assert rc == 1

    @patch(f"{_MOD}.NetworkChokepoint")
    def test_manifest_written_with_correct_fields(
        self, mock_cp_cls, tmp_path: Path
    ) -> None:
        out = _out_root(tmp_path)
        rc = main(["--out-root", out, "--allow-s3-archive-read"])
        run_dirs = list(Path(out).iterdir())
        run_dir = run_dirs[0]
        manifest = json.loads((run_dir / "run_manifest.json").read_text())
        # Core fields must be present
        assert manifest["study_id"] == "hip3_replica_cmds_setoracle_reconstruction_v0"
        assert manifest["safety_mode"] == "public_archive_observer_only"
        assert manifest["no_orders_no_auth_no_live_confirmation"] is True
        assert manifest["registry_mutated"] is False
        assert manifest["phase0_precommitment_written"] is False
        assert manifest["run_id"]  # non-empty
        assert manifest["created_at_utc"]  # non-empty
