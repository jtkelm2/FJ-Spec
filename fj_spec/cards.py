"""
Fools' Journey — Executable Spec
Stage 1: Card definitions registry.

All card definitions are built here with types, levels, effects, and modifiers
derived from the JSON card data and the rules Q&A.
"""

from __future__ import annotations

from .types import (
    CardDef, CardType, EffectDef, Trigger, Alignment,
)


# ---------------------------------------------------------------------------
# Helper to reduce boilerplate
# ---------------------------------------------------------------------------

def _def(
    name: str,
    big_name: str = "",
    level: int | None = None,
    card_types: frozenset[CardType] = frozenset(),
    text: str = "",
    is_elusive: bool = False,
    is_first: bool = False,
    effects: tuple[EffectDef, ...] = (),
) -> CardDef:
    return CardDef(
        name=name,
        big_name=big_name,
        level=level,
        card_types=card_types,
        text=text,
        is_elusive=is_elusive,
        is_first=is_first,
        effects=effects,
    )


E = EffectDef  # shorthand


# ---------------------------------------------------------------------------
# Card type sets (shorthand)
# ---------------------------------------------------------------------------

FOOD = frozenset({CardType.FOOD})
ENEMY = frozenset({CardType.ENEMY})
BOSS = frozenset({CardType.BOSS})
WEAPON = frozenset({CardType.WEAPON})
EQUIPMENT = frozenset({CardType.EQUIPMENT})
EVENT = frozenset({CardType.EVENT})
GUARD = frozenset({CardType.GUARD})
EVENT_EQUIP = frozenset({CardType.EVENT, CardType.EQUIPMENT})
EQUIP_WEAPON = frozenset({CardType.EQUIPMENT, CardType.WEAPON})
CURSED_EQUIP = frozenset({CardType.EQUIPMENT})  # Same mechanics, "Cursed" is flavor
ROLE_GOOD = frozenset({CardType.ROLE_GOOD})
ROLE_EVIL = frozenset({CardType.ROLE_EVIL})


# ---------------------------------------------------------------------------
# FOOD CARDS (nameless 1-10)
# ---------------------------------------------------------------------------

FOOD_DEFS: list[CardDef] = [
    _def("food_1", level=5, card_types=FOOD,
         text="On resolve: After eating, receive d10 damage.",
         effects=(E(Trigger.ON_RESOLVE_AFTER, "food_1_d10_damage"),)),

    _def("food_2", level=2, card_types=FOOD),

    _def("food_3", big_name="Saltine Shuriken", level=3, card_types=FOOD,
         text="On resolve: You may wield this as a weapon instead of eating it. "
              "As a weapon: Discard all slain enemies. When this is discarded, you may eat this.",
         effects=(
             E(Trigger.ON_RESOLVE, "saltine_choice"),
             E(Trigger.AS_WEAPON, "saltine_weapon_kill"),
             E(Trigger.ON_DISCARD, "saltine_weapon_eat"),
         )),

    _def("food_4", level=4, card_types=FOOD),

    _def("food_5", level=5, card_types=FOOD),

    _def("food_6", level=6, card_types=FOOD),

    _def("food_7", big_name="Fat Sandwich", level=7, card_types=FOOD,
         text="On resolve: Equip this instead of eating it. "
              "While equipped: You may discard this to eat this.",
         effects=(
             E(Trigger.ON_RESOLVE, "fat_sandwich_equip"),
             E(Trigger.WHILE_EQUIPPED, "fat_sandwich_eat"),
         )),

    _def("food_8", level=8, card_types=FOOD),

    # food_9 (Bellyfiller) has no numeric level for eating — it equips instead
    _def("food_9", big_name="Bellyfiller", level=None, card_types=FOOD,
         text="On resolve: Equip this instead of eating it. "
              "While equipped: At end of each Refresh Phase, heal 3 HP then place a counter. "
              "If 3 counters, discard this.",
         effects=(
             E(Trigger.ON_RESOLVE, "bellyfiller_equip"),
             E(Trigger.REFRESH_PHASE_END, "bellyfiller_heal"),
         )),

    _def("food_10", level=10, card_types=FOOD),
]


# ---------------------------------------------------------------------------
# WEAPON CARDS (nameless 11-20)
# ---------------------------------------------------------------------------

WEAPON_DEFS: list[CardDef] = [
    _def("weapon_1", big_name="Fetch Stick", level=1, card_types=WEAPON,
         text="As a weapon: If this has no counters, then when this would be discarded, "
              "instead the other player must wield it and add a counter to it.",
         effects=(E(Trigger.AS_WEAPON, "fetch_stick_transfer"),)),

    _def("weapon_2", level=2, card_types=WEAPON),

    _def("weapon_3", big_name="Piñata Stick", level=3, card_types=WEAPON,
         text="On discard: You may deal 3 damage to the other player to see their hand.",
         effects=(E(Trigger.ON_DISCARD, "pinata_stick"),)),

    _def("weapon_4", level=4, card_types=WEAPON),

    _def("weapon_5", level=5, card_types=WEAPON),

    _def("weapon_6", level=6, card_types=WEAPON),

    _def("weapon_7", level=7, card_types=WEAPON,
         text="As a weapon: You never pay the distance penalty. "
              "Whenever you kill an enemy on the other player's field with this, discard the enemy.",
         effects=(E(Trigger.AS_WEAPON, "weapon_7_no_distance"),)),

    _def("weapon_8", level=8, card_types=WEAPON, is_elusive=True),

    _def("weapon_9", level=9, card_types=WEAPON, is_elusive=True),

    _def("weapon_10", big_name="Vorpal Blade", level=10, card_types=WEAPON,
         is_elusive=True, is_first=True,
         text="As a weapon: When this is discarded, place all your action cards into refresh. "
              "Your Action Phase is over.",
         effects=(E(Trigger.ON_DISCARD, "vorpal_blade_discard"),)),
]


# ---------------------------------------------------------------------------
# ENEMY CARDS (nameless 21-34)
# ---------------------------------------------------------------------------

ENEMY_DEFS: list[CardDef] = [
    _def("enemy_1", big_name="Gobshite", level=1, card_types=ENEMY,
         text="If attacking with your fists: This is a level 22 enemy.",
         effects=(E(Trigger.ON_RESOLVE, "gobshite_fist_check"),)),

    _def("enemy_2", level=2, card_types=ENEMY),

    _def("enemy_3", level=3, card_types=ENEMY,
         text="If you kill this with a weapon: Discard this and your kill pile.",
         effects=(E(Trigger.ON_KILL, "enemy_3_discard_kills"),)),

    _def("enemy_4", big_name="Skeleton", level=4, card_types=ENEMY,
         text="On placement: Draw another card underneath this.",
         effects=(E(Trigger.ON_PLACEMENT, "skeleton_draw_underneath"),)),

    _def("enemy_5", big_name="Skeleton", level=5, card_types=ENEMY),

    _def("enemy_6", big_name="Skeleton", level=6, card_types=ENEMY),

    _def("enemy_7", level=7, card_types=ENEMY,
         text="If you kill this with a weapon: Discard your weapon.",
         effects=(E(Trigger.ON_KILL, "enemy_7_discard_weapon"),)),

    _def("enemy_8", big_name="Lonely Ogre", level=8, card_types=ENEMY,
         text="On kill: Wield this as a weapon.",
         effects=(E(Trigger.ON_KILL, "lonely_ogre_wield"),)),

    _def("enemy_9", level=9, card_types=ENEMY),

    _def("enemy_10", level=10, card_types=ENEMY),

    _def("enemy_11", level=11, card_types=ENEMY),

    _def("enemy_12", level=12, card_types=ENEMY),

    _def("enemy_13", level=13, card_types=ENEMY),

    _def("enemy_14", big_name="BA Barockus", level=14, card_types=ENEMY,
         text="On kill: Take 3 damage.",
         effects=(E(Trigger.ON_KILL, "ba_barockus_damage"),)),
]


# ---------------------------------------------------------------------------
# MAJOR ARCANA (named 1-22 / major_0 through major_21)
# ---------------------------------------------------------------------------

MAJOR_DEFS: list[CardDef] = [
    _def("major_0", big_name="The Fool", card_types=EVENT,
         is_elusive=True,
         text="On resolve: Look at the top card of the deck and resolve it.",
         effects=(E(Trigger.ON_RESOLVE, "fool_event_resolve"),)),

    _def("major_1", big_name="The Magician", card_types=EVENT,
         text="On resolve: Look at top 3 cards of deck, resolve one, refresh 2.",
         effects=(E(Trigger.ON_RESOLVE, "magician_choose"),)),

    _def("major_2", big_name="The High Priestess", card_types=EVENT,
         text="On resolve: Name up to 2 cards. Check refresh pile. Per match: "
              "heal 7 / deal 7 to other / force discard equipment.",
         effects=(E(Trigger.ON_RESOLVE, "high_priestess"),)),

    _def("major_3", big_name="The Empress", card_types=EQUIPMENT,
         text="While equipped: Heal 1 at end of Refresh Phase.",
         effects=(E(Trigger.REFRESH_PHASE_END, "empress_heal"),)),

    _def("major_4", big_name="The Emperor", card_types=EQUIPMENT,
         text="While equipped: Add +1 to your weapon's level.",
         effects=(E(Trigger.WHILE_EQUIPPED, "emperor_weapon_boost"),)),

    _def("major_5", big_name="The Hierophant", card_types=CURSED_EQUIP,
         text="While equipped: When discarded, draw to 6 cards, split hand, "
              "give one pile to other player, place in action slots.",
         effects=(E(Trigger.ON_DISCARD, "hierophant_discard"),)),

    _def("major_6", big_name="The Lovers", card_types=EVENT,
         text="On resolve: Give the other player any amount of HP. Then take 1 damage.",
         effects=(E(Trigger.ON_RESOLVE, "lovers_give_hp"),)),

    _def("major_7", big_name="The Chariot", card_types=EVENT_EQUIP,
         text="On resolve: Take 7 damage. "
              "While equipped: You may discard this to prevent any single instance of damage.",
         effects=(
             E(Trigger.ON_RESOLVE, "chariot_take_7"),
             E(Trigger.WHILE_EQUIPPED, "chariot_prevent"),
         )),

    _def("major_8", big_name="Strength", level=8, card_types=EQUIP_WEAPON,
         text="While equipped: You may discard this to wield it as a weapon. "
              "As a weapon: On kill, discard enemy; other player rolls d20 (Evil may lie); "
              "counter logic; Good gets -1 level per counter.",
         effects=(
             E(Trigger.WHILE_EQUIPPED, "strength_wield_option"),
             E(Trigger.AS_WEAPON, "strength_on_kill"),
         )),

    _def("major_9", big_name="The Hermit", card_types=EVENT,
         text="You may choose to give the other player 1HP. "
              "Then if Good: discard equipment + heal d10. If Evil: take d20 damage.",
         effects=(E(Trigger.ON_RESOLVE, "hermit_choice"),)),

    _def("major_10", big_name="The Wheel of Fortune", card_types=EVENT,
         text="On resolve: Roll d20. Set HP equal to result.",
         effects=(E(Trigger.ON_RESOLVE, "wheel_of_fortune"),)),

    _def("major_11", big_name="Justice", card_types=EVENT,
         text="On resolve: Deal 5 damage to the other player. Put this into refresh pile.",
         effects=(E(Trigger.ON_RESOLVE, "justice_damage_refresh"),)),

    _def("major_12", big_name="The Hanged Man", card_types=EVENT,
         text="On resolve: Deal 5 damage to other player, heal yourself for 7. "
              "Put this into refresh pile.",
         effects=(E(Trigger.ON_RESOLVE, "hanged_man"),)),

    _def("major_13", big_name="Death", card_types=EVENT,
         is_first=True,
         text="On resolve: Discard all adjacent action cards. Your Action Phase ends now.",
         effects=(E(Trigger.ON_RESOLVE, "death_discard_adjacent"),)),

    _def("major_14", big_name="Temperance", level=14, card_types=BOSS,
         is_elusive=True,
         text="On kill: Heal 5. After death: At any point, you may give any amount of HP "
              "to the other player.",
         effects=(
             E(Trigger.ON_KILL, "temperance_heal"),
             E(Trigger.AFTER_DEATH, "temperance_give_hp"),
         )),

    _def("major_15", big_name="The Devil", level=15, card_types=BOSS,
         is_first=True,
         text="After death: Once per Action Phase, declare a number. If the other player "
              "has at least that much HP, steal it. Otherwise, you die!",
         effects=(E(Trigger.AFTER_DEATH, "devil_gamble"),)),

    _def("major_16", big_name="The Tower", card_types=EVENT,
         is_elusive=True,
         text="On resolve: You die!",
         effects=(E(Trigger.ON_RESOLVE, "tower_die"),)),

    _def("major_17", big_name="The Star", card_types=EQUIPMENT,
         is_elusive=True, is_first=True,
         text="While equipped: When you die, discard this and revive to d4 HP. "
              "Place all action cards into refresh. Your Action Phase is over.",
         effects=(E(Trigger.WHILE_EQUIPPED, "star_revive"),)),

    _def("major_18", big_name="The Moon", level=18, card_types=BOSS,
         text="After death: At start of each Refresh Phase, record HP. "
              "Until next Refresh, HP may not deviate more than 10.",
         effects=(E(Trigger.AFTER_DEATH, "moon_deviation_cap"),)),

    _def("major_19", big_name="The Sun", level=19, card_types=BOSS,
         text="After death: Once per Action Phase, resolve one of the other player's "
              "action slots without consent.",
         effects=(E(Trigger.AFTER_DEATH, "sun_force_resolve"),)),

    _def("major_20", big_name="Judgement", level=20, card_types=EQUIP_WEAPON,
         text="While equipped: You may discard this to wield it as a weapon. "
              "As a weapon: Discards after one use.",
         effects=(
             E(Trigger.WHILE_EQUIPPED, "judgement_wield_option"),
             E(Trigger.AS_WEAPON, "judgement_single_use"),
         )),

    _def("major_21", big_name="The World", level=21, card_types=BOSS,
         is_elusive=True,
         text="After death: If both copies are dead and two Good players jointly announce, "
              "Good wins!",
         effects=(E(Trigger.AFTER_DEATH, "world_win_check"),)),
]


# ---------------------------------------------------------------------------
# GUARD CARDS (4 unique, 4 copies each = 16 total)
# ---------------------------------------------------------------------------

GUARD_DEFS: list[CardDef] = [
    _def("guards_1", level=8, card_types=GUARD,
         text="You cannot run while this is on your field. "
              "On placement: If nothing beneath, draw another card beneath this. "
              "After death: Draw another guard into refresh pile.",
         effects=(
             E(Trigger.PREVENT_RUN, "guard_prevent_run"),
             E(Trigger.ON_PLACEMENT, "guard_draw_underneath"),
             E(Trigger.AFTER_DEATH, "guard_respawn"),
         )),

    _def("guards_2", level=9, card_types=GUARD,
         text="You cannot run while this is on your field. "
              "On placement: If nothing beneath, draw another card beneath this. "
              "After death: Draw another guard into refresh pile.",
         effects=(
             E(Trigger.PREVENT_RUN, "guard_prevent_run"),
             E(Trigger.ON_PLACEMENT, "guard_draw_underneath"),
             E(Trigger.AFTER_DEATH, "guard_respawn"),
         )),

    _def("guards_3", level=10, card_types=GUARD,
         text="You cannot run while this is on your field. "
              "On placement: If nothing beneath, draw another card beneath this. "
              "After death: Draw another guard into refresh pile.",
         effects=(
             E(Trigger.PREVENT_RUN, "guard_prevent_run"),
             E(Trigger.ON_PLACEMENT, "guard_draw_underneath"),
             E(Trigger.AFTER_DEATH, "guard_respawn"),
         )),

    _def("guards_4", level=11, card_types=GUARD,
         text="You cannot run while this is on your field. "
              "On placement: If nothing beneath, draw another card beneath this. "
              "After death: Draw another guard into refresh pile.",
         effects=(
             E(Trigger.PREVENT_RUN, "guard_prevent_run"),
             E(Trigger.ON_PLACEMENT, "guard_draw_underneath"),
             E(Trigger.AFTER_DEATH, "guard_respawn"),
         )),
]


# ---------------------------------------------------------------------------
# ROLE CARDS — GOOD
# ---------------------------------------------------------------------------

GOOD_ROLE_DEFS: list[CardDef] = [
    _def("good_role_1", big_name="Human", card_types=ROLE_GOOD,
         text="While equipped: As a Last Resort, discard this to disarm the other player "
              "then stack a guard on each of their action slots.",
         effects=(E(Trigger.WHILE_EQUIPPED, "human_call_guards"),)),

    _def("good_role_2", big_name="Cardsharp", card_types=ROLE_GOOD,
         text="During Refresh Phase, you may look at and rearrange your action cards. "
              "The other player does not require your consent to resolve your action slots.",
         effects=(
             E(Trigger.CONTINUOUS, "cardsharp_no_consent_needed"),
             # Cardsharp rearrangement is handled as a phase-level decision, not a card effect
         )),

    _def("good_role_3", big_name="Mutineer", card_types=ROLE_GOOD,
         text="At start of game, discard this. "
              "As a Last Resort, you may attack the other player.",
         effects=(
             E(Trigger.SETUP, "mutineer_setup_discard"),
             # Mutiny is handled as a last resort option, not a card effect
         )),

    _def("good_role_4", big_name="The Fool", card_types=ROLE_GOOD,
         text="At start of game, discard this. Add a copy of The Fool to your deck. "
              "All copies of The Fool are your role card: refresh instead of discard, "
              "refresh on weapon discard.",
         effects=(
             E(Trigger.SETUP, "fool_role_setup"),
             E(Trigger.CONTINUOUS, "fool_role_redirect"),
         )),

    _def("good_role_5", big_name="Survivor", card_types=ROLE_GOOD,
         text="As an action, you (or with consent, the other player) may place a counter "
              "on this and resolve the top of your (their) deck. "
              "Additional actions to remove counters. "
              "At end of Refresh Phase, take damage equal to counters.",
         effects=(
             E(Trigger.CONTINUOUS, "survivor_extra_action"),
             E(Trigger.REFRESH_PHASE_END, "survivor_counter_damage"),
         )),

    _def("good_role_6", big_name="Two-Armed Freak", card_types=ROLE_GOOD,
         text="Left and right weapon slots: odd- and even-leveled weapons only, "
              "used against odd- and even-leveled enemies respectively.",
         effects=(E(Trigger.SETUP, "two_armed_freak_setup"),)),

    _def("good_role_7", big_name="The Ocean", card_types=ROLE_GOOD,
         text="Cannot call guards. "
              "Counter-based: add counter at any time if none; then at end of Action Phase, "
              "add counter, spawn guards into other's refresh; discard at 5.",
         effects=(
             E(Trigger.CONTINUOUS, "ocean_no_guards"),
             E(Trigger.CONTINUOUS, "ocean_counter_mechanic"),
         )),

    _def("good_role_8", big_name="Detective", card_types=ROLE_GOOD,
         text="Cannot call guards. "
              "On discard: Look through entire deck and refresh pile.",
         effects=(
             E(Trigger.CONTINUOUS, "detective_no_guards"),
             E(Trigger.ON_DISCARD, "detective_view_deck"),
         )),
]


# ---------------------------------------------------------------------------
# ROLE CARDS — EVIL
# ---------------------------------------------------------------------------

EVIL_ROLE_DEFS: list[CardDef] = [
    _def("bad_role_1", big_name="???", card_types=ROLE_EVIL,
         text="(Base Evil role — no special abilities.)"),

    _def("bad_role_2", big_name="Cardsharp", card_types=ROLE_EVIL,
         text="During Refresh Phase, you may look at and rearrange your action cards. "
              "The other player does not require your consent to resolve your action slots.",
         effects=(
             E(Trigger.CONTINUOUS, "cardsharp_no_consent_needed"),
         )),

    _def("bad_role_3", big_name="Foo(d) Fighter", card_types=ROLE_EVIL,
         text="Whenever you would wield a weapon, instead eat it as food. "
              "Whenever you would eat food, instead wield it as a weapon.",
         effects=(E(Trigger.CONTINUOUS, "food_fighter_swap"),)),

    _def("bad_role_4", big_name="Corruption", card_types=ROLE_EVIL,
         text="Heal 6HP per turn at end of Refresh Phase. "
              "Whenever you would heal by any other means, instead take that much damage.",
         effects=(
             E(Trigger.REFRESH_PHASE_END, "corruption_heal"),
             E(Trigger.CONTINUOUS, "corruption_invert_healing"),
         )),

    _def("bad_role_5", big_name="Survivor", card_types=ROLE_EVIL,
         text="As an action, you (or with consent, the other player) may place a counter "
              "on this and resolve the top of your (their) deck. "
              "Additional actions to remove counters. "
              "At end of Refresh Phase, take damage equal to counters.",
         effects=(
             E(Trigger.CONTINUOUS, "survivor_extra_action"),
             E(Trigger.REFRESH_PHASE_END, "survivor_counter_damage"),
         )),

    _def("bad_role_6", big_name="The Poet", card_types=ROLE_EVIL,
         text="When fighting a non-guard enemy, you may refresh it instead. "
              "Your weapons discard on first use.",
         effects=(
             E(Trigger.CONTINUOUS, "poet_refresh_enemy"),
             E(Trigger.AS_WEAPON, "poet_weapon_fragile"),
         )),

    _def("bad_role_7", big_name="The World", card_types=ROLE_EVIL,
         text="If The World dies on your action field, so do you. "
              "While equipped: If you would kill a non-guard enemy, you may instead "
              "place it in the other player's refresh pile.",
         effects=(
             E(Trigger.CONTINUOUS, "world_role_self_destruct"),
             E(Trigger.WHILE_EQUIPPED, "world_role_redirect_kill"),
         )),

    _def("bad_role_8", big_name="Phoenix", card_types=ROLE_EVIL,
         text="Cannot give HP. At end of each Refresh Phase, take 1 damage. "
              "If this kills you: Revive to d20 HP. Other player permanently loses "
              "role abilities and traits.",
         effects=(
             E(Trigger.CONTINUOUS, "phoenix_no_give_hp"),
             E(Trigger.REFRESH_PHASE_END, "phoenix_tick"),
         )),

    _def("bad_role_9", big_name="Leo", card_types=ROLE_EVIL,
         text="HP cap starts at 9. Whenever you die, reduce HP cap by 1 then "
              "revive to full HP.",
         effects=(
             E(Trigger.SETUP, "leo_setup"),
             E(Trigger.CONTINUOUS, "leo_revive"),
         )),
]


# ---------------------------------------------------------------------------
# Master registry: name → CardDef
# ---------------------------------------------------------------------------

ALL_CARD_DEFS: dict[str, CardDef] = {}

for _def_list in [
    FOOD_DEFS, WEAPON_DEFS, ENEMY_DEFS, MAJOR_DEFS, GUARD_DEFS,
    GOOD_ROLE_DEFS, EVIL_ROLE_DEFS,
]:
    for _cd in _def_list:
        assert _cd.name not in ALL_CARD_DEFS, f"Duplicate card name: {_cd.name}"
        ALL_CARD_DEFS[_cd.name] = _cd


def get_card_def(name: str) -> CardDef:
    """Look up a card definition by name. Raises KeyError if not found."""
    return ALL_CARD_DEFS[name]


# ---------------------------------------------------------------------------
# Deck composition: the 70 cards each player starts with
# ---------------------------------------------------------------------------

def standard_deck_names() -> list[str]:
    """
    Returns the list of card names in one player's starting deck (70 cards).
    Majors 0-21 (22 cards), enemies 1-14 x2 (28 cards), food 1-10 (10), weapon 1-10 (10).
    """
    names: list[str] = []

    # Major arcana: 0 through 21
    for i in range(0, 22):
        names.append(f"major_{i}")

    # Enemies: 1 through 14, two copies each
    for i in range(1, 15):
        names.append(f"enemy_{i}")
        names.append(f"enemy_{i}")

    # Food: 1 through 10
    for i in range(1, 11):
        names.append(f"food_{i}")

    # Weapons: 1 through 10
    for i in range(1, 11):
        names.append(f"weapon_{i}")

    assert len(names) == 70, f"Expected 70 cards, got {len(names)}"
    return names


def guard_deck_names() -> list[str]:
    """
    Returns the 16 guard card names (4 copies each of guards_1 through guards_4).
    """
    names: list[str] = []
    for i in range(1, 5):
        for _ in range(4):
            names.append(f"guards_{i}")
    assert len(names) == 16
    return names


# ---------------------------------------------------------------------------
# Role pools
# ---------------------------------------------------------------------------

# Base roles (recommended for beginners)
BASE_GOOD_ROLES: list[str] = ["good_role_1", "good_role_1"]  # Two Human cards
BASE_EVIL_ROLES: list[str] = ["bad_role_1"]                   # One ??? card

# All available good roles
ALL_GOOD_ROLES: list[str] = [f"good_role_{i}" for i in range(1, 9)]

# All available evil roles
ALL_EVIL_ROLES: list[str] = [f"bad_role_{i}" for i in range(1, 10)]


# ---------------------------------------------------------------------------
# Role pairing groups (for "with replacement" variant)
# Some roles share names across Good/Evil (e.g., Cardsharp, Survivor).
# These are the paired groups.
# ---------------------------------------------------------------------------

ROLE_PAIRS: dict[str, tuple[str, str]] = {
    "Cardsharp": ("good_role_2", "bad_role_2"),
    "Survivor": ("good_role_5", "bad_role_5"),
}


# ---------------------------------------------------------------------------
# Queries on card defs
# ---------------------------------------------------------------------------

def has_trigger(card_def: CardDef, trigger: Trigger) -> bool:
    return any(e.trigger == trigger for e in card_def.effects)


def get_handlers_for_trigger(card_def: CardDef, trigger: Trigger) -> list[str]:
    return [e.handler for e in card_def.effects if e.trigger == trigger]


def is_enemy_like(card_def: CardDef) -> bool:
    """Returns True if the card is fought in combat (ENEMY, BOSS, or GUARD)."""
    return bool(card_def.card_types & {CardType.ENEMY, CardType.BOSS, CardType.GUARD})


def is_food(card_def: CardDef) -> bool:
    return CardType.FOOD in card_def.card_types


def is_weapon(card_def: CardDef) -> bool:
    return CardType.WEAPON in card_def.card_types


def is_equipment(card_def: CardDef) -> bool:
    return CardType.EQUIPMENT in card_def.card_types


def is_event(card_def: CardDef) -> bool:
    return CardType.EVENT in card_def.card_types


def is_role(card_def: CardDef) -> bool:
    return bool(card_def.card_types & {CardType.ROLE_GOOD, CardType.ROLE_EVIL})


def is_good_role(card_def: CardDef) -> bool:
    return CardType.ROLE_GOOD in card_def.card_types


def is_evil_role(card_def: CardDef) -> bool:
    return CardType.ROLE_EVIL in card_def.card_types
