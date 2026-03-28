#!/usr/bin/env python3
"""Stage 8 validation: Card effect handlers."""

from dataclasses import replace

from fj_spec.types import (
    PlayerId, Alignment, Phase, CardType, CardState, Parity,
    ActionSlot, ActionField, WeaponSlot,
    PlayerState, GameState,
    ActionContext, ActionStep, ResolutionContext,
    PendingDecision, DecisionKind, Action, ActionKind,
)
from fj_spec.cards import get_card_def, ALL_CARD_DEFS, is_enemy_like
from fj_spec.setup import create_initial_state, player_is_corruption
from fj_spec.state_helpers import (
    gs_get_player, gs_update_player, gs_modify_player,
    gs_set_action_field, gs_set_context, gs_set_card_state,
    gs_get_rng, gs_with_rng_result,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_equipment,
    ps_set_weapon_slots, ps_set_hp_cap,
    ps_add_to_refresh, ps_add_to_discard, ps_add_permanent_ability,
    af_add_card_to_slot, af_get_slot, af_set_slot, af_clear_slot,
)
from fj_spec.combat import (
    apply_damage, apply_healing, resolve_combat, DamageSource, HealSource,
)
from fj_spec.effects import (
    fire_on_resolve, fire_on_resolve_after, fire_on_kill, fire_after_death,
    HANDLER_REGISTRY, _resolve_single_card,
)


def find_in_deck(state, pid, name):
    ps = gs_get_player(state, pid)
    for cid in ps.deck:
        if state.card_def(cid).name == name:
            return cid
    return None


def remove_from_deck(state, pid, cid):
    ps = gs_get_player(state, pid)
    deck = list(ps.deck)
    deck.remove(cid)
    ps = replace(ps, deck=tuple(deck))
    return gs_update_player(state, pid, ps)


def equip_weapon(state, pid, name, kill_pile_names=None):
    cid = find_in_deck(state, pid, name)
    if cid is None:
        return state, None
    state = remove_from_deck(state, pid, cid)
    ps = gs_get_player(state, pid)
    kp = ()
    if kill_pile_names:
        for kn in kill_pile_names:
            kid = find_in_deck(state, pid, kn)
            if kid:
                state = remove_from_deck(state, pid, kid)
                ps = gs_get_player(state, pid)
                kp = kp + (kid,)
    ws = WeaponSlot(weapon=cid, kill_pile=kp)
    ps = gs_get_player(state, pid)
    ps = replace(ps, weapon_slots=(ws,))
    return gs_update_player(state, pid, ps), cid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_handler_registry_complete():
    print("Testing handler registry completeness...")
    all_handlers = set()
    for cd in ALL_CARD_DEFS.values():
        for e in cd.effects:
            all_handlers.add(e.handler)

    missing = all_handlers - set(HANDLER_REGISTRY.keys())
    assert len(missing) == 0, f"Missing handlers: {missing}"
    print(f"  All {len(all_handlers)} handlers registered")
    print("  PASS")


def test_food_1_d10_damage():
    print("Testing food_1: after eating, take d10 damage...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    food_id = find_in_deck(state, pid, "food_1")
    assert food_id is not None

    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 20))
    state = remove_from_deck(state, pid, food_id)

    # Fire the ON_RESOLVE_AFTER handler directly
    state = fire_on_resolve_after(state, food_id, pid)

    ps = gs_get_player(state, pid)
    assert ps.hp < 20, f"Should have taken d10 damage, HP={ps.hp}"
    assert ps.hp >= 10, f"d10 damage should be 1-10, HP={ps.hp}"

    print(f"  food_1: took {20 - ps.hp} damage (d10)")
    print("  PASS")


def test_chariot_take_7():
    print("Testing Chariot ON_RESOLVE: take 7 damage...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    chariot_id = find_in_deck(state, pid, "major_7")
    assert chariot_id is not None

    state = fire_on_resolve(state, chariot_id, pid)

    ps = gs_get_player(state, pid)
    assert ps.hp == 13, f"Chariot should deal 7 damage: expected 13, got {ps.hp}"

    print("  Chariot: 7 damage (20→13)")
    print("  PASS")


def test_wheel_of_fortune():
    print("Testing Wheel of Fortune: set HP to d20...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    wof_id = find_in_deck(state, pid, "major_10")
    assert wof_id is not None

    state = fire_on_resolve(state, wof_id, pid)
    ps = gs_get_player(state, pid)
    assert 1 <= ps.hp <= 20, f"Wheel should set HP to d20 (1-20), got {ps.hp}"

    print(f"  Wheel of Fortune: HP set to {ps.hp}")
    print("  PASS")


def test_justice_damage_and_refresh():
    print("Testing Justice: 5 damage to other, refresh self...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED
    other = PlayerId.BLUE

    justice_id = find_in_deck(state, pid, "major_11")
    assert justice_id is not None
    state = remove_from_deck(state, pid, justice_id)

    # Put Justice in discard (as if event processing discarded it)
    ps = gs_get_player(state, pid)
    ps = replace(ps, discard_pile=ps.discard_pile + (justice_id,))
    state = gs_update_player(state, pid, ps)

    state = fire_on_resolve(state, justice_id, pid)

    other_ps = gs_get_player(state, other)
    assert other_ps.hp == 15, f"Other should take 5 damage: {other_ps.hp}"

    # Justice should be in refresh, not discard
    ps = gs_get_player(state, pid)
    assert justice_id in ps.refresh_pile, "Justice should be in refresh pile"
    assert justice_id not in ps.discard_pile, "Justice should not be in discard"

    print("  Justice: 5 to other, self refreshed")
    print("  PASS")


def test_hanged_man():
    print("Testing Hanged Man: 5 damage to other, heal 7, refresh self...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    hm_id = find_in_deck(state, pid, "major_12")
    assert hm_id is not None
    state = remove_from_deck(state, pid, hm_id)

    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 10))

    ps = gs_get_player(state, pid)
    ps = replace(ps, discard_pile=ps.discard_pile + (hm_id,))
    state = gs_update_player(state, pid, ps)

    state = fire_on_resolve(state, hm_id, pid)

    ps = gs_get_player(state, pid)
    assert ps.hp == 17, f"Should heal 7: 10→17, got {ps.hp}"
    assert hm_id in ps.refresh_pile

    other_ps = gs_get_player(state, PlayerId.BLUE)
    assert other_ps.hp == 15

    print("  Hanged Man: heal 7, deal 5, refresh")
    print("  PASS")


def test_tower_die():
    print("Testing The Tower: you die!...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    tower_id = find_in_deck(state, pid, "major_16")
    assert tower_id is not None

    state = fire_on_resolve(state, tower_id, pid)
    ps = gs_get_player(state, pid)
    # May or may not be dead depending on Star/revive mechanics
    # With base roles (no Star equipped), should be dead
    if "major_17" not in [state.card_def(eq).name for eq in ps.equipment if eq is not None]:
        assert ps.is_dead, "Tower should kill the player"

    print("  Tower: player killed")
    print("  PASS")


def test_temperance_on_kill_heal():
    print("Testing Temperance ON_KILL: heal 5...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED
    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 10))

    temp_id = find_in_deck(state, pid, "major_14")
    assert temp_id is not None

    state = fire_on_kill(state, temp_id, pid)
    ps = gs_get_player(state, pid)
    assert ps.hp == 15, f"Temperance ON_KILL should heal 5: 10→15, got {ps.hp}"

    print("  Temperance ON_KILL: heals 5")
    print("  PASS")


def test_after_death_grants_ability():
    print("Testing AFTER_DEATH grants permanent abilities...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    # Temperance
    temp_id = find_in_deck(state, pid, "major_14")
    state = fire_after_death(state, temp_id, pid)
    ps = gs_get_player(state, pid)
    assert "temperance_give_hp" in ps.permanent_abilities

    # Devil
    devil_id = find_in_deck(state, pid, "major_15")
    state = fire_after_death(state, devil_id, pid)
    ps = gs_get_player(state, pid)
    assert "devil_gamble" in ps.permanent_abilities

    # Moon
    moon_id = find_in_deck(state, pid, "major_18")
    state = fire_after_death(state, moon_id, pid)
    ps = gs_get_player(state, pid)
    assert "moon_deviation_cap" in ps.permanent_abilities

    # Sun
    sun_id = find_in_deck(state, pid, "major_19")
    state = fire_after_death(state, sun_id, pid)
    ps = gs_get_player(state, pid)
    assert "sun_force_resolve" in ps.permanent_abilities

    # World
    world_id = find_in_deck(state, pid, "major_21")
    state = fire_after_death(state, world_id, pid)
    ps = gs_get_player(state, pid)
    assert "world_killed" in ps.permanent_abilities

    print("  All 5 boss AFTER_DEATH abilities granted correctly")
    print("  PASS")


def test_ba_barockus_on_kill():
    print("Testing BA Barockus ON_KILL: take 3 damage...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    bar_id = find_in_deck(state, pid, "enemy_14")
    assert bar_id is not None

    state = fire_on_kill(state, bar_id, pid)
    ps = gs_get_player(state, pid)
    assert ps.hp == 17, f"BA Barockus ON_KILL: 3 damage, expected 17, got {ps.hp}"

    print("  BA Barockus: 3 damage on kill")
    print("  PASS")


def test_enemy_3_discard_kills():
    print("Testing enemy_3 ON_KILL: discard kill pile...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    # Equip weapon with enemy_3 and enemy_5 in kill pile
    state, weapon_id = equip_weapon(state, pid, "weapon_6", kill_pile_names=["enemy_5"])

    # Find enemy_3 and add to kill pile manually
    e3_id = find_in_deck(state, pid, "enemy_3")
    state = remove_from_deck(state, pid, e3_id)
    ps = gs_get_player(state, pid)
    ws = ps.weapon_slots[0]
    ws = WeaponSlot(weapon=ws.weapon, kill_pile=ws.kill_pile + (e3_id,))
    ps = replace(ps, weapon_slots=(ws,))
    state = gs_update_player(state, pid, ps)

    # Fire ON_KILL for enemy_3
    state = fire_on_kill(state, e3_id, pid)

    ps = gs_get_player(state, pid)
    ws = ps.weapon_slots[0]
    assert len(ws.kill_pile) == 0, f"Kill pile should be empty, got {len(ws.kill_pile)}"
    assert ws.weapon is not None, "Weapon itself should still be equipped"

    print("  enemy_3: kill pile cleared on weapon kill")
    print("  PASS")


def test_enemy_7_discard_weapon():
    print("Testing enemy_7 ON_KILL: discard weapon...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    state, weapon_id = equip_weapon(state, pid, "weapon_4")
    e7_id = find_in_deck(state, pid, "enemy_7")
    state = remove_from_deck(state, pid, e7_id)

    # Place enemy_7 in kill pile
    ps = gs_get_player(state, pid)
    ws = ps.weapon_slots[0]
    ws = WeaponSlot(weapon=ws.weapon, kill_pile=(e7_id,))
    ps = replace(ps, weapon_slots=(ws,))
    state = gs_update_player(state, pid, ps)

    state = fire_on_kill(state, e7_id, pid)

    ps = gs_get_player(state, pid)
    ws = ps.weapon_slots[0]
    assert ws.weapon is None, "Weapon should be discarded"
    assert len(ws.kill_pile) == 0, "Kill pile should be empty"

    print("  enemy_7: weapon and kill pile discarded")
    print("  PASS")


def test_lonely_ogre_wield():
    print("Testing Lonely Ogre ON_KILL: wield as weapon...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    ogre_id = find_in_deck(state, pid, "enemy_8")
    assert ogre_id is not None
    state = remove_from_deck(state, pid, ogre_id)

    # Put ogre in discard (killed with fists)
    ps = gs_get_player(state, pid)
    ps = replace(ps, discard_pile=ps.discard_pile + (ogre_id,))
    state = gs_update_player(state, pid, ps)

    state = fire_on_kill(state, ogre_id, pid)

    ps = gs_get_player(state, pid)
    ws = ps.weapon_slots[0]
    assert ws.weapon == ogre_id, "Lonely Ogre should be wielded as weapon"

    print("  Lonely Ogre: wielded as weapon after kill")
    print("  PASS")


def test_fool_event_resolve():
    print("Testing Fool event: resolve top card of deck...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    fool_id = find_in_deck(state, pid, "major_0")
    assert fool_id is not None

    deck_before = len(gs_get_player(state, pid).deck)
    hp_before = gs_get_player(state, pid).hp

    state = fire_on_resolve(state, fool_id, pid)

    ps = gs_get_player(state, pid)
    # One card should have been drawn from deck and resolved
    assert len(ps.deck) < deck_before, "Should have drawn from deck"

    print(f"  Fool: drew and resolved card, HP {hp_before}→{ps.hp}")
    print("  PASS")


def test_magician_top_3():
    print("Testing Magician: top 3, resolve 1, refresh 2...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    mag_id = find_in_deck(state, pid, "major_1")
    assert mag_id is not None

    deck_before = len(gs_get_player(state, pid).deck)
    refresh_before = len(gs_get_player(state, pid).refresh_pile)

    state = fire_on_resolve(state, mag_id, pid)

    ps = gs_get_player(state, pid)
    # 3 cards drawn: 1 resolved, 2 refreshed
    assert len(ps.deck) <= deck_before - 3, "Should draw 3 from deck"
    assert len(ps.refresh_pile) >= refresh_before + 2, "2 cards should be refreshed"

    print(f"  Magician: 3 drawn, 2 refreshed")
    print("  PASS")


def test_vorpal_blade_discard():
    print("Testing Vorpal Blade ON_DISCARD: refresh action, phase over...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    vb_id = find_in_deck(state, pid, "weapon_10")
    assert vb_id is not None

    # Place some cards on action field
    enemy_id = find_in_deck(state, pid, "enemy_2")
    state = remove_from_deck(state, pid, enemy_id)
    af = state.action_field
    af = af_add_card_to_slot(af, pid, 0, enemy_id)
    state = gs_set_action_field(state, af)

    from fj_spec.effects import fire_on_discard
    state = fire_on_discard(state, vb_id, pid)

    ps = gs_get_player(state, pid)
    assert ps.action_phase_over, "Action Phase should be over"
    # Action field should be cleared (refreshed)
    af = state.action_field
    for i in range(4):
        slot = af_get_slot(af, pid, i)
        assert slot.is_empty, f"Slot {i} should be empty after Vorpal Blade"

    print("  Vorpal Blade: action field refreshed, phase over")
    print("  PASS")


def test_guard_respawn():
    print("Testing guard AFTER_DEATH: draw another guard to refresh...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    guard_deck_before = len(state.guard_deck)
    refresh_before = len(gs_get_player(state, pid).refresh_pile)

    # Use first guard from guard deck as the dying guard
    guard_id = state.guard_deck[0]

    state = fire_after_death(state, guard_id, pid)

    ps = gs_get_player(state, pid)
    # Should have drawn a guard from guard deck to refresh pile
    if guard_deck_before > 0:
        assert len(state.guard_deck) < guard_deck_before, "Guard deck should shrink"
        assert len(ps.refresh_pile) > refresh_before, "Refresh pile should grow"

    print("  Guard respawn: new guard drawn into refresh pile")
    print("  PASS")


def test_death_discard_adjacent():
    print("Testing Death: discard adjacent action cards...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    death_id = find_in_deck(state, pid, "major_13")
    assert death_id is not None
    state = remove_from_deck(state, pid, death_id)

    # Place cards in slots 0, 1, 2 (Death will be "in" slot 1)
    e1 = find_in_deck(state, pid, "enemy_2")
    e2 = find_in_deck(state, pid, "enemy_5")
    state = remove_from_deck(state, pid, e1)
    state = remove_from_deck(state, pid, e2)

    af = state.action_field
    af = af_add_card_to_slot(af, pid, 0, e1)
    af = af_add_card_to_slot(af, pid, 2, e2)
    state = gs_set_action_field(state, af)

    # Set up resolution context as if Death is in slot 1
    ctx = ActionContext(
        current_turn=pid,
        step=ActionStep.RESOLVING_SLOT,
        resolving=ResolutionContext(slot_owner=pid, slot_index=1),
    )
    state = replace(state, phase=Phase.ACTION, phase_context=ctx)

    state = fire_on_resolve(state, death_id, pid)

    # Adjacent to slot 1: slots 0 and 2 should be cleared
    af = state.action_field
    assert af_get_slot(af, pid, 0).is_empty, "Slot 0 (adjacent) should be cleared"
    assert af_get_slot(af, pid, 2).is_empty, "Slot 2 (adjacent) should be cleared"

    ps = gs_get_player(state, pid)
    assert ps.action_phase_over, "Action Phase should be over after Death"

    print("  Death: adjacent slots cleared, phase over")
    print("  PASS")


def test_full_resolution_with_effects():
    print("Testing full card resolution pipeline with effects...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    # Place Chariot (Event+Equipment: take 7 damage, then equip) on action field
    chariot_id = find_in_deck(state, pid, "major_7")
    assert chariot_id is not None
    state = remove_from_deck(state, pid, chariot_id)

    af = state.action_field
    af = af_add_card_to_slot(af, pid, 0, chariot_id)
    state = gs_set_action_field(state, af)

    # Set up action context and resolve slot 0
    from fj_spec.phases.action import _begin_slot_resolution, _step_resolving_slot
    ctx = ActionContext(current_turn=pid, step=ActionStep.RESOLVING_SLOT)
    state = replace(state, phase=Phase.ACTION, phase_context=ctx)

    state = _begin_slot_resolution(state, ctx, pid, pid, 0)
    ctx = state.phase_context
    state = _step_resolving_slot(state, ctx)

    ps = gs_get_player(state, pid)
    # Should have taken 7 damage from ON_RESOLVE
    assert ps.hp == 13, f"Chariot should deal 7 damage: expected 13, got {ps.hp}"
    # Should be equipped (Event+Equipment)
    assert chariot_id in ps.equipment, "Chariot should be equipped"

    print("  Chariot: 7 damage dealt, card equipped")
    print("  PASS")


def test_hermit_alignment_effects():
    print("Testing Hermit alignment-dependent effects...")
    # Find a seed where we know RED's alignment
    for seed in range(100):
        state = create_initial_state(seed=seed)
        pid = PlayerId.RED
        ps = gs_get_player(state, pid)

        hermit_id = find_in_deck(state, pid, "major_9")
        if hermit_id is None:
            continue

        state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 15))
        state = fire_on_resolve(state, hermit_id, pid)

        ps = gs_get_player(state, pid)
        if ps.alignment == Alignment.GOOD:
            # Should heal d10 (after potentially discarding equipment)
            assert ps.hp != 15, f"Hermit Good should change HP from 15"
        else:
            # Should take d20 damage
            assert ps.hp <= 15, f"Hermit Evil should not gain HP"

        print(f"  Hermit ({ps.alignment.name}): HP 15→{ps.hp}")
        print("  PASS")
        return

    print("  SKIP")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 8 Validation")
    print("=" * 60)
    test_handler_registry_complete()
    test_food_1_d10_damage()
    test_chariot_take_7()
    test_wheel_of_fortune()
    test_justice_damage_and_refresh()
    test_hanged_man()
    test_tower_die()
    test_temperance_on_kill_heal()
    test_after_death_grants_ability()
    test_ba_barockus_on_kill()
    test_enemy_3_discard_kills()
    test_enemy_7_discard_weapon()
    test_lonely_ogre_wield()
    test_fool_event_resolve()
    test_magician_top_3()
    test_vorpal_blade_discard()
    test_guard_respawn()
    test_death_discard_adjacent()
    test_full_resolution_with_effects()
    test_hermit_alignment_effects()
    print("=" * 60)
    print("ALL STAGE 8 TESTS PASSED")
    print("=" * 60)