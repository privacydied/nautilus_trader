"""
Bot gate — the ledger-replay authorization check.

The bot must derive permission at load time by replaying current ledger
state. A manifest file alone is not sufficient. The bot fails closed on
any ledger integrity failure or unrecognized state.

The gate implements exactly this logic:
  "Replay the ledger. Confirm latest candidate state is approved,
   unexpired, unrecalled, not demoted, not killed, not corrupt, not
   superseded, and supported by current estimator evidence. Then load
   manifest."

The bot must not:
- discover signals
- scan new grids
- tune parameters
- reinterpret candidates
- promote candidates
- ignore ledger revocations
- run if ledger replay fails
- run if manifest exists but current ledger state is not approved
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from venue_agnostic_signal_observer.governance.ledger import EvidenceLedger
from venue_agnostic_signal_observer.governance.replay import replay_ledger

from .manifest import ManifestRecord
from .manifest import load_manifest


@dataclass
class GateResult:
    authorized: bool
    candidate_hash: str | None
    manifest_record: ManifestRecord | None
    ledger_replay_success: bool
    candidate_state_tradeable: bool
    manifest_present: bool
    manifest_expired: bool
    fail_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


class BotGate:
    """
    Authorization gate for the trading bot.

    The bot says: "I am allowed to quote this exact frozen rule under
    these exact conditions, with these exact limits."

    It says nothing else. It does not discover, tune, or promote.
    """

    def __init__(self, ledger_path: Path, manifest_path: Path) -> None:
        self._ledger_path = ledger_path
        self._manifest_path = manifest_path

    def authorize(self, candidate_hash: str) -> GateResult:
        """
        Authorize bot execution for a specific candidate.

        Steps (all must pass for authorization):
        1. Replay ledger — fail closed on any error.
        2. Confirm candidate state is tradeable (approved, not revoked/killed/demoted/superseded).
        3. Load manifest — must exist and contain the candidate.
        4. Confirm manifest record is not expired.
        """
        # Step 1: Ledger replay
        ledger = EvidenceLedger(self._ledger_path)
        replay = replay_ledger(ledger)

        if not replay.success:
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=None,
                ledger_replay_success=False,
                candidate_state_tradeable=False,
                manifest_present=self._manifest_path.exists(),
                manifest_expired=False,
                fail_reason=f"Ledger replay failed: {replay.error_message}",
            )

        # Step 2: Candidate state check
        is_tradeable = replay.is_approved(candidate_hash)
        state = replay.get_state(candidate_hash)
        if not is_tradeable:
            reason = "Candidate not found in ledger"
            if state is not None:
                if state.is_revoked:
                    reason = f"Candidate revoked: {state.revocation_reason}"
                elif state.is_killed:
                    reason = "Candidate killed by kill-switch"
                elif state.is_demoted:
                    reason = f"Candidate demoted: {state.demotion_reason}"
                elif state.is_superseded:
                    reason = f"Candidate superseded by {state.superseded_by}"
                elif not state.is_approved:
                    reason = "Candidate not approved in ledger"
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=None,
                ledger_replay_success=True,
                candidate_state_tradeable=False,
                manifest_present=self._manifest_path.exists(),
                manifest_expired=False,
                fail_reason=reason,
            )

        # Step 3: Manifest check
        if not self._manifest_path.exists():
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=None,
                ledger_replay_success=True,
                candidate_state_tradeable=True,
                manifest_present=False,
                manifest_expired=False,
                fail_reason="Manifest file not found",
            )

        try:
            manifest = load_manifest(self._manifest_path)
        except Exception as exc:
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=None,
                ledger_replay_success=True,
                candidate_state_tradeable=True,
                manifest_present=True,
                manifest_expired=False,
                fail_reason=f"Manifest load failed: {exc}",
            )

        record = manifest.get(candidate_hash)
        if record is None:
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=None,
                ledger_replay_success=True,
                candidate_state_tradeable=True,
                manifest_present=True,
                manifest_expired=False,
                fail_reason="Candidate not found in manifest",
            )

        if state is None or not state.evidence_events:
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=record,
                ledger_replay_success=True,
                candidate_state_tradeable=False,
                manifest_present=True,
                manifest_expired=False,
                fail_reason="Candidate lacks current estimator evidence",
            )

        if record.grid_hash != state.grid_hash:
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=record,
                ledger_replay_success=True,
                candidate_state_tradeable=False,
                manifest_present=True,
                manifest_expired=False,
                fail_reason=(
                    f"Manifest grid_hash mismatch: manifest has {record.grid_hash}, "
                    f"ledger state has {state.grid_hash}"
                ),
            )

        # Step 4: Expiry check
        if record.is_expired():
            return GateResult(
                authorized=False,
                candidate_hash=candidate_hash,
                manifest_record=record,
                ledger_replay_success=True,
                candidate_state_tradeable=True,
                manifest_present=True,
                manifest_expired=True,
                fail_reason="Manifest record is expired",
            )

        return GateResult(
            authorized=True,
            candidate_hash=candidate_hash,
            manifest_record=record,
            ledger_replay_success=True,
            candidate_state_tradeable=True,
            manifest_present=True,
            manifest_expired=False,
            fail_reason=None,
        )
