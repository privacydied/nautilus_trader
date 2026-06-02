"""Pure S3/source-probe helpers for node-fills liquidation reconstruction.

Leaf module — no runner.py imports, no network calls, no reconstruction logic.
"""
from __future__ import annotations

import re


__all__ = [
    "_redact_error",
]


def _redact_error(text: str, limit: int = 500) -> str:
    text = re.sub(r"AKIA[0-9A-Z]{16}", "<AWS_ACCESS_KEY_REDACTED>", text or "")
    text = re.sub(r"(?i)(secret|token|credential)[^\s]*", "<REDACTED>", text)
    return text.strip()[:limit]
