import json
import math
from datetime import datetime
from pathlib import Path

# Path to the latest Phase0A summary JSON (adjust if path changes)
SUMMARY_PATH = Path("reports") / "liquidation_flush_aftershock_reversal_phase0a" / "liquidation_flush_aftershock_reversal_phase0a_20260525T043209_857593_bbe102" / "summary.json"


def load_summary() -> dict:
    if not SUMMARY_PATH.is_file():
        raise FileNotFoundError(f"Phase0A summary not found at {SUMMARY_PATH}")
    return json.loads(SUMMARY_PATH.read_text())


def test_year_concentration_gate_feasibility():
    data = load_summary()

    # Extract needed values
    total_events = data["accepted_event_count_after_cooldown"]
    max_year_share = data["max_calendar_year_event_share"]
    max_year_events = int(round(max_year_share * total_events))
    start_year = datetime.fromisoformat(data["archive_start_utc"].replace("Z", "+00:00")).year
    end_year = datetime.fromisoformat(data["archive_end_utc"].replace("Z", "+00:00")).year
    distinct_years = set(range(start_year, end_year + 1))
    distinct_count = len(distinct_years)

    # Theoretical minimum possible max-year share with perfect balance
    theoretical_min_share = 1.0 / distinct_count if distinct_count else 1.0

    # Gate satisfiability (threshold 0.45)
    threshold = 0.45
    gate_satisfiable = theoretical_min_share <= threshold

    # Extra non‑2024 events required to bring share under threshold
    # Using max_year_events (assuming the most‑populated year is 2024)
    required_total = math.ceil(max_year_events / threshold)
    extra_needed = max(0, required_total - total_events)

    # Assertions: the gate should be unsatisfiable with current archive
    assert not gate_satisfiable, "Gate incorrectly marked satisfiable"
    # Verify extra needed is positive and matches manual calculation (expected 273)
    assert extra_needed == 273, f"Unexpected extra event count: {extra_needed}"
    # Ensure distinct years count is 2 (2024, 2025)
    assert distinct_count == 2, "Archive should cover exactly two calendar years"
    # Check theoretical min share is 0.5
    assert math.isclose(theoretical_min_share, 0.5, rel_tol=1e-9), "Theoretical min share mismatch"
