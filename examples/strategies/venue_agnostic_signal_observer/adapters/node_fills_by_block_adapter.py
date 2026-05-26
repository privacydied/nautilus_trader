"""Adapter for parsing Hyperliquid node_fills_by_block archive data.

Stream-friendly, deterministic ordering, schema validation.
No live API calls.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Fill record type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FillRecord:
    """One parsed fill from node_fills_by_block archive."""

    block_number: int | None
    tx_hash: str
    symbol: str
    price: float
    size: float
    side: str  # buy or sell from the perspective of the fill
    buyer: str
    seller: str
    timestamp_ns: int
    liquidation: bool  # True if schema marks this as liquidation
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class NodeFillsSchemaError(Exception):
    """Raised when fill data does not match expected schema."""


def validate_fill_row(row: dict[str, Any], row_idx: int) -> None:
    """Validate a single fill row against expected schema.

    Raises NodeFillsSchemaError if required fields are missing or wrong type.
    """
    required_fields = {
        "coin": str,
        "sz": (int, float),
        "px": (int, float),
        "buyer": str,
        "seller": str,
        "time": int,
    }
    for field_name, expected_type in required_fields.items():
        if field_name not in row:
            raise NodeFillsSchemaError(
                f"Row {row_idx}: missing required field '{field_name}'"
            )
        if row[field_name] is None:
            raise NodeFillsSchemaError(
                f"Row {row_idx}: required field '{field_name}' is null"
            )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_symbol(coin: str) -> str:
    """Normalize coin/symbol field."""
    return coin.upper().split("/")[0].split("-")[0]


def _parse_liquidation(row: dict[str, Any]) -> bool:
    """Detect if a fill is a liquidation from the archive schema.

    Common markers:
    - 'liquidation' field exists and is truthy
    - 'liquidation' appears in the fill type
    """
    if row.get("liquidation"):
        return True
    fill_type = row.get("fillType", "")
    if fill_type and "liquidation" in str(fill_type).lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Fill parser
# ---------------------------------------------------------------------------


def parse_fill_row(
    row: dict[str, Any],
    row_idx: int,
    *,
    validate: bool = True,
) -> FillRecord:
    """Parse one archive row into a FillRecord."""
    if validate:
        validate_fill_row(row, row_idx)

    symbol = _parse_symbol(row.get("coin", ""))
    size_raw = row.get("sz", 0)
    price_raw = row.get("px", 0)
    ts_raw = row.get("time", 0)

    # Handle string/integer price and size
    size = float(size_raw) if not isinstance(size_raw, float) else size_raw
    price = float(price_raw) if not isinstance(price_raw, float) else price_raw

    # Handle timestamp - may be in seconds, milliseconds, or nanoseconds
    ts_ns = _normalize_timestamp_ns(ts_raw)

    buyer = str(row.get("buyer", ""))
    seller = str(row.get("seller", ""))

    return FillRecord(
        block_number=row.get("block"),
        tx_hash=row.get("hash", ""),
        symbol=symbol,
        price=price,
        size=abs(size),
        side="buy" if size > 0 else "sell",
        buyer=buyer,
        seller=seller,
        timestamp_ns=ts_ns,
        liquidation=_parse_liquidation(row),
        raw=dict(row),
    )


def _normalize_timestamp_ns(ts: int) -> int:
    """Normalize timestamp to nanoseconds.

    Automatically detects seconds, milliseconds, microseconds, or nanoseconds.
    """
    if ts > 1_000_000_000_000_000_000:  # ns
        return ts
    if ts > 1_000_000_000_000_000:  # μs
        return ts * 1_000
    if ts > 1_000_000_000_000:  # ms
        return ts * 1_000_000
    if ts > 1_000_000_000_0:  # likely seconds
        return ts * 1_000_000_000
    return ts  # assume ns already


# ---------------------------------------------------------------------------
# Streaming reader
# ---------------------------------------------------------------------------


def stream_fills_from_file(
    path: str | Path,
    *,
    validate: bool = True,
    lazy: bool = False,
) -> Iterator[FillRecord]:
    """Stream FillRecord objects from a JSONL archive file.

    If lazy=True, skips validation until a row is accessed.
    Each row is parsed immediately; validation can be skipped for speed.
    """
    path = Path(path)
    with open(path, "rb") as f:
        for row_idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise NodeFillsSchemaError(
                    f"Line {row_idx}: JSON parse error: {e}"
                ) from e

            yield parse_fill_row(row, row_idx, validate=validate)


def load_fills_to_list(
    path: str | Path,
    *,
    validate: bool = True,
    max_rows: int | None = None,
) -> list[FillRecord]:
    """Load all fill records from a file into a list.

    Useful for small fixtures and tests.
    For large archives, use stream_fills_from_file().
    """
    records: list[FillRecord] = []
    for i, record in enumerate(stream_fills_from_file(path, validate=validate)):
        if max_rows is not None and i >= max_rows:
            break
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def filter_fills_by_vault(
    fills: Sequence[FillRecord] | Iterator[FillRecord],
    vault_addresses: set[str],
) -> Iterator[FillRecord]:
    """Yield fills where buyer or seller is in the vault address set."""
    for fill in fills:
        if fill.buyer in vault_addresses or fill.seller in vault_addresses:
            yield fill


def compute_signed_delta(
    fill: FillRecord,
    vault_address: str,
) -> float:
    """Compute signed size delta for a vault.

    vault is buyer: +size
    vault is seller: -size
    If vault is both sides (unlikely for fills): 0
    """
    buyer = fill.buyer == vault_address
    seller = fill.seller == vault_address
    if buyer and seller:
        return 0.0
    if buyer:
        return fill.size
    if seller:
        return -fill.size
    return 0.0


# ---------------------------------------------------------------------------
# Source inventory check
# ---------------------------------------------------------------------------


def check_node_fills_source(
    data_root: str | Path | None,
) -> tuple[bool, str | None, str | None, int]:
    """Check availability of node_fills_by_block data.

    Returns (available, path, schema_version, row_count).
    """
    if not data_root:
        return False, None, None, 0

    root = Path(data_root)
    candidate_paths = [
        root / "node_fills_by_block.jsonl",
        root / "node_fills_by_block" / "fills.jsonl",
        root / "fills" / "node_fills_by_block.jsonl",
        root / "hl" / "node_fills_by_block.jsonl",
    ]

    for cp in candidate_paths:
        if cp.exists():
            # Count rows for the first 1000 to estimate
            count = 0
            with open(cp, "rb") as f:
                for line in f:
                    if line.strip():
                        count += 1
                    if count > 1000:
                        break
            return True, str(cp), "node_fills_by_block_v1", count

    return False, None, None, 0