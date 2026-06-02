from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

try:
    import orjson
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("orjson is required for JSONL scaffold helpers") from exc


def iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = orjson.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object row in {path}")
            yield dict(row)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return list(iter_jsonl(path))


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for row in rows:
            handle.write(_dump_row(row))
            handle.write(b"\n")


def append_jsonl(path: Path, row: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(_dump_row(row))
        handle.write(b"\n")


def _dump_row(row: Mapping[str, object]) -> bytes:
    payload: dict[str, Any] = dict(row)
    return orjson.dumps(payload)
