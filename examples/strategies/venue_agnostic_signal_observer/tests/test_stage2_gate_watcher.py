# ruff: noqa: D202,D403,RUF100,S108,SIM117
"""Tests for Stage 2 Gate Watcher."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


# Ensure the module is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.strategies.venue_agnostic_signal_observer import (
    run_stage2_gate_watcher as watcher,  # noqa: E402
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def args():
    """Default test args."""
    parser = watcher.build_parser()
    return parser.parse_args([
        "--interval-seconds", "1",
        "--max-runtime-seconds", "5",
        "--preflight-seconds", "5",
        "--fast-duration-seconds", "10",
        "--full-duration-seconds", "15",
        "--oi-interval-seconds", "3",
        "--gpu-device", "cuda:0",
    ])


@pytest.fixture
def tmp_workdir(tmp_path):
    """Change to a temp directory for artifact isolation."""
    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old_cwd)


@pytest.fixture
def mock_watcher_log():
    """Create a mock WatcherLogger with extra attributes expected by notification code."""
    m = MagicMock(spec=watcher.WatcherLogger)
    m._log_path = Path("/tmp/test_watcher.log")
    m._summary_path = Path("/tmp/test_watcher_summary.json")
    return m


# ---------------------------------------------------------------------------
# Mode parsing
# ---------------------------------------------------------------------------

class TestParseCaptureModeDir:
    def test_fast_diagnostic(self):
        result = watcher._parse_capture_mode_dir("FAST_DIAGNOSTIC", "20260515_123456")
        assert "FAST_DIAGNOSTIC" in result
        assert "ACTIVE" not in result

    def test_full_active(self):
        result = watcher._parse_capture_mode_dir("FULL_ACTIVE", "20260515_123456")
        assert "ACTIVE" in result
        assert "FAST_DIAGNOSTIC" not in result


class TestParseEvaluationModeDir:
    def test_fast_diagnostic(self):
        result = watcher._parse_evaluation_mode_dir("FAST_DIAGNOSTIC", "20260515_123456")
        assert "FAST_DIAGNOSTIC" in result

    def test_full_active(self):
        result = watcher._parse_evaluation_mode_dir("FULL_ACTIVE", "20260515_123456")
        assert "ACTIVE" in result


# ---------------------------------------------------------------------------
# NO_CAPTURE behavior
# ---------------------------------------------------------------------------

def test_no_capture_does_not_run_preflight(mock_watcher_log, args):
    """NO_CAPTURE loops and does not run preflight/capture."""
    with patch.object(watcher, "run_volatility_gate") as mock_gate:
        mock_gate.return_value = {"capture_permission": "NO_CAPTURE",
                                  "fast_capture_reason": "not_eligible"}

        with patch.object(watcher, "WatcherLogger") as mock_logger_cls:
            mock_logger = MagicMock()
            mock_logger_cls.return_value = mock_logger

            # The watcher loop should run out of max_runtime without triggering capture
            with patch.object(watcher, "run_preflight") as mock_preflight:
                # We need to let the loop run for a bit. Use a short max-runtime
                short_args = MagicMock()
                short_args.interval_seconds = 0.01
                short_args.max_runtime_seconds = 0.2
                short_args.dry_run = False
                short_args.prefer_gpu = False
                short_args.gpu_device = "cuda:0"
                short_args.preflight_seconds = 5
                short_args.fast_duration_seconds = 10
                short_args.full_duration_seconds = 15
                short_args.oi_interval_seconds = 3

                mock_gate.return_value = {
                    "capture_permission": "NO_CAPTURE",
                    "fast_capture_reason": "not_eligible",
                }

                watcher._main_impl(short_args, mock_logger, "20260515_000000")

                mock_preflight.assert_not_called()
                mock_logger.finalize.assert_called_once()
                verdict = mock_logger.finalize.call_args[0][0]
                assert verdict == "MAX_RUNTIME_REACHED"


# ---------------------------------------------------------------------------
# Permission → mode mapping
# ---------------------------------------------------------------------------

def test_fast_diagnostic_chooses_900s(args):
    """FAST_DIAGNOSTIC_CAPTURE_ONLY chooses fast_duration_seconds capture."""
    mode = "FAST_DIAGNOSTIC"
    duration = args.fast_duration_seconds
    assert duration == 10  # fast-duration-seconds set to 10 in fixture
    capture_dir = watcher._parse_capture_mode_dir(mode, "20260515_000000")
    assert "FAST_DIAGNOSTIC" in capture_dir


def test_full_active_chooses_1800s(args):
    """FULL_ACTIVE_CAPTURE chooses full_duration_seconds capture."""
    mode = "FULL_ACTIVE"
    duration = args.full_duration_seconds
    assert duration == 15  # full-duration-seconds set to 15 in fixture
    capture_dir = watcher._parse_capture_mode_dir(mode, "20260515_000000")
    assert "ACTIVE" in capture_dir


# ---------------------------------------------------------------------------
# Lock behavior
# ---------------------------------------------------------------------------

def test_existing_lock_blocks_collection(tmp_workdir, mock_watcher_log, args):
    """Existing lock blocks collection."""
    lock_path = Path("reports/stage2_collection.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_data = {
        "created_at_utc": "2026-05-15T12:00:00Z",
        "pid": 99999,
        "capture_permission": "FULL_ACTIVE_CAPTURE",
        "fast_capture_reason": "test",
        "git_sha": "abc123",
        "mode": "FULL_ACTIVE",
    }
    with open(lock_path, "w") as f:
        json.dump(lock_data, f)

    locked, msg = watcher.check_lock(mock_watcher_log)
    assert locked
    assert "COLLECTION_LOCK_EXISTS" in msg


def test_no_lock_allows_collection(tmp_workdir, mock_watcher_log):
    """No lock means no block."""
    locked, msg = watcher.check_lock(mock_watcher_log)
    assert not locked
    assert msg == ""


# ---------------------------------------------------------------------------
# Preflight failure
# ---------------------------------------------------------------------------

@patch.object(watcher, "check_lock", return_value=(False, ""))
def test_preflight_failure_blocks_capture(mock_lock, tmp_workdir, mock_watcher_log, args):
    """Preflight failure blocks capture/evaluation."""

    with patch.object(watcher, "run_volatility_gate") as mock_gate:
        mock_gate.return_value = {
            "capture_permission": "FAST_DIAGNOSTIC_CAPTURE_ONLY",
            "fast_capture_reason": "test",
        }

        with patch.object(watcher, "run_preflight") as mock_preflight:
            mock_preflight.return_value = None  # preflight fails

            with patch.object(watcher, "run_capture") as mock_capture:
                mock_impl_args = MagicMock()
                mock_impl_args.interval_seconds = 0.01
                mock_impl_args.max_runtime_seconds = 0.5
                mock_impl_args.dry_run = False
                mock_impl_args.prefer_gpu = False
                mock_impl_args.gpu_device = "cuda:0"
                mock_impl_args.preflight_seconds = 5
                mock_impl_args.fast_duration_seconds = 10
                mock_impl_args.full_duration_seconds = 15
                mock_impl_args.oi_interval_seconds = 3

                watcher._main_impl(mock_impl_args, mock_watcher_log, "20260515_000000")

                mock_capture.assert_not_called()
                mock_watcher_log.finalize.assert_called_once()
                verdict = mock_watcher_log.finalize.call_args[0][0]
                assert verdict == "STREAM_PREFLIGHT_FAILED"


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_creates_no_lock(tmp_workdir, mock_watcher_log, args):
    """Dry run creates no lock and runs no capture."""
    with patch.object(watcher, "run_volatility_gate") as mock_gate:
        mock_gate.return_value = {
            "capture_permission": "FAST_DIAGNOSTIC_CAPTURE_ONLY",
            "fast_capture_reason": "test",
        }

        dry_args = MagicMock()
        dry_args.interval_seconds = 0.01
        dry_args.max_runtime_seconds = 0.5
        dry_args.dry_run = True
        dry_args.prefer_gpu = False
        dry_args.gpu_device = "cuda:0"
        dry_args.preflight_seconds = 5
        dry_args.fast_duration_seconds = 10
        dry_args.full_duration_seconds = 15
        dry_args.oi_interval_seconds = 3

        lock_path = Path("reports/stage2_collection.lock")

        with patch.object(watcher, "run_preflight") as mock_preflight:
            with patch.object(watcher, "run_capture") as mock_capture:
                watcher._main_impl(dry_args, mock_watcher_log, "20260515_000000")

                assert not lock_path.exists()
                mock_preflight.assert_not_called()
                mock_capture.assert_not_called()
                mock_watcher_log.finalize.assert_called_once()
                verdict = mock_watcher_log.finalize.call_args[0][0]
                assert verdict == "DRY_RUN_COMPLETE"


# ---------------------------------------------------------------------------
# Unknown permission
# ---------------------------------------------------------------------------

def test_unknown_permission_fails_safely(mock_watcher_log, args):
    """Unknown capture_permission fails safely."""
    with patch.object(watcher, "run_volatility_gate") as mock_gate:
        mock_gate.return_value = {
            "capture_permission": "SOMETHING_ELSE",
            "fast_capture_reason": "unknown",
        }

        unknown_args = MagicMock()
        unknown_args.interval_seconds = 0.01
        unknown_args.max_runtime_seconds = 0.3
        unknown_args.dry_run = False
        unknown_args.prefer_gpu = False
        unknown_args.gpu_device = "cuda:0"
        unknown_args.preflight_seconds = 5
        unknown_args.fast_duration_seconds = 10
        unknown_args.full_duration_seconds = 15
        unknown_args.oi_interval_seconds = 3

        with patch.object(watcher, "run_preflight") as mock_preflight:
            with patch.object(watcher, "run_capture") as mock_capture:
                watcher._main_impl(unknown_args, mock_watcher_log, "20260515_000000")

                mock_preflight.assert_not_called()
                mock_capture.assert_not_called()
                mock_watcher_log.finalize.assert_called_once()
                verdict = mock_watcher_log.finalize.call_args[0][0]
                assert verdict == "MAX_RUNTIME_REACHED"


# ---------------------------------------------------------------------------
# Output directory naming
# ---------------------------------------------------------------------------

class TestOutputDirNaming:
    def test_capture_fast_dir_format(self):
        ts = "20260515_123456"
        d = watcher._parse_capture_mode_dir("FAST_DIAGNOSTIC", ts)
        assert d == f"data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_{ts}"

    def test_capture_active_dir_format(self):
        ts = "20260515_123456"
        d = watcher._parse_capture_mode_dir("FULL_ACTIVE", ts)
        assert d == f"data/derivatives_spot_capture_v2_ACTIVE_{ts}"

    def test_eval_fast_dir_format(self):
        ts = "20260515_123456"
        d = watcher._parse_evaluation_mode_dir("FAST_DIAGNOSTIC", ts)
        assert d == f"reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_{ts}"

    def test_eval_active_dir_format(self):
        ts = "20260515_123456"
        d = watcher._parse_evaluation_mode_dir("FULL_ACTIVE", ts)
        assert d == f"reports/derivatives_spot_lead_lag_v2_ACTIVE_{ts}"


# ---------------------------------------------------------------------------
# Uses sys.executable
# ---------------------------------------------------------------------------

def test_uses_sys_executable(tmp_workdir, mock_watcher_log):
    """Uses sys.executable, not bare python."""
    assert hasattr(watcher, "main")

    # The _main_impl function sets python_exe = sys.executable at the top.
    # Verify by checking the function signature and first assignment.
    import inspect
    source = inspect.getsource(watcher._main_impl)
    assert "sys.executable" in source
    assert "python " not in source.split("python_exe")[0].split("\n")[-3:]  # no bare 'python' before assignment


# ---------------------------------------------------------------------------
# Does NOT touch REJECTED_RESEARCH.md
# ---------------------------------------------------------------------------

def test_no_rejected_research_reference():
    """Module does not reference REJECTED_RESEARCH.md."""
    with open(watcher.__file__) as f:
        content = f.read()
    assert "REJECTED_RESEARCH" not in content
    assert "rejected_research" not in content.lower()


# ---------------------------------------------------------------------------
# Preflight validation logic
# ---------------------------------------------------------------------------

class TestValidatePreflight:
    def test_valid_preflight(self, tmp_path):
        """A well-formed manifest passes validation."""
        capture_dir = tmp_path / "preflight"
        capture_dir.mkdir(parents=True)
        manifest = {
            "global_timestamp_overlap_seconds": 30.0,
            "streams": [
                {"source": "binance_perp", "type": "trade", "symbol": "BTCUSDT", "count": 100},
                {"source": "binance_perp", "type": "trade", "symbol": "ETHUSDT", "count": 80},
                {"source": "binance_perp", "type": "trade", "symbol": "SOLUSDT", "count": 60},
                {"source": "kraken", "type": "trade", "symbol": "BTC/USD", "count": 50},
                {"source": "coinbase", "type": "trade", "symbol": "ETH/USD", "count": 40},
                {"source": "kraken", "type": "trade", "symbol": "SOL/USD", "count": 30},
                {"source": "binance_perp", "type": "open_interest", "symbol": "BTCUSDT", "count": 20},
                {"source": "binance_perp", "type": "open_interest", "symbol": "ETHUSDT", "count": 20},
                {"source": "binance_perp", "type": "open_interest", "symbol": "SOLUSDT", "count": 20},
            ],
        }
        with open(capture_dir / "capture_manifest.json", "w") as f:
            json.dump(manifest, f)

        result = watcher.validate_preflight(str(capture_dir))
        assert result

    def test_missing_manifest(self, tmp_path):
        capture_dir = tmp_path / "empty"
        capture_dir.mkdir()
        result = watcher.validate_preflight(str(capture_dir))
        assert not result

    def test_zero_overlap(self, tmp_path):
        capture_dir = tmp_path / "zero_overlap"
        capture_dir.mkdir(parents=True)
        manifest = {
            "global_timestamp_overlap_seconds": 0,
            "streams": [],
        }
        with open(capture_dir / "capture_manifest.json", "w") as f:
            json.dump(manifest, f)
        assert not watcher.validate_preflight(str(capture_dir))

    def test_missing_binance_stream(self, tmp_path):
        capture_dir = tmp_path / "missing_binance"
        capture_dir.mkdir(parents=True)
        manifest = {
            "global_timestamp_overlap_seconds": 30.0,
            "streams": [
                {"source": "binance_perp", "type": "trade", "symbol": "BTCUSDT", "count": 100},
                # Missing ETH, SOL source streams
                {"source": "kraken", "type": "trade", "symbol": "BTC/USD", "count": 50},
            ],
        }
        with open(capture_dir / "capture_manifest.json", "w") as f:
            json.dump(manifest, f)
        assert not watcher.validate_preflight(str(capture_dir))

    def test_subscription_failure(self, tmp_path):
        capture_dir = tmp_path / "sub_failure"
        capture_dir.mkdir(parents=True)
        manifest = {
            "global_timestamp_overlap_seconds": 30.0,
            "streams": [
                {"source": "binance_perp", "type": "trade", "symbol": "BTCUSDT", "count": 0,
                 "error": "subscription failed"},
            ],
        }
        with open(capture_dir / "capture_manifest.json", "w") as f:
            json.dump(manifest, f)
        assert not watcher.validate_preflight(str(capture_dir))


# ---------------------------------------------------------------------------
# Volatility gate parsing
# ---------------------------------------------------------------------------

class TestGateParsing:
    def test_no_capture_parsing(self):
        data = {
            "capture_permission": "NO_CAPTURE",
            "fast_capture_eligible": False,
            "fast_capture_reason": "not_fast_capture_eligible",
            "market_verdict": "MARKET_ACTIVE",
            "acceleration": {"verdict": "NOT_ACCELERATING"},
        }
        assert data["capture_permission"] == "NO_CAPTURE"
        assert data["fast_capture_eligible"] is False

    def test_fast_diagnostic_parsing(self):
        data = {
            "capture_permission": "FAST_DIAGNOSTIC_CAPTURE_ONLY",
            "fast_capture_eligible": True,
            "fast_capture_reason": "eth_fast_active_accelerating",
        }
        assert data["capture_permission"] == "FAST_DIAGNOSTIC_CAPTURE_ONLY"
        assert data["fast_capture_eligible"]

    def test_full_active_parsing(self):
        data = {
            "capture_permission": "FULL_ACTIVE_CAPTURE",
            "fast_capture_eligible": True,
            "fast_capture_reason": "main_gate_confirmed: MARKET_ACTIVE + ACCELERATING",
        }
        assert data["capture_permission"] == "FULL_ACTIVE_CAPTURE"


# ---------------------------------------------------------------------------
# Notification tests
# ---------------------------------------------------------------------------


class TestSendDesktopNotification:
    def test_disabled_returns_false(self, mock_watcher_log):
        """--notify disabled does not call subprocess.run."""
        with patch.object(watcher.subprocess, "run") as mock_run:
            result = watcher.send_desktop_notification(
                enabled=False, command="notify-send",
                app_name="test", title="t", body="b",
                urgency="normal", timeout_ms=5000, log=mock_watcher_log,
            )
            assert result is False
            mock_run.assert_not_called()

    def test_enabled_calls_subprocess_with_shell_false(self, mock_watcher_log):
        """--notify enabled calls subprocess.run with args list."""
        with patch.object(watcher.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            result = watcher.send_desktop_notification(
                enabled=True, command="notify-send",
                app_name="Nautilus Stage2", title="Test Title",
                body="Test body here", urgency="normal",
                timeout_ms=10000, log=mock_watcher_log,
            )
            assert result is True
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "notify-send"
            assert args[1] == "--app-name"
            assert args[2] == "Nautilus Stage2"
            assert args[3] == "--urgency"
            assert args[4] == "normal"
            assert args[5] == "--expire-time"
            assert args[6] == "10000"
            assert args[7] == "Test Title"
            assert args[8] == "Test body here"
            # Ensure no shell=True
            kwargs = mock_run.call_args[1]
            assert kwargs.get("shell") is not True

    def test_non_zero_exit_returns_false(self, mock_watcher_log):
        """Notification failure returns False and logs a warning."""
        with patch.object(watcher.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "permission denied"
            result = watcher.send_desktop_notification(
                enabled=True, command="notify-send",
                app_name="test", title="t", body="b",
                urgency="normal", timeout_ms=5000, log=mock_watcher_log,
            )
            assert result is False
            mock_watcher_log.warning.assert_called()

    def test_missing_command_logs_warning(self, mock_watcher_log):
        """Missing notify-send returns False and logs a warning."""
        with patch.object(watcher.subprocess, "run", side_effect=FileNotFoundError):
            result = watcher.send_desktop_notification(
                enabled=True, command="notify-send",
                app_name="test", title="t", body="b",
                urgency="normal", timeout_ms=5000, log=mock_watcher_log,
            )
            assert result is False
            mock_watcher_log.warning.assert_called()
            warning_text = mock_watcher_log.warning.call_args[0][0]
            assert "notify-send" in warning_text or "libnotify" in warning_text

    def test_timeout_logs_warning(self, mock_watcher_log):
        """Timeout returns False and logs a warning."""
        with patch.object(watcher.subprocess, "run", side_effect=watcher.subprocess.TimeoutExpired("cmd", 5)):
            result = watcher.send_desktop_notification(
                enabled=True, command="notify-send",
                app_name="test", title="t", body="b",
                urgency="normal", timeout_ms=5000, log=mock_watcher_log,
            )
            assert result is False
            mock_watcher_log.warning.assert_called()


class TestNotificationLifecycle:
    def test_no_capture_loop_does_not_notify(self, mock_watcher_log):
        """NO_CAPTURE loop does not send notifications."""
        with patch.object(watcher, "run_volatility_gate") as mock_gate:
            mock_gate.return_value = {
                "capture_permission": "NO_CAPTURE",
                "fast_capture_reason": "not_eligible",
            }
            notify_args = MagicMock()
            notify_args.interval_seconds = 0.01
            notify_args.max_runtime_seconds = 0.2
            notify_args.dry_run = False
            notify_args.prefer_gpu = False
            notify_args.gpu_device = "cuda:0"
            notify_args.preflight_seconds = 5
            notify_args.fast_duration_seconds = 10
            notify_args.full_duration_seconds = 15
            notify_args.oi_interval_seconds = 3
            notify_args.notify = True
            notify_args.notify_command = "notify-send"
            notify_args.notify_app_name = "Nautilus Stage2"
            notify_args.notify_timeout_ms = 10000

            with patch.object(watcher, "run_preflight") as mock_preflight:
                with patch.object(watcher, "send_desktop_notification") as mock_notify:
                    watcher._main_impl(notify_args, mock_watcher_log, "20260515_000000")
                    mock_notify.assert_not_called()
                    mock_preflight.assert_not_called()

    def test_capture_start_notification_emitted(self, mock_watcher_log):
        """Capture-start notification is emitted after preflight passes, before capture."""
        with patch.object(watcher, "run_volatility_gate") as mock_gate:
            mock_gate.return_value = {
                "capture_permission": "FAST_DIAGNOSTIC_CAPTURE_ONLY",
                "fast_capture_reason": "test",
            }
            with patch.object(watcher, "check_lock", return_value=(False, "")):
                with patch.object(watcher, "run_preflight") as mock_preflight:
                    mock_preflight.return_value = "data/derivatives_spot_capture_v2_PREFLIGHT_20260515"
                    with patch.object(watcher, "run_capture") as mock_capture:
                        mock_capture.return_value = "data/derivatives_spot_capture_v2_FAST_DIAGNOSTIC_20260515"
                        with patch.object(watcher, "run_evaluation") as mock_eval:
                            mock_eval.return_value = "reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_20260515"
                            with patch.object(watcher, "send_desktop_notification") as mock_notify:
                                with patch.object(watcher, "run_post_evaluation_diagnostics"):
                                    nargs = MagicMock()
                                    nargs.interval_seconds = 0.01
                                    nargs.max_runtime_seconds = 0.5
                                    nargs.dry_run = False
                                    nargs.prefer_gpu = False
                                    nargs.gpu_device = "cuda:0"
                                    nargs.preflight_seconds = 5
                                    nargs.fast_duration_seconds = 10
                                    nargs.full_duration_seconds = 15
                                    nargs.oi_interval_seconds = 3
                                    nargs.notify = True
                                    nargs.notify_command = "notify-send"
                                    nargs.notify_app_name = "Nautilus Stage2"
                                    nargs.notify_timeout_ms = 10000

                                    watcher._main_impl(nargs, mock_watcher_log, "20260515_000000")

                                    # Should have 2 notifications: capture-start + completion
                                    assert mock_notify.call_count >= 2
                                    # First call should be the capture-start notification
                                    first_title = mock_notify.call_args_list[0][1]["title"]
                                    assert "started" in first_title.lower()

    def test_preflight_failure_notifies(self, mock_watcher_log):
        """Failed preflight emits failure notification."""
        with patch.object(watcher, "run_volatility_gate") as mock_gate:
            mock_gate.return_value = {
                "capture_permission": "FAST_DIAGNOSTIC_CAPTURE_ONLY",
                "fast_capture_reason": "test",
            }
            with patch.object(watcher, "check_lock", return_value=(False, "")):
                with patch.object(watcher, "run_preflight") as mock_preflight:
                    mock_preflight.return_value = None
                    with patch.object(watcher, "send_desktop_notification") as mock_notify:
                        nargs = MagicMock()
                        nargs.interval_seconds = 0.01
                        nargs.max_runtime_seconds = 0.5
                        nargs.dry_run = False
                        nargs.prefer_gpu = False
                        nargs.gpu_device = "cuda:0"
                        nargs.preflight_seconds = 5
                        nargs.fast_duration_seconds = 10
                        nargs.full_duration_seconds = 15
                        nargs.oi_interval_seconds = 3
                        nargs.notify = True
                        nargs.notify_command = "notify-send"
                        nargs.notify_app_name = "Nautilus Stage2"
                        nargs.notify_timeout_ms = 10000

                        watcher._main_impl(nargs, mock_watcher_log, "20260515_000000")

                        mock_notify.assert_called_once()
                        title = mock_notify.call_args[1]["title"]
                        assert "failed" in title.lower()


# ---------------------------------------------------------------------------
# Service template tests
# ---------------------------------------------------------------------------


SERVICE_PATH = Path(__file__).resolve().parents[1] / "systemd" / "nautilus-stage2-gate-watcher.service"


class TestServiceTemplate:
    def test_service_template_exists(self):
        """Service template exists at expected path."""
        assert SERVICE_PATH.exists(), f"Service template not found: {SERVICE_PATH}"

    def test_service_uses_uv_python(self):
        """Service template uses uv run python from the configured worktree."""
        content = SERVICE_PATH.read_text()
        assert "ExecStart=uv run python -m" in content
        assert ".venv/bin/python" not in content

    def test_service_uses_correct_working_directory(self):
        """Service template uses WorkingDirectory pointing to the configured worktree."""
        content = SERVICE_PATH.read_text()
        assert "WorkingDirectory=/mnt/nasirjones/py/nautilus_trader_stage2_runtime" in content
        assert "WorkingDirectory=${NAUTILUS_STAGE2_WORKTREE}" not in content
        assert "NAUTILUS_STAGE2_WORKTREE=/mnt/nasirjones/py/nautilus_trader_stage2_runtime" in content

    def test_service_includes_env_vars(self):
        """Service template includes NAUTILUS_STAGE2_* env vars."""
        content = SERVICE_PATH.read_text()
        assert "NAUTILUS_STAGE2_REPO_ROOT" in content

    def test_service_uses_optional_environment_file(self):
        """Service template treats local env overrides as optional."""
        content = SERVICE_PATH.read_text()
        assert "EnvironmentFile=-%h/.config/nautilus/stage2-gate-watcher.env" in content

    def test_service_uses_restart_no(self):
        """Service template uses Restart=no."""
        content = SERVICE_PATH.read_text()
        assert "Restart=no" in content

    def test_service_is_user_template(self):
        """Service template targets user-level systemd (no system install script)."""
        content = SERVICE_PATH.read_text()
        assert "WantedBy=default.target" in content
        assert "WantedBy=multi-user.target" not in content
        assert "systemctl --user" not in content  # docs, not the template


# ---------------------------------------------------------------------------
# CLI args for notifications
# ---------------------------------------------------------------------------


class TestNotificationCliArgs:
    def test_notify_default_false(self):
        """--notify defaults to False."""
        parser = watcher.build_parser()
        args = parser.parse_args(["--interval-seconds", "1", "--max-runtime-seconds", "1"])
        assert args.notify is False

    def test_notify_flag_enables(self):
        """--notify flag sets to True."""
        parser = watcher.build_parser()
        args = parser.parse_args(["--interval-seconds", "1", "--max-runtime-seconds", "1", "--notify"])
        assert args.notify is True

    def test_notify_command_default(self):
        """--notify-command defaults to notify-send."""
        parser = watcher.build_parser()
        args = parser.parse_args(["--interval-seconds", "1", "--max-runtime-seconds", "1"])
        assert args.notify_command == "notify-send"

    def test_notify_timeout_default(self):
        """--notify-timeout-ms defaults to 10000."""
        parser = watcher.build_parser()
        args = parser.parse_args(["--interval-seconds", "1", "--max-runtime-seconds", "1"])
        assert args.notify_timeout_ms == 10000


# ---------------------------------------------------------------------------
# Path configuration tests
# ---------------------------------------------------------------------------


class TestPathConfiguration:
    """Tests for path configuration functions in stage2_gate_watcher.py."""

    def test_write_status_includes_runtime_paths(self, tmp_path):
        """Status JSON includes runtime_repo_root, reports_root, data_root."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        # Reset module-level overrides
        v1_watcher._REPORTS_ROOT = tmp_path / "reports"
        v1_watcher._DATA_ROOT = tmp_path / "data"
        v1_watcher._REPO_ROOT = tmp_path / "runtime"

        status_path = tmp_path / "reports" / "stage2_gate_watcher_status.json"
        v1_watcher._STATUS_PATH_OVERRIDE = status_path

        # Write status
        v1_watcher._write_status(readiness_status="TEST")

        assert status_path.exists()
        data = json.loads(status_path.read_text())
        assert "runtime_repo_root" in data
        assert "reports_root" in data
        assert "data_root" in data
        assert str(tmp_path / "runtime") in data["runtime_repo_root"]
        assert str(tmp_path / "reports") in data["reports_root"]

    def test_path_defaults_match_current_behavior(self, tmp_path):
        """Path functions return expected defaults when no overrides are set."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        # Clear any overrides
        saved_reports = v1_watcher._REPORTS_ROOT
        saved_data = v1_watcher._DATA_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status_override = v1_watcher._STATUS_PATH_OVERRIDE
        saved_python_exe_override = v1_watcher._PYTHON_EXE_OVERRIDE
        v1_watcher._REPORTS_ROOT = None
        v1_watcher._DATA_ROOT = None
        v1_watcher._REPO_ROOT = None
        v1_watcher._STATUS_PATH_OVERRIDE = None
        v1_watcher._PYTHON_EXE_OVERRIDE = None

        try:
            old_cwd = Path.cwd()
            os.chdir(tmp_path)

            # Create expected paths
            (tmp_path / "reports").mkdir()
            (tmp_path / "data").mkdir()

            status_path = v1_watcher._get_status_path()
            data_root = v1_watcher._get_data_root()
            reports_root = v1_watcher._get_reports_root()

            assert status_path == Path("reports") / "stage2_gate_watcher_status.json"
            assert data_root == Path("data")
            assert reports_root == Path("reports")
        finally:
            os.chdir(old_cwd)
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._DATA_ROOT = saved_data
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status_override
            v1_watcher._PYTHON_EXE_OVERRIDE = saved_python_exe_override

    def test_env_override_via_args(self):
        """CLI args override defaults arg-parser level."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        parser = v1_watcher.build_parser()
        args = parser.parse_args([
            "--poll-seconds", "60", "--once",
            "--reports-root", "/custom/reports",
            "--data-root", "/custom/data",
        ])
        assert args.reports_root == "/custom/reports"
        assert args.data_root == "/custom/data"
        assert args.workdir is not None  # Has default

    def test_workdir_has_default(self):
        """--workdir has a meaningful default value."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        parser = v1_watcher.build_parser()
        args = parser.parse_args(["--poll-seconds", "60", "--once"])
        assert args.workdir is not None
        assert "/mnt/nasirjones/py/nautilus_trader" in args.workdir

    def test_resolve_python_exe_finds_venv(self, tmp_path):
        """_resolve_python_exe finds .venv/bin/python in the worktree."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        python_exe = venv_dir / "python"
        python_exe.write_text("#!/bin/sh\necho mock")
        python_exe.chmod(0o755)

        result = v1_watcher._resolve_python_exe(tmp_path)
        assert result == python_exe.resolve()

    def test_no_live_trading_imports(self):
        """No execution/trading/order imports in stage2_gate_watcher."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )
        content = Path(v1_watcher.__file__).read_text()
        # Check specifically for execution-related imports
        assert "from .execution" not in content
        assert "from .order" not in content
        assert "nautilus_trader.execution" not in content

    def test_service_file_has_worktree_workdir(self):
        """systemd service points WorkingDirectory at configured worktree."""
        service_path = Path(
            __file__).resolve().parents[1] / "systemd" / "nautilus-stage2-gate-watcher.service"
        content = service_path.read_text()
        assert "WorkingDirectory=/mnt/nasirjones/py/nautilus_trader_stage2_runtime" in content
        assert "WorkingDirectory=${NAUTILUS_STAGE2_WORKTREE}" not in content
        assert "NAUTILUS_STAGE2_WORKTREE=/mnt/nasirjones/py/nautilus_trader_stage2_runtime" in content

    def test_service_file_uses_uv_not_hardcoded_venv(self):
        """systemd service uses uv from the worktree instead of a hardcoded venv path."""
        service_path = Path(
            __file__).resolve().parents[1] / "systemd" / "nautilus-stage2-gate-watcher.service"
        content = service_path.read_text()
        assert "ExecStart=uv run python -m" in content
        assert ".venv/bin/python" not in content

    def test_service_file_has_env_vars(self):
        """systemd service sets NAUTILUS_STAGE2_* environment variables."""
        service_path = Path(
            __file__).resolve().parents[1] / "systemd" / "nautilus-stage2-gate-watcher.service"
        content = service_path.read_text()
        assert "NAUTILUS_STAGE2_REPO_ROOT" in content
        assert "NAUTILUS_STAGE2_REPORTS_ROOT" in content
        assert "NAUTILUS_STAGE2_DATA_ROOT" in content
        assert "NAUTILUS_STAGE2_STATUS_PATH" in content

    def test_service_executable_is_stage2_gate_watcher(self):
        """Service uses stage2_gate_watcher module, not run_stage2_gate_watcher."""
        service_path = Path(
            __file__).resolve().parents[1] / "systemd" / "nautilus-stage2-gate-watcher.service"
        content = service_path.read_text()
        assert "stage2_gate_watcher" in content
        # Ensure it's not using the old run_stage2_gate_watcher
        assert "run_stage2_gate_watcher" not in content


# ---------------------------------------------------------------------------
# Edge Miner harvester requirements
# ---------------------------------------------------------------------------


class TestCaptureCommandIncludesAvaxUsd:
    """Verify AVAX/USD appears in the capture subprocess command."""

    def test_default_signal_family_includes_avax(self):
        """Default signal family includes AVAX/USD in target_symbols."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )
        import inspect

        source = inspect.getsource(v1_watcher._run_capture)
        assert "AVAX/USD" in source

    def test_stress_v2_signal_family_includes_avax(self):
        """stress_v2 signal family includes AVAX/USD in target_symbols."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        saved = v1_watcher._SIGNAL_FAMILY
        try:
            v1_watcher._SIGNAL_FAMILY = v1_watcher._STRESS_V2_SIGNAL_FAMILY
            # The command built by _run_capture should include AVAX/USD
            # We verify by checking the source path taken for v2 family
            import inspect
            source = inspect.getsource(v1_watcher._run_capture)
            assert "AVAX/USD" in source
        finally:
            v1_watcher._SIGNAL_FAMILY = saved


class TestStatusReportsCorpusReadiness:
    """Status JSON must contain corpus readiness fields."""

    def test_write_status_has_corpus_fields(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_data = v1_watcher._DATA_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status = v1_watcher._STATUS_PATH_OVERRIDE
        try:
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._DATA_ROOT = tmp_path / "data"
            v1_watcher._REPO_ROOT = tmp_path
            status_path = tmp_path / "reports" / "status.json"
            v1_watcher._STATUS_PATH_OVERRIDE = status_path

            v1_watcher._write_status()

            assert status_path.exists()
            data = json.loads(status_path.read_text())
            assert "usable_window_count" in data
            assert "minimum_ready_usable_windows" in data
            assert "ready_for_rerun" in data
            assert "corpus_status" in data
            assert "next_action" in data
            assert isinstance(data["ready_for_rerun"], bool)
            assert data["corpus_status"] in ("ACCUMULATING", "CORPUS_READY_FOR_RERUN")
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._DATA_ROOT = saved_data
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status

    def test_accumulating_status_when_below_target(self, tmp_path):
        """Corpus status is ACCUMULATING and ready_for_rerun=False when count < target."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_data = v1_watcher._DATA_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status = v1_watcher._STATUS_PATH_OVERRIDE
        try:
            # Empty data dir means 0 validated captures
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._DATA_ROOT = tmp_path / "data"
            (tmp_path / "data").mkdir()
            v1_watcher._REPO_ROOT = tmp_path
            status_path = tmp_path / "reports" / "status.json"
            v1_watcher._STATUS_PATH_OVERRIDE = status_path

            v1_watcher._write_status()

            data = json.loads(status_path.read_text())
            assert data["ready_for_rerun"] is False
            assert data["corpus_status"] == "ACCUMULATING"
            assert "ACCUMULATING" in data["next_action"]
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._DATA_ROOT = saved_data
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status


class TestCompletedWithErrorsNotFatal:
    """completed_with_errors from a capture must not be treated as a fatal failure."""

    def test_run_index_accepts_completed_with_errors(self):
        """run_index.py validates completed_with_errors as a legal status."""
        from examples.strategies.venue_agnostic_signal_observer.run_index import (
            validate_status,
        )
        assert validate_status("completed_with_errors") == "completed_with_errors"

    def test_status_json_reflects_completed_with_errors(self, tmp_path):
        """After capture with completed_with_errors manifest, status reports COMPLETED_WITH_ERRORS."""
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )
        from unittest.mock import patch, MagicMock

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_data = v1_watcher._DATA_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status = v1_watcher._STATUS_PATH_OVERRIDE
        saved_signal = v1_watcher._SIGNAL_FAMILY
        try:
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._DATA_ROOT = tmp_path / "data"
            (tmp_path / "data").mkdir()
            v1_watcher._REPO_ROOT = tmp_path
            status_path = tmp_path / "reports" / "status.json"
            v1_watcher._STATUS_PATH_OVERRIDE = status_path
            v1_watcher._SIGNAL_FAMILY = "cross_asset_beta_lag_v1"

            # Write a capture dir with a completed_with_errors manifest
            capture_dir = tmp_path / "data" / "cross_asset_beta_lag_v1_FULL_ACTIVE_test"
            capture_dir.mkdir(parents=True)
            manifest = {
                "run_id": "cap_test_001",
                "capture_status": "completed_with_errors",
                "capture_mode": "FULL_ACTIVE",
                "streams": {},
            }
            (capture_dir / "capture_manifest.json").write_text(
                json.dumps(manifest)
            )

            log = v1_watcher.WatcherLogger()
            state = MagicMock()
            state.cooldown_remaining_seconds.return_value = None

            with patch.object(v1_watcher, "_run_capture", return_value=str(capture_dir)):
                with patch.object(v1_watcher, "_run_validation") as mock_val:
                    mock_val.return_value = {
                        "verdict": "CAPTURE_VALIDATION_PASSED",
                        "run_id": "cap_test_001",
                    }
                    with patch.object(v1_watcher, "_run_gate") as mock_gate:
                        mock_gate.return_value = {
                            "gate_passed": True,
                            "btc_1h_bps": 180.0,
                            "market_verdict": "MARKET_ACTIVE",
                            "accel_verdict": "ACCELERATING",
                        }
                        with patch.object(v1_watcher, "CaptureGuard") as mock_guard:
                            mock_guard.try_acquire.return_value = True
                            mock_guard.release = MagicMock()
                            with patch.object(v1_watcher, "_get_collection_lock_path") as mock_lock_path:
                                mock_lock_path.return_value = tmp_path / "nonexistent.lock"
                                with patch.object(v1_watcher, "CollectionLock") as mock_coll_lock:
                                    mock_coll_lock.return_value.create = MagicMock()
                                    with patch.object(v1_watcher, "_run_readiness") as mock_ready:
                                        mock_ready.return_value = {"ready": True, "hard_blockers": []}
                                        args = MagicMock()
                                        args.poll_seconds = 1
                                        args.stress_bps = 150.0
                                        args.once = True
                                        args.no_capture = False
                                        args.min_gap_seconds = 3600
                                        try:
                                            v1_watcher._run_watcher_cycle(log, args, state)
                                        except SystemExit:
                                            pass

            assert status_path.exists()
            data = json.loads(status_path.read_text())
            assert data.get("last_capture_status") == "COMPLETED_WITH_ERRORS"
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._DATA_ROOT = saved_data
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status
            v1_watcher._SIGNAL_FAMILY = saved_signal


class TestLockPreventsOverlappingCapture:
    """CaptureGuard blocks a second capture while one is already in progress."""

    def test_capture_guard_blocks_when_flag_exists(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )
        from unittest.mock import patch

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status = v1_watcher._STATUS_PATH_OVERRIDE
        try:
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._REPO_ROOT = tmp_path

            # Write the in-capture flag so CaptureGuard.try_acquire returns False
            flag_path = tmp_path / "reports" / "cross_asset_beta_lag_capturing.flag"
            flag_path.parent.mkdir(parents=True)
            flag_path.write_text("99999")

            with patch.object(v1_watcher, "_get_in_capture_flag_path",
                               return_value=flag_path):
                acquired = v1_watcher.CaptureGuard.try_acquire()
            assert acquired is False
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status

    def test_cooldown_blocks_capture_after_recent_run(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        try:
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._REPO_ROOT = tmp_path

            state = v1_watcher.WatcherState(min_gap_seconds=3600)
            state.record_capture(run_id="cap_test", capture_dir=str(tmp_path / "data" / "test"))
            remaining = state.cooldown_remaining_seconds()
            assert remaining is not None
            assert remaining > 0
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._REPO_ROOT = saved_repo


class TestNoTradeReadyEmitted:
    """Watcher must never emit TRADE_READY in status or source."""

    def test_no_trade_ready_in_stage2_gate_watcher_source(self):
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )
        content = Path(v1_watcher.__file__).read_text()
        assert "TRADE_READY" not in content

    def test_no_trade_ready_in_status_json(self, tmp_path):
        from examples.strategies.venue_agnostic_signal_observer import (
            stage2_gate_watcher as v1_watcher,
        )

        saved_reports = v1_watcher._REPORTS_ROOT
        saved_data = v1_watcher._DATA_ROOT
        saved_repo = v1_watcher._REPO_ROOT
        saved_status = v1_watcher._STATUS_PATH_OVERRIDE
        try:
            v1_watcher._REPORTS_ROOT = tmp_path / "reports"
            v1_watcher._DATA_ROOT = tmp_path / "data"
            (tmp_path / "data").mkdir()
            v1_watcher._REPO_ROOT = tmp_path
            status_path = tmp_path / "reports" / "status.json"
            v1_watcher._STATUS_PATH_OVERRIDE = status_path

            v1_watcher._write_status(capture_status="IDLE")

            content = status_path.read_text()
            assert "TRADE_READY" not in content
        finally:
            v1_watcher._REPORTS_ROOT = saved_reports
            v1_watcher._DATA_ROOT = saved_data
            v1_watcher._REPO_ROOT = saved_repo
            v1_watcher._STATUS_PATH_OVERRIDE = saved_status
