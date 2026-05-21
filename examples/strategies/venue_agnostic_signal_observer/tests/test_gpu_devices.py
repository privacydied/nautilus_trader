"""
Tests for the shared multi-GPU device helpers.

Pure unit tests — no CUDA required. Validates parsing, splitting,
seed derivation, and benchmark metadata shape.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.gpu_devices import benchmark_metadata
from examples.strategies.venue_agnostic_signal_observer.gpu_devices import derive_per_device_seeds
from examples.strategies.venue_agnostic_signal_observer.gpu_devices import parse_cuda_devices
from examples.strategies.venue_agnostic_signal_observer.gpu_devices import split_work_evenly
from examples.strategies.venue_agnostic_signal_observer.gpu_devices import validate_cuda_devices


# ---------------------------------------------------------------------------
# parse_cuda_devices
# ---------------------------------------------------------------------------


class TestParseCudaDevices:
    def test_cpu_engine_returns_empty(self):
        assert parse_cuda_devices("cuda:0,cuda:1", "cuda:0", engine="cpu") == []

    def test_none_returns_fallback(self):
        assert parse_cuda_devices(None, "cuda:0", engine="gpu") == ["cuda:0"]

    def test_empty_returns_fallback(self):
        assert parse_cuda_devices("", "cuda:0", engine="gpu") == ["cuda:0"]
        assert parse_cuda_devices("   ", "cuda:0", engine="gpu") == ["cuda:0"]

    def test_single_device(self):
        assert parse_cuda_devices("cuda:0", "cuda:0", engine="gpu") == ["cuda:0"]

    def test_multi_device(self):
        assert parse_cuda_devices("cuda:0,cuda:1", "cuda:0", engine="gpu") == [
            "cuda:0",
            "cuda:1",
        ]

    def test_whitespace_handling(self):
        assert parse_cuda_devices(" cuda:0 , cuda:1 ", "cuda:0", engine="gpu") == [
            "cuda:0",
            "cuda:1",
        ]

    def test_duplicate_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            parse_cuda_devices("cuda:0,cuda:0", "cuda:0", engine="gpu")

    def test_invalid_string_rejected(self):
        with pytest.raises(ValueError, match="invalid device string"):
            parse_cuda_devices("cpu", "cuda:0", engine="gpu")
        with pytest.raises(ValueError, match="invalid device string"):
            parse_cuda_devices("cuda:0,gpu1", "cuda:0", engine="gpu")


# ---------------------------------------------------------------------------
# validate_cuda_devices
# ---------------------------------------------------------------------------


class TestValidateCudaDevices:
    def test_empty_list_fails(self):
        ok, reason = validate_cuda_devices([])
        assert not ok
        assert reason == "no_devices_requested"

    def test_no_torch_or_cuda_returns_clean_reason(self):
        # On a CPU-only host this will hit either "torch_not_installed" or
        # "torch_cuda_unavailable" — both are acceptable clean failures
        # (NOT a hidden CPU fallback).
        ok, reason = validate_cuda_devices(["cuda:0"])
        if not ok:
            assert reason in {"torch_not_installed", "torch_cuda_unavailable"} or reason.startswith(
                "cuda_device_not_found:"
            )


# ---------------------------------------------------------------------------
# split_work_evenly
# ---------------------------------------------------------------------------


class TestSplitWorkEvenly:
    def test_even_split(self):
        ranges = split_work_evenly(10, 2)
        assert [(r.start, r.stop) for r in ranges] == [(0, 5), (5, 10)]

    def test_uneven_split_remainder_goes_to_front(self):
        ranges = split_work_evenly(10, 3)
        # 4 + 3 + 3
        assert [(r.start, r.stop) for r in ranges] == [(0, 4), (4, 7), (7, 10)]

    def test_single_device(self):
        ranges = split_work_evenly(100, 1)
        assert ranges == [range(100)]

    def test_more_devices_than_items(self):
        ranges = split_work_evenly(2, 4)
        # First two get one each, last two are empty.
        sizes = [len(r) for r in ranges]
        assert sizes == [1, 1, 0, 0]
        assert sum(sizes) == 2

    def test_zero_items(self):
        ranges = split_work_evenly(0, 3)
        assert all(len(r) == 0 for r in ranges)

    def test_covers_every_item_exactly_once(self):
        # Property-style: across many shapes, the ranges partition [0, n).
        for n in [0, 1, 7, 10, 100, 1000, 10001]:
            for d in [1, 2, 3, 4, 7, 8]:
                ranges = split_work_evenly(n, d)
                seen = []
                for r in ranges:
                    seen.extend(list(r))
                assert seen == list(range(n)), f"failed for n={n} d={d}"

    def test_zero_devices_rejected(self):
        with pytest.raises(ValueError):
            split_work_evenly(10, 0)

    def test_negative_items_rejected(self):
        with pytest.raises(ValueError):
            split_work_evenly(-1, 2)


# ---------------------------------------------------------------------------
# derive_per_device_seeds
# ---------------------------------------------------------------------------


class TestDerivePerDeviceSeeds:
    def test_deterministic(self):
        a = derive_per_device_seeds(42, 4)
        b = derive_per_device_seeds(42, 4)
        assert a == b

    def test_distinct_per_device(self):
        seeds = derive_per_device_seeds(42, 8)
        assert len(set(seeds)) == 8, "per-device seeds should be distinct"

    def test_changes_with_base_seed(self):
        a = derive_per_device_seeds(42, 4)
        b = derive_per_device_seeds(43, 4)
        assert a != b

    def test_changes_with_n_devices(self):
        # First two seeds for n=2 should NOT equal first two for n=4
        # because the derivation hashes (base, index, n) — actually we hash
        # only (base, index), so first-N are stable. That's an explicit
        # design choice — assert it so it doesn't drift.
        a = derive_per_device_seeds(42, 2)
        b = derive_per_device_seeds(42, 4)
        assert a == b[:2]  # prefix-stable: device i always gets the same seed

    def test_seeds_within_64bit(self):
        seeds = derive_per_device_seeds(42, 4)
        for s in seeds:
            assert 0 <= s < 2**64

    def test_zero_devices_rejected(self):
        with pytest.raises(ValueError):
            derive_per_device_seeds(42, 0)


# ---------------------------------------------------------------------------
# benchmark_metadata
# ---------------------------------------------------------------------------


def test_benchmark_metadata_shape():
    md = benchmark_metadata(
        engine="gpu",
        devices=["cuda:0", "cuda:1"],
        workload={"num_events": 1000, "num_horizons": 4},
        elapsed_seconds=1.234,
        per_device_shards=[500, 500],
        batch_size=8192,
    )
    assert md["engine"] == "gpu"
    assert md["devices"] == ["cuda:0", "cuda:1"]
    assert md["num_devices"] == 2
    assert md["batch_size"] == 8192
    assert md["workload"] == {"num_events": 1000, "num_horizons": 4}
    assert md["elapsed_seconds"] == 1.234
    assert md["per_device_shards"] == [500, 500]
    assert md["diagnostic_only"] is True
    assert md["safety_mode"] == "public_data_observer_only"


# ---------------------------------------------------------------------------
# Safety scan
# ---------------------------------------------------------------------------


def test_no_auth_or_order_strings_in_gpu_devices():
    src = Path(__file__).parent.parent / "gpu_devices.py"
    text = src.read_text()
    forbidden = [
        "api" + "_key", "API" + "_KEY",
        "secret" + "_key",
        "private" + "_key",
        "place" + "_order", "submit" + "_order", "create" + "_order",
        "pass" + "phrase",
        "Authoriz" + "ation",
    ]
    found = [p for p in forbidden if re.search(rf"\b{re.escape(p)}\b", text)]
    assert not found, f"forbidden strings present: {found}"
