from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_asset_ctxs_archive import (
    DEFAULT_OUTPUT_DIR,
    AssetCtxsArchiveError,
    build_asset_ctxs_archive,
    estimate_asset_ctxs_cost,
    result_as_dict,
)
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_oi_velocity_compression_phase0 import (
    FROZEN_SYMBOLS,
    sha256_file,
)

DEFAULT_PRECOMMITMENT = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/"
    "HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_PRECOMMITMENT.md"
)


def _read_date_list(path: Path) -> list[date]:
    dates: list[date] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            dates.append(date.fromisoformat(line))
    if not dates:
        raise ValueError(f"empty date list: {path}")
    return sorted(set(dates))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Hyperliquid public S3 asset context snapshots for OI Phase 0")
    parser.add_argument("--date-list", type=Path, required=True, help="Newline-separated YYYY-MM-DD dates to fetch")
    parser.add_argument("--coins", default=",".join(FROZEN_SYMBOLS), help="Comma-separated requested coins; filtered to frozen universe")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-usd-budget", type=float, required=True)
    parser.add_argument("--confirm-s3-spend", default="", help="Required token: I_HAVE_BUDGET_<estimated_usd>")
    parser.add_argument("--estimate-only", action="store_true", help="Only print requester-pays cost estimate; no download")
    parser.add_argument("--precommitment", type=Path, default=DEFAULT_PRECOMMITMENT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Guard against typo in output path
    out_str = str(args.out)
    if "hyperliquid" not in out_str:
        raise ValueError(f"Output directory must contain 'hyperliquid' spelling; got: {out_str}")
    dates = _read_date_list(args.date_list)
    coins = [part.strip().upper() for part in args.coins.split(",") if part.strip()]
    try:
        estimated_bytes, estimated_usd, confirm_token = estimate_asset_ctxs_cost(dates)
        if args.estimate_only or not args.confirm_s3_spend:
            print(json.dumps({"estimated_bytes": estimated_bytes, "estimated_usd": estimated_usd, "confirm_token": confirm_token}, indent=2, sort_keys=True))
            return 0
        result = build_asset_ctxs_archive(
            dates=dates,
            coins=coins,
            out_dir=args.out,
            max_usd_budget=args.max_usd_budget,
            confirm_token=args.confirm_s3_spend,
            precommitment_hash=sha256_file(args.precommitment),
        )
    except AssetCtxsArchiveError as exc:
        print(json.dumps({"status": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result_as_dict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
