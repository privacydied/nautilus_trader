"""Pure bar-construction helpers for node-fills liquidation reconstruction.

Leaf module — no runner.py imports, no network calls, no reconstruction logic.
"""
from __future__ import annotations

import re


__all__ = [
    "_date_bucket_from_key",
]


def _date_bucket_from_key(key: str) -> str:
    m = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", key)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return "unknown"
