"""
Fools' Journey — Executable Spec
Stage 10: Card effect handlers — all decisions fully interactive.

Architecture for mid-resolution decisions:
  1. Handler detects a decision is needed
  2. Handler stores state in EffectContext, sets step to EFFECT_DECISION
  3. Handler returns state with PendingDecision set
  4. Player responds → _apply_effect_decision dispatches to handler's resume function
  5. Resume function continues processing and returns to RESOLVING_SLOT

Handlers that need no decision remain simple (state, card_id, resolver) → state.
"""

from __future__ import annotations

from dataclasses import replace, field
from typing import Any

from .types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Alignment, Phase, Parity,
    PlayerState, WeaponSlot, ManipulationField,
    ActionSlot, ActionField,
    GameState, Action, ActionKind, AttackMode,
    PendingDecision, DecisionKind,
    ActionContext, ActionStep, ResolutionContext, EffectContext,
    Trigger, SlotRef,
    ACTION_FILL_ORDER,
)
from .cards import (
    has_trigger, get_handlers_for_trigger,
    is_enemy_like, is_food, is_weapon, is_equipment, is_event,
    get_card_def, ALL_CARD_DEFS,
)
from .state_helpers import (
    gs_get_player, gs_update_player,
    gs_set_action_field, gs_set_context,
    gs_set_card_state, gs_get_rng, gs_with_rng_result,
    gs_set_pending,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_hand,
    ps_set_refresh, ps_set_discard,
    ps_set_equipment, ps_set_weapon_slots, ps_set_eaten,
    ps_set_action_phase_over, ps_set_role_card_id,
    ps_add_to_refresh, ps_add_to_discard,
    ps_add_permanent_ability,
    af_get_slot, af_set_slot, af_clear_slot,
    af_add_card_to_slot, af_find_empty_slots,
)
from .combat import (
    apply_damage, apply_healing, set_hp_direct, resolve_combat,
    can_use_weapon, get_attack_options,
    DamageSource, HealSource,
    _discard_equipment_by_id, _find_equipped_named, _has_equipped_named,
    _refresh_player_action_field,
)
from .setup import (
    player_is_corruption, player_is_food_fighter,
    player_is_fool_role, player_is_world_role,
    player_is_poet, player_is_phoenix,
)
from .rng import rng_d20, rng_d10, rng_d4, rng_shuffle


# ---------------------------------------------------------------------------
# Effect dispatch
# ---------------------------------------------------------------------------

def fire_on_resolve(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    cd = state.card_def(card_id)
    for h in get_handlers_for_trigger(cd, Trigger.ON_RESOLVE):
        fn = HANDLER_REGISTRY.get(h)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_resolve_after(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    cd = state.card_def(card_id)
    for h in get_handlers_for_trigger(cd, Trigger.ON_RESOLVE_AFTER):
        fn = HANDLER_REGISTRY.get(h)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_kill(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    cd = state.card_def(card_id)
    for h in get_handlers_for_trigger(cd, Trigger.ON_KILL):
        fn = HANDLER_REGISTRY.get(h)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_discard(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    cd = state.card_def(card_id)
    for h in get_handlers_for_trigger(cd, Trigger.ON_DISCARD):
        fn = HANDLER_REGISTRY.get(h)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_after_death(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    cd = state.card_def(card_id)
    for h in get_handlers_for_trigger(cd, Trigger.AFTER_DEATH):
        fn = HANDLER_REGISTRY.get(h)
        if fn:
            state = fn(state, card_id, resolver)
    return state


# ---------------------------------------------------------------------------
# Effect decision infrastructure
# ---------------------------------------------------------------------------

def set_effect_decision(
    state: GameState,
    handler: str,
    card_id: CardId,
    resolver: PlayerId,
    decision: PendingDecision,
    data: dict | None = None,
) -> GameState:
    """
    Set up a mid-resolution effect decision.
    Only works during Action Phase. If called outside Action Phase
    (e.g., during tests or nested resolution), auto-selects first legal action.
    """
    ctx = state.phase_context
    if not isinstance(ctx, ActionContext):
        # Fallback: auto-resolve with first legal action
        resume_fn = RESUME_REGISTRY.get(handler)
        if resume_fn and decision.legal_actions:
            ectx = EffectContext(handler=handler, card_id=card_id,
                                resolver=resolver, data=data or {})
            return resume_fn(state, ectx, decision.legal_actions[0])
        return state

    ectx = EffectContext(
        handler=handler,
        card_id=card_id,
        resolver=resolver,
        data=data or {},
    )
    # Mark that ON_RESOLVE has already fired for the current card
    if ctx.resolving:
        new_res = replace(ctx.resolving, on_resolve_done=True)
        ctx = replace(ctx, resolving=new_res)
    ctx = replace(ctx, step=ActionStep.EFFECT_DECISION, effect_ctx=ectx)
    state = gs_set_context(state, ctx)
    return gs_set_pending(state, decision)


def resume_effect(state: GameState, action: Action) -> GameState:
    """Resume a handler after the player responds to an effect decision."""
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)
    assert ctx.effect_ctx is not None
    ectx = ctx.effect_ctx
    resume_fn = RESUME_REGISTRY.get(ectx.handler)
    if resume_fn is None:
        raise RuntimeError(f"No resume handler for: {ectx.handler}")
    # Clear effect context and return to RESOLVING_SLOT
    ctx = replace(ctx, effect_ctx=None, step=ActionStep.RESOLVING_SLOT)
    state = gs_set_context(state, ctx)
    state = resume_fn(state, ectx, action)

    # Now continue with default type processing for the current card
    # (ON_RESOLVE already fired, on_resolve_done flag ensures we skip it)
    ctx = state.phase_context
    if isinstance(ctx, ActionContext) and ctx.resolving and ctx.resolving.current_card is not None:
        # Check if a sub-decision was set by the resume handler
        if ctx.step in (ActionStep.EFFECT_DECISION, ActionStep.ATTACK_CHOICE):
            return state
        if state.pending is not None:
            return state

        # The card still needs default processing — _step_resolving_slot will handle it
        # since on_resolve_done is True. Return to RESOLVING_SLOT step.
        pass

    return state


# ---------------------------------------------------------------------------
# Helper: card short name for decision descriptions
# ---------------------------------------------------------------------------

def _cname(state: GameState, card_id: CardId) -> str:
    cd = state.card_def(card_id)
    lv = f" Lv{cd.level}" if cd.level is not None else ""
    return f"{cd.big_name or cd.name}{lv}"


# ---------------------------------------------------------------------------
# No-op handler
# ---------------------------------------------------------------------------

def _noop(state, card_id, resolver):
    return state


# ===========================================================================
# HANDLER IMPLEMENTATIONS
# ===========================================================================

# --- Food ---

def _food_1_d10_damage(state, card_id, resolver):
    """food_1: After eating, receive d10 damage."""
    rng = gs_get_rng(state)
    rng, roll = rng_d10(rng)
    state = gs_with_rng_result(state, rng)
    return apply_damage(state, resolver, roll, DamageSource.FOOD_PENALTY)


def _saltine_choice(state, card_id, resolver):
    """food_3: May wield as weapon instead of eating. Decision."""
    actions = (
        Action(kind=ActionKind.SELECT_INDEX, index=0),  # Eat as food
        Action(kind=ActionKind.SELECT_INDEX, index=1),  # Wield as weapon
    )
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.SALTINE_CHOICE,
        legal_actions=actions,
        context_description="Saltine Shuriken: [0] Eat as food  [1] Wield as weapon",
    )
    return set_effect_decision(state, "saltine_choice", card_id, resolver, decision)


def _saltine_choice_resume(state, ectx, action):
    card_id = ectx.card_id
    resolver = ectx.resolver
    if action.index == 1:
        # Wield as weapon — remove from discard if present, then wield
        ps = gs_get_player(state, resolver)
        discard = list(ps.discard_pile)
        if card_id in discard:
            discard.remove(card_id)
            ps = replace(ps, discard_pile=tuple(discard))
            state = gs_update_player(state, resolver, ps)
        # Un-set eaten flag if it was set by food processing
        ps = gs_get_player(state, resolver)
        ps = replace(ps, has_eaten_this_phase=False)
        state = gs_update_player(state, resolver, ps)
        from .phases.action import _wield_weapon
        state = _wield_weapon(state, resolver, card_id)
    # else: index 0, eat as food — already handled by default processing
    return state


def _fat_sandwich_equip(state, card_id, resolver):
    """food_7: Equip instead of eating."""
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)
    from .phases.action import _equip_card
    return _equip_card(state, resolver, card_id)


def _bellyfiller_equip(state, card_id, resolver):
    """food_9: Equip instead of eating."""
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)
    from .phases.action import _equip_card
    return _equip_card(state, resolver, card_id)


# --- Weapons ---

def _vorpal_blade_discard(state, card_id, resolver):
    """weapon_10: On discard, refresh all action cards, Action Phase over."""
    state = _refresh_player_action_field(state, resolver)
    ps = gs_get_player(state, resolver)
    ps = ps_set_action_phase_over(ps, True)
    return gs_update_player(state, resolver, ps)


def _pinata_stick(state, card_id, resolver):
    """weapon_3: On discard, may deal 3 damage to other to see their hand. Decision."""
    other = resolver.other()
    actions = (
        Action(kind=ActionKind.SELECT_BOOL, flag=True),   # Pay 3 damage, see hand
        Action(kind=ActionKind.SELECT_BOOL, flag=False),   # Decline
    )
    other_hand_size = len(gs_get_player(state, other).hand)
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.VOLUNTARY_DISCARD,  # Reuse for yes/no
        legal_actions=actions,
        context_description=(
            f"Piñata Stick: Deal 3 damage to yourself to see {other.name}'s hand "
            f"({other_hand_size} cards)?\n  [True] Yes  [False] No"
        ),
    )
    return set_effect_decision(state, "pinata_stick", card_id, resolver, decision)


def _pinata_stick_resume(state, ectx, action):
    resolver = ectx.resolver
    if action.flag:
        other = resolver.other()
        # TODO: card says "deal 3 damage to the OTHER player", not self
        state = apply_damage(state, resolver, 3, DamageSource.SELF_INFLICTED)
        # In a real UI, the hand would be revealed. In our debug CLI, it's always visible.
    return state


def _fetch_stick_transfer(state, card_id, resolver):
    """weapon_1: On discard without counter, other player must wield it + add counter."""
    cs = state.card_state(card_id)
    if cs.counters == 0:
        other = resolver.other()
        ps = gs_get_player(state, resolver)
        discard = list(ps.discard_pile)
        if card_id in discard:
            discard.remove(card_id)
            ps = replace(ps, discard_pile=tuple(discard))
            state = gs_update_player(state, resolver, ps)
        from .phases.action import _wield_weapon
        state = _wield_weapon(state, other, card_id)
        state = gs_set_card_state(state, card_id, CardState(counters=1))
    return state


# --- Enemies ---

def _gobshite_fist_check(state, card_id, resolver):
    """enemy_1: If attacking with fists, treat as level 22.
    Checked during attack mode choice — stores flag in effect context."""
    return state  # Handled in attack choice presentation


def _enemy_3_discard_kills(state, card_id, resolver):
    """enemy_3: On kill with weapon, discard this and kill pile."""
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            to_discard = ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=(), parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _enemy_7_discard_weapon(state, card_id, resolver):
    """enemy_7: On kill with weapon, discard your weapon."""
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            to_discard = (ws.weapon,) + ws.kill_pile if ws.weapon else ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _lonely_ogre_wield(state, card_id, resolver):
    """enemy_8: On kill, wield this as a weapon."""
    ps = gs_get_player(state, resolver)
    # Remove from kill pile
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            kp = list(ws.kill_pile)
            kp.remove(card_id)
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=tuple(kp), parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots))
            state = gs_update_player(state, resolver, ps)
            break
    # Remove from discard
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)
    from .phases.action import _wield_weapon
    return _wield_weapon(state, resolver, card_id)


def _ba_barockus_damage(state, card_id, resolver):
    """enemy_14: On kill, take 3 damage."""
    return apply_damage(state, resolver, 3, DamageSource.CARD_EFFECT)


# --- Major Arcana ---

def _fool_event_resolve(state, card_id, resolver):
    """major_0: Resolve top card of own deck."""
    from .phases.refresh import _safe_draw
    rng = gs_get_rng(state)
    state, rng, drawn = _safe_draw(state, rng, resolver, 1)
    state = gs_with_rng_result(state, rng)
    if state.phase == Phase.GAME_OVER or not drawn:
        return state
    return _resolve_single_card(state, resolver, drawn[0], state.card_def(drawn[0]))


def _magician_choose(state, card_id, resolver):
    """major_1: Top 3 cards, player chooses 1 to resolve, refresh 2. Decision."""
    from .phases.refresh import _safe_draw
    rng = gs_get_rng(state)
    state, rng, drawn = _safe_draw(state, rng, resolver, 3)
    state = gs_with_rng_result(state, rng)
    if state.phase == Phase.GAME_OVER or not drawn:
        return state
    actions = tuple(
        Action(kind=ActionKind.SELECT_CARD, card_id=cid)
        for cid in drawn
    )
    card_descs = [f"[{_cname(state, c)}]" for c in drawn]
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.MAGICIAN_CHOOSE,
        legal_actions=actions,
        context_description=f"The Magician: Choose one to resolve, refresh the others.\n  {', '.join(card_descs)}",
        visible_cards=drawn,
    )
    return set_effect_decision(state, "magician_choose", card_id, resolver, decision,
                               data={"drawn": list(drawn)})


def _magician_resume(state, ectx, action):
    chosen = action.card_id
    drawn = ectx.data["drawn"]
    resolver = ectx.resolver
    rest = tuple(c for c in drawn if c != chosen)
    # Resolve chosen card
    state = _resolve_single_card(state, resolver, chosen, state.card_def(chosen))
    # Refresh the rest
    ps = gs_get_player(state, resolver)
    ps = ps_add_to_refresh(ps, rest)
    return gs_update_player(state, resolver, ps)


def _high_priestess(state, card_id, resolver):
    """major_2: Name up to 2 cards, check refresh pile, choose effect per match. Decision."""
    # Gather all unique card names in the game for naming options
    all_names = sorted(set(cd.name for cd in ALL_CARD_DEFS.values()))
    # Present: choose 0, 1, or 2 card names
    # For simplicity, present as: name first card (or decline), then name second (or decline)
    actions = [Action(kind=ActionKind.SELECT_INDEX, index=i) for i in range(len(all_names))]
    actions.append(Action(kind=ActionKind.DECLINE))
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.HIGH_PRIESTESS_NAME,
        legal_actions=tuple(actions),
        context_description=(
            f"The High Priestess: Name a card (by index) or DECLINE to skip.\n"
            f"  Card names: {', '.join(f'[{i}]{n}' for i, n in enumerate(all_names[:20]))}..."
        ),
    )
    return set_effect_decision(state, "high_priestess", card_id, resolver, decision,
                               data={"named": [], "all_names": all_names})


def _high_priestess_resume(state, ectx, action):
    resolver = ectx.resolver
    named = list(ectx.data["named"])
    all_names = ectx.data["all_names"]

    if action.kind == ActionKind.DECLINE:
        pass  # No more names
    elif action.kind == ActionKind.SELECT_INDEX:
        named.append(all_names[action.index])

    # If we have < 2 names and didn't decline, ask for another
    if len(named) < 2 and action.kind != ActionKind.DECLINE:
        actions = [Action(kind=ActionKind.SELECT_INDEX, index=i) for i in range(len(all_names))]
        actions.append(Action(kind=ActionKind.DECLINE))
        decision = PendingDecision(
            player=resolver,
            kind=DecisionKind.HIGH_PRIESTESS_NAME,
            legal_actions=tuple(actions),
            context_description=f"Name a second card or DECLINE. Named so far: {named}",
        )
        return set_effect_decision(state, "high_priestess", ectx.card_id, resolver, decision,
                                   data={"named": named, "all_names": all_names})

    # Check refresh pile for matches
    ps = gs_get_player(state, resolver)
    matches = 0
    for cid in ps.refresh_pile:
        cd = state.card_def(cid)
        if cd.name in named:
            matches += 1

    # For each match: choose heal 7, deal 7, or force discard equipment
    for _ in range(matches):
        # TODO: present three-way choice per match: heal 7 / deal 7 to other / force other to
        # discard a piece of equipment (or confirm they have none). Currently auto-heals 7.
        state = apply_healing(state, resolver, 7, HealSource.CARD_EFFECT)

    return state


def _chariot_take_7(state, card_id, resolver):
    """major_7: On resolve, take 7 damage."""
    return apply_damage(state, resolver, 7, DamageSource.CARD_EFFECT)


def _hermit_choice(state, card_id, resolver):
    """major_9: Optionally give 1HP, then alignment effect. Decision."""
    actions = (
        Action(kind=ActionKind.SELECT_BOOL, flag=True),   # Give 1 HP
        Action(kind=ActionKind.SELECT_BOOL, flag=False),   # Don't give
    )
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.HERMIT_CHOOSE,
        legal_actions=actions,
        context_description="The Hermit: Give the other player 1 HP?\n  [True] Yes  [False] No",
    )
    return set_effect_decision(state, "hermit_choice", card_id, resolver, decision)


def _hermit_resume(state, ectx, action):
    resolver = ectx.resolver
    rng = gs_get_rng(state)

    if action.flag:
        other = resolver.other()
        state = apply_healing(state, other, 1, HealSource.CARD_EFFECT)

    ps = gs_get_player(state, resolver)
    if ps.alignment == Alignment.GOOD:
        # Discard a piece of equipment and heal d10
        # Choose which equipment to discard (if any)
        equip_ids = [eq for eq in ps.equipment if eq is not None and eq != ps.role_card_id]
        if equip_ids:
            # TODO: present a choice of which equipment to discard; currently auto-discards first
            state = _discard_equipment_by_id(state, resolver, equip_ids[0])
        rng, roll = rng_d10(rng)
        state = gs_with_rng_result(state, rng)
        state = apply_healing(state, resolver, roll, HealSource.CARD_EFFECT)
    else:
        rng, roll = rng_d20(rng)
        state = gs_with_rng_result(state, rng)
        state = apply_damage(state, resolver, roll, DamageSource.CARD_EFFECT)

    return state


def _wheel_of_fortune(state, card_id, resolver):
    """major_10: Roll d20, set HP to result."""
    rng = gs_get_rng(state)
    rng, roll = rng_d20(rng)
    state = gs_with_rng_result(state, rng)
    return set_hp_direct(state, resolver, roll)


def _justice_damage_refresh(state, card_id, resolver):
    """major_11: Deal 5 to other, refresh this card."""
    other = resolver.other()
    state = apply_damage(state, other, 5, DamageSource.CARD_EFFECT)
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard), refresh_pile=ps.refresh_pile + (card_id,))
        state = gs_update_player(state, resolver, ps)
    return state


def _hanged_man(state, card_id, resolver):
    """major_12: Deal 5 to other, heal 7, refresh this."""
    other = resolver.other()
    state = apply_damage(state, other, 5, DamageSource.CARD_EFFECT)
    state = apply_healing(state, resolver, 7, HealSource.CARD_EFFECT)
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard), refresh_pile=ps.refresh_pile + (card_id,))
        state = gs_update_player(state, resolver, ps)
    return state


def _death_discard_adjacent(state, card_id, resolver):
    """major_13: Discard all adjacent action cards. Action Phase ends."""
    ctx = state.phase_context
    if not isinstance(ctx, ActionContext) or ctx.resolving is None:
        return state
    slot_owner = ctx.resolving.slot_owner
    slot_index = ctx.resolving.slot_index

    adjacent = []
    if slot_index > 0: adjacent.append(slot_index - 1)
    if slot_index < 3: adjacent.append(slot_index + 1)

    af = state.action_field
    ps = gs_get_player(state, resolver)
    for adj_idx in adjacent:
        af, cleared = af_clear_slot(af, slot_owner, adj_idx)
        if cleared:
            for cid in cleared:
                ps = replace(ps, discard_pile=ps.discard_pile + (cid,))
                state = gs_update_player(state, resolver, ps)
                state = fire_on_kill(state, cid, resolver)
                ps = gs_get_player(state, resolver)

    state = gs_set_action_field(state, af)
    ps = gs_get_player(state, resolver)
    ps = ps_set_action_phase_over(ps, True)
    state = gs_update_player(state, resolver, ps)

    if isinstance(state.phase_context, ActionContext):
        ctx = state.phase_context
        if ctx.resolving:
            for cid in ctx.resolving.card_queue:
                ps = gs_get_player(state, resolver)
                ps = replace(ps, discard_pile=ps.discard_pile + (cid,))
                state = gs_update_player(state, resolver, ps)
            new_res = replace(ctx.resolving, card_queue=())
            state = gs_set_context(state, replace(ctx, resolving=new_res))
    return state


def _temperance_heal(state, card_id, resolver):
    return apply_healing(state, resolver, 5, HealSource.CARD_EFFECT)


def _temperance_give_hp(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "temperance_give_hp")
    return gs_update_player(state, resolver, ps)


def _devil_gamble(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "devil_gamble")
    return gs_update_player(state, resolver, ps)


def _tower_die(state, card_id, resolver):
    return apply_damage(state, resolver, 999, DamageSource.CARD_EFFECT)


def _moon_deviation_cap(state, card_id, resolver):
    # TODO: ability is registered but _get_moon_recorded_hp in combat.py always returns None;
    # recorded HP must be persisted at Refresh Phase start and carried through Action Phase.
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "moon_deviation_cap")
    return gs_update_player(state, resolver, ps)


def _sun_force_resolve(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "sun_force_resolve")
    return gs_update_player(state, resolver, ps)


def _world_win_check(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "world_killed")
    return gs_update_player(state, resolver, ps)


def _lovers_give_hp(state, card_id, resolver):
    """major_6: Give any amount of HP to other, then take 1 damage. Decision."""
    ps = gs_get_player(state, resolver)
    max_give = ps.hp - 1  # Must survive the 1 damage after
    if max_give < 0:
        max_give = 0
    actions = tuple(
        Action(kind=ActionKind.SELECT_AMOUNT, amount=i)
        for i in range(max_give + 1)
    )
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.LOVERS_CHOOSE_HP,
        legal_actions=actions,
        context_description=f"The Lovers: Give 0–{max_give} HP to the other player (then take 1 damage).",
    )
    return set_effect_decision(state, "lovers_give_hp", card_id, resolver, decision)


def _lovers_resume(state, ectx, action):
    resolver = ectx.resolver
    amount = action.amount or 0
    if amount > 0:
        other = resolver.other()
        state = apply_damage(state, resolver, amount, DamageSource.SELF_INFLICTED)
        state = apply_healing(state, other, amount, HealSource.CARD_EFFECT)
    state = apply_damage(state, resolver, 1, DamageSource.CARD_EFFECT)
    return state


def _judgement_wield_option(state, card_id, resolver):
    # TODO: present decision to discard this from equipment and wield it as a weapon
    return state


def _judgement_single_use(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon == card_id:
            to_discard = (card_id,) + ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _strength_wield_option(state, card_id, resolver):
    # TODO: present decision to discard this from equipment and wield it as a weapon
    return state


def _strength_on_kill(state, card_id, resolver):
    """major_8: On kill with Strength, other player rolls d20 (Evil may lie). Decision."""
    other = resolver.other()
    # The other player declares a d20 result. Evil may lie.
    # Present the decision to the OTHER player.
    actions = tuple(
        Action(kind=ActionKind.SELECT_AMOUNT, amount=i)
        for i in range(1, 21)
    )
    decision = PendingDecision(
        player=other,
        kind=DecisionKind.STRENGTH_DECLARE_D20,
        legal_actions=actions,
        context_description=(
            f"Strength: Declare a d20 result (1-20). Evil may lie.\n"
            f"If result is 10 or less, a counter is placed on Strength."
        ),
    )
    return set_effect_decision(state, "strength_on_kill", card_id, resolver, decision)


def _strength_resume(state, ectx, action):
    declared = action.amount or 11
    resolver = ectx.resolver
    # Find Strength weapon
    ps = gs_get_player(state, resolver)
    for ws in ps.weapon_slots:
        if ws.weapon is not None and state.card_def(ws.weapon).name == "major_8":
            if declared <= 10:
                cs = state.card_state(ws.weapon)
                state = gs_set_card_state(state, ws.weapon, CardState(counters=cs.counters + 1))
            break
    # Also: discard the killed enemy (Strength discards enemy on kill)
    # This is handled by the card text: "discard enemy" — enemy goes to discard not kill pile
    # Actually the kill already placed the enemy in kill pile via resolve_combat;
    # the Strength text says to discard the enemy, so move it from kill pile to discard
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon is not None and state.card_def(ws.weapon).name == "major_8":
            if ws.kill_pile:
                last = ws.kill_pile[-1]
                kp = ws.kill_pile[:-1]
                weapon_slots = list(ps.weapon_slots)
                weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=kp, parity=ws.parity)
                ps = replace(ps, weapon_slots=tuple(weapon_slots),
                             discard_pile=ps.discard_pile + (last,))
                state = gs_update_player(state, resolver, ps)
            break
    return state


def _hierophant_discard(state, card_id, resolver):
    """major_5: On discard, draw to 6 cards, split hand into two piles. Decision."""
    # Draw until hand has 6 cards
    from .phases.refresh import _safe_draw
    ps = gs_get_player(state, resolver)
    rng = gs_get_rng(state)
    # ???: card says "draw until you have 6 cards in hand" — should this draw from resolver's
    # own deck or from other? Currently draws from other. Verify against rules intent.
    other = resolver.other()
    need = max(0, 6 - len(ps.hand))
    if need > 0:
        state, rng, drawn = _safe_draw(state, rng, other, need)
        state = gs_with_rng_result(state, rng)
        if state.phase == Phase.GAME_OVER:
            return state
        ps = gs_get_player(state, resolver)
        ps = replace(ps, hand=ps.hand + drawn)
        state = gs_update_player(state, resolver, ps)

    ps = gs_get_player(state, resolver)
    hand = ps.hand
    if not hand:
        return state

    # Player chooses how to split. For simplicity: choose which cards to give away.
    # Each subset of the hand is a valid split.
    # Present: select cards to give to the other player (rest go to your action field)
    # Use SELECT_AMOUNT with bitmask — or just enumerate small subsets
    # Since hand can be up to 6 cards, 2^6 = 64 subsets
    import itertools
    n = len(hand)
    actions = []
    for r in range(n + 1):
        for combo in itertools.combinations(range(n), r):
            give_indices = set(combo)
            give_cards = tuple(hand[i] for i in range(n) if i in give_indices)
            keep_cards = tuple(hand[i] for i in range(n) if i not in give_indices)
            actions.append(Action(
                kind=ActionKind.SELECT_PERMUTATION,  # Reuse for the split encoding
                permutation=tuple(sorted(give_indices)),
            ))

    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.HIEROPHANT_SPLIT,
        legal_actions=tuple(actions),
        context_description=(
            f"The Hierophant: Split your hand into two piles.\n"
            f"  Select indices to give to the other player (rest go to your action field).\n"
            f"  Hand: {', '.join(f'[{i}]{_cname(state, c)}' for i, c in enumerate(hand))}"
        ),
        visible_cards=hand,
    )
    return set_effect_decision(state, "hierophant_discard", card_id, resolver, decision,
                               data={"hand": list(hand)})


def _hierophant_resume(state, ectx, action):
    resolver = ectx.resolver
    other = resolver.other()
    hand = ectx.data["hand"]
    give_indices = set(action.permutation) if action.permutation else set()

    give_cards = tuple(hand[i] for i in range(len(hand)) if i in give_indices)
    keep_cards = tuple(hand[i] for i in range(len(hand)) if i not in give_indices)

    # Remove all from hand
    ps = gs_get_player(state, resolver)
    ps = replace(ps, hand=())
    state = gs_update_player(state, resolver, ps)

    # Place keep_cards into resolver's action slots in fill order
    # These go to the OTHER player's action field (per the rules: "place them in action slots")
    # Actually re-reading: the Hierophant owner places the OTHER player's pile in THEIR action slots
    # "split your hand into two piles and give one to the other player. Place them in your action slots in order."
    # This means: give one pile to the other player to place in the other player's action slots.
    # The kept pile goes into the resolver's action slots.
    af = state.action_field
    empty = af_find_empty_slots(af, other)
    for i, cid in enumerate(give_cards):
        if i < len(empty):
            af = af_add_card_to_slot(af, other, empty[i], cid, position="top")
    # Excess cards refreshed
    excess_give = give_cards[len(empty):]
    if excess_give:
        other_ps = gs_get_player(state, other)
        other_ps = replace(other_ps, refresh_pile=other_ps.refresh_pile + excess_give)
        state = gs_update_player(state, other, other_ps)

    empty_self = af_find_empty_slots(af, resolver)
    for i, cid in enumerate(keep_cards):
        if i < len(empty_self):
            af = af_add_card_to_slot(af, resolver, empty_self[i], cid, position="top")
    excess_keep = keep_cards[len(empty_self):]
    if excess_keep:
        ps = gs_get_player(state, resolver)
        ps = replace(ps, refresh_pile=ps.refresh_pile + excess_keep)
        state = gs_update_player(state, resolver, ps)

    state = gs_set_action_field(state, af)
    return state


def _chariot_prevent(state, card_id, resolver):
    """major_7: While equipped, may discard to prevent damage."""
    # TODO: not wired into combat.py damage pipeline; requires an interruptible decision
    # (player chooses to discard Chariot in response to any incoming damage instance)
    return state


# --- Guards ---

def _guard_respawn(state, card_id, resolver):
    if state.guard_deck:
        guard_id = state.guard_deck[0]
        state = replace(state, guard_deck=state.guard_deck[1:])
        ps = gs_get_player(state, resolver)
        ps = ps_add_to_refresh(ps, (guard_id,))
        state = gs_update_player(state, resolver, ps)
    return state


# --- Role continuous effects (structural checks) ---

def _saltine_weapon_kill(state, card_id, resolver):
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon == card_id and ws.kill_pile:
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=(), parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + ws.kill_pile)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _saltine_weapon_eat(state, card_id, resolver):
    """Saltine as weapon: on discard, may eat. Decision."""
    ps = gs_get_player(state, resolver)
    if ps.has_eaten_this_phase:
        return state  # Can't eat again
    cd = state.card_def(card_id)
    if cd.level is None:
        return state
    actions = (
        Action(kind=ActionKind.SELECT_BOOL, flag=True),
        Action(kind=ActionKind.SELECT_BOOL, flag=False),
    )
    decision = PendingDecision(
        player=resolver,
        kind=DecisionKind.VOLUNTARY_DISCARD,
        legal_actions=actions,
        context_description=f"Saltine Shuriken discarded: Eat it to heal {cd.level}?\n  [True] Yes  [False] No",
    )
    return set_effect_decision(state, "saltine_weapon_eat", card_id, resolver, decision)


def _saltine_weapon_eat_resume(state, ectx, action):
    if action.flag:
        resolver = ectx.resolver
        card_id = ectx.card_id
        cd = state.card_def(card_id)
        if cd.level is not None:
            state = apply_healing(state, resolver, cd.level, HealSource.FOOD)
            ps = gs_get_player(state, resolver)
            ps = replace(ps, has_eaten_this_phase=True)
            state = gs_update_player(state, resolver, ps)
    return state


def _fat_sandwich_eat(state, card_id, resolver):
    # ???: "While equipped: You may discard this to eat this." The voluntary discard
    # option during Action Phase needs to be explicitly offered when Fat Sandwich is equipped.
    return state


# ---------------------------------------------------------------------------
# Helper: resolve a single card (used by Fool, Magician nested resolution)
# ---------------------------------------------------------------------------

def _resolve_single_card(state, resolver, card_id, cd):
    state = fire_on_resolve(state, card_id, resolver)
    ps = gs_get_player(state, resolver)
    if ps.is_dead:
        return state

    if is_enemy_like(cd):
        options = get_attack_options(state, resolver, card_id)
        mode, slot_idx = "fists", 0
        for m, idx in options:
            if m != "fists":
                mode, slot_idx = "weapon", idx
                break
        state = resolve_combat(state, resolver, card_id, mode, slot_idx)
        ps = gs_get_player(state, resolver)
        if not ps.is_dead:
            state = fire_on_kill(state, card_id, resolver)
            if CardType.BOSS in cd.card_types:
                state = fire_after_death(state, card_id, resolver)
    elif is_food(cd):
        from .phases.action import _card_is_placed
        if not _card_is_placed(state, resolver, card_id):
            ps = gs_get_player(state, resolver)
            if not ps.has_eaten_this_phase and cd.level is not None:
                state = apply_healing(state, resolver, cd.level, HealSource.FOOD)
                ps = gs_get_player(state, resolver)
                ps = replace(ps, has_eaten_this_phase=True)
                state = gs_update_player(state, resolver, ps)
            ps = gs_get_player(state, resolver)
            ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
            state = gs_update_player(state, resolver, ps)
    elif is_weapon(cd) and not is_equipment(cd):
        from .phases.action import _wield_weapon
        state = _wield_weapon(state, resolver, card_id)
    elif is_equipment(cd):
        from .phases.action import _equip_card
        state = _equip_card(state, resolver, card_id)
    elif is_event(cd):
        ps = gs_get_player(state, resolver)
        ps = replace(ps, discard_pile=ps.discard_pile + (card_id,))
        state = gs_update_player(state, resolver, ps)

    state = fire_on_resolve_after(state, card_id, resolver)
    return state


# ---------------------------------------------------------------------------
# Handler + Resume registries
# ---------------------------------------------------------------------------

HANDLER_REGISTRY: dict[str, Any] = {
    # Food
    "food_1_d10_damage": _food_1_d10_damage,
    "saltine_choice": _saltine_choice,
    "saltine_weapon_kill": _saltine_weapon_kill,
    "saltine_weapon_eat": _saltine_weapon_eat,
    "fat_sandwich_equip": _fat_sandwich_equip,
    "fat_sandwich_eat": _fat_sandwich_eat,
    "bellyfiller_equip": _bellyfiller_equip,
    # Weapons
    "vorpal_blade_discard": _vorpal_blade_discard,
    "pinata_stick": _pinata_stick,
    "fetch_stick_transfer": _fetch_stick_transfer,
    # Enemies
    "gobshite_fist_check": _gobshite_fist_check,
    "enemy_3_discard_kills": _enemy_3_discard_kills,
    "enemy_7_discard_weapon": _enemy_7_discard_weapon,
    "lonely_ogre_wield": _lonely_ogre_wield,
    "ba_barockus_damage": _ba_barockus_damage,
    # Major arcana
    "fool_event_resolve": _fool_event_resolve,
    "magician_choose": _magician_choose,
    "high_priestess": _high_priestess,
    "chariot_take_7": _chariot_take_7,
    "chariot_prevent": _chariot_prevent,
    "hermit_choice": _hermit_choice,
    "wheel_of_fortune": _wheel_of_fortune,
    "justice_damage_refresh": _justice_damage_refresh,
    "hanged_man": _hanged_man,
    "death_discard_adjacent": _death_discard_adjacent,
    "temperance_heal": _temperance_heal,
    "temperance_give_hp": _temperance_give_hp,
    "devil_gamble": _devil_gamble,
    "tower_die": _tower_die,
    "moon_deviation_cap": _moon_deviation_cap,
    "sun_force_resolve": _sun_force_resolve,
    "world_win_check": _world_win_check,
    "lovers_give_hp": _lovers_give_hp,
    "judgement_wield_option": _judgement_wield_option,
    "judgement_single_use": _judgement_single_use,
    "strength_wield_option": _strength_wield_option,
    "strength_on_kill": _strength_on_kill,
    "hierophant_discard": _hierophant_discard,
    # Guards
    "guard_respawn": _guard_respawn,
    # --- Structural no-ops: logic lives in another module, handler correctly does nothing ---
    "cardsharp_no_consent_needed": _noop,   # enforced in action.py player_is_cardsharp check
    "guard_prevent_run": _noop,             # enforced in action.py _can_run via has_trigger check
    "guard_draw_underneath": _noop,         # handled in refresh.py _safe_draw
    "skeleton_draw_underneath": _noop,      # handled in refresh.py _safe_draw
    "mutineer_setup_discard": _noop,        # handled in setup.py _apply_role_setup
    "fool_role_setup": _noop,               # handled in setup.py _apply_role_setup
    "leo_setup": _noop,                     # handled in setup.py _apply_role_setup
    "two_armed_freak_setup": _noop,         # handled in setup.py _apply_role_setup
    "empress_heal": _noop,                  # handled in refresh.py _step_periodic_effects
    "bellyfiller_heal": _noop,              # handled in refresh.py _step_periodic_effects
    "corruption_heal": _noop,               # handled in refresh.py _step_periodic_effects
    "phoenix_tick": _noop,                  # handled in refresh.py _step_periodic_effects
    "survivor_counter_damage": _noop,       # handled in refresh.py _step_periodic_effects
    "star_revive": _noop,                   # handled in combat.py _apply_lethal_damage
    "leo_revive": _noop,                    # handled in combat.py _apply_lethal_damage
    "emperor_weapon_boost": _noop,          # handled in combat.py resolve_combat weapon attack

    # --- TODO: not implemented anywhere ---
    "weapon_7_no_distance": _noop,          # TODO: distance penalty waiver not applied (see action.py:689); enemy-on-other-field discard also missing
    "food_fighter_swap": _noop,             # TODO: Foo(d) Fighter swap wield↔eat not enforced at wield/eat call sites
    "corruption_invert_healing": _noop,     # TODO: apply_healing not wired to check Corruption; only periodic healing is inverted in refresh.py
    "phoenix_no_give_hp": _noop,            # TODO: Lovers/Hermit/Temperance give-HP paths don't check Phoenix restriction
    "fool_role_redirect": _noop,            # TODO: The Fool role card should refresh instead of discard, and refresh on weapon discard
    "survivor_extra_action": _noop,         # TODO: counter-place/resolve-top action not offered in Action Phase
    "ocean_counter_mechanic": _noop,        # TODO: Ocean counter/guard-spawn mechanic not implemented
    "ocean_no_guards": _noop,               # TODO: Call Guards last resort not blocked for Ocean role
    "detective_no_guards": _noop,           # TODO: Call Guards last resort not blocked for Detective role
    "detective_view_deck": _noop,           # TODO: on discard, offer view of own deck + refresh pile
    "poet_refresh_enemy": _noop,            # TODO: when fighting non-guard enemy, offer refresh instead
    "poet_weapon_fragile": _noop,           # TODO: Poet's weapons should discard after first kill
    "world_role_self_destruct": _noop,      # TODO: player dies if The World boss dies on their action field
    "world_role_redirect_kill": _noop,      # TODO: while equipped, offer redirect of killed non-guard to other's refresh
    "human_call_guards": _noop,             # TODO: Human Last Resort is distinct: disarm other player then stack a guard on each of their action slots
}

RESUME_REGISTRY: dict[str, Any] = {
    "saltine_choice": _saltine_choice_resume,
    "magician_choose": _magician_resume,
    "high_priestess": _high_priestess_resume,
    "hermit_choice": _hermit_resume,
    "lovers_give_hp": _lovers_resume,
    "pinata_stick": _pinata_stick_resume,
    "strength_on_kill": _strength_resume,
    "hierophant_discard": _hierophant_resume,
    "saltine_weapon_eat": _saltine_weapon_eat_resume,
}