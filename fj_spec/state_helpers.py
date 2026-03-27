"""
Fools' Journey — Executable Spec
Stage 3: Functional state-update helpers.

Since GameState and all sub-structures are frozen dataclasses, these helpers
provide concise ways to produce new states with targeted modifications.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .types import (
    CardId, CardDef, CardState,
    PlayerId, PlayerState, WeaponSlot, ManipulationField,
    ActionSlot, ActionField,
    GameState, Phase, PhaseContext,
    PendingDecision, Continuation,
    GameResult,
)
from .rng import RngState


# ---------------------------------------------------------------------------
# GameState-level updates
# ---------------------------------------------------------------------------

def gs_set_phase(state: GameState, phase: Phase, ctx: PhaseContext) -> GameState:
    return replace(state, phase=phase, phase_context=ctx)


def gs_set_context(state: GameState, ctx: PhaseContext) -> GameState:
    return replace(state, phase_context=ctx)


def gs_set_pending(state: GameState, pending: PendingDecision | None) -> GameState:
    return replace(state, pending=pending)


def gs_set_result(state: GameState, result: GameResult) -> GameState:
    return replace(state, game_result=result, phase=Phase.GAME_OVER)


def gs_set_priority(state: GameState, pid: PlayerId) -> GameState:
    return replace(state, priority=pid)


def gs_flip_priority(state: GameState) -> GameState:
    return replace(state, priority=state.priority.other())


def gs_set_rng(state: GameState, rng: RngState) -> GameState:
    return replace(state, rng_seed=rng.seed, rng_counter=rng.counter)


def gs_increment_turn(state: GameState) -> GameState:
    return replace(state, turn_number=state.turn_number + 1)


def gs_set_guard_deck(state: GameState, deck: tuple[CardId, ...]) -> GameState:
    return replace(state, guard_deck=deck)


def gs_push_continuation(state: GameState, cont: Continuation) -> GameState:
    return replace(state, continuation_stack=state.continuation_stack + (cont,))


def gs_pop_continuation(state: GameState) -> tuple[GameState, Continuation | None]:
    if not state.continuation_stack:
        return state, None
    *rest, top = state.continuation_stack
    return replace(state, continuation_stack=tuple(rest)), top


def gs_set_card_state(state: GameState, card_id: CardId, cs: CardState) -> GameState:
    new_states = dict(state.card_states)
    if cs == CardState():
        new_states.pop(card_id, None)
    else:
        new_states[card_id] = cs
    return replace(state, card_states=new_states)


def gs_add_card_def(state: GameState, card_id: CardId, card_def: CardDef) -> GameState:
    new_defs = dict(state.card_defs)
    new_defs[card_id] = card_def
    return replace(state, card_defs=new_defs)


# ---------------------------------------------------------------------------
# Player-level updates
# ---------------------------------------------------------------------------

def gs_get_player(state: GameState, pid: PlayerId) -> PlayerState:
    return state.players[pid.value]


def gs_update_player(state: GameState, pid: PlayerId, ps: PlayerState) -> GameState:
    players = list(state.players)
    players[pid.value] = ps
    return replace(state, players=(players[0], players[1]))


def gs_modify_player(
    state: GameState,
    pid: PlayerId,
    fn: Callable[[PlayerState], PlayerState],
) -> GameState:
    """Apply a transformation function to one player's state."""
    return gs_update_player(state, pid, fn(gs_get_player(state, pid)))


def ps_set_hp(ps: PlayerState, hp: int) -> PlayerState:
    return replace(ps, hp=max(0, min(hp, ps.hp_cap)))


def ps_set_hp_uncapped(ps: PlayerState, hp: int) -> PlayerState:
    """Set HP without capping (used when hp_cap itself is being modified)."""
    return replace(ps, hp=max(0, hp))


def ps_set_hp_cap(ps: PlayerState, cap: int) -> PlayerState:
    return replace(ps, hp_cap=cap)


def ps_set_dead(ps: PlayerState) -> PlayerState:
    return replace(ps, is_dead=True, hp=0)


def ps_set_deck(ps: PlayerState, deck: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, deck=deck)


def ps_set_hand(ps: PlayerState, hand: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, hand=hand)


def ps_set_refresh(ps: PlayerState, pile: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, refresh_pile=pile)


def ps_set_discard(ps: PlayerState, pile: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, discard_pile=pile)


def ps_set_manipulation(ps: PlayerState, mf: ManipulationField) -> PlayerState:
    return replace(ps, manipulation_field=mf)


def ps_set_equipment(ps: PlayerState, eq: tuple[CardId | None, CardId | None]) -> PlayerState:
    return replace(ps, equipment=eq)


def ps_set_weapon_slots(ps: PlayerState, ws: tuple[WeaponSlot, ...]) -> PlayerState:
    return replace(ps, weapon_slots=ws)


def ps_set_eaten(ps: PlayerState, eaten: bool) -> PlayerState:
    return replace(ps, has_eaten_this_phase=eaten)


def ps_set_action_plays(ps: PlayerState, n: int) -> PlayerState:
    return replace(ps, action_plays_made=n)


def ps_set_action_phase_over(ps: PlayerState, over: bool) -> PlayerState:
    return replace(ps, action_phase_over=over)


def ps_set_role_card_id(ps: PlayerState, cid: CardId | None) -> PlayerState:
    return replace(ps, role_card_id=cid)


def ps_add_permanent_ability(ps: PlayerState, ability: str) -> PlayerState:
    return replace(ps, permanent_abilities=ps.permanent_abilities | {ability})


# --- Deck operations ---

def ps_draw_from_deck(ps: PlayerState, n: int = 1) -> tuple[PlayerState, tuple[CardId, ...]]:
    """
    Draw n cards from top of deck. Returns (new_state, drawn_cards).
    If deck is empty, returns fewer cards (caller must handle exhaustion).
    """
    drawn = ps.deck[:n]
    remaining = ps.deck[n:]
    return replace(ps, deck=remaining), drawn


def ps_add_to_hand(ps: PlayerState, cards: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, hand=ps.hand + cards)


def ps_remove_from_hand(ps: PlayerState, card_id: CardId) -> PlayerState:
    hand = list(ps.hand)
    hand.remove(card_id)
    return replace(ps, hand=tuple(hand))


def ps_add_to_refresh(ps: PlayerState, cards: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, refresh_pile=ps.refresh_pile + cards)


def ps_add_to_discard(ps: PlayerState, cards: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, discard_pile=ps.discard_pile + cards)


def ps_add_to_deck_bottom(ps: PlayerState, cards: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, deck=ps.deck + cards)


def ps_add_to_deck_top(ps: PlayerState, cards: tuple[CardId, ...]) -> PlayerState:
    return replace(ps, deck=cards + ps.deck)


# ---------------------------------------------------------------------------
# Action field updates
# ---------------------------------------------------------------------------

def gs_get_action_field(state: GameState) -> ActionField:
    return state.action_field


def gs_set_action_field(state: GameState, af: ActionField) -> GameState:
    return replace(state, action_field=af)


def af_get_slot(af: ActionField, pid: PlayerId, index: int) -> ActionSlot:
    slots = af.slots_for(pid)
    return slots[index]


def af_set_slot(af: ActionField, pid: PlayerId, index: int, slot: ActionSlot) -> ActionField:
    slots = list(af.slots_for(pid))
    slots[index] = slot
    return af.with_slots(pid, (slots[0], slots[1], slots[2], slots[3]))


def af_add_card_to_slot(
    af: ActionField, pid: PlayerId, index: int,
    card_id: CardId, position: str = "top",
) -> ActionField:
    """Add a card to a slot at top (index 0) or bottom (end)."""
    slot = af_get_slot(af, pid, index)
    if position == "top":
        new_cards = (card_id,) + slot.cards
    else:
        new_cards = slot.cards + (card_id,)
    return af_set_slot(af, pid, index, ActionSlot(cards=new_cards))


def af_clear_slot(af: ActionField, pid: PlayerId, index: int) -> tuple[ActionField, tuple[CardId, ...]]:
    """Remove all cards from a slot, returning the cleared cards."""
    slot = af_get_slot(af, pid, index)
    cleared = slot.cards
    return af_set_slot(af, pid, index, ActionSlot()), cleared


def af_find_empty_slots(af: ActionField, pid: PlayerId) -> list[int]:
    """Return indices of empty slots for a player, in fill order."""
    from .types import ACTION_FILL_ORDER
    result = []
    slots = af.slots_for(pid)
    for i in ACTION_FILL_ORDER:
        if slots[i].is_empty:
            result.append(i)
    return result


def af_find_nonempty_slots(af: ActionField, pid: PlayerId) -> list[int]:
    """Return indices of non-empty slots for a player."""
    result = []
    slots = af.slots_for(pid)
    for i in range(4):
        if not slots[i].is_empty:
            result.append(i)
    return result


# ---------------------------------------------------------------------------
# RNG within state
# ---------------------------------------------------------------------------

def gs_get_rng(state: GameState) -> RngState:
    return RngState(seed=state.rng_seed, counter=state.rng_counter)


def gs_with_rng_result(state: GameState, rng: RngState) -> GameState:
    """Update state with new RNG position after a random operation."""
    return replace(state, rng_seed=rng.seed, rng_counter=rng.counter)
