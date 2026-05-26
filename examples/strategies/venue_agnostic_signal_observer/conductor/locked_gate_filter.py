"""Locked-gate filter — checks job overlap with locked gates in REJECTED_RESEARCH.md.

Conservative false positives are acceptable.  Silent under-blocking is not.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ConductorJobSpec


@dataclass(frozen=True)
class LockedGateDecision:
    allowed: bool
    matched_gate_numbers: tuple[int, ...] = ()
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def allowed_empty() -> LockedGateDecision:
        return LockedGateDecision(allowed=True, reason="NO_GATE_OVERLAP")

    @staticmethod
    def blocked_with_gates(
        gate_numbers: tuple[int, ...], reason: str
    ) -> LockedGateDecision:
        return LockedGateDecision(
            allowed=False,
            matched_gate_numbers=gate_numbers,
            reason=reason,
        )

    @staticmethod
    def allowed_with_acknowledgment(
        gate_numbers: tuple[int, ...],
    ) -> LockedGateDecision:
        return LockedGateDecision(
            allowed=True,
            matched_gate_numbers=gate_numbers,
            reason="LOCKED_GATE_OVERLAP_ACKNOWLEDGED",
            metadata={"locked_gate_overlap_acknowledged": True},
        )


_GATE_NUMBER_RE = re.compile(r"^(\d+)\.\s+(.*)")


def _parse_locked_gates(path: Path) -> list[dict[str, Any]]:
    """Parse the Locked Gates section from REJECTED_RESEARCH.md.

    Returns a list of dicts with 'gate_number' (int) and 'gate_text' (str).
    """
    gates: list[dict[str, Any]] = []
    if not path.is_file():
        return gates

    with open(path, "r") as f:
        lines = f.readlines()

    in_gates_section = False
    for line in lines:
        stripped = line.rstrip("\n")
        if stripped.startswith("## Locked Gates"):
            in_gates_section = True
            continue
        if in_gates_section and stripped.startswith("## "):
            break
        if not in_gates_section:
            continue
        m = _GATE_NUMBER_RE.match(stripped)
        if m:
            gates.append(
                {"gate_number": int(m.group(1)), "gate_text": m.group(2).strip()}
            )

    return gates


def _eligible_tokens(job: ConductorJobSpec) -> list[str]:
    """Return eligible metadata/study tokens for overlap matching."""
    tokens: list[str] = [job.study_id]
    for key in ("source_venue", "target_venue", "source_symbol", "target_symbol"):
        val = job.metadata.get(key)
        if val is not None and isinstance(val, str):
            tokens.append(val)
    return tokens


def _overlaps_gate(gate_text: str, job: ConductorJobSpec, tokens: list[str]) -> bool:
    """Check if a job overlaps a locked gate.

    A job overlaps a gate if both conditions are true:
    1. job.signal_family appears as a case-insensitive substring in gate_text.
    2. At least one eligible metadata/study token appears in gate_text.
    """
    # Condition 1: signal family substring match (case-insensitive)
    # signal_family uses underscores (e.g. "flow_impulse") but gate text uses
    # natural language (e.g. "flow impulse").  Check both forms.
    family_lower = job.signal_family.lower()
    family_with_spaces = family_lower.replace("_", " ")
    gate_lower = gate_text.lower()

    if family_lower not in gate_lower and family_with_spaces not in gate_lower:
        return False

    # Condition 2: at least one eligible token appears
    for token in tokens:
        if len(token) >= 6:
            # Substring match for longer tokens
            if token.lower() in gate_text.lower():
                return True
        else:
            # Whole-word match for short tokens (< 6 chars)
            pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.IGNORECASE)
            if pattern.search(gate_text):
                return True

    return False


def check_locked_gate(
    rejected_research_path: Path, job: ConductorJobSpec
) -> LockedGateDecision:
    """Check a job against locked gates in REJECTED_RESEARCH.md.

    Returns a LockedGateDecision:
    - Blocked if overlap exists and structural_change_rationale is empty/None.
    - Allowed with acknowledgment flag if overlap exists and rationale is present.
    - Allowed if no overlap.
    """
    gates = _parse_locked_gates(rejected_research_path)
    if not gates:
        return LockedGateDecision.allowed_empty()

    tokens = _eligible_tokens(job)
    matched_numbers: list[int] = []

    for gate in gates:
        if _overlaps_gate(gate["gate_text"], job, tokens):
            matched_numbers.append(gate["gate_number"])

    if not matched_numbers:
        return LockedGateDecision.allowed_empty()

    matched_tuple = tuple(sorted(matched_numbers))

    if not job.structural_change_rationale:
        return LockedGateDecision.blocked_with_gates(
            gate_numbers=matched_tuple,
            reason="LOCKED_GATE_OVERLAP_NO_STRUCTURAL_RATIONALE",
        )

    return LockedGateDecision.allowed_with_acknowledgment(
        gate_numbers=matched_tuple
    )