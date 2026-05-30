"""Forward residual diagnostic using HL oracle contexts from forward recorder.

Diagnostic-only: computes mid-oracle and mark-oracle basis residuals.
No PnL, no returns, no signals, no trading logic.
"""

import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict


def load_forward_recorder_contexts(recorder_root: str) -> list[dict]:
    """Load all asset context snapshots from newest forward recorder run."""
    root = Path(recorder_root)
    if not root.exists():
        return []
    
    # Find newest run
    runs = sorted([d for d in root.iterdir() if d.is_dir() and len(d.name) == 8])
    if not runs:
        return []
    
    newest_run = runs[-1]
    context_file = newest_run / "asset_context_snapshots" / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
    
    # If today's file doesn't exist, find the most recent
    if not context_file.exists():
        for run_dir in reversed(runs):
            for jsonl in run_dir.glob("asset_context_snapshots/*.jsonl"):
                context_file = jsonl
                break
            if context_file.exists():
                break
    
    contexts = []
    if context_file.exists():
        with open(context_file, 'r') as f:
            for line in f:
                try:
                    row = json.loads(line.strip())
                    contexts.append(row)
                except:
                    pass
    
    return contexts


def compute_forward_residuals(contexts: list[dict], target_api_symbols: list[str]) -> dict:
    """Compute forward oracle-basis residuals from recorder contexts."""
    
    # Group by API symbol
    by_symbol = defaultdict(list)
    for ctx in contexts:
        api_sym = ctx.get("api_symbol")
        if api_sym in target_api_symbols:
            by_symbol[api_sym].append(ctx)
    
    results = {}
    for api_sym in target_api_symbols:
        rows = by_symbol.get(api_sym, [])
        if not rows:
            results[api_sym] = {"status": "NO_DATA", "aligned_sample_count": 0}
            continue
        
        # Extract prices
        samples = []
        for row in rows:
            oracle_px = row.get("oracle_price")
            mark_px = row.get("mark_price")
            mid_px = row.get("mid_price")
            
            # Convert to float if string
            try:
                oracle_px = float(oracle_px) if oracle_px is not None else None
                mark_px = float(mark_px) if mark_px is not None else None
                mid_px = float(mid_px) if mid_px is not None else None
            except (ValueError, TypeError):
                continue
            
            if oracle_px and mid_px and oracle_px > 0 and mid_px > 0:
                # Compute residuals
                mid_oracle_bps = 10000.0 * (mid_px - oracle_px) / oracle_px
                sample = {
                    "timestamp_utc": row.get("timestamp_utc"),
                    "mid_px": mid_px,
                    "oracle_px": oracle_px,
                    "mid_oracle_bps": round(mid_oracle_bps, 4),
                    "abs_mid_oracle_bps": round(abs(mid_oracle_bps), 4),
                }
                
                if mark_px and mark_px > 0:
                    mark_oracle_bps = 10000.0 * (mark_px - oracle_px) / oracle_px
                    sample["mark_px"] = mark_px
                    sample["mark_oracle_bps"] = round(mark_oracle_bps, 4)
                    sample["abs_mark_oracle_bps"] = round(abs(mark_oracle_bps), 4)
                
                samples.append(sample)
        
        if not samples:
            results[api_sym] = {"status": "NO_ALIGNED_SAMPLES", "aligned_sample_count": 0}
            continue
        
        # Compute statistics
        abs_bps = sorted(s["abs_mid_oracle_bps"] for s in samples)
        
        def quantile(vals, q):
            if not vals:
                return None
            idx = min(int(len(vals) * q), len(vals) - 1)
            return vals[idx]
        
        # Calendar days
        days = set(s["timestamp_utc"][:10] for s in samples if s.get("timestamp_utc"))
        
        # Tail counts
        tail_counts = {
            f"ge_{t}_bps": sum(1 for x in abs_bps if x >= t)
            for t in [10, 25, 50, 100]
        }
        
        # Tight oracle heuristic
        p99 = quantile(abs_bps, 0.99)
        max_abs = max(abs_bps) if abs_bps else 0
        
        if p99 is not None and p99 < 5 and max_abs < 10:
            oracle_diag = "ORACLE_TRACKS_MID_TIGHT"
        elif len(samples) < 100:
            oracle_diag = "ORACLE_MID_BASIS_UNDERPOWERED"
        else:
            oracle_diag = "ORACLE_MID_BASIS_HAS_TAILS"
        
        results[api_sym] = {
            "api_symbol": api_sym,
            "dex": api_sym.split(":")[0] if ":" in api_sym else "unknown",
            "display_symbol": api_sym.split(":")[1] if ":" in api_sym else api_sym,
            "sample_count": len(rows),
            "aligned_sample_count": len(samples),
            "distinct_calendar_days": len(days),
            "timestamp_min_utc": min(s["timestamp_utc"] for s in samples),
            "timestamp_max_utc": max(s["timestamp_utc"] for s in samples),
            "median_mid_oracle_bps": round(quantile([s["mid_oracle_bps"] for s in samples], 0.5), 4),
            "p75_abs_mid_oracle_bps": round(quantile(abs_bps, 0.75), 4),
            "p90_abs_mid_oracle_bps": round(quantile(abs_bps, 0.9), 4),
            "p99_abs_mid_oracle_bps": round(p99, 4) if p99 else None,
            "max_abs_mid_oracle_bps": round(max_abs, 4),
            **tail_counts,
            "median_mark_oracle_bps": round(quantile([s.get("mark_oracle_bps", 0) for s in samples if "mark_oracle_bps" in s], 0.5), 4) if any("mark_oracle_bps" in s for s in samples) else None,
            "p90_abs_mark_oracle_bps": round(quantile([s.get("abs_mark_oracle_bps", 0) for s in samples if "abs_mark_oracle_bps" in s], 0.9), 4) if any("abs_mark_oracle_bps" in s for s in samples) else None,
            "oracle_tracks_mid_diagnostic": oracle_diag,
            "residual_status": "HL_ORACLE_FORWARD_RESIDUAL_AVAILABLE" if len(samples) >= 100 else "HL_ORACLE_FORWARD_RESIDUAL_UNDERPOWERED",
        }
    
    return results


def main():
    target_symbols = [
        "xyz:TSLA", "flx:TSLA", "km:TSLA", "cash:TSLA",
        "xyz:AAPL", "km:AAPL",
        "xyz:MSFT", "cash:MSFT",
        "xyz:NVDA", "flx:NVDA", "km:NVDA", "cash:NVDA",
    ]
    
    recorder_root = "/mnt/nasirjones/py/nautilus_trader/reports/hip3_builder_dex_tradfi_forward_recorder_v0"
    out_root = Path("/mnt/nasirjones/py/nautilus_trader/.worktrees/hip3-residual-validation/reports/hip3_sonarx_tradfi_l2_residual_phase_minus1_v0")
    out_root.mkdir(parents=True, exist_ok=True)
    
    print(f"Loading forward recorder contexts from: {recorder_root}")
    contexts = load_forward_recorder_contexts(recorder_root)
    print(f"Loaded {len(contexts)} context records")
    
    if not contexts:
        print("ERROR: No forward recorder contexts found")
        result = {"status": "FORWARD_RECORDER_CONTEXTS_NOT_FOUND"}
        with open(out_root / "hl_oracle_forward_residual_diagnostic.json", 'w') as f:
            json.dump(result, f, indent=2)
        return
    
    print("\nComputing forward residuals...")
    results = compute_forward_residuals(contexts, target_symbols)
    
    # Summary
    available_count = sum(1 for r in results.values() if r.get("aligned_sample_count", 0) > 0)
    underpowered_count = sum(1 for r in results.values() if r.get("residual_status") == "HL_ORACLE_FORWARD_RESIDUAL_UNDERPOWERED")
    
    summary = {
        "study_id": "hip3_sonarx_tradfi_l2_residual_phase_minus1_v0",
        "created_at_utc": datetime.now().isoformat(),
        "diagnostic_type": "hl_oracle_forward_residual",
        "forward_recorder_root": recorder_root,
        "target_symbols": target_symbols,
        "markets_with_data": available_count,
        "underpowered_count": underpowered_count,
        "results_by_symbol": results,
        "overall_status": "HL_ORACLE_FORWARD_RESIDUAL_AVAILABLE" if available_count >= 10 else "HL_ORACLE_FORWARD_RESIDUAL_UNDERPOWERED",
    }
    
    # Write JSON
    with open(out_root / "hl_oracle_forward_residual_diagnostic.json", 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    
    # Write Markdown summary
    md_lines = [
        "# HL Oracle Forward Residual Diagnostic\n",
        f"**Study ID**: hip3_sonarx_tradfi_l2_residual_phase_minus1_v0\n",
        f"**Created**: {summary['created_at_utc']}\n",
        f"**Overall Status**: {summary['overall_status']}\n",
        f"**Markets with data**: {available_count}/12\n",
        "\n## Per-Symbol Results\n",
    ]
    
    for api_sym, res in results.items():
        sample_count = res.get("aligned_sample_count", 0)
        status = res.get("residual_status", "UNKNOWN")
        median_bps = res.get("median_mid_oracle_bps", "N/A")
        p90_bps = res.get("p90_abs_mid_oracle_bps", "N/A")
        diag = res.get("oracle_tracks_mid_diagnostic", "N/A")
        
        md_lines.append(f"\n### {api_sym}\n")
        md_lines.append(f"- Aligned samples: {sample_count}\n")
        md_lines.append(f"- Median mid-oracle bps: {median_bps}\n")
        md_lines.append(f"- P90 abs mid-oracle bps: {p90_bps}\n")
        md_lines.append(f"- Oracle diagnostic: {diag}\n")
        md_lines.append(f"- Status: {status}\n")
    
    with open(out_root / "hl_oracle_forward_residual_diagnostic.md", 'w') as f:
        f.write("".join(md_lines))
    
    print(f"\nResults written to:")
    print(f"  {out_root / 'hl_oracle_forward_residual_diagnostic.json'}")
    print(f"  {out_root / 'hl_oracle_forward_residual_diagnostic.md'}")
    print(f"\nOverall status: {summary['overall_status']}")


if __name__ == "__main__":
    main()