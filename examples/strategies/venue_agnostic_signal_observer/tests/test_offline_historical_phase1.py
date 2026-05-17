"""Phase 1 offline historical data-lane tests.

All 26 required tests plus the safety scan.
Synthetic/local fixtures only — no network, no auth, no live adapters.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import textwrap
from pathlib import Path
from typing import List

import pytest

from examples.strategies.venue_agnostic_signal_observer.offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    RESOLUTION_AGG_TRADE,
    RESOLUTION_BAR,
    RESOLUTION_TRADE,
    WINDOW_MODE_CAUSAL,
    WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC,
    OfflinePrepareManifest,
    OfflineSourceFile,
    can_promote_from_window_mode,
    validate_family2_signal_variants,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_normalize import (
    to_nanoseconds,
    validate_timestamp_range,
)
from examples.strategies.venue_agnostic_signal_observer.offline_historical_sources import (
    parse_binance_agg_trades,
    parse_binance_klines,
    parse_coinbase_candles,
    parse_kraken_ohlcvt,
    parse_kraken_trades,
)
from examples.strategies.venue_agnostic_signal_observer.offline_corpus_hash import (
    HashCache,
    compute_data_corpus_hash,
    sha256_file,
)
from examples.strategies.venue_agnostic_signal_observer.offline_replay_manifest import (
    assert_compatible_offline_corpus,
    build_manifest,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A known-good epoch in milliseconds: 2024-01-01T00:00:00Z
_T0_MS = 1_704_067_200_000
_T1_MS = _T0_MS + 60_000        # +1 minute
_T0_NS = _T0_MS * 1_000_000
_T1_NS = _T1_MS * 1_000_000
_EXPECTED_START_NS = _T0_NS - 86_400_000_000_000   # 1 day before
_EXPECTED_END_NS = _T1_NS + 86_400_000_000_000     # 1 day after


def _write_tmp(content: str, suffix: str = ".csv") -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    f.write(textwrap.dedent(content))
    f.close()
    return Path(f.name)


def _binance_agg_trades_csv(ts_ms: int = _T0_MS) -> str:
    # agg_trade_id, price, qty, first, last, transact_time, is_buyer_maker
    return f"0,50000.0,0.1,0,0,{ts_ms},False\n"


def _binance_klines_csv(ts_ms: int = _T0_MS) -> str:
    # open_time, o, h, l, c, vol, close_time, qvol, ntrades, ...
    return f"{ts_ms},50000,50100,49900,50050,1.5,{ts_ms+59999},75000,100,0.5,25000,0\n"


def _kraken_ohlcvt_csv(ts_s: float = _T0_MS / 1000) -> str:
    return f"{ts_s},49800,49900,49700,49850,49800,2.3,15\n"


def _coinbase_candles_csv(ts_s: float = _T0_MS / 1000) -> str:
    return f"{ts_s},49700,49900,49750,49850,1.2\n"


def _make_source_file(**overrides) -> OfflineSourceFile:
    defaults = dict(
        path="/tmp/test.csv",
        logical_source_id="test_source",
        venue="binance",
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        source_kind="binance_spot_agg_trades",
        stream_type="agg_trades",
        resolution_type=RESOLUTION_AGG_TRADE,
        timestamp_unit="ms",
        expected_start_ns=_EXPECTED_START_NS,
        expected_end_ns=_EXPECTED_END_NS,
        file_size_bytes=100,
        mtime_ns=1_700_000_000_000_000_000,
        file_sha256="abc123",
        row_count=10,
        data_start_ns=_T0_NS,
        data_end_ns=_T1_NS,
    )
    defaults.update(overrides)
    return OfflineSourceFile(**defaults)


def _make_manifest(**overrides) -> OfflinePrepareManifest:
    sf = _make_source_file()
    defaults = dict(
        run_id="test_run",
        phase="offline_historical_prepare",
        generated_at_utc="2024-01-01T00:00:00+00:00",
        git_sha="deadbeef",
        schema_version=OFFLINE_DATA_SCHEMA_VERSION,
        precommitment_hash=None,
        data_corpus_hash=compute_data_corpus_hash([sf], OFFLINE_DATA_SCHEMA_VERSION),
        source_files=[],
        timestamp_validation={},
        hash_cache_used=True,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1,
        normalized_time_range={},
        stream_counts={},
        resolution_summary={},
        quote_currency_summary={},
        safety="public_data_observer_only",
        forbidden_capabilities_present=False,
        next_phase_allowed=True,
    )
    defaults.update(overrides)
    return OfflinePrepareManifest(**defaults)


# ---------------------------------------------------------------------------
# Test 1: Binance aggTrades — explicit milliseconds
# ---------------------------------------------------------------------------


def test_binance_agg_trades_milliseconds():
    path = _write_tmp(_binance_agg_trades_csv(_T0_MS))
    try:
        result = parse_binance_agg_trades(
            path=path,
            venue="binance",
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            source_kind="binance_spot_agg_trades",
            logical_source_id="test",
            timestamp_unit="ms",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        assert result.row_count == 1
        assert result.trades[0].timestamp_ns == _T0_NS
        assert result.trades[0].resolution_type == RESOLUTION_AGG_TRADE
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: Binance aggTrades — explicit microseconds
# ---------------------------------------------------------------------------


def test_binance_agg_trades_microseconds():
    ts_us = _T0_MS * 1_000   # same moment in microseconds
    path = _write_tmp(_binance_agg_trades_csv(ts_us))
    try:
        result = parse_binance_agg_trades(
            path=path,
            venue="binance",
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            source_kind="binance_spot_agg_trades",
            logical_source_id="test",
            timestamp_unit="us",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        assert result.row_count == 1
        assert result.trades[0].timestamp_ns == _T0_NS
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: Binance timestamp normalization rejects impossible date ranges
# ---------------------------------------------------------------------------


def test_binance_agg_trades_rejects_impossible_timestamp():
    # Raw value of 1 in milliseconds would normalise to 1,000,000 ns — year 1970
    path = _write_tmp("0,50000,0.1,0,0,1,False\n")
    try:
        with pytest.raises(ValueError, match="plausible range"):
            parse_binance_agg_trades(
                path=path,
                venue="binance",
                symbol="BTC/USDT",
                base_asset="BTC",
                quote_asset="USDT",
                source_kind="binance_spot_agg_trades",
                logical_source_id="test",
                timestamp_unit="ms",
                expected_start_ns=_EXPECTED_START_NS,
                expected_end_ns=_EXPECTED_END_NS,
            )
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 4: Binance kline parser marks stream as bar
# ---------------------------------------------------------------------------


def test_binance_klines_resolution_is_bar():
    path = _write_tmp(_binance_klines_csv(_T0_MS))
    try:
        result = parse_binance_klines(
            path=path,
            venue="binance",
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            source_kind="binance_spot_klines",
            logical_source_id="test",
            timestamp_unit="ms",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        assert result.row_count == 1
        assert result.bars[0].resolution_type == RESOLUTION_BAR
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 5: Kraken OHLCVT parser preserves OHLCVT fields
# ---------------------------------------------------------------------------


def test_kraken_ohlcvt_preserves_fields():
    ts_s = _T0_MS / 1000
    path = _write_tmp(f"{ts_s},100,110,90,105,100,2.5,20\n")
    try:
        result = parse_kraken_ohlcvt(
            path=path,
            venue="kraken",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            logical_source_id="test",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        bar = result.bars[0]
        assert bar.open == 100.0
        assert bar.high == 110.0
        assert bar.low == 90.0
        assert bar.close == 105.0
        assert bar.volume == 2.5
        assert bar.trade_count == 20
        assert bar.resolution_type == RESOLUTION_BAR
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 6: Kraken BTC/USD and BTC/USDT remain distinct
# ---------------------------------------------------------------------------


def test_kraken_usd_usdt_distinct():
    ts_s = _T0_MS / 1000
    path_usd = _write_tmp(f"{ts_s},100,110,90,105,100,2.5,10\n")
    path_usdt = _write_tmp(f"{ts_s},100.1,110.1,90.1,105.1,100,2.6,11\n")
    try:
        result_usd = parse_kraken_ohlcvt(
            path=path_usd,
            venue="kraken",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            logical_source_id="kraken_btc_usd",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        result_usdt = parse_kraken_ohlcvt(
            path=path_usdt,
            venue="kraken",
            symbol="BTC/USDT",
            base_asset="BTC",
            quote_asset="USDT",
            logical_source_id="kraken_btc_usdt",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        assert result_usd.bars[0].quote_asset == "USD"
        assert result_usdt.bars[0].quote_asset == "USDT"
        assert result_usd.bars[0].symbol != result_usdt.bars[0].symbol
    finally:
        path_usd.unlink(missing_ok=True)
        path_usdt.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 7: Coinbase BTC-USD parser remains USD reference data
# ---------------------------------------------------------------------------


def test_coinbase_btcusd_is_usd_reference():
    ts_s = _T0_MS / 1000
    path = _write_tmp(f"{ts_s},49700,49900,49750,49850,1.2\n")
    try:
        result = parse_coinbase_candles(
            path=path,
            venue="coinbase",
            symbol="BTC-USD",
            base_asset="BTC",
            quote_asset="USD",
            logical_source_id="coinbase_btc_usd",
            expected_start_ns=_EXPECTED_START_NS,
            expected_end_ns=_EXPECTED_END_NS,
        )
        bar = result.bars[0]
        assert bar.venue == "coinbase"
        assert bar.quote_asset == "USD"
        assert bar.symbol == "BTC-USD"
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 8: Bars are never emitted with trade/agg_trade resolution
# ---------------------------------------------------------------------------


def test_bars_never_emitted_as_trade_resolution():
    ts_s = _T0_MS / 1000
    path_kr = _write_tmp(f"{ts_s},100,110,90,105,100,2.5,10\n")
    path_cb = _write_tmp(f"{ts_s},49700,49900,49750,49850,1.2\n")
    path_bn = _write_tmp(_binance_klines_csv(_T0_MS))
    try:
        for result in [
            parse_kraken_ohlcvt(
                path=path_kr, venue="k", symbol="X", base_asset="B", quote_asset="USD",
                logical_source_id="k", expected_start_ns=_EXPECTED_START_NS,
                expected_end_ns=_EXPECTED_END_NS,
            ),
            parse_coinbase_candles(
                path=path_cb, venue="c", symbol="X", base_asset="B", quote_asset="USD",
                logical_source_id="c", expected_start_ns=_EXPECTED_START_NS,
                expected_end_ns=_EXPECTED_END_NS,
            ),
            parse_binance_klines(
                path=path_bn, venue="b", symbol="X", base_asset="B", quote_asset="USDT",
                source_kind="binance_spot_klines", logical_source_id="bn",
                timestamp_unit="ms", expected_start_ns=_EXPECTED_START_NS,
                expected_end_ns=_EXPECTED_END_NS,
            ),
        ]:
            for bar in result.bars:
                assert bar.resolution_type == RESOLUTION_BAR
                assert bar.resolution_type not in (RESOLUTION_TRADE, RESOLUTION_AGG_TRADE)
    finally:
        for p in (path_kr, path_cb, path_bn):
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 9: File SHA-256 changes when content changes
# ---------------------------------------------------------------------------


def test_sha256_changes_on_content_change():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as f:
        f.write(b"hello world\n")
        path = Path(f.name)
    try:
        h1 = sha256_file(path)
        path.write_bytes(b"different content\n")
        h2 = sha256_file(path)
        assert h1 != h2
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests 10–12: Hash cache behaviour
# ---------------------------------------------------------------------------


def test_hash_cache_reuses_on_same_path_size_mtime(tmp_path):
    cache_file = tmp_path / "cache.json"
    p = tmp_path / "data.csv"
    p.write_bytes(b"some data")

    cache = HashCache(cache_path=cache_file)
    sha1, reused1 = cache.get_or_compute(p)
    assert not reused1

    cache2 = HashCache(cache_path=cache_file)
    sha2, reused2 = cache2.get_or_compute(p)
    assert reused2
    assert sha1 == sha2


def test_hash_cache_recomputes_on_size_change(tmp_path):
    cache_file = tmp_path / "cache.json"
    p = tmp_path / "data.csv"
    p.write_bytes(b"original")

    cache = HashCache(cache_path=cache_file)
    sha1, _ = cache.get_or_compute(p)

    p.write_bytes(b"longer content here!")
    cache2 = HashCache(cache_path=cache_file)
    sha2, reused = cache2.get_or_compute(p)
    assert not reused
    assert sha1 != sha2


def test_hash_cache_recomputes_on_mtime_change(tmp_path):
    cache_file = tmp_path / "cache.json"
    p = tmp_path / "data.csv"
    p.write_bytes(b"same content")

    cache = HashCache(cache_path=cache_file)
    sha1, _ = cache.get_or_compute(p)

    # Advance mtime by 10 seconds without changing content
    stat = p.stat()
    os.utime(p, (stat.st_atime + 10, stat.st_mtime + 10))

    cache2 = HashCache(cache_path=cache_file)
    sha2, reused = cache2.get_or_compute(p)
    assert not reused
    assert sha1 == sha2  # same hash but recomputed because mtime differed


# ---------------------------------------------------------------------------
# Tests 13–16: data_corpus_hash sensitivity
# ---------------------------------------------------------------------------


def test_corpus_hash_changes_on_sha_change():
    sf1 = _make_source_file(file_sha256="aaa")
    sf2 = _make_source_file(file_sha256="bbb")
    assert (
        compute_data_corpus_hash([sf1], OFFLINE_DATA_SCHEMA_VERSION)
        != compute_data_corpus_hash([sf2], OFFLINE_DATA_SCHEMA_VERSION)
    )


def test_corpus_hash_changes_on_row_count_change():
    sf1 = _make_source_file(row_count=10)
    sf2 = _make_source_file(row_count=11)
    assert (
        compute_data_corpus_hash([sf1], OFFLINE_DATA_SCHEMA_VERSION)
        != compute_data_corpus_hash([sf2], OFFLINE_DATA_SCHEMA_VERSION)
    )


def test_corpus_hash_changes_on_timestamp_unit_change():
    sf1 = _make_source_file(timestamp_unit="ms")
    sf2 = _make_source_file(timestamp_unit="us")
    assert (
        compute_data_corpus_hash([sf1], OFFLINE_DATA_SCHEMA_VERSION)
        != compute_data_corpus_hash([sf2], OFFLINE_DATA_SCHEMA_VERSION)
    )


def test_corpus_hash_changes_on_schema_version_change():
    sf = _make_source_file()
    h1 = compute_data_corpus_hash([sf], "offline_historical_v1")
    h2 = compute_data_corpus_hash([sf], "offline_historical_v2")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Tests 17–18: Manifest fields
# ---------------------------------------------------------------------------


def test_manifest_includes_data_corpus_hash():
    sf = _make_source_file()
    m = build_manifest(
        run_id="r1",
        git_sha="abc",
        source_files=[sf],
        trades_by_stream={},
        bars_by_stream={},
        hash_cache_used=True,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1,
        precommitment_hash=None,
    )
    assert m.data_corpus_hash
    assert len(m.data_corpus_hash) == 64  # SHA-256 hex


def test_manifest_includes_nullable_precommitment_hash():
    sf = _make_source_file()
    m_no_pre = build_manifest(
        run_id="r1", git_sha="abc", source_files=[sf],
        trades_by_stream={}, bars_by_stream={},
        hash_cache_used=True, hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1, precommitment_hash=None,
    )
    assert m_no_pre.precommitment_hash is None

    m_with_pre = build_manifest(
        run_id="r1", git_sha="abc", source_files=[sf],
        trades_by_stream={}, bars_by_stream={},
        hash_cache_used=True, hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1, precommitment_hash="deadcafe",
    )
    assert m_with_pre.precommitment_hash == "deadcafe"


# ---------------------------------------------------------------------------
# Test 19: Runner writes run-specific directories, no default overwrite
# ---------------------------------------------------------------------------


def test_runner_writes_run_dir_no_overwrite(tmp_path):
    sf = _make_source_file()
    m = build_manifest(
        run_id="test_run_19",
        git_sha="abc",
        source_files=[sf],
        trades_by_stream={},
        bars_by_stream={},
        hash_cache_used=False,
        hash_cache_entries_reused=0,
        hash_cache_entries_recomputed=1,
        precommitment_hash=None,
    )
    manifest_path = write_manifest(m, tmp_path, overwrite=False)
    assert manifest_path.exists()

    # Second write to same run_id must fail without overwrite
    with pytest.raises(FileExistsError):
        write_manifest(m, tmp_path, overwrite=False)


# ---------------------------------------------------------------------------
# Tests 20–22: Corpus compatibility guard
# ---------------------------------------------------------------------------


def test_corpus_guard_rejects_different_data_corpus_hash():
    sf1 = _make_source_file(file_sha256="aaa")
    sf2 = _make_source_file(file_sha256="bbb")
    m1 = _make_manifest(data_corpus_hash=compute_data_corpus_hash([sf1], OFFLINE_DATA_SCHEMA_VERSION))
    m2 = _make_manifest(data_corpus_hash=compute_data_corpus_hash([sf2], OFFLINE_DATA_SCHEMA_VERSION))
    with pytest.raises(ValueError, match="data_corpus_hash"):
        assert_compatible_offline_corpus([m1, m2])


def test_corpus_guard_rejects_different_schema_versions():
    sf = _make_source_file()
    h = compute_data_corpus_hash([sf], OFFLINE_DATA_SCHEMA_VERSION)
    m1 = _make_manifest(data_corpus_hash=h, schema_version="offline_historical_v1")
    m2 = _make_manifest(data_corpus_hash=h, schema_version="offline_historical_v99")
    with pytest.raises(ValueError, match="schema_version"):
        assert_compatible_offline_corpus([m1, m2])


def test_corpus_guard_rejects_different_nonnull_precommitment_hashes():
    sf = _make_source_file()
    h = compute_data_corpus_hash([sf], OFFLINE_DATA_SCHEMA_VERSION)
    m1 = _make_manifest(data_corpus_hash=h, precommitment_hash="hash_a")
    m2 = _make_manifest(data_corpus_hash=h, precommitment_hash="hash_b")
    with pytest.raises(ValueError, match="precommitment_hash"):
        assert_compatible_offline_corpus([m1, m2])


def test_corpus_guard_allows_null_precommitment_vs_nonnull():
    """null vs non-null precommitment does not trigger rejection."""
    sf = _make_source_file()
    h = compute_data_corpus_hash([sf], OFFLINE_DATA_SCHEMA_VERSION)
    m1 = _make_manifest(data_corpus_hash=h, precommitment_hash=None)
    m2 = _make_manifest(data_corpus_hash=h, precommitment_hash="some_hash")
    assert_compatible_offline_corpus([m1, m2])  # should not raise


# ---------------------------------------------------------------------------
# Test 23: Retrospective diagnostic window mode blocks promotion
# ---------------------------------------------------------------------------


def test_retrospective_diagnostic_blocks_promotion():
    assert can_promote_from_window_mode(WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC) is False


def test_causal_mode_may_allow_promotion():
    assert can_promote_from_window_mode(WINDOW_MODE_CAUSAL) is True


# ---------------------------------------------------------------------------
# Tests 24–25: Family 2 signal variant validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("soft_value", ["all", "auto", "default", "existing_implemented_families"])
def test_family2_validator_rejects_soft_values(soft_value):
    with pytest.raises(ValueError, match="soft/implicit"):
        validate_family2_signal_variants([soft_value])


def test_family2_validator_rejects_unknown_variant():
    with pytest.raises(ValueError, match="unknown"):
        validate_family2_signal_variants(["some_invented_signal"])


def test_family2_validator_accepts_known_variants():
    validate_family2_signal_variants(["signed_imbalance", "notional_burst"])


# ---------------------------------------------------------------------------
# Test 26: Safety — new offline files do not import forbidden terms
# ---------------------------------------------------------------------------

_FORBIDDEN_TERMS = [
    "OrderFactory",
    "submit_order",
    "TradingNode",
    "LiveNode",
    "private_key",
    "wallet",
    "signing",
    "CANDIDATE_FOR_LIVE",
    "TRADE_READY",
    "EXECUTION_READY",
    "POLYMARKET_PK",
    "KRAKEN_API_KEY",
]

_OFFLINE_FILES = [
    "offline_historical_models.py",
    "offline_historical_normalize.py",
    "offline_historical_sources.py",
    "offline_corpus_hash.py",
    "offline_replay_manifest.py",
    "run_offline_historical_prepare.py",
]


def test_offline_files_contain_no_forbidden_execution_terms():
    base = Path(__file__).resolve().parent.parent
    violations = []
    for filename in _OFFLINE_FILES:
        fpath = base / filename
        if not fpath.exists():
            violations.append(f"MISSING: {filename}")
            continue
        text = fpath.read_text(encoding="utf-8")
        for term in _FORBIDDEN_TERMS:
            if term in text:
                # Allow only if inside a comment that explicitly says "forbidden"
                # (i.e., inside a forbidden-terms list for documentation).
                # Simple heuristic: look for actual code usage (not inside string literals
                # or comment lists that are documenting what's forbidden).
                for lineno, line in enumerate(text.splitlines(), 1):
                    stripped = line.strip()
                    if term in stripped:
                        # Skip comment lines that enumerate forbidden terms
                        if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                            continue
                        # Skip list literals of forbidden terms (like in this test file)
                        if term in stripped and ("_FORBIDDEN_TERMS" in stripped or '"' + term + '"' in stripped or "'" + term + "'" in stripped):
                            continue
                        violations.append(
                            f"{filename}:{lineno}: contains forbidden term {term!r}: {stripped!r}"
                        )

    # The only allowed matches are this test file's own _FORBIDDEN_TERMS list
    assert not violations, "\n".join(violations)
