"""Observer-only Phase 0 scaffold for Hyperliquid OI velocity compression breakout.

Archive/backfill-only feasibility code. It emits diagnostic verdicts only and has
no capture, exchange-client, account, or order-capable path.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

FROZEN_SYMBOLS: tuple[str, ...] = (
    "BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX",
    "SUI", "TRX", "LTC", "BCH", "TON", "DOT", "AAVE", "UNI", "APT", "ARB",
    "OP", "SEI", "INJ", "NEAR", "TIA", "WIF", "PEPE", "FET", "ENA", "ONDO",
    "MKR", "JUP", "PENDLE", "WLD", "ATOM",
)

PHASE0_READY_FOR_V1_PRECOMMITMENT = "PHASE0_READY_FOR_V1_PRECOMMITMENT"
TERMINAL_VERDICTS = (
    "PRECOMMITMENT_HASH_MISMATCH",
    "PHASE0A_INSUFFICIENT_OI_PRICE_COVERAGE",
    "PHASE0A_FUNDING_QUARANTINE_VIOLATION",
    "PHASE0B_INSUFFICIENT_COMPRESSED_OI_BUILD_EVENTS",
    "PHASE0B_UNIVERSE_CONCENTRATION_FAILURE",
    "PHASE0C_HORIZON_UNDERPOWERED",
    "PHASE0C_DIRECTION_PROXY_UNSTABLE",
    "PHASE0C_MECHANISM_MISMATCH_VOL_ONLY",
    "PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION",
    "PHASE0C_MOVE_MAGNITUDE_TOO_SMALL",
    PHASE0_READY_FOR_V1_PRECOMMITMENT,
)

PRICE_FIELD = "mark_price"
SECOND_DIRECTION_PROXY = "perp_basis_mark_minus_index"
WARMUP_DAYS = 30
HORIZONS_H = (1, 4, 12)


@dataclass(frozen=True)
class Phase0Config:
    frozen_symbols: tuple[str, ...] = FROZEN_SYMBOLS
    oi_velocity_lookback_h: int = 6
    realized_vol_lookback_h: int = 24
    percentile_lookback_days: int = 30
    warmup_days: int = 30
    event_cooldown_h: int = 6
    min_usable_symbols: int = 30
    min_events: int = 200
    min_symbols_with_events: int = 10
    min_events_per_symbol: int = 5
    max_single_symbol_event_fraction: float = 0.30


@dataclass(frozen=True)
class CoverageResult:
    symbol: str = ""
    usable: bool = False
    drop_reason: str = ""
    n_rows: int = 0
    start_ts: str | None = None
    end_ts: str | None = None
    evaluated_start_ts: str | None = None
    evaluated_end_ts: str | None = None
    max_gap_h: float | None = None
    total_missing_gap_h: float | None = None
    missing_gap_fraction: float | None = None


def parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e18:
            return datetime.fromtimestamp(number / 1e9, tz=UTC)
        if number > 1e15:
            return datetime.fromtimestamp(number / 1e6, tz=UTC)
        if number > 1e12:
            return datetime.fromtimestamp(number / 1e3, tz=UTC)
        return datetime.fromtimestamp(number, tz=UTC)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(UTC)


def compute_symbol_list_hash(symbols: Sequence[str]) -> str:
    canonical = "".join(f"{str(s).upper()}\n" for s in symbols)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_precommitment_hash(precommitment_path: Path, hash_path: Path) -> str:
    expected = hash_path.read_text(encoding="utf-8").strip()
    actual = sha256_file(precommitment_path)
    if actual != expected:
        raise RuntimeError("PRECOMMITMENT_HASH_MISMATCH")
    return actual


def _module_names_from_ast(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if "/" in value or "\\" in value:
                names.append(value)
    return names


def enforce_funding_quarantine(paths: Iterable[Path], manifest_paths: Iterable[Path] = ()) -> dict[str, Any]:
    banned_path_parts = ("hyperliquid_funding_archive_phase0", "funding_dispersion_carry_v1_archives")
    for path in paths:
        for value in _module_names_from_ast(path):
            if any(part in value for part in banned_path_parts):
                raise RuntimeError("PHASE0A_FUNDING_QUARANTINE_VIOLATION")
    for manifest in manifest_paths:
        if not manifest.exists():
            continue
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for k, v in item.items():
                    if k == "funding_history_available" and type(v) is not bool:
                        raise RuntimeError("PHASE0A_FUNDING_QUARANTINE_VIOLATION")
                    if "funding" in k and k != "funding_history_available":
                        raise RuntimeError("PHASE0A_FUNDING_QUARANTINE_VIOLATION")
                    stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)
    return {"status": "passed", "funding_history_available_values": "boolean_only"}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return rows
    if text.startswith("["):
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [dict(r) for r in parsed]
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _symbol_path(data_dir: Path, symbol: str) -> Path | None:
    candidates = [data_dir / f"{symbol}.jsonl", data_dir / f"{symbol}.csv", data_dir / f"{symbol}.json"]
    candidates.extend(sorted(data_dir.glob(f"*{symbol}*.jsonl")))
    candidates.extend(sorted(data_dir.glob(f"*{symbol}*.csv")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _float_field(row: dict[str, Any], names: Sequence[str], *, required: bool = True) -> float | None:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return float(row[name])
    if required:
        raise ValueError(f"missing field from {names}")
    return None


def load_symbol_frame(data_dir: Path, symbol: str) -> list[dict[str, Any]]:
    path = _symbol_path(data_dir, symbol)
    if path is None:
        return []
    rows = _read_rows(path)
    out: list[dict[str, Any]] = []
    for row in rows:
        forbidden = [k for k in row if "funding" in k.lower()]
        if forbidden:
            raise RuntimeError("PHASE0A_FUNDING_QUARANTINE_VIOLATION")
        ts = parse_ts(row.get("timestamp", row.get("ts_event", row.get("ts", row.get("time", row.get("timestamp_ms"))))))
        mark = _float_field(row, ("mark_price", "mark", "price", "last_price", "close"))
        index = _float_field(row, ("index_price", "index"), required=False)
        out.append({
            "timestamp": ts,
            "symbol": symbol,
            "open_interest": _float_field(row, ("open_interest", "oi")),
            "mark_price": mark,
            "index_price": index if index is not None else mark,
            "taker_buy_volume": _float_field(row, ("taker_buy_volume", "buy_volume"), required=False),
            "taker_sell_volume": _float_field(row, ("taker_sell_volume", "sell_volume"), required=False),
        })
    out.sort(key=lambda r: r["timestamp"])
    return out


def inspect_symbol_coverage(timestamps: Sequence[datetime], symbol: str = "") -> CoverageResult:
    if not timestamps:
        return CoverageResult(symbol=symbol, drop_reason="missing_symbol")
    ordered = sorted(t.astimezone(UTC) for t in timestamps)
    warmup_cutoff = ordered[0] + timedelta(days=WARMUP_DAYS)
    eval_ts = [t for t in ordered if t >= warmup_cutoff]
    if not eval_ts:
        return CoverageResult(symbol=symbol, n_rows=len(ordered), start_ts=ordered[0].isoformat(), end_ts=ordered[-1].isoformat(), drop_reason="warmup_exhausts_window")
    if (eval_ts[-1] - eval_ts[0]) < timedelta(days=365):
        return CoverageResult(symbol=symbol, n_rows=len(ordered), start_ts=ordered[0].isoformat(), end_ts=ordered[-1].isoformat(), evaluated_start_ts=eval_ts[0].isoformat(), evaluated_end_ts=eval_ts[-1].isoformat(), drop_reason="lt_12m_after_warmup")
    gaps_h = [(eval_ts[i] - eval_ts[i - 1]).total_seconds() / 3600.0 for i in range(1, len(eval_ts))]
    max_gap = max(gaps_h) if gaps_h else 0.0
    missing = sum(max(0.0, gap - 1.0) for gap in gaps_h)
    window_h = (eval_ts[-1] - eval_ts[0]).total_seconds() / 3600.0
    frac = missing / window_h if window_h else 1.0
    if max_gap >= 48.0:
        reason = "max_gap_ge_48h"
        usable = False
    elif frac >= 0.05:
        reason = "total_gap_ge_5pct"
        usable = False
    else:
        reason = "usable"
        usable = True
    return CoverageResult(symbol=symbol, usable=usable, drop_reason=reason, n_rows=len(ordered), start_ts=ordered[0].isoformat(), end_ts=ordered[-1].isoformat(), evaluated_start_ts=eval_ts[0].isoformat(), evaluated_end_ts=eval_ts[-1].isoformat(), max_gap_h=max_gap, total_missing_gap_h=missing, missing_gap_fraction=frac)


def compute_oi_velocity_bps(oi: Sequence[float], lookback_h: int = 6) -> list[float | None]:
    out: list[float | None] = []
    for i, value in enumerate(oi):
        if i < lookback_h or oi[i - lookback_h] == 0:
            out.append(None)
        else:
            out.append(10000.0 * (float(value) - float(oi[i - lookback_h])) / float(oi[i - lookback_h]))
    return out


def compute_realized_vol_bps(prices: Sequence[float], lookback_h: int = 24) -> list[float | None]:
    returns: list[float] = []
    for i in range(1, len(prices)):
        prev = float(prices[i - 1])
        returns.append((float(prices[i]) - prev) / prev if prev else 0.0)
    out: list[float | None] = [None] * len(prices)
    for i in range(lookback_h, len(prices)):
        window = returns[i - lookback_h : i]
        if len(window) == lookback_h:
            out[i] = statistics.pstdev(window) * 10000.0
    return out


def compute_past_percentile_ranks(values: Sequence[float | None], lookback_h: int) -> list[float | None]:
    out: list[float | None] = []
    for i, value in enumerate(values):
        if value is None or i < lookback_h:
            out.append(None)
            continue
        past = [float(v) for v in values[i - lookback_h : i] if v is not None and math.isfinite(float(v))]
        if not past:
            out.append(None)
            continue
        le = sum(1 for v in past if v <= float(value))
        out.append(le / len(past))
    return out


def select_first_wins_events(candidates: Sequence[dict[str, Any]], cooldown_h: int = 6) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    last_ts: datetime | None = None
    for event in sorted(candidates, key=lambda e: e["timestamp"]):
        ts = event["timestamp"]
        if last_ts is None or ts - last_ts >= timedelta(hours=cooldown_h):
            kept.append(dict(event))
            last_ts = ts
    return kept


def _build_symbol_events(rows: list[dict[str, Any]], cfg: Phase0Config) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    oi = [float(r["open_interest"]) for r in rows]
    prices = [float(r[PRICE_FIELD]) for r in rows]
    vel = compute_oi_velocity_bps(oi, cfg.oi_velocity_lookback_h)
    rv = compute_realized_vol_bps(prices, cfg.realized_vol_lookback_h)
    rank_lookback = cfg.percentile_lookback_days * 24
    vel_rank = compute_past_percentile_ranks(vel, rank_lookback)
    rv_rank = compute_past_percentile_ranks(rv, rank_lookback)
    candidates: list[dict[str, Any]] = []
    tails: list[dict[str, Any]] = []
    warmup_cutoff = rows[0]["timestamp"] + timedelta(days=cfg.warmup_days)
    for i, row in enumerate(rows):
        tail = {"symbol": row["symbol"], "timestamp": row["timestamp"].isoformat(), "oi_velocity_bps": vel[i], "realized_vol_bps": rv[i], "oi_velocity_percentile": vel_rank[i], "realized_vol_percentile": rv_rank[i]}
        if i >= len(rows) - 3:
            tails.append(tail)
        if row["timestamp"] < warmup_cutoff:
            continue
        if vel_rank[i] is None or rv_rank[i] is None:
            continue
        if vel_rank[i] >= 0.90 and rv_rank[i] <= 0.30:
            prior_i = i - cfg.oi_velocity_lookback_h
            if prior_i < 0:
                continue
            prior_price = prices[prior_i]
            drift = 10000.0 * (prices[i] - prior_price) / prior_price if prior_price else 0.0
            drift_sign = 1 if drift > 0 else -1 if drift < 0 else 0
            basis = float(row["mark_price"]) - float(row["index_price"])
            second_sign = 1 if basis > 0 else -1 if basis < 0 else 0
            fwd: dict[str, float | None] = {}
            for h in HORIZONS_H:
                j = i + h
                fwd[f"{h}h"] = 10000.0 * (prices[j] - prices[i]) / prices[i] if j < len(prices) and prices[i] else None
            candidates.append({**tail, "timestamp": row["timestamp"], "price": prices[i], "drift_sign": drift_sign, "second_proxy_sign": second_sign, "lookback_drift_bps": drift, "second_proxy_raw_value": basis, "forward_returns_bps": fwd})
    return select_first_wins_events(candidates, cfg.event_cooldown_h), tails


def evaluate_phase0b(events: Sequence[dict[str, Any]], cfg: Phase0Config = Phase0Config()) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event["symbol"])] = counts.get(str(event["symbol"]), 0) + 1
    total = len(events)
    participating = sum(1 for c in counts.values() if c >= cfg.min_events_per_symbol)
    max_fraction = max(counts.values()) / total if total else 0.0
    if total >= cfg.min_events and max_fraction > cfg.max_single_symbol_event_fraction:
        verdict = "PHASE0B_UNIVERSE_CONCENTRATION_FAILURE"
    elif total < cfg.min_events or participating < cfg.min_symbols_with_events:
        verdict = "PHASE0B_INSUFFICIENT_COMPRESSED_OI_BUILD_EVENTS"
    else:
        verdict = "PHASE0B_PASSED"
    return {"verdict": verdict, "total_events": total, "symbols_with_5_events": participating, "max_single_symbol_fraction": max_fraction, "events_by_symbol": counts}


def _median(values: Sequence[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(vals) if vals else None


def evaluate_phase0c(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    horizon_rows: list[dict[str, Any]] = []
    all_under = True
    any_direction_gate = False
    any_move_gate = False
    momentum_ok = False
    vol_only = False
    comparable = [e for e in events if e.get("drift_sign", 0) != 0 and e.get("second_proxy_sign", 0) != 0]
    disagreements = sum(1 for e in comparable if e["drift_sign"] != e["second_proxy_sign"])
    disagreement_rate = disagreements / len(comparable) if comparable else 0.0
    n_zero = sum(1 for e in events if e.get("drift_sign", 0) == 0)
    for horizon in ("1h", "4h", "12h"):
        vals: list[float] = []
        abs_vals: list[float] = []
        adjusted: list[float] = []
        lookback_adj: list[float] = []
        second_hits = 0
        second_n = 0
        for e in events:
            drift_sign = int(e.get("drift_sign", 0))
            if drift_sign == 0:
                continue
            fwd = e.get("forward_returns_bps", {}).get(horizon)
            if fwd is None:
                continue
            fwd_f = float(fwd)
            vals.append(fwd_f * drift_sign)
            abs_vals.append(float(e.get("abs_forward_bps") or abs(fwd_f)))
            adjusted.append(fwd_f * drift_sign)
            lookback_adj.append(abs(float(e.get("lookback_drift_bps", 0.0))))
            if e.get("second_proxy_sign", 0):
                second_n += 1
                if int(e["second_proxy_sign"]) == drift_sign:
                    second_hits += 1
        n = len(vals)
        powered = n >= 100
        all_under = all_under and not powered
        hit_rate = sum(1 for v in vals if v > 0) / n if n else None
        med_signed = _median(vals)
        med_abs = _median(abs_vals)
        ratio = (abs(med_abs) / abs(med_signed)) if med_abs is not None and med_signed not in (None, 0) else None
        med_fwd = _median(adjusted)
        med_look = _median(lookback_adj)
        fwd_to_look = (med_fwd / med_look) if med_fwd is not None and med_look not in (None, 0) else None
        if powered and hit_rate is not None and med_signed is not None:
            if hit_rate >= 0.60 and med_signed >= 20.0:
                any_direction_gate = True
            if med_signed >= 20.0:
                any_move_gate = True
            if ratio is not None and med_abs is not None and abs(med_abs) > 3 * abs(med_signed):
                vol_only = True
            if fwd_to_look is not None and fwd_to_look > 1.5:
                momentum_ok = True
        horizon_rows.append({"horizon": horizon, "n_valid_events": n, "n_drift_zero_excluded": n_zero, "hit_rate": hit_rate, "median_signed_bps": med_signed, "median_abs_bps": med_abs, "abs_to_signed_median_ratio": ratio, "second_direction_proxy_hit_rate": (second_hits / second_n if second_n else None), "median_direction_adjusted_lookback_drift_bps": med_look, "median_direction_adjusted_forward_bps": med_fwd, "forward_to_lookback_drift_ratio": fwd_to_look, "horizon_status": "powered" if powered else "PHASE0C_HORIZON_UNDERPOWERED", "verdict_contribution": "diagnostic"})
    if all_under:
        verdict = "PHASE0C_HORIZON_UNDERPOWERED"
    elif disagreement_rate > 0.40:
        verdict = "PHASE0C_DIRECTION_PROXY_UNSTABLE"
    elif vol_only:
        verdict = "PHASE0C_MECHANISM_MISMATCH_VOL_ONLY"
    elif not momentum_ok:
        verdict = "PHASE0C_DIRECTIONAL_BUT_DRIFT_CONTINUATION"
    elif not any_move_gate or not any_direction_gate:
        verdict = "PHASE0C_MOVE_MAGNITUDE_TOO_SMALL"
    else:
        verdict = PHASE0_READY_FOR_V1_PRECOMMITMENT
    return {"verdict": verdict, "horizons": horizon_rows, "direction_proxy_disagreement_rate": disagreement_rate, "n_comparable_direction_proxy_events": len(comparable), "n_drift_zero_excluded": n_zero}


def _git_metadata() -> dict[str, Any]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:
        sha = ""
        dirty = False
    return {"git_sha": sha, "dirty": dirty}


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def run_phase0_pipeline(data_dir: Path, cfg: Phase0Config = Phase0Config(), *, out_root: Path | None = None, verify_hash: bool = True, precommitment_path: Path | None = None, hash_path: Path | None = None, write_reports: bool = True, args: dict[str, Any] | None = None) -> dict[str, Any]:
    pre_hash = None
    if verify_hash:
        if precommitment_path is None or hash_path is None:
            raise ValueError("precommitment_path and hash_path are required when verify_hash is true")
        pre_hash = verify_precommitment_hash(precommitment_path, hash_path)
    source_paths = [Path(__file__)]
    enforce_funding_quarantine(source_paths, manifest_paths=data_dir.glob("*manifest*.json"))
    symbol_hash = compute_symbol_list_hash(cfg.frozen_symbols)
    coverage: list[CoverageResult] = []
    all_events: list[dict[str, Any]] = []
    feature_tail: list[dict[str, Any]] = []
    missing: list[str] = []
    usable_symbols: list[str] = []
    for symbol in cfg.frozen_symbols:
        rows = load_symbol_frame(data_dir, symbol)
        if not rows:
            missing.append(symbol)
            coverage.append(CoverageResult(symbol=symbol, drop_reason="missing_symbol"))
            continue
        cov = inspect_symbol_coverage([r["timestamp"] for r in rows], symbol=symbol)
        coverage.append(cov)
        if not cov.usable:
            continue
        usable_symbols.append(symbol)
        events, tails = _build_symbol_events(rows, cfg)
        all_events.extend(events)
        feature_tail.extend(tails)
    summary: dict[str, Any] = {"safety_mode": "archive_backfill_only_observer", "evaluated_symbols": list(cfg.frozen_symbols), "missing_symbols": missing, "usable_symbols": usable_symbols, "n_usable_symbols": len(usable_symbols), "symbol_list_hash": symbol_hash, "precommitment_hash": pre_hash, "frozen_parameters": asdict(cfg)}
    phase0b = None
    phase0c = None
    if len(usable_symbols) < cfg.min_usable_symbols:
        verdict = "PHASE0A_INSUFFICIENT_OI_PRICE_COVERAGE"
    else:
        phase0b = evaluate_phase0b(all_events, cfg)
        verdict = phase0b["verdict"]
        if verdict == "PHASE0B_PASSED":
            phase0c = evaluate_phase0c(all_events)
            verdict = phase0c["verdict"]
    summary.update({"verdict": verdict, "n_events": len(all_events), "phase0b": phase0b, "phase0c": phase0c})
    report_dir = None
    if write_reports:
        run_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = (out_root or Path("reports/hyperliquid_oi_velocity_compression_phase0")) / run_id
        report_dir.mkdir(parents=True, exist_ok=False)
        (report_dir / "summary.json").write_text(json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (report_dir / "summary.md").write_text(f"# Hyperliquid OI velocity compression Phase 0\n\nverdict: `{verdict}`\n\nevents: {len(all_events)}\nusable_symbols: {len(usable_symbols)}\n", encoding="utf-8")
        (report_dir / "precommitment_hash.txt").write_text((pre_hash or "") + "\n", encoding="utf-8")
        _write_csv(report_dir / "coverage_by_symbol.csv", [asdict(c) for c in coverage], list(asdict(CoverageResult()).keys()))
        _write_csv(report_dir / "feature_tail_by_symbol.csv", feature_tail, ["symbol", "timestamp", "oi_velocity_bps", "realized_vol_bps", "oi_velocity_percentile", "realized_vol_percentile"])
        if all_events:
            preview = [{**e, "timestamp": e["timestamp"].isoformat(), "forward_returns_bps": json.dumps(e["forward_returns_bps"])} for e in all_events[:100]]
            _write_csv(report_dir / "events_preview.csv", preview, list(preview[0].keys()))
        horizon_rows = phase0c["horizons"] if phase0c else []
        _write_csv(report_dir / "mechanism_sanity_by_horizon.csv", horizon_rows, ["horizon", "n_valid_events", "n_drift_zero_excluded", "hit_rate", "median_signed_bps", "median_abs_bps", "abs_to_signed_median_ratio", "second_direction_proxy_hit_rate", "median_direction_adjusted_lookback_drift_bps", "median_direction_adjusted_forward_bps", "forward_to_lookback_drift_ratio", "horizon_status", "verdict_contribution"])
        agreement_rows = []
        for e in all_events:
            if e.get("drift_sign", 0) and e.get("second_proxy_sign", 0):
                fwd = e.get("forward_returns_bps", {})
                agreement_rows.append({"event_timestamp": e["timestamp"].isoformat(), "symbol": e["symbol"], "drift_sign": e["drift_sign"], "second_proxy_sign": e["second_proxy_sign"], "agreement_bool": e["drift_sign"] == e["second_proxy_sign"], "lookback_drift_bps": e["lookback_drift_bps"], "second_proxy_raw_value": e["second_proxy_raw_value"], "horizon_1h_available": fwd.get("1h") is not None, "horizon_4h_available": fwd.get("4h") is not None, "horizon_12h_available": fwd.get("12h") is not None})
        _write_csv(report_dir / "direction_proxy_agreement.csv", agreement_rows, ["event_timestamp", "symbol", "drift_sign", "second_proxy_sign", "agreement_bool", "lookback_drift_bps", "second_proxy_raw_value", "horizon_1h_available", "horizon_4h_available", "horizon_12h_available"])
        manifest = {**_git_metadata(), "safety_mode": "archive_backfill_only_observer", "args": args or {}, "precommitment_hash": pre_hash, "symbol_list_hash": symbol_hash, "frozen_parameters": asdict(cfg), "report_dir": str(report_dir)}
        (report_dir / "manifest.json").write_text(json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"summary": summary, "events": all_events, "coverage": coverage, "report_dir": str(report_dir) if report_dir else None}
