"""GPU device scheduler — local file-lock based acquisition.

Uses fcntl.flock on Linux for cross-process coordination.
Falls back gracefully at import time if fcntl is unavailable.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]


_CUDA_DEVICE_RE = re.compile(r"^cuda:\d+$")


def validate_device_syntax(devices: Sequence[str]) -> tuple[str, ...]:
    """Validate device syntax, returning canonical tuple.

    Accepts ``cuda:N`` format.  Raises ValueError for invalid syntax.
    """
    validated: list[str] = []
    for d in devices:
        if not _CUDA_DEVICE_RE.match(d):
            raise ValueError(f"Invalid device syntax: {d!r} (expected cuda:N)")
        validated.append(d)
    return tuple(validated)


def choose_devices(
    requested: tuple[str, ...],
    available: tuple[str, ...],
    max_devices: int,
) -> tuple[str, ...]:
    """Choose devices respecting request, availability, and cap.

    If *requested* is non-empty, validates and returns requested devices
    limited to *max_devices*.  Otherwise chooses from *available* up to
    *max_devices*.
    """
    if requested:
        validated = validate_device_syntax(requested)
        if len(validated) > max_devices:
            return validated[:max_devices]
        return validated

    chosen: list[str] = []
    for d in available:
        if len(chosen) >= max_devices:
            break
        if _CUDA_DEVICE_RE.match(d):
            chosen.append(d)
    return tuple(chosen)


def _lock_filename(device: str) -> str:
    """Convert cuda:N to cuda_N.lock."""
    return device.replace(":", "_") + ".lock"


@contextmanager
def acquire_gpu_devices(
    devices: tuple[str, ...],
    *,
    lock_dir: Path = Path("/tmp/va_signal_observer_gpu_locks"),
) -> Generator[tuple[str, ...], None, None]:
    """Acquire exclusive file locks for the given GPU devices.

    Acquires locks in sorted device order to avoid deadlock.
    Releases all locks on context manager exit.

    Raises RuntimeError if fcntl is unavailable at the time of first lock use.
    """
    if fcntl is None:
        raise RuntimeError(
            "fcntl module is unavailable on this platform. "
            "GPU device locking requires fcntl (POSIX)."
        )

    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)

    sorted_devices = tuple(sorted(devices))
    lock_files: list[tuple[str, int]] = []  # (device, fd)

    try:
        for device in sorted_devices:
            lock_path = lock_dir / _lock_filename(device)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            lock_files.append((device, fd))
        yield sorted_devices
    finally:
        for device, fd in reversed(lock_files):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass