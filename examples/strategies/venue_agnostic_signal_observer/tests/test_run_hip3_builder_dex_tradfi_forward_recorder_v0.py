"""Tests for the forward recorder CLI runner."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 import main


class TestRunnerCLI:
    """CLI runner tests."""

    def test_dry_run_writes_preview(self):
        """--dry-run should write dry_run_preview.json and exit without network calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--symbols', 'TSLA,AAPL',
                '--dry-run',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_DRY_RUN_READY", "dry_run": True, "run_dir": tmpdir}
                    main()

                    mock_run.assert_called_once()
                    # Check that config was created with dry_run=True
                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.dry_run is True
                    assert config.symbols == ["TSLA", "AAPL"]

    def test_stop_after_init(self):
        """--stop-after-init should write manifest/status and exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--symbols', 'TSLA',
                '--stop-after-init',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_SYMBOLS_RESOLVED", "run_dir": tmpdir}
                    main()

                    mock_run.assert_called_once()
                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.stop_after_init is True
                    assert config.symbols == ["TSLA"]

    def test_once_mode(self):
        """--once should perform exactly one capture poll."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--symbols', 'TSLA,AAPL,MSFT,NVDA',
                '--once',
                '--allow-network-public',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_CAPTURE_COMPLETE", "run_dir": tmpdir}
                    main()

                    mock_run.assert_called_once()
                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.once is True
                    assert config.allow_network_public is True

    def test_include_secondary_symbols(self):
        """--include-secondary-symbols should add secondary symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--symbols', 'TSLA',
                '--include-secondary-symbols',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_READY", "run_dir": tmpdir}
                    main()

                    mock_run.assert_called_once()
                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert "TSLA" in config.symbols
                    assert "AMZN" in config.symbols
                    assert "GOOG" in config.symbols

    def test_summarize_mode(self):
        """--summarize should call summarize_forward_capture."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal report dir
            run_dir = Path(tmpdir) / "test_run"
            run_dir.mkdir()
            manifest = {"run_id": "test_run", "symbols_resolved": ["TSLA"], "api_symbols": ["cash:TSLA"]}
            (run_dir / "run_manifest.json").write_text(json.dumps(manifest))
            status = {"run_id": "test_run", "total_polls": 0, "total_errors": 0}
            (run_dir / "capture_status.json").write_text(json.dumps(status))

            with patch.object(sys, 'argv', [
                'run_recorder',
                '--summarize', str(run_dir),
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.summarize_forward_capture') as mock_sum:
                    mock_sum.return_value = {"final_status": "FORWARD_DATA_UNDERPOWERED"}
                    main()

                    mock_sum.assert_called_once_with(str(run_dir))

    def test_default_symbols(self):
        """Default symbols should be TSLA,AAPL,MSFT,NVDA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--dry-run',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_DRY_RUN_READY", "run_dir": tmpdir}
                    main()

                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.symbols == ["TSLA", "AAPL", "MSFT", "NVDA"]

    def test_poll_seconds_config(self):
        """--poll-seconds should set config.poll_seconds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--poll-seconds', '30',
                '--dry-run',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_DRY_RUN_READY", "run_dir": tmpdir}
                    main()

                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.poll_seconds == 30

    def test_duration_minutes_config(self):
        """--duration-minutes should set config.duration_minutes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--duration-minutes', '60',
                '--dry-run',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_DRY_RUN_READY", "run_dir": tmpdir}
                    main()

                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.duration_minutes == 60

    def test_enable_anchors_config(self):
        """--enable-anchors should set config.enable_anchors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, 'argv', [
                'run_recorder',
                '--out-root', tmpdir,
                '--enable-anchors',
                '--anchor-source', 'yahoo',
                '--dry-run',
            ]):
                with patch('examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0.run_forward_recorder') as mock_run:
                    mock_run.return_value = {"status": "HIP3_FORWARD_RECORDER_DRY_RUN_READY", "run_dir": tmpdir}
                    main()

                    call_kwargs = mock_run.call_args
                    config = call_kwargs[0][0]
                    assert config.enable_anchors is True
                    assert config.anchor_source == "yahoo"
