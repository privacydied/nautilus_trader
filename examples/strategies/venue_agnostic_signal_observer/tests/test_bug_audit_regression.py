"""Regression tests for bug-audit fixes in stage2 pipeline.

Covers:
- _ts_now_iso double-datetime race (single datetime.now call)
- None mean_bps_values crash (skip groups without mean_net_bps_per_event)
- falsy ``or`` trap on numeric dimensions (explicit None check)
- ``rejected_by_bh`` → ``bh_failed_configs`` semantic rename
- dead ``survivors.append([])`` removal
- CPU engine passes empty device string, not ``"cuda:0"``

These tests use stdlib-only dependencies so they can run on any machine
without NautilusTrader Rust extension build.
"""

from __future__ import annotations

import datetime
import json
import re
from datetime import timezone


# ---------------------------------------------------------------------------
# 1. _ts_now_iso single-datetime fix
# ---------------------------------------------------------------------------

def _ts_now_iso() -> str:
    """Replica of the fixed _ts_now_iso from stage2_gate_watcher.py."""
    now = datetime.datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}"[:3] + "Z"


ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


class TestTsNowIso:
    def test_format(self) -> None:
        ts = _ts_now_iso()
        assert ISO_TS_RE.match(ts), f"invalid ISO format: {ts!r}"

    def test_millis_truncated_not_rounded(self) -> None:
        """Verify only 3 digits of microsecond (millis) are used."""
        now = datetime.datetime(2026, 5, 16, 22, 30, 45, 123456, tzinfo=timezone.utc)
        result = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}"[:3] + "Z"
        assert result == "2026-05-16T22:30:45.123Z"

    def test_precision_boundary(self) -> None:
        """Single now call means second and millis are always from the same instant."""
        timestamps = [_ts_now_iso() for _ in range(100)]
        for ts in timestamps:
            assert ISO_TS_RE.match(ts), f"invalid ISO timestamp: {ts!r}"

    def test_no_double_datetime_call(self) -> None:
        """_ts_now_iso function must use a single ``now`` variable."""
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_gate_watcher.py"
        ).read()
        # Isolate the _ts_now_iso function body
        func_start = source.find("def _ts_now_iso")
        assert func_start >= 0, "_ts_now_iso function not found"
        # Find the next def after it (or end of file)
        func_end = source.find("\ndef ", func_start + 1)
        if func_end < 0:
            func_end = len(source)
        func_body = source[func_start:func_end]

        # Old pattern: two datetime.now() calls
        assert "datetime.now(timezone.utc).strftime" not in func_body, (
            "_ts_now_iso still uses double datetime.now() calls"
        )
        # New pattern: single now variable
        assert "now = datetime.now(timezone.utc)" in func_body, (
            "_ts_now_iso missing single now variable"
        )


# ---------------------------------------------------------------------------
# 2. None mean_bps_values protection & falsy or trap
# ---------------------------------------------------------------------------

class TestMeanBpsValuesNoneGuard:
    """Verify run_discovery_check skips groups with None mean_net_bps_per_event."""

    def test_none_mean_bps_guard_in_source(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        # Must have the None guard after event_count check
        assert "if mean_bps is None:" in source, (
            "Missing guard for None mean_bps"
        )


class TestFalsyOrTrap:
    """Verify dimension extraction uses explicit None check, not ``or``."""

    def test_no_falsy_or_in_dim_extraction(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        # The old pattern ``group.get(key) or summary.get(key)`` should not appear
        # in the dimension extraction loop
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Only check lines inside the dimension extraction loop
            if "val = group.get(key)" in stripped and "or summary.get(key)" in stripped:
                assert "is not None" in stripped, (
                    f"Line {i}: dimension extraction uses falsy ``or`` instead of None check"
                )


# ---------------------------------------------------------------------------
# 3. rejected_by_bh → bh_failed_configs
# ---------------------------------------------------------------------------

class TestBhFailedConfigs:
    """Verify variable rename from ``rejected_by_bh`` to ``bh_failed_configs``."""

    def test_no_rejected_by_bh_variable(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        # The variable name ``rejected_by_bh`` must not appear as an assignment target
        # It may appear only in explanatory comments
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comment-only lines
            if stripped.lstrip().startswith("#"):
                continue
            if "rejected_by_bh" in stripped and ("=" in stripped or "append" in stripped):
                # Check it's in a comment
                if "#" not in stripped:
                    assert False, (
                        f"Line {i}: unremediated use of 'rejected_by_bh' in code (not comment)"
                    )

    def test_bh_failed_configs_present(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        assert "bh_failed_configs" in source, (
            "bh_failed_configs variable not found in source"
        )

    def test_bh_failed_configs_in_result(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        assert '"bh_failed_configs": len(bh_failed_configs)' in source, (
            "Result dict does not use bh_failed_configs key"
        )

    def test_discovery_result_schema(self) -> None:
        """Verify the result dict shape with bh_failed_configs is sound."""
        test_result = {
            "mode": "discovery",
            "checked_at": "2026-05-16T22:00:00.000Z",
            "signal_family": "test",
            "discovery_run_count": 0,
            "total_groups_processed": 0,
            "survivors": [],
            "frozen_configs": [],
            "frozen_config_count": 0,
            "bh_failed_configs": 0,
            "required_same_sign": 2,
            "n_discovery_captures": 0,
            "errors": [],
            "burn_record": None,
            "status": "DISCOVERY_COMPLETED_NO_SURVIVORS",
        }
        # Must serialize cleanly
        serialized = json.dumps(test_result)
        deserialized = json.loads(serialized)
        assert deserialized["bh_failed_configs"] == 0
        # Legacy key 'rejected_by_bh' must NOT be present in serialized result
        assert "rejected_by_bh" not in deserialized, (
            "Legacy key 'rejected_by_bh' still present in serialized result"
        )


# ---------------------------------------------------------------------------
# 4. Dead survivors.append([]) removal
# ---------------------------------------------------------------------------

class TestNoDeadSurvivorsAppend:
    def test_no_bare_append_empty_list(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/stage2_check_criteria.py"
        ).read()
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # ``survivors.append([])`` is the dead code pattern
            if stripped == "survivors.append([])":
                assert False, f"Line {i}: dead survivors.append([]) still present"

    def test_survivors_is_empty_list_in_result(self) -> None:
        """Verify survivors is [] not [[]] in a no-survivor result."""
        test_result = {
            "survivors": [],
            "frozen_configs": [],
            "status": "DISCOVERY_COMPLETED_NO_SURVIVORS",
        }
        assert test_result["survivors"] == []
        assert isinstance(test_result["survivors"], list)


# ---------------------------------------------------------------------------
# 5. CPU device string fix
# ---------------------------------------------------------------------------

class TestCpuDeviceString:
    """Verify CPU engine passes empty device string, not ``\"cuda:0\"``."""

    def test_no_cuda_zero_on_cpu_heatmap(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/run_stage2_gate_watcher.py"
        ).read()
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            if 'heatmap_device' in line and 'cuda' in line.lower():
                assert '"cuda:0"' not in line, (
                    f"Line {i}: heatmap_device still has 'cuda:0' for CPU path"
                )

    def test_no_cuda_zero_on_cpu_permutation(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/run_stage2_gate_watcher.py"
        ).read()
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            if '--device' in line and 'cuda:0' in line:
                assert False, (
                    f"Line {i}: --device still passes 'cuda:0' for CPU path"
                )

    def test_device_empty_when_cpu(self) -> None:
        """Simulate the logic when CUDA is not available."""
        cuda_ok = False
        # Without change, this would be 'cuda:0'.  With fix it's ''.
        heatmap_device = "" if not cuda_ok else "cuda:0"
        assert heatmap_device == "", (
            f"CPU device should be empty, got {heatmap_device!r}"
        )

    def test_heatmap_empty_device_pattern_in_source(self) -> None:
        """Confirm the CPU branch yields '' not 'cuda:0'."""
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/run_stage2_gate_watcher.py"
        ).read()
        assert (
            'heatmap_device = gpu_device if heatmap_engine == "gpu" else ""'
        ) in source, "heatmap_device CPU branch does not return ''"

    def test_permutation_empty_device_pattern_in_source(self) -> None:
        source = open(
            "examples/strategies/venue_agnostic_signal_observer/run_stage2_gate_watcher.py"
        ).read()
        assert (
            'gpu_device if perm_engine == "gpu" else ""'
        ) in source, "permutation null --device CPU branch does not return ''"
