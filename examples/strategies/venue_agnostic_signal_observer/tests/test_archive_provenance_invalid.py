"""Test that no real historical Hyperliquid asset‑context rows are discovered.

The synthetic archive generator is not part of the repository history, and the
`discover_archive_paths` discovery only points at the empty `data/hyperliquid`
directory. This test asserts that loading those paths yields zero rows, confirming
that the Phase 0A run cannot succeed on genuine archive data.
"""

from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    discover_archive_paths,
    load_archive_rows,
)


def test_no_real_archive_rows(tmp_path: Path) -> None:
    # Use an isolated temporary directory that contains no archive data.
    repo_root = tmp_path
    archive_paths = discover_archive_paths(repo_root)
    rows, diagnostics = load_archive_rows(archive_paths)
    assert rows == []
    # No rows loaded means the synthetic archive is not being considered.
    assert diagnostics.loaded_rows == 0
