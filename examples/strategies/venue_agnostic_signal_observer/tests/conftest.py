"""Pytest configuration — add parent dir to sys.path so sibling module imports work."""
import sys
from pathlib import Path

_parent = str(Path(__file__).resolve().parent)  # venue_agnostic_signal_observer/
if _parent not in sys.path:
    sys.path.insert(0, _parent)
