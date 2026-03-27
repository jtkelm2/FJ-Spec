"""
Fools' Journey — Executable Spec
Stage 5: Manipulation Phase implementation.

Manipulation Phase flow:
  1. Each player chooses MANIPULATE or DUMP (priority first)
  2. Manipulate players swap cards between manipulation field and hand
  3. Dump players choose discard/refresh for each non-Elusive hand card
  4. Manipulate players optionally force (discard equipment to control dealing)
  5. Automated dealing:
     a. Draw a third card (face-down) from other's deck into manipulation
     b. If forcing: player chooses which card to send; else shuffle & pick random
     c. Deal chosen card to other player's open action slot
     d. Refresh remaining manipulation cards to other's refresh pile
     e. Refresh Elusive cards from hand to other's refresh pile
  6. On Placement triggers for dealt action cards
  7. Transition to Action Phase

Key rules:
  - Manipulation field and hand cards are from the OTHER player's deck (Non-Mixing)
  - Refreshing manipulation/hand cards sends them to the OTHER player's refresh pile
  - Discarding hand cards (dump) sends them to the OTHER player's discard pile
  - Elusive cards in hand cannot be discarded during dump
  - Forcing costs discarding one of your own equipment cards
"""

from __future__ import annotations

from dataclasses import replace

from ..types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Phase,
    PlayerState, ManipulationField,
    ActionSlot, ActionField,
    GameState, Action, ActionKind,
    PendingDecision, DecisionKind,
    ManipulationContext, ManipStep, ManipChoice,
    ActionContext,
    Trigger,
    SwapPair, DumpFateChoice, DumpFate,
)
from ..cards import has_trigger
from ..state_helpers import (
    gs_set_phase, gs_set_context, gs_set_pending,
    gs_set_action_field,
    gs_get_player, gs_update_player,
    gs_get_rng, gs_with_rng_result,
    ps_set_hand, ps_set_manipulation,
    ps_set_equipment,
    ps_add_to_hand, ps_remove_from_hand,
    af_find_empty_slots, af_add_card_to_slot,
)
from ..rng import RngState, rng_shuffle
from ..phases.refresh import _safe_draw, _handle_on_placement


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advance_manipulation(state: GameState) -> GameState:
    """Advance the Manipulation Phase by one step."""
    ctx = state.phase_context
    assert isinstance(ctx, ManipulationContext)

    match ctx.step:
        case ManipStep.CHOOSE:
            return _step_choose(state, ctx)
        case ManipStep.SWAP_OR_DUMP:
            return _step_swap_or_dump(state, ctx)
        case ManipStep.FORCE_OFFER:
            return _step_force_offer(state, ctx)
        case ManipStep.DEALING:
            return _step_dealing(state, ctx)
        case ManipStep.FORCE_CARD_CHOOSE:
            return _step_force_card_choose(state, ctx)
        case ManipStep.DONE:
            return _step_done(state, ctx)
        case _:
            raise RuntimeError(f"Unknown manipulation step: {ctx.step}")


def apply_manipulation_action(state: GameState, action: Action) -> GameState:
    """Apply a player action during Manipulation Phase."""
    ctx = state.phase_context
    assert isinstance(ctx, ManipulationContext)

    match ctx.step:
        case ManipStep.CHOOSE:
            return _apply_choose(state, ctx, action)
        case ManipStep.SWAP_OR_DUMP:
            return _apply_swap_or_dump(state, ctx, action)
        case ManipStep.FORCE_OFFER:
            return _apply_force_offer(state, ctx, action)
        case ManipStep.FORCE_CARD_CHOOSE:
            return _apply_force_card_choose(state, ctx, action)
        case _:
            raise RuntimeError(f"Cannot apply action in manipulation step: {ctx.step}")


# ---------------------------------------------------------------------------
# CHOOSE step: each player picks manipulate or dump
# ---------------------------------------------------------------------------

def _step_choose(state: GameState, ctx: ManipulationContext) -> GameState:
    """Present manipulate/dump choice to the next player who hasn't chosen."""
    # Priority player chooses first
    for pid in _player_order(state):
        choice = _get_choice(ctx, pid)
        if choice is None:
            return _present_choose_decision(state, ctx, pid)

    # Both have chosen — move to SWAP_OR_DUMP, starting with priority player
    first = _player_order(state)[0]
    return gs_set_context(state, replace(ctx,
                                         step=ManipStep.SWAP_OR_DUMP,
                                         current_player=first))


def _present_choose_decision(
    state: GameState, ctx: ManipulationContext, pid: PlayerId
) -> GameState:
    """Present MANIPULATE or DUMP choice."""
    actions = (
        Action(kind=ActionKind.SELECT_INDEX, index=0),  # Manipulate
        Action(kind=ActionKind.SELECT_INDEX, index=1),  # Dump
    )
    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_MANIPULATE_OR_DUMP,
        legal_actions=actions,
        context_description="Choose: [0] Manipulate (swap cards) or [1] Dump (discard/refresh hand)",
    )
    new_ctx = replace(ctx, current_player=pid)
    state = gs_set_context(state, new_ctx)
    return gs_set_pending(state, decision)


def _apply_choose(state: GameState, ctx: ManipulationContext, action: Action) -> GameState:
    """Record a player's manipulate/dump choice."""
    assert action.kind == ActionKind.SELECT_INDEX
    assert ctx.current_player is not None
    pid = ctx.current_player
    choice = ManipChoice.MANIPULATE if action.index == 0 else ManipChoice.DUMP
    ctx = _set_choice(ctx, pid, choice)
    return gs_set_context(state, replace(ctx, current_player=None))


# ---------------------------------------------------------------------------
# SWAP_OR_DUMP step: each player performs their action
# ---------------------------------------------------------------------------

def _step_swap_or_dump(state: GameState, ctx: ManipulationContext) -> GameState:
    """Process current player's swaps (manipulate) or dump fates."""
    assert ctx.current_player is not None
    pid = ctx.current_player

    if _is_done(ctx, pid):
        # This player is done — move to next player or next step
        return _advance_to_next_player_or_step(state, ctx, pid, ManipStep.FORCE_OFFER)

    choice = _get_choice(ctx, pid)
    assert choice is not None

    if choice == ManipChoice.MANIPULATE:
        return _present_swap_decision(state, ctx, pid)
    else:
        return _present_dump_decision(state, ctx, pid)


def _present_swap_decision(
    state: GameState, ctx: ManipulationContext, pid: PlayerId
) -> GameState:
    """Present swap options: any (manip_card, hand_card) pair, or DONE."""
    ps = gs_get_player(state, pid)
    manip_cards = ps.manipulation_field.cards
    hand_cards = ps.hand

    actions: list[Action] = []

    # Generate all valid swap pairs
    for mc in manip_cards:
        for hc in hand_cards:
            actions.append(Action(
                kind=ActionKind.SELECT_SWAP,
                swap=SwapPair(manip_card=mc, hand_card=hc),
            ))

    # Always allow finishing
    actions.append(Action(kind=ActionKind.DECLINE))

    # Build description
    manip_desc = ", ".join(
        state.card_def(c).big_name or state.card_def(c).name for c in manip_cards
    ) if manip_cards else "(empty)"
    hand_desc = ", ".join(
        state.card_def(c).big_name or state.card_def(c).name for c in hand_cards
    ) if hand_cards else "(empty)"

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_SWAP,
        legal_actions=tuple(actions),
        context_description=(
            f"Swap a manipulation card with a hand card, or DECLINE to finish.\n"
            f"  Manipulation: {manip_desc}\n"
            f"  Hand: {hand_desc}"
        ),
    )
    return gs_set_pending(state, decision)


def _present_dump_decision(
    state: GameState, ctx: ManipulationContext, pid: PlayerId
) -> GameState:
    """Present dump fates: for each non-Elusive hand card, discard or refresh."""
    ps = gs_get_player(state, pid)

    if not ps.hand:
        # No hand cards — auto-complete dump
        ctx = _set_done(ctx, pid, True)
        return gs_set_context(state, ctx)

    # Separate Elusive and non-Elusive cards
    non_elusive = []
    for cid in ps.hand:
        cd = state.card_def(cid)
        if not cd.is_elusive:
            non_elusive.append(cid)

    if not non_elusive:
        # All hand cards are Elusive — nothing to dump, auto-complete
        ctx = _set_done(ctx, pid, True)
        return gs_set_context(state, ctx)

    # Generate all combinations of discard/refresh for non-Elusive cards
    # Each non-Elusive card independently gets DISCARD or REFRESH
    import itertools
    n = len(non_elusive)
    combos = list(itertools.product([DumpFate.DISCARD, DumpFate.REFRESH], repeat=n))

    actions: list[Action] = []
    for combo in combos:
        fates = tuple(
            DumpFateChoice(card=non_elusive[i], fate=combo[i])
            for i in range(n)
        )
        actions.append(Action(kind=ActionKind.SELECT_DUMP_FATES, dump_fates=fates))

    # Build description
    card_descs = []
    for cid in ps.hand:
        cd = state.card_def(cid)
        name = cd.big_name or cd.name
        if cd.is_elusive:
            card_descs.append(f"  {name} [Elusive — will be refreshed]")
        else:
            card_descs.append(f"  {name} — choose discard or refresh")

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_DUMP_FATE,
        legal_actions=tuple(actions),
        context_description=(
            f"Dump: Choose discard or refresh for each non-Elusive hand card.\n"
            + "\n".join(card_descs)
        ),
    )
    return gs_set_pending(state, decision)


def _apply_swap_or_dump(
    state: GameState, ctx: ManipulationContext, action: Action
) -> GameState:
    """Apply a swap or dump action."""
    assert ctx.current_player is not None
    pid = ctx.current_player

    choice = _get_choice(ctx, pid)
    assert choice is not None

    if choice == ManipChoice.MANIPULATE:
        return _apply_swap(state, ctx, pid, action)
    else:
        return _apply_dump(state, ctx, pid, action)


def _apply_swap(
    state: GameState, ctx: ManipulationContext, pid: PlayerId, action: Action
) -> GameState:
    """Apply a card swap or finish swapping."""
    if action.kind == ActionKind.DECLINE:
        # Done swapping
        ctx = _set_done(ctx, pid, True)
        return gs_set_context(state, ctx)

    assert action.kind == ActionKind.SELECT_SWAP
    assert action.swap is not None
    swap = action.swap

    ps = gs_get_player(state, pid)

    # Remove cards from their current locations
    manip_list = list(ps.manipulation_field.cards)
    hand_list = list(ps.hand)

    manip_list.remove(swap.manip_card)
    hand_list.remove(swap.hand_card)

    # Swap them
    manip_list.append(swap.hand_card)
    hand_list.append(swap.manip_card)

    ps = ps_set_manipulation(ps, ManipulationField(cards=tuple(manip_list)))
    ps = ps_set_hand(ps, tuple(hand_list))
    state = gs_update_player(state, pid, ps)

    # Stay in SWAP_OR_DUMP — will present another swap decision
    return state


def _apply_dump(
    state: GameState, ctx: ManipulationContext, pid: PlayerId, action: Action
) -> GameState:
    """Apply dump fates to hand cards."""
    assert action.kind == ActionKind.SELECT_DUMP_FATES
    assert action.dump_fates is not None

    ps = gs_get_player(state, pid)
    other_pid = pid.other()
    other_ps = gs_get_player(state, other_pid)

    for fate_choice in action.dump_fates:
        cid = fate_choice.card
        if fate_choice.fate == DumpFate.DISCARD:
            # Discard to OTHER player's discard (Non-Mixing: cards are from other's deck)
            other_ps = replace(other_ps,
                               discard_pile=other_ps.discard_pile + (cid,))
        else:
            # Refresh to OTHER player's refresh pile
            other_ps = replace(other_ps,
                               refresh_pile=other_ps.refresh_pile + (cid,))

    # Remove dumped cards from hand (keep Elusive cards)
    dumped_ids = {fc.card for fc in action.dump_fates}
    remaining_hand = tuple(c for c in ps.hand if c not in dumped_ids)
    ps = ps_set_hand(ps, remaining_hand)

    state = gs_update_player(state, pid, ps)
    state = gs_update_player(state, other_pid, other_ps)

    ctx = _set_done(ctx, pid, True)
    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# FORCE_OFFER step: manipulate players optionally force
# ---------------------------------------------------------------------------

def _step_force_offer(state: GameState, ctx: ManipulationContext) -> GameState:
    """Offer force option to current manipulate player."""
    assert ctx.current_player is not None
    pid = ctx.current_player

    choice = _get_choice(ctx, pid)

    # Only manipulate players can force
    if choice != ManipChoice.MANIPULATE:
        return _advance_to_next_player_or_step(state, ctx, pid, ManipStep.DEALING)

    # Check if player has any equipment to sacrifice
    ps = gs_get_player(state, pid)
    available_equipment = [
        eq for eq in ps.equipment if eq is not None
    ]

    if not available_equipment:
        # No equipment to sacrifice — skip force
        return _advance_to_next_player_or_step(state, ctx, pid, ManipStep.DEALING)

    # Present force option
    actions: list[Action] = []
    for eq_id in available_equipment:
        actions.append(Action(kind=ActionKind.SELECT_CARD, card_id=eq_id))
    actions.append(Action(kind=ActionKind.DECLINE))

    eq_descs = [
        state.card_def(eq).big_name or state.card_def(eq).name
        for eq in available_equipment
    ]
    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_FORCE,
        legal_actions=tuple(actions),
        context_description=(
            f"Force: Discard an equipment to choose which card to send.\n"
            f"  Available equipment: {', '.join(eq_descs)}\n"
            f"  Or DECLINE to deal randomly."
        ),
    )
    return gs_set_pending(state, decision)


def _apply_force_offer(
    state: GameState, ctx: ManipulationContext, action: Action
) -> GameState:
    """Record force decision."""
    assert ctx.current_player is not None
    pid = ctx.current_player

    if action.kind == ActionKind.DECLINE:
        # No forcing
        return _advance_to_next_player_or_step(state, ctx, pid, ManipStep.DEALING)

    assert action.kind == ActionKind.SELECT_CARD
    assert action.card_id is not None

    # Record which equipment is being sacrificed
    ctx = _set_forcing(ctx, pid, action.card_id)

    # Discard the equipment
    ps = gs_get_player(state, pid)
    equipment = list(ps.equipment)
    for i, eq in enumerate(equipment):
        if eq == action.card_id:
            equipment[i] = None
            break
    ps = ps_set_equipment(ps, (equipment[0], equipment[1]))
    ps = replace(ps, discard_pile=ps.discard_pile + (action.card_id,))

    # If this was the role card, clear role_card_id
    if ps.role_card_id == action.card_id:
        ps = replace(ps, role_card_id=None)

    state = gs_update_player(state, pid, ps)
    state = gs_set_context(state, ctx)

    return _advance_to_next_player_or_step(state, ctx, pid, ManipStep.DEALING)


# ---------------------------------------------------------------------------
# DEALING step: automated dealing for both players
# ---------------------------------------------------------------------------

def _step_dealing(state: GameState, ctx: ManipulationContext) -> GameState:
    """
    Process dealing for one player at a time.
    
    For each player:
      1. Draw a third card from other's deck into manipulation field
      2. If forcing: present choice of which card to send
      3. If not: shuffle and pick one randomly
      4. Deal chosen card to other's action slot
      5. Refresh remaining to other's refresh pile
    """
    # Determine which player to process
    if ctx.dealing_player is None:
        # Start with priority player
        first = _player_order(state)[0]
        ctx = replace(ctx, dealing_player=first)
        state = gs_set_context(state, ctx)

    pid = ctx.dealing_player
    assert pid is not None
    rng = gs_get_rng(state)

    # Draw third card from other's deck
    other_pid = pid.other()
    state, rng, drawn = _safe_draw(state, rng, other_pid, 1)
    if state.phase == Phase.GAME_OVER:
        return state

    # Add to manipulation field
    ps = gs_get_player(state, pid)
    new_manip = ps.manipulation_field.cards + drawn
    ps = ps_set_manipulation(ps, ManipulationField(cards=new_manip))
    state = gs_update_player(state, pid, ps)
    state = gs_with_rng_result(state, rng)

    # Check if this player is forcing
    forcing_card = _get_forcing(ctx, pid)
    if forcing_card is not None:
        # Player gets to choose which card to send
        return gs_set_context(state, replace(ctx, step=ManipStep.FORCE_CARD_CHOOSE))
    else:
        # Shuffle and pick randomly
        return _deal_randomly(state, ctx, pid)


def _deal_randomly(
    state: GameState, ctx: ManipulationContext, pid: PlayerId
) -> GameState:
    """Shuffle manipulation cards and deal one randomly to other's action slot."""
    rng = gs_get_rng(state)
    ps = gs_get_player(state, pid)
    other_pid = pid.other()

    manip_cards = list(ps.manipulation_field.cards)
    rng, manip_cards = rng_shuffle(rng, manip_cards)
    state = gs_with_rng_result(state, rng)

    # Deal first card to other's action slot
    card_to_send = manip_cards[0]
    remaining = manip_cards[1:]

    state = _deal_card_to_action(state, card_to_send, other_pid)
    if state.phase == Phase.GAME_OVER:
        return state

    # Refresh remaining to other's refresh pile
    state = _refresh_cards_to_owner(state, pid, tuple(remaining))

    # Clear manipulation field
    ps = gs_get_player(state, pid)
    ps = ps_set_manipulation(ps, ManipulationField())
    state = gs_update_player(state, pid, ps)

    # Move to next player's dealing or finish
    return _advance_dealing(state, ctx, pid)


# ---------------------------------------------------------------------------
# FORCE_CARD_CHOOSE step
# ---------------------------------------------------------------------------

def _step_force_card_choose(state: GameState, ctx: ManipulationContext) -> GameState:
    """Present the forcing player with their manipulation cards to choose from."""
    assert ctx.dealing_player is not None
    pid = ctx.dealing_player
    ps = gs_get_player(state, pid)
    manip_cards = ps.manipulation_field.cards

    actions = tuple(
        Action(kind=ActionKind.SELECT_CARD, card_id=cid)
        for cid in manip_cards
    )

    card_descs = [
        state.card_def(c).big_name or state.card_def(c).name
        for c in manip_cards
    ]
    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_FORCE_CARD,
        legal_actions=actions,
        context_description=(
            f"Force: Choose which card to send to the other player.\n"
            f"  Cards: {', '.join(card_descs)}"
        ),
    )
    return gs_set_pending(state, decision)


def _apply_force_card_choose(
    state: GameState, ctx: ManipulationContext, action: Action
) -> GameState:
    """Apply the forcing player's choice of card to send."""
    assert action.kind == ActionKind.SELECT_CARD
    assert action.card_id is not None
    assert ctx.dealing_player is not None
    pid = ctx.dealing_player
    other_pid = pid.other()

    ps = gs_get_player(state, pid)
    manip_cards = list(ps.manipulation_field.cards)

    chosen = action.card_id
    manip_cards.remove(chosen)

    # Deal chosen card to other's action slot
    state = _deal_card_to_action(state, chosen, other_pid)
    if state.phase == Phase.GAME_OVER:
        return state

    # Refresh remaining to other's refresh pile
    state = _refresh_cards_to_owner(state, pid, tuple(manip_cards))

    # Clear manipulation field
    ps = gs_get_player(state, pid)
    ps = ps_set_manipulation(ps, ManipulationField())
    state = gs_update_player(state, pid, ps)

    # Move to next player's dealing or finish
    ctx = replace(ctx, step=ManipStep.DEALING)
    state = gs_set_context(state, ctx)
    return _advance_dealing(state, ctx, pid)


# ---------------------------------------------------------------------------
# Dealing helpers
# ---------------------------------------------------------------------------

def _deal_card_to_action(
    state: GameState, card_id: CardId, target_player: PlayerId
) -> GameState:
    """Deal a single card to the target player's first open action slot."""
    af = state.action_field
    empty_slots = af_find_empty_slots(af, target_player)

    if not empty_slots:
        # No open slots — refresh the card instead
        # (shouldn't normally happen, but handle gracefully)
        target_ps = gs_get_player(state, target_player)
        target_ps = replace(target_ps,
                            refresh_pile=target_ps.refresh_pile + (card_id,))
        return gs_update_player(state, target_player, target_ps)

    slot_idx = empty_slots[0]
    af = af_add_card_to_slot(af, target_player, slot_idx, card_id, position="top")
    state = gs_set_action_field(state, af)

    # Handle On Placement trigger
    rng = gs_get_rng(state)
    state, rng = _handle_on_placement(state, rng, target_player, slot_idx, card_id)
    state = gs_with_rng_result(state, rng)

    return state


def _refresh_cards_to_owner(
    state: GameState, holder: PlayerId, cards: tuple[CardId, ...]
) -> GameState:
    """
    Refresh manipulation/hand cards back to their owner's refresh pile.
    
    Per Non-Mixing Principle: manipulation and hand cards are from the
    OTHER player's deck, so they refresh to the OTHER player's refresh pile.
    """
    if not cards:
        return state
    other_pid = holder.other()
    other_ps = gs_get_player(state, other_pid)
    other_ps = replace(other_ps,
                       refresh_pile=other_ps.refresh_pile + cards)
    return gs_update_player(state, other_pid, other_ps)


def _advance_dealing(
    state: GameState, ctx: ManipulationContext, done_pid: PlayerId
) -> GameState:
    """Move to next player's dealing or to Elusive cleanup."""
    other_pid = done_pid.other()

    # Check if the other player's dealing has been processed
    order = _player_order(state)
    if done_pid == order[0]:
        # First player done, process second
        ctx = replace(ctx, dealing_player=order[1], step=ManipStep.DEALING)
        return gs_set_context(state, ctx)
    else:
        # Both done — do Elusive cleanup and finish
        state = _elusive_hand_cleanup(state)
        ctx = replace(ctx, step=ManipStep.DONE)
        return gs_set_context(state, ctx)


def _elusive_hand_cleanup(state: GameState) -> GameState:
    """
    Refresh Elusive cards from each player's hand.
    Per Non-Mixing: hand cards are from other's deck → other's refresh pile.
    """
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        other_pid = pid.other()
        other_ps = gs_get_player(state, other_pid)

        elusive_ids: list[CardId] = []
        remaining: list[CardId] = []

        for cid in ps.hand:
            cd = state.card_def(cid)
            if cd.is_elusive:
                elusive_ids.append(cid)
            else:
                remaining.append(cid)

        if elusive_ids:
            ps = ps_set_hand(ps, tuple(remaining))
            other_ps = replace(other_ps,
                               refresh_pile=other_ps.refresh_pile + tuple(elusive_ids))
            state = gs_update_player(state, pid, ps)
            state = gs_update_player(state, other_pid, other_ps)

    return state


# ---------------------------------------------------------------------------
# DONE step
# ---------------------------------------------------------------------------

def _step_done(state: GameState, ctx: ManipulationContext) -> GameState:
    """Transition to Action Phase."""
    return gs_set_phase(state, Phase.ACTION, ActionContext(
        current_turn=state.priority,
    ))


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _player_order(state: GameState) -> list[PlayerId]:
    """Return players in priority order."""
    p = state.priority
    return [p, p.other()]


def _get_choice(ctx: ManipulationContext, pid: PlayerId) -> ManipChoice | None:
    return ctx.red_choice if pid is PlayerId.RED else ctx.blue_choice


def _set_choice(ctx: ManipulationContext, pid: PlayerId, choice: ManipChoice) -> ManipulationContext:
    if pid is PlayerId.RED:
        return replace(ctx, red_choice=choice)
    return replace(ctx, blue_choice=choice)


def _is_done(ctx: ManipulationContext, pid: PlayerId) -> bool:
    return ctx.red_done if pid is PlayerId.RED else ctx.blue_done


def _set_done(ctx: ManipulationContext, pid: PlayerId, done: bool) -> ManipulationContext:
    if pid is PlayerId.RED:
        return replace(ctx, red_done=done)
    return replace(ctx, blue_done=done)


def _get_forcing(ctx: ManipulationContext, pid: PlayerId) -> CardId | None:
    return ctx.red_forcing if pid is PlayerId.RED else ctx.blue_forcing


def _set_forcing(ctx: ManipulationContext, pid: PlayerId, card_id: CardId) -> ManipulationContext:
    if pid is PlayerId.RED:
        return replace(ctx, red_forcing=card_id)
    return replace(ctx, blue_forcing=card_id)


def _advance_to_next_player_or_step(
    state: GameState, ctx: ManipulationContext,
    done_pid: PlayerId, next_step: ManipStep
) -> GameState:
    """
    After a player finishes a step, advance to the other player
    (same step) or to the next step if both are done.
    """
    other_pid = done_pid.other()
    order = _player_order(state)

    if done_pid == order[0]:
        # First player done — process second player at same step
        ctx = replace(ctx, current_player=order[1], step=ctx.step)
        return gs_set_context(state, ctx)
    else:
        # Both done — advance to next step
        first = order[0]
        ctx = replace(ctx, current_player=first, step=next_step)

        # For DEALING step, reset dealing_player
        if next_step == ManipStep.DEALING:
            ctx = replace(ctx, dealing_player=None, current_player=None)

        return gs_set_context(state, ctx)