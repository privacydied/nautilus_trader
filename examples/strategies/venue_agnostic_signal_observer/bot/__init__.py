"""
Trading Bot — Phase 6.

NautilusTrader execution gated on manifest + ledger replay.

The bot consumes current approved candidate state only. It does not
discover, scan, tune, promote, or reinterpret candidates.

Invariants:
- No bot discovery, tuning, or promotion.
- No bot fail-open on corrupt, missing, truncated, or ambiguous ledger.
- No approved manifest can authorize trading without current ledger replay.
- No running with expired manifests, missing hash checks, or absent kill switches.
- The bot says: "I am allowed to quote this exact frozen rule under
  these exact conditions, with these exact limits."
"""

from .gate import BotGate
from .gate import GateResult
from .manifest import ApprovedManifest
from .manifest import ManifestRecord
from .manifest import load_manifest


__all__ = [
    "ApprovedManifest",
    "BotGate",
    "GateResult",
    "ManifestRecord",
    "load_manifest",
]
