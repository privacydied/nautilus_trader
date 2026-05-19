# Spot Availability Audit Report

## Family 3 v1 — funding_falling_oi_unwind_v1

### Root Cause

The Binance Vision spot klines archive changed timestamp units at the 2025 year boundary:

| Era | Timestamp Format | Example (2025-01-01 00:00 UTC) |
|---|---|---|
| **2024 and earlier** | Milliseconds (13-digit) | `1735689600000` |
| **2025+** | Microseconds (16-digit) | `1735689600000000` |

The parser used `datetime.fromtimestamp(int(parts[0]) / 1000, tz=timezone.utc)` which assumes milliseconds. When 2025+ data arrived in microseconds, dividing by 1000 produced a value ~1.7×10^12 (year ~56,971), triggering `OverflowError`/`ValueError` inside `fromtimestamp()`. The exception was silently caught by `except (ValueError, IndexError): pass`, dropping every 2025+ spot kline row.

### Impact on Stage A

| Metric | Before Fix | After Fix | Change |
|---|---|---|---|
| Spot klines loaded | 37,973 | 47,477 | +9,504 (2025+ data restored) |
| Spot months with data | 40 (2020-09 to 2024-12) | 65 (2020-09 to 2026-01) | +25 months |
| Last spot timestamp | 2024-12-31 23:00:00 | 2026-01-31 23:00:00 | +13 months |
| Events with 24h spot | 183 / 223 | **223 / 223** | +40 |
| Events with 48h spot | 183 / 223 | **223 / 223** | +40 |
| Holdout 24h pass | 26 / 66 | **66 / 66** | +40 |
| Holdout 48h pass | 26 / 66 | **66 / 66** | +40 |
| **Stage A outcome** | **UNDERPOWERED_HOLDOUT_FAILURE** | **POPULATION_SUFFICIENT** | |

### Bug Pattern Confirmation

The diagnostic audit confirmed:

1. **24h and 48h failure sets were IDENTICAL** — this was the first tell. Both had the same 40 failures because the bug was at the data-loading level (all 2025+ spot rows were dropped), so no event had any spot data at all after 2024-12-31 regardless of horizon length.

2. **Failures were broadly distributed across 2025/2026** — not clustered at month boundaries. Failures appeared in Feb, Mar, Apr, May, Jun, Sep, Oct, Nov 2025, and Jan 2026. This ruled out a cross-month-ZIP loading bug.

3. **ZIP files existed and downloaded fine** — all 65 monthly spot ZIPs returned HTTP 200 and downloaded successfully. The ZIPs contained CSV data with proper row counts (744 rows for Jan 2025, etc.). The failure was in the *parser*, not the *loader*.

4. **Only 1 failure crossed a month boundary** — confirmed this was NOT a cross-month loader issue.

### Verdict: SPOT_TIMESTAMP_UNIT_MISMATCH_SUSPECTED → SPOT_TIMESTAMP_UNIT_MISMATCH_CONFIRMED

The initial diagnostic verdict was `SPOT_TIMESTAMP_ALIGNMENT_BUG_SUSPECTED`. Upon inspection of the ZIP contents, the exact mechanism was confirmed as **Binance Vision archive timestamp unit change (ms → µs) at the 2025 year boundary**.

### Fix Applied

Added `parse_klines_timestamp()` to `funding_falling_oi_unwind_phase0.py`:

```python
def parse_klines_timestamp(ts_int):
    if ts_int > 100_000_000_000_000:   # > 1e14 → microseconds
        return datetime.fromtimestamp(ts_int / 1_000_000, tz=timezone.utc)
    else:                               # milliseconds (standard)
        return datetime.fromtimestamp(ts_int / 1_000, tz=timezone.utc)
```

Threshold rationale: ms values (2024 and earlier) are ~1.7×10^12; µs values (2025+) are ~1.7×10^15. The 1e14 boundary cleanly separates both domains with a ~100× margin.

### New Stage A Counts

| Metric | Value |
|---|---|
| Total falling OI events | 223 |
| Train | 157 |
| Holdout | 66 |
| 24h cell total | 223 |
| 24h cell holdout | **66** ✅ (>= 50) |
| 48h cell total | 223 |
| 48h cell holdout | **66** ✅ (>= 50) |
| Stage A outcome | **POPULATION_SUFFICIENT** |

### Regression Tests Added

- `test_parse_klines_timestamp_ms` — ms timestamps parse correctly
- `test_parse_klines_timestamp_us` — µs timestamps parse correctly
- `test_parse_klines_timestamp_auto_detects_unit` — auto-detection keeps chronological ordering
- `test_parse_klines_timestamp_invalid_returns_none` — overflow returns None
- `test_spot_availability_late_month_24h` — cross-month horizon works
- `test_spot_availability_late_month_48h` — cross-month 48h works
- `test_24h_vs_48h_are_distinct` — horizon-specific availability differs
- `test_utc_boundary_entry_exact` — exact-match boundary
- `test_utc_boundary_entry_between_klines` — inter-kline boundary
- `test_utc_boundary_horizon_exact` — exact forward horizon boundary
- `test_fixed_module_no_forbidden_imports` — safety grep
- `test_audit_script_no_forbidden` — safety grep

### Multi-Asset Note

The same µs timestamp bug would silently undercount every asset in a future multi-asset portfolio study. The Binance Vision archive uses the same spot klines format for all symbols. Any future multi-asset funding-unwind design must:
- Use the same `parse_klines_timestamp()` helper (already fixed).
- Precommit asset universe, asset×horizon family size, and minimum per-asset history-length rule before seeing counts.
- Not be implemented now.

### Safety

- No Stage B was built or run.
- No forward returns, edge stats, win rates, net bps, nulls, or FDR were computed.
- No registry update was performed.
- No live endpoints, private keys, API keys, authenticated APIs, orders, paper trading, shadow execution, governance, or bot paths were used.

### Artifacts

| File | Description |
|---|---|
| `reports/funding_falling_oi_unwind_v1/spot_availability_audit.json` | Full diagnostic audit data |
| `reports/funding_falling_oi_unwind_v1/spot_availability_audit.md` | This report |
| `reports/funding_falling_oi_unwind_v1/phase0_population_report.json` | Updated Phase 0 report (POPULATION_SUFFICIENT) |
| `examples/strategies/venue_agnostic_signal_observer/funding_falling_oi_unwind_phase0.py` | Fixed module with `parse_klines_timestamp()` |
| `examples/strategies/venue_agnostic_signal_observer/spot_availability_audit.py` | Diagnostic auditor script |
| `examples/strategies/venue_agnostic_signal_observer/tests/test_funding_falling_oi_unwind.py` | 60 tests (all passing) |
