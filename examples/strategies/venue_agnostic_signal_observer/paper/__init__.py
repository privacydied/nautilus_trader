"""Paper execution layer for the venue-agnostic signal observer.

Automatically promotes research hypotheses through frozen gates, manages
simulated Nautilus paper strategies, and periodically re-falsifies
promoted strategies against fresh evidence.
"""

from .models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
from .registry import (
    delete_strategy,
    load_all_strategies,
    load_strategy,
    save_strategy,
    update_strategy_state,
)
from .gate_verifier import (
    FrozenGateDecision,
    GateResult,
    check_promotion_frozen,
    verify_promotion_gates,
)
from .auto_promotion import PromotionDecision, evaluate_promotion
from .refalsification import (
    RefalsificationConfig,
    RefalsificationDecision,
    refalsify_strategy,
    run_refalsification_once,
)

__all__ = [
    "PaperExecutionMode",
    "PaperStrategySpec",
    "PaperStrategyState",
    "save_strategy",
    "load_strategy",
    "load_all_strategies",
    "update_strategy_state",
    "delete_strategy",
    "check_promotion_frozen",
    "verify_promotion_gates",
    "evaluate_promotion",
    "PromotionDecision",
    "FrozenGateDecision",
    "GateResult",
    "RefalsificationConfig",
    "RefalsificationDecision",
    "refalsify_strategy",
    "run_refalsification_once",
]