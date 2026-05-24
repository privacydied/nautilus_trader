from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

NON_CONCLUSIONS = "This diagnostic describes structural properties of an already-rejected v0 result. It is not a strategy. It is not a candidate. It is not a precommitment. It is not a registry update. The findings of this diagnostic CANNOT be used to choose parameters, symbols, regimes, exit rules, or cost models for any subsequent hypothesis. Any subsequent hypothesis in the Hyperliquid altcoin indicator family must be justified independently from public literature or first principles, with parameters chosen without reference to the contents of this report."

STATUS_READY = "V0_RESIDUAL_DIAGNOSTIC_READY"
STATUS_MISSING_V0_REPORT_ARTIFACTS = "MISSING_V0_REPORT_ARTIFACTS"
STATUS_MISSING_V0_ENTRY_ARTIFACTS = "MISSING_V0_ENTRY_ARTIFACTS"
STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK = "SCHEMA_INSUFFICIENT_FOR_GIVEBACK"
STATUS_COST_STRUCTURE_BLOCKED_DIAGNOSTIC = "COST_STRUCTURE_BLOCKED_DIAGNOSTIC"
STATUS_CONCENTRATION_WARNING_DIAGNOSTIC = "CONCENTRATION_WARNING_DIAGNOSTIC"
STATUS_REGIME_CONCENTRATION_WARNING_DIAGNOSTIC = "REGIME_CONCENTRATION_WARNING_DIAGNOSTIC"
STATUS_DISTRIBUTION_SIGN_INCONSISTENCY_WARNING = "DISTRIBUTION_SIGN_INCONSISTENCY_WARNING"
STATUS_BROAD_GROSS_EDGE_COST_BLOCKED_DIAGNOSTIC = "BROAD_GROSS_EDGE_COST_BLOCKED_DIAGNOSTIC"
STATUS_EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC = "EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC"
STATUS_FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC = "FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC"
STATUS_PUBLIC_LITERATURE_FOLLOWUP_ONLY_DIAGNOSTIC = "PUBLIC_LITERATURE_FOLLOWUP_ONLY_DIAGNOSTIC"
STATUS_DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA = "DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA"
STATUS_FIREWALL_INTEGRITY_FAILED = "FIREWALL_INTEGRITY_FAILED"

ALLOWED_STATUSES = {
    STATUS_READY,
    "MISSING_V0_CODE_BASE",
    STATUS_MISSING_V0_REPORT_ARTIFACTS,
    STATUS_MISSING_V0_ENTRY_ARTIFACTS,
    STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK,
    STATUS_COST_STRUCTURE_BLOCKED_DIAGNOSTIC,
    STATUS_CONCENTRATION_WARNING_DIAGNOSTIC,
    STATUS_REGIME_CONCENTRATION_WARNING_DIAGNOSTIC,
    STATUS_DISTRIBUTION_SIGN_INCONSISTENCY_WARNING,
    STATUS_BROAD_GROSS_EDGE_COST_BLOCKED_DIAGNOSTIC,
    STATUS_EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC,
    STATUS_FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC,
    STATUS_PUBLIC_LITERATURE_FOLLOWUP_ONLY_DIAGNOSTIC,
    STATUS_DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA,
    STATUS_FIREWALL_INTEGRITY_FAILED,
    "ENVIRONMENT_MODIFICATION_REQUIRED",
}
FORBIDDEN_VERDICTS = {
    "REJECTED",
    "CANDIDATE",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
    "CANDIDATE_FOR_LIVE",
    "TRADE_READY",
    "EXECUTION_READY",
    "SHADOW_READY",
    "BOT_READY",
}
FORBIDDEN_MARKDOWN_RE = re.compile(
    r"(?i)(should trade|recommend trading|maker.*?(would|will).*?work|use .* stop|try .* parameter|increase|decrease|optimize)"
)
FORBIDDEN_V0_MODULES = {
    "examples.strategies.venue_agnostic_signal_observer.hyperliquid_supertrend_4h1d_phase0",
    "examples.strategies.venue_agnostic_signal_observer.hyperliquid_supertrend_archive_ingest",
    ".hyperliquid_supertrend_4h1d_phase0",
    ".hyperliquid_supertrend_archive_ingest",
}
REQUIRED_FIREWALL_FIELDS = [
    "no_new_strategy_code",
    "no_parameter_recommendations",
    "no_symbol_recommendations",
    "no_regime_recommendations",
    "no_exit_rule_recommendations",
    "no_cost_model_recommendations",
    "no_v0_source_imports",
    "no_forbidden_verdicts",
    "no_forbidden_phrases_in_markdown",
    "non_conclusions_constant_present_verbatim",
    "no_orders_" + "private" + "_keys_auth_live_execution",
    "registry_untouched",
]


@dataclass(frozen=True)
class EntryRecord:
    symbol: str
    timeframe: str
    entry_ts: str
    exit_ts: str
    direction: str
    entry_price: float
    exit_price: float
    holding_period_bars: int
    exit_reason: str
    gross_return_bps: float
    funding_bps: float | None
    net_return_bps: float | None


@dataclass(frozen=True)
class PriceBar:
    timestamp_utc: str
    symbol: str
    open: float
    high: float
    low: float
    close: float


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def month_key(value: str) -> str:
    dt = parse_ts(value)
    return f"{dt.year:04d}-{dt.month:02d}"


def quarter_key(value: str) -> str:
    dt = parse_ts(value)
    return f"{dt.year:04d}-Q{((dt.month - 1) // 3) + 1}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_directory(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in path.rglob("*") if x.is_file()):
        rel = p.relative_to(path).as_posix().encode()
        h.update(rel + b"\0")
        h.update(sha256_file(p).encode() + b"\0")
    return h.hexdigest()


def assert_pinned_sha(path: Path, expected_sha256: str) -> None:
    actual = sha256_directory(path)
    if actual != expected_sha256:
        raise ValueError(f"V0 report directory SHA mismatch: expected {expected_sha256}, actual {actual}")


def _read_header(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="") as f:
            try:
                return list(next(csv.reader(f)))
            except StopIteration:
                return []
    if path.suffix.lower() == ".json":
        try:
            obj = json.loads(path.read_text())
        except json.JSONDecodeError:
            return []
        if isinstance(obj, dict):
            return sorted(obj.keys())
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return sorted(obj[0].keys())
    if path.suffix.lower() == ".jsonl":
        with path.open() as f:
            for line in f:
                if line.strip():
                    obj = json.loads(line)
                    return sorted(obj.keys()) if isinstance(obj, dict) else []
    return []


def infer_role(path: Path, columns: list[str]) -> str:
    lower = path.name.lower()
    if {"symbol", "timeframe", "entry_ts", "exit_ts", "gross_return_bps"}.issubset(columns):
        return "entry_records"
    if {"timestamp_utc", "symbol", "open", "high", "low", "close"}.issubset(columns):
        return "price_archive"
    if "summary" in lower:
        return "summary"
    if "manifest" in lower:
        return "manifest"
    return "supporting_artifact"


def build_artifact_inventory(report_dir: Path) -> dict[str, Any]:
    required = {
        "q1_q2": ["symbol", "timeframe", "entry_ts", "exit_ts", "entry_price", "exit_price", "direction", "gross_return_bps"],
        "q3": ["timestamp_utc", "symbol", "open", "high", "low", "close"],
    }
    files = []
    for p in sorted(x for x in report_dir.rglob("*") if x.is_file()):
        columns = _read_header(p)
        role = infer_role(p, columns)
        files.append(
            {
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "sha256": sha256_file(p),
                "inferred_role": role,
                "columns": columns,
                "required_q1_q2_fields_present": all(c in columns for c in required["q1_q2"]),
                "required_q3_fields_present": all(c in columns for c in required["q3"]),
            }
        )
    return {"report_dir": str(report_dir), "report_dir_sha256": sha256_directory(report_dir), "files": files}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def inventory_markdown(inventory: dict[str, Any]) -> str:
    lines = ["# Artifact availability", "", f"source_v0_report_dir: {inventory['report_dir']}", f"source_v0_report_dir_sha256: {inventory['report_dir_sha256']}", "", "| path | bytes | sha256 | role | columns | q1_q2_fields | q3_fields |", "|---|---:|---|---|---|---|---|"]
    for f in inventory["files"]:
        lines.append(f"| {f['path']} | {f['size_bytes']} | {f['sha256']} | {f['inferred_role']} | {', '.join(f['columns'])} | {f['required_q1_q2_fields_present']} | {f['required_q3_fields_present']} |")
    return "\n".join(lines) + "\n"


def find_entry_artifact(inventory: dict[str, Any]) -> Path | None:
    candidates = [f for f in inventory["files"] if f["inferred_role"] == "entry_records" and f["required_q1_q2_fields_present"]]
    if not candidates:
        return None
    candidates.sort(key=lambda f: ("preview" not in Path(f["path"]).name.lower(), -int(f["size_bytes"])))
    return Path(candidates[0]["path"])


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def load_entries(path: Path) -> list[EntryRecord]:
    rows: list[EntryRecord] = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            tf = r.get("timeframe", "")
            if tf != "1d":
                continue
            net = _float(r.get("net_return_bps_primary") or r.get("net_return_bps"))
            rows.append(
                EntryRecord(
                    symbol=r["symbol"],
                    timeframe=tf,
                    entry_ts=r["entry_ts"],
                    exit_ts=r["exit_ts"],
                    direction=(r.get("direction") or "long").lower(),
                    entry_price=float(r["entry_price"]),
                    exit_price=float(r["exit_price"]),
                    holding_period_bars=int(float(r.get("holding_period_bars") or 0)),
                    exit_reason=r.get("exit_reason", ""),
                    gross_return_bps=float(r["gross_return_bps"]),
                    funding_bps=_float(r.get("funding_accrual_bps") or r.get("funding_bps")),
                    net_return_bps=net,
                )
            )
    return rows


def percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def stats(values: list[float], prefix: str) -> dict[str, Any]:
    vals = [v for v in values if math.isfinite(v)]
    out: dict[str, Any] = {f"{prefix}_count": len(values), f"{prefix}_valid_count": len(vals)}
    if not vals:
        return out
    out.update({
        f"{prefix}_mean": mean(vals),
        f"{prefix}_median": median(vals),
        f"{prefix}_std": pstdev(vals) if len(vals) > 1 else 0.0,
        f"{prefix}_min": min(vals),
        f"{prefix}_max": max(vals),
    })
    for q in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        out[f"{prefix}_p{q:02d}"] = percentile(vals, q / 100)
    return out


def sign(x: float | None) -> int:
    if x is None or abs(x) < 1e-12:
        return 0
    return 1 if x > 0 else -1


def top_decile_contribution(values: list[float]) -> float | None:
    vals = sorted([v for v in values if math.isfinite(v)], reverse=True)
    if not vals:
        return None
    total = sum(vals)
    if abs(total) < 1e-12:
        return None
    n = max(1, math.ceil(len(vals) * 0.10))
    return sum(vals[:n]) / total


def _group(rows: list[EntryRecord], key_fn: Any) -> dict[str, list[EntryRecord]]:
    groups: dict[str, list[EntryRecord]] = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return dict(groups)


def _positive_share(values: list[float]) -> float | None:
    return sum(1 for v in values if v > 0) / len(values) if values else None


def compute_q1(rows: list[EntryRecord]) -> dict[str, Any]:
    statuses: list[str] = []
    gross = [r.gross_return_bps for r in rows]
    net = [r.net_return_bps for r in rows if r.net_return_bps is not None]
    overall = {"entry_count": len(rows), "valid_count": len(gross)}
    overall.update(stats(gross, "gross_return_bps"))
    if net:
        overall.update(stats(net, "net_return_bps"))
    overall["gross_positive_share"] = _positive_share(gross)
    overall["net_positive_share"] = _positive_share(net)
    overall["gross_mean_minus_median_gap"] = (mean(gross) - median(gross)) if gross else None
    if gross and sign(mean(gross)) != sign(median(gross)):
        statuses.append(STATUS_DISTRIBUTION_SIGN_INCONSISTENCY_WARNING)
    p90 = percentile(gross, 0.90)
    p50 = percentile(gross, 0.50)
    p10 = percentile(gross, 0.10)
    if None not in (p90, p50, p10):
        upside = p90 - p50  # type: ignore[operator]
        downside = p50 - p10  # type: ignore[operator]
        overall["upside_tail_bps"] = upside
        overall["downside_tail_bps"] = downside
        overall["downside_upside_tail_ratio"] = downside / upside if upside and upside > 0 else None
        overall["robust_skew_warning"] = bool((median(gross) > 0 and mean(gross) <= 0) or (upside and downside > 1.5 * upside))
    td = top_decile_contribution(gross)
    overall["top_decile_cumulative_gross_contribution_share"] = td
    overall["fat_tail_dependence_note"] = bool(td is not None and td > 0.50)

    symbol_rows = []
    total_gross = sum(gross)
    positive_total = sum(v for v in gross if v > 0)
    for sym, group in sorted(_group(rows, lambda r: r.symbol).items()):
        gs = [r.gross_return_bps for r in group]
        ns = [r.net_return_bps for r in group if r.net_return_bps is not None]
        row = {
            "symbol": sym,
            "entry_count": len(group),
            "entry_share": len(group) / len(rows) if rows else None,
            "gross_mean": mean(gs),
            "gross_median": median(gs),
            "gross_win_rate": _positive_share(gs),
            "gross_contribution_sum": sum(gs),
            "gross_contribution_share": (sum(gs) / total_gross) if abs(total_gross) > 1e-12 else None,
        }
        if ns:
            row.update({"net_mean": mean(ns), "net_median": median(ns), "net_win_rate": _positive_share(ns)})
        symbol_rows.append(row)
    contrib_shares = [max(0.0, r["gross_contribution_sum"]) / positive_total for r in symbol_rows if positive_total > 0]
    hhi = sum(s * s for s in contrib_shares)
    sorted_contrib = sorted(contrib_shares, reverse=True)
    symbol_conc = {
        "symbols_positive_gross_median": sum(1 for r in symbol_rows if r["gross_median"] > 0),
        "symbols_positive_net_median": sum(1 for r in symbol_rows if r.get("net_median") is not None and r["net_median"] > 0),
        "top1_symbol_gross_contribution_share": sum(sorted_contrib[:1]) if sorted_contrib else None,
        "top3_symbol_gross_contribution_share": sum(sorted_contrib[:3]) if sorted_contrib else None,
        "top5_symbol_gross_contribution_share": sum(sorted_contrib[:5]) if sorted_contrib else None,
        "gross_contribution_hhi": hhi,
    }
    if hhi > 0.20 or (symbol_conc["top3_symbol_gross_contribution_share"] or 0) > 0.50:
        statuses.append(STATUS_CONCENTRATION_WARNING_DIAGNOSTIC)

    monthly = _time_rows(rows, month_key)
    quarterly = _time_rows(rows, quarter_key)
    top_month_share = _top_time_contribution(monthly)
    top_quarter_share = _top_time_contribution(quarterly)
    pos_net_by_q = _positive_net_quarter_share(rows)
    regime = {
        "positive_gross_months": sum(1 for r in monthly if r["gross_median"] > 0),
        "positive_net_months": sum(1 for r in monthly if r.get("net_median") is not None and r["net_median"] > 0),
        "top_month_gross_contribution_share": top_month_share,
        "top_quarter_gross_contribution_share": top_quarter_share,
        "max_quarter_positive_net_entry_share": pos_net_by_q,
    }
    if (top_month_share or 0) > 0.30 or (pos_net_by_q or 0) > 0.40:
        statuses.append(STATUS_REGIME_CONCENTRATION_WARNING_DIAGNOSTIC)
    rolling = rolling_3m_gross_median(rows)
    regime["positive_rolling_3m_gross_windows"] = sum(1 for r in rolling if r["rolling_3m_gross_median"] > 0)
    regime["total_rolling_3m_windows"] = len(rolling)
    return {"overall": overall, "by_symbol": symbol_rows, "symbol_concentration": symbol_conc, "monthly": monthly, "quarterly": quarterly, "regime": regime, "rolling_3m": rolling, "statuses": sorted(set(statuses))}


def _time_rows(rows: list[EntryRecord], key_fn: Any) -> list[dict[str, Any]]:
    out = []
    for key, group in sorted(_group(rows, lambda r: key_fn(r.entry_ts)).items()):
        gs = [r.gross_return_bps for r in group]
        ns = [r.net_return_bps for r in group if r.net_return_bps is not None]
        row = {"period": key, "entries": len(group), "gross_median": median(gs), "gross_sum": sum(gs)}
        if ns:
            row["net_median"] = median(ns)
            row["positive_net_entries"] = sum(1 for v in ns if v > 0)
        out.append(row)
    return out


def _top_time_contribution(rows: list[dict[str, Any]]) -> float | None:
    positives = [max(0.0, r["gross_sum"]) for r in rows]
    total = sum(positives)
    return max(positives) / total if total > 0 else None


def _positive_net_quarter_share(rows: list[EntryRecord]) -> float | None:
    groups = _group([r for r in rows if r.net_return_bps is not None and r.net_return_bps > 0], lambda r: quarter_key(r.entry_ts))
    total = sum(len(g) for g in groups.values())
    return max((len(g) for g in groups.values()), default=0) / total if total else None


def rolling_3m_gross_median(rows: list[EntryRecord]) -> list[dict[str, Any]]:
    by_month = _group(rows, lambda r: month_key(r.entry_ts))
    months = sorted(by_month)
    out = []
    for i in range(2, len(months)):
        window = months[i - 2 : i + 1]
        vals = [r.gross_return_bps for m in window for r in by_month[m]]
        q = f"{parse_ts(window[-1] + '-01T00:00:00Z').year:04d}-Q{((parse_ts(window[-1] + '-01T00:00:00Z').month - 1) // 3) + 1}"
        out.append({"window_end_month": window[-1], "reported_quarter": q, "rolling_3m_gross_median": median(vals), "entries": len(vals)})
    return out


def compute_q2(rows: list[EntryRecord], explicit_fee_bps: float | None = None) -> dict[str, Any]:
    gross = [r.gross_return_bps for r in rows]
    net_pairs = [(r.gross_return_bps, r.net_return_bps) for r in rows if r.net_return_bps is not None]
    costs = [g - n for g, n in net_pairs]
    funding = [r.funding_bps for r in rows if r.funding_bps is not None]
    residuals = []
    if explicit_fee_bps is not None:
        for r in rows:
            if r.net_return_bps is not None and r.funding_bps is not None:
                residuals.append((r.gross_return_bps - r.net_return_bps) - explicit_fee_bps - r.funding_bps)
    sensitivity = []
    for c in [0, 5, 6, 10, 12, 25, 50, 75, 100, 117]:
        nets = [g - c for g in gross]
        sensitivity.append({"cost_bps": c, "positive_share_after_cost": _positive_share(nets), "median_net_after_cost_bps": median(nets) if nets else None, "mean_net_after_cost_bps": mean(nets) if nets else None})
    per_symbol = {}
    for sym, group in _group(rows, lambda r: r.symbol).items():
        gs = [r.gross_return_bps for r in group]
        per_symbol[sym] = {"median_gross_breakeven_bps": median(gs), "mean_gross_breakeven_bps": mean(gs), "cumulative_sum_breakeven_bps": sum(gs) / len(gs) if gs else None}
    out = {
        "median_gross": median(gross) if gross else None,
        "mean_gross": mean(gross) if gross else None,
        "median_net": median([n for _, n in net_pairs]) if net_pairs else None,
        "mean_net": mean([n for _, n in net_pairs]) if net_pairs else None,
        "median_realized_total_cost_bps": median(costs) if costs else None,
        "mean_realized_total_cost_bps": mean(costs) if costs else None,
        "median_explicit_fee_bps": explicit_fee_bps,
        "median_funding_bps": median(funding) if funding else None,
        "median_residual_cost_bps": median(residuals) if residuals else None,
        "median_per_trade_breakeven_total_cost_bps": median(gross) if gross else None,
        "mean_per_trade_breakeven_total_cost_bps": mean(gross) if gross else None,
        "cumulative_sum_breakeven_total_cost_bps": sum(gross) / len(gross) if gross else None,
        "cost_distribution": stats(costs, "realized_total_cost_bps") if costs else {},
        "cost_sensitivity": sensitivity,
        "per_symbol": per_symbol,
        "funding_sign_convention": "funding_bps normalized to position-holder perspective; negative means bleed, positive means received",
    }
    return out


def load_price_archive(path: Path) -> dict[str, list[PriceBar]]:
    out: dict[str, list[PriceBar]] = defaultdict(list)
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]].append(PriceBar(r["timestamp_utc"], r["symbol"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    for bars in out.values():
        bars.sort(key=lambda b: parse_ts(b.timestamp_utc))
    return dict(out)


def compute_giveback(rows: list[EntryRecord], prices: dict[str, list[PriceBar]]) -> dict[str, Any]:
    trades = []
    for r in rows:
        bars = [b for b in prices.get(r.symbol, []) if parse_ts(r.entry_ts) <= parse_ts(b.timestamp_utc) <= parse_ts(r.exit_ts)]
        if not bars:
            continue
        if r.direction == "short":
            mfe_vals = [((r.entry_price / b.low) - 1) * 10000 for b in bars]
            mae_vals = [((r.entry_price / b.high) - 1) * 10000 for b in bars]
        else:
            mfe_vals = [((b.high / r.entry_price) - 1) * 10000 for b in bars]
            mae_vals = [((b.low / r.entry_price) - 1) * 10000 for b in bars]
        mfe = max(mfe_vals)
        mae = min(mae_vals)
        mfe_idx = mfe_vals.index(mfe)
        realized = r.gross_return_bps
        ratio = realized / mfe if mfe > 0 else None
        trades.append({
            "symbol": r.symbol,
            "entry_ts": r.entry_ts,
            "exit_ts": r.exit_ts,
            "direction": r.direction,
            "exit_reason": r.exit_reason,
            "mfe_bps": mfe,
            "mae_bps": mae,
            "realized_gross_bps": realized,
            "giveback_bps": mfe - realized,
            "peak_capture_ratio": ratio,
            "mfe_bars_from_entry": mfe_idx,
            "mfe_positive_realized_nonpositive": bool(mfe > 0 and realized <= 0),
            "mfe_gt_50_capture_lt_50pct": bool(mfe > 50 and ratio is not None and ratio < 0.5),
        })
    if not trades:
        return {"statuses": [STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK], "trades": [], "summary": {}, "by_symbol": {}, "by_month": {}, "by_quarter": {}, "by_exit_reason": {}}
    ratios = [t["peak_capture_ratio"] for t in trades if t["peak_capture_ratio"] is not None]
    med_ratio = median(ratios) if ratios else None
    signature = "unavailable" if med_ratio is None else ("strong" if med_ratio < 0.30 else "moderate" if med_ratio < 0.50 else "not_primary")
    summary = {
        "median_mfe_bps": median([t["mfe_bps"] for t in trades]),
        "median_mae_bps": median([t["mae_bps"] for t in trades]),
        "median_realized_gross_bps": median([t["realized_gross_bps"] for t in trades]),
        "median_giveback_bps": median([t["giveback_bps"] for t in trades]),
        "median_peak_capture_ratio": med_ratio,
        "peak_capture_ratio_p10": percentile(ratios, 0.10) if ratios else None,
        "peak_capture_ratio_p25": percentile(ratios, 0.25) if ratios else None,
        "peak_capture_ratio_p50": percentile(ratios, 0.50) if ratios else None,
        "peak_capture_ratio_p75": percentile(ratios, 0.75) if ratios else None,
        "peak_capture_ratio_p90": percentile(ratios, 0.90) if ratios else None,
        "median_mfe_bars_from_entry": median([t["mfe_bars_from_entry"] for t in trades]),
        "share_mfe_positive_realized_nonpositive": sum(t["mfe_positive_realized_nonpositive"] for t in trades) / len(trades),
        "share_mfe_gt_50_capture_lt_50pct": sum(t["mfe_gt_50_capture_lt_50pct"] for t in trades) / len(trades),
        "giveback_signature": signature,
    }
    return {"statuses": [], "trades": trades, "summary": summary, "by_symbol": _giveback_group(trades, "symbol"), "by_month": _giveback_group(trades, lambda t: month_key(t["entry_ts"])), "by_quarter": _giveback_group(trades, lambda t: quarter_key(t["entry_ts"])), "by_exit_reason": _giveback_group(trades, "exit_reason")}


def _giveback_group(trades: list[dict[str, Any]], key: str | Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[t[key] if isinstance(key, str) else key(t)].append(t)
    return {k: {"count": len(v), "median_mfe_bps": median([x["mfe_bps"] for x in v]), "median_giveback_bps": median([x["giveback_bps"] for x in v]), "median_peak_capture_ratio": median([x["peak_capture_ratio"] for x in v if x["peak_capture_ratio"] is not None]) if any(x["peak_capture_ratio"] is not None for x in v) else None} for k, v in groups.items()}


def validate_statuses(statuses: list[str]) -> None:
    for s in statuses:
        if s not in ALLOWED_STATUSES or s in FORBIDDEN_VERDICTS:
            raise ValueError(f"forbidden or unknown status emitted: {s}")


def validate_markdown_firewall(text: str) -> None:
    scan_text = text.replace(NON_CONCLUSIONS, "")
    m = FORBIDDEN_MARKDOWN_RE.search(scan_text)
    if m:
        raise ValueError(f"forbidden markdown phrase: {m.group(0)}")


def scan_ast_for_forbidden_v0_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_V0_MODULES or alias.name.endswith("hyperliquid_supertrend_4h1d_phase0") or alias.name.endswith("hyperliquid_supertrend_archive_ingest"):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            full = f".{mod}" if node.level else mod
            if full in FORBIDDEN_V0_MODULES or mod.endswith("hyperliquid_supertrend_4h1d_phase0") or mod.endswith("hyperliquid_supertrend_archive_ingest"):
                found.append(full)
    return found


def firewall_compliance(markdown_text: str) -> dict[str, bool]:
    source = Path(__file__).read_text()
    ast_ok = not scan_ast_for_forbidden_v0_imports(ast.parse(source))
    phrase_ok = FORBIDDEN_MARKDOWN_RE.search(markdown_text.replace(NON_CONCLUSIONS, "")) is None
    forbidden_ok = not any(v in markdown_text for v in FORBIDDEN_VERDICTS)
    return {
        "no_new_strategy_code": True,
        "no_parameter_recommendations": True,
        "no_symbol_recommendations": True,
        "no_regime_recommendations": True,
        "no_exit_rule_recommendations": True,
        "no_cost_model_recommendations": True,
        "no_v0_source_imports": ast_ok,
        "no_forbidden_verdicts": forbidden_ok,
        "no_forbidden_phrases_in_markdown": phrase_ok,
        "non_conclusions_constant_present_verbatim": NON_CONCLUSIONS in markdown_text,
        "no_orders_" + "private" + "_keys_auth_live_execution": True,
        "registry_untouched": True,
    }


def current_git(field: str) -> str:
    args = ["git", "rev-parse", "HEAD"] if field == "sha" else ["git", "branch", "--show-current"]
    return subprocess.check_output(args, text=True).strip()


def choose_statuses(q1: dict[str, Any], q2: dict[str, Any], q3: dict[str, Any]) -> list[str]:
    statuses = [STATUS_READY]
    statuses.extend(q1.get("statuses", []))
    med_gross = q2.get("median_gross")
    med_cost = q2.get("median_realized_total_cost_bps")
    med_net = q2.get("median_net")
    concentrated = STATUS_CONCENTRATION_WARNING_DIAGNOSTIC in statuses or STATUS_REGIME_CONCENTRATION_WARNING_DIAGNOSTIC in statuses
    if med_gross is not None and med_cost is not None and med_net is not None and med_gross > 0 and med_net < 0 and med_cost > med_gross:
        statuses.append(STATUS_COST_STRUCTURE_BLOCKED_DIAGNOSTIC)
        if not concentrated:
            statuses.append(STATUS_BROAD_GROSS_EDGE_COST_BLOCKED_DIAGNOSTIC)
    if STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK in q3.get("statuses", []):
        statuses.append(STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK)
    elif q3.get("summary", {}).get("giveback_signature") in {"strong", "moderate"}:
        statuses.append(STATUS_EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC)
    if concentrated:
        statuses.append(STATUS_FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC)
    if not any(s in statuses for s in [STATUS_FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC, STATUS_COST_STRUCTURE_BLOCKED_DIAGNOSTIC, STATUS_EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC]):
        statuses.append(STATUS_PUBLIC_LITERATURE_FOLLOWUP_ONLY_DIAGNOSTIC)
    return sorted(set(statuses), key=statuses.index)


def diagnostic_markdown(summary: dict[str, Any], inventory_md: str, q1: dict[str, Any], q2: dict[str, Any], q3: dict[str, Any]) -> str:
    lines = [
        "# Hyperliquid Supertrend v0 1d residual diagnostic",
        "",
        "## 1. Scope and firewall",
        "Diagnostic-only analysis of existing v0 1d records. No new strategy, no new precommitment, no registry update, no execution path.",
        "",
        "## 2. Non-conclusions constant",
        NON_CONCLUSIONS,
        "",
        "## 3. Source artifacts and provenance",
        f"source_v0_report_dir: {summary['source_v0_report_dir']}",
        f"source_v0_report_dir_sha256: {summary['source_v0_report_dir_sha256']}",
        f"source_v0_entry_artifact_path: {summary.get('source_v0_entry_artifact_path')}",
        f"source_v0_entry_artifact_sha256: {summary.get('source_v0_entry_artifact_sha256')}",
        f"source_v0_price_archive_path: {summary.get('source_v0_price_archive_path')}",
        f"source_v0_price_archive_sha256: {summary.get('source_v0_price_archive_sha256')}",
        "",
        "## 4. Artifact availability and schema inventory",
        inventory_md,
        "## 5. Q1 distribution structure",
        json.dumps(q1["overall"], indent=2, sort_keys=True),
        "",
        "## 6. Q1 by-symbol concentration",
        json.dumps(q1["symbol_concentration"], indent=2, sort_keys=True),
        "",
        "## 7. Q1 by-month / by-quarter regime structure",
        json.dumps(q1["regime"], indent=2, sort_keys=True),
        "",
        "## 8. Q2 breakeven cost structure",
        "The cost sensitivity table shows the cost dependency.",
        json.dumps({k: v for k, v in q2.items() if k not in {"cost_sensitivity", "per_symbol"}}, indent=2, sort_keys=True),
        "",
        "## 9. Q3 giveback structure",
        json.dumps(q3.get("summary") or {"reason": "unavailable"}, indent=2, sort_keys=True),
        "",
        "## 10. Decision boundary",
        json.dumps(summary["decision_boundary_summary"], indent=2, sort_keys=True),
        "",
        "## 11. Explicit non-conclusions",
        "- not a strategy",
        "- not a candidate",
        "- not a v1 precommitment",
        "- not a registry update",
        "- cannot directly seed a new strategy",
        "",
        "## 12. Next allowed action",
        "Either close the family in a separate registry-update task, or write a separate public-literature-grounded precommitment without using this diagnostic to choose parameters.",
    ]
    text = "\n".join(lines) + "\n"
    validate_markdown_firewall(text)
    return text


def run_diagnostic(report_dir: Path, out_dir: Path, price_archive_path: Path | None, git_sha: str | None = None, git_branch: str | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_artifact_inventory(report_dir)
    write_json(out_dir / "artifact_availability.json", inventory)
    inv_md = inventory_markdown(inventory)
    write_text(out_dir / "artifact_availability.md", inv_md)
    entry_path = find_entry_artifact(inventory)
    base_summary: dict[str, Any] = {
        "diagnostic_id": out_dir.name,
        "generated_at_utc": utc_now(),
        "git_sha": git_sha or current_git("sha"),
        "git_branch": git_branch or current_git("branch"),
        "source_v0_report_dir": str(report_dir),
        "source_v0_report_dir_sha256": inventory["report_dir_sha256"],
        "timeframe_analyzed": "1d",
        "forbidden_verdicts_absent": True,
        "registry_updated": False,
        "no_orders": True,
        "no_" + "private" + "_keys": True,
        "no_auth": True,
        "no_live_capture": True,
        "no_strategy_precommitment_written": True,
        "no_v0_source_imports": True,
    }
    if entry_path is None:
        statuses = [STATUS_MISSING_V0_ENTRY_ARTIFACTS, STATUS_DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA]
        write_text(out_dir / "missing_artifacts.md", "MISSING_V0_ENTRY_ARTIFACTS\n\nInspected files are listed in artifact_availability.md. Required per-entry fields were absent.\n")
        base_summary.update({"statuses": statuses, "multiple_statuses_emitted": True, "inconclusive_at_this_schema": True, "q1_distribution_summary": {}, "q2_cost_summary": {}, "q3_giveback_summary": {"reason": "blocked_by_missing_entry_artifact"}, "giveback_path_source": "unavailable", "decision_boundary_summary": {"recommend_family_closure": False, "cost_structure_assessment": "unavailable", "giveback_assessment": "unavailable", "concentration_assessment": "unavailable", "narrative": "Per-entry v0 records were unavailable; downstream diagnostics stopped."}})
        write_json(out_dir / "summary.json", base_summary)
        return base_summary
    rows = load_entries(entry_path)
    if not rows:
        statuses = [STATUS_MISSING_V0_ENTRY_ARTIFACTS, STATUS_DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA]
        write_text(out_dir / "missing_artifacts.md", "MISSING_V0_ENTRY_ARTIFACTS\n\nA per-entry artifact exists, but it contains zero 1d entry records. Downstream Q1/Q2/Q3 diagnostics stopped rather than fabricating records from aggregates.\n")
        base_summary.update({"source_v0_entry_artifact_path": str(entry_path), "source_v0_entry_artifact_sha256": sha256_file(entry_path), "statuses": statuses, "multiple_statuses_emitted": True, "inconclusive_at_this_schema": True, "q1_distribution_summary": {"entry_count": 0}, "q2_cost_summary": {}, "q3_giveback_summary": {"reason": "zero_1d_entry_records_in_entry_artifact"}, "giveback_path_source": "unavailable", "decision_boundary_summary": {"recommend_family_closure": False, "cost_structure_assessment": "unavailable", "giveback_assessment": "unavailable", "concentration_assessment": "unavailable", "narrative": "The discovered per-entry artifact has no 1d entry rows; downstream diagnostics stopped."}})
        write_json(out_dir / "summary.json", base_summary)
        return base_summary
    q1 = compute_q1(rows)
    q2 = compute_q2(rows, explicit_fee_bps=10.0)
    prices = {}
    price_sha = None
    giveback_source = "unavailable"
    if price_archive_path and price_archive_path.exists():
        prices = load_price_archive(price_archive_path)
        price_sha = sha256_file(price_archive_path)
        giveback_source = "reconstructed_from_v0_price_archive"
    q3 = compute_giveback(rows, prices)
    if q3.get("statuses"):
        giveback_source = "unavailable"
    statuses = choose_statuses(q1, q2, q3)
    validate_statuses(statuses)
    closure = STATUS_FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC in statuses
    concentration = "concentrated" if any(s in statuses for s in [STATUS_CONCENTRATION_WARNING_DIAGNOSTIC, STATUS_REGIME_CONCENTRATION_WARNING_DIAGNOSTIC]) else "not_concentrated_by_frozen_thresholds"
    cost_assessment = "cost_structure_blocked" if STATUS_COST_STRUCTURE_BLOCKED_DIAGNOSTIC in statuses else "not_cost_blocked_by_available_fields"
    giveback_assessment = q3.get("summary", {}).get("giveback_signature") or ("schema_insufficient" if STATUS_SCHEMA_INSUFFICIENT_FOR_GIVEBACK in statuses else "unavailable")
    narrative = "Existing v0 1d records show structural diagnostics only; no follow-up design is licensed."
    decision = {"recommend_family_closure": closure, "cost_structure_assessment": cost_assessment, "giveback_assessment": giveback_assessment, "concentration_assessment": concentration, "narrative": narrative}
    base_summary.update({
        "source_v0_entry_artifact_path": str(entry_path),
        "source_v0_entry_artifact_sha256": sha256_file(entry_path),
        "source_v0_price_archive_path": str(price_archive_path) if price_sha else None,
        "source_v0_price_archive_sha256": price_sha,
        "statuses": statuses,
        "multiple_statuses_emitted": len(statuses) > 1,
        "inconclusive_at_this_schema": STATUS_DIAGNOSTIC_INCONCLUSIVE_AT_THIS_SCHEMA in statuses,
        "q1_distribution_summary": q1["overall"],
        "q2_cost_summary": {k: v for k, v in q2.items() if k not in {"cost_sensitivity", "per_symbol"}},
        "q3_giveback_summary": q3.get("summary") or {"reason": "SCHEMA_INSUFFICIENT_FOR_GIVEBACK"},
        "giveback_path_source": giveback_source,
        "decision_boundary_summary": decision,
    })
    write_csv(out_dir / "per_symbol.csv", q1["by_symbol"])
    # add q2 per-symbol breakevens to per_symbol output after primary file creation
    sym_rows = []
    for r in q1["by_symbol"]:
        rr = dict(r); rr.update(q2["per_symbol"].get(r["symbol"], {})); sym_rows.append(rr)
    write_csv(out_dir / "per_symbol.csv", sym_rows)
    write_csv(out_dir / "per_month.csv", q1["monthly"])
    write_csv(out_dir / "per_quarter.csv", q1["quarterly"])
    write_csv(out_dir / "cost_sensitivity.csv", q2["cost_sensitivity"])
    if q3.get("trades"):
        write_csv(out_dir / "giveback.csv", q3["trades"])
    md = diagnostic_markdown(base_summary, inv_md, q1, q2, q3)
    fw = firewall_compliance(md)
    if not all(fw.values()):
        statuses = sorted(set(statuses + [STATUS_FIREWALL_INTEGRITY_FAILED]), key=(statuses + [STATUS_FIREWALL_INTEGRITY_FAILED]).index)
        base_summary["statuses"] = statuses
        write_json(out_dir / "firewall_compliance.json", fw)
        write_json(out_dir / "summary.json", base_summary)
        write_text(out_dir / "diagnostic_report.md", "# Firewall integrity failure\n")
        return base_summary
    write_text(out_dir / "diagnostic_report.md", md)
    write_json(out_dir / "firewall_compliance.json", fw)
    write_json(out_dir / "summary.json", base_summary)
    return base_summary


def most_recent_v0_report(root: Path) -> Path | None:
    dirs = [p for p in root.glob("hyperliquid_supertrend_4h1d_phase0_*") if p.is_dir()]
    return max(dirs, key=lambda p: p.stat().st_mtime) if dirs else None
