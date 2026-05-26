"""Adapter for Artemis daily Perp/Spot Balances data.

Handles parsing of daily balance snapshot files if present locally.
Produces source-unavailable result if missing.
Deterministic schema validation.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyBalanceRecord:
    """One daily balance snapshot per symbol for a vault."""

    date_iso: str  # YYYY-MM-DD
    symbol: str
    vault_address: str
    position_size: float  # net perp position
    spot_balance: float | None = None  # spot balance if available
    source_file: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyBalanceResult:
    """Result of daily balance loading."""

    available: bool
    records: tuple[DailyBalanceRecord, ...] = ()
    path: str | None = None
    schema_version: str | None = None
    vault_addresses: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    date_range: tuple[str, str] | None = None  # (first_date, last_date)
    error: str | None = None


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class ArtemisSchemaError(Exception):
    """Raised when balance data does not match expected schema."""


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_artemis_csv_line(
    row: dict[str, str],
    vault_address: str | None = None,
) -> DailyBalanceRecord:
    """Parse one CSV row from Artemis-format daily balances.

    Expected columns (flexible mapping):
    - date, timestamp, day: the date
    - symbol, coin, asset, token: symbol name
    - position, position_size, balance, perp_balance: net perp position
    - spot, spot_balance: optional spot balance
    """
    date_val = _pick_field(row, ["date", "timestamp", "day", "Date"])
    symbol_val = _pick_field(row, ["symbol", "coin", "asset", "token", "Symbol"])
    position_val = _pick_field(row, ["position_size", "position", "balance", "perp_balance", "Position"])
    spot_val = _pick_field_or_none(row, ["spot_balance", "spot", "Spot"])

    if not date_val or not symbol_val or position_val is None:
        raise ArtemisSchemaError(
            f"Missing required fields: date={date_val}, symbol={symbol_val}, position={position_val}"
        )

    # Normalize date
    date_iso = date_val[:10]  # trim to YYYY-MM-DD
    if "T" in date_iso:
        date_iso = date_iso.split("T")[0]

    position = float(position_val)
    spot = float(spot_val) if spot_val is not None else None

    return DailyBalanceRecord(
        date_iso=date_iso,
        symbol=symbol_val.upper(),
        vault_address=vault_address or "",
        position_size=position,
        spot_balance=spot,
    )


def _pick_field(row: dict[str, str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in row and row[c]:
            return row[c]
    return None


def _pick_field_or_none(row: dict[str, str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in row:
            return row[c]
    return None


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def load_daily_balances_from_csv(
    path: str | Path,
    *,
    vault_address: str | None = None,
) -> DailyBalanceResult:
    """Load daily balance records from a CSV file.

    Returns DailyBalanceResult with records, available flag, and metadata.
    """
    path = Path(path)
    if not path.exists():
        return DailyBalanceResult(
            available=False,
            error=f"File not found: {path}",
        )

    records: list[DailyBalanceRecord] = []
    vaults: set[str] = set()
    symbols: set[str] = set()
    dates: list[str] = []

    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                record = parse_artemis_csv_line(row, vault_address=vault_address)
                # Patch source
                record = DailyBalanceRecord(
                    date_iso=record.date_iso,
                    symbol=record.symbol,
                    vault_address=record.vault_address,
                    position_size=record.position_size,
                    spot_balance=record.spot_balance,
                    source_file=str(path),
                )
                records.append(record)
                vaults.add(record.vault_address)
                symbols.add(record.symbol)
                dates.append(record.date_iso)

        if not records:
            return DailyBalanceResult(
                available=False,
                path=str(path),
                error="CSV file is empty or has no valid records",
            )

        dates_sorted = sorted(set(dates))
        return DailyBalanceResult(
            available=True,
            records=tuple(records),
            path=str(path),
            schema_version="artemis_csv_v1",
            vault_addresses=tuple(sorted(vaults)),
            symbols=tuple(sorted(symbols)),
            date_range=(dates_sorted[0], dates_sorted[-1]),
        )

    except (csv.Error, OSError, ValueError, ArtemisSchemaError) as e:
        return DailyBalanceResult(
            available=False,
            path=str(path),
            error=f"Parse error: {e}",
        )


def load_daily_balances_from_json(
    path: str | Path,
) -> DailyBalanceResult:
    """Load daily balance records from a JSON file.

    Expected format: list of dicts with date, symbol, position_size fields
    or a dict with a "records" key containing the list.
    """
    path = Path(path)
    if not path.exists():
        return DailyBalanceResult(
            available=False,
            error=f"File not found: {path}",
        )

    try:
        with open(path) as f:
            data = json.load(f)

        if isinstance(data, dict):
            records_data = data.get("records", data.get("data", []))
        else:
            records_data = data

        records: list[DailyBalanceRecord] = []
        vaults: set[str] = set()
        symbols: set[str] = set()
        dates: list[str] = []

        for entry in records_data:
            record = DailyBalanceRecord(
                date_iso=entry.get("date", "")[:10],
                symbol=entry.get("symbol", entry.get("coin", "")).upper(),
                vault_address=entry.get("vault_address", ""),
                position_size=float(entry.get("position_size", entry.get("position", 0))),
                spot_balance=float(entry["spot_balance"]) if entry.get("spot_balance") is not None else None,
                source_file=str(path),
            )
            records.append(record)
            vaults.add(record.vault_address)
            symbols.add(record.symbol)
            dates.append(record.date_iso)

        if not records:
            return DailyBalanceResult(
                available=False,
                path=str(path),
                error="JSON data is empty",
            )

        dates_sorted = sorted(set(dates))
        return DailyBalanceResult(
            available=True,
            records=tuple(records),
            path=str(path),
            schema_version="artemis_json_v1",
            vault_addresses=tuple(sorted(vaults)),
            symbols=tuple(sorted(symbols)),
            date_range=(dates_sorted[0], dates_sorted[-1]),
        )

    except (json.JSONDecodeError, OSError, ValueError) as e:
        return DailyBalanceResult(
            available=False,
            path=str(path),
            error=f"Parse error: {e}",
        )


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------


def compute_daily_deltas(
    records: Sequence[DailyBalanceRecord],
) -> dict[str, list[tuple[str, float]]]:
    """Compute day-over-day position deltas for each vault+symbol.

    Returns dict mapping "vault:symbol" -> [(date, delta), ...]
    """
    from collections import defaultdict

    # Group by vault:symbol and sort by date
    groups: dict[str, list[DailyBalanceRecord]] = defaultdict(list)
    for rec in records:
        key = f"{rec.vault_address}:{rec.symbol}"
        groups[key].append(rec)

    result: dict[str, list[tuple[str, float]]] = {}
    for key, recs in groups.items():
        sorted_recs = sorted(recs, key=lambda r: r.date_iso)
        deltas: list[tuple[str, float]] = []
        for i in range(1, len(sorted_recs)):
            delta = sorted_recs[i].position_size - sorted_recs[i - 1].position_size
            deltas.append((sorted_recs[i].date_iso, delta))
        result[key] = deltas

    return result


# ---------------------------------------------------------------------------
# Source inventory check
# ---------------------------------------------------------------------------


def check_artemis_source(
    data_root: str | Path | None,
) -> DailyBalanceResult:
    """Check availability of Artemis daily balance data.

    Returns DailyBalanceResult with available flag and metadata.
    """
    if not data_root:
        return DailyBalanceResult(
            available=False,
            error="No data_root provided",
        )

    root = Path(data_root)
    candidate_csv = [
        root / "artemis" / "daily_perp_balances.csv",
        root / "artemis" / "perp_balances.csv",
        root / "balances" / "artemis_daily.csv",
    ]
    candidate_json = [
        root / "artemis" / "daily_perp_balances.json",
        root / "artemis" / "perp_balances.json",
        root / "balances" / "artemis_daily.json",
    ]

    for cp in candidate_csv:
        if cp.exists():
            return load_daily_balances_from_csv(cp)

    for cp in candidate_json:
        if cp.exists():
            return load_daily_balances_from_json(cp)

    return DailyBalanceResult(
        available=False,
        error="No Artemis balance file found in data_root",
    )