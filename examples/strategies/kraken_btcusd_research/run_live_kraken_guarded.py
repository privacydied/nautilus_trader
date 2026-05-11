#!/usr/bin/env python3
"""
Guarded live trading runner for Kraken BTC/USD.

Live trading is DISABLED by default. To enable:

1. Set environment variables:
   - I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes
   - KRAKEN_API_KEY=*** API key
   - KRAKEN_API_SECRET=*** API secret
2. Pass --live flag on CLI

Safety features:
- Requires explicit --live flag
- Requires I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes environment variable
- Requires KRAKEN_API_KEY and KRAKEN_API_SECRET
- Never prints secret values
- Heavy Nautilus/Kraken live imports deferred until live mode is actually entered
"""

import os
import sys
import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    MAKER_FEE,
    TAKER_FEE,
)


@dataclass(frozen=True)
class GuardResult:
    safe: bool
    reason: str | None = None


def validate_live_guard(args: argparse.Namespace, environ: Mapping[str, str]) -> GuardResult:
    """Validate live trading safety conditions.

    This function must NOT import Nautilus live modules.
    """
    if not args.live:
        return GuardResult(safe=True, reason="paper mode")

    # Check safety acknowledgment
    if environ.get("I_UNDERSTAND_THIS_CAN_LOSE_MONEY") != "yes":
        return GuardResult(
            safe=False,
            reason="I_UNDERSTAND_THIS_CAN_LOSE_MONEY must be set to 'yes'",
        )

    # Check API keys present (never print values)
    kraken_key = environ.get("KRAKEN_API_KEY", "")
    kraken_secret = environ.get("KRAKEN_API_SECRET", "")

    if not kraken_key:
        return GuardResult(safe=False, reason="KRAKEN_API_KEY is not set")
    if not kraken_secret:
        return GuardResult(safe=False, reason="KRAKEN_API_SECRET is not set")

    return GuardResult(safe=True)


def main() -> int:
    """Main entry point for live trading (guarded)."""
    parser = argparse.ArgumentParser(description="Run live trading for Kraken BTC/USD strategy")
    parser.add_argument("--live", action="store_true", help="Enable live trading (requires API keys)")
    parser.add_argument("--catalog", type=str, default=None, help="Path to data catalog")
    parser.add_argument("--starting-balance", type=float, default=STARTING_BALANCE_USD)
    args = parser.parse_args()

    # Safety check — pure guard, no Nautilus imports
    result = validate_live_guard(args, os.environ)
    if not result.safe:
        logging.error("Live trading blocked: %s", result.reason)
        return 1

    if args.live:
        # Defer all heavy Nautilus/Kraken imports until here
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.instruments import CurrencyPair
        from nautilus_trader.model.objects import Currency
        from nautilus_trader.model.functions import currency_type_from_str

        instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
        instrument = CurrencyPair(
            instrument_id=instrument_id,
            raw_symbol="BTCUSD",
            base_currency=Currency.from_str("BTC"),
            quote_currency=Currency.from_str("USD"),
            is_inverse=False,
            price_precision=1,
            size_precision=8,
            price_increment="0.1",
            size_increment="0.00000001",
            multiplier=1,
            maker_fee=MAKER_FEE,
            taker_fee=TAKER_FEE,
            margin_init=0,
            margin_maint=0,
            ts_event=0,
            ts_init=0,
        )
        logging.info("Live mode: instrument %s created", instrument_id)
        print("Live trading: instrument ready. Full Nautilus live setup required.")
        return 0
    else:
        print("Paper mode (no live orders). Use --live to enable live trading.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
