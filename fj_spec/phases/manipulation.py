"""
Fools' Journey — Executable Spec
Manipulation Phase logic.

Stage 3: Stub implementation that transitions to Action Phase.
Will be fully implemented in Stage 5.
"""

from __future__ import annotations

from ..types import (
    GameState, Action, Phase,
    ManipulationContext, ActionContext,
)
from ..state_helpers import gs_set_phase


def advance_manipulation(state: GameState) -> GameState:
    """
    Advance the Manipulation Phase by one step.

    Stage 3 stub: Immediately transitions to Action Phase.
    Stage 5 will implement the full manipulation logic.
    """
    ctx = state.phase_context
    assert isinstance(ctx, ManipulationContext)

    return gs_set_phase(state, Phase.ACTION, ActionContext(
        current_turn=state.priority,
    ))


def apply_manipulation_action(state: GameState, action: Action) -> GameState:
    """
    Apply a player action during Manipulation Phase.
    Stage 3 stub: no-op.
    """
    return state
