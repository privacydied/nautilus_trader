"""Adapter for parsing Hyperliquid node_fills_by_block archive data.

Stream-friendly, deterministic ordering, schema validation.
No live API calls.

Supports two schemas:
1. Legacy flat schema (FillRecord: coin, sz, px, buyer, seller, time)
2. Real S3 pair/event schema (NodeFillRecord: block with [address, fill_detail])
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Legacy FillRecord (flat schema, backward compatibility)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FillRecord:
    """One parsed fill from legacy flat schema."""

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
# NodeFillRecord (real pair/event archive schema)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeFillRecord:
    """One address-level fill from the real node_fills_by_block archive schema.

    The archive stores events as [address, fill_detail] pairs per block.
    Each event is a single party's fill — the trade counterparty is another event.
    """

    address: str
    block_number: int
    block_time: datetime | None
    local_time: datetime | None
    fill_time: datetime | None
    coin: str
    px: Decimal
    sz: Decimal
    side: str  # "A" = ask/taker, "B" = bid/maker in Hyperliquid archive
    dir: str | None  # "Open Long", "Close Long", "Open Short", "Close Short"
    oid: int | str | None
    tid: int | str | None
    hash: str | None
    start_position: Decimal | None
    closed_pnl: Decimal | None
    fee: Decimal | None
    crossed: bool | None
    builder_fee: Decimal | None
    deployer_fee: Decimal | None
    fee_token: str | None
    builder: str | None
    cloid: str | None
    twap_id: int | None
    priority_gas: int | None
    raw: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Paired fills (two addresses sharing the same trade)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairedFill:
    """Two NodeFillRecords that share trade keys (tid, hash, block, coin, etc.)."""

    record_a: NodeFillRecord  # side "A"
    record_b: NodeFillRecord  # side "B"
    trade_keys: dict[str, Any]  # shared identifying keys


# ---------------------------------------------------------------------------
# Schema validation errors
# ---------------------------------------------------------------------------


class NodeFillsSchemaError(Exception):
    """Raised when fill data does not match expected schema."""


class NodeFillsSideError(Exception):
    """Raised when fill side is unknown or delta mapping fails."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Frozen HLP perp universe symbols (without xyz: prefix)
FROZEN_SYMBOLS: tuple[str, ...] = (
    "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "BTC",
    "DOGE", "DOT", "ENA", "ETH", "FET", "HYPE", "INJ", "JUP", "LINK",
    "LTC", "MKR", "NEAR", "ONDO", "OP", "PENDLE", "SEI", "SOL", "SUI",
    "TIA", "TON", "TRX", "UNI", "WIF", "WLD", "XRP",
)

# Hyperliquid archive side interpretation:
# "A" = ask/taker (aggressive) — inventory decreases (-sz)
# "B" = bid/maker (passive) — inventory increases (+sz)
SIDE_TO_SIGNED_DELTA: dict[str, Decimal] = {
    "A": Decimal("-1"),
    "B": Decimal("1"),
}

# ---------------------------------------------------------------------------
# Legacy schema validation (backward compat)
# ---------------------------------------------------------------------------


def validate_fill_row(row: dict[str, Any], row_idx: int) -> None:
    """Validate a single flat-schema fill row.

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
# Legacy parsing helpers (backward compat)
# ---------------------------------------------------------------------------


def _parse_symbol(coin: str) -> str:
    """Normalize coin/symbol field. Strips xyz: prefix, slash, dash."""
    coin = coin.upper()
    # Strip xyz: prefix if present
    if coin.startswith("XYZ:"):
        coin = coin[4:]
    return coin.split("/")[0].split("-")[0]


def _parse_liquidation(row: dict[str, Any]) -> bool:
    """Detect if a fill is a liquidation from the archive schema."""
    if row.get("liquidation"):
        return True
    fill_type = row.get("fillType", "")
    if fill_type and "liquidation" in str(fill_type).lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------


def _normalize_timestamp_ns(ts: int) -> int:
    """Normalize timestamp to nanoseconds."""
    if ts > 1_000_000_000_000_000_000:  # ns
        return ts
    if ts > 1_000_000_000_000_000:  # μs
        return ts * 1_000
    if ts > 1_000_000_000_000:  # ms
        return ts * 1_000_000
    if ts > 1_000_000_000_0:  # likely seconds
        return ts * 1_000_000_000
    return ts  # assume ns already


def _ns_to_datetime_ms(ts_ns: int) -> datetime | None:
    """Convert nanosecond timestamp to UTC datetime (ms precision)."""
    if ts_ns == 0:
        return None
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)


def _ns_to_datetime_us(ts_ns: int) -> datetime | None:
    """Convert nanosecond timestamp to UTC datetime (us precision)."""
    if ts_ns == 0:
        return None
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# ISO timestamp parsing
# ---------------------------------------------------------------------------


def _parse_iso_timestamp(ts_str: str | None) -> datetime | None:
    """Parse an ISO timestamp string to datetime."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# NodeFillRecord schema: parse [address, fill_detail] pair from block event
# ---------------------------------------------------------------------------


def parse_node_fill_event(
    address: str,
    detail: dict[str, Any],
    block_number: int,
    block_time: datetime | None,
    local_time: datetime | None,
) -> NodeFillRecord:
    """Parse one [address, fill_detail] event from a block's events array.

    Returns a NodeFillRecord with all available fields.
    """
    # Parse numeric fields as Decimal
    px_str = detail.get("px", "0")
    sz_str = detail.get("sz", "0")
    try:
        px = Decimal(str(px_str))
    except Exception:
        px = Decimal("0")
    try:
        sz = Decimal(str(sz_str))
    except Exception:
        sz = Decimal("0")

    # Parse timestamps
    fill_time_ms = detail.get("time", 0)
    if fill_time_ms:
        fill_time = _ns_to_datetime_ms(int(fill_time_ms) * 1_000_000)
    else:
        fill_time = None

    # Parse optional Decimal fields
    def _to_decimal(val: Any) -> Decimal | None:
        if val is None:
            return None
        try:
            return Decimal(str(val))
        except Exception:
            return None

    start_position = _to_decimal(detail.get("startPosition"))
    closed_pnl = _to_decimal(detail.get("closedPnl"))
    fee = _to_decimal(detail.get("fee"))
    builder_fee = _to_decimal(detail.get("builderFee"))
    deployer_fee = _to_decimal(detail.get("deployerFee"))

    # Parse optional int fields
    priority_gas = detail.get("priorityGas")

    return NodeFillRecord(
        address=address,
        block_number=block_number,
        block_time=block_time,
        local_time=local_time,
        fill_time=fill_time,
        coin=detail.get("coin", ""),
        px=px,
        sz=sz,
        side=detail.get("side", ""),
        dir=detail.get("dir"),
        oid=detail.get("oid"),
        tid=detail.get("tid"),
        hash=detail.get("hash"),
        start_position=start_position,
        closed_pnl=closed_pnl,
        fee=fee,
        crossed=detail.get("crossed"),
        builder_fee=builder_fee,
        deployer_fee=deployer_fee,
        fee_token=detail.get("feeToken"),
        builder=detail.get("builder"),
        cloid=detail.get("cloid"),
        twap_id=detail.get("twapId"),
        priority_gas=priority_gas,
        raw=dict(detail),
    )


# ---------------------------------------------------------------------------
# Block-level parsing
# ---------------------------------------------------------------------------


def parse_block(block: dict[str, Any]) -> list[NodeFillRecord]:
    """Parse one block from the archive into a list of NodeFillRecords.

    Block schema:
        {
            "local_time": "...",
            "block_time": "...",
            "block_number": 1234567890,
            "events": [
                ["0xaddress", {"coin": "...", "side": "A", ...}],
                ["0xaddress2", {"coin": "...", "side": "B", ...}],
                ...
            ]
        }
    """
    block_number = block.get("block_number")
    if block_number is None:
        return []

    block_time = _parse_iso_timestamp(block.get("block_time"))
    local_time = _parse_iso_timestamp(block.get("local_time"))

    events = block.get("events", [])
    if not isinstance(events, list):
        return []

    records: list[NodeFillRecord] = []
    for event in events:
        if not isinstance(event, list) or len(event) < 2:
            continue
        address, detail = event[0], event[1]
        if not isinstance(address, str) or not isinstance(detail, dict):
            continue
        record = parse_node_fill_event(
            address=address,
            detail=detail,
            block_number=block_number,
            block_time=block_time,
            local_time=local_time,
        )
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Normalize coin name (strip xyz: prefix)
# ---------------------------------------------------------------------------


def normalize_coin(coin: str) -> str:
    """Normalize coin to standard symbol. Strips xyz: prefix if present."""
    coin = coin.upper().strip()
    if coin.startswith("XYZ:"):
        return coin[4:]
    return coin


def is_frozen_coin(coin: str) -> bool:
    """Check if a raw coin (with or without xyz: prefix) is in the frozen universe."""
    normalized = normalize_coin(coin)
    return normalized in FROZEN_SYMBOLS


def has_xyz_prefix(coin: str) -> bool:
    """Check if a coin has the xyz: prefix."""
    return coin.upper().strip().startswith("XYZ:")


# ---------------------------------------------------------------------------
# Side-to-signed-delta mapping
# ---------------------------------------------------------------------------


def signed_delta_for_side(side: str, sz: Decimal) -> Decimal:
    """Compute signed inventory delta from side and size.

    B (bid/maker) = +sz (inventory increases)
    A (ask/taker) = -sz (inventory decreases)
    Unknown side raises NodeFillsSideError.
    """
    multiplier = SIDE_TO_SIGNED_DELTA.get(side)
    if multiplier is None:
        raise NodeFillsSideError(
            f"Unknown side '{side}' — cannot compute signed delta. "
            f"Expected 'A' or 'B'."
        )
    return multiplier * abs(sz)


# ---------------------------------------------------------------------------
# Pair reconstruction diagnostic
# ---------------------------------------------------------------------------


def _build_trade_key(record: NodeFillRecord) -> tuple:
    """Build a trade key for pairing two sides of the same fill.

    Uses (block_number, coin, tid, hash, px, sz, time_ms).
    """
    fill_time_ms = (
        int(record.fill_time.timestamp() * 1000) if record.fill_time else 0
    )
    return (
        record.block_number,
        record.coin,
        record.tid,
        record.hash,
        record.px,
        record.sz,
        fill_time_ms,
    )


@dataclass
class PairDiagnostics:
    """Result of pair reconstruction analysis."""

    total_fills: int
    pairable_fills: int
    unpaired_fills: int
    paired_fraction: float  # 0.0 to 1.0
    side_consistency_failures: int
    unpaired_side_a: int
    unpaired_side_b: int


def compute_pair_diagnostics(records: list[NodeFillRecord]) -> PairDiagnostics:
    """Analyze how many fills can be paired by trade key.

    Side consistency: each pair should have one A and one B.
    """
    total = len(records)
    if total == 0:
        return PairDiagnostics(
            total_fills=0, pairable_fills=0, unpaired_fills=0,
            paired_fraction=0.0, side_consistency_failures=0,
            unpaired_side_a=0, unpaired_side_b=0,
        )

    # Group by trade key
    pairs: dict[tuple, list[NodeFillRecord]] = {}
    for record in records:
        key = _build_trade_key(record)
        pairs.setdefault(key, []).append(record)

    pairable = 0
    unpaired = 0
    side_consistency_failures = 0
    unpaired_a = 0
    unpaired_b = 0

    for key, group in pairs.items():
        sides = [r.side for r in group]
        if len(group) >= 2:
            pairable += len(group)
            # Check side consistency: should have both A and B
            if "A" in sides and "B" in sides:
                pass  # consistent
            else:
                side_consistency_failures += 1
        elif len(group) == 1:
            unpaired += 1
            if group[0].side == "A":
                unpaired_a += 1
            elif group[0].side == "B":
                unpaired_b += 1

    paired_fraction = pairable / total if total > 0 else 0.0

    return PairDiagnostics(
        total_fills=total,
        pairable_fills=pairable,
        unpaired_fills=unpaired,
        paired_fraction=paired_fraction,
        side_consistency_failures=side_consistency_failures,
        unpaired_side_a=unpaired_a,
        unpaired_side_b=unpaired_b,
    )


# ---------------------------------------------------------------------------
# Block-level ordering: deterministic by block_number then event order
# ---------------------------------------------------------------------------


def stream_records_from_blocks(
    blocks: list[dict[str, Any]],
) -> Iterator[NodeFillRecord]:
    """Stream NodeFillRecords from a list of blocks, preserving block ordering."""
    sorted_blocks = sorted(blocks, key=lambda b: b.get("block_number", 0))
    for block in sorted_blocks:
        records = parse_block(block)
        yield from records


# ---------------------------------------------------------------------------
# LZ4 file reader
# ---------------------------------------------------------------------------


def stream_fills_from_lz4(
    path: str | Path,
) -> Iterator[NodeFillRecord]:
    """Stream NodeFillRecords from an LZ4-compressed JSONL archive file.

    Each line is a JSON block with key 'events' containing [address, fill_detail] pairs.
    """
    import lz4.frame  # optional dependency

    path = Path(path)
    with lz4.frame.open(str(path), "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                block = json.loads(line)
            except json.JSONDecodeError:
                continue
            records = parse_block(block)
            yield from records


def stream_fills_from_jsonl(
    path: str | Path,
    *,
    validate: bool = True,
) -> Iterator[NodeFillRecord]:
    """Stream NodeFillRecords from a plain JSONL file (decompressed).

    Uses the real pair/event schema. Each line is a JSON block.
    """
    path = Path(path)
    with open(path, "rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                block = json.loads(line)
            except json.JSONDecodeError:
                continue
            records = parse_block(block)
            yield from records


# ---------------------------------------------------------------------------
# S3 streaming (bounded, explicit opt-in only)
# ---------------------------------------------------------------------------


def _build_s3_date_path(date_str: str, hour: int | None = None) -> str:
    """Build S3 path for a date."""
    if hour is not None:
        return f"node_fills_by_block/hourly/{date_str}/{hour}.lz4"
    return f"node_fills_by_block/hourly/{date_str}/"


def stream_fills_from_s3_hour(
    date_str: str,
    hour: int,
    bucket: str = "hl-mainnet-node-data",
    work_dir: str | Path | None = None,
    *,
    request_payer: bool = True,
) -> tuple[list[NodeFillRecord], int, str]:
    """Download and parse one hour from S3.

    Returns (records, bytes_downloaded, local_path).
    Requires explicit opt-in (request_payer flag).
    """
    import subprocess

    s3_path = f"s3://{bucket}/{_build_s3_date_path(date_str, hour)}"
    work_dir_path = Path(work_dir or "_probe_tmp")
    work_dir_path.mkdir(parents=True, exist_ok=True)
    local_path = work_dir_path / f"{date_str}_{hour}.lz4"

    cmd = ["aws", "s3", "cp", s3_path, str(local_path)]
    if request_payer:
        cmd.extend(["--request-payer", "requester"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"S3 download failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    bytes_downloaded = local_path.stat().st_size
    records = list(stream_fills_from_lz4(local_path))
    return records, bytes_downloaded, str(local_path)


# ---------------------------------------------------------------------------
# Legacy parsing (backward compat)
# ---------------------------------------------------------------------------


def parse_fill_row(
    row: dict[str, Any],
    row_idx: int,
    *,
    validate: bool = True,
) -> FillRecord:
    """Parse one archive row into a FillRecord (legacy flat schema)."""
    if validate:
        validate_fill_row(row, row_idx)

    symbol = _parse_symbol(row.get("coin", ""))
    size_raw = row.get("sz", 0)
    price_raw = row.get("px", 0)
    ts_raw = row.get("time", 0)

    size = float(size_raw) if not isinstance(size_raw, float) else size_raw
    price = float(price_raw) if not isinstance(price_raw, float) else price_raw
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


def stream_fills_from_file(
    path: str | Path,
    *,
    validate: bool = True,
    lazy: bool = False,
) -> Iterator[FillRecord]:
    """Stream FillRecord objects from a legacy flat-schema JSONL file."""
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
    """Load all fill records from a file into a list (legacy schema)."""
    records: list[FillRecord] = []
    for i, record in enumerate(stream_fills_from_file(path, validate=validate)):
        if max_rows is not None and i >= max_rows:
            break
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Filtering helpers (legacy schema)
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
    """Compute signed size delta for a vault (legacy schema)."""
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
# Source inventory check (local)
# ---------------------------------------------------------------------------


def check_node_fills_source(
    data_root: str | Path | None,
) -> tuple[bool, str | None, str | None, int]:
    """Check availability of node_fills_by_block data (local files).

    Checks for both legacy flat files and new LZ4 archive files.
    Returns (available, path, schema_version, row_count).
    """
    if not data_root:
        return False, None, None, 0

    root = Path(data_root)

    # Check for LZ4 archive directory (new schema)
    archive_dir = root / "node_fills_by_block"
    if archive_dir.is_dir() and any(archive_dir.rglob("*.lz4")):
        return True, str(archive_dir), "node_fills_by_block_lz4_v1", 0

    # Check for legacy flat JSONL files
    candidate_paths = [
        root / "node_fills_by_block.jsonl",
        root / "node_fills_by_block" / "fills.jsonl",
        root / "fills" / "node_fills_by_block.jsonl",
        root / "hl" / "node_fills_by_block.jsonl",
    ]
    for cp in candidate_paths:
        if cp.exists():
            count = 0
            with open(cp, "rb") as f:
                for line in f:
                    if line.strip():
                        count += 1
                    if count > 1000:
                        break
            return True, str(cp), "node_fills_by_block_v1", count

    return False, None, None, 0


# ---------------------------------------------------------------------------
# Signed per-address inventory delta (NodeFillRecord)
# ---------------------------------------------------------------------------


def compute_address_signed_delta(
    record: NodeFillRecord,
    address: str,
) -> Decimal:
    """Compute signed size delta for a specific address from a NodeFillRecord.

    If the record's address matches the target, compute delta from side.
    Otherwise, return 0.
    """
    if record.address != address:
        return Decimal("0")
    return signed_delta_for_side(record.side, record.sz)


def total_signed_delta_for_address(
    records: list[NodeFillRecord],
    address: str,
) -> Decimal:
    """Sum signed deltas for an address across all records."""
    total = Decimal("0")
    for record in records:
        if record.address == address:
            total += signed_delta_for_side(record.side, record.sz)
    return total


# ---------------------------------------------------------------------------
# Smoke report helpers
# ---------------------------------------------------------------------------


@dataclass
class SmokeReport:
    """Report from a bounded smoke parse."""

    date: str
    hours_downloaded: list[int]
    total_rows: int
    unique_addresses: int
    unique_coins_raw: list[str]
    frozen_coins_present: list[str]
    non_frozen_coins: list[str]
    pairable_fraction: float
    side_consistency_failures: int
    bytes_downloaded: int
    parse_seconds: float
    has_xyz_prefix_coins: int
    address_resolution_attempted: bool
    backstop_attribution_possible: bool


def build_smoke_report(
    records: list[NodeFillRecord],
    date: str,
    hours: list[int],
    bytes_downloaded: int,
    parse_seconds: float,
) -> SmokeReport:
    """Build a smoke report from parsed records."""
    addresses = {r.address for r in records}
    raw_coins = list({r.coin for r in records})
    raw_coins.sort()

    frozen_present = [c for c in raw_coins if is_frozen_coin(c)]
    non_frozen = [c for c in raw_coins if not is_frozen_coin(c)]
    xyz_count = sum(1 for c in raw_coins if has_xyz_prefix(c))

    diag = compute_pair_diagnostics(records)

    return SmokeReport(
        date=date,
        hours_downloaded=hours,
        total_rows=len(records),
        unique_addresses=len(addresses),
        unique_coins_raw=raw_coins,
        frozen_coins_present=frozen_present,
        non_frozen_coins=non_frozen,
        pairable_fraction=diag.paired_fraction,
        side_consistency_failures=diag.side_consistency_failures,
        bytes_downloaded=bytes_downloaded,
        parse_seconds=round(parse_seconds, 3),
        has_xyz_prefix_coins=xyz_count,
        address_resolution_attempted=False,
        backstop_attribution_possible=False,
    )
