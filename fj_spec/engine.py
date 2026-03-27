"""
Fools' Journey — Executable Spec
Stage 3: Engine skeleton.

The engine is a pure state machine built around three operations:

  get_decision(state) → PendingDecision | None
      Returns the current decision point, or None if the game is over
      or in a fully automated state (which shouldn't happen after apply).

  apply(state, action) → GameState
      Validates and applies a player action, then auto-advances to the
      next decision point (or game-over).

  auto_advance(state) → GameState
      Processes deterministic steps (shuffling, dealing, phase transitions)
      until a decision point is reached or the game ends.

Phase-specific logic is delegated to phase modules (Stages 4–8).
Until those are implemented, stubs advance to the next phase.
"""

from __future__ import annotations

from .types import (
    PlayerId, Phase, Action, ActionKind,
    GameState, PendingDecision,
    RefreshContext, ManipulationContext, ActionContext, SetupContext,
    GameResult, GameResultKind,
)
from .state_helpers import gs_set_phase, gs_set_pending, gs_set_result


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class IllegalActionError(Exception):
    """Raised when a player submits an action not in the legal action list."""
    pass


class GameOverError(Exception):
    """Raised when apply() is called on a finished game."""
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_decision(state: GameState) -> PendingDecision | None:
    """
    Return the current pending decision, or None if the game is over.
    After apply(), the state will always either have a pending decision
    or be in GAME_OVER phase.
    """
    if state.phase == Phase.GAME_OVER:
        return None
    return state.pending


def apply(state: GameState, action: Action) -> GameState:
    """
    Apply a player action to the game state.

    1. Validate the action is legal.
    2. Process the action (phase-specific).
    3. Auto-advance to the next decision point.

    Returns a new GameState positioned at the next decision point
    (or in GAME_OVER).

    Raises:
        GameOverError: if the game has already ended.
        IllegalActionError: if the action is not legal.
    """
    if state.phase == Phase.GAME_OVER:
        raise GameOverError("Game is already over")

    if state.pending is None:
        raise IllegalActionError("No pending decision to respond to")

    # Validate action is in the legal set
    _validate_action(state, action)

    # Clear the pending decision
    state = gs_set_pending(state, None)

    # Delegate to phase-specific action handler
    state = _apply_phase_action(state, action)

    # Auto-advance to next decision point
    state = auto_advance(state)

    return state


def auto_advance(state: GameState) -> GameState:
    """
    Process deterministic steps until a decision point is reached
    or the game ends.

    This is called after every action application and at game start.
    It handles phase transitions and automated sub-steps within phases.
    """
    # Safety limit to prevent infinite loops during development
    max_iterations = 1000
    for _ in range(max_iterations):
        # Stop if game is over
        if state.phase == Phase.GAME_OVER:
            return state

        # Stop if there's a pending decision
        if state.pending is not None:
            return state

        # Check for death / win conditions
        result = _check_game_end(state)
        if result is not None:
            return gs_set_result(state, result)

        # Delegate to phase-specific auto-advance
        state = _advance_phase(state)

    raise RuntimeError("auto_advance exceeded iteration limit — likely infinite loop")


def start_game(state: GameState) -> GameState:
    """
    Kick off the game from its initial state (phase=REFRESH).
    Auto-advances through the first Refresh Phase until the first
    decision point is reached.
    """
    return auto_advance(state)


# ---------------------------------------------------------------------------
# Phase delegation
# ---------------------------------------------------------------------------

def _advance_phase(state: GameState) -> GameState:
    """
    Perform one step of deterministic phase advancement.
    Returns the state after the step (which may have a new pending decision,
    a phase transition, or require further advancement).
    """
    match state.phase:
        case Phase.SETUP:
            # Setup is fully handled by create_initial_state;
            # if we ever land here, just move to REFRESH.
            return gs_set_phase(state, Phase.REFRESH, RefreshContext())

        case Phase.REFRESH:
            return _advance_refresh(state)

        case Phase.MANIPULATION:
            return _advance_manipulation(state)

        case Phase.ACTION:
            return _advance_action(state)

        case Phase.GAME_OVER:
            return state

        case _:
            raise RuntimeError(f"Unknown phase: {state.phase}")


def _apply_phase_action(state: GameState, action: Action) -> GameState:
    """Delegate action application to the current phase handler."""
    match state.phase:
        case Phase.REFRESH:
            return _apply_refresh_action(state, action)

        case Phase.MANIPULATION:
            return _apply_manipulation_action(state, action)

        case Phase.ACTION:
            return _apply_action_action(state, action)

        case _:
            raise RuntimeError(f"Cannot apply action in phase: {state.phase}")


# ---------------------------------------------------------------------------
# Phase stubs (to be replaced in Stages 4–8)
# ---------------------------------------------------------------------------

def _advance_refresh(state: GameState) -> GameState:
    """Advance the Refresh Phase. Stub: transitions to Manipulation."""
    from .phases.refresh import advance_refresh
    return advance_refresh(state)


def _apply_refresh_action(state: GameState, action: Action) -> GameState:
    """Apply an action during Refresh Phase."""
    from .phases.refresh import apply_refresh_action
    return apply_refresh_action(state, action)


def _advance_manipulation(state: GameState) -> GameState:
    """Advance the Manipulation Phase. Stub: transitions to Action."""
    from .phases.manipulation import advance_manipulation
    return advance_manipulation(state)


def _apply_manipulation_action(state: GameState, action: Action) -> GameState:
    """Apply an action during Manipulation Phase."""
    from .phases.manipulation import apply_manipulation_action
    return apply_manipulation_action(state, action)


def _advance_action(state: GameState) -> GameState:
    """Advance the Action Phase. Stub: transitions to Refresh."""
    from .phases.action import advance_action
    return advance_action(state)


def _apply_action_action(state: GameState, action: Action) -> GameState:
    """Apply an action during Action Phase."""
    from .phases.action import apply_action_action
    return apply_action_action(state, action)


# ---------------------------------------------------------------------------
# Win condition / game-end checking
# ---------------------------------------------------------------------------

def _check_game_end(state: GameState) -> GameResult | None:
    """
    Check if the game should end.
    Called during auto_advance after every step.

    Returns a GameResult if the game is over, None otherwise.
    """
    from .types import Alignment

    red = state.player(PlayerId.RED)
    blue = state.player(PlayerId.BLUE)

    # Both dead (shouldn't normally happen, but handle it)
    if red.is_dead and blue.is_dead:
        return GameResult(
            kind=GameResultKind.EXHAUSTION,
            winner=None,
            description="Both players died simultaneously.",
        )

    # One player dead
    if red.is_dead:
        if red.alignment == Alignment.EVIL:
            return GameResult(
                kind=GameResultKind.GOOD_KILLS_EVIL,
                winner=PlayerId.BLUE,
                description="Good (BLUE) has slain Evil (RED)!",
            )
        else:
            return GameResult(
                kind=GameResultKind.EVIL_KILLS_GOOD,
                winner=PlayerId.BLUE,
                description="Evil (BLUE) has slain Good (RED)!",
            )

    if blue.is_dead:
        if blue.alignment == Alignment.EVIL:
            return GameResult(
                kind=GameResultKind.GOOD_KILLS_EVIL,
                winner=PlayerId.RED,
                description="Good (RED) has slain Evil (BLUE)!",
            )
        else:
            return GameResult(
                kind=GameResultKind.EVIL_KILLS_GOOD,
                winner=PlayerId.RED,
                description="Evil (RED) has slain Good (BLUE)!",
            )

    # Note: Cooperative Good win (both Worlds dead + announced) is NOT checked here.
    # That requires explicit player announcement and is handled as a special action
    # during the Action Phase.

    return None


# ---------------------------------------------------------------------------
# Action validation
# ---------------------------------------------------------------------------

def _validate_action(state: GameState, action: Action) -> None:
    """
    Verify that the submitted action is in the legal action set.
    Raises IllegalActionError if not.
    """
    assert state.pending is not None

    legal = state.pending.legal_actions

    if action not in legal:
        legal_strs = [repr(a) for a in legal[:5]]
        if len(legal) > 5:
            legal_strs.append(f"... ({len(legal)} total)")
        raise IllegalActionError(
            f"Illegal action: {action!r}\n"
            f"Legal actions include: {legal_strs}\n"
            f"Decision: {state.pending.kind.name} — {state.pending.context_description}"
        )
