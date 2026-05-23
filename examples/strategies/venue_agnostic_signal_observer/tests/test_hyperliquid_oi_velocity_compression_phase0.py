from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_oi_velocity_compression_phase0 import (
    FROZEN_SYMBOLS,
    PHASE0_READY_FOR_V1_PRECOMMITMENT,
    Phase0Config,
    compute_oi_velocity_bps,
    compute_past_percentile_ranks,
    compute_realized_vol_bps,
    compute_symbol_list_hash,
    enforce_funding_quarantine,
    evaluate_phase0b,
    evaluate_phase0c,
    inspect_symbol_coverage,
    load_symbol_frame,
    run_phase0_pipeline,
    select_first_wins_events,
    verify_precommitment_hash,
)

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "examples/strategies/venue_agnostic_signal_observer"
NEW_FILES = [
    PKG / "hyperliquid_oi_velocity_compression_phase0.py",
    PKG / "run_hyperliquid_oi_velocity_compression_phase0.py",
]


def _ts(i: int, start: datetime = datetime(2024, 1, 1, tzinfo=UTC)) -> str:
    return (start + timedelta(hours=i)).isoformat()


def _write_symbol(path: Path, symbol: str, hours: int, *, start_oi: float = 1000.0, price: float = 100.0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(hours):
        rows.append({
            "timestamp": _ts(i),
            "symbol": symbol,
            "open_interest": start_oi + i,
            "mark_price": price + i * 0.01,
            "index_price": price,
            "taker_buy_volume": 10 + (i % 3),
            "taker_sell_volume": 8,
        })
    (path / f"{symbol}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_symbol_list_hash_is_stable_and_deterministic():
    assert compute_symbol_list_hash(["BTC", "ETH"]) == hashlib.sha256(b"BTC\nETH\n").hexdigest()
    assert compute_symbol_list_hash(FROZEN_SYMBOLS) == compute_symbol_list_hash(list(FROZEN_SYMBOLS))


def test_precommitment_hash_mismatch_fails_closed_and_hash_file_written(tmp_path: Path):
    doc = tmp_path / "pre.md"
    doc.write_text("frozen", encoding="utf-8")
    hash_file = tmp_path / "precommitment_hash.txt"
    hash_file.write_text(hashlib.sha256(b"different").hexdigest() + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PRECOMMITMENT_HASH_MISMATCH"):
        verify_precommitment_hash(doc, hash_file)
    good = hashlib.sha256(b"frozen").hexdigest()
    hash_file.write_text(good + "\n", encoding="utf-8")
    assert verify_precommitment_hash(doc, hash_file) == good


def test_frozen_universe_used_exactly_and_missing_symbols_reported(tmp_path: Path):
    _write_symbol(tmp_path, "BTC", 24 * 400)
    cfg = Phase0Config(frozen_symbols=("BTC", "ETH"), min_usable_symbols=30)
    result = run_phase0_pipeline(tmp_path, cfg, verify_hash=False, write_reports=False)
    assert result["summary"]["evaluated_symbols"] == ["BTC", "ETH"]
    assert "ETH" in result["summary"]["missing_symbols"]
    assert result["summary"]["verdict"] == "PHASE0A_INSUFFICIENT_OI_PRICE_COVERAGE"


def test_runtime_does_not_auto_expand_symbols_based_on_available_data(tmp_path: Path):
    _write_symbol(tmp_path, "BTC", 24 * 400)
    _write_symbol(tmp_path, "NOT_FROZEN", 24 * 400)
    cfg = Phase0Config(frozen_symbols=("BTC",), min_usable_symbols=1)
    result = run_phase0_pipeline(tmp_path, cfg, verify_hash=False, write_reports=False)
    assert result["summary"]["evaluated_symbols"] == ["BTC"]
    assert "NOT_FROZEN" not in result["summary"]["evaluated_symbols"]


def test_coverage_gap_rules_and_warmup_denominator():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    base = [start + timedelta(hours=i) for i in range(24 * 400)]
    assert inspect_symbol_coverage(base).usable
    with_big_gap = base[: 24 * 60] + [t + timedelta(hours=48) for t in base[24 * 60 :]]
    bad = inspect_symbol_coverage(with_big_gap)
    assert not bad.usable
    assert bad.drop_reason == "max_gap_ge_48h"
    sparse = [t for i, t in enumerate(base) if not (24 * 40 <= i < 24 * 90 and i % 2 == 0)]
    bad_total = inspect_symbol_coverage(sparse)
    assert not bad_total.usable
    assert bad_total.drop_reason == "total_gap_ge_5pct"
    short = [start + timedelta(hours=i) for i in range(24 * 30 + 24 * 360)]
    assert not inspect_symbol_coverage(short).usable


def test_funding_quarantine_rejects_imports_paths_and_funding_values(tmp_path: Path):
    bad = tmp_path / "bad.py"
    bad.write_text("import hyperliquid_funding_archive_phase0\nP='funding_dispersion_carry_v1_archives/x'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PHASE0A_FUNDING_QUARANTINE_VIOLATION"):
        enforce_funding_quarantine([bad])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "BTC.jsonl").write_text(json.dumps({"timestamp": _ts(0), "open_interest": 1, "mark_price": 1, "funding_rate": 0.1}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PHASE0A_FUNDING_QUARANTINE_VIOLATION"):
        load_symbol_frame(data_dir, "BTC")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"symbols": {"BTC": {"funding_history_available": True}}}), encoding="utf-8")
    enforce_funding_quarantine([], manifest_paths=[manifest])
    manifest.write_text(json.dumps({"symbols": {"BTC": {"funding_history_available": 0.01}}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="PHASE0A_FUNDING_QUARANTINE_VIOLATION"):
        enforce_funding_quarantine([], manifest_paths=[manifest])


def test_oi_velocity_and_realized_vol_frozen_lookbacks():
    oi = [100.0] * 6 + [110.0]
    vel = compute_oi_velocity_bps(oi, lookback_h=6)
    assert vel[-1] == pytest.approx(1000.0)
    prices = [100.0 + i for i in range(25)]
    rv = compute_realized_vol_bps(prices, lookback_h=24)
    assert rv[23] is None
    assert rv[24] is not None


def test_percentile_ranks_are_past_only_with_future_spike_adversary():
    values = [float(i) for i in range(24 * 31)]
    ranks_a = compute_past_percentile_ranks(values, lookback_h=24 * 30)
    values[24 * 31 - 1] = 999999.0
    ranks_b = compute_past_percentile_ranks(values, lookback_h=24 * 30)
    boundary = 24 * 30
    assert ranks_a[boundary] == ranks_b[boundary]


def test_first_30_days_warmup_produces_no_events_and_first_wins_cooldown():
    cfg = Phase0Config(frozen_symbols=("BTC",))
    candidates = [
        {"timestamp": datetime(2024, 1, 31, tzinfo=UTC), "rank": 0.91},
        {"timestamp": datetime(2024, 1, 31, 1, tzinfo=UTC), "rank": 0.99},
        {"timestamp": datetime(2024, 1, 31, 7, tzinfo=UTC), "rank": 0.92},
    ]
    kept = select_first_wins_events(candidates, cooldown_h=cfg.event_cooldown_h)
    assert [e["rank"] for e in kept] == [0.91, 0.92]


def test_phase0b_failure_verdicts():
    events = [{"symbol": "BTC", "timestamp": datetime(2024, 1, 1, tzinfo=UTC)} for _ in range(199)]
    assert evaluate_phase0b(events)["verdict"] == "PHASE0B_INSUFFICIENT_COMPRESSED_OI_BUILD_EVENTS"
    concentrated = [{"symbol": "BTC", "timestamp": datetime(2024, 1, 1, tzinfo=UTC)} for _ in range(70)]
    concentrated += [{"symbol": f"S{i}", "timestamp": datetime(2024, 1, 1, tzinfo=UTC)} for i in range(1, 131)]
    assert evaluate_phase0b(concentrated)["verdict"] == "PHASE0B_UNIVERSE_CONCENTRATION_FAILURE"


def _events(n: int, *, forward: float, lookback: float, second: int = 1, drift: int = 1, abs_forward: float | None = None):
    rows = []
    for i in range(n):
        signed = forward
        rows.append({
            "symbol": f"S{i % 20}",
            "timestamp": datetime(2024, 2, 1, tzinfo=UTC) + timedelta(hours=i),
            "drift_sign": drift,
            "second_proxy_sign": second if not isinstance(second, list) else second[i],
            "lookback_drift_bps": lookback * drift,
            "forward_returns_bps": {"1h": signed * drift, "4h": signed * drift, "12h": signed * drift},
            "abs_forward_bps": abs_forward,
        })
    return rows


def test_phase0c_verdict_ladder_cases():
    assert evaluate_phase0c(_events(99, forward=50, lookback=10))["verdict"] == "PHASE0C_HORIZON_UNDERPOWERED"
    second = [-1 if i < 41 else 1 for i in range(100)]
    assert evaluate_phase0c(_events(100, forward=50, lookback=10, second=second))["verdict"] == "PHASE0C_DIRECTION_PROXY_UNSTABLE"
    assert evaluate_phase0c(_events(100, forward=1, lookback=0.1, abs_forward=100))["verdict"] == "PHASE0C_MECHANISM_MISMATCH_VOL_ONLY"
    assert evaluate_phase0c(_events(100, forward=10, lookback=10))["verdict"] == "PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION"
    assert evaluate_phase0c(_events(100, forward=19, lookback=1))["verdict"] == "PHASE0C_MOVE_MAGNITUDE_TOO_SMALL"
    assert evaluate_phase0c(_events(100, forward=30, lookback=1))["verdict"] == PHASE0_READY_FOR_V1_PRECOMMITMENT


def test_pure_noise_and_momentum_synthetic_failures():
    noise = [{"symbol": f"S{i%20}", "timestamp": datetime(2024, 1, 1, tzinfo=UTC), "drift_sign": 1 if i % 2 else -1, "second_proxy_sign": 1, "lookback_drift_bps": 0.1, "forward_returns_bps": {"1h": 0.0, "4h": 0.0, "12h": 0.0}, "abs_forward_bps": 10.0} for i in range(100)]
    assert evaluate_phase0c(noise)["verdict"] != PHASE0_READY_FOR_V1_PRECOMMITMENT
    assert evaluate_phase0c(_events(100, forward=10, lookback=10))["verdict"] == "PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION"


def test_new_python_files_parse_and_forbid_runtime_imports_and_verdict_language():
    banned_import_fragments = ["execution", "live", "order", "bot", "private", "credential", "auth", "clob_client", "clobclient", "signing", "wallet", "key"]
    banned_verdict_literals = ["CANDIDATE", "TRADE_READY", "EXECUTION_READY", "PROMOTED", "LIVE_AUTHORIZED", "SHADOW_AUTHORIZED"]
    for path in NEW_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                assert not any(fragment in name.lower() for fragment in banned_import_fragments), (path, name)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                assert not any(v == value for v in banned_verdict_literals), (path, value)
                if "verdict" in path.read_text(encoding="utf-8").lower():
                    assert not any(x in value.lower() for x in ["approv", "authoriz", "promot"]), (path, value)
