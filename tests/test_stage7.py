#!/usr/bin/env python3
"""Stage 7 validation: Combat and damage/healing pipeline."""

from dataclasses import replace

from fj_spec.types import (
    PlayerId, Alignment, Phase, CardType, CardState, Parity,
    ActionSlot, ActionField,
    PlayerState, WeaponSlot, GameState,
    ActionContext, ActionStep, SlotRef,
    PendingDecision, DecisionKind, Action, ActionKind,
)
from fj_spec.cards import get_card_def, ALL_CARD_DEFS
from fj_spec.setup import (
    create_initial_state,
    player_is_leo, player_is_phoenix, player_is_corruption,
)
from fj_spec.state_helpers import (
    gs_get_player, gs_update_player, gs_modify_player,
    gs_set_action_field, gs_set_card_state,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_equipment,
    ps_set_weapon_slots, ps_set_hp_cap,
    ps_add_permanent_ability,
    af_add_card_to_slot, af_get_slot,
)
from fj_spec.combat import (
    apply_damage, apply_healing, set_hp_direct,
    resolve_combat, can_use_weapon, get_attack_options,
    resolve_mutiny,
    DamageSource, HealSource,
)


# ---------------------------------------------------------------------------
# Helpers: create a minimal game state for isolated testing
# ---------------------------------------------------------------------------

def make_test_state(seed=42, **overrides):
    """Create a state with sensible defaults for combat testing."""
    return create_initial_state(seed=seed)


def equip_weapon(state, pid, card_name, kill_pile_names=None):
    """Find a card by name in deck and equip it as a weapon."""
    ps = gs_get_player(state, pid)

    # Find card in deck
    weapon_id = None
    for cid in ps.deck:
        if state.card_def(cid).name == card_name:
            weapon_id = cid
            break

    if weapon_id is None:
        return state, None

    # Remove from deck
    deck_list = list(ps.deck)
    deck_list.remove(weapon_id)
    ps = ps_set_deck(ps, tuple(deck_list))

    # Build kill pile from deck
    kill_pile_ids = ()
    if kill_pile_names:
        for kn in kill_pile_names:
            for cid in ps.deck:
                if state.card_def(cid).name == kn and cid not in kill_pile_ids:
                    deck_list = list(ps.deck)
                    deck_list.remove(cid)
                    ps = ps_set_deck(ps, tuple(deck_list))
                    kill_pile_ids = kill_pile_ids + (cid,)
                    break

    ws = WeaponSlot(weapon=weapon_id, kill_pile=kill_pile_ids)
    ps = ps_set_weapon_slots(ps, (ws,))
    state = gs_update_player(state, pid, ps)
    return state, weapon_id


def find_card_in_deck(state, pid, card_name):
    """Find a CardId with the given name in a player's deck."""
    ps = gs_get_player(state, pid)
    for cid in ps.deck:
        if state.card_def(cid).name == card_name:
            return cid
    return None


# ---------------------------------------------------------------------------
# Damage pipeline tests
# ---------------------------------------------------------------------------

def test_apply_damage_basic():
    print("Testing basic damage application...")
    state = make_test_state()
    pid = PlayerId.RED

    state = apply_damage(state, pid, 5)
    assert gs_get_player(state, pid).hp == 15

    state = apply_damage(state, pid, 3)
    assert gs_get_player(state, pid).hp == 12

    # Zero damage is no-op
    state = apply_damage(state, pid, 0)
    assert gs_get_player(state, pid).hp == 12

    # Negative damage is no-op
    state = apply_damage(state, pid, -5)
    assert gs_get_player(state, pid).hp == 12

    print("  PASS")


def test_apply_damage_lethal():
    print("Testing lethal damage (death)...")
    state = make_test_state()
    pid = PlayerId.RED

    state = apply_damage(state, pid, 25)
    ps = gs_get_player(state, pid)

    # With base roles (Human/???), no revive — player should die
    if not player_is_leo(state, pid) and not player_is_phoenix(state, pid):
        assert ps.is_dead, "Player should be dead from 25 damage at 20 HP"
    else:
        # Leo or Phoenix would revive
        assert not ps.is_dead or ps.hp > 0

    print("  PASS")


def test_apply_healing_basic():
    print("Testing basic healing application...")
    state = make_test_state()
    pid = PlayerId.RED

    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 10))
    state = apply_healing(state, pid, 5)
    assert gs_get_player(state, pid).hp == 15

    # Healing caps at hp_cap (20)
    state = apply_healing(state, pid, 10)
    assert gs_get_player(state, pid).hp == 20

    print("  PASS")


def test_healing_corruption_inversion():
    print("Testing Corruption healing inversion...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_4"],  # Corruption
        )
        corr_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_corruption(state, pid):
                corr_pid = pid
                break
        if corr_pid is None:
            continue

        state = gs_modify_player(state, corr_pid, lambda ps: ps_set_hp(ps, 15))

        # Non-Corruption healing should be inverted to damage
        state = apply_healing(state, corr_pid, 3, HealSource.FOOD)
        assert gs_get_player(state, corr_pid).hp == 12, (
            f"Corruption should invert food healing: 15 - 3 = 12, got {gs_get_player(state, corr_pid).hp}"
        )

        # Corruption-source healing should NOT be inverted
        state = apply_healing(state, corr_pid, 6, HealSource.CORRUPTION)
        assert gs_get_player(state, corr_pid).hp == 18

        print(f"  Corruption inversion verified at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find Corruption)")
    print("  SKIP")


def test_set_hp_direct():
    print("Testing direct HP setting (Wheel of Fortune)...")
    state = make_test_state()
    pid = PlayerId.RED

    state = set_hp_direct(state, pid, 7)
    assert gs_get_player(state, pid).hp == 7

    # Can't exceed cap
    state = set_hp_direct(state, pid, 25)
    assert gs_get_player(state, pid).hp == 20

    # Can't go below 0 (lethal)
    state = set_hp_direct(state, pid, 0)
    ps = gs_get_player(state, pid)
    # Should trigger death (no revive for base roles usually)
    if not player_is_leo(state, pid) and not player_is_phoenix(state, pid):
        assert ps.is_dead

    print("  PASS")


# ---------------------------------------------------------------------------
# Combat tests
# ---------------------------------------------------------------------------

def test_combat_fists():
    print("Testing combat with fists...")
    state = make_test_state()
    pid = PlayerId.RED

    # Find a level-5 enemy
    enemy_id = find_card_in_deck(state, pid, "enemy_5")
    assert enemy_id is not None

    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 20))
    state = resolve_combat(state, pid, enemy_id, "fists")

    ps = gs_get_player(state, pid)
    assert ps.hp == 15, f"Should take 5 damage from Lv5 enemy: expected 15, got {ps.hp}"
    assert enemy_id in ps.discard_pile, "Fist kill should go to discard pile"
    # Should NOT be in any kill pile
    for ws in ps.weapon_slots:
        assert enemy_id not in ws.kill_pile

    print("  Fists: 5 damage, enemy to discard")
    print("  PASS")


def test_combat_weapon():
    print("Testing combat with weapon...")
    state = make_test_state()
    pid = PlayerId.RED

    # Equip a level-4 weapon
    state, weapon_id = equip_weapon(state, pid, "weapon_4")
    assert weapon_id is not None

    # Find a level-7 enemy
    enemy_id = find_card_in_deck(state, pid, "enemy_7")
    assert enemy_id is not None

    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 20))
    state = resolve_combat(state, pid, enemy_id, "weapon", 0)

    ps = gs_get_player(state, pid)
    # Damage: 7 - 4 = 3
    assert ps.hp == 17, f"Should take 3 damage: expected 17, got {ps.hp}"
    # Enemy should be in kill pile
    assert enemy_id in ps.weapon_slots[0].kill_pile

    print("  Weapon Lv4 vs Enemy Lv7: 3 damage, enemy to kill pile")
    print("  PASS")


def test_weapon_dulling():
    print("Testing weapon dulling...")
    state = make_test_state()
    pid = PlayerId.RED

    # Equip weapon with a level-5 enemy in kill pile
    state, weapon_id = equip_weapon(state, pid, "weapon_8", kill_pile_names=["enemy_5"])
    assert weapon_id is not None

    # Level-5 enemy: can attack (last kill = 5 >= 5) ✓
    enemy_5 = find_card_in_deck(state, pid, "enemy_5")
    assert can_use_weapon(state, pid, enemy_5, 0)

    # Level-3 enemy: can attack (last kill = 5 >= 3) ✓
    enemy_3 = find_card_in_deck(state, pid, "enemy_3")
    assert can_use_weapon(state, pid, enemy_3, 0)

    # Level-7 enemy: CANNOT attack (last kill = 5 < 7) ✗
    enemy_7 = find_card_in_deck(state, pid, "enemy_7")
    assert not can_use_weapon(state, pid, enemy_7, 0)

    print("  Dulling: Lv5 kill → can hit ≤5, cannot hit 7")
    print("  PASS")


def test_weapon_dulling_empty_kill_pile():
    print("Testing weapon with empty kill pile...")
    state = make_test_state()
    pid = PlayerId.RED

    state, weapon_id = equip_weapon(state, pid, "weapon_4")
    assert weapon_id is not None

    # Empty kill pile: can attack anything
    enemy_14 = find_card_in_deck(state, pid, "enemy_14")
    assert can_use_weapon(state, pid, enemy_14, 0)

    enemy_1 = find_card_in_deck(state, pid, "enemy_1")
    assert can_use_weapon(state, pid, enemy_1, 0)

    print("  Empty kill pile: weapon can attack any level")
    print("  PASS")


def test_get_attack_options():
    print("Testing attack option enumeration...")
    state = make_test_state()
    pid = PlayerId.RED

    # No weapon: only fists
    enemy_id = find_card_in_deck(state, pid, "enemy_5")
    options = get_attack_options(state, pid, enemy_id)
    assert len(options) == 1
    assert options[0] == ("fists", -1)

    # With weapon and empty kill pile: fists + weapon
    state, weapon_id = equip_weapon(state, pid, "weapon_6")
    options = get_attack_options(state, pid, enemy_id)
    assert len(options) == 2
    modes = {o[0] for o in options}
    assert "fists" in modes
    assert "weapon" in modes

    print("  Options: fists-only without weapon, fists+weapon with weapon")
    print("  PASS")


def test_combat_lethal_enemy():
    print("Testing lethal combat (player dies)...")
    state = make_test_state()
    pid = PlayerId.RED

    # Fight level-14 enemy with fists at 10 HP
    state = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 10))
    enemy_14 = find_card_in_deck(state, pid, "enemy_14")
    assert enemy_14 is not None

    state = resolve_combat(state, pid, enemy_14, "fists")
    ps = gs_get_player(state, pid)

    if not player_is_leo(state, pid) and not player_is_phoenix(state, pid):
        assert ps.is_dead, "Player should die from 14 damage at 10 HP"
        # Enemy should NOT be in discard (player died, didn't kill it)
        assert enemy_14 not in ps.discard_pile
    else:
        assert not ps.is_dead  # Revived

    print("  Lethal combat: player dies, enemy not killed")
    print("  PASS")


def test_combat_weapon_no_damage():
    print("Testing weapon kill with zero damage...")
    state = make_test_state()
    pid = PlayerId.RED

    # Equip level-8 weapon, fight level-5 enemy
    state, weapon_id = equip_weapon(state, pid, "weapon_8")
    enemy_5 = find_card_in_deck(state, pid, "enemy_5")

    state = resolve_combat(state, pid, enemy_5, "weapon", 0)
    ps = gs_get_player(state, pid)
    assert ps.hp == 20, "Should take 0 damage (8 > 5)"
    assert enemy_5 in ps.weapon_slots[0].kill_pile

    print("  Weapon Lv8 vs Enemy Lv5: 0 damage, enemy killed")
    print("  PASS")


# ---------------------------------------------------------------------------
# Revive tests
# ---------------------------------------------------------------------------

def test_star_revive():
    print("Testing Star revive on lethal damage...")
    state = make_test_state()
    pid = PlayerId.RED

    # Find and equip The Star
    ps = gs_get_player(state, pid)
    star_id = None
    for cid in ps.deck:
        if state.card_def(cid).name == "major_17":
            star_id = cid
            break
    if star_id is None:
        print("  (No Star in deck)")
        print("  SKIP")
        return

    # Remove from deck, equip
    deck_list = list(ps.deck)
    deck_list.remove(star_id)
    ps = ps_set_deck(ps, tuple(deck_list))
    ps = ps_set_equipment(ps, (ps.equipment[0], star_id))
    ps = ps_set_hp(ps, 5)
    state = gs_update_player(state, pid, ps)

    # Take lethal damage
    state = apply_damage(state, pid, 10)
    ps = gs_get_player(state, pid)

    assert not ps.is_dead, "Star should prevent death"
    assert ps.hp > 0, f"Should have revived HP > 0, got {ps.hp}"
    assert ps.hp <= 4, f"Star revives to d4 HP (1-4), got {ps.hp}"
    # Star should be discarded
    assert star_id not in ps.equipment
    assert star_id in ps.discard_pile
    # Action phase should be over
    assert ps.action_phase_over

    print(f"  Star revive: HP={ps.hp}, Star discarded, phase over")
    print("  PASS")


def test_leo_revive():
    print("Testing Leo revive on death...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_9"],  # Leo
        )
        leo_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_leo(state, pid):
                leo_pid = pid
                break
        if leo_pid is None:
            continue

        ps = gs_get_player(state, leo_pid)
        assert ps.hp_cap == 9

        # Take lethal damage
        state = apply_damage(state, leo_pid, 15)
        ps = gs_get_player(state, leo_pid)

        assert not ps.is_dead, "Leo should revive"
        assert ps.hp_cap == 8, f"Leo's cap should decrease: expected 8, got {ps.hp_cap}"
        assert ps.hp == 8, f"Leo should revive to full (new cap): expected 8, got {ps.hp}"

        # Kill Leo again
        state = apply_damage(state, leo_pid, 15)
        ps = gs_get_player(state, leo_pid)
        assert ps.hp_cap == 7

        print(f"  Leo revive: cap 9→8→7, HP restores to cap")
        print("  PASS")
        return

    print("  (Could not find Leo)")
    print("  SKIP")


def test_leo_permanent_death():
    print("Testing Leo permanent death at cap 0...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_9"],
        )
        leo_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_leo(state, pid):
                leo_pid = pid
                break
        if leo_pid is None:
            continue

        # Set cap to 1 and HP to 1
        ps = gs_get_player(state, leo_pid)
        ps = ps_set_hp_cap(ps, 1)
        ps = ps_set_hp(ps, 1)
        state = gs_update_player(state, leo_pid, ps)

        # Take lethal damage — cap would go to 0, permanent death
        state = apply_damage(state, leo_pid, 5)
        ps = gs_get_player(state, leo_pid)
        assert ps.is_dead, "Leo should permanently die when cap reaches 0"

        print(f"  Leo permanent death at cap=0 verified")
        print("  PASS")
        return

    print("  (Could not find Leo)")
    print("  SKIP")


def test_phoenix_revive():
    print("Testing Phoenix revive...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_8"],  # Phoenix
        )
        phoenix_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_phoenix(state, pid):
                phoenix_pid = pid
                break
        if phoenix_pid is None:
            continue

        other_pid = phoenix_pid.other()
        other_before = gs_get_player(state, other_pid)
        assert other_before.role_def_name is not None

        state = apply_damage(state, phoenix_pid, 25)
        ps = gs_get_player(state, phoenix_pid)

        assert not ps.is_dead, "Phoenix should revive"
        assert ps.hp > 0 and ps.hp <= 20, f"Phoenix revives to d20: {ps.hp}"

        # Other player should lose role
        other_after = gs_get_player(state, other_pid)
        assert other_after.role_def_name is None
        assert other_after.permanent_abilities == frozenset()

        print(f"  Phoenix revive: HP={ps.hp}, other's role stripped")
        print("  PASS")
        return

    print("  (Could not find Phoenix)")
    print("  SKIP")


# ---------------------------------------------------------------------------
# Mutiny tests
# ---------------------------------------------------------------------------

def test_mutiny_basic():
    print("Testing Mutiny combat...")
    state = make_test_state()

    # Set up attacker with weapon level 5
    attacker = PlayerId.RED
    defender = PlayerId.BLUE

    state, weapon_id = equip_weapon(state, attacker, "weapon_5")

    # Defender has 10 HP, no weapon → enemy_level = 10 + 0 = 10
    state = gs_modify_player(state, defender, lambda ps: ps_set_hp(ps, 10))

    state = resolve_mutiny(state, attacker)

    attacker_ps = gs_get_player(state, attacker)
    defender_ps = gs_get_player(state, defender)

    # Attacker takes max(0, 10 - 5) = 5 damage
    if not attacker_ps.is_dead:
        assert attacker_ps.hp == 15, f"Attacker should take 5 damage: {attacker_ps.hp}"
        assert defender_ps.is_dead, "Defender should die if attacker survives"
    # else: attacker died, defender lives

    print("  PASS")


def test_mutiny_with_defender_weapon():
    print("Testing Mutiny with defender's weapon sharpness...")
    state = make_test_state()
    attacker = PlayerId.RED
    defender = PlayerId.BLUE

    # Give defender a level-6 weapon with empty kill pile
    state, _ = equip_weapon(state, defender, "weapon_6")
    state = gs_modify_player(state, defender, lambda ps: ps_set_hp(ps, 8))

    # Defender enemy level = HP(8) + sharpness(6) = 14
    # Attacker has no weapon → attack value 0
    # Damage = 14
    state = resolve_mutiny(state, attacker)

    attacker_ps = gs_get_player(state, attacker)
    defender_ps = gs_get_player(state, defender)

    # Attacker takes 14 damage at 20 HP → 6 HP, survives
    if not player_is_leo(state, attacker) and not player_is_phoenix(state, attacker):
        assert not attacker_ps.is_dead
        assert attacker_ps.hp == 6
        assert defender_ps.is_dead

    print("  PASS")


# ---------------------------------------------------------------------------
# Integration: combat through action phase resolution
# ---------------------------------------------------------------------------

def test_combat_through_action_phase():
    print("Testing combat resolution through action phase...")
    state = make_test_state()
    pid = PlayerId.RED

    # Equip a weapon
    state, weapon_id = equip_weapon(state, pid, "weapon_6")

    # Place a level-2 enemy (plain, no ON_KILL effects) on action field slot 0
    enemy_id = find_card_in_deck(state, pid, "enemy_2")
    assert enemy_id is not None
    ps = gs_get_player(state, pid)
    deck_list = list(ps.deck)
    deck_list.remove(enemy_id)
    ps = ps_set_deck(ps, tuple(deck_list))
    state = gs_update_player(state, pid, ps)

    af = state.action_field
    af = af_add_card_to_slot(af, pid, 0, enemy_id)
    state = gs_set_action_field(state, af)

    # Set up action context
    from fj_spec.phases.action import _begin_slot_resolution, _step_resolving_slot, _apply_attack_choice
    ctx = ActionContext(current_turn=pid, step=ActionStep.RESOLVING_SLOT)
    state = replace(state, phase=Phase.ACTION, phase_context=ctx)

    # Begin resolution of slot 0
    state = _begin_slot_resolution(state, ctx, pid, pid, 0)
    ctx = state.phase_context
    assert isinstance(ctx, ActionContext)

    # Step through resolution — now presents ATTACK_CHOICE decision
    state = _step_resolving_slot(state, ctx)

    # Should have a pending ATTACK_CHOICE decision (weapon available)
    if state.pending and state.pending.kind == DecisionKind.CHOOSE_ATTACK_MODE:
        # Choose weapon (index 1, since fists is index 0)
        weapon_action = state.pending.legal_actions[1]  # weapon option
        from fj_spec.state_helpers import gs_set_pending
        state = gs_set_pending(state, None)
        state = _apply_attack_choice(state, state.phase_context, weapon_action)
    else:
        # Only fists available (shouldn't happen with weapon equipped)
        pass

    ps = gs_get_player(state, pid)
    # Level-6 weapon vs level-2 enemy: 0 damage
    assert ps.hp == 20

    # Enemy should be in kill pile (weapon kill)
    found_in_kill = False
    for ws in ps.weapon_slots:
        if enemy_id in ws.kill_pile:
            found_in_kill = True
    assert found_in_kill, "Enemy should be in kill pile after weapon kill"

    print("  Combat through action phase: weapon chosen via decision, enemy in kill pile")
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 7 Validation")
    print("=" * 60)
    test_apply_damage_basic()
    test_apply_damage_lethal()
    test_apply_healing_basic()
    test_healing_corruption_inversion()
    test_set_hp_direct()
    test_combat_fists()
    test_combat_weapon()
    test_weapon_dulling()
    test_weapon_dulling_empty_kill_pile()
    test_get_attack_options()
    test_combat_lethal_enemy()
    test_combat_weapon_no_damage()
    test_star_revive()
    test_leo_revive()
    test_leo_permanent_death()
    test_phoenix_revive()
    test_mutiny_basic()
    test_mutiny_with_defender_weapon()
    test_combat_through_action_phase()
    print("=" * 60)
    print("ALL STAGE 7 TESTS PASSED")
    print("=" * 60)