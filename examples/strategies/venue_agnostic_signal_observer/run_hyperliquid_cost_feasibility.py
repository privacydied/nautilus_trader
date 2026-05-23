from __future__ import annotations

import argparse
import json
from pathlib import Path

from .hyperliquid_cost_feasibility import compute_cost_feasibility
from .hyperliquid_cost_feasibility import write_cost_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute Hyperliquid cost feasibility from captured/archive Parquet.")
    parser.add_argument("--data-dir", type=Path, default=Path("data/hyperliquid_live/v0"))
    parser.add_argument("--archive-data-dir", type=Path, default=Path("data/hyperliquid_archive/v0"))
    parser.add_argument("--data-source", choices=["live", "archive", "both"], default="live")
    parser.add_argument("--stress-labels-file", type=Path, help="Filter archive/both analysis to UTC hours containing stress_end_ns labels.")
    parser.add_argument("--coins", default="BTC,LINK")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--maker-fee-bps", type=float, default=1.5)
    parser.add_argument("--taker-fee-bps", type=float, default=4.5)
    parser.add_argument("--out", type=Path, default=Path("reports/hyperliquid_cost_feasibility_v0"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    coins = [x.strip().upper() for x in args.coins.split(",") if x.strip()]
    result = compute_cost_feasibility(
        args.data_dir,
        coins,
        args.window_hours,
        args.maker_fee_bps,
        args.taker_fee_bps,
        data_source=args.data_source,
        archive_data_dir=args.archive_data_dir,
        stress_labels_file=args.stress_labels_file,
    )
    run_dir = write_cost_outputs(result, args.out)
    print(json.dumps({"run_dir": str(run_dir), "recommendation": result.recommendation, "reason": result.reason, "data_source": result.data_source}, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
