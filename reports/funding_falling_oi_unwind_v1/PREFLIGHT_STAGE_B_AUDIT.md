# Preflight Stage B Audit Report

## Study: `family3_funding_falling_oi_unwind_v1`
**Audit timestamp:** 2026-05-19T00:58:03.666643+00:00

**Preflight verdict:** `PREFLIGHT_PASSED`

---
## Preflight 1: 24h/48h Horizon Awareness

- Total falling-OI events: 223
- Events where BOTH 24h AND 48h available: 223
- Events where BOTH unavailable: 0
- Events where ONLY 24h available: 0
- Events where ONLY 48h available: 0
- Latest settlement timestamp: 2026-01-09T00:00:00.003000+00:00
- Latest 24h target: 2026-01-10T00:00:00.003000+00:00
- Latest 48h target: 2026-01-11T00:00:00.003000+00:00
- Last available spot timestamp: 2026-01-31T23:00:00+00:00
- Boundary events (24h should pass, 48h should fail): 0

**Conclusion:** HORIZON_AWARENESS_CONFIRMED_IDENTICAL_BY_DATA

The last spot timestamp covers all 48h targets. No event lies near
the archive boundary where the two horizons would diverge. Identical
24h/48h counts are **data-driven**, not code-driven.

---
## Preflight 2a: Funding Timestamp Parser Audit

- Parser: `datetime.fromtimestamp(int(r['calc_time']) / 1000, tz=timezone.utc)`
- Raw column: `calc_time`
- Total rows: 5937
- earliest: raw=1598918400000, digits=13, parsed=2020-09-01T00:00:00+00:00, grid=True
- latest: raw=1769875200009, digits=13, parsed=2026-01-31T16:00:00.009000+00:00, grid=True
- around_2024_12: raw=1734220800000, digits=13, parsed=2024-12-15T00:00:00+00:00, grid=True
- around_2025_01: raw=1735689600015, digits=13, parsed=2025-01-01T00:00:00.015000+00:00, grid=True
- Rows with µs timestamps: 0
- Post-2024 rows: 1188
- Off-grid settlements: 0
- Verdict: FUNDING_TIMESTAMP_MS_CONFIRMED

---
## Preflight 2b: OI Metrics Timestamp Parser Audit

- Parser: `datetime.strptime(r['create_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)`
- Raw column: `create_time`
- Total rows: 103426
- Rows exactly at funding grid (00:00/08:00/16:00): 1040
- Alignment verdict: OI_ALIGNMENT_GENUINE_CONFIRMED

---
## Safety

- No Stage B was built or run.
- No forward returns, edge stats, win rates, net bps, nulls, or FDR computed.
- No registry update performed.
- No live endpoints, private keys, API keys, authenticated APIs, orders,
  paper trading, shadow execution, governance, or bot paths used.
- Only Binance Vision archive data used.
