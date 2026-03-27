"""
Fools' Journey — Executable Spec
Stage 6: Action Phase core implementation.

Action Phase flow:
  1. Players alternate, starting with priority player, 3 plays each.
  2. Before first play: optional Last Resort (Run, Call Guards, Mutiny).
  3. Each play: choose a slot → optional consent → resolve slot (card by card).
  4. Between cards in a slot: voluntary discard window.
  5. After all plays: Elusive cleanup, transition to Refresh Phase.

This stage implements:
  - Full action phase loop with alternation
  - Slot legality (First constraint, empty slots, Cardsharp consent waiver)
  - Consent negotiation for opponent's slots
  - Default type-based card resolution (food/weapon/equipment/event/enemy)
  - Combat with stub damage (full pipeline in Stage 7)
  - Voluntary discard windows
  - Running and Call Guards Last Resorts
  - Elusive cleanup
  - Fallback to opponent's field or deck when own field is exhausted

Card-specific effect handlers are stubs — Stage 8 will implement them.
"""

from __future__ import annotations

from dataclasses import replace

from ..types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Alignment, Phase, Parity,
    PlayerState, WeaponSlot, ManipulationField,
    ActionSlot, ActionField,
    GameState, Action, ActionKind, AttackMode,
    PendingDecision, DecisionKind,
    ActionContext, ActionStep,
    ResolutionContext, RunningContext, ConsentRequest,
    RefreshContext,
    SlotRef, SlotKind,
    Trigger,
    DISTANT_INDICES, HIDDEN_INDICES,
    ACTION_FILL_ORDER,
)
from ..cards import (
    has_trigger, get_handlers_for_trigger,
    is_enemy_like, is_food, is_weapon, is_equipment, is_event,
    CardType as CT,
)
from ..state_helpers import (
    gs_set_phase, gs_set_context, gs_set_pending, gs_set_result,
    gs_set_action_field, gs_increment_turn,
    gs_get_player, gs_update_player,
    gs_set_card_state, gs_get_rng, gs_with_rng_result,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_hand,
    ps_set_refresh, ps_set_discard, ps_set_manipulation,
    ps_set_equipment, ps_set_weapon_slots, ps_set_eaten,
    ps_set_action_plays, ps_set_action_phase_over,
    ps_set_role_card_id,
    ps_draw_from_deck, ps_add_to_hand, ps_remove_from_hand,
    ps_add_to_refresh, ps_add_to_discard,
    ps_add_permanent_ability,
    af_get_slot, af_set_slot, af_add_card_to_slot,
    af_clear_slot, af_find_empty_slots, af_find_nonempty_slots,
)
from ..setup import (
    player_can_call_guards, player_can_mutiny,
    player_is_cardsharp, player_is_food_fighter,
)
from ..rng import RngState, rng_shuffle, rng_d20
from ..phases.refresh import _safe_draw, _handle_on_placement
from ..types import classify_slot, GameResult, GameResultKind


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advance_action(state: GameState) -> GameState:
    """Advance the Action Phase by one step."""
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)

    match ctx.step:
        case ActionStep.LAST_RESORT_OFFER:
            return _step_last_resort_offer(state, ctx)
        case ActionStep.CHOOSE_SLOT:
            return _step_choose_slot(state, ctx)
        case ActionStep.CONSENT_CHECK:
            return _step_consent_check(state, ctx)
        case ActionStep.RESOLVING_SLOT:
            return _step_resolving_slot(state, ctx)
        case ActionStep.VOLUNTARY_DISCARD:
            return _step_voluntary_discard(state, ctx)
        case ActionStep.RESOLVE_FROM_DECK:
            return _step_resolve_from_deck(state, ctx)
        case ActionStep.NEXT_TURN:
            return _step_next_turn(state, ctx)
        case ActionStep.ELUSIVE_CLEANUP:
            return _step_elusive_cleanup(state, ctx)
        case ActionStep.RUNNING_PREP:
            return _step_running_prep(state, ctx)
        case ActionStep.RUNNING_DECIDE:
            return _step_running_decide(state, ctx)
        case ActionStep.RUNNING_DEAL:
            return _step_running_deal(state, ctx)
        case ActionStep.GUARDS:
            return _step_guards(state, ctx)
        case ActionStep.DONE:
            return _step_done(state, ctx)
        case _:
            raise RuntimeError(f"Unknown action step: {ctx.step}")


def apply_action_action(state: GameState, action: Action) -> GameState:
    """Apply a player action during Action Phase."""
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)

    match ctx.step:
        case ActionStep.LAST_RESORT_OFFER:
            return _apply_last_resort(state, ctx, action)
        case ActionStep.CHOOSE_SLOT:
            return _apply_choose_slot(state, ctx, action)
        case ActionStep.CONSENT_CHECK:
            return _apply_consent(state, ctx, action)
        case ActionStep.RESOLVING_SLOT:
            return _apply_resolving(state, ctx, action)
        case ActionStep.VOLUNTARY_DISCARD:
            return _apply_voluntary_discard(state, ctx, action)
        case ActionStep.RUNNING_DECIDE:
            return _apply_running_decide(state, ctx, action)
        case _:
            raise RuntimeError(f"Cannot apply action in step: {ctx.step}")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _plays_remaining(ctx: ActionContext, pid: PlayerId) -> int:
    return ctx.plays_remaining_red if pid is PlayerId.RED else ctx.plays_remaining_blue


def _set_plays_remaining(ctx: ActionContext, pid: PlayerId, n: int) -> ActionContext:
    if pid is PlayerId.RED:
        return replace(ctx, plays_remaining_red=n)
    return replace(ctx, plays_remaining_blue=n)


def _last_resort_offered(ctx: ActionContext, pid: PlayerId) -> bool:
    return ctx.last_resort_offered_red if pid is PlayerId.RED else ctx.last_resort_offered_blue


def _set_last_resort_offered(ctx: ActionContext, pid: PlayerId) -> ActionContext:
    if pid is PlayerId.RED:
        return replace(ctx, last_resort_offered_red=True)
    return replace(ctx, last_resort_offered_blue=True)


def _set_last_resort_used(ctx: ActionContext, pid: PlayerId) -> ActionContext:
    if pid is PlayerId.RED:
        return replace(ctx, last_resort_used_red=True)
    return replace(ctx, last_resort_used_blue=True)


def _is_first_play(ctx: ActionContext, pid: PlayerId) -> bool:
    return _plays_remaining(ctx, pid) == 3


def _consume_play(ctx: ActionContext, pid: PlayerId) -> ActionContext:
    n = _plays_remaining(ctx, pid)
    return _set_plays_remaining(ctx, pid, max(0, n - 1))


def _both_done(ctx: ActionContext) -> bool:
    red_done = ctx.plays_remaining_red <= 0 or gs_get_player_action_over(ctx, PlayerId.RED)
    blue_done = ctx.plays_remaining_blue <= 0 or gs_get_player_action_over(ctx, PlayerId.BLUE)
    return red_done and blue_done


def gs_get_player_action_over(ctx: ActionContext, pid: PlayerId) -> bool:
    """Check if phase is over for this player (set by Vorpal Blade, Star, etc.)."""
    # This checks the context, not player state — we'll check player state too
    return False  # Actual check happens via PlayerState.action_phase_over


def _player_is_done(state: GameState, ctx: ActionContext, pid: PlayerId) -> bool:
    ps = gs_get_player(state, pid)
    return (_plays_remaining(ctx, pid) <= 0
            or ps.action_phase_over
            or ps.is_dead)


# ---------------------------------------------------------------------------
# LAST_RESORT_OFFER
# ---------------------------------------------------------------------------

def _step_last_resort_offer(state: GameState, ctx: ActionContext) -> GameState:
    """Offer Last Resort before the current player's first action play."""
    pid = ctx.current_turn

    if not _is_first_play(ctx, pid) or _last_resort_offered(ctx, pid):
        return gs_set_context(state, replace(ctx, step=ActionStep.CHOOSE_SLOT))

    ctx = _set_last_resort_offered(ctx, pid)
    state = gs_set_context(state, ctx)

    # Build available last resort options
    actions: list[Action] = []
    descriptions: list[str] = []

    # Running: available unless guards prevent it
    if _can_run(state, pid):
        actions.append(Action(kind=ActionKind.SELECT_INDEX, index=0))
        descriptions.append("[0] Run")

    # Call the Guards
    if player_can_call_guards(state, pid):
        actions.append(Action(kind=ActionKind.SELECT_INDEX, index=1))
        descriptions.append("[1] Call the Guards")

    # Mutiny
    if player_can_mutiny(state, pid):
        actions.append(Action(kind=ActionKind.SELECT_INDEX, index=2))
        descriptions.append("[2] Mutiny")

    # Always can decline
    actions.append(Action(kind=ActionKind.DECLINE))
    descriptions.append("[DECLINE] No Last Resort")

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_LAST_RESORT,
        legal_actions=tuple(actions),
        context_description="Choose a Last Resort or decline.\n" + "\n".join(descriptions),
    )
    return gs_set_pending(state, decision)


def _can_run(state: GameState, pid: PlayerId) -> bool:
    """Check if a player can run (no guards on their action field)."""
    af = state.action_field
    for i in range(4):
        slot = af_get_slot(af, pid, i)
        for cid in slot.cards:
            cd = state.card_def(cid)
            if has_trigger(cd, Trigger.PREVENT_RUN):
                return False
    return True


def _apply_last_resort(state: GameState, ctx: ActionContext, action: Action) -> GameState:
    pid = ctx.current_turn

    if action.kind == ActionKind.DECLINE:
        return gs_set_context(state, replace(ctx, step=ActionStep.CHOOSE_SLOT))

    assert action.kind == ActionKind.SELECT_INDEX
    idx = action.index

    if idx == 0:  # Run
        ctx = _set_last_resort_used(ctx, pid)
        ctx = replace(ctx, step=ActionStep.RUNNING_PREP,
                      running=RunningContext(runner=pid))
        return gs_set_context(state, ctx)

    elif idx == 1:  # Call the Guards
        ctx = _set_last_resort_used(ctx, pid)
        ctx = replace(ctx, step=ActionStep.GUARDS)
        return gs_set_context(state, ctx)

    elif idx == 2:  # Mutiny — stub for now (Stage 7/8)
        ctx = _set_last_resort_used(ctx, pid)
        # TODO: Implement mutiny combat in Stage 7
        return gs_set_context(state, replace(ctx, step=ActionStep.CHOOSE_SLOT))

    raise RuntimeError(f"Unknown last resort index: {idx}")


# ---------------------------------------------------------------------------
# RUNNING sub-phase
# ---------------------------------------------------------------------------

def _step_running_prep(state: GameState, ctx: ActionContext) -> GameState:
    """Running step 1: Refresh runner's action field, other player draws 4."""
    assert ctx.running is not None
    runner = ctx.running.runner
    other_pid = runner.other()
    rng = gs_get_rng(state)

    # 1. Refresh all of runner's action slots
    af = state.action_field
    for i in range(4):
        af, cleared = af_clear_slot(af, runner, i)
        if cleared:
            ps = gs_get_player(state, runner)
            ps = ps_add_to_refresh(ps, cleared)
            state = gs_update_player(state, runner, ps)
    state = gs_set_action_field(state, af)

    # 2. Other player draws 4 from runner's deck
    state, rng, drawn = _safe_draw(state, rng, runner, 4)
    if state.phase == Phase.GAME_OVER:
        return state
    state = gs_with_rng_result(state, rng)

    ctx = replace(ctx, step=ActionStep.RUNNING_DECIDE,
                  running=RunningContext(runner=runner, drawn_cards=drawn))
    state = gs_set_context(state, ctx)

    # Present recycling decision to other player
    card_descs = [state.card_def(c).big_name or state.card_def(c).name for c in drawn]
    actions = []
    # Generate all 2^4 recycling combinations
    import itertools
    for combo in itertools.product([False, True], repeat=len(drawn)):
        actions.append(Action(kind=ActionKind.SELECT_RECYCLE, recycle_flags=tuple(combo)))

    decision = PendingDecision(
        player=other_pid,
        kind=DecisionKind.RECYCLE_DECISION,
        legal_actions=tuple(actions),
        context_description=(
            f"Running: Choose which cards to recycle (refresh → replace).\n"
            f"Cards: {', '.join(card_descs)}\n"
            f"True = recycle, False = keep."
        ),
        visible_cards=drawn,
    )
    return gs_set_pending(state, decision)


def _step_running_decide(state: GameState, ctx: ActionContext) -> GameState:
    """Shouldn't be called — RUNNING_DECIDE waits for player input."""
    raise RuntimeError("RUNNING_DECIDE should have a pending decision")


def _apply_running_decide(state: GameState, ctx: ActionContext, action: Action) -> GameState:
    """Process recycling decisions and prepare for dealing."""
    assert action.kind == ActionKind.SELECT_RECYCLE
    assert action.recycle_flags is not None
    assert ctx.running is not None
    runner = ctx.running.runner
    rng = gs_get_rng(state)

    drawn = list(ctx.running.drawn_cards)
    flags = action.recycle_flags
    final_cards: list[CardId] = []

    for i, card_id in enumerate(drawn):
        if flags[i]:
            # Recycle: refresh this card, draw a replacement (face-down)
            ps = gs_get_player(state, runner)
            ps = ps_add_to_refresh(ps, (card_id,))
            state = gs_update_player(state, runner, ps)

            state, rng, replacement = _safe_draw(state, rng, runner, 1)
            if state.phase == Phase.GAME_OVER:
                return state
            if replacement:
                final_cards.append(replacement[0])
        else:
            final_cards.append(card_id)

    state = gs_with_rng_result(state, rng)

    # Shuffle the final cards and deal face-up to runner's action field
    rng = gs_get_rng(state)
    rng, final_cards = rng_shuffle(rng, final_cards)
    state = gs_with_rng_result(state, rng)

    # Deal in fill order
    af = state.action_field
    for i, card_id in enumerate(final_cards):
        if i < 4:
            slot_idx = ACTION_FILL_ORDER[i]
            af = af_add_card_to_slot(af, runner, slot_idx, card_id, position="top")
            state = gs_set_action_field(state, af)

            # On Placement triggers
            rng = gs_get_rng(state)
            state, rng = _handle_on_placement(state, rng, runner, slot_idx, card_id)
            state = gs_with_rng_result(state, rng)
            af = state.action_field

    state = gs_set_action_field(state, af)

    # Running complete — return to slot selection
    ctx = replace(ctx, step=ActionStep.CHOOSE_SLOT, running=None)
    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# CALL THE GUARDS
# ---------------------------------------------------------------------------

def _step_guards(state: GameState, ctx: ActionContext) -> GameState:
    """Call the Guards: discard role card, disarm other player, place 4 guards."""
    pid = ctx.current_turn
    other_pid = pid.other()
    rng = gs_get_rng(state)

    # 1. Discard caller's role card
    ps = gs_get_player(state, pid)
    role_cid = ps.role_card_id
    if role_cid is not None:
        equipment = list(ps.equipment)
        for i, eq in enumerate(equipment):
            if eq == role_cid:
                equipment[i] = None
                break
        ps = replace(ps,
                     equipment=(equipment[0], equipment[1]),
                     discard_pile=ps.discard_pile + (role_cid,),
                     role_card_id=None)
        state = gs_update_player(state, pid, ps)

    # 2. Disarm the other player (discard weapon + kill pile)
    state = _disarm_player(state, other_pid)

    # 3. Place 4 guards on other player's action field
    af = state.action_field
    for i in range(4):
        if not state.guard_deck:
            break
        guard_id = state.guard_deck[0]
        state = replace(state, guard_deck=state.guard_deck[1:])

        slot_idx = i
        af = af_add_card_to_slot(af, other_pid, slot_idx, guard_id, position="top")
        state = gs_set_action_field(state, af)

        # On Placement for guards
        state, rng = _handle_on_placement(state, rng, other_pid, slot_idx, guard_id)
        af = state.action_field

    state = gs_with_rng_result(state, rng)
    state = gs_set_action_field(state, af)

    # Return to slot selection
    ctx = replace(ctx, step=ActionStep.CHOOSE_SLOT)
    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# CHOOSE_SLOT
# ---------------------------------------------------------------------------

def _step_choose_slot(state: GameState, ctx: ActionContext) -> GameState:
    """Present slot selection to the current player."""
    pid = ctx.current_turn

    # Check if this player is done
    if _player_is_done(state, ctx, pid):
        return gs_set_context(state, replace(ctx, step=ActionStep.NEXT_TURN))

    # Build legal slot choices
    legal_slots = _get_legal_slots(state, ctx, pid)

    if not legal_slots:
        # No legal slots anywhere — resolve from deck
        return gs_set_context(state, replace(ctx, step=ActionStep.RESOLVE_FROM_DECK))

    actions: list[Action] = []
    descriptions: list[str] = []

    for (slot_owner, slot_idx, kind) in legal_slots:
        slot = af_get_slot(state.action_field, slot_owner, slot_idx)
        slot_label = _slot_label(slot_owner, slot_idx, kind)

        # Show card info for visible slots
        if kind in (SlotKind.OWN_DISTANT, SlotKind.OWN_HIDDEN, SlotKind.OTHER_DISTANT):
            card_names = [state.card_def(c).big_name or state.card_def(c).name
                          for c in slot.cards]
            slot_desc = f"{slot_label}: {', '.join(card_names)}"
        else:  # OTHER_HIDDEN — can't see cards
            slot_desc = f"{slot_label}: ({len(slot.cards)} cards)"

        actions.append(Action(kind=ActionKind.SELECT_SLOT,
                              slot_ref=SlotRef(owner=slot_owner, index=slot_idx)))
        descriptions.append(slot_desc)

    # Voluntary discard option (can discard equipment/weapon before choosing slot)
    vd_actions = _get_voluntary_discard_actions(state, pid)
    for vd in vd_actions:
        cd = state.card_def(vd.card_id)
        descriptions.append(f"Discard: {cd.big_name or cd.name}")
        actions.append(vd)

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.CHOOSE_ACTION_SLOT,
        legal_actions=tuple(actions),
        context_description=(
            f"Choose an action slot to resolve (play {4 - _plays_remaining(ctx, pid) + 1}/3).\n"
            + "\n".join(descriptions)
        ),
    )
    return gs_set_pending(state, decision)


def _apply_choose_slot(state: GameState, ctx: ActionContext, action: Action) -> GameState:
    pid = ctx.current_turn

    # Handle voluntary discard
    if action.kind == ActionKind.SELECT_CARD:
        return _do_voluntary_discard(state, ctx, pid, action.card_id)

    assert action.kind == ActionKind.SELECT_SLOT
    assert action.slot_ref is not None
    slot_ref = action.slot_ref

    kind = classify_slot(pid, slot_ref.owner, slot_ref.index)

    # Check if consent is needed
    if kind in (SlotKind.OTHER_HIDDEN, SlotKind.OTHER_DISTANT):
        # Check Cardsharp waiver
        if player_is_cardsharp(state, slot_ref.owner):
            # Cardsharp: no consent needed for their slots
            pass
        else:
            # Need consent
            ctx = replace(ctx,
                          step=ActionStep.CONSENT_CHECK,
                          consent_request=ConsentRequest(
                              requester=pid,
                              slot_owner=slot_ref.owner,
                              slot_index=slot_ref.index,
                          ))
            return gs_set_context(state, ctx)

    # No consent needed — proceed to resolution
    return _begin_slot_resolution(state, ctx, pid, slot_ref.owner, slot_ref.index)


# ---------------------------------------------------------------------------
# CONSENT_CHECK
# ---------------------------------------------------------------------------

def _step_consent_check(state: GameState, ctx: ActionContext) -> GameState:
    """Present consent request to the slot owner."""
    assert ctx.consent_request is not None
    cr = ctx.consent_request

    slot = af_get_slot(state.action_field, cr.slot_owner, cr.slot_index)
    kind = classify_slot(cr.requester, cr.slot_owner, cr.slot_index)
    label = _slot_label(cr.slot_owner, cr.slot_index, kind)

    actions = (
        Action(kind=ActionKind.SELECT_BOOL, flag=True),   # Grant
        Action(kind=ActionKind.SELECT_BOOL, flag=False),   # Deny
    )
    decision = PendingDecision(
        player=cr.slot_owner,
        kind=DecisionKind.GRANT_CONSENT,
        legal_actions=actions,
        context_description=(
            f"{cr.requester.name} wants to resolve your {label}.\n"
            f"[True] Grant consent  [False] Deny"
        ),
    )
    return gs_set_pending(state, decision)


def _apply_consent(state: GameState, ctx: ActionContext, action: Action) -> GameState:
    assert action.kind == ActionKind.SELECT_BOOL
    assert action.flag is not None
    assert ctx.consent_request is not None
    cr = ctx.consent_request

    if action.flag:
        # Consent granted — proceed to resolution
        # Apply distance penalty if distant slot
        requester = cr.requester
        kind = classify_slot(requester, cr.slot_owner, cr.slot_index)
        if kind == SlotKind.OTHER_DISTANT:
            # 3 damage distance penalty (check weapon_7 waiver later in Stage 8)
            ps = gs_get_player(state, requester)
            ps = ps_set_hp(ps, ps.hp - 3)
            state = gs_update_player(state, requester, ps)

            # Check death from distance penalty
            ps = gs_get_player(state, requester)
            if ps.hp <= 0:
                ps = ps_set_dead(ps)
                state = gs_update_player(state, requester, ps)
                ctx = replace(ctx, consent_request=None, step=ActionStep.NEXT_TURN)
                return gs_set_context(state, ctx)

        ctx = replace(ctx, consent_request=None)
        return _begin_slot_resolution(state, ctx, cr.requester,
                                      cr.slot_owner, cr.slot_index)
    else:
        # Consent denied — go back to slot selection (doesn't consume play)
        ctx = replace(ctx, consent_request=None, step=ActionStep.CHOOSE_SLOT)
        return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# RESOLVING_SLOT — card-by-card resolution
# ---------------------------------------------------------------------------

def _begin_slot_resolution(
    state: GameState, ctx: ActionContext,
    resolver: PlayerId, slot_owner: PlayerId, slot_index: int
) -> GameState:
    """Start resolving a slot: pull cards, set up resolution context."""
    af = state.action_field
    slot = af_get_slot(af, slot_owner, slot_index)

    # Clear the slot on the action field
    af, cards = af_clear_slot(af, slot_owner, slot_index)
    state = gs_set_action_field(state, af)

    if not cards:
        # Shouldn't happen (we filtered empty slots), but handle gracefully
        ctx = _consume_play(ctx, resolver)
        ctx = replace(ctx, step=ActionStep.NEXT_TURN)
        return gs_set_context(state, ctx)

    # Set up resolution context
    res = ResolutionContext(
        slot_owner=slot_owner,
        slot_index=slot_index,
        card_queue=cards[1:],  # remaining after current
        current_card=cards[0],
    )
    ctx = replace(ctx, step=ActionStep.RESOLVING_SLOT, resolving=res)
    return gs_set_context(state, ctx)


def _step_resolving_slot(state: GameState, ctx: ActionContext) -> GameState:
    """Process the current card in the resolution queue."""
    assert ctx.resolving is not None
    res = ctx.resolving
    pid = ctx.current_turn

    if res.current_card is None:
        # No more cards — this play is done
        ctx = _consume_play(ctx, pid)
        ctx = replace(ctx, resolving=None, step=ActionStep.NEXT_TURN)
        return gs_set_context(state, ctx)

    card_id = res.current_card
    cd = state.card_def(card_id)

    # Resolve the card by type
    state = _resolve_card(state, ctx, pid, card_id, cd)

    # Check if player died during resolution
    ps = gs_get_player(state, pid)
    if ps.is_dead:
        ctx_new = state.phase_context
        if isinstance(ctx_new, ActionContext):
            ctx_new = replace(ctx_new, resolving=None, step=ActionStep.NEXT_TURN)
            return gs_set_context(state, ctx_new)
        return state

    # Move to next card or voluntary discard window
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)
    if ctx.resolving is not None and ctx.resolving.card_queue:
        # More cards — offer voluntary discard window first
        ctx = replace(ctx, step=ActionStep.VOLUNTARY_DISCARD)
        return gs_set_context(state, ctx)
    else:
        # No more cards — play is done
        ctx = _consume_play(ctx, pid)
        ctx = replace(ctx, resolving=None, step=ActionStep.NEXT_TURN)
        return gs_set_context(state, ctx)


def _resolve_card(
    state: GameState, ctx: ActionContext,
    resolver: PlayerId, card_id: CardId, cd: CardDef
) -> GameState:
    """
    Resolve a single card by its type(s).
    Dual-typed cards process all types.

    On Resolve effects are NOT fired yet (Stage 8).
    Default type processing uses combat module for enemies.
    """
    from ..combat import resolve_combat, get_attack_options, apply_damage, apply_healing

    # Determine type processing order
    # Enemy-like: combat
    if is_enemy_like(cd):
        state = _resolve_enemy(state, ctx, resolver, card_id, cd)
        return state

    # For non-enemy cards, process by type
    processed = False

    # Event component (fires first for dual-types like Chariot)
    if is_event(cd):
        # Stage 8 will fire ON_RESOLVE handlers
        # For now, just discard pure events
        if not is_equipment(cd):
            ps = gs_get_player(state, resolver)
            ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
            state = gs_update_player(state, resolver, ps)
            processed = True

    # Food component
    if is_food(cd) and not processed:
        state = _resolve_food(state, resolver, card_id, cd)
        processed = True

    # Equipment component (including dual Event+Equipment like Chariot)
    if is_equipment(cd) and not is_weapon(cd) and not processed:
        state = _resolve_equipment(state, resolver, card_id, cd)
        processed = True
    elif is_equipment(cd) and is_event(cd) and not processed:
        # Event+Equipment: event part handled, now equip
        state = _resolve_equipment(state, resolver, card_id, cd)
        processed = True

    # Weapon component (pure weapon, not Equipment+Weapon like Judgement)
    if is_weapon(cd) and not is_equipment(cd) and not processed:
        state = _resolve_weapon(state, resolver, card_id, cd)
        processed = True

    # Equipment+Weapon (e.g., Judgement, Strength): equip by default
    if is_equipment(cd) and is_weapon(cd) and not processed:
        state = _resolve_equipment(state, resolver, card_id, cd)
        processed = True

    return state


def _resolve_enemy(
    state: GameState, ctx: ActionContext,
    resolver: PlayerId, card_id: CardId, cd: CardDef
) -> GameState:
    """
    Combat resolution using the combat module.

    For now, auto-selects attack mode:
      - Use weapon if available and legal (dulling check passes)
      - Otherwise use fists

    Stage 8 will present CHOOSE_ATTACK_MODE decision to the player.
    """
    from ..combat import resolve_combat, get_attack_options

    options = get_attack_options(state, resolver, card_id)

    # Auto-select: prefer weapon over fists
    mode = "fists"
    slot_idx = 0
    for opt_mode, opt_idx in options:
        if opt_mode != "fists":
            mode = "weapon"
            slot_idx = opt_idx
            break

    state = resolve_combat(state, resolver, card_id, mode, slot_idx)
    return state


def _resolve_food(
    state: GameState, resolver: PlayerId,
    card_id: CardId, cd: CardDef
) -> GameState:
    """Resolve a food card: eat for healing if not eaten this phase."""
    from ..combat import apply_healing, HealSource

    ps = gs_get_player(state, resolver)

    if not ps.has_eaten_this_phase and cd.level is not None:
        # Eat: heal for food's level
        state = apply_healing(state, resolver, cd.level, HealSource.FOOD)
        ps = gs_get_player(state, resolver)
        ps = replace(ps, has_eaten_this_phase=True)
        state = gs_update_player(state, resolver, ps)

    # Discard food
    ps = gs_get_player(state, resolver)
    ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
    state = gs_update_player(state, resolver, ps)
    return state


def _resolve_weapon(
    state: GameState, resolver: PlayerId,
    card_id: CardId, cd: CardDef
) -> GameState:
    """Wield a weapon: discard old weapon + kill pile, place new weapon."""
    return _wield_weapon(state, resolver, card_id)


def _resolve_equipment(
    state: GameState, resolver: PlayerId,
    card_id: CardId, cd: CardDef
) -> GameState:
    """Equip: place in equipment slot, discard if full (auto-pick first for stub)."""
    return _equip_card(state, resolver, card_id)


def _resolve_event(
    state: GameState, resolver: PlayerId,
    card_id: CardId, cd: CardDef
) -> GameState:
    """Resolve an event: process text (stub), then discard."""
    # Stage 8 will fire ON_RESOLVE handlers here
    ps = gs_get_player(state, resolver)
    ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
    state = gs_update_player(state, resolver, ps)
    return state


# ---------------------------------------------------------------------------
# VOLUNTARY_DISCARD
# ---------------------------------------------------------------------------

def _step_voluntary_discard(state: GameState, ctx: ActionContext) -> GameState:
    """Offer voluntary discard window between cards in a multi-card slot."""
    pid = ctx.current_turn
    vd_actions = _get_voluntary_discard_actions(state, pid)

    if not vd_actions:
        # Nothing to discard — advance to next card
        return _advance_to_next_card(state, ctx)

    # Add DECLINE option
    all_actions = list(vd_actions) + [Action(kind=ActionKind.DECLINE)]

    decision = PendingDecision(
        player=pid,
        kind=DecisionKind.VOLUNTARY_DISCARD,
        legal_actions=tuple(all_actions),
        context_description="You may voluntarily discard equipment or a weapon, or DECLINE to continue.",
    )
    return gs_set_pending(state, decision)


def _apply_voluntary_discard(state: GameState, ctx: ActionContext, action: Action) -> GameState:
    pid = ctx.current_turn

    if action.kind == ActionKind.DECLINE:
        return _advance_to_next_card(state, ctx)

    assert action.kind == ActionKind.SELECT_CARD
    assert action.card_id is not None

    state = _do_voluntary_discard(state, ctx, pid, action.card_id)
    # Stay in VOLUNTARY_DISCARD to allow more discards
    return state


def _advance_to_next_card(state: GameState, ctx: ActionContext) -> GameState:
    """Move to the next card in the resolution queue."""
    assert ctx.resolving is not None
    res = ctx.resolving

    if res.card_queue:
        new_res = ResolutionContext(
            slot_owner=res.slot_owner,
            slot_index=res.slot_index,
            card_queue=res.card_queue[1:],
            current_card=res.card_queue[0],
        )
        ctx = replace(ctx, resolving=new_res, step=ActionStep.RESOLVING_SLOT)
    else:
        # No more cards
        pid = ctx.current_turn
        ctx = _consume_play(ctx, pid)
        ctx = replace(ctx, resolving=None, step=ActionStep.NEXT_TURN)

    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# RESOLVE_FROM_DECK
# ---------------------------------------------------------------------------

def _step_resolve_from_deck(state: GameState, ctx: ActionContext) -> GameState:
    """No legal slots — resolve one card from the top of own deck."""
    pid = ctx.current_turn
    rng = gs_get_rng(state)

    state, rng, drawn = _safe_draw(state, rng, pid, 1)
    state = gs_with_rng_result(state, rng)

    if state.phase == Phase.GAME_OVER:
        return state

    if drawn:
        card_id = drawn[0]
        cd = state.card_def(card_id)
        state = _resolve_card(state, ctx, pid, card_id, cd)

    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)
    ctx = _consume_play(ctx, pid)
    ctx = replace(ctx, step=ActionStep.NEXT_TURN)
    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# NEXT_TURN
# ---------------------------------------------------------------------------

def _step_next_turn(state: GameState, ctx: ActionContext) -> GameState:
    """Switch to the other player's turn, or end the phase if both are done."""
    pid = ctx.current_turn
    other_pid = pid.other()

    # Check if both players are done
    if _player_is_done(state, ctx, PlayerId.RED) and _player_is_done(state, ctx, PlayerId.BLUE):
        return gs_set_context(state, replace(ctx, step=ActionStep.ELUSIVE_CLEANUP))

    # Switch to other player
    if _player_is_done(state, ctx, other_pid):
        # Other player is already done — stay on current player
        if _player_is_done(state, ctx, pid):
            return gs_set_context(state, replace(ctx, step=ActionStep.ELUSIVE_CLEANUP))
        # Current player continues
        ctx = replace(ctx, step=ActionStep.CHOOSE_SLOT)
        return gs_set_context(state, ctx)

    # Normal alternation
    ctx = replace(ctx, current_turn=other_pid)

    # If it's other player's first play, offer Last Resort
    if _is_first_play(ctx, other_pid) and not _last_resort_offered(ctx, other_pid):
        ctx = replace(ctx, step=ActionStep.LAST_RESORT_OFFER)
    else:
        ctx = replace(ctx, step=ActionStep.CHOOSE_SLOT)

    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# ELUSIVE_CLEANUP
# ---------------------------------------------------------------------------

def _step_elusive_cleanup(state: GameState, ctx: ActionContext) -> GameState:
    """Refresh Elusive cards from the action field at end of Action Phase."""
    af = state.action_field

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        for i in range(4):
            slot = af_get_slot(af, pid, i)
            remaining: list[CardId] = []
            refreshed: list[CardId] = []

            for cid in slot.cards:
                cd = state.card_def(cid)
                if cd.is_elusive:
                    refreshed.append(cid)
                else:
                    remaining.append(cid)

            if refreshed:
                af = af_set_slot(af, pid, i, ActionSlot(cards=tuple(remaining)))
                ps = ps_add_to_refresh(ps, tuple(refreshed))

        state = gs_update_player(state, pid, ps)

    state = gs_set_action_field(state, af)
    ctx = replace(ctx, step=ActionStep.DONE)
    return gs_set_context(state, ctx)


# ---------------------------------------------------------------------------
# DONE
# ---------------------------------------------------------------------------

def _step_done(state: GameState, ctx: ActionContext) -> GameState:
    """Transition to Refresh Phase."""
    state = gs_increment_turn(state)
    return gs_set_phase(state, Phase.REFRESH, RefreshContext())


# ---------------------------------------------------------------------------
# Slot legality
# ---------------------------------------------------------------------------

def _get_legal_slots(
    state: GameState, ctx: ActionContext, resolver: PlayerId
) -> list[tuple[PlayerId, int, SlotKind]]:
    """
    Get all legal slot choices for the resolver.

    Rules:
      - Cannot resolve empty slots.
      - First slots can only be resolved as first action play.
      - Own slots: always legal (subject to First).
      - Other's slots: need consent (handled later), but included in choices.
      - If NO own slots are legal, MUST use other's slots.
      - If other's slots also exhausted, resolve from deck (handled by caller).
    """
    af = state.action_field
    is_first = _is_first_play(ctx, resolver)

    own_slots: list[tuple[PlayerId, int, SlotKind]] = []
    other_slots: list[tuple[PlayerId, int, SlotKind]] = []

    for pid in [resolver, resolver.other()]:
        for i in range(4):
            slot = af_get_slot(af, pid, i)
            if slot.is_empty:
                continue

            kind = classify_slot(resolver, pid, i)

            # First constraint: slots containing First cards can only be used on first play
            has_first = any(state.card_def(c).is_first for c in slot.cards)
            if has_first and not is_first:
                continue

            if pid == resolver:
                own_slots.append((pid, i, kind))
            else:
                other_slots.append((pid, i, kind))

    # If player has legal own slots, offer all (own + other's)
    if own_slots:
        return own_slots + other_slots

    # No legal own slots — must use other's field
    if other_slots:
        return other_slots

    # Nothing — caller will handle resolve-from-deck
    return []


def _slot_label(owner: PlayerId, index: int, kind: SlotKind) -> str:
    labels = {0: "Top Distant", 1: "Top Hidden", 2: "Bottom Hidden", 3: "Bottom Distant"}
    return f"{owner.name} {labels[index]} ({kind.name})"


# ---------------------------------------------------------------------------
# Voluntary discard helpers
# ---------------------------------------------------------------------------

def _get_voluntary_discard_actions(state: GameState, pid: PlayerId) -> list[Action]:
    """Build actions for voluntarily discardable equipment/weapons."""
    ps = gs_get_player(state, pid)
    actions: list[Action] = []

    # Equipment
    for eq_id in ps.equipment:
        if eq_id is not None:
            actions.append(Action(kind=ActionKind.SELECT_CARD, card_id=eq_id))

    # Weapons
    for ws in ps.weapon_slots:
        if ws.weapon is not None:
            actions.append(Action(kind=ActionKind.SELECT_CARD, card_id=ws.weapon))

    return actions


def _do_voluntary_discard(
    state: GameState, ctx: ActionContext,
    pid: PlayerId, card_id: CardId
) -> GameState:
    """Voluntarily discard an equipment or weapon."""
    ps = gs_get_player(state, pid)

    # Check equipment
    equipment = list(ps.equipment)
    for i, eq in enumerate(equipment):
        if eq == card_id:
            equipment[i] = None
            ps = replace(ps, equipment=(equipment[0], equipment[1]))
            if ps.role_card_id == card_id:
                ps = replace(ps, role_card_id=None)
            ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
            state = gs_update_player(state, pid, ps)
            return state

    # Check weapons
    weapon_slots = list(ps.weapon_slots)
    for i, ws in enumerate(weapon_slots):
        if ws.weapon == card_id:
            # Discard weapon + kill pile
            discard_cards = (card_id,) + ws.kill_pile
            weapon_slots[i] = WeaponSlot(parity=ws.parity)
            ps = replace(ps,
                         weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + discard_cards)
            state = gs_update_player(state, pid, ps)
            return state

    return state


# ---------------------------------------------------------------------------
# Inventory operations
# ---------------------------------------------------------------------------

def _wield_weapon(state: GameState, pid: PlayerId, card_id: CardId) -> GameState:
    """Wield a new weapon: discard old weapon + kill pile, place new weapon."""
    ps = gs_get_player(state, pid)
    weapon_slots = list(ps.weapon_slots)

    # For now, use the first (and usually only) weapon slot
    # Two-Armed Freak parity matching will be handled in Stage 8
    slot_idx = 0
    ws = weapon_slots[slot_idx]

    discard_cards: tuple[CardId, ...] = ()
    if ws.weapon is not None:
        discard_cards = (ws.weapon,) + ws.kill_pile

    weapon_slots[slot_idx] = WeaponSlot(weapon=card_id, kill_pile=(), parity=ws.parity)
    ps = replace(ps,
                 weapon_slots=tuple(weapon_slots),
                 discard_pile=ps.discard_pile + discard_cards)
    state = gs_update_player(state, pid, ps)
    return state


def _equip_card(state: GameState, pid: PlayerId, card_id: CardId) -> GameState:
    """Equip a card. If both slots full, auto-discard first non-role equipment (stub)."""
    ps = gs_get_player(state, pid)
    equipment = list(ps.equipment)

    # Find empty slot
    for i in range(2):
        if equipment[i] is None:
            equipment[i] = card_id
            ps = replace(ps, equipment=(equipment[0], equipment[1]))
            state = gs_update_player(state, pid, ps)
            return state

    # Both full — auto-discard the first non-role equipment
    # Full implementation with player choice comes in Stage 8
    for i in range(2):
        if equipment[i] != ps.role_card_id:
            discard_id = equipment[i]
            equipment[i] = card_id
            ps = replace(ps,
                         equipment=(equipment[0], equipment[1]),
                         discard_pile=ps.discard_pile + (discard_id,))
            state = gs_update_player(state, pid, ps)
            return state

    # Edge case: both slots are role card (shouldn't happen) — discard slot 0
    discard_id = equipment[0]
    equipment[0] = card_id
    ps = replace(ps,
                 equipment=(equipment[0], equipment[1]),
                 discard_pile=ps.discard_pile + (discard_id,))
    if ps.role_card_id == discard_id:
        ps = replace(ps, role_card_id=None)
    state = gs_update_player(state, pid, ps)
    return state


def _disarm_player(state: GameState, pid: PlayerId) -> GameState:
    """Discard all weapons and kill piles for a player."""
    ps = gs_get_player(state, pid)
    weapon_slots = list(ps.weapon_slots)
    discard_cards: list[CardId] = []

    for i, ws in enumerate(weapon_slots):
        if ws.weapon is not None:
            discard_cards.append(ws.weapon)
            discard_cards.extend(ws.kill_pile)
            weapon_slots[i] = WeaponSlot(parity=ws.parity)

    ps = replace(ps,
                 weapon_slots=tuple(weapon_slots),
                 discard_pile=ps.discard_pile + tuple(discard_cards))
    state = gs_update_player(state, pid, ps)
    return state