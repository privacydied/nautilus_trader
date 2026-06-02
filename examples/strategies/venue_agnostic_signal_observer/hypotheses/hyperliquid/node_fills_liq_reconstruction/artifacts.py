from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    import orjson as _orjson_imported

    _orjson_module = _orjson_imported
except ImportError:
    _orjson_module = None

HAS_ORJSON = _orjson_module is not None


def _json_dumps(obj: Any) -> bytes:
    if _orjson_module is not None:
        try:
            return _orjson_module.dumps(
                obj, option=_orjson_module.OPT_INDENT_2 | _orjson_module.OPT_SORT_KEYS
            )
        except TypeError:
            pass
    return json.dumps(obj, indent=2, sort_keys=True, default=str).encode()


def _json_loads(data: bytes | str) -> Any:
    if _orjson_module is not None:
        return _orjson_module.loads(data)
    if isinstance(data, str):
        data = data.encode()
    return json.loads(data)


def _jsonl_write(path: Path, records: Sequence[dict]) -> None:
    with open(path, "wb") as f:
        for r in records:
            if _orjson_module is not None:
                f.write(_orjson_module.dumps(r))
            else:
                f.write(json.dumps(r).encode())
            f.write(b"\n")


def _jsonl_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        if _orjson_module is not None:
            f.write(_orjson_module.dumps(record))
        else:
            f.write(json.dumps(record, default=str).encode())
        f.write(b"\n")


def _jsonl_read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        rows.append(_json_loads(line))
    return rows


def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON atomically via tmp+rename."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(_json_dumps(obj))
    os.replace(str(tmp), str(path))


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically via tmp+rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(str(tmp), str(path))


__all__ = (
    'HAS_ORJSON',
    '_json_dumps',
    '_json_loads',
    '_jsonl_write',
    '_jsonl_append',
    '_jsonl_read',
    '_orjson_module',
    'atomic_write_json',
    'atomic_write_text',
)
