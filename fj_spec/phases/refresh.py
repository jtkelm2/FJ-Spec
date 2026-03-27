"""
Fools' Journey — Executable Spec
Stage 4: Refresh Phase implementation.

Refresh Phase steps (per rules):
  1. (Moon) Record HP for Moon deviation cap
  2. Shuffle each player's refresh pile into their deck
  3. Deal hand (from OTHER player's deck, up to 4)
  4. Deal action cards: (empty_slots - 1) from OWN deck, face-up in fill order
  5. Deal manipulation cards (2 from OTHER player's deck)
  6. Flip priority
  7. (Cardsharp) Rearrangement decision point
  8. Periodic effects (Empress, Bellyfiller, Corruption, Phoenix, Survivor)
  9. Transition to Manipulation Phase

Key rules:
  - Hand cards come from the OTHER player's deck (Non-Mixing Principle)
  - Manipulation cards also come from the OTHER player's deck
  - Action cards come from your OWN deck
  - Exhaustion: drawing from empty deck -> shuffle refresh -> if still empty, both lose
  - On Placement triggers fire when cards are dealt to action slots (Skeleton)
"""

from __future__ import annotations

from dataclasses import replace

from ..types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Alignment, Phase,
    PlayerState, ManipulationField,
    ActionSlot, ActionField,
    GameState, Action, ActionKind,
    PendingDecision, DecisionKind,
    RefreshContext, RefreshStep, ManipulationContext,
    GameResult, GameResultKind,
    ACTION_FILL_ORDER,
    Trigger,
)
from ..cards import has_trigger, get_handlers_for_trigger
from ..state_helpers import (
    gs_set_phase, gs_set_context, gs_set_pending, gs_set_result,
    gs_flip_priority, gs_set_action_field,
    gs_get_player, gs_update_player,
    gs_set_card_state, gs_get_rng, gs_with_rng_result,
    ps_set_hp, ps_set_dead, ps_set_deck,
    ps_set_refresh, ps_set_manipulation,
    ps_draw_from_deck, ps_add_to_hand,
    af_get_slot, af_add_card_to_slot, af_find_empty_slots,
)
from ..setup import (
    player_is_cardsharp, player_is_corruption, player_is_phoenix,
    player_is_survivor,
)
from ..rng import RngState, rng_shuffle, rng_d20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advance_refresh(state: GameState) -> GameState:
    """Advance the Refresh Phase by one step."""
    ctx = state.phase_context
    assert isinstance(ctx, RefreshContext)

    match ctx.step:
        case RefreshStep.MOON_RECORD:
            return _step_moon_record(state, ctx)
        case RefreshStep.SHUFFLE_REFRESH:
            return _step_shuffle_refresh(state, ctx)
        case RefreshStep.DEAL_ALL:
            return _step_deal_all(state, ctx)
        case RefreshStep.FLIP_PRIORITY:
            return _step_flip_priority(state, ctx)
        case RefreshStep.CARDSHARP_REARRANGE:
            return _step_cardsharp_rearrange(state, ctx)
        case RefreshStep.PERIODIC_EFFECTS:
            return _step_periodic_effects(state, ctx)
        case RefreshStep.DONE:
            return _step_done(state, ctx)
        case _:
            raise RuntimeError(f"Unknown refresh step: {ctx.step}")


def apply_refresh_action(state: GameState, action: Action) -> GameState:
    """Apply a player action during Refresh Phase (only Cardsharp rearrangement)."""
    ctx = state.phase_context
    assert isinstance(ctx, RefreshContext)
    assert ctx.step == RefreshStep.CARDSHARP_REARRANGE

    if action.kind == ActionKind.SELECT_PERMUTATION:
        return _apply_cardsharp_rearrange(state, ctx, action)

    raise RuntimeError(f"Unexpected action kind in Refresh: {action.kind}")


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def _step_moon_record(state: GameState, ctx: RefreshContext) -> GameState:
    """Record HP for players with Moon deviation cap ability."""
    red_hp = gs_get_player(state, PlayerId.RED).hp
    blue_hp = gs_get_player(state, PlayerId.BLUE).hp
    new_ctx = replace(ctx,
                      step=RefreshStep.SHUFFLE_REFRESH,
                      moon_recorded_hp=(red_hp, blue_hp))
    return gs_set_context(state, new_ctx)


def _step_shuffle_refresh(state: GameState, ctx: RefreshContext) -> GameState:
    """Shuffle each player's refresh pile into their deck."""
    rng = gs_get_rng(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        if ps.refresh_pile:
            combined = list(ps.deck) + list(ps.refresh_pile)
            rng, combined = rng_shuffle(rng, combined)
            ps = ps_set_deck(ps, tuple(combined))
            ps = ps_set_refresh(ps, ())
            state = gs_update_player(state, pid, ps)

    state = gs_with_rng_result(state, rng)
    return gs_set_context(state, replace(ctx, step=RefreshStep.DEAL_ALL))


def _step_deal_all(state: GameState, ctx: RefreshContext) -> GameState:
    """Deal hand, action cards, and manipulation cards for both players."""
    rng = gs_get_rng(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        other_pid = pid.other()

        # --- Deal hand: draw up to 4 from OTHER player's deck ---
        ps = gs_get_player(state, pid)
        cards_needed = max(0, 4 - len(ps.hand))
        if cards_needed > 0:
            state, rng, drawn = _safe_draw(state, rng, other_pid, cards_needed)
            if state.phase == Phase.GAME_OVER:
                return state
            ps = gs_get_player(state, pid)
            ps = ps_add_to_hand(ps, drawn)
            state = gs_update_player(state, pid, ps)

        # --- Deal action cards: (empty_slots - 1) from OWN deck ---
        af = state.action_field
        empty_slots = af_find_empty_slots(af, pid)
        cards_to_deal = max(0, len(empty_slots) - 1)

        if cards_to_deal > 0:
            state, rng, drawn = _safe_draw(state, rng, pid, cards_to_deal)
            if state.phase == Phase.GAME_OVER:
                return state

            af = state.action_field
            empty_slots = af_find_empty_slots(af, pid)
            for i, card_id in enumerate(drawn):
                if i < len(empty_slots):
                    slot_idx = empty_slots[i]
                    af = af_add_card_to_slot(af, pid, slot_idx, card_id, position="top")
                    state = gs_set_action_field(state, af)

                    # On Placement trigger (Skeleton draw-underneath, etc.)
                    state = gs_with_rng_result(state, rng)
                    state, rng = _handle_on_placement(state, rng, pid, slot_idx, card_id)
                    af = state.action_field

            state = gs_set_action_field(state, af)

        # --- Deal manipulation: 2 from OTHER player's deck ---
        state, rng, drawn = _safe_draw(state, rng, other_pid, 2)
        if state.phase == Phase.GAME_OVER:
            return state

        ps = gs_get_player(state, pid)
        ps = ps_set_manipulation(ps, ManipulationField(cards=drawn))
        state = gs_update_player(state, pid, ps)

    state = gs_with_rng_result(state, rng)
    return gs_set_context(state, replace(ctx, step=RefreshStep.FLIP_PRIORITY))


def _step_flip_priority(state: GameState, ctx: RefreshContext) -> GameState:
    """Flip the priority tracker."""
    state = gs_flip_priority(state)
    return gs_set_context(state, replace(ctx, step=RefreshStep.CARDSHARP_REARRANGE))


def _step_cardsharp_rearrange(state: GameState, ctx: RefreshContext) -> GameState:
    """
    Check if either player is Cardsharp and needs rearrangement.
    Process sequentially: RED first if applicable, then BLUE.
    """
    if ctx.cardsharp_player is None:
        # First entry — find first Cardsharp
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_cardsharp(state, pid):
                return _present_cardsharp_decision(state, ctx, pid)
        # No Cardsharp
        return gs_set_context(state, replace(ctx, step=RefreshStep.PERIODIC_EFFECTS))
    else:
        # Already processed one Cardsharp — check the other
        done_pid = ctx.cardsharp_player
        other_pid = done_pid.other()
        if player_is_cardsharp(state, other_pid):
            return _present_cardsharp_decision(state, ctx, other_pid)
        # All done
        return gs_set_context(state, replace(ctx,
                                             step=RefreshStep.PERIODIC_EFFECTS,
                                             cardsharp_player=None))


def _present_cardsharp_decision(
    state: GameState, ctx: RefreshContext, pid: PlayerId
) -> GameState:
    """Present the Cardsharp rearrangement decision to a player."""
    import itertools

    af = state.action_field
    slots = af.slots_for(pid)

    perms = list(itertools.permutations(range(4)))
    legal_actions = tuple(
        Action(kind=ActionKind.SELECT_PERMUTATION, permutation=tuple(p))
        for p in perms
    )

    slot_labels = ["Top Distant", "Top Hidden", "Bottom Hidden", "Bottom Distant"]
    slot_descs = []
    for i in range(4):
        slot = slots[i]
        if slot.is_empty:
            slot_descs.append(f"  {slot_labels[i]}: [empty]")
        else:
            names = [state.card_def(cid).big_name or state.card_def(cid).name
                     for cid in slot.cards]
            slot_descs.append(f"  {slot_labels[i]}: {', '.join(names)}")

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.REARRANGE_ACTION_FIELD,
        legal_actions=legal_actions,
        context_description=(
            f"Cardsharp: Rearrange your action field.\n"
            + "\n".join(slot_descs) + "\n"
            f"Provide a permutation of (0,1,2,3) where index i is moved to position perm[i]."
        ),
    )

    new_ctx = replace(ctx, cardsharp_player=pid)
    state = gs_set_context(state, new_ctx)
    return gs_set_pending(state, decision)


def _apply_cardsharp_rearrange(
    state: GameState, ctx: RefreshContext, action: Action
) -> GameState:
    """Apply a Cardsharp's rearrangement permutation."""
    assert action.permutation is not None
    assert ctx.cardsharp_player is not None
    pid = ctx.cardsharp_player
    perm = action.permutation

    af = state.action_field
    old_slots = af.slots_for(pid)

    # Apply: new_slots[perm[i]] gets old_slots[i]
    new_slot_list: list[ActionSlot] = [ActionSlot() for _ in range(4)]
    for old_idx in range(4):
        new_idx = perm[old_idx]
        new_slot_list[new_idx] = old_slots[old_idx]

    new_slots = (new_slot_list[0], new_slot_list[1],
                 new_slot_list[2], new_slot_list[3])
    af = af.with_slots(pid, new_slots)
    state = gs_set_action_field(state, af)

    # Stay in CARDSHARP_REARRANGE — advance will check for second Cardsharp
    return gs_set_context(state, replace(ctx, cardsharp_player=pid))


def _step_periodic_effects(state: GameState, ctx: RefreshContext) -> GameState:
    """
    Process end-of-Refresh-Phase periodic effects for both players.

    Order per player:
      1. Gather healing from Empress, Bellyfiller
      2. If Corruption: invert gathered healing to damage, then heal 6
      3. Bellyfiller counter management
      4. Phoenix: take 1 damage, check death (revive d20, strip other's role)
      5. Survivor: take damage = counters on role card, check death
    """
    rng = gs_get_rng(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        if ps.is_dead:
            continue

        has_corruption = player_is_corruption(state, pid)

        # --- Gather periodic healing ---
        heal_amount = 0

        if _has_equipped_card_named(state, pid, "major_3"):  # Empress
            heal_amount += 1

        bellyfiller_id = _find_equipped_card_named(state, pid, "food_9")
        if bellyfiller_id is not None:
            heal_amount += 3

        # --- Apply healing (possibly inverted by Corruption) ---
        if has_corruption:
            if heal_amount > 0:
                ps = ps_set_hp(ps, ps.hp - heal_amount)
            ps = ps_set_hp(ps, ps.hp + 6)
        else:
            if heal_amount > 0:
                ps = ps_set_hp(ps, ps.hp + heal_amount)

        state = gs_update_player(state, pid, ps)

        # --- Bellyfiller counter management ---
        if bellyfiller_id is not None:
            cs = state.card_state(bellyfiller_id)
            new_count = cs.counters + 1
            if new_count >= 3:
                state = _discard_equipment(state, pid, bellyfiller_id)
                state = gs_set_card_state(state, bellyfiller_id, CardState(counters=0))
            else:
                state = gs_set_card_state(state, bellyfiller_id,
                                          CardState(counters=new_count))

        # --- Phoenix: take 1 damage ---
        if player_is_phoenix(state, pid):
            ps = gs_get_player(state, pid)
            new_hp = ps.hp - 1
            if new_hp <= 0:
                # Phoenix revive: d20 HP
                rng, roll = rng_d20(rng)
                ps = ps_set_hp(ps, min(roll, ps.hp_cap))
                state = gs_update_player(state, pid, ps)
                # Strip other player's role abilities
                other_pid = pid.other()
                other_ps = gs_get_player(state, other_pid)
                other_ps = replace(other_ps,
                                   role_def_name=None,
                                   permanent_abilities=frozenset())
                state = gs_update_player(state, other_pid, other_ps)
            else:
                ps = ps_set_hp(ps, new_hp)
                state = gs_update_player(state, pid, ps)

        # --- Survivor: take damage = counters ---
        if player_is_survivor(state, pid):
            ps = gs_get_player(state, pid)
            role_cid = ps.role_card_id
            if role_cid is not None:
                cs = state.card_state(role_cid)
                if cs.counters > 0:
                    new_hp = ps.hp - cs.counters
                    if new_hp <= 0:
                        ps = ps_set_dead(ps)
                    else:
                        ps = ps_set_hp(ps, new_hp)
                    state = gs_update_player(state, pid, ps)

    state = gs_with_rng_result(state, rng)
    return gs_set_context(state, replace(ctx, step=RefreshStep.DONE))


def _step_done(state: GameState, ctx: RefreshContext) -> GameState:
    """Transition to Manipulation Phase, resetting per-phase tracking."""
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        ps = replace(ps,
                     has_eaten_this_phase=False,
                     action_plays_made=0,
                     devil_used_this_phase=False,
                     sun_used_this_phase=False,
                     action_phase_over=False)
        state = gs_update_player(state, pid, ps)

    return gs_set_phase(state, Phase.MANIPULATION, ManipulationContext())


# ---------------------------------------------------------------------------
# Safe drawing with exhaustion handling
# ---------------------------------------------------------------------------

def _safe_draw(
    state: GameState,
    rng: RngState,
    from_player: PlayerId,
    count: int,
) -> tuple[GameState, RngState, tuple[CardId, ...]]:
    """
    Draw `count` cards from a player's deck, handling exhaustion.

    If deck is empty, shuffles refresh pile into deck first.
    If both empty, sets game to GAME_OVER (exhaustion).

    Returns (new_state, new_rng, drawn_cards).
    """
    drawn: list[CardId] = []

    for _ in range(count):
        ps = gs_get_player(state, from_player)

        if not ps.deck:
            if not ps.refresh_pile:
                # Exhaustion!
                result = GameResult(
                    kind=GameResultKind.EXHAUSTION,
                    winner=None,
                    description=(
                        f"Exhaustion: {from_player.name}'s deck and refresh pile "
                        f"are both empty. Both players lose!"
                    ),
                )
                state = gs_set_result(state, result)
                return state, rng, tuple(drawn)

            # Shuffle refresh into deck
            combined = list(ps.refresh_pile)
            rng, combined = rng_shuffle(rng, combined)
            ps = ps_set_deck(ps, tuple(combined))
            ps = ps_set_refresh(ps, ())
            state = gs_update_player(state, from_player, ps)

        # Draw one card from top
        ps = gs_get_player(state, from_player)
        ps, card = ps_draw_from_deck(ps, 1)
        if card:
            drawn.append(card[0])
        state = gs_update_player(state, from_player, ps)

    return state, rng, tuple(drawn)


# ---------------------------------------------------------------------------
# On Placement handling
# ---------------------------------------------------------------------------

def _handle_on_placement(
    state: GameState,
    rng: RngState,
    slot_owner: PlayerId,
    slot_index: int,
    card_id: CardId,
) -> tuple[GameState, RngState]:
    """
    Handle On Placement triggers for a card placed on an action slot.

    Currently handles:
      - Skeleton (enemy_4): Draw another card underneath from owner's deck
      - Guard (guards_1-4): Draw underneath if nothing beneath

    Cascades if the drawn card also has On Placement.
    """
    cd = state.card_def(card_id)

    if not has_trigger(cd, Trigger.ON_PLACEMENT):
        return state, rng

    handlers = get_handlers_for_trigger(cd, Trigger.ON_PLACEMENT)

    for handler in handlers:
        if handler == "skeleton_draw_underneath":
            state, rng = _draw_underneath(state, rng, slot_owner, slot_index)
        elif handler == "guard_draw_underneath":
            slot = af_get_slot(state.action_field, slot_owner, slot_index)
            if len(slot.cards) <= 1:  # Only the guard itself, nothing beneath
                state, rng = _draw_underneath(state, rng, slot_owner, slot_index)

    return state, rng


def _draw_underneath(
    state: GameState,
    rng: RngState,
    slot_owner: PlayerId,
    slot_index: int,
) -> tuple[GameState, RngState]:
    """Draw a card from the slot owner's deck and place it underneath in the slot."""
    state, rng, drawn = _safe_draw(state, rng, slot_owner, 1)

    if state.phase == Phase.GAME_OVER or not drawn:
        return state, rng

    new_card = drawn[0]
    af = state.action_field
    af = af_add_card_to_slot(af, slot_owner, slot_index, new_card, position="bottom")
    state = gs_set_action_field(state, af)

    # Cascade: drawn card might also have On Placement
    state, rng = _handle_on_placement(state, rng, slot_owner, slot_index, new_card)

    return state, rng


# ---------------------------------------------------------------------------
# Equipment helpers
# ---------------------------------------------------------------------------

def _has_equipped_card_named(state: GameState, pid: PlayerId, card_name: str) -> bool:
    """Check if a player has a card with the given name equipped."""
    ps = gs_get_player(state, pid)
    for eq_id in ps.equipment:
        if eq_id is not None and state.card_def(eq_id).name == card_name:
            return True
    return False


def _find_equipped_card_named(
    state: GameState, pid: PlayerId, card_name: str
) -> CardId | None:
    """Find the CardId of an equipped card with the given name, or None."""
    ps = gs_get_player(state, pid)
    for eq_id in ps.equipment:
        if eq_id is not None and state.card_def(eq_id).name == card_name:
            return eq_id
    return None


def _discard_equipment(state: GameState, pid: PlayerId, card_id: CardId) -> GameState:
    """Remove a card from a player's equipment and add to their discard pile."""
    ps = gs_get_player(state, pid)
    equipment = list(ps.equipment)
    for i, eq in enumerate(equipment):
        if eq == card_id:
            equipment[i] = None
            break
    ps = replace(ps,
                 equipment=(equipment[0], equipment[1]),
                 discard_pile=ps.discard_pile + (card_id,))
    return gs_update_player(state, pid, ps)
