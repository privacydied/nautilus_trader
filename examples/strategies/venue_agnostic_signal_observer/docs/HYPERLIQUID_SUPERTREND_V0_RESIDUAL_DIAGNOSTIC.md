# Hyperliquid Supertrend v0 residual diagnostic

Diagnostic-only artifact reader for the existing Hyperliquid Supertrend 4h/1d altcoin perp v0 output.

This document describes the diagnostic runner only. It does not define a new strategy, precommitment, registry update, execution path, or follow-up design.

Run:

```bash
uv run --no-sync -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_supertrend_v0_residual_diagnostic
```

The runner reads existing v0 report artifacts and, when present, the existing local hourly price archive for intrahold giveback reconstruction. It does not import the v0 implementation module; tests enforce this with an AST scan.
