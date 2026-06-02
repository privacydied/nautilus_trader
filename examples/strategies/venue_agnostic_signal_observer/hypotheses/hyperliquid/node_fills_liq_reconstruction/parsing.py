"""Pure parsing / extraction helpers for node-fills liquidation reconstruction.

Leaf module — no runner.py imports, no network calls, no reconstruction logic.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


__all__ = [
    "_normalize_address",
    "_extract_replica_cmds_date_from_key",
    "_extract_replica_cmds_object_timestamp",
    "_extract_action_order_key",
    "_decimal_str",
]


def _normalize_address(addr: str) -> str:
    """Normalize an Ethereum-style address to lowercase with 0x prefix.

    Handles bare hex strings, uppercase, mixed case, and missing 0x prefix.
    """
    a = addr.strip()
    if not a.lower().startswith('0x'):
        a = '0x' + a
    return a.lower()


def _extract_replica_cmds_date_from_key(key: str) -> str:
    """Extract the YYYYMMDD date prefix from a replica_cmds S3 object key.

    Key shape: replica_cmds/YYYY-MM-DDThh:mm:ssZ/YYYYMMDD/<timestamp>.lz4
    Returns the YYYYMMDD string (e.g. '20250727') for range filtering.
    """
    parts = key.rstrip('/').split('/')
    # Expected after split: ['replica_cmds', 'YYYY-MM-DDThh:mm:ssZ', 'YYYYMMDD', '<timestamp>.lz4']
    if len(parts) >= 3:
        return parts[2]
    return ''


def _extract_replica_cmds_object_timestamp(key: str) -> tuple[str, int]:
    """Extract (YYYYMMDD, block_timestamp_ms) from replica_cmds S3 key.

    Key format: replica_cmds/YYYY-MM-DDThh:mm:ssZ/YYYYMMDD/<timestamp>.lz4

    Returns (date_prefix_str, timestamp_int_ms) where timestamp is the filename
    integer component used for strict newest-prior chronology ordering.
    Returns ('unknown', 0) if the key doesn't match the expected format.
    """
    parts = key.split('/')
    # parts[0] = 'replica_cmds', parts[1] = 'YYYY-MM-DDThh:mm:ssZ', parts[2] = 'YYYYMMDD'
    # The last part is the filename like '677270000.lz4' or '677270000'
    if len(parts) >= 4:
        date_prefix = parts[2]  # YYYYMMDD
        filename = parts[-1]
        ts_str = filename.replace('.lz4', '')
        try:
            ts_int = int(ts_str)
            return (date_prefix, ts_int)
        except ValueError:
            pass
    elif len(parts) >= 3:
        # Fallback: try to extract from timestamp component
        iso_part = parts[1]
        if 'T' in iso_part:
            date_prefix = iso_part[:10].replace('-', '')
            return (date_prefix, 0)
    return ('unknown', 0)


def _extract_action_order_key(action: dict[str, Any]) -> tuple[int, int]:
    block = int(action.get('block') or action.get('block_number') or 0)
    nonce = int(action.get('nonce') or action.get('timestamp') or 0)
    return (block, nonce)


def _decimal_str(value: Decimal | int | float | str | None) -> str:
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return format(value, 'f')
    return format(Decimal(str(value)), 'f')
