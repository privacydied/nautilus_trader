"""Tests for the GPU scheduler module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..conductor.gpu_scheduler import (
    acquire_gpu_devices,
    choose_devices,
    validate_device_syntax,
)


def _fcntl_available() -> bool:
    try:
        import fcntl  # noqa: F401

        return True
    except ImportError:
        return False


class TestValidateDeviceSyntax:
    def test_valid_cuda(self) -> None:
        result = validate_device_syntax(["cuda:0", "cuda:1"])
        assert result == ("cuda:0", "cuda:1")

    def test_invalid_syntax_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid device syntax"):
            validate_device_syntax(["gpu:0"])

    def test_empty_list(self) -> None:
        result = validate_device_syntax([])
        assert result == ()


class TestChooseDevices:
    def test_respects_requested(self) -> None:
        result = choose_devices(
            requested=("cuda:0", "cuda:1"),
            available=("cuda:0", "cuda:1", "cuda:2"),
            max_devices=2,
        )
        assert result == ("cuda:0", "cuda:1")

    def test_respects_max_devices(self) -> None:
        result = choose_devices(
            requested=("cuda:0", "cuda:1", "cuda:2"),
            available=("cuda:0", "cuda:1", "cuda:2"),
            max_devices=1,
        )
        assert result == ("cuda:0",)

    def test_no_requested_uses_available(self) -> None:
        result = choose_devices(
            requested=(),
            available=("cuda:0", "cuda:1", "cuda:2"),
            max_devices=2,
        )
        assert result == ("cuda:0", "cuda:1")

    def test_no_requested_respects_max(self) -> None:
        result = choose_devices(
            requested=(),
            available=("cuda:0", "cuda:1", "cuda:2"),
            max_devices=1,
        )
        assert result == ("cuda:0",)

    def test_empty_available(self) -> None:
        result = choose_devices(
            requested=(),
            available=(),
            max_devices=2,
        )
        assert result == ()


class TestAcquireGpuDevices:
    def test_lock_acquisition_and_release(self, tmp_path: Path) -> None:
        if not _fcntl_available():
            pytest.skip("fcntl not available on this platform")
        lock_dir = tmp_path / "gpu_locks"
        with acquire_gpu_devices(("cuda:0",), lock_dir=lock_dir) as acquired:
            assert acquired == ("cuda:0",)
            lock_file = lock_dir / "cuda_0.lock"
            assert lock_file.is_file()

    def test_multi_device_sorted_order(self, tmp_path: Path) -> None:
        if not _fcntl_available():
            pytest.skip("fcntl not available on this platform")
        lock_dir = tmp_path / "gpu_locks"
        with acquire_gpu_devices(
            ("cuda:1", "cuda:0"), lock_dir=lock_dir
        ) as acquired:
            assert acquired == ("cuda:0", "cuda:1")

    def test_deterministic_lock_filenames(self, tmp_path: Path) -> None:
        if not _fcntl_available():
            pytest.skip("fcntl not available on this platform")
        lock_dir = tmp_path / "gpu_locks"
        with acquire_gpu_devices(("cuda:0",), lock_dir=lock_dir):
            lock_file = lock_dir / "cuda_0.lock"
            assert lock_file.is_file()

    def test_import_does_not_fail_without_fcntl(self) -> None:
        from ..conductor import gpu_scheduler as gpu_mod

        assert gpu_mod is not None

    def test_non_lock_helpers_work_without_fcntl(self) -> None:
        from ..conductor import gpu_scheduler as gpu_mod

        result = gpu_mod.validate_device_syntax(["cuda:0"])
        assert result == ("cuda:0",)
        result2 = gpu_mod.choose_devices(
            requested=("cuda:0",),
            available=("cuda:0", "cuda:1"),
            max_devices=2,
        )
        assert result2 == ("cuda:0",)

    def test_lock_raises_if_fcntl_unavailable(self, tmp_path: Path) -> None:
        from ..conductor import gpu_scheduler as gpu_mod

        saved = gpu_mod.fcntl
        gpu_mod.fcntl = None
        try:
            with pytest.raises(RuntimeError, match="fcntl module is unavailable"):
                with gpu_mod.acquire_gpu_devices(
                    ("cuda:0",), lock_dir=tmp_path
                ):
                    pass
        finally:
            gpu_mod.fcntl = saved