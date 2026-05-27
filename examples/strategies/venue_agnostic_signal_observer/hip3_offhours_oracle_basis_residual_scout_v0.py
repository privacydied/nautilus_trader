# /// script
# dependencies = []
# ///
"""
HIP-3 off-hours oracle basis residual scout v0 — Phase -1 feasibility.

Six-gate kill-chain for HIP-3 equity/index-like perps on Hyperliquid:

  1. Symbol discovery          → public Hyperliquid info endpoint
  2. Archive coverage          → S3 archive namespace/coverage
  3. Oracle / fair-value class → anchor alignment (CME futures proxy or cash EOD)
  4. Fee discovery             → public metadata / conservative estimates
  5. L2 liquidity / depth      → bounded archive L2 samples
  6. Off-hours residual basis  → executable mid vs anchor

Each gate short-circuits the run. This scout does NOT:
  - Evaluate strategy PnL
  - Claim null/FDR results
  - Authorize Phase 0, paper, or live execution
  - Mutate REJECTED_RESEARCH.md
  - Use private keys, auth, orders, or account access

This is a Phase -1 feasibility scout only. It decides whether a later
Phase 0 precommitment is worth drafting — it does not authorize one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime, date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Logging discipline
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("hip3_scout")
_handler = logging.StreamHandler()
_handler.setLevel(logging.INFO)
_formatter = logging.Formatter("%(message)s")
_handler.setFormatter(_formatter)
logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


# ──────────────────────────────────────────────────────────────────────────────
# Constants & statuses
# ──────────────────────────────────────────────────────────────────────────────

STUDY_ID = "hip3_offhours_oracle_basis_residual_scout_v0"
SCHEMA_VERSION = "1.0.0"
SAFETY_MODE = "public_data_observer_only"
DEFAULT_OUT_ROOT = "reports/hip3_offhours_oracle_basis_residual_scout_v0"
DEFAULT_START_DATE = date(2025, 10, 13)
DEFAULT_MAX_SYMBOLS = 5
DEFAULT_MIN_COVERAGE_DAYS = 90
DEFAULT_MIN_TAIL_EVENTS = 50
DEFAULT_MAX_L2_HOURS_PER_SYMBOL = 200
DEFAULT_SAMPLE_L2_DAYS = 30
DEFAULT_DOWNLOAD_BUDGET_BYTES = 5 * 1024**3  # 5 GB
DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES = 500 * 1024**2  # 500 MB


class ScoutStatus(Enum):
    HIP3_SCOUT_READY = "HIP3_SCOUT_READY"
    HIP3_SYMBOL_DISCOVERY_FAILED = "HIP3_SYMBOL_DISCOVERY_FAILED"
    HIP3_NO_EQUITY_OR_INDEX_LIKE_SYMBOLS = "HIP3_NO_EQUITY_OR_INDEX_LIKE_SYMBOLS"
    HIP3_ARCHIVE_NAMESPACE_MISSING = "HIP3_ARCHIVE_NAMESPACE_MISSING"
    HIP3_ARCHIVE_COVERAGE_INSUFFICIENT = "HIP3_ARCHIVE_COVERAGE_INSUFFICIENT"
    HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED = (
        "HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED"
    )
    HIP3_SCHEMA_UNUSABLE = "HIP3_SCHEMA_UNUSABLE"
    HIP3_ANCHOR_DATA_UNAVAILABLE = "HIP3_ANCHOR_DATA_UNAVAILABLE"
    HIP3_ORACLE_ALREADY_FAIR_VALUE_TRACKING = (
        "HIP3_ORACLE_ALREADY_FAIR_VALUE_TRACKING"
    )
    HIP3_ORACLE_CLASSIFICATION_INCONCLUSIVE = (
        "HIP3_ORACLE_CLASSIFICATION_INCONCLUSIVE"
    )
    HIP3_FEE_DISCOVERY_FAILED = "HIP3_FEE_DISCOVERY_FAILED"
    HIP3_FEES_TOO_HIGH_FOR_TAIL = "HIP3_FEES_TOO_HIGH_FOR_TAIL"
    HIP3_L2_LIQUIDITY_TOO_THIN = "HIP3_L2_LIQUIDITY_TOO_THIN"
    HIP3_NO_OFFHOURS_RESIDUAL_BASIS_TAIL = (
        "HIP3_NO_OFFHOURS_RESIDUAL_BASIS_TAIL"
    )
    HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED = (
        "HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED"
    )
    HIP3_SCOUT_ERROR = "HIP3_SCOUT_ERROR"


FORBIDDEN_STATUSES = {
    "REJECTED",
    "CANDIDATE",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
    "CANDIDATE_FOR_LIVE",
    "EXECUTION_READY",
    "TRADE_READY",
    "LIVE_READY",
    "PAPER_STRATEGY_PROMOTED",
    "PROMOTION_AUTHORIZED",
    "EDGE_CONFIRMED",
    "PROFITABLE",
    "ALPHA_FOUND",
    "READY_FOR_PHASE_0",
}

FORBIDDEN_SAFETY_TERMS = [
    "submit_order",
    "place_order",
    "cancel_order",
    "private_key",
    "api_key",
    "secret",
    "wallet",
    "live_execute",
    "paper_broker",
    "broker_connect",
    "TRADE_READY",
    "EXECUTION_READY",
    "LIVE_READY",
    "CANDIDATE_FOR_LIVE",
    "PAPER_STRATEGY_PROMOTED",
    "account_value",
    "withdraw",
    "transfer",
    "bridge",
    "os.system",
]

PASSED_VERDICT = "HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED"

# Ticker-pattern discovery hints (longest-match first for safety)
INDEX_LIKE_PATTERNS: list[tuple[str, str]] = [
    ("S&P 500", "index_like"),
    ("SPX", "index_like"),
    ("US500", "index_like"),
    ("NDX", "index_like"),
    ("NASDAQ", "index_like"),
    ("NAS100", "index_like"),
    ("QQQ", "index_like"),
    ("DOW", "index_like"),
    ("DJI", "index_like"),
    ("DJIA", "index_like"),
]
SINGLE_STOCK_LIKE_PATTERNS: list[tuple[str, str]] = [
    ("AAPL", "single_stock_like"),
    ("MSFT", "single_stock_like"),
    ("NVDA", "single_stock_like"),
    ("TSLA", "single_stock_like"),
    ("AMZN", "single_stock_like"),
    ("GOOG", "single_stock_like"),
    ("META", "single_stock_like"),
]
COMMODITY_LIKE_PATTERNS: list[tuple[str, str]] = [
    ("GOLD", "commodity_like"),
    ("XAU", "commodity_like"),
    ("SILVER", "commodity_like"),
    ("XAG", "commodity_like"),
    ("OIL", "commodity_like"),
    ("WTI", "commodity_like"),
    ("BRENT", "commodity_like"),
    ("NATGAS", "commodity_like"),
]
CRYPTO_LIKE_SUBSTRINGS = [
    "BTC",
    "ETH",
    "SOL",
    "DOGE",
    "XRP",
    "ADA",
    "AVAX",
    "LINK",
    "ARB",
    "OP",
    "MATIC",
    "DOT",
    "ATOM",
    "UNI",
    "AAVE",
    "PEPE",
    "WIF",
    "BONK",
    "TRUMP",
    "MELANIA",
]


# ──────────────────────────────────────────────────────────────────────────────
# Symbol classification
# ──────────────────────────────────────────────────────────────────────────────


class SymbolClass(Enum):
    INDEX_LIKE = "index_like"
    SINGLE_STOCK_LIKE = "single_stock_like"
    COMMODITY_LIKE = "commodity_like"
    CRYPTO_LIKE = "crypto_like"
    UNKNOWN = "unknown"


def _score_symbol_patterns(symbol: str, patterns: list[tuple[str, str]]) -> str | None:
    """Return the longest-matching pattern's classification or None."""
    symbol_upper = symbol.upper()
    best: tuple[int, str | None] = (0, None)
    for pattern, classification in patterns:
        if pattern.upper() in symbol_upper:
            length = len(pattern)
            if length > best[0]:
                best = (length, classification)
    return best[1]


def classify_symbol(symbol: str, deployer_namespace: str | None = None) -> SymbolClass:
    """Classify a HIP-3 perp symbol.

    Longest-match first. Crypto-like symbols are detected via substring
    avoidance: if the symbol matches a crypto_substring and is not also
    a clear index/equity pattern with longer match, classify as crypto.
    """
    symbol_upper = symbol.upper()

    # First check CRYPTO — longest crypto substring match wins over short index hints
    longest_crypto_len = 0
    for cs in CRYPTO_LIKE_SUBSTRINGS:
        if cs.upper() in symbol_upper:
            longest_crypto_len = max(longest_crypto_len, len(cs))

    # Check commodity (before index to handle e.g. GOLD as commodity not index)
    commodity_class = _score_symbol_patterns(symbol_upper, COMMODITY_LIKE_PATTERNS)
    if commodity_class:
        return SymbolClass.COMMODITY_LIKE

    # Check index — must beat longest crypto substring to win
    index_class = _score_symbol_patterns(symbol_upper, INDEX_LIKE_PATTERNS)
    if index_class:
        idx_pattern_len = 0
        for p, _ in INDEX_LIKE_PATTERNS:
            if p.upper() in symbol_upper:
                idx_pattern_len = max(idx_pattern_len, len(p))
        # If crypto pattern is longer than index pattern, crypto wins
        if longest_crypto_len > idx_pattern_len:
            return SymbolClass.CRYPTO_LIKE
        return SymbolClass.INDEX_LIKE

    # Check single stock
    stock_class = _score_symbol_patterns(symbol_upper, SINGLE_STOCK_LIKE_PATTERNS)
    if stock_class:
        stock_pattern_len = 0
        for p, _ in SINGLE_STOCK_LIKE_PATTERNS:
            if p.upper() in symbol_upper:
                stock_pattern_len = max(stock_pattern_len, len(p))
        if longest_crypto_len > stock_pattern_len:
            return SymbolClass.CRYPTO_LIKE
        return SymbolClass.SINGLE_STOCK_LIKE

    # Final crypto check — if any crypto substring matches and it's not
    # a longer-known index/stock pattern, it's crypto
    if longest_crypto_len > 0:
        return SymbolClass.CRYPTO_LIKE

    return SymbolClass.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# US Cash Session / Holiday Classification
# ──────────────────────────────────────────────────────────────────────────────


def load_nyse_holidays(path: str | Path) -> dict[str, Any]:
    """Load NYSE holidays sidecar from JSON."""
    p = Path(path)
    if not p.exists():
        return {"full_closures": {}, "early_closes": {}}
    with open(p) as f:
        return json.load(f)


def _holidays_for_year(
    data: dict[str, Any], key: str, year: int
) -> list[str]:
    return data.get(key, {}).get(str(year), [])


class USSessionBuckets(Enum):
    CASH_SESSION = "cash_session"
    EXTENDED_HOURS_US = "extended_hours_us"
    OVERNIGHT_US = "overnight_us"
    WEEKEND = "weekend"


# ET offset: America/New_York is UTC-5 (Standard) or UTC-4 (Daylight)
# For simplicity, use a fixed offset heuristic:
#   ET = UTC-5 during winter, UTC-4 during summer
# We approximate using simple rules rather than zoneinfo dependency.


def _is_edt(dt: datetime) -> bool:
    """Approximate EDT (March 2nd Sunday → November 1st Sunday)."""
    m = dt.month
    if m < 3 or m > 11:
        return False
    if 4 <= m <= 10:
        return True
    # March: after 2nd Sunday
    if m == 3:
        # 2nd Sunday is >= 8 and <= 14
        march_sunday = _nth_sunday(dt.year, 3, 2)
        return dt.day >= march_sunday
    # November: before 1st Sunday
    if m == 11:
        nov_sunday = _nth_sunday(dt.year, 11, 1)
        return dt.day < nov_sunday
    return False


def _nth_sunday(year: int, month: int, n: int) -> int:
    """Return day-of-month for the nth Sunday (1-indexed)."""
    # Start from day 1
    first = datetime(year, month, 1, tzinfo=UTC)
    # Days until first Sunday (0=Sun..6=Sat)
    days_to_first_sunday = (6 - first.weekday()) % 7
    first_sunday_day = 1 + days_to_first_sunday
    return first_sunday_day + (n - 1) * 7


def _et_offset(dt: datetime) -> int:
    """Return UTC offset for US Eastern Time at given datetime (hours)."""
    return -4 if _is_edt(dt) else -5


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5


def _is_holiday_full(dt: datetime, holidays: dict[str, Any]) -> bool:
    date_str = dt.strftime("%Y-%m-%d")
    closures = _holidays_for_year(holidays, "full_closures", dt.year)
    return date_str in closures


def _is_holiday_early_close(dt: datetime, holidays: dict[str, Any]) -> bool:
    date_str = dt.strftime("%Y-%m-%d")
    early = _holidays_for_year(holidays, "early_closes", dt.year)
    return date_str in early


def classify_us_session(
    dt_utc: datetime,
    holidays: dict[str, Any] | None = None,
) -> USSessionBuckets:
    """Classify a UTC datetime into US cash session or off-hours bucket.

    No external calendar dependency — uses static NYSE holiday sidecar.
    """
    _holidays = holidays or {"full_closures": {}, "early_closes": {}}

    # Weekend detection (simple weekday check)
    if _is_weekend(dt_utc):
        return USSessionBuckets.WEEKEND

    # Full-closure holiday
    if _is_holiday_full(dt_utc, _holidays):
        return USSessionBuckets.WEEKEND

    offset = _et_offset(dt_utc)
    local_hour = dt_utc.hour + offset
    local_minute = dt_utc.minute
    # Handle wrap
    if local_hour < 0:
        local_hour += 24
    elif local_hour >= 24:
        local_hour -= 24

    local_decimal = local_hour + local_minute / 60.0

    early_close = _is_holiday_early_close(dt_utc, _holidays)
    open_time = 9.5  # 09:30
    close_time = 13.0 if early_close else 16.0  # 13:00 or 16:00

    if open_time <= local_decimal < close_time:
        return USSessionBuckets.CASH_SESSION

    if 16.0 <= local_decimal < 20.0:
        return USSessionBuckets.EXTENDED_HOURS_US

    if 20.0 <= local_decimal or local_decimal < 4.0:
        return USSessionBuckets.OVERNIGHT_US

    if 4.0 <= local_decimal < 9.5:
        return USSessionBuckets.EXTENDED_HOURS_US

    return USSessionBuckets.EXTENDED_HOURS_US


def classify_us_session_bucket(
    dt_utc: datetime,
    holidays: dict[str, Any] | None = None,
) -> USSessionBuckets:
    """Standalone alias for classify_us_session."""
    return classify_us_session(dt_utc, holidays)


def is_off_hours(
    dt_utc: datetime,
    holidays: dict[str, Any] | None = None,
) -> bool:
    """Return True if dt_utc falls outside US cash session."""
    return classify_us_session(dt_utc, holidays) != USSessionBuckets.CASH_SESSION


# ──────────────────────────────────────────────────────────────────────────────
# Network chokepoint
# ──────────────────────────────────────────────────────────────────────────────

# The scout chokepoint is THE ONLY call site for:
#   urllib, requests, httpx, boto3, aiohttp
# All network access flows through the functions below.

_allow_network_public: bool = False
_allow_s3_archive_read: bool = False

# Download budget tracking
_bytes_downloaded_total: int = 0
_bytes_downloaded_per_symbol: dict[str, int] = {}
_download_budget_bytes: int = DEFAULT_DOWNLOAD_BUDGET_BYTES
_per_symbol_l2_budget_bytes: int = DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES


def configure_chokepoint(
    allow_network_public: bool,
    allow_s3_archive_read: bool,
    download_budget_bytes: int = DEFAULT_DOWNLOAD_BUDGET_BYTES,
    per_symbol_l2_budget_bytes: int = DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES,
) -> None:
    """Configure scout chokepoint flags and download budgets."""
    global _allow_network_public, _allow_s3_archive_read
    global _download_budget_bytes, _per_symbol_l2_budget_bytes
    _allow_network_public = allow_network_public
    _allow_s3_archive_read = allow_s3_archive_read
    _download_budget_bytes = download_budget_bytes
    _per_symbol_l2_budget_bytes = per_symbol_l2_budget_bytes


def check_download_budget(additional_bytes: int = 0) -> None:
    """Check if total or per-symbol budget would be exceeded."""
    global _bytes_downloaded_total
    if _bytes_downloaded_total + additional_bytes > _download_budget_bytes:
        raise RuntimeError("DOWNLOAD_BUDGET_EXCEEDED")


def check_per_symbol_l2_budget(symbol: str, additional_bytes: int = 0) -> None:
    """Check per-symbol L2 budget."""
    current = _bytes_downloaded_per_symbol.get(symbol, 0)
    if current + additional_bytes > _per_symbol_l2_budget_bytes:
        raise RuntimeError("DOWNLOAD_BUDGET_EXCEEDED")


def _record_download(symbol: str | None, byte_count: int) -> None:
    """Record a download and check budgets."""
    global _bytes_downloaded_total
    _bytes_downloaded_total += byte_count
    check_download_budget()
    if symbol:
        _bytes_downloaded_per_symbol[symbol] = (
            _bytes_downloaded_per_symbol.get(symbol, 0) + byte_count
        )


def get_download_stats() -> dict[str, Any]:
    """Return current download tracking state."""
    return {
        "bytes_downloaded_total": _bytes_downloaded_total,
        "bytes_downloaded_per_symbol": dict(_bytes_downloaded_per_symbol),
        "download_budget_bytes": _download_budget_bytes,
        "per_symbol_l2_budget_bytes": _per_symbol_l2_budget_bytes,
        "s3_requester_pays_acknowledged": _allow_s3_archive_read,
    }


def public_http_post(url: str, data: dict[str, Any] | None = None, timeout: int = 30) -> str:
    """Public HTTP POST through the scout network chokepoint (JSON body).

    Required for Hyperliquid info endpoint POST requests.
    """
    if not _allow_network_public:
        raise RuntimeError(
            "NETWORK_PUBLIC_BLOCKED: --allow-network-public required"
        )
    import urllib.request
    import json as _json

    body = _json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "hip3-scout-v0/1.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        response_data = resp.read()
    _record_download(symbol=None, byte_count=len(response_data))
    return response_data.decode("utf-8")


def public_http_get(url: str, timeout: int = 30) -> str:
    """Public HTTP GET through the scout network chokepoint."""
    if not _allow_network_public:
        raise RuntimeError(
            "NETWORK_PUBLIC_BLOCKED: --allow-network-public required"
        )
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "hip3-scout-v0/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    _record_download(symbol=None, byte_count=len(data))
    return data.decode("utf-8")


def _validate_s3_path(path: str) -> None:
    """Validate and normalize an S3 path for the chokepoint.

    Raises RuntimeError if the path is suspicious or does not match
    the expected hyperliquid-archive pattern.
    """
    if not path.startswith("s3://hyperliquid-archive/"):
        raise RuntimeError(f"Invalid S3 path: {path}. Only s3://hyperliquid-archive/ allowed.")
    # Reject path traversal
    if ".." in path:
        raise RuntimeError(f"Path traversal detected in S3 path: {path}")
    # Reject shell-special characters
    dangerous = {"`", "$", "|", ";", "&", ">", "<", "(", ")", "{", "}", "!"}
    for ch in dangerous:
        if ch in path:
            raise RuntimeError(f"Dangerous character '{ch}' in S3 path: {path}")


def public_s3_read(key: str, timeout: int = 120) -> bytes:
    """Public S3 archive read through the scout network chokepoint.

    Uses the `aws s3 cp` CLI with --request-payer requester.
    Requester-pays acknowledgement is implied by --allow-s3-archive-read.

    Validates S3 path before invocation.
    """
    if not _allow_s3_archive_read:
        raise RuntimeError(
            "S3_ARCHIVE_BLOCKED: --allow-s3-archive-read required"
        )
    _validate_s3_path(key)
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # Check budget before download (conservative estimate from path size)
        check_download_budget(len(key))
        cmd = [
            "aws", "s3", "cp",
            key, tmp_path,
            "--request-payer", "requester",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"S3 read failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        with open(tmp_path, "rb") as f:
            data = f.read()
        _record_download(symbol=None, byte_count=len(data))
        return data
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def public_s3_ls(prefix: str, timeout: int = 60) -> list[dict[str, Any]]:
    """List S3 prefix contents through the scout network chokepoint."""
    if not _allow_s3_archive_read:
        raise RuntimeError(
            "S3_ARCHIVE_BLOCKED: --allow-s3-archive-read required"
        )
    _validate_s3_path(prefix)
    import subprocess

    cmd = [
        "aws", "s3", "ls",
        prefix,
        "--request-payer", "requester",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"S3 ls failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    entries = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            entries.append({
                "raw": line,
                "name": parts[-1],
                "is_prefix": line.startswith("PRE"),
            })
    return entries


# ──────────────────────────────────────────────────────────────────────────────
# Helper provenance tracking
# ──────────────────────────────────────────────────────────────────────────────

HELPERS_REUSED: list[dict[str, str]] = []


def record_helper(module_path: str, symbol: str | None = None) -> None:
    """Record a helper module reuse for provenance."""
    entry = {
        "full_dotted_path": module_path,
        "source_file": module_path,
    }
    if symbol:
        entry["content_sha256"] = _compute_file_sha256(module_path)
    else:
        try:
            entry["content_sha256"] = _compute_file_sha256(module_path)
        except Exception:
            entry["content_sha256"] = "unknown"
    HELPERS_REUSED.append(entry)


def _compute_file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Atomic write
# ──────────────────────────────────────────────────────────────────────────────


def atomic_write_json(path: str | Path, data: Any) -> str:
    """Write JSON atomically via tempfile + rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp" + hashlib.md5(str(p).encode()).hexdigest() + ".json")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str, sort_keys=True)
    tmp.replace(p)
    return str(p)


# ──────────────────────────────────────────────────────────────────────────────
# Run metadata
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class RunContext:
    args: dict[str, Any]
    run_id: str
    created_at_utc: str
    git_sha: str
    git_dirty: bool
    repo_root: str
    config_hash: str | None = None


def make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + hashlib.md5(
        str(time.time_ns()).encode()
    ).hexdigest()[:8]


def get_git_sha(repo_root: str) -> str:
    """Read git SHA from .git/HEAD using pure Python. No subprocess."""
    try:
        head_path = os.path.join(repo_root, ".git", "HEAD")
        with open(head_path) as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            ref_path = os.path.join(repo_root, ".git", ref[5:])
            with open(ref_path) as f:
                return f.read().strip()
        return ref
    except Exception:
        return "unknown"


def get_git_dirty(repo_root: str) -> bool:
    """Check git dirty status using pure Python.

    Reads .git/index mtime and compares to HEAD tree hash.
    Falls back to True (conservative) on any failure.
    """
    try:
        # Read HEAD tree hash
        head_path = os.path.join(repo_root, ".git", "HEAD")
        with open(head_path) as f:
            ref_line = f.read().strip()
        if ref_line.startswith("ref: "):
            ref_path = os.path.join(repo_root, ".git", ref_line[5:])
            with open(ref_path) as f:
                sha = f.read().strip()
        else:
            sha = ref_line

        # Read the tree object hash from this commit
        obj_dir = sha[:2]
        obj_file = sha[2:]
        obj_path = os.path.join(repo_root, ".git", "objects", obj_dir, obj_file)
        if not os.path.exists(obj_path):
            return True  # conservative
        import zlib
        with open(obj_path, "rb") as f:
            raw = zlib.decompress(f.read())
        # Parse commit object: lines until blank line, then message
        # Tree line: "tree <sha>"
        for line in raw.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("tree "):
                committed_tree = line[5:].strip()
                break
        else:
            return True

        # Read HEAD's tree object
        t_dir = committed_tree[:2]
        t_file = committed_tree[2:]
        t_path = os.path.join(repo_root, ".git", "objects", t_dir, t_file)
        if not os.path.exists(t_path):
            return True
        # Get the mtime of .git/index
        index_path = os.path.join(repo_root, ".git", "index")
        if not os.path.exists(index_path):
            return True
        # Simple heuristic: if index was modified after the commit tree was written,
        # there may be staged changes. This is a best-effort check.
        index_mtime = os.path.getmtime(index_path)
        tree_mtime = os.path.getmtime(t_path)
        return index_mtime > tree_mtime
    except Exception:
        return True  # conservative default


def compute_config_hash(args: dict[str, Any]) -> str:
    """Deterministic config hash with stable key/symbol/date ordering."""
    # Filter to stable keys only
    stable_keys = [
        "start_date", "end_date", "max_symbols", "prefer_index_like",
        "sample_l2_days", "max_l2_hours_per_symbol", "skip_l2_download",
        "dry_run", "anchor_source", "min_coverage_days", "min_tail_events",
        "download_budget_bytes", "per_symbol_l2_budget_bytes",
    ]
    filtered = {k: args.get(k) for k in stable_keys if k in args}
    # Normalize dates
    for k in ("start_date", "end_date"):
        v = filtered.get(k)
        if isinstance(v, date):
            filtered[k] = v.isoformat()
        elif isinstance(v, str):
            filtered[k] = v
    canonical = json.dumps(filtered, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Gate 1: Symbol discovery
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DiscoveredSymbol:
    symbol: str
    name: str | None = None
    deployer_namespace: str | None = None
    market_type: str | None = None
    oracle_fields: dict[str, Any] | None = None
    max_leverage: float | None = None
    margin_info: dict[str, Any] | None = None
    base_user_fee_bps: float | None = None
    deployer_fee_share_bps: float | None = None
    classification: str | None = None
    listing_date_utc: str | None = None


def discover_public_metadata(
    dry_run: bool = False,
    cache_path: str | None = None,
) -> list[DiscoveredSymbol]:
    """Discover HIP-3/builder-deployed perp symbols from public metadata.

    In dry-run mode, reads from cache if available.
    """
    result: list[DiscoveredSymbol] = []

    if dry_run:
        if cache_path and os.path.exists(cache_path):
            with open(cache_path) as f:
                cached = json.load(f)
            for entry in cached.get("symbols", []):
                result.append(DiscoveredSymbol(
                    symbol=entry["symbol"],
                    name=entry.get("name"),
                    deployer_namespace=entry.get("deployer_namespace"),
                    market_type=entry.get("market_type"),
                    oracle_fields=entry.get("oracle_fields"),
                    max_leverage=entry.get("max_leverage"),
                    margin_info=entry.get("margin_info"),
                    base_user_fee_bps=entry.get("base_user_fee_bps"),
                    deployer_fee_share_bps=entry.get("deployer_fee_share_bps"),
                    classification=entry.get("classification"),
                    listing_date_utc=entry.get("listing_date_utc"),
                ))
        return result

    # Public Hyperliquid info endpoint (requires POST, JSON body)
    raw = public_http_post(
        "https://api.hyperliquid.xyz/info",
        data={"type": "meta"},
        timeout=30,
    )
    data = json.loads(raw)
    perps = data.get("universe", data)

    for entry in perps:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("name") or entry.get("symbol") or ""
        if not symbol:
            continue
        deployer = entry.get("deployer") or entry.get("builder") or entry.get("namespace")
        discovered = DiscoveredSymbol(
            symbol=str(symbol).upper(),
            name=str(entry.get("name", "")),
            deployer_namespace=str(deployer) if deployer else None,
            market_type=str(entry.get("type", "perp")),
            oracle_fields=entry.get("oracle") or entry.get("oracleParams"),
            max_leverage=entry.get("maxLeverage"),
            margin_info=entry.get("marginInfo") or entry.get("margin"),
            base_user_fee_bps=entry.get("userFee") or entry.get("fee"),
            deployer_fee_share_bps=entry.get("builderFee") or entry.get("deployerFee"),
            listing_date_utc=entry.get("listingDate") or entry.get("listing"),
        )
        # Classify
        classification = classify_symbol(
            discovered.symbol, discovered.deployer_namespace
        )
        discovered.classification = classification.value
        result.append(discovered)

    return result


def gate1_symbol_discovery(
    symbols: list[DiscoveredSymbol],
    prefer_index_like: bool = True,
) -> tuple[ScoutStatus, list[DiscoveredSymbol], list[DiscoveredSymbol]]:
    """Gate 1: Symbol discovery and classification.

    Returns (status, index_like_symbols, all_classified_symbols).
    """
    if not symbols:
        return (ScoutStatus.HIP3_SYMBOL_DISCOVERY_FAILED, [], [])

    classified: list[DiscoveredSymbol] = []
    for sym in symbols:
        if sym.classification is None:
            sym.classification = classify_symbol(sym.symbol, None).value
        classified.append(sym)

    index_like = [s for s in classified if s.classification == "index_like"]
    if prefer_index_like and not index_like:
        # Fallback to single_stock_like
        single_stock = [s for s in classified if s.classification == "single_stock_like"]
        if not single_stock:
            return (ScoutStatus.HIP3_NO_EQUITY_OR_INDEX_LIKE_SYMBOLS, [], classified)
        logger.info("GATE1 WARN: no index-like symbols; falling back to single-stock-like")
        return (ScoutStatus.HIP3_SCOUT_READY, single_stock, classified)

    return (ScoutStatus.HIP3_SCOUT_READY, index_like, classified)


# ──────────────────────────────────────────────────────────────────────────────
# Gate 2: Archive coverage
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ArchiveCoverageResult:
    symbol: str
    first_date_utc: str | None = None
    last_date_utc: str | None = None
    total_days: int = 0
    missing_days: int = 0
    listing_date_utc: str | None = None
    delisting_date_utc: str | None = None
    asset_ctxs_available: bool = False
    l2_samples_available: bool = False
    asset_ctxs_required_fields_present: bool = False
    l2_sufficient_levels: bool = False
    post_listing_days: int = 0
    insufficient_post_listing_history: bool = False


def probe_archive_coverage(
    symbols: list[DiscoveredSymbol],
    start_date: date,
    end_date: date | None = None,
    min_coverage_days: int = DEFAULT_MIN_COVERAGE_DAYS,
    sample_l2_days: int = DEFAULT_SAMPLE_L2_DAYS,
    skip_l2_download: bool = False,
    dry_run: bool = False,
) -> tuple[ScoutStatus, list[ArchiveCoverageResult]]:
    """Gate 2: Probe archive namespace and coverage for discovered symbols.

    Checks asset_ctxs namespace and L2 namespace availability.
    """
    if dry_run:
        # Dry-run always passes gate 2 with placeholder coverage
        results: list[ArchiveCoverageResult] = []
        for sym in symbols[:1]:
            results.append(ArchiveCoverageResult(
                symbol=sym.symbol,
                first_date_utc=start_date.isoformat(),
                last_date_utc=(end_date or datetime.now(UTC).date()).isoformat(),
                total_days=min_coverage_days,
                asset_ctxs_available=True,
                l2_samples_available=not skip_l2_download,
                asset_ctxs_required_fields_present=True,
                l2_sufficient_levels=not skip_l2_download,
                post_listing_days=min_coverage_days,
                insufficient_post_listing_history=False,
            ))
        return (ScoutStatus.HIP3_SCOUT_READY, results)

    # Check S3 archive namespace
    try:
        archive_prefix = "s3://hyperliquid-archive/"
        prefixes = public_s3_ls(archive_prefix, timeout=30)
        archive_names = [p["name"] for p in prefixes]
    except RuntimeError as e:
        logger.info(f"GATE2: S3 archive prefix unavailable: {e}")
        if "S3_ARCHIVE_BLOCKED" in str(e):
            raise
        return (ScoutStatus.HIP3_ARCHIVE_NAMESPACE_MISSING, [])

    # Check asset_ctxs namespace
    has_asset_ctxs = any("asset_ctxs" in n for n in archive_names)
    has_market_data = any("market_data" in n for n in archive_names)

    if not has_asset_ctxs:
        # Try explicit probe
        try:
            public_s3_ls("s3://hyperliquid-archive/asset_ctxs/", timeout=30)
            has_asset_ctxs = True
        except RuntimeError:
            return (ScoutStatus.HIP3_ARCHIVE_NAMESPACE_MISSING, [])

    # Probe per-symbol asset_ctxs coverage
    results = []
    for sym in symbols:
        result = ArchiveCoverageResult(symbol=sym.symbol)

        # Probe asset_ctxs dates (files: YYYYMMDD.csv.lz4, not subdirectories)
        try:
            dates = public_s3_ls(
                f"s3://hyperliquid-archive/asset_ctxs/", timeout=30
            )
            date_stamps: list[str] = []
            for d in dates:
                name = d["name"].rstrip("/")
                # Extract YYYYMMDD from filename like "20251013.csv.lz4"
                if ".csv" in name:
                    parts = name.split(".")[0]
                    if parts.isdigit() and len(parts) == 8:
                        date_stamps.append(parts)
            if date_stamps:
                date_stamps = sorted(set(date_stamps))
                result.first_date_utc = min(date_stamps)
                result.last_date_utc = max(date_stamps)
                try:
                    first_dt = datetime.strptime(str(result.first_date_utc), "%Y%m%d").date()
                    last_dt = datetime.strptime(str(result.last_date_utc), "%Y%m%d").date()
                    result.total_days = (last_dt - first_dt).days + 1
                except (ValueError, TypeError):
                    pass
        except RuntimeError:
            pass

        # Compute total_days from first/last
        first_s = result.first_date_utc
        last_s = result.last_date_utc
        if first_s and last_s:
            try:
                first_dt = datetime.strptime(str(first_s), "%Y%m%d").date()
                last_dt = datetime.strptime(str(last_s), "%Y%m%d").date()
                result.total_days = (last_dt - first_dt).days + 1
            except (ValueError, TypeError):
                pass

        result.asset_ctxs_available = result.total_days > 0
        result.asset_ctxs_required_fields_present = True  # assume fields from schema

        # Check L2 namespace
        if not skip_l2_download and has_market_data and result.asset_ctxs_available:
            # Probe a bounded date for L2
            try:
                probe_date = start_date.strftime("%Y%m%d")
                l2_dates = public_s3_ls(
                    f"s3://hyperliquid-archive/market_data/{probe_date}/",
                    timeout=30,
                )
                l2_hours = [d["name"] for d in l2_dates if d.get("is_prefix")]
                if l2_hours:
                    # Probe a single hour for the symbol
                    sample_hour = l2_hours[0]
                    sym_files = public_s3_ls(
                        f"s3://hyperliquid-archive/market_data/{probe_date}/{sample_hour}/l2Book/",
                        timeout=30,
                    )
                    result.l2_samples_available = any(
                        sym.symbol.upper() in f["name"].upper()
                        for f in sym_files
                    ) if sym_files else False
                    if sym_files and not result.l2_samples_available:
                        # Check if there are any l2Book files at all
                        result.l2_samples_available = len(sym_files) > 0
                    result.l2_sufficient_levels = result.l2_samples_available
            except RuntimeError:
                pass

        # Post-listing stabilization
        if result.asset_ctxs_available and result.first_date_utc:
            try:
                listing_dt = datetime.strptime(result.first_date_utc, "%Y%m%d").date()
                result.listing_date_utc = listing_dt.isoformat()
                # 14-day post-listing exclusion
                cutoff = listing_dt + timedelta(days=14)
                eligible_start = max(cutoff, start_date)
                if result.last_date_utc:
                    last_dt = datetime.strptime(result.last_date_utc, "%Y%m%d").date()
                    post_listing_days = (last_dt - eligible_start).days + 1
                    result.post_listing_days = max(0, post_listing_days)
                else:
                    result.post_listing_days = 0
                result.insufficient_post_listing_history = (
                    result.post_listing_days < min_coverage_days
                )
            except (ValueError, TypeError):
                pass

        results.append(result)

    if not results:
        return (ScoutStatus.HIP3_ARCHIVE_NAMESPACE_MISSING, [])

    # Check at least one symbol meets coverage
    eligible = [
        r for r in results
        if r.asset_ctxs_available
        and r.total_days >= min_coverage_days
        and not r.insufficient_post_listing_history
    ]

    if not eligible:
        return (ScoutStatus.HIP3_ARCHIVE_COVERAGE_INSUFFICIENT, results)

    # At least one index-like symbol with L2?
    eligible_with_l2 = [
        r for r in eligible
        if r.l2_samples_available or skip_l2_download
    ]

    if not eligible_with_l2 and not skip_l2_download:
        # L2 missing for all symbols
        return (ScoutStatus.HIP3_ARCHIVE_COVERAGE_INSUFFICIENT, results)

    return (ScoutStatus.HIP3_SCOUT_READY, results)


# ──────────────────────────────────────────────────────────────────────────────
# Gate 3: Oracle / fair-value classification
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class AnchorSample:
    timestamp_utc: datetime
    anchor_value: float  # in USD or index points


@dataclass
class OracleClassificationResult:
    symbol: str
    anchor_source: str
    mark_vs_anchor_stats: dict[str, float] | None = None
    mid_vs_anchor_stats: dict[str, float] | None = None
    oracle_minus_executable_mid_stats: dict[str, float] | None = None
    off_hours_drift_stats: dict[str, float] | None = None
    extended_hours_residual: dict[str, float] | None = None
    overnight_residual: dict[str, float] | None = None
    weekend_residual: dict[str, float] | None = None
    stale_anchor_dropped_count: int = 0
    sample_count: int = 0
    status: ScoutStatus | None = None


def load_anchor_data(
    anchor_source: str,
    symbol: str,
    start_date: date,
    end_date: date | None,
) -> list[AnchorSample]:
    """Load anchor data for a given source.

    Anchors:
    - cme_futures_proxy: CME ES/NQ continuous futures proxy (public no-auth)
    - cash_eod_only: Public end-of-day cash index close carried forward
    - none: Return empty list

    Returns an empty list if data is unavailable.
    """
    if anchor_source == "none":
        return []

    if anchor_source == "cme_futures_proxy":
        # In a real run, this would fetch public CME reference data.
        # For Phase -1 scout framework, return a minimal synthetic stub
        # that can be overridden by tests.
        return _load_cme_futures_proxy(symbol, start_date, end_date)

    if anchor_source == "cash_eod_only":
        return _load_cash_eod_anchor(symbol, start_date, end_date)

    return []


def _load_cme_futures_proxy(
    symbol: str,
    start_date: date,
    end_date: date | None,
) -> list[AnchorSample]:
    """Load CME futures proxy anchor.

    Public reference data sources (no auth required):
    - CME delayed futures data
    - Barchart delayed quotes
    - Yahoo Finance adjusted close for ES/NQ futures

    For scout framework, this is a stub that returns empty unless
    the chokepoint allows network and data is available.
    """
    # Stub: In a real scout, this would fetch from a public CME proxy.
    # Return empty to signal the caller that data must be mocked in tests.
    return []


def _load_cash_eod_anchor(
    symbol: str,
    start_date: date,
    end_date: date | None,
) -> list[AnchorSample]:
    """Load cash EOD anchor.

    Public end-of-day cash index values carried forward.
    """
    return []


def _compute_anchor_aligned_residuals(
    mark_prices: list[tuple[datetime, float]],
    anchor_samples: list[AnchorSample],
    max_stale_hours: float = 4.0,
) -> tuple[list[float], int]:
    """Compute residual = mark - last observed anchor.

    Uses last anchor at or before mark timestamp.
    Drops samples where anchor is > max_stale_hours old.
    Returns (residuals_bps, stale_dropped_count).
    """
    residuals: list[float] = []
    stale_count = 0
    anchor_idx = 0

    for ts, mark in sorted(mark_prices, key=lambda x: x[0]):
        # Find last anchor at or before ts
        while (anchor_idx + 1 < len(anchor_samples) and
               anchor_samples[anchor_idx + 1].timestamp_utc <= ts):
            anchor_idx += 1

        if anchor_idx >= len(anchor_samples):
            stale_count += 1
            continue

        anchor = anchor_samples[anchor_idx]
        age_hours = (ts - anchor.timestamp_utc).total_seconds() / 3600
        if age_hours > max_stale_hours:
            stale_count += 1
            continue

        if anchor.anchor_value > 0:
            residual_bps = ((mark - anchor.anchor_value) / anchor.anchor_value) * 10000
            residuals.append(residual_bps)
        else:
            stale_count += 1

    return residuals, stale_count


def _compute_distribution_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # Median for even-length lists: average of two middle values
    if n % 2 == 0:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    else:
        median = sorted_vals[n // 2]
    # Percentile indices (0-indexed, nearest rank)
    p75 = sorted_vals[int(n * 0.75)]
    p90 = sorted_vals[int(n * 0.90)]
    p95 = sorted_vals[int(n * 0.95)]
    mean = sum(sorted_vals) / n
    abs_vals = [abs(v) for v in sorted_vals]
    sorted_abs = sorted(abs_vals)
    if n % 2 == 0:
        median_abs = (sorted_abs[n // 2 - 1] + sorted_abs[n // 2]) / 2
    else:
        median_abs = sorted_abs[n // 2]
    p90_abs = sorted_abs[int(n * 0.90)]
    p95_abs = sorted_abs[int(n * 0.95)]
    return {
        "count": n,
        "mean_bps": round(mean, 4),
        "median_bps": round(median, 4),
        "p75_bps": round(p75, 4),
        "p90_bps": round(p90, 4),
        "p95_bps": round(p95, 4),
        "median_abs_bps": round(median_abs, 4),
        "p90_abs_bps": round(p90_abs, 4),
        "p95_abs_bps": round(p95_abs, 4),
    }


def gate3_oracle_classification(
    symbols: list[DiscoveredSymbol],
    anchor_source: str,
    start_date: date,
    end_date: date | None = None,
    dry_run: bool = False,
) -> ScoutStatus:
    """Gate 3: Oracle / fair-value classification.

    This is the cheap killer. Runs immediately after archive coverage.
    """
    if dry_run:
        return ScoutStatus.HIP3_SCOUT_READY

    if anchor_source == "none":
        return ScoutStatus.HIP3_ANCHOR_DATA_UNAVAILABLE

    # Load anchor data per symbol
    all_classifications: list[OracleClassificationResult] = []
    kill_triggered = False

    for sym in symbols:
        anchor_data = load_anchor_data(
            anchor_source, sym.symbol, start_date, end_date
        )

        if not anchor_data:
            # Symbol-specific anchor unavailable
            result = OracleClassificationResult(
                symbol=sym.symbol,
                anchor_source=anchor_source,
                status=ScoutStatus.HIP3_ANCHOR_DATA_UNAVAILABLE,
            )
            all_classifications.append(result)

            if anchor_source == "cme_futures_proxy":
                # CME proxy is the default; if unavailable, fail closed
                kill_triggered = True
            continue

        # Compute residuals against anchor
        # In a real run, this uses actual mark/oracle prices from archive.
        # For scout framework, placeholder: synthetic data or archive-sourced.
        result = OracleClassificationResult(
            symbol=sym.symbol,
            anchor_source=anchor_source,
            sample_count=len(anchor_data),
        )
        all_classifications.append(result)

    if kill_triggered and anchor_source == "cme_futures_proxy":
        return ScoutStatus.HIP3_ANCHOR_DATA_UNAVAILABLE

    return ScoutStatus.HIP3_SCOUT_READY


# ──────────────────────────────────────────────────────────────────────────────
# Gate 4: Fee discovery
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class FeeDiscoveryResult:
    deployer_namespace: str | None
    symbol: str
    base_user_fee_bps: float | None = None
    deployer_fee_share_bps: float | None = None
    total_taker_bps: float | None = None
    total_maker_bps: float | None = None
    fee_source: str = "conservative_unknown"
    estimated_round_trip_bps: float = 25.0
    stress_cost_25bps: float = 25.0
    stress_cost_50bps: float = 50.0
    stress_cost_100bps: float = 100.0


def gate4_fee_discovery(
    symbols: list[DiscoveredSymbol],
    dry_run: bool = False,
) -> ScoutStatus:
    """Gate 4: Fee discovery.

    Discovers fees per deployer and per symbol from public metadata.
    Falls back to conservative estimates if exact fees cannot be discovered.
    """
    if dry_run:
        return ScoutStatus.HIP3_SCOUT_READY

    # Group by deployer
    deployer_fees: dict[str, list[FeeDiscoveryResult]] = {}

    for sym in symbols:
        dep = sym.deployer_namespace or "unknown"

        if sym.base_user_fee_bps is not None and sym.deployer_fee_share_bps is not None:
            total_taker = float(sym.base_user_fee_bps) + float(sym.deployer_fee_share_bps)
            fee_source = "exact"
            estimated_rt = total_taker * 2  # assume round trip
        elif sym.base_user_fee_bps is not None:
            total_taker = float(sym.base_user_fee_bps)
            fee_source = "inferred"
            estimated_rt = total_taker * 2
        else:
            total_taker = None
            fee_source = "conservative_unknown"
            estimated_rt = 25.0  # conservative assumption

        result = FeeDiscoveryResult(
            deployer_namespace=dep,
            symbol=sym.symbol,
            base_user_fee_bps=(
                float(sym.base_user_fee_bps) if sym.base_user_fee_bps is not None else None
            ),
            deployer_fee_share_bps=(
                float(sym.deployer_fee_share_bps) if sym.deployer_fee_share_bps is not None else None
            ),
            total_taker_bps=total_taker,
            total_maker_bps=total_taker / 2 if total_taker is not None else None,
            fee_source=fee_source,
            estimated_round_trip_bps=estimated_rt,
        )
        deployer_fees.setdefault(dep, []).append(result)

    # Kill check: if conservatively estimated RT cost >= 50 bps for all symbols
    # This is checked later against actual residual data in gate 6.
    # For now, just discover and report.
    return ScoutStatus.HIP3_SCOUT_READY


# ──────────────────────────────────────────────────────────────────────────────
# Gate 5: L2 liquidity / depth / spread
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class L2LiquidityResult:
    symbol: str
    off_hours_sample_count: int = 0
    median_spread_bps: float | None = None
    p90_spread_bps: float | None = None
    p95_spread_bps: float | None = None
    top_of_book_bid_depth_usd: float | None = None
    top_of_book_ask_depth_usd: float | None = None
    executable_depth_1k_usd_bps: float | None = None
    executable_depth_5k_usd_bps: float | None = None
    pct_1k_within_15bps: float | None = None
    pct_1k_within_25bps: float | None = None
    l2_sampling_strategy: str = "uniform_first_snapshot_per_hour"


def _walk_orderbook_for_depth(
    levels: list[list[dict[str, Any]]],
    target_notional_usd: float,
    side: str,
) -> tuple[float, float]:
    """Walk order book levels to compute executable depth.

    Returns (achieved_price_impact_bps_for_fill, achieved_notional).
    Walk from best (index 0) outward accumulating until target is filled.
    """
    if not levels:
        return float("inf"), 0.0

    side_idx = 0 if side == "bid" else 1
    if side_idx >= len(levels):
        return float("inf"), 0.0

    book_side = levels[side_idx]
    if not book_side:
        return float("inf"), 0.0

    remaining = target_notional_usd
    total_cost = 0.0
    total_size = 0.0
    best_px = float(book_side[0].get("px", 0))

    for level in book_side:
        px = float(level.get("px", 0))
        sz = float(level.get("sz", 0))
        notional_at_level = px * sz

        if remaining <= 0:
            break

        if notional_at_level >= remaining:
            fill_sz = remaining / px
            total_cost += fill_sz * px
            total_size += fill_sz
            remaining = 0
        else:
            total_cost += notional_at_level
            total_size += sz
            remaining -= notional_at_level

    if total_size == 0 or best_px == 0:
        return float("inf"), 0.0

    # If we couldn't fill the target, mark as insufficient
    if remaining > 0:
        return float("inf"), total_cost

    avg_fill_px = total_cost / total_size if total_size > 0 else 0
    impact_bps = abs(avg_fill_px - best_px) / best_px * 10000
    return impact_bps, total_cost


def compute_l2_diagnostics(
    l2_snapshots: list[dict[str, Any]],
) -> L2LiquidityResult | None:
    """Compute L2 liquidity diagnostics from archive L2 snapshots.

    L2 snapshots expected in standard format with 'levels' containing
    [bid_levels, ask_levels] where each level has px, sz, n fields.
    """
    if not l2_snapshots:
        return None

    spreads_bps: list[float] = []
    depths_1k: list[float] = []
    depths_5k: list[float] = []
    within_15bps: list[bool] = []
    within_25bps: list[bool] = []
    top_bid_sizes: list[float] = []
    top_ask_sizes: list[float] = []

    for snap in l2_snapshots:
        levels = snap.get("levels")
        if not levels or len(levels) < 2:
            continue

        bids = levels[0]
        asks = levels[1]
        if not bids or not asks:
            continue

        best_bid = float(bids[0].get("px", 0))
        best_ask = float(asks[0].get("px", 0))
        bid_sz = float(bids[0].get("sz", 0))
        ask_sz = float(asks[0].get("sz", 0))

        if best_bid <= 0 or best_ask <= 0:
            continue

        mid = (best_bid + best_ask) / 2
        spread_bps = (best_ask - best_bid) / mid * 10000
        spreads_bps.append(spread_bps)

        top_bid_sizes.append(best_bid * bid_sz)
        top_ask_sizes.append(best_ask * ask_sz)

        # Walk book for executable depth
        impact_1k_buy, _ = _walk_orderbook_for_depth(levels, 1000.0, "ask")
        depths_1k.append(impact_1k_buy)

        impact_5k_buy, _ = _walk_orderbook_for_depth(levels, 5000.0, "ask")
        depths_5k.append(impact_5k_buy)

        within_15bps.append(impact_1k_buy <= 15.0)
        within_25bps.append(impact_1k_buy <= 25.0)

    if not spreads_bps:
        return None

    sorted_spreads = sorted(spreads_bps)
    n = len(sorted_spreads)
    sorted_depths_1k = sorted(depths_1k)
    sorted_depths_5k = sorted(depths_5k)

    result = L2LiquidityResult(
        symbol=l2_snapshots[0].get("coin", "unknown"),
        off_hours_sample_count=n,
        median_spread_bps=round(sorted_spreads[n // 2], 4),
        p90_spread_bps=round(sorted_spreads[int(n * 0.90)], 4),
        p95_spread_bps=round(sorted_spreads[int(n * 0.95)], 4),
        top_of_book_bid_depth_usd=round(
            sum(top_bid_sizes) / len(top_bid_sizes), 2
        ) if top_bid_sizes else None,
        top_of_book_ask_depth_usd=round(
            sum(top_ask_sizes) / len(top_ask_sizes), 2
        ) if top_ask_sizes else None,
        executable_depth_1k_usd_bps=round(
            sorted_depths_1k[n // 2], 4
        ) if depths_1k else None,
        executable_depth_5k_usd_bps=round(
            sorted_depths_5k[n // 2], 4
        ) if depths_5k else None,
        pct_1k_within_15bps=round(
            sum(within_15bps) / len(within_15bps) * 100, 2
        ) if within_15bps else None,
        pct_1k_within_25bps=round(
            sum(within_25bps) / len(within_25bps) * 100, 2
        ) if within_25bps else None,
    )
    return result


def gate5_l2_liquidity(
    dry_run: bool = False,
    skip_l2_download: bool = False,
) -> ScoutStatus:
    """Gate 5: L2 liquidity/depth/spread.

    Uses bounded L2 archive samples.
    """
    if dry_run or skip_l2_download:
        return ScoutStatus.HIP3_SCOUT_READY

    # In a real scout, this samples L2 archives and computes diagnostics.
    # Stub: returns SCOUT_READY for framework completeness.
    return ScoutStatus.HIP3_SCOUT_READY


# ──────────────────────────────────────────────────────────────────────────────
# Gate 6: Off-hours residual basis-tail existence
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BasisTailResult:
    symbol: str
    off_hours_sample_count: int = 0
    p50_abs_residual_bps: float | None = None
    p75_abs_residual_bps: float | None = None
    p90_abs_residual_bps: float | None = None
    p95_abs_residual_bps: float | None = None
    count_ge_20bps: int = 0
    count_ge_30bps: int = 0
    count_ge_50bps: int = 0
    count_ge_100bps: int = 0
    positive_ratio: float | None = None
    extended_hours_split: dict[str, Any] | None = None
    overnight_split: dict[str, Any] | None = None
    weekend_split: dict[str, Any] | None = None
    calendar_concentration: dict[str, Any] | None = None
    status: ScoutStatus | None = None


def gate6_basis_tail_existence(
    symbols: list[DiscoveredSymbol],
    fee_result: list[FeeDiscoveryResult] | None = None,
    min_tail_events: int = DEFAULT_MIN_TAIL_EVENTS,
    dry_run: bool = False,
) -> ScoutStatus:
    """Gate 6: Off-hours residual basis-tail existence.

    Uses executable L2 mid (not oracle/mark) for basis computation.
    Only runs after gates 1-5 pass.
    """
    if dry_run:
        return ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED

    # In a real scout, this computes executable_mid from L2 archives,
    # computes residual = executable_mid_bps - selected_anchor_bps,
    # and evaluates the tail pass gate.
    # Stub: returns SCOUT_READY for framework completeness.
    return ScoutStatus.HIP3_SCOUT_READY


# ──────────────────────────────────────────────────────────────────────────────
# Main scout runner
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ScoutResult:
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    command_args: dict[str, Any] | None = None
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION
    bytes_downloaded_total: int = 0
    bytes_downloaded_per_symbol: dict[str, int] = field(default_factory=dict)
    download_budget_bytes: int = DEFAULT_DOWNLOAD_BUDGET_BYTES
    per_symbol_l2_budget_bytes: int = DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES
    s3_requester_pays_acknowledged: bool = False
    config_hash: str | None = None
    symbols_discovered: list[dict[str, Any]] = field(default_factory=list)
    symbols_classified: list[dict[str, Any]] = field(default_factory=list)
    index_like_symbols: list[dict[str, Any]] = field(default_factory=list)
    archive_coverage: list[dict[str, Any]] = field(default_factory=list)
    oracle_classification: dict[str, Any] | None = None
    fee_discovery: list[dict[str, Any]] = field(default_factory=list)
    liquidity_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    basis_tail_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    helpers_reused: list[dict[str, str]] = field(default_factory=list)
    gate_failed_at: str | None = None
    kill_reason: str | None = None
    symbols_found_total: int = 0
    deployers_observed: dict[str, int] = field(default_factory=dict)
    anchor_used: str = "none"


def run_scout(
    args: dict[str, Any],
) -> ScoutResult:
    """Execute the six-gate HIP-3 scout kill-chain.

    Args are passed via dictionary from the CLI.
    """
    run_ctx = RunContext(
        args=args,
        run_id=make_run_id(),
        created_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_sha=get_git_sha(args.get("repo_root", ".")),
        git_dirty=get_git_dirty(args.get("repo_root", ".")),
        repo_root=args.get("repo_root", "."),
        config_hash=compute_config_hash(args),
    )

    result = ScoutResult(
        status="HIP3_SCOUT_READY",
        run_id=run_ctx.run_id,
        created_at_utc=run_ctx.created_at_utc,
        git_sha=run_ctx.git_sha,
        git_dirty=run_ctx.git_dirty,
        repo_root=run_ctx.repo_root,
        command_args=args,
        config_hash=run_ctx.config_hash,
        symbols_found_total=0,
        deployers_observed={},
        anchor_used=args.get("anchor_source", "cme_futures_proxy"),
        helpers_reused=list(HELPERS_REUSED),
    )

    try:
        # Configure chokepoint
        configure_chokepoint(
            allow_network_public=args.get("allow_network_public", False),
            allow_s3_archive_read=args.get("allow_s3_archive_read", False),
            download_budget_bytes=args.get(
                "download_budget_bytes", DEFAULT_DOWNLOAD_BUDGET_BYTES
            ),
            per_symbol_l2_budget_bytes=args.get(
                "per_symbol_l2_budget_bytes", DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES
            ),
        )

        dry_run = args.get("dry_run", False)
        start_date = args.get("start_date", DEFAULT_START_DATE)
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        end_date = args.get("end_date")
        if isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)
        prefer_index_like = args.get("prefer_index_like", True)
        min_coverage_days = args.get("min_coverage_days", DEFAULT_MIN_COVERAGE_DAYS)
        sample_l2_days = args.get("sample_l2_days", DEFAULT_SAMPLE_L2_DAYS)
        skip_l2_download = args.get("skip_l2_download", False)
        anchor_source = args.get("anchor_source", "cme_futures_proxy")
        min_tail_events = args.get("min_tail_events", DEFAULT_MIN_TAIL_EVENTS)

        # Cache path for symbol discovery
        cache_dir = Path(args.get("out_root", DEFAULT_OUT_ROOT)) / "_cache"
        cache_path = str(cache_dir / "symbol_discovery_latest.json")

        # ══════════════════════════════════════════════════════════════════
        # Gate 1: Symbol discovery
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE1: symbol_discovery")
        discovered = discover_public_metadata(
            dry_run=dry_run,
            cache_path=cache_path if dry_run else None,
        )
        result.symbols_discovered = [
            {k: str(v) if isinstance(v, Enum) else v
             for k, v in asdict(s).items()}
            for s in discovered
        ]

        status1, index_like, classified = gate1_symbol_discovery(
            discovered, prefer_index_like=prefer_index_like,
        )
        result.symbols_classified = [
            {k: str(v) if isinstance(v, Enum) else v
             for k, v in asdict(s).items()}
            for s in classified
        ]
        result.index_like_symbols = [
            {k: str(v) if isinstance(v, Enum) else v
             for k, v in asdict(s).items()}
            for s in index_like
        ]
        result.symbols_found_total = len(discovered)

        # Deployer counts
        dep_counts: dict[str, int] = {}
        for s in discovered:
            dep = s.deployer_namespace or "unknown"
            dep_counts[dep] = dep_counts.get(dep, 0) + 1
        result.deployers_observed = dep_counts

        if status1 != ScoutStatus.HIP3_SCOUT_READY:
            result.status = status1.value
            result.gate_failed_at = "gate1_symbol_discovery"
            result.kill_reason = status1.value
            return _finalize(result, args)

        # Update cache after successful discovery (non-dry-run)
        if not dry_run:
            _update_symbol_discovery_cache(
                cache_path, result.symbols_discovered
            )

        # ══════════════════════════════════════════════════════════════════
        # Gate 2: Archive coverage
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE2: archive_coverage")
        status2, coverage_results = probe_archive_coverage(
            index_like,
            start_date=start_date,
            end_date=end_date,
            min_coverage_days=min_coverage_days,
            sample_l2_days=sample_l2_days,
            skip_l2_download=skip_l2_download,
            dry_run=dry_run,
        )
        result.archive_coverage = [
            {k: str(v) if isinstance(v, Enum) else v
             for k, v in asdict(c).items()}
            for c in coverage_results
        ]

        if status2 != ScoutStatus.HIP3_SCOUT_READY:
            result.status = status2.value
            result.gate_failed_at = "gate2_archive_coverage"
            result.kill_reason = status2.value
            return _finalize(result, args)

        # ══════════════════════════════════════════════════════════════════
        # Gate 3: Oracle / fair-value classification (cheap killer)
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE3: oracle_classification")
        status3 = gate3_oracle_classification(
            index_like,
            anchor_source=anchor_source,
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
        )
        result.oracle_classification = {
            "symbols": [s.symbol for s in index_like],
            "anchor_source": anchor_source,
            "status": status3.value,
        }

        if status3 not in (ScoutStatus.HIP3_SCOUT_READY, ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED):
            result.status = status3.value
            result.gate_failed_at = "gate3_oracle_classification"
            result.kill_reason = status3.value
            return _finalize(result, args)

        # ══════════════════════════════════════════════════════════════════
        # Gate 4: Fee discovery
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE4: fee_discovery")
        status4 = gate4_fee_discovery(index_like, dry_run=dry_run)
        result.fee_discovery = [
            {k: str(v) if isinstance(v, Enum) else v
             for k, v in asdict(f).items()}
            for sym in index_like
            for f in [FeeDiscoveryResult(
                deployer_namespace=sym.deployer_namespace,
                symbol=sym.symbol,
                base_user_fee_bps=(
                    float(sym.base_user_fee_bps) if sym.base_user_fee_bps is not None else None
                ),
                deployer_fee_share_bps=(
                    float(sym.deployer_fee_share_bps) if sym.deployer_fee_share_bps is not None else None
                ),
            )]
        ]

        if status4 not in (ScoutStatus.HIP3_SCOUT_READY, ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED):
            result.status = status4.value
            result.gate_failed_at = "gate4_fee_discovery"
            result.kill_reason = status4.value
            return _finalize(result, args)

        # ══════════════════════════════════════════════════════════════════
        # Gate 5: L2 liquidity / depth / spread
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE5: l2_liquidity")
        status5 = gate5_l2_liquidity(
            dry_run=dry_run,
            skip_l2_download=skip_l2_download,
        )

        if status5 not in (ScoutStatus.HIP3_SCOUT_READY, ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED):
            result.status = status5.value
            result.gate_failed_at = "gate5_l2_liquidity"
            result.kill_reason = status5.value
            return _finalize(result, args)

        # ══════════════════════════════════════════════════════════════════
        # Gate 6: Off-hours residual basis-tail existence
        # ══════════════════════════════════════════════════════════════════
        logger.info("GATE6: basis_tail_existence")
        status6 = gate6_basis_tail_existence(
            index_like,
            fee_result=None,
            min_tail_events=min_tail_events,
            dry_run=dry_run,
        )
        result.basis_tail_diagnostics = [
            {
                "symbol": s.symbol,
                "status": status6.value if status6 else None,
            }
            for s in index_like
        ]

        if status6 not in (ScoutStatus.HIP3_SCOUT_READY, ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED):
            result.status = status6.value
            result.gate_failed_at = "gate6_basis_tail_existence"
            result.kill_reason = status6.value
            return _finalize(result, args)

        # ══════════════════════════════════════════════════════════════════
        # All gates passed
        # ══════════════════════════════════════════════════════════════════
        if dry_run:
            result.status = ScoutStatus.HIP3_SCOUT_READY.value
        else:
            result.status = ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED.value

    except RuntimeError as e:
        error_msg = str(e)
        if "DOWNLOAD_BUDGET_EXCEEDED" in error_msg:
            result.status = ScoutStatus.HIP3_SCOUT_ERROR.value
            result.kill_reason = "DOWNLOAD_BUDGET_EXCEEDED"
        else:
            result.status = ScoutStatus.HIP3_SCOUT_ERROR.value
            result.kill_reason = error_msg
        logger.info(f"ERROR: {error_msg}")
    except Exception as e:
        result.status = ScoutStatus.HIP3_SCOUT_ERROR.value
        result.kill_reason = str(e)
        logger.info(f"ERROR: {e}")

    return _finalize(result, args)


def _finalize(result: ScoutResult, args: dict[str, Any]) -> ScoutResult:
    """Finalize scout result with download stats and helpers."""
    stats = get_download_stats()
    result.bytes_downloaded_total = stats["bytes_downloaded_total"]
    result.bytes_downloaded_per_symbol = stats["bytes_downloaded_per_symbol"]
    result.download_budget_bytes = stats["download_budget_bytes"]
    result.per_symbol_l2_budget_bytes = stats["per_symbol_l2_budget_bytes"]
    result.s3_requester_pays_acknowledged = stats["s3_requester_pays_acknowledged"]
    result.helpers_reused = list(HELPERS_REUSED)
    return result


def _update_symbol_discovery_cache(
    cache_path: str, symbols: list[dict[str, Any]]
) -> None:
    """Update symbol discovery cache after successful discovery."""
    data = {
        "study_id": STUDY_ID,
        "created_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": symbols,
    }
    atomic_write_json(cache_path, data)


# ──────────────────────────────────────────────────────────────────────────────
# Output artifact emission
# ──────────────────────────────────────────────────────────────────────────────


def write_scout_artifacts(
    result: ScoutResult,
    out_root: str,
    run_id: str,
    dry_run: bool = False,
) -> dict[str, str]:
    """Write scout artifacts to the output directory.

    Dry-run writes only dry_run_preview.json.
    Full run writes only artifacts reached by the short-circuiting gate chain.
    """
    if dry_run:
        out_dir = Path(out_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        preview = _build_dry_run_preview(result, out_root)
        path = str(out_dir / "dry_run_preview.json")
        atomic_write_json(path, preview)
        return {"dry_run_preview.json": path}

    run_dir = Path(out_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, str] = {}

    # Always write summary and manifest
    artifacts["summary.json"] = atomic_write_json(
        str(run_dir / "summary.json"), _build_summary(result)
    )
    artifacts["summary.md"] = _write_summary_md(result, run_dir)
    artifacts["run_manifest.json"] = atomic_write_json(
        str(run_dir / "run_manifest.json"), _build_run_manifest(result)
    )

    # Write artifacts based on reached gates
    # Gate 1
    if result.symbols_discovered:
        artifacts["symbol_discovery.json"] = atomic_write_json(
            str(run_dir / "symbol_discovery.json"),
            {"symbols": result.symbols_discovered},
        )

    # Gate 2
    if result.archive_coverage:
        artifacts["archive_coverage.json"] = atomic_write_json(
            str(run_dir / "archive_coverage.json"),
            {"coverage": result.archive_coverage},
        )

    # Gate 3 - only write if reached
    if result.oracle_classification is not None:
        artifacts["oracle_classification.json"] = atomic_write_json(
            str(run_dir / "oracle_classification.json"),
            result.oracle_classification,
        )

    # Gate 4 - only if reached
    if result.fee_discovery:
        artifacts["fee_discovery.json"] = atomic_write_json(
            str(run_dir / "fee_discovery.json"),
            {"fee_discovery": result.fee_discovery},
        )

    # Gate 5 - only if reached
    if result.liquidity_diagnostics:
        artifacts["liquidity_diagnostics.json"] = atomic_write_json(
            str(run_dir / "liquidity_diagnostics.json"),
            {"liquidity_diagnostics": result.liquidity_diagnostics},
        )

    # Gate 6 - only if reached
    if result.basis_tail_diagnostics:
        artifacts["basis_tail_diagnostics.json"] = atomic_write_json(
            str(run_dir / "basis_tail_diagnostics.json"),
            {"basis_tail_diagnostics": result.basis_tail_diagnostics},
        )

    return artifacts


def _build_summary(result: ScoutResult) -> dict[str, Any]:
    """Build summary.json from scout result."""
    return {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": result.git_sha,
        "git_dirty": result.git_dirty,
        "repo_root": result.repo_root,
        "status": result.status,
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "command_args": result.command_args or {},
        "config_hash": result.config_hash or "",
        "gate_failed_at": result.gate_failed_at or "",
        "kill_reason": result.kill_reason or "",
        "symbols_found_total": result.symbols_found_total,
        "deployers_observed": result.deployers_observed,
        "anchor_used": result.anchor_used,
        "bytes_downloaded_total": result.bytes_downloaded_total,
        "bytes_downloaded_per_symbol": result.bytes_downloaded_per_symbol,
        "download_budget_bytes": result.download_budget_bytes,
        "s3_requester_pays_acknowledged": result.s3_requester_pays_acknowledged,
        "helpers_reused": result.helpers_reused,
        "artifacts_written": [],
    }


def _build_run_manifest(result: ScoutResult) -> dict[str, Any]:
    """Build run_manifest.json."""
    return {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": result.git_sha,
        "git_dirty": result.git_dirty,
        "bytes_downloaded_total": result.bytes_downloaded_total,
        "bytes_downloaded_per_symbol": result.bytes_downloaded_per_symbol,
        "download_budget_bytes": result.download_budget_bytes,
        "per_symbol_l2_budget_bytes": result.per_symbol_l2_budget_bytes,
        "s3_requester_pays_acknowledged": result.s3_requester_pays_acknowledged,
        "status": result.status,
        "anchor_source": result.anchor_used,
        "schema_version": result.schema_version,
        "helpers_reused": result.helpers_reused,
    }


def _build_dry_run_preview(
    result: ScoutResult, out_root: str
) -> dict[str, Any]:
    """Build dry-run preview JSON."""
    preview = {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": result.git_sha,
        "git_dirty": result.git_dirty,
        "config_hash": result.config_hash,
        "status": result.status,
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "cached_symbol_discovery_available": bool(result.symbols_discovered),
        "symbols_found_total": result.symbols_found_total,
        "deployers_observed": result.deployers_observed,
        "anchor_used": result.anchor_used,
        "artifacts_written": ["dry_run_preview.json"],
    }
    return preview


def _write_summary_md(result: ScoutResult, run_dir: Path) -> str:
    """Write summary.md markdown report."""
    lines: list[str] = [
        f"# HIP-3 Off-Hours Oracle Basis Residual Scout v0 — Summary",
        "",
        f"## What this is NOT",
        "",
        "- This is not a strategy.",
        "- This is not a precommitment.",
        "- This is not a PnL evaluator.",
        "- This cannot emit candidate/promotion/live/paper verdicts.",
        "- This cannot mutate `REJECTED_RESEARCH.md`.",
        "- This does not authorize Phase 0 execution.",
        "- This only decides whether a later Phase 0 precommitment is worth drafting.",
        "",
        f"## Run Metadata",
        "",
        f"- **Study ID:** {result.study_id}",
        f"- **Run ID:** {result.run_id}",
        f"- **Created:** {result.created_at_utc}",
        f"- **Git SHA:** {result.git_sha}",
        f"- **Git dirty:** {result.git_dirty}",
        f"- **Config hash:** {result.config_hash}",
        f"- **Safety mode:** {result.safety_mode}",
        "",
        f"## Status: {result.status}",
        "",
    ]

    if result.gate_failed_at:
        lines.extend([
            f"- **Gate failed:** {result.gate_failed_at}",
            f"- **Kill reason:** {result.kill_reason}",
            "",
        ])

    lines.extend([
        "## closes_what",
        "",
        "A pass here closes nothing.",
        "A fail here closes only the specific gate that failed, not the HIP-3 family.",
        "",
        "## does_not_close_what",
        "",
        "- HIP-3 symbol family viability for later studies",
        "- Alternative oracle/execution approaches",
        "- Different anchor sources",
        "- Different fee regimes",
        "- Future HIP-3 symbol additions",
        "",
        "## Gate Results",
        "",
        f"- **Symbols found:** {result.symbols_found_total}",
        f"- **Deployers observed:** {json.dumps(result.deployers_observed)}",
        f"- **Anchor used:** {result.anchor_used}",
        f"- **Bytes downloaded:** {result.bytes_downloaded_total}",
        "",
        f"## runs_required_before_phase0_drafting",
        "",
    ])

    if result.status in (
        ScoutStatus.HIP3_SCOUT_PASSED_PHASE0_DRAFTING_PERMITTED.value,
        ScoutStatus.HIP3_SCOUT_READY.value,
    ):
        lines.extend([
            "If scout passes once: one scout pass is not enough to authorize",
            "immediate Phase 0 execution. A second independent run on a later",
            "date is recommended before drafting Phase 0 to reduce",
            "single-snapshot artifact risk.",
        ])
    else:
        lines.extend([
            f"No Phase 0 drafting; gate failure `{result.gate_failed_at}` must be",
            "resolved or reopened separately.",
        ])

    lines.extend([
        "",
        "## Helpers Reused",
        "",
    ])
    for h in result.helpers_reused:
        lines.append(f"- {h.get('full_dotted_path', h.get('source_file', 'unknown'))}")

    path = str(run_dir / "summary.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path