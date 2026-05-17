"""CLI --device / --devices argument tests for the three GPU runner scripts.

No CUDA required. Tests parse, override, validation-failure, and CPU-mode
behaviour using only the argparse layer and gpu_devices helpers.

No auth, no orders, no network, no captures.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.gpu_devices import (
    parse_cuda_devices,
    validate_cuda_devices,
)
from examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag import (
    build_parser as build_forward_parser,
)
from examples.strategies.venue_agnostic_signal_observer.run_permutation_null import (
    build_parser as build_null_parser,
)
from examples.strategies.venue_agnostic_signal_observer.run_lead_lag_heatmap import (
    build_parser as build_heatmap_parser,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(parser, args_list):
    return parser.parse_args(args_list)


# ---------------------------------------------------------------------------
# run_derivatives_spot_lead_lag — --forward-device / --forward-devices
# ---------------------------------------------------------------------------

class TestForwardReturnsDeviceCLI:
    def test_default_single_device(self):
        args = _parse(build_forward_parser(), [])
        assert args.forward_device == "cuda:0"
        assert args.forward_devices == ""

    def test_legacy_device_flag(self):
        args = _parse(build_forward_parser(), ["--forward-device", "cuda:1"])
        assert args.forward_device == "cuda:1"
        assert args.forward_devices == ""

    def test_devices_flag_present(self):
        args = _parse(build_forward_parser(), ["--forward-devices", "cuda:0,cuda:1"])
        assert args.forward_devices == "cuda:0,cuda:1"

    def test_devices_overrides_device_via_parse(self):
        # When --forward-devices is set, parse_cuda_devices should use it
        args = _parse(build_forward_parser(), [
            "--forward-device", "cuda:1",
            "--forward-devices", "cuda:0,cuda:1",
            "--forward-engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.forward_devices, fallback_device=args.forward_device, engine="gpu")
        assert parsed == ["cuda:0", "cuda:1"]

    def test_single_devices_matches_device_behavior(self):
        args = _parse(build_forward_parser(), [
            "--forward-devices", "cuda:0",
            "--forward-engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.forward_devices, fallback_device=args.forward_device, engine="gpu")
        assert parsed == ["cuda:0"]

    def test_cpu_engine_devices_ignored_by_parse(self):
        args = _parse(build_forward_parser(), [
            "--forward-devices", "cuda:0,cuda:1",
            "--forward-engine", "cpu",
        ])
        result = parse_cuda_devices(args.forward_devices, fallback_device=args.forward_device, engine="cpu")
        assert result == []

    def test_invalid_device_string_raises(self):
        with pytest.raises(ValueError, match="invalid device string"):
            parse_cuda_devices("gpu0,cuda:1", fallback_device="cuda:0", engine="gpu")

    def test_duplicate_device_raises(self):
        with pytest.raises(ValueError, match="duplicate"):
            parse_cuda_devices("cuda:0,cuda:0", fallback_device="cuda:0", engine="gpu")


# ---------------------------------------------------------------------------
# run_permutation_null — --device / --devices
# ---------------------------------------------------------------------------

class TestPermutationNullDeviceCLI:
    def _required(self):
        return ["--capture-dir", "/tmp/cap", "--report-dir", "/tmp/rep"]

    def test_default_device(self):
        args = _parse(build_null_parser(), self._required())
        assert args.device == "cuda:0"
        assert args.devices == ""

    def test_legacy_device_flag(self):
        args = _parse(build_null_parser(), self._required() + ["--device", "cuda:1"])
        assert args.device == "cuda:1"

    def test_devices_flag_parses(self):
        args = _parse(build_null_parser(), self._required() + ["--devices", "cuda:0,cuda:1"])
        assert args.devices == "cuda:0,cuda:1"

    def test_devices_overrides_device(self):
        args = _parse(build_null_parser(), self._required() + [
            "--device", "cuda:1",
            "--devices", "cuda:0,cuda:1",
            "--engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.devices, fallback_device=args.device, engine="gpu")
        assert parsed == ["cuda:0", "cuda:1"]

    def test_single_devices_matches_device_behavior(self):
        args = _parse(build_null_parser(), self._required() + [
            "--devices", "cuda:0",
            "--engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.devices, fallback_device=args.device, engine="gpu")
        assert parsed == ["cuda:0"]

    def test_cpu_mode_devices_ignored(self):
        args = _parse(build_null_parser(), self._required() + [
            "--devices", "cuda:0,cuda:1",
            "--engine", "cpu",
        ])
        result = parse_cuda_devices(args.devices, fallback_device=args.device, engine="cpu")
        assert result == []

    def test_invalid_device_string_raises(self):
        with pytest.raises(ValueError, match="invalid device string"):
            parse_cuda_devices("nongpu", fallback_device="cuda:0", engine="gpu")

    def test_unavailable_device_clean_failure(self):
        ok, reason = validate_cuda_devices(["cuda:99"])
        if not ok:
            assert "cuda_device_not_found" in reason or reason in {
                "torch_not_installed", "torch_cuda_unavailable"
            }


# ---------------------------------------------------------------------------
# run_lead_lag_heatmap — --device / --devices
# ---------------------------------------------------------------------------

class TestLeadLagHeatmapDeviceCLI:
    def _required(self):
        return ["--capture-dir", "/tmp/cap", "--out", "/tmp/out"]

    def test_default_device(self):
        args = _parse(build_heatmap_parser(), self._required())
        assert args.device == "cuda:0"
        assert args.devices == ""

    def test_legacy_device_flag(self):
        args = _parse(build_heatmap_parser(), self._required() + ["--device", "cuda:1"])
        assert args.device == "cuda:1"

    def test_devices_flag_parses(self):
        args = _parse(build_heatmap_parser(), self._required() + ["--devices", "cuda:0,cuda:1"])
        assert args.devices == "cuda:0,cuda:1"

    def test_devices_overrides_device(self):
        args = _parse(build_heatmap_parser(), self._required() + [
            "--device", "cuda:1",
            "--devices", "cuda:0,cuda:1",
            "--engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.devices, fallback_device=args.device, engine="gpu")
        assert parsed == ["cuda:0", "cuda:1"]

    def test_single_devices_matches_device_behavior(self):
        args = _parse(build_heatmap_parser(), self._required() + [
            "--devices", "cuda:0",
            "--engine", "gpu",
        ])
        parsed = parse_cuda_devices(args.devices, fallback_device=args.device, engine="gpu")
        assert parsed == ["cuda:0"]

    def test_cpu_mode_devices_ignored(self):
        args = _parse(build_heatmap_parser(), self._required() + [
            "--devices", "cuda:0,cuda:1",
            "--engine", "cpu",
        ])
        result = parse_cuda_devices(args.devices, fallback_device=args.device, engine="cpu")
        assert result == []


# ---------------------------------------------------------------------------
# GPU_UNAVAILABLE_DIAGNOSTIC behaviour (CPU-only hosts)
# ---------------------------------------------------------------------------

class TestGpuUnavailableDiagnostic:
    def test_nonexistent_device_returns_clean_reason(self):
        ok, reason = validate_cuda_devices(["cuda:9"])
        if not ok:
            # Must NOT be a Python traceback — must be a clean diagnostic string
            assert isinstance(reason, str)
            assert "Traceback" not in reason

    def test_empty_devices_fails_cleanly(self):
        ok, reason = validate_cuda_devices([])
        assert not ok
        assert reason == "no_devices_requested"


# ---------------------------------------------------------------------------
# Safety: no auth/order strings in the runners
# ---------------------------------------------------------------------------

def test_no_auth_strings_in_runners():
    pkg = Path(__file__).parent.parent
    forbidden = [
        "api" + "_key", "API" + "_KEY",
        "secret" + "_key", "private" + "_key",
        "place" + "_order", "submit" + "_order", "create" + "_order",
        "pass" + "phrase", "Authoriz" + "ation",
    ]
    for fname in (
        "run_derivatives_spot_lead_lag.py",
        "run_permutation_null.py",
        "run_lead_lag_heatmap.py",
    ):
        text = (pkg / fname).read_text()
        found = [p for p in forbidden if re.search(rf"\b{re.escape(p)}\b", text)]
        assert not found, f"{fname}: forbidden strings present: {found}"
