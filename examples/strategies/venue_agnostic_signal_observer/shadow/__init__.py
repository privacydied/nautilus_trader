"""
Shadow Executor — Phase 5.

Extends the L2 maker paper simulator into cross-venue trigger → target-book
fill modelling. Tests whether statistically validated candidates can actually
be captured in practice.

Invariants:
- Shadow must not place orders.
- Shadow must report uncertainty/error bars on all fill estimates.
- A shadow result with positive mean but wider uncertainty than the edge is not a pass.
- No Miner/Validator/Shadow code imports live execution clients.
- No private-key env vars.
"""

from .fill_model import CrossVenueFillModel, FillModelConfig, FillEvent
from .shadow_executor import run_shadow, ShadowResult

__all__ = [
    "CrossVenueFillModel",
    "FillModelConfig",
    "FillEvent",
    "run_shadow",
    "ShadowResult",
]
