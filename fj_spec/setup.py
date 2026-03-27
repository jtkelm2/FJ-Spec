"""
Fools' Journey — Executable Spec
Stage 2: Game initialization and Setup Phase.

Creates a fully initialized GameState ready for the first Refresh Phase.
Handles:
  - CardId allocation and registry construction
  - Two identical 70-card decks (shuffled)
  - 16-card guard deck (shuffled)
  - Role assignment (2 Good + 1 Evil, random draw)
  - Per-role setup effects (Mutineer, Fool, Leo, Two-Armed Freak)
"""

from __future__ import annotations

from .types import (
    CardId, CardDef, CardState, CardType,
    PlayerId, Alignment, Parity,
    PlayerState, WeaponSlot, ManipulationField,
    ActionSlot, ActionField,
    GameState, Phase,
    SetupContext, RefreshContext,
    PendingDecision, Continuation,
)
from .cards import (
    ALL_CARD_DEFS, get_card_def,
    standard_deck_names, guard_deck_names,
    is_good_role, is_evil_role,
)
from .rng import RngState, rng_create, rng_shuffle


# ---------------------------------------------------------------------------
# CardId allocator
# ---------------------------------------------------------------------------

class CardIdAllocator:
    """Sequential CardId allocator."""

    def __init__(self, start: int = 1):
        self._next = start

    def alloc(self) -> CardId:
        cid = self._next
        self._next += 1
        return cid


# ---------------------------------------------------------------------------
# Deck building
# ---------------------------------------------------------------------------

def _build_card_registry(
    allocator: CardIdAllocator,
    card_names: list[str],
) -> tuple[dict[CardId, CardDef], list[CardId]]:
    """
    Allocate CardIds for a list of card names, returning
    (registry_entries, ordered_card_ids).
    """
    registry: dict[CardId, CardDef] = {}
    card_ids: list[CardId] = []
    for name in card_names:
        cid = allocator.alloc()
        registry[cid] = get_card_def(name)
        card_ids.append(cid)
    return registry, card_ids


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------

def _select_roles(
    rng: RngState,
    good_pool: list[str],
    evil_pool: list[str],
) -> tuple[RngState, str, str, str]:
    """
    From good_pool and evil_pool, select 2 Good + 1 Evil role,
    shuffle, and draw one for each player.

    Returns (rng, red_role_name, blue_role_name, unused_role_name).
    """
    # Pick 2 good roles (without replacement from pool)
    rng, shuffled_good = rng_shuffle(rng, list(good_pool))
    chosen_good = shuffled_good[:2]

    # Pick 1 evil role
    rng, shuffled_evil = rng_shuffle(rng, list(evil_pool))
    chosen_evil = shuffled_evil[:1]

    # Mix 3 roles and shuffle
    role_pile = chosen_good + chosen_evil
    rng, role_pile = rng_shuffle(rng, role_pile)

    # Player RED draws first, then BLUE
    red_role = role_pile[0]
    blue_role = role_pile[1]
    unused_role = role_pile[2]

    return rng, red_role, blue_role, unused_role


# ---------------------------------------------------------------------------
# Setup effects per role
# ---------------------------------------------------------------------------

def _get_alignment(role_name: str) -> Alignment:
    cd = get_card_def(role_name)
    if is_good_role(cd):
        return Alignment.GOOD
    if is_evil_role(cd):
        return Alignment.EVIL
    raise ValueError(f"Role {role_name} is neither Good nor Evil")


def _apply_role_setup(
    player: PlayerState,
    role_card_id: CardId,
    role_def: CardDef,
    allocator: CardIdAllocator,
    card_registry: dict[CardId, CardDef],
) -> PlayerState:
    """
    Apply one-time setup effects for a role.
    Returns updated PlayerState (and may add cards to card_registry as side effect).
    """
    role_name = role_def.name

    # --- Mutineer (good_role_3): Discard role card at start ---
    if role_name == "good_role_3":
        # Role card goes to discard; player no longer has it equipped
        # but retains alignment and role_def_name permanently
        equipment = list(player.equipment)
        for i, eq in enumerate(equipment):
            if eq == role_card_id:
                equipment[i] = None
                break
        return PlayerState(
            hp=player.hp,
            hp_cap=player.hp_cap,
            alignment=player.alignment,
            role_def_name=player.role_def_name,
            role_card_id=None,  # No longer has the physical card
            permanent_abilities=player.permanent_abilities,
            deck=player.deck,
            refresh_pile=player.refresh_pile,
            discard_pile=player.discard_pile + (role_card_id,),
            hand=player.hand,
            manipulation_field=player.manipulation_field,
            equipment=(equipment[0], equipment[1]),
            weapon_slots=player.weapon_slots,
        )

    # --- The Fool role (good_role_4): Discard role card, add Fool event copy ---
    if role_name == "good_role_4":
        # Discard role card
        equipment = list(player.equipment)
        for i, eq in enumerate(equipment):
            if eq == role_card_id:
                equipment[i] = None
                break

        # Add a second copy of The Fool event (major_0) to the deck
        fool_def = get_card_def("major_0")
        extra_fool_id = allocator.alloc()
        card_registry[extra_fool_id] = fool_def

        # Insert extra fool into deck (will be shuffled later during Refresh)
        new_deck = player.deck + (extra_fool_id,)

        return PlayerState(
            hp=player.hp,
            hp_cap=player.hp_cap,
            alignment=player.alignment,
            role_def_name=player.role_def_name,
            role_card_id=None,
            permanent_abilities=player.permanent_abilities,
            deck=new_deck,
            refresh_pile=player.refresh_pile,
            discard_pile=player.discard_pile + (role_card_id,),
            hand=player.hand,
            manipulation_field=player.manipulation_field,
            equipment=(equipment[0], equipment[1]),
            weapon_slots=player.weapon_slots,
        )

    # --- Leo (bad_role_9): HP cap starts at 9 ---
    if role_name == "bad_role_9":
        return PlayerState(
            hp=9,
            hp_cap=9,
            alignment=player.alignment,
            role_def_name=player.role_def_name,
            role_card_id=player.role_card_id,
            permanent_abilities=player.permanent_abilities,
            deck=player.deck,
            refresh_pile=player.refresh_pile,
            discard_pile=player.discard_pile,
            hand=player.hand,
            manipulation_field=player.manipulation_field,
            equipment=player.equipment,
            weapon_slots=player.weapon_slots,
        )

    # --- Two-Armed Freak (good_role_6): Two weapon slots ---
    if role_name == "good_role_6":
        return PlayerState(
            hp=player.hp,
            hp_cap=player.hp_cap,
            alignment=player.alignment,
            role_def_name=player.role_def_name,
            role_card_id=player.role_card_id,
            permanent_abilities=player.permanent_abilities,
            deck=player.deck,
            refresh_pile=player.refresh_pile,
            discard_pile=player.discard_pile,
            hand=player.hand,
            manipulation_field=player.manipulation_field,
            equipment=player.equipment,
            weapon_slots=(
                WeaponSlot(parity=Parity.ODD),
                WeaponSlot(parity=Parity.EVEN),
            ),
        )

    # --- All other roles: no special setup ---
    return player


# ---------------------------------------------------------------------------
# Main initialization
# ---------------------------------------------------------------------------

def create_initial_state(
    seed: int,
    good_pool: list[str] | None = None,
    evil_pool: list[str] | None = None,
) -> GameState:
    """
    Create a fully initialized GameState ready for the first Refresh Phase.

    Args:
        seed: RNG seed for deterministic play.
        good_pool: List of good role card names to select from.
                   Defaults to base roles (two Humans).
        evil_pool: List of evil role card names to select from.
                   Defaults to base role (one ???).

    Returns:
        A GameState with phase=REFRESH, both decks shuffled, roles assigned,
        and setup effects applied.
    """
    from .cards import BASE_GOOD_ROLES, BASE_EVIL_ROLES

    if good_pool is None:
        good_pool = BASE_GOOD_ROLES
    if evil_pool is None:
        evil_pool = BASE_EVIL_ROLES

    rng = rng_create(seed)
    allocator = CardIdAllocator()
    full_registry: dict[CardId, CardDef] = {}

    # --- Build player decks ---
    deck_names = standard_deck_names()  # 70 cards

    red_registry, red_deck_ids = _build_card_registry(allocator, deck_names)
    full_registry.update(red_registry)

    blue_registry, blue_deck_ids = _build_card_registry(allocator, deck_names)
    full_registry.update(blue_registry)

    # --- Build guard deck ---
    guard_names = guard_deck_names()  # 16 cards
    guard_registry, guard_ids = _build_card_registry(allocator, guard_names)
    full_registry.update(guard_registry)

    # --- Shuffle decks ---
    rng, red_deck_ids = rng_shuffle(rng, red_deck_ids)
    rng, blue_deck_ids = rng_shuffle(rng, blue_deck_ids)
    rng, guard_ids = rng_shuffle(rng, guard_ids)

    # --- Assign roles ---
    rng, red_role_name, blue_role_name, _unused = _select_roles(
        rng, good_pool, evil_pool
    )

    # Create role card instances
    red_role_id = allocator.alloc()
    full_registry[red_role_id] = get_card_def(red_role_name)

    blue_role_id = allocator.alloc()
    full_registry[blue_role_id] = get_card_def(blue_role_name)

    # --- Build initial player states ---
    red_player = PlayerState(
        hp=20,
        hp_cap=20,
        alignment=_get_alignment(red_role_name),
        role_def_name=red_role_name,
        role_card_id=red_role_id,
        deck=tuple(red_deck_ids),
        equipment=(red_role_id, None),  # Role card in first equipment slot
        weapon_slots=(WeaponSlot(),),
    )

    blue_player = PlayerState(
        hp=20,
        hp_cap=20,
        alignment=_get_alignment(blue_role_name),
        role_def_name=blue_role_name,
        role_card_id=blue_role_id,
        deck=tuple(blue_deck_ids),
        equipment=(blue_role_id, None),  # Role card in first equipment slot
        weapon_slots=(WeaponSlot(),),
    )

    # --- Apply role-specific setup effects ---
    red_player = _apply_role_setup(
        red_player, red_role_id, get_card_def(red_role_name),
        allocator, full_registry,
    )
    blue_player = _apply_role_setup(
        blue_player, blue_role_id, get_card_def(blue_role_name),
        allocator, full_registry,
    )

    # --- Random initial priority ---
    rng, priority_val = rng_shuffle(rng, [PlayerId.RED, PlayerId.BLUE])
    initial_priority = priority_val[0]

    # --- Construct empty action field ---
    empty_slots = (ActionSlot(), ActionSlot(), ActionSlot(), ActionSlot())
    action_field = ActionField(red_slots=empty_slots, blue_slots=empty_slots)

    # --- Assemble game state ---
    # Phase is REFRESH: the first Refresh Phase will execute before any play.
    state = GameState(
        rng_seed=rng.seed,
        rng_counter=rng.counter,
        phase=Phase.REFRESH,
        phase_context=RefreshContext(),
        priority=initial_priority,
        turn_number=1,
        game_result=None,
        players=(red_player, blue_player),
        guard_deck=tuple(guard_ids),
        action_field=action_field,
        card_defs=full_registry,
        card_states={},
        pending=None,
        continuation_stack=(),
    )

    return state


# ---------------------------------------------------------------------------
# Query helpers on initialized state
# ---------------------------------------------------------------------------

def get_player_role_def(state: GameState, pid: PlayerId) -> CardDef | None:
    """Get the CardDef of a player's assigned role (even if card was discarded)."""
    name = state.player(pid).role_def_name
    if name is None:
        return None
    return get_card_def(name)


def player_has_role_equipped(state: GameState, pid: PlayerId) -> bool:
    """Check if the player still has their physical role card in equipment."""
    ps = state.player(pid)
    return ps.role_card_id is not None and ps.role_card_id in ps.equipment


def player_can_call_guards(state: GameState, pid: PlayerId) -> bool:
    """
    Check if a player can Call the Guards as a Last Resort.
    Requirements:
      - Player is Good
      - Player has their role card equipped
      - Role doesn't forbid calling guards (Ocean, Detective)
    """
    ps = state.player(pid)
    if ps.alignment != Alignment.GOOD:
        return False
    if not player_has_role_equipped(state, pid):
        return False
    role_name = ps.role_def_name
    # Ocean and Detective cannot call guards
    if role_name in ("good_role_7", "good_role_8"):
        return False
    return True


def player_can_mutiny(state: GameState, pid: PlayerId) -> bool:
    """Check if a player can perform Mutiny (Mutineer role only)."""
    ps = state.player(pid)
    return ps.role_def_name == "good_role_3"


def player_is_cardsharp(state: GameState, pid: PlayerId) -> bool:
    """Check if a player has the Cardsharp role (Good or Evil)."""
    ps = state.player(pid)
    return ps.role_def_name in ("good_role_2", "bad_role_2")


def player_is_food_fighter(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_3"


def player_is_corruption(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_4"


def player_is_survivor(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name in ("good_role_5", "bad_role_5")


def player_is_poet(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_6"


def player_is_world_role(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_7"


def player_is_phoenix(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_8"


def player_is_leo(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "bad_role_9"


def player_is_fool_role(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "good_role_4"


def player_is_two_armed_freak(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "good_role_6"


def player_is_ocean(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "good_role_7"


def player_is_detective(state: GameState, pid: PlayerId) -> bool:
    ps = state.player(pid)
    return ps.role_def_name == "good_role_8"
