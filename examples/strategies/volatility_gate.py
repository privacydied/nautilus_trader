"""Check recent Kraken OHLCV volatility for BTC/USD, ETH/USD, SOL/USD, DOGE/USD.

Provides:
  - Main hourly gate (3h range + 1h acceleration) -- authoritative for capture decisions
  - Fast 1-minute diagnostic (15m/30m/60m ranges + fast acceleration)
  - Candle freshness metadata so repeated checks report when to next re-check
"""
import json
import sys
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

PAIRS = {
    "BTC/USD": "XXBTZUSD",
    "ETH/USD": "XETHZUSD",
    "SOL/USD": "SOLUSD",
    "DOGE/USD": "XDGUSD",
}


def fetch_ohlc(pair: str, interval: int = 60, count: int = 10) -> list | None:
    """Fetch Kraken OHLC for a pair. interval=60 means hourly bars, interval=1 means 1-min bars."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = Request(url, headers={"User-Agent": "VolatilityGate/1.0"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                break
        except URLError:
            time.sleep(2**attempt)
    else:
        return None

    result = data.get("result", {})
    ohlc_data = None
    for key in result:
        if key != "last":
            ohlc_data = result[key]
            break
    if ohlc_data is None:
        return None

    bars = []
    for b in ohlc_data[-count:]:
        bars.append({
            "time": int(b[0]),
            "open": float(b[1]),
            "high": float(b[2]),
            "low": float(b[3]),
            "close": float(b[4]),
        })
    return bars


def compute_range(bars: list) -> float | None:
    """Compute high-low range in bps from a list of OHLC bars."""
    if not bars:
        return None
    high = max(b["high"] for b in bars)
    low = min(b["low"] for b in bars)
    ref = low if low > 0 else 1.0
    return ((high - low) / ref) * 10000


def hourly_bar_freshness(btc_bars: list | None) -> dict:
    """Report freshness of the latest hourly candle."""
    if not btc_bars:
        return {
            "latest_hourly_bar_timestamp": None,
            "latest_hourly_bar_age_seconds": None,
            "next_hourly_bar_expected_utc": None,
            "gate_data_stale_or_unchanged": True,
        }

    latest_ts = btc_bars[-1]["time"]
    now_ts = int(time.time())
    age = now_ts - latest_ts

    # Next hourly bar starts at the next whole-hour boundary
    next_hour = ((latest_ts // 3600) + 1) * 3600
    next_human = datetime.fromtimestamp(next_hour, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "latest_hourly_bar_timestamp": latest_ts,
        "latest_hourly_bar_utc": datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_hourly_bar_age_seconds": age,
        "next_hourly_bar_expected_utc": next_human,
        "gate_data_stale_or_unchanged": age < 300,  # candle has less than 5 min lived
    }


def compute_fast_diagnostic(btc_bars_1m: list | None, eth_bars_1m: list | None) -> dict:
    """Compute 15m/30m/60m ranges and acceleration from 1-minute bars.

    Purely diagnostic -- does not authorize capture on its own.
    """
    result = {}

    for symbol, bars, threshold_15m, threshold_30m in [
        ("BTC/USD", btc_bars_1m, 30, 45),
        ("ETH/USD", eth_bars_1m, 40, 60),
    ]:
        if not bars or len(bars) < 15:
            result[symbol] = {"error": "insufficient_1m_bars", "available": len(bars) if bars else 0}
            continue

        r15 = compute_range(bars[-15:])
        r30 = compute_range(bars[-30:]) if len(bars) >= 30 else None
        r60 = compute_range(bars[-60:]) if len(bars) >= 60 else None

        # Fast acceleration: current 15m vs previous 15m, current 30m vs previous 30m
        prev_15 = compute_range(bars[-30:-15]) if len(bars) >= 30 else None
        prev_30 = compute_range(bars[-60:-30]) if len(bars) >= 60 else None

        accel_15 = (r15 / max(prev_15, 1.0)) if (r15 is not None and prev_15 is not None) else None
        accel_30 = (r30 / max(prev_30, 1.0)) if (r30 is not None and prev_30 is not None) else None

        # Fast market state
        fast_active = (r15 is not None and r15 >= threshold_15m) or (r30 is not None and r30 >= threshold_30m)
        fast_building = not fast_active and (
            (r15 is not None and r15 >= (threshold_15m * 0.5))
            or (r30 is not None and r30 >= (threshold_30m * 0.5))
        )
        if fast_active:
            fast_state = "ACTIVE"
        elif fast_building:
            fast_state = "BUILDING"
        else:
            fast_state = "QUIET"

        fast_accel = "ACCELERATING" if (accel_15 is not None and accel_15 >= 2.0) or (accel_30 is not None and accel_30 >= 2.0) else "NOT_ACCELERATING"

        result[symbol] = {
            "range_15m_bps": round(r15, 2) if r15 is not None else None,
            "range_30m_bps": round(r30, 2) if r30 is not None else None,
            "range_60m_bps": round(r60, 2) if r60 is not None else None,
            "prev_15m_bps": round(prev_15, 2) if prev_15 is not None else None,
            "prev_30m_bps": round(prev_30, 2) if prev_30 is not None else None,
            "accel_15m_ratio": round(accel_15, 2) if accel_15 is not None else None,
            "accel_30m_ratio": round(accel_30, 2) if accel_30 is not None else None,
            "fast_market_state": fast_state,
            "fast_acceleration_state": fast_accel,
        }

    return result


def compute_range_3h(bars: list) -> float | None:
    """Compute 3-hour range in bps from a list of hourly bars."""
    if len(bars) < 3:
        return None
    last_3 = bars[-3:]
    return compute_range(last_3)


def compute_1h_ranges(bars: list) -> tuple:
    """Compute the most recent 1h range and the previous 1h range in bps."""
    if len(bars) < 2:
        return None, None
    cur = compute_range([bars[-1]])
    prev = compute_range([bars[-2]])
    return cur, prev


def compute_hourly_gate() -> tuple:
    """Run the main hourly gate. Returns (results dict, market verdict, acceleration verdict)."""
    results = {}
    for symbol, kraken_pair in PAIRS.items():
        bars = fetch_ohlc(kraken_pair, interval=60, count=10)
        if bars is None:
            print(f"ERROR: Failed to fetch {symbol}", file=sys.stderr)
            results[symbol] = {"error": "fetch_failed"}
            continue

        range_3h = compute_range_3h(bars)
        cur_1h, prev_1h = compute_1h_ranges(bars)

        results[symbol] = {
            "range_3h_bps": round(range_3h, 2) if range_3h is not None else None,
            "current_1h_bps": round(cur_1h, 2) if cur_1h is not None else None,
            "previous_1h_bps": round(prev_1h, 2) if prev_1h is not None else None,
            "current_1h_start_utc": datetime.fromtimestamp(bars[-1]["time"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if bars else None,
        }

    # Market classification
    btc_3h = results.get("BTC/USD", {}).get("range_3h_bps") or 0
    eth_3h = results.get("ETH/USD", {}).get("range_3h_bps") or 0
    sol_3h = results.get("SOL/USD", {}).get("range_3h_bps") or 0
    doge_3h = results.get("DOGE/USD", {}).get("range_3h_bps") or 0

    if btc_3h >= 75 or eth_3h >= 90:
        market_verdict = "MARKET_ACTIVE"
    elif sol_3h >= 125 or doge_3h >= 150:
        market_verdict = "MARKET_ALT_ACTIVE"
    elif btc_3h >= 30 or eth_3h >= 40:
        market_verdict = "MARKET_MODERATE"
    else:
        market_verdict = "MARKET_QUIET"

    # Acceleration
    btc_cur_1h = results.get("BTC/USD", {}).get("current_1h_bps") or 0
    btc_prev_1h = results.get("BTC/USD", {}).get("previous_1h_bps") or 0
    eth_cur_1h = results.get("ETH/USD", {}).get("current_1h_bps") or 0
    eth_prev_1h = results.get("ETH/USD", {}).get("previous_1h_bps") or 0

    btc_accel = btc_cur_1h / max(btc_prev_1h, 1.0)
    eth_accel = eth_cur_1h / max(eth_prev_1h, 1.0)

    if btc_accel >= 2.0 or eth_accel >= 2.0:
        accel_verdict = "ACCELERATING"
    else:
        accel_verdict = "NOT_ACCELERATING"

    return results, market_verdict, accel_verdict


if __name__ == "__main__":
    results, market_verdict, accel_verdict = compute_hourly_gate()

    # Freshness from BTC hourly bars
    btc_bars = fetch_ohlc(PAIRS["BTC/USD"], interval=60, count=10)
    freshness = hourly_bar_freshness(btc_bars)
    if freshness["latest_hourly_bar_timestamp"] is None:
        btc_bars_err = results.get("BTC/USD", {}).get("error")
        freshness["error"] = f"BTC/USD fetch failed: {btc_bars_err}"

    # Fast diagnostic using 1-minute bars
    btc_1m = fetch_ohlc(PAIRS["BTC/USD"], interval=1, count=90)
    eth_1m = fetch_ohlc(PAIRS["ETH/USD"], interval=1, count=90)
    fast_diag = compute_fast_diagnostic(btc_1m, eth_1m)

    # Combined market-building diagnostic
    fast_active = False
    if market_verdict in ("MARKET_MODERATE", "MARKET_QUIET"):
        btc_fast = fast_diag.get("BTC/USD", {})
        eth_fast = fast_diag.get("ETH/USD", {})
        fast_active = btc_fast.get("fast_market_state") == "ACTIVE" or eth_fast.get("fast_market_state") == "ACTIVE"
        if fast_active:
            market_verdict_display = "MARKET_BUILDING_FAST_DIAGNOSTIC"
        else:
            market_verdict_display = market_verdict
    else:
        market_verdict_display = market_verdict

    # --- Capture permission logic ---------------------------------------------
    # FULL_ACTIVE_CAPTURE: main gate confirmed MARKET_ACTIVE + ACCELERATING
    # FAST_DIAGNOSTIC_CAPTURE_ONLY: main gate not active, but fast signals front-of-move
    # NO_CAPTURE: neither condition met
    full_gate_pass = market_verdict == "MARKET_ACTIVE" and accel_verdict == "ACCELERATING"

    eth_fast = fast_diag.get("ETH/USD", {})
    btc_fast = fast_diag.get("BTC/USD", {})

    eth_fast_ok = (
        eth_fast.get("fast_market_state") == "ACTIVE"
        and eth_fast.get("fast_acceleration_state") == "ACCELERATING"
        and (
            (eth_fast.get("range_15m_bps") or 0) >= 50
            or (eth_fast.get("range_30m_bps") or 0) >= 60
        )
        and (
            (eth_fast.get("accel_15m_ratio") or 0) >= 2.0
            or (eth_fast.get("accel_30m_ratio") or 0) >= 2.0
        )
    )

    btc_fast_ok = (
        (btc_fast.get("fast_market_state") in ("ACTIVE", "BUILDING"))
        and btc_fast.get("fast_acceleration_state") == "ACCELERATING"
        and (
            (btc_fast.get("range_15m_bps") or 0) >= 30
            or (btc_fast.get("range_30m_bps") or 0) >= 40
        )
        and (
            (btc_fast.get("accel_15m_ratio") or 0) >= 1.75
            or (btc_fast.get("accel_30m_ratio") or 0) >= 1.75
        )
    )

    eth_at_least_building = eth_fast.get("fast_market_state") in ("ACTIVE", "BUILDING")

    fast_capture_eligible = False
    fast_capture_reason = ""
    capture_permission = "NO_CAPTURE"

    if full_gate_pass:
        capture_permission = "FULL_ACTIVE_CAPTURE"
        fast_capture_eligible = True
        fast_capture_reason = (
            f"main_gate_confirmed: {market_verdict} + {accel_verdict}"
        )
    elif eth_fast_ok:
        fast_capture_eligible = True
        capture_permission = "FAST_DIAGNOSTIC_CAPTURE_ONLY"
        r15 = eth_fast.get("range_15m_bps")
        ar30 = eth_fast.get("accel_30m_ratio")
        fast_capture_reason = f"eth_fast_active_accelerating: 30m={r15}bps accel={ar30}"
    elif btc_fast_ok and eth_at_least_building:
        fast_capture_eligible = True
        capture_permission = "FAST_DIAGNOSTIC_CAPTURE_ONLY"
        fast_capture_reason = "btc_fast_building_accelerating_with_eth_building"

    output = {
        "values": results,
        "market_verdict": market_verdict,
        "market_verdict_display": market_verdict_display,
        "acceleration": {
            "btc_current_1h_bps": results.get("BTC/USD", {}).get("current_1h_bps", 0),
            "btc_previous_1h_bps": results.get("BTC/USD", {}).get("previous_1h_bps", 0),
            "btc_accel_ratio": round(results.get("BTC/USD", {}).get("current_1h_bps", 0) / max(results.get("BTC/USD", {}).get("previous_1h_bps", 0), 1.0), 2) if results.get("BTC/USD", {}).get("previous_1h_bps") else None,
            "eth_current_1h_bps": results.get("ETH/USD", {}).get("current_1h_bps", 0),
            "eth_previous_1h_bps": results.get("ETH/USD", {}).get("previous_1h_bps", 0),
            "eth_accel_ratio": round(results.get("ETH/USD", {}).get("current_1h_bps", 0) / max(results.get("ETH/USD", {}).get("previous_1h_bps", 0), 1.0), 2) if results.get("ETH/USD", {}).get("previous_1h_bps") else None,
            "verdict": accel_verdict,
        },
        "freshness": freshness,
        "fast_diagnostic": fast_diag,
        "fast_capture_eligible": fast_capture_eligible,
        "fast_capture_reason": fast_capture_reason if fast_capture_eligible else "not_fast_capture_eligible",
        "capture_permission": capture_permission,
    }

    if market_verdict == "MARKET_MODERATE" and not fast_active:
        print("Hourly gate unchanged; next useful check when the next hourly candle rolls.", file=sys.stderr)
    elif market_verdict in ("MARKET_MODERATE", "MARKET_QUIET") and fast_active:
        print("Fast diagnostic shows building activity; re-check soon.", file=sys.stderr)
    elif market_verdict == "MARKET_ACTIVE" and accel_verdict == "ACCELERATING":
        print("Main gate is MARKET_ACTIVE + ACCELERATING; derivatives v2 capture is eligible.", file=sys.stderr)
    elif market_verdict == "MARKET_ACTIVE" and accel_verdict == "NOT_ACCELERATING":
        print("Main gate is MARKET_ACTIVE but NOT_ACCELERATING; move may be in tail phase.", file=sys.stderr)

    print(json.dumps(output, indent=2))
