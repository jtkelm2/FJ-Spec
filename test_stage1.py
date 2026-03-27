#!/usr/bin/env python3
"""Stage 1 validation: verify types and card registry integrity."""

from fj_spec.types import (
    CardDef, CardType, PlayerId, Alignment, Trigger, EffectDef,
    ActionSlot, ActionField, WeaponSlot, PlayerState, GameState,
    SlotKind, classify_slot, DISTANT_INDICES, HIDDEN_INDICES,
    Phase, DecisionKind, ActionKind, Action, PendingDecision,
)
from fj_spec.cards import (
    ALL_CARD_DEFS, get_card_def, standard_deck_names, guard_deck_names,
    FOOD_DEFS, WEAPON_DEFS, ENEMY_DEFS, MAJOR_DEFS, GUARD_DEFS,
    GOOD_ROLE_DEFS, EVIL_ROLE_DEFS,
    has_trigger, is_enemy_like, is_food, is_weapon, is_equipment, is_event,
    is_role, is_good_role, is_evil_role,
)


def test_card_counts():
    print("Testing card counts...")
    assert len(FOOD_DEFS) == 10, f"Expected 10 food, got {len(FOOD_DEFS)}"
    assert len(WEAPON_DEFS) == 10, f"Expected 10 weapons, got {len(WEAPON_DEFS)}"
    assert len(ENEMY_DEFS) == 14, f"Expected 14 enemies, got {len(ENEMY_DEFS)}"
    assert len(MAJOR_DEFS) == 22, f"Expected 22 majors, got {len(MAJOR_DEFS)}"
    assert len(GUARD_DEFS) == 4, f"Expected 4 guard types, got {len(GUARD_DEFS)}"
    assert len(GOOD_ROLE_DEFS) == 8, f"Expected 8 good roles, got {len(GOOD_ROLE_DEFS)}"
    assert len(EVIL_ROLE_DEFS) == 9, f"Expected 9 evil roles, got {len(EVIL_ROLE_DEFS)}"
    print(f"  Total unique card defs: {len(ALL_CARD_DEFS)}")
    print("  PASS")


def test_standard_deck():
    print("Testing standard deck composition...")
    names = standard_deck_names()
    assert len(names) == 70
    # Verify all names exist in registry
    for name in names:
        assert name in ALL_CARD_DEFS, f"Deck card '{name}' not in registry"
    print("  70 cards, all found in registry")
    print("  PASS")


def test_guard_deck():
    print("Testing guard deck composition...")
    names = guard_deck_names()
    assert len(names) == 16
    for name in names:
        assert name in ALL_CARD_DEFS, f"Guard '{name}' not in registry"
    print("  16 guards, all found in registry")
    print("  PASS")


def test_elusive_tags():
    print("Testing Elusive tags...")
    expected_elusive = {
        "weapon_8", "weapon_9", "weapon_10",
        "major_0",   # The Fool
        "major_14",  # Temperance
        "major_16",  # The Tower
        "major_17",  # The Star
        "major_21",  # The World
    }
    actual_elusive = {name for name, cd in ALL_CARD_DEFS.items() if cd.is_elusive}
    assert actual_elusive == expected_elusive, (
        f"Elusive mismatch.\n  Expected: {expected_elusive}\n  Got: {actual_elusive}"
    )
    print(f"  {len(expected_elusive)} Elusive cards verified")
    print("  PASS")


def test_first_tags():
    print("Testing First tags...")
    expected_first = {
        "weapon_10",  # Vorpal Blade
        "major_13",   # Death
        "major_15",   # The Devil
        "major_17",   # The Star
    }
    actual_first = {name for name, cd in ALL_CARD_DEFS.items() if cd.is_first}
    assert actual_first == expected_first, (
        f"First mismatch.\n  Expected: {expected_first}\n  Got: {actual_first}"
    )
    print(f"  {len(expected_first)} First cards verified")
    print("  PASS")


def test_slot_classification():
    print("Testing slot classification...")
    RED = PlayerId.RED
    BLUE = PlayerId.BLUE
    assert classify_slot(RED, RED, 0) == SlotKind.OWN_DISTANT
    assert classify_slot(RED, RED, 1) == SlotKind.OWN_HIDDEN
    assert classify_slot(RED, RED, 2) == SlotKind.OWN_HIDDEN
    assert classify_slot(RED, RED, 3) == SlotKind.OWN_DISTANT
    assert classify_slot(RED, BLUE, 0) == SlotKind.OTHER_DISTANT
    assert classify_slot(RED, BLUE, 1) == SlotKind.OTHER_HIDDEN
    assert classify_slot(RED, BLUE, 2) == SlotKind.OTHER_HIDDEN
    assert classify_slot(RED, BLUE, 3) == SlotKind.OTHER_DISTANT
    print("  All 8 slot classifications correct")
    print("  PASS")


def test_type_queries():
    print("Testing type query functions...")
    assert is_enemy_like(get_card_def("enemy_5"))
    assert is_enemy_like(get_card_def("major_14"))   # Temperance (Boss)
    assert is_enemy_like(get_card_def("guards_1"))
    assert not is_enemy_like(get_card_def("food_2"))

    assert is_food(get_card_def("food_3"))
    assert not is_food(get_card_def("weapon_1"))

    assert is_weapon(get_card_def("weapon_7"))
    assert not is_weapon(get_card_def("food_1"))

    assert is_equipment(get_card_def("major_3"))     # Empress
    assert is_equipment(get_card_def("major_7"))     # Chariot (Event+Equipment)
    assert is_event(get_card_def("major_7"))         # Chariot is also Event

    assert is_role(get_card_def("good_role_1"))
    assert is_good_role(get_card_def("good_role_1"))
    assert not is_evil_role(get_card_def("good_role_1"))
    assert is_evil_role(get_card_def("bad_role_1"))

    # Dual types
    chariot = get_card_def("major_7")
    assert CardType.EVENT in chariot.card_types
    assert CardType.EQUIPMENT in chariot.card_types

    judgement = get_card_def("major_20")
    assert CardType.EQUIPMENT in judgement.card_types
    assert CardType.WEAPON in judgement.card_types

    print("  All type queries correct")
    print("  PASS")


def test_effect_triggers():
    print("Testing effect trigger assignments...")

    # Skeleton has ON_PLACEMENT
    assert has_trigger(get_card_def("enemy_4"), Trigger.ON_PLACEMENT)
    # Guards have PREVENT_RUN and ON_PLACEMENT and AFTER_DEATH
    for i in range(1, 5):
        g = get_card_def(f"guards_{i}")
        assert has_trigger(g, Trigger.PREVENT_RUN)
        assert has_trigger(g, Trigger.ON_PLACEMENT)
        assert has_trigger(g, Trigger.AFTER_DEATH)

    # Empress has REFRESH_PHASE_END
    assert has_trigger(get_card_def("major_3"), Trigger.REFRESH_PHASE_END)

    # The Fool event has ON_RESOLVE
    assert has_trigger(get_card_def("major_0"), Trigger.ON_RESOLVE)

    # Temperance has ON_KILL and AFTER_DEATH
    temp = get_card_def("major_14")
    assert has_trigger(temp, Trigger.ON_KILL)
    assert has_trigger(temp, Trigger.AFTER_DEATH)

    # Vorpal Blade has ON_DISCARD
    assert has_trigger(get_card_def("weapon_10"), Trigger.ON_DISCARD)

    print("  All effect triggers verified")
    print("  PASS")


def test_player_id_other():
    print("Testing PlayerId.other()...")
    assert PlayerId.RED.other() == PlayerId.BLUE
    assert PlayerId.BLUE.other() == PlayerId.RED
    print("  PASS")


def test_action_slot():
    print("Testing ActionSlot...")
    empty = ActionSlot()
    assert empty.is_empty
    assert empty.cards == ()
    filled = ActionSlot(cards=(1, 2, 3))
    assert not filled.is_empty
    assert len(filled.cards) == 3
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 1 Validation")
    print("=" * 60)
    test_card_counts()
    test_standard_deck()
    test_guard_deck()
    test_elusive_tags()
    test_first_tags()
    test_slot_classification()
    test_type_queries()
    test_effect_triggers()
    test_player_id_other()
    test_action_slot()
    print("=" * 60)
    print("ALL STAGE 1 TESTS PASSED")
    print("=" * 60)
