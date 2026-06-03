from __future__ import annotations

from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.core.jsonl import (
    append_jsonl,
    iter_jsonl,
    read_jsonl,
    write_jsonl,
)


def test_jsonl_helpers_write_read_append_and_skip_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "rows" / "events.jsonl"
    rows = [
        {"id": 1, "status": "ok"},
        {"id": 2, "status": "more"},
    ]

    write_jsonl(path, rows)
    path.write_bytes(path.read_bytes() + b"\n\n")
    append_jsonl(path, {"id": 3, "status": "tail"})

    assert path.parent.exists()
    data = path.read_bytes()
    assert isinstance(data, bytes)
    assert b'"id":1' in data
    assert read_jsonl(path) == [
        {"id": 1, "status": "ok"},
        {"id": 2, "status": "more"},
        {"id": 3, "status": "tail"},
    ]
    assert list(iter_jsonl(path)) == read_jsonl(path)
