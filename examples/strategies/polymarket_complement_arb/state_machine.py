"""
Hybrid state machine for Polymarket complement arb strategy.

Manages the lifecycle of a paired YES+NO position with explicit states,
timeouts, and unwind logic. Not buried in ad-hoc if-statements.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from .config import ComplementArbConfig
from .models import ComplementMarket, StateTransition


class ArbState(Enum):
    IDLE = auto()
    QUOTING_BOTH_LEGS = auto()
    ONE_LEG_FILLED_PENDING_SECOND = auto()
    SECOND_LEG_CROSSABLE = auto()
    SECOND_LEG_RESTING = auto()
    PAIR_COMPLETE = auto()
    UNWINDING = auto()
    UNWOUND = auto()
    CLOSED_UNPAIRED = auto()
    RESOLUTION_DETECTED_UNPAIRED = auto()
    SETTLED_UNPAIRED = auto()
    CANCELLED = auto()
    ERROR = auto()


@dataclass
class PairStateMachine:
    """
    State machine for a single condition's complement arb position.

    Each condition_id gets its own state machine instance.
    """

    condition_id: str
    market: ComplementMarket
    config: ComplementArbConfig

    state: ArbState = ArbState.IDLE
    yes_fill_price: float | None = None
    no_fill_price: float | None = None
    intended_qty: float = 0.0
    paired_qty: float = 0.0
    residual_yes_qty: float = 0.0
    residual_no_qty: float = 0.0
    one_leg_timestamp_ns: int | None = None
    transitions: list[StateTransition] = field(default_factory=list)
    entry_prices: dict[str, float] = field(default_factory=dict)
    second_leg_rest_price: float | None = None

    def _record_transition(self, to_state: ArbState, reason: str) -> None:
        """Record a state transition."""
        ts_ns = time.time_ns()
        self.transitions.append(
            StateTransition(
                condition_id=self.condition_id,
                state_before=self.state.name,
                state_after=to_state.name,
                reason=reason,
                ts_event_ns=ts_ns,
            ),
        )
        self.state = to_state

    def transition_to_quoting(self, intended_qty: float) -> None:
        """Start quoting both legs."""
        self.intended_qty = intended_qty
        self._record_transition(ArbState.QUOTING_BOTH_LEGS, "start_quoting")

    def on_one_leg_filled(self, side: str, fill_price: float, fill_qty: float) -> None:
        """Handle one leg of the pair being filled."""
        if side == "YES":
            self.yes_fill_price = fill_price
            self.residual_yes_qty += fill_qty
        else:
            self.no_fill_price = fill_price
            self.residual_no_qty += fill_qty

        self.one_leg_timestamp_ns = time.time_ns()
        self._record_transition(
            ArbState.ONE_LEG_FILLED_PENDING_SECOND,
            f"{side}_leg_filled at {fill_price}",
        )

    def on_one_leg_filled_stale(self, side: str, fill_price: float, fill_qty: float) -> None:
        """Handle one leg of the pair being filled from stale data."""
        self.on_one_leg_filled(side, fill_price, fill_qty)

    def try_close_second_leg(self, target_price: float) -> bool:
        """
        Attempt to close the second leg. Returns True if crossable.

        Must be called after one leg has filled.
        """
        if self.state != ArbState.ONE_LEG_FILLED_PENDING_SECOND:
            return False

        # Determine which leg needs closing
        if self.yes_fill_price is not None and self.no_fill_price is None:
            second_leg = "NO"
            first_price = self.yes_fill_price
        elif self.no_fill_price is not None and self.yes_fill_price is None:
            second_leg = "YES"
            first_price = self.no_fill_price
        else:
            self._record_transition(ArbState.ERROR, "both_legs_filled_or_neither")
            return False

        # Evaluate if crossable
        from .edge_model import evaluate_taker_close_gate
        from decimal import Decimal

        qty = Decimal(str(self.intended_qty))
        fee_rate = Decimal(str(self.market.taker_fee_rate))

        passes, costs, gap = evaluate_taker_close_gate(
            yes_fill_price=Decimal(str(first_price)),
            target_no_price=Decimal(str(target_price)),
            quantity=qty,
            fee_rate=fee_rate,
            config=self.config,
        )

        if passes:
            self._record_transition(ArbState.SECOND_LEG_CROSSABLE, f"second_leg={second_leg}")
            return True
        else:
            self.second_leg_rest_price = target_price
            self._record_transition(
                ArbState.SECOND_LEG_RESTING,
                f"second_leg={second_leg} not_crossable, costs={costs['total_cost']:.5f}",
            )
            return False

    def on_pair_complete(self, yes_qty: float, no_qty: float) -> None:
        """Both legs filled."""
        self.paired_qty = min(yes_qty, no_qty)
        self.residual_yes_qty = max(yes_qty - no_qty, 0.0)
        self.residual_no_qty = max(no_qty - yes_qty, 0.0)
        self._record_transition(ArbState.PAIR_COMPLETE, "both_legs_filled")

    def on_unwind(self, reason: str) -> None:
        """Start unwinding."""
        self._record_transition(ArbState.UNWINDING, reason)

    def on_unwound(self) -> None:
        """Unwind complete."""
        self._record_transition(ArbState.UNWOUND, "unwind_complete")

    def on_timeout(self) -> None:
        """One-leg timeout expired."""
        if self.state == ArbState.ONE_LEG_FILLED_PENDING_SECOND:
            self._record_transition(ArbState.UNWINDING, "timeout_expired")
        else:
            self._record_transition(ArbState.CANCELLED, "timeout_in_quoting")

    def on_market_closed(self) -> None:
        """Market became closed while unpaired."""
        if self.state in (
            ArbState.ONE_LEG_FILLED_PENDING_SECOND,
            ArbState.SECOND_LEG_RESTING,
            ArbState.SECOND_LEG_CROSSABLE,
        ):
            self._record_transition(ArbState.CLOSED_UNPAIRED, "market_closed_during_unpaired_exposure")

    def on_resolution_detected(self) -> None:
        """Market resolution detected while unpaired."""
        if self.state in (
            ArbState.ONE_LEG_FILLED_PENDING_SECOND,
            ArbState.SECOND_LEG_RESTING,
            ArbState.SECOND_LEG_CROSSABLE,
            ArbState.CLOSED_UNPAIRED,
        ):
            self._record_transition(
                ArbState.RESOLUTION_DETECTED_UNPAIRED,
                "resolution_detected_during_unpaired",
            )

    def on_settled(self) -> None:
        """Settlement value known for unpaired exposure."""
        if self.state == ArbState.RESOLUTION_DETECTED_UNPAIRED:
            self._record_transition(
                ArbState.SETTLED_UNPAIRED,
                "settlement_value_known",
            )

    def on_cancelled(self) -> None:
        """Position cancelled."""
        self._record_transition(ArbState.CANCELLED, "cancelled")

    def check_timeout(self) -> bool:
        """Check if one-leg timeout has expired. Returns True if should unwind."""
        if (
            self.state == ArbState.ONE_LEG_FILLED_PENDING_SECOND
            and self.one_leg_timestamp_ns is not None
        ):
            elapsed_ms = (time.time_ns() - self.one_leg_timestamp_ns) / 1_000_000
            return elapsed_ms > self.config.one_leg_timeout_ms
        return False

    def is_terminal(self) -> bool:
        """Check if this machine is in a terminal state."""
        return self.state in (
            ArbState.PAIR_COMPLETE,
            ArbState.UNWOUND,
            ArbState.CLOSED_UNPAIRED,
            ArbState.RESOLUTION_DETECTED_UNPAIRED,
            ArbState.SETTLED_UNPAIRED,
            ArbState.CANCELLED,
            ArbState.ERROR,
        )

    def current_exposure_usdc(self) -> float:
        """Compute current unpaired exposure in USDC."""
        max_price = max(
            self.yes_fill_price or 0,
            self.no_fill_price or 0,
            0.01,
        )
        residual = self.residual_yes_qty + self.residual_no_qty
        return residual * max_price
