"""Tests for the runner metadata registry CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.registry_cli import (
    build_parser,
    filter_specs,
    format_json_payload,
    format_runner_detail_text,
    format_runner_list_text,
    main,
    runner_spec_to_dict,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]

_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
_COST_FEASIBILITY_KEY = "hyperliquid_cost_feasibility"
_HEAVY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
)
_COST_FEASIBILITY_HEAVY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.runner"
)


# ---------------------------------------------------------------------------
# runner_spec_to_dict
# ---------------------------------------------------------------------------


class TestRunnerSpecToDict:
    def test_contains_all_expected_fields(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        d = runner_spec_to_dict(NODE_FILLS_LIQ_RECONSTRUCTION)
        expected_keys = {
            "key",
            "family",
            "venue",
            "study_id",
            "description",
            "cli_module",
            "implementation_module",
            "package_module",
            "legacy_module",
            "tags",
        }
        assert set(d.keys()) == expected_keys

    def test_deterministic_output(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        d1 = runner_spec_to_dict(NODE_FILLS_LIQ_RECONSTRUCTION)
        d2 = runner_spec_to_dict(NODE_FILLS_LIQ_RECONSTRUCTION)
        assert d1 == d2
        assert list(d1.keys()) == list(d2.keys())

    def test_tags_is_tuple(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        d = runner_spec_to_dict(NODE_FILLS_LIQ_RECONSTRUCTION)
        assert isinstance(d["tags"], tuple)


# ---------------------------------------------------------------------------
# filter_specs
# ---------------------------------------------------------------------------


class TestFilterSpecs:
    def test_no_filters_returns_all(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs())
        assert len(specs) == 2

    def test_venue_filter_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), venue="hyperliquid")
        assert len(specs) == 2
        assert all(spec.venue == "hyperliquid" for spec in specs)

    def test_venue_filter_no_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), venue="binance")
        assert len(specs) == 0

    def test_family_filter_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), family="node_fills_liq_reconstruction")
        assert len(specs) == 1
        assert specs[0].key == _NODE_FILLS_KEY

    def test_cost_feasibility_family_filter_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), family="cost_feasibility")
        assert len(specs) == 1
        assert specs[0].key == _COST_FEASIBILITY_KEY

    def test_family_filter_no_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), family="nonexistent")
        assert len(specs) == 0

    def test_tag_filter_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), tag="observer_only")
        assert len(specs) == 2

    def test_tag_filter_no_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(iter_runner_specs(), tag="nonexistent_tag")
        assert len(specs) == 0

    def test_combined_filters(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(
            iter_runner_specs(),
            venue="hyperliquid",
            tag="observer_only",
        )
        assert len(specs) == 2

    def test_combined_filters_no_match(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        specs = filter_specs(
            iter_runner_specs(),
            venue="hyperliquid",
            tag="nonexistent",
        )
        assert len(specs) == 0


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------


class TestFormatRunnerListText:
    def test_output_starts_with_count(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        text = format_runner_list_text(iter_runner_specs())
        assert text.startswith("Registered runner specs: 2")

    def test_output_ends_with_newline(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        text = format_runner_list_text(iter_runner_specs())
        assert text.endswith("\n")

    def test_output_contains_key(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        text = format_runner_list_text(iter_runner_specs())
        assert _NODE_FILLS_KEY in text
        assert _COST_FEASIBILITY_KEY in text

    def test_empty_list(self) -> None:
        text = format_runner_list_text(())
        assert text.startswith("Registered runner specs: 0")


class TestFormatRunnerDetailText:
    def test_contains_all_fields(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        text = format_runner_detail_text(NODE_FILLS_LIQ_RECONSTRUCTION)
        for field in ("key", "family", "venue", "study_id", "description"):
            assert f"{field}:" in text

    def test_ends_with_newline(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        text = format_runner_detail_text(NODE_FILLS_LIQ_RECONSTRUCTION)
        assert text.endswith("\n")


# ---------------------------------------------------------------------------
# JSON formatting
# ---------------------------------------------------------------------------


class TestFormatJsonPayload:
    def test_single_spec_valid_json(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        raw = format_json_payload((NODE_FILLS_LIQ_RECONSTRUCTION,), single=True)
        data = json.loads(raw)
        assert "runner" in data
        assert data["runner"]["key"] == _NODE_FILLS_KEY

    def test_list_valid_json(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            NODE_FILLS_LIQ_RECONSTRUCTION,
        )

        from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
            iter_runner_specs,
        )

        raw = format_json_payload(iter_runner_specs())
        data = json.loads(raw)
        assert "runners" in data
        assert len(data["runners"]) == 2
        assert [runner["key"] for runner in data["runners"]] == [
            _NODE_FILLS_KEY,
            _COST_FEASIBILITY_KEY,
        ]

    def test_empty_list_json(self) -> None:
        raw = format_json_payload(())
        data = json.loads(raw)
        assert data == {"runners": []}


# ---------------------------------------------------------------------------
# CLI main() integration
# ---------------------------------------------------------------------------


class TestRegistryCliMain:
    def test_no_args_lists_runners(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert _NODE_FILLS_KEY in out
        assert _COST_FEASIBILITY_KEY in out

    def test_list_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert _NODE_FILLS_KEY in out
        assert _COST_FEASIBILITY_KEY in out

    def test_list_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--list", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "runners" in data
        assert len(data["runners"]) == 2
        assert [runner["key"] for runner in data["runners"]] == [
            _NODE_FILLS_KEY,
            _COST_FEASIBILITY_KEY,
        ]

    def test_key_detail(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--key", _NODE_FILLS_KEY])
        assert rc == 0
        out = capsys.readouterr().out
        assert "key:" in out
        assert _NODE_FILLS_KEY in out

    def test_key_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--key", _NODE_FILLS_KEY, "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "runner" in data
        assert data["runner"]["key"] == _NODE_FILLS_KEY

    def test_cost_feasibility_key_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--key", _COST_FEASIBILITY_KEY, "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "runner" in data
        assert data["runner"]["key"] == _COST_FEASIBILITY_KEY

    def test_unknown_key_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--key", "definitely_missing_key"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "definitely_missing_key" in err

    def test_unknown_key_json_returns_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["--key", "definitely_missing_key", "--json"])
        assert rc == 2

    def test_venue_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--venue", "hyperliquid", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data["runners"]) == 2
        assert [runner["key"] for runner in data["runners"]] == [
            _NODE_FILLS_KEY,
            _COST_FEASIBILITY_KEY,
        ]

    def test_venue_filter_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--venue", "binance", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"runners": []}

    def test_family_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--family", "node_fills_liq_reconstruction", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data["runners"]) == 1
        assert data["runners"][0]["key"] == _NODE_FILLS_KEY

    def test_cost_feasibility_family_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--family", "cost_feasibility", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data["runners"]) == 1
        assert data["runners"][0]["key"] == _COST_FEASIBILITY_KEY

    def test_tag_filter(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--tag", "observer_only", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data["runners"]) == 2

    def test_tag_filter_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["--tag", "nonexistent", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data == {"runners": []}

    def test_filter_with_key_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["--key", _NODE_FILLS_KEY, "--venue", "hyperliquid"])


# ---------------------------------------------------------------------------
# Lazy import test
# ---------------------------------------------------------------------------


class TestRegistryCliLazyImports:
    def test_list_json_does_not_import_heavy_module_subprocess(self) -> None:
        """Verify list --json does not import heavy runner implementations."""
        code = textwrap.dedent(f"""\
            import sys
            from examples.strategies.venue_agnostic_signal_observer.runners.registry_cli import main
            node_fills_heavy = "{_HEAVY_MODULE}"
            cost_heavy = "{_COST_FEASIBILITY_HEAVY_MODULE}"
            sys.modules.pop(node_fills_heavy, None)
            sys.modules.pop(cost_heavy, None)
            rc = main(["--list", "--json"])
            assert rc == 0, rc
            assert node_fills_heavy not in sys.modules, f"heavy module imported: {{node_fills_heavy}}"
            assert cost_heavy not in sys.modules, f"heavy module imported: {{cost_heavy}}"
            print("OK: lazy import verified")
        """)
        script = _REPO_ROOT / "_test_registry_cli_lazy.py"
        script.write_text(code)
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            script.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "OK: lazy import verified" in result.stdout

    def test_key_json_does_not_import_heavy_module_subprocess(self) -> None:
        """Verify --key --json does not import the heavy node-fills implementation."""
        code = textwrap.dedent(f"""\
            import sys
            from examples.strategies.venue_agnostic_signal_observer.runners.registry_cli import main
            node_fills_heavy = "{_HEAVY_MODULE}"
            cost_heavy = "{_COST_FEASIBILITY_HEAVY_MODULE}"
            sys.modules.pop(node_fills_heavy, None)
            sys.modules.pop(cost_heavy, None)
            rc = main(["--key", "{_NODE_FILLS_KEY}", "--json"])
            assert rc == 0, rc
            assert node_fills_heavy not in sys.modules, f"heavy module imported: {{node_fills_heavy}}"
            assert cost_heavy not in sys.modules, f"heavy module imported: {{cost_heavy}}"
            print("OK: lazy import verified for key mode")
        """)
        script = _REPO_ROOT / "_test_registry_cli_lazy_key.py"
        script.write_text(code)
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            script.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "OK: lazy import verified for key mode" in result.stdout

    def test_cost_feasibility_key_json_does_not_import_heavy_module_subprocess(self) -> None:
        """Verify cost-feasibility key lookup stays metadata-only."""
        code = textwrap.dedent(f"""\
            import sys
            from examples.strategies.venue_agnostic_signal_observer.runners.registry_cli import main
            node_fills_heavy = "{_HEAVY_MODULE}"
            cost_heavy = "{_COST_FEASIBILITY_HEAVY_MODULE}"
            sys.modules.pop(node_fills_heavy, None)
            sys.modules.pop(cost_heavy, None)
            rc = main(["--key", "{_COST_FEASIBILITY_KEY}", "--json"])
            assert rc == 0, rc
            assert node_fills_heavy not in sys.modules, f"heavy module imported: {{node_fills_heavy}}"
            assert cost_heavy not in sys.modules, f"heavy module imported: {{cost_heavy}}"
            print("OK: lazy import verified for cost-feasibility key mode")
        """)
        script = _REPO_ROOT / "_test_registry_cli_lazy_cost_key.py"
        script.write_text(code)
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            script.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "OK: lazy import verified for cost-feasibility key mode" in result.stdout
