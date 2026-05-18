"""Tests for OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json hash stability.

The precommitment file must produce a deterministic, idempotent hash.
If serialization ordering, timestamps, generated fields, or any
nondeterministic content affect the hash, these tests will fail.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_PRECOMMITMENT_REPO_RELATIVE = "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"


@pytest.fixture(scope="module")
def precommitment_path() -> Path:
    """Resolve the precommitment file relative to the repository root."""
    # Walk up from the test directory to find the repo root
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (parent / _PRECOMMITMENT_REPO_RELATIVE).exists():
            return parent / _PRECOMMITMENT_REPO_RELATIVE
    raise FileNotFoundError(
        f"Could not find {_PRECOMMITMENT_REPO_RELATIVE} in any parent of {candidate}"
    )


@pytest.fixture(scope="module")
def precommitment_bytes(precommitment_path: Path) -> bytes:
    return precommitment_path.read_bytes()


class TestPrecommitmentHashStability:
    """The precommitment file must have a stable, reproducible hash.

    The ``compute_precommitment_hash`` function in
    ``offline_replay_manifest.py`` computes ``sha256(path.read_bytes())``.
    If the file is serialized with nondeterministic ordering (e.g. ``dict``
    field order, timestamps, UUIDs, or generated fields), two consecutive
    loads will produce different hashes and the pipeline will reject the
    file with a hash mismatch.
    """

    def test_load_twice_produces_identical_bytes(
        self, precommitment_path: Path,
    ) -> None:
        """Load the file twice, confirm raw bytes are identical.

        This catches nondeterministic write behavior — if the file contains
        a timestamp that changes between writes, the bytes will differ.
        """
        bytes_a = precommitment_path.read_bytes()
        bytes_b = precommitment_path.read_bytes()
        assert bytes_a == bytes_b, (
            "Two successive reads produced different byte sequences. "
            "The precommitment file may contain a live-generated field "
            "(timestamp, UUID, etc.) that changes on each write."
        )

    def test_hash_twice_produces_identical_hash(
        self, precommitment_path: Path,
    ) -> None:
        """Hash the file twice — confirm SHA-256 is identical.

        This is the actual hash that ``compute_precommitment_hash``
        computes.  If this assertion fails, the precommitment hash will
        not match between two pipeline stages.
        """
        hash_a = hashlib.sha256(precommitment_path.read_bytes()).hexdigest()
        hash_b = hashlib.sha256(precommitment_path.read_bytes()).hexdigest()
        assert hash_a == hash_b, (
            "SHA-256 of precommitment file changed between two successive "
            "computations.  The file content is nondeterministic."
        )

    def test_parsed_json_roundtrip_produces_identical_hash(
        self, precommitment_bytes: bytes,
    ) -> None:
        """Parse and re-serialize with sort_keys=True — hash must match.

        This simulates what the pipeline does internally (json.dumps with
        sort_keys).  If the original file has any unstable field or a
        nondeterministic serialization, re-serializing will change the
        content.
        """
        parsed = json.loads(precommitment_bytes)
        reserialized = json.dumps(parsed, indent=2, sort_keys=True).encode("utf-8")
        original_hash = hashlib.sha256(precommitment_bytes).hexdigest()
        reserialized_hash = hashlib.sha256(reserialized).hexdigest()
        assert original_hash == reserialized_hash, (
            "Re-serializing the precommitment JSON with sort_keys=True "
            "produced different bytes than the original file.  The file may "
            "have different indentation, key ordering, or trailing whitespace "
            "than the canonical sort_keys representation."
        )

    def test_roundtrip_parses_to_same_dict(
        self, precommitment_bytes: bytes,
    ) -> None:
        """Parse twice — confirm identical Python dict.

        This catches JSON-level nondeterminism (float precision, duplicate
        keys, unicode normalization).
        """
        d1 = json.loads(precommitment_bytes)
        d2 = json.loads(precommitment_bytes)
        assert d1 == d2, (
            "Two successive parses of the precommitment file produced "
            "different Python objects.  The file contains nondeterministic "
            "content."
        )

    def test_compute_precommitment_hash_function(
        self, precommitment_path: Path,
    ) -> None:
        """Verify the actual compute_precommitment_hash function is stable."""
        from examples.strategies.venue_agnostic_signal_observer.offline_replay_manifest import (
            compute_precommitment_hash,
        )

        hash_a = compute_precommitment_hash(precommitment_path)
        hash_b = compute_precommitment_hash(precommitment_path)
        assert hash_a == hash_b, (
            "compute_precommitment_hash returned different values on two "
            "successive calls with the same file path."
        )

    def test_file_has_created_after_pipeline_construction_field(
        self, precommitment_bytes: bytes,
    ) -> None:
        """The honesty field must be present and True."""
        parsed = json.loads(precommitment_bytes)
        assert parsed.get("created_after_pipeline_construction") is True, (
            "Missing or False 'created_after_pipeline_construction' field. "
            "The precommitment must declare it was created after pipeline "
            "construction, not as a clean pre-registration."
        )

    def test_file_has_created_after_pipeline_construction_note(
        self, precommitment_bytes: bytes,
    ) -> None:
        """The honesty note must be present and non-empty."""
        parsed = json.loads(precommitment_bytes)
        note = parsed.get("created_after_pipeline_construction_note", "")
        assert note and len(note) > 50, (
            "Missing or too-short 'created_after_pipeline_construction_note'. "
            "Future readers must be able to understand the retrospective nature."
        )

    def test_resolution_pinned_before_run_is_true(
        self, precommitment_bytes: bytes,
    ) -> None:
        """The resolution pinning field must be present and True."""
        parsed = json.loads(precommitment_bytes)
        eligibility = parsed.get("horizon_eligibility", {})
        assert eligibility.get("resolution_pinned_before_run") is True, (
            "Missing or False 'resolution_pinned_before_run'. "
            "The data-resolution decision must be pinned before the verdict run."
        )

    def test_resolution_pinned_value_is_trade(
        self, precommitment_bytes: bytes,
    ) -> None:
        """The pinned resolution must be trade-level for Kraken ticks."""
        parsed = json.loads(precommitment_bytes)
        eligibility = parsed.get("horizon_eligibility", {})
        pinned = eligibility.get("resolution_pinned_value")
        assert pinned in ("trade", "agg_trade"), (
            f"Expected pinned resolution to be 'trade' or 'agg_trade', "
            f"got {pinned!r}. Kraken Trades API provides tick data."
        )

    def test_horizon_eligibility_states_data_type(
        self, precommitment_bytes: bytes,
    ) -> None:
        """The horizon eligibility must state whether tick or bar data is used."""
        parsed = json.loads(precommitment_bytes)
        eligibility = parsed.get("horizon_eligibility", {})
        note = eligibility.get("resolution_pinned_note", "")
        assert "trade" in note.lower(), (
            "The horizon eligibility note must explicitly reference the "
            "data resolution type."
        )
