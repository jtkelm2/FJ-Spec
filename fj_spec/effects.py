"""
Fools' Journey — Executable Spec
Stage 8: Card effect handlers.

This module implements all card-specific effect handlers. Handlers are
pure functions: (GameState, CardId, PlayerId, **kwargs) → GameState.

Handlers are dispatched from the resolution pipeline in action.py via
the fire_effects() function, which looks up handlers by trigger type
on the card definition.

Some handlers need player decisions — they return the state with a
pending decision set. The action phase loop will resume the handler
after the decision is resolved.

For this stage, we implement the deterministic handlers inline and
mark decision-requiring handlers with a simplified auto-resolution
(the full decision presentation will be refined as needed).
"""

from __future__ import annotations

from dataclasses import replace

from .types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Alignment, Phase, Parity,
    PlayerState, WeaponSlot, ManipulationField,
    ActionSlot, ActionField,
    GameState, Action, ActionKind,
    PendingDecision, DecisionKind,
    ActionContext, ActionStep, ResolutionContext,
    Trigger,
    ACTION_FILL_ORDER,
)
from .cards import (
    has_trigger, get_handlers_for_trigger,
    is_enemy_like, is_food, is_weapon, is_equipment, is_event,
    get_card_def,
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

def fire_on_resolve(
    state: GameState, card_id: CardId, resolver: PlayerId
) -> GameState:
    """Fire all ON_RESOLVE handlers for a card."""
    cd = state.card_def(card_id)
    for handler_name in get_handlers_for_trigger(cd, Trigger.ON_RESOLVE):
        fn = HANDLER_REGISTRY.get(handler_name)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_resolve_after(
    state: GameState, card_id: CardId, resolver: PlayerId
) -> GameState:
    """Fire all ON_RESOLVE_AFTER handlers."""
    cd = state.card_def(card_id)
    for handler_name in get_handlers_for_trigger(cd, Trigger.ON_RESOLVE_AFTER):
        fn = HANDLER_REGISTRY.get(handler_name)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_kill(
    state: GameState, card_id: CardId, resolver: PlayerId
) -> GameState:
    """Fire ON_KILL handlers when a card is killed/discarded during Action Phase."""
    cd = state.card_def(card_id)
    for handler_name in get_handlers_for_trigger(cd, Trigger.ON_KILL):
        fn = HANDLER_REGISTRY.get(handler_name)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_on_discard(
    state: GameState, card_id: CardId, resolver: PlayerId
) -> GameState:
    """Fire ON_DISCARD handlers when a card is discarded during Action Phase."""
    cd = state.card_def(card_id)
    for handler_name in get_handlers_for_trigger(cd, Trigger.ON_DISCARD):
        fn = HANDLER_REGISTRY.get(handler_name)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def fire_after_death(
    state: GameState, card_id: CardId, resolver: PlayerId
) -> GameState:
    """Fire AFTER_DEATH handlers — grant permanent abilities to the resolver."""
    cd = state.card_def(card_id)
    for handler_name in get_handlers_for_trigger(cd, Trigger.AFTER_DEATH):
        fn = HANDLER_REGISTRY.get(handler_name)
        if fn:
            state = fn(state, card_id, resolver)
    return state


def check_continuous(state: GameState, handler_name: str, pid: PlayerId) -> bool:
    """Check if a continuous effect is active for a player (role-based)."""
    ps = gs_get_player(state, pid)
    role_name = ps.role_def_name
    if role_name is None:
        return False
    role_def = get_card_def(role_name)
    return handler_name in [e.handler for e in role_def.effects if e.trigger == Trigger.CONTINUOUS]


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

Handler = type(lambda s, c, p: s)  # function signature hint


def _handler_noop(state: GameState, card_id: CardId, resolver: PlayerId) -> GameState:
    """No-op handler for effects that are checked structurally elsewhere."""
    return state


# --- Food effects ---

def _food_1_d10_damage(state, card_id, resolver):
    """food_1: After eating, receive d10 damage."""
    rng = gs_get_rng(state)
    rng, roll = rng_d10(rng)
    state = gs_with_rng_result(state, rng)
    state = apply_damage(state, resolver, roll, DamageSource.FOOD_PENALTY)
    return state


def _saltine_choice(state, card_id, resolver):
    """food_3 (Saltine Shuriken): May wield as weapon instead of eating.
    Auto-resolution: eat as food (default behavior). Stage 8+ can present choice."""
    # Default: eat as food (handled by normal food processing)
    return state


def _fat_sandwich_equip(state, card_id, resolver):
    """food_7: Equip instead of eating."""
    # Override food processing: equip the card instead
    # The caller should NOT eat this card; instead equip it
    # We signal this by equipping and skipping the food heal
    ps = gs_get_player(state, resolver)
    # Remove from discard if food processing already added it
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)
    # Un-set eaten flag if it was set
    ps = gs_get_player(state, resolver)
    # Equip
    from .phases.action import _equip_card
    state = _equip_card(state, resolver, card_id)
    return state


def _bellyfiller_equip(state, card_id, resolver):
    """food_9 (Bellyfiller): Equip instead of eating."""
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)
    from .phases.action import _equip_card
    state = _equip_card(state, resolver, card_id)
    return state


# --- Weapon effects ---

def _vorpal_blade_discard(state, card_id, resolver):
    """weapon_10: On discard, refresh all action cards, Action Phase over."""
    state = _refresh_player_action_field(state, resolver)
    ps = gs_get_player(state, resolver)
    ps = ps_set_action_phase_over(ps, True)
    state = gs_update_player(state, resolver, ps)
    return state


def _pinata_stick(state, card_id, resolver):
    """weapon_3: On discard, may deal 3 damage to other to see their hand.
    Auto-resolution: decline (no damage)."""
    return state


def _fetch_stick_transfer(state, card_id, resolver):
    """weapon_1: On discard without counter, other player must wield it.
    Auto-resolution: transfer to other player."""
    cs = state.card_state(card_id)
    if cs.counters == 0:
        other = resolver.other()
        # Remove from resolver's discard
        ps = gs_get_player(state, resolver)
        discard = list(ps.discard_pile)
        if card_id in discard:
            discard.remove(card_id)
            ps = replace(ps, discard_pile=tuple(discard))
            state = gs_update_player(state, resolver, ps)
        # Other player wields it with a counter
        from .phases.action import _wield_weapon
        state = _wield_weapon(state, other, card_id)
        state = gs_set_card_state(state, card_id, CardState(counters=1))
    return state


def _weapon_7_no_distance(state, card_id, resolver):
    """weapon_7: No distance penalty, cross-field kills discard enemy.
    This is a continuous AS_WEAPON effect checked during combat/consent."""
    return state


# --- Enemy effects ---

def _gobshite_fist_check(state, card_id, resolver):
    """enemy_1: If attacking with fists, treat as level 22.
    This is checked during combat — modifies effective level."""
    return state


def _enemy_3_discard_kills(state, card_id, resolver):
    """enemy_3: On kill with weapon, discard this and kill pile."""
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            # Discard everything in this kill pile
            to_discard = ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=(), parity=ws.parity)
            ps = replace(ps,
                         weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _enemy_7_discard_weapon(state, card_id, resolver):
    """enemy_7: On kill with weapon, discard your weapon."""
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            # Discard this weapon + kill pile
            to_discard = (ws.weapon,) + ws.kill_pile if ws.weapon else ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(parity=ws.parity)
            ps = replace(ps,
                         weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _lonely_ogre_wield(state, card_id, resolver):
    """enemy_8: On kill, wield this as a weapon."""
    # Remove from kill pile or discard first
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if card_id in ws.kill_pile:
            kp = list(ws.kill_pile)
            kp.remove(card_id)
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=tuple(kp), parity=ws.parity)
            ps = replace(ps, weapon_slots=tuple(weapon_slots))
            state = gs_update_player(state, resolver, ps)
            break
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = gs_get_player(state, resolver)
        ps = replace(ps, discard_pile=tuple(discard))
        state = gs_update_player(state, resolver, ps)

    from .phases.action import _wield_weapon
    state = _wield_weapon(state, resolver, card_id)
    return state


def _ba_barockus_damage(state, card_id, resolver):
    """enemy_14: On kill, take 3 damage."""
    state = apply_damage(state, resolver, 3, DamageSource.CARD_EFFECT)
    return state


# --- Major arcana effects ---

def _fool_event_resolve(state, card_id, resolver):
    """major_0 (The Fool): Resolve top card of own deck."""
    from .phases.refresh import _safe_draw
    rng = gs_get_rng(state)
    state, rng, drawn = _safe_draw(state, rng, resolver, 1)
    state = gs_with_rng_result(state, rng)
    if state.phase == Phase.GAME_OVER or not drawn:
        return state
    drawn_id = drawn[0]
    drawn_cd = state.card_def(drawn_id)
    # Resolve the drawn card (may recurse!)
    state = _resolve_single_card(state, resolver, drawn_id, drawn_cd)
    return state


def _magician_choose(state, card_id, resolver):
    """major_1 (The Magician): Top 3 cards, choose 1 to resolve, refresh 2.
    Auto-resolution: resolve first card, refresh rest."""
    from .phases.refresh import _safe_draw
    rng = gs_get_rng(state)
    state, rng, drawn = _safe_draw(state, rng, resolver, 3)
    state = gs_with_rng_result(state, rng)
    if state.phase == Phase.GAME_OVER or not drawn:
        return state
    # Auto: resolve first, refresh rest
    chosen = drawn[0]
    rest = drawn[1:]
    chosen_cd = state.card_def(chosen)
    state = _resolve_single_card(state, resolver, chosen, chosen_cd)
    # Refresh the rest
    ps = gs_get_player(state, resolver)
    ps = ps_add_to_refresh(ps, rest)
    state = gs_update_player(state, resolver, ps)
    return state


def _high_priestess(state, card_id, resolver):
    """major_2: Name cards, check refresh pile, choose effects per match.
    Auto-resolution: skip (no names chosen)."""
    return state


def _chariot_take_7(state, card_id, resolver):
    """major_7: On resolve, take 7 damage."""
    state = apply_damage(state, resolver, 7, DamageSource.CARD_EFFECT)
    return state


def _hermit_choice(state, card_id, resolver):
    """major_9: Optionally give 1HP, then Good: discard equip + heal d10, Evil: take d20.
    Auto-resolution: don't give HP, then apply alignment effect."""
    ps = gs_get_player(state, resolver)
    rng = gs_get_rng(state)
    if ps.alignment == Alignment.GOOD:
        # Discard a non-role equipment if available
        for eq_id in ps.equipment:
            if eq_id is not None and eq_id != ps.role_card_id:
                state = _discard_equipment_by_id(state, resolver, eq_id)
                break
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
    state = set_hp_direct(state, resolver, roll)
    return state


def _justice_damage_refresh(state, card_id, resolver):
    """major_11: Deal 5 to other, refresh this card."""
    other = resolver.other()
    state = apply_damage(state, other, 5, DamageSource.CARD_EFFECT)
    # Move from discard to refresh (it was already discarded by event processing)
    ps = gs_get_player(state, resolver)
    discard = list(ps.discard_pile)
    if card_id in discard:
        discard.remove(card_id)
        ps = replace(ps, discard_pile=tuple(discard),
                     refresh_pile=ps.refresh_pile + (card_id,))
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
        ps = replace(ps, discard_pile=tuple(discard),
                     refresh_pile=ps.refresh_pile + (card_id,))
        state = gs_update_player(state, resolver, ps)
    return state


def _death_discard_adjacent(state, card_id, resolver):
    """major_13 (Death): Discard all adjacent action cards. Action Phase ends."""
    # Find which slot this was in from the resolution context
    ctx = state.phase_context
    if not isinstance(ctx, ActionContext) or ctx.resolving is None:
        return state
    slot_owner = ctx.resolving.slot_owner
    slot_index = ctx.resolving.slot_index

    # Adjacent slots: index ± 1 (within 0-3)
    adjacent = []
    if slot_index > 0:
        adjacent.append(slot_index - 1)
    if slot_index < 3:
        adjacent.append(slot_index + 1)

    af = state.action_field
    ps = gs_get_player(state, resolver)
    for adj_idx in adjacent:
        af, cleared = af_clear_slot(af, slot_owner, adj_idx)
        if cleared:
            # On Kill triggers for discarded cards during Action Phase
            for cid in cleared:
                ps = replace(ps, discard_pile=ps.discard_pile + (cid,))
                state = gs_update_player(state, resolver, ps)
                state = fire_on_kill(state, cid, resolver)
                ps = gs_get_player(state, resolver)

    state = gs_set_action_field(state, af)

    # End Action Phase
    ps = gs_get_player(state, resolver)
    ps = ps_set_action_phase_over(ps, True)
    state = gs_update_player(state, resolver, ps)

    # Also clear remaining cards in resolution queue
    if isinstance(state.phase_context, ActionContext):
        ctx = state.phase_context
        if ctx.resolving:
            # Discard remaining queued cards too
            for cid in ctx.resolving.card_queue:
                ps = gs_get_player(state, resolver)
                ps = replace(ps, discard_pile=ps.discard_pile + (cid,))
                state = gs_update_player(state, resolver, ps)
            new_res = replace(ctx.resolving, card_queue=())
            state = gs_set_context(state, replace(ctx, resolving=new_res))
    return state


def _temperance_heal(state, card_id, resolver):
    """major_14 (Temperance): On kill, heal 5."""
    state = apply_healing(state, resolver, 5, HealSource.CARD_EFFECT)
    return state


def _temperance_give_hp(state, card_id, resolver):
    """major_14: After death, permanently gain ability to give HP."""
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "temperance_give_hp")
    state = gs_update_player(state, resolver, ps)
    return state


def _devil_gamble(state, card_id, resolver):
    """major_15: After death, gain Devil gamble ability."""
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "devil_gamble")
    state = gs_update_player(state, resolver, ps)
    return state


def _tower_die(state, card_id, resolver):
    """major_16 (The Tower): You die!"""
    state = apply_damage(state, resolver, 999, DamageSource.CARD_EFFECT)
    return state


def _moon_deviation_cap(state, card_id, resolver):
    """major_18: After death, gain Moon deviation cap."""
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "moon_deviation_cap")
    state = gs_update_player(state, resolver, ps)
    return state


def _sun_force_resolve(state, card_id, resolver):
    """major_19: After death, gain Sun forced-resolve ability."""
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "sun_force_resolve")
    state = gs_update_player(state, resolver, ps)
    return state


def _world_win_check(state, card_id, resolver):
    """major_21: After death, grant World-killed ability for win check."""
    ps = gs_get_player(state, resolver)
    ps = ps_add_permanent_ability(ps, "world_killed")
    state = gs_update_player(state, resolver, ps)
    return state


def _lovers_give_hp(state, card_id, resolver):
    """major_6: Give any amount of HP to other, then take 1 damage.
    Auto-resolution: give 0 HP, take 1 damage."""
    state = apply_damage(state, resolver, 1, DamageSource.CARD_EFFECT)
    return state


def _judgement_wield_option(state, card_id, resolver):
    """major_20: While equipped, may discard to wield as weapon.
    This is a WHILE_EQUIPPED continuous — checked via voluntary discard."""
    return state


def _judgement_single_use(state, card_id, resolver):
    """major_20: As a weapon, discards after one use."""
    # After any kill with Judgement, discard it
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon == card_id:
            to_discard = (card_id,) + ws.kill_pile
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(parity=ws.parity)
            ps = replace(ps,
                         weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + to_discard)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _strength_wield_option(state, card_id, resolver):
    """major_8: While equipped, may discard to wield. Continuous check."""
    return state


def _strength_on_kill(state, card_id, resolver):
    """major_8: On kill with Strength weapon, discard enemy, d20 roll, counter logic.
    Auto-resolution: assume d20 result > 10 (no counter added)."""
    return state


def _hierophant_discard(state, card_id, resolver):
    """major_5: On discard, draw to 6 cards, split hand, place in action.
    Auto-resolution: skip (complex sub-phase, to be fully implemented)."""
    return state


def _chariot_prevent(state, card_id, resolver):
    """major_7: While equipped, may discard to prevent damage.
    This is checked during the damage pipeline (interrupt)."""
    return state


# --- Guard effects ---

def _guard_respawn(state, card_id, resolver):
    """guards: After death, draw another guard into refresh pile."""
    if state.guard_deck:
        guard_id = state.guard_deck[0]
        state = replace(state, guard_deck=state.guard_deck[1:])
        ps = gs_get_player(state, resolver)
        ps = ps_add_to_refresh(ps, (guard_id,))
        state = gs_update_player(state, resolver, ps)
    return state


# --- Role continuous effects (mostly structural checks, handled elsewhere) ---

def _food_fighter_swap(state, card_id, resolver):
    """Foo(d) Fighter: weapon↔food type swap. Structural check."""
    return state


def _corruption_invert_healing(state, card_id, resolver):
    """Corruption: healing inversion. Handled in combat.py pipeline."""
    return state


def _phoenix_no_give_hp(state, card_id, resolver):
    """Phoenix: cannot give HP. Checked when presenting give-HP options."""
    return state


def _fool_role_redirect(state, card_id, resolver):
    """Fool role: Fool events refresh instead of discard. Checked during discard."""
    return state


def _survivor_extra_action(state, card_id, resolver):
    """Survivor: extra action option. Checked when presenting action choices."""
    return state


def _ocean_counter_mechanic(state, card_id, resolver):
    """Ocean: counter-based guard spawning. Handled in refresh periodic."""
    return state


def _poet_refresh_enemy(state, card_id, resolver):
    """Poet: may refresh non-guard enemy instead of fighting. Checked in combat."""
    return state


def _poet_weapon_fragile(state, card_id, resolver):
    """Poet: weapons discard on first use. Checked after combat."""
    return state


def _world_role_self_destruct(state, card_id, resolver):
    """World role: if The World boss dies on your field, you die. Checked on boss death."""
    return state


def _world_role_redirect_kill(state, card_id, resolver):
    """World role: while equipped, redirect non-guard kills. Checked in combat."""
    return state


def _detective_view_deck(state, card_id, resolver):
    """Detective: on discard, view entire deck and refresh pile.
    In a digital game this auto-reveals. No mechanical state change."""
    return state


def _saltine_weapon_kill(state, card_id, resolver):
    """Saltine Shuriken as weapon: discard all slain enemies on kill."""
    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon == card_id and ws.kill_pile:
            weapon_slots = list(ps.weapon_slots)
            weapon_slots[i] = WeaponSlot(weapon=ws.weapon, kill_pile=(), parity=ws.parity)
            ps = replace(ps,
                         weapon_slots=tuple(weapon_slots),
                         discard_pile=ps.discard_pile + ws.kill_pile)
            state = gs_update_player(state, resolver, ps)
            break
    return state


def _saltine_weapon_eat(state, card_id, resolver):
    """Saltine Shuriken: on weapon discard, may eat this.
    Auto-resolution: eat it if not eaten this phase."""
    ps = gs_get_player(state, resolver)
    if not ps.has_eaten_this_phase:
        cd = state.card_def(card_id)
        if cd.level is not None:
            state = apply_healing(state, resolver, cd.level, HealSource.FOOD)
            ps = gs_get_player(state, resolver)
            ps = replace(ps, has_eaten_this_phase=True)
            state = gs_update_player(state, resolver, ps)
    return state


def _fat_sandwich_eat(state, card_id, resolver):
    """Fat Sandwich: while equipped, may discard to eat.
    This is a WHILE_EQUIPPED action option — checked via voluntary discard."""
    return state


# ---------------------------------------------------------------------------
# Helper: resolve a single card (used by Fool, Magician nested resolution)
# ---------------------------------------------------------------------------

def _resolve_single_card(
    state: GameState, resolver: PlayerId,
    card_id: CardId, cd: CardDef,
) -> GameState:
    """Resolve a card drawn from the deck (by Fool, Magician, etc.)."""
    # Fire ON_RESOLVE effects
    state = fire_on_resolve(state, card_id, resolver)

    ps = gs_get_player(state, resolver)
    if ps.is_dead:
        return state

    # Default type processing
    if is_enemy_like(cd):
        options = get_attack_options(state, resolver, card_id)
        mode = "fists"
        slot_idx = 0
        for m, idx in options:
            if m != "fists":
                mode = "weapon"
                slot_idx = idx
                break
        state = resolve_combat(state, resolver, card_id, mode, slot_idx)
        ps = gs_get_player(state, resolver)
        if not ps.is_dead:
            state = fire_on_kill(state, card_id, resolver)
    elif is_food(cd):
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

    # Fire ON_RESOLVE_AFTER effects
    state = fire_on_resolve_after(state, card_id, resolver)

    return state


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLER_REGISTRY: dict[str, Handler] = {
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
    "weapon_7_no_distance": _weapon_7_no_distance,

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

    # Role continuous (structural checks — mostly no-ops here)
    "cardsharp_no_consent_needed": _handler_noop,
    "food_fighter_swap": _food_fighter_swap,
    "corruption_invert_healing": _corruption_invert_healing,
    "phoenix_no_give_hp": _phoenix_no_give_hp,
    "fool_role_redirect": _fool_role_redirect,
    "survivor_extra_action": _survivor_extra_action,
    "ocean_counter_mechanic": _ocean_counter_mechanic,
    "ocean_no_guards": _handler_noop,
    "detective_no_guards": _handler_noop,
    "detective_view_deck": _detective_view_deck,
    "poet_refresh_enemy": _poet_refresh_enemy,
    "poet_weapon_fragile": _poet_weapon_fragile,
    "world_role_self_destruct": _world_role_self_destruct,
    "world_role_redirect_kill": _world_role_redirect_kill,
    "human_call_guards": _handler_noop,
    "guard_prevent_run": _handler_noop,

    # Setup/periodic (handled elsewhere, but registered for completeness)
    "mutineer_setup_discard": _handler_noop,
    "fool_role_setup": _handler_noop,
    "leo_setup": _handler_noop,
    "two_armed_freak_setup": _handler_noop,
    "empress_heal": _handler_noop,
    "bellyfiller_heal": _handler_noop,
    "corruption_heal": _handler_noop,
    "phoenix_tick": _handler_noop,
    "survivor_counter_damage": _handler_noop,
    "skeleton_draw_underneath": _handler_noop,
    "guard_draw_underneath": _handler_noop,
    "star_revive": _handler_noop,
    "leo_revive": _handler_noop,
    "emperor_weapon_boost": _handler_noop,
}