"""
Fools' Journey — Executable Spec
Action Phase logic.

Stage 3: Stub implementation that transitions back to Refresh Phase.
Will be fully implemented in Stages 6–8.
"""

from __future__ import annotations

from ..types import (
    GameState, Action, Phase,
    ActionContext, RefreshContext,
)
from ..state_helpers import gs_set_phase, gs_increment_turn


def advance_action(state: GameState) -> GameState:
    """
    Advance the Action Phase by one step.

    Stage 3 stub: Immediately transitions back to Refresh Phase
    (incrementing turn number).
    Stage 6 will implement the full action logic.
    """
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)

    state = gs_increment_turn(state)
    return gs_set_phase(state, Phase.REFRESH, RefreshContext())


def apply_action_action(state: GameState, action: Action) -> GameState:
    """
    Apply a player action during Action Phase.
    Stage 3 stub: no-op.
    """
    return state
