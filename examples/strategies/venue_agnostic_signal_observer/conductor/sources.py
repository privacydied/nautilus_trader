"""Source pollers that emit ConductorJobSpec from local-only discovery.

No network calls.  No live captures.  No archive downloads.
"""

from __future__ import annotations

from pathlib import Path

from .models import (
    ConductorJobSpec,
    ConductorRunMode,
    ConductorSourceKind,
    derive_job_id,
)


class ExistingCaptureScanner:
    """Scan a data root for existing capture directories with capture_manifest.json.

    Emits one exploration ConductorJobSpec per capture.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        command: tuple[str, ...],
        reports_root: Path,
        default_min_events: int = 50,
        default_cost_floor_bps: float = 50.0,
        requested_devices: tuple[str, ...] = (),
    ) -> None:
        self._data_root = Path(data_root)
        self._command = command
        self._reports_root = Path(reports_root)
        self._default_min_events = default_min_events
        self._default_cost_floor_bps = default_cost_floor_bps
        self._requested_devices = requested_devices

    def scan(self) -> list[ConductorJobSpec]:
        """Scan and return exploration jobs for existing captures."""
        jobs: list[ConductorJobSpec] = []
        if not self._data_root.is_dir():
            return jobs

        for child in sorted(self._data_root.iterdir()):
            if not child.is_dir():
                continue
            manifest = child / "capture_manifest.json"
            if not manifest.is_file():
                continue
            capture_dir_name = child.name
            output_dir = (
                self._reports_root
                / "conductor"
                / "exploration"
                / "existing_capture"
                / capture_dir_name
            )
            signal_family = "existing_capture"
            study_id = capture_dir_name

            job_id = derive_job_id(
                signal_family=signal_family,
                study_id=study_id,
                command=self._command,
                capture_dir=str(child),
                run_mode=ConductorRunMode.EXPLORATION,
            )

            job = ConductorJobSpec(
                job_id=job_id,
                source_kind=ConductorSourceKind.EXISTING_CAPTURE,
                signal_family=signal_family,
                study_id=study_id,
                command=self._command,
                capture_dir=str(child),
                report_dir=str(self._reports_root),
                output_dir=str(output_dir),
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=self._default_min_events,
                cost_floor_bps=self._default_cost_floor_bps,
                structural_change_rationale=None,
                requested_devices=self._requested_devices,
                metadata={
                    "source": "existing_capture_scanner",
                    "capture_manifest": str(manifest),
                },
            )
            jobs.append(job)
        return jobs


class GateWatcherSignalScanner:
    """Read a local stage2 gate-watcher status JSON and emit exploration jobs.

    Keeps an in-memory seen set so repeated polls in the same process
    do not emit duplicates.
    """

    def __init__(
        self,
        *,
        status_path: Path,
        command: tuple[str, ...],
        reports_root: Path,
        default_min_events: int = 50,
        default_cost_floor_bps: float = 50.0,
        requested_devices: tuple[str, ...] = (),
    ) -> None:
        self._status_path = Path(status_path)
        self._command = command
        self._reports_root = Path(reports_root)
        self._default_min_events = default_min_events
        self._default_cost_floor_bps = default_cost_floor_bps
        self._requested_devices = requested_devices
        self._seen_capture_dirs: set[str] = set()

    def scan(self) -> list[ConductorJobSpec]:
        """Return exploration jobs for new gate-watcher-detected captures."""
        jobs: list[ConductorJobSpec] = []
        if not self._status_path.is_file():
            return jobs

        import json

        try:
            with open(self._status_path, "r") as f:
                status = json.load(f)
        except (json.JSONDecodeError, OSError):
            return jobs

        capture_dir_str = status.get("last_capture_dir")
        if not capture_dir_str:
            return jobs

        if capture_dir_str in self._seen_capture_dirs:
            return jobs

        self._seen_capture_dirs.add(capture_dir_str)

        output_dir = (
            self._reports_root
            / "conductor"
            / "exploration"
            / "gate_watcher_trigger"
            / Path(capture_dir_str).name
        )

        signal_family = "gate_watcher"
        study_id = Path(capture_dir_str).name

        job_id = derive_job_id(
            signal_family=signal_family,
            study_id=study_id,
            command=self._command,
            capture_dir=capture_dir_str,
            run_mode=ConductorRunMode.EXPLORATION,
        )

        job = ConductorJobSpec(
            job_id=job_id,
            source_kind=ConductorSourceKind.GATE_WATCHER_TRIGGER,
            signal_family=signal_family,
            study_id=study_id,
            command=self._command,
            capture_dir=capture_dir_str,
            report_dir=str(self._reports_root),
            output_dir=str(output_dir),
            run_mode=ConductorRunMode.EXPLORATION,
            min_events=self._default_min_events,
            cost_floor_bps=self._default_cost_floor_bps,
            structural_change_rationale=None,
            requested_devices=self._requested_devices,
            metadata={
                "source": "gate_watcher_scanner",
                "gate_watcher_status_path": str(self._status_path),
            },
        )
        jobs.append(job)
        return jobs


class ArchiveWindowIterator:
    """Read a local JSON file containing archive window specs.

    Emits one exploration job per archive window.
    Does not download archive data.
    Does not check remote endpoints.
    """

    def __init__(
        self,
        *,
        windows_path: Path,
        command: tuple[str, ...],
        reports_root: Path,
        default_min_events: int = 50,
        default_cost_floor_bps: float = 50.0,
        requested_devices: tuple[str, ...] = (),
    ) -> None:
        self._windows_path = Path(windows_path)
        self._command = command
        self._reports_root = Path(reports_root)
        self._default_min_events = default_min_events
        self._default_cost_floor_bps = default_cost_floor_bps
        self._requested_devices = requested_devices

    def scan(self) -> list[ConductorJobSpec]:
        """Return exploration jobs for each archive window."""
        jobs: list[ConductorJobSpec] = []
        if not self._windows_path.is_file():
            return jobs

        import json

        try:
            with open(self._windows_path, "r") as f:
                windows = json.load(f)
        except (json.JSONDecodeError, OSError):
            return jobs

        if not isinstance(windows, list):
            return jobs

        for idx, window in enumerate(windows):
            if not isinstance(window, dict):
                continue

            window_id = window.get("window_id", f"archive_window_{idx}")
            study_label = window.get("study_id", window_id)

            output_dir = (
                self._reports_root
                / "conductor"
                / "exploration"
                / "archive_window"
                / window_id
            )

            signal_family = "archive_window"
            study_id = study_label

            job_id = derive_job_id(
                signal_family=signal_family,
                study_id=study_id,
                command=self._command,
                capture_dir=None,
                run_mode=ConductorRunMode.EXPLORATION,
            )

            job = ConductorJobSpec(
                job_id=job_id,
                source_kind=ConductorSourceKind.ARCHIVE_WINDOW,
                signal_family=signal_family,
                study_id=study_id,
                command=self._command,
                capture_dir=None,
                report_dir=str(self._reports_root),
                output_dir=str(output_dir),
                run_mode=ConductorRunMode.EXPLORATION,
                min_events=self._default_min_events,
                cost_floor_bps=self._default_cost_floor_bps,
                structural_change_rationale=None,
                requested_devices=self._requested_devices,
                metadata={
                    "source": "archive_window_iterator",
                    "archive_window": window,
                    "window_index": idx,
                },
            )
            jobs.append(job)
        return jobs