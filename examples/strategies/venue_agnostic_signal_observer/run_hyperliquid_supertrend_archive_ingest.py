#!/usr/bin/env python3
"""Convert local Hyperliquid archives for Supertrend Phase 0A."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import hyperliquid_supertrend_archive_ingest as ingest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build canonical Hyperliquid Supertrend Phase 0 CSV archives")
    parser.add_argument("--asset-ctxs-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/hyperliquid_supertrend_4h1d_phase0"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = ingest.convert_asset_ctxs_and_fetch_funding(asset_ctxs_dir=args.asset_ctxs_dir, output_dir=args.out)
    print(json.dumps({
        "price_csv": str(result.price_csv),
        "funding_csv": str(result.funding_csv),
        "manifest_path": str(result.manifest_path),
        "price_rows": result.manifest["price_rows"],
        "funding_rows": result.manifest["funding_rows"],
        "network_used": result.manifest["network_used"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
