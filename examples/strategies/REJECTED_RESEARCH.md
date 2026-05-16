# Rejected / Frozen Research — Polymarket Strategies

This file catalogs strategies that were investigated through falsification and
determined to be non-viable under their tested assumptions. Frozen strategies
are not mathematical impossibilities — they are execution/queue-position
failures under current public-data, fee, and fill assumptions.

---

## Polymarket Complement Arb

**Status**: `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE`
**Date**: 2026-05-16

**Hypothesis**: Buy YES/UP and NO/DOWN tokens of the same Polymarket binary
condition as maker quotes, collecting gross edge when both legs fill before
a one-leg timeout, net of fees, buffers, and unwind losses.

**Result**: 6 separated windows, 1541 detected opportunities, 1098 non-dust,
0 pessimistic paired fills. Trade evidence pipeline is READY. The pincer
failure mode: short durations lack depth, medium durations lack queue turnover,
long durations have edges but zero fills.

**Failure mode**: `queue_position_execution_failure`

**Key constraint**: Public data-api trades endpoint uses `market=<conditionId>`
filter (not per-asset). Market-specific fee rates sourced from Gamma API. Maker
fill path requires cumulative compatible trade volume exceeding depth ahead +
quote size, which did not occur in any observed window.

**Not rejected** — frozen under base 100-share pessimistic maker execution.
Does not prove complement arbitrage impossible. Does not justify live trading.
