#!/usr/bin/env python3
"""Stage 2 validation: game initialization and Setup Phase."""

from fj_spec.types import (
    PlayerId, Alignment, Parity, Phase, CardType,
    ActionSlot, WeaponSlot, RefreshContext,
)
from fj_spec.cards import (
    get_card_def, is_good_role, is_evil_role, ALL_CARD_DEFS,
)
from fj_spec.setup import (
    create_initial_state,
    get_player_role_def, player_has_role_equipped,
    player_can_call_guards, player_can_mutiny,
    player_is_cardsharp, player_is_two_armed_freak,
    player_is_leo, player_is_fool_role,
)
from fj_spec.rng import rng_create, rng_shuffle, rng_d20


def test_rng_determinism():
    print("Testing RNG determinism...")
    r1 = rng_create(42)
    r2 = rng_create(42)

    r1a, v1a = rng_d20(r1)
    r2a, v2a = rng_d20(r2)
    assert v1a == v2a, f"Same seed should give same d20: {v1a} != {v2a}"

    r1b, v1b = rng_d20(r1a)
    r2b, v2b = rng_d20(r2a)
    assert v1b == v2b, f"Second d20 should also match: {v1b} != {v2b}"

    # Different seeds should (almost certainly) differ
    r3 = rng_create(999)
    _, v3 = rng_shuffle(r3, list(range(100)))
    _, v4 = rng_shuffle(rng_create(42), list(range(100)))
    assert v3 != v4, "Different seeds should produce different shuffles"

    print("  PASS")


def test_basic_initialization():
    print("Testing basic game initialization...")
    state = create_initial_state(seed=12345)

    # Phase should be REFRESH (first Refresh about to happen)
    assert state.phase == Phase.REFRESH
    assert isinstance(state.phase_context, RefreshContext)
    assert state.turn_number == 1
    assert state.game_result is None
    assert state.pending is None
    assert state.continuation_stack == ()

    print("  Phase: REFRESH, turn 1")
    print("  PASS")


def test_deck_sizes():
    print("Testing deck sizes...")
    state = create_initial_state(seed=42)

    red = state.player(PlayerId.RED)
    blue = state.player(PlayerId.BLUE)

    # Each player starts with 70 cards in deck
    # (minus role cards that might have been discarded to discard pile)
    red_total = len(red.deck) + len(red.discard_pile)
    blue_total = len(blue.deck) + len(blue.discard_pile)

    # With base roles (Human/???), no cards are discarded during setup
    # so deck should be exactly 70
    assert len(red.deck) == 70, f"Red deck: expected 70, got {len(red.deck)}"
    assert len(blue.deck) == 70, f"Blue deck: expected 70, got {len(blue.deck)}"

    # Guard deck: 16 cards
    assert len(state.guard_deck) == 16, f"Guard deck: expected 16, got {len(state.guard_deck)}"

    # All guard cards should have GUARD type
    for gid in state.guard_deck:
        gdef = state.card_def(gid)
        assert CardType.GUARD in gdef.card_types, f"Guard card {gid} missing GUARD type"

    print(f"  Red deck: {len(red.deck)}, Blue deck: {len(blue.deck)}, Guards: {len(state.guard_deck)}")
    print("  PASS")


def test_role_assignment_base():
    print("Testing base role assignment (2 Human + 1 ???)...")
    # Run many seeds to verify distribution
    good_count = 0
    evil_count = 0
    both_good = 0
    trials = 200

    for seed in range(trials):
        state = create_initial_state(seed=seed)
        red = state.player(PlayerId.RED)
        blue = state.player(PlayerId.BLUE)

        # Each player must have an alignment
        assert red.alignment in (Alignment.GOOD, Alignment.EVIL)
        assert blue.alignment in (Alignment.GOOD, Alignment.EVIL)

        # Exactly one Evil among the two players (since pool is 2G+1E, one is unused)
        # Actually: 2G+1E shuffled, draw 2. Could be GG, GE, or EG.
        # GG probability = 2/3 * 1/2 = 1/3
        # GE or EG probability = 2/3
        if red.alignment == Alignment.GOOD:
            good_count += 1
        else:
            evil_count += 1

        if red.alignment == Alignment.GOOD and blue.alignment == Alignment.GOOD:
            both_good += 1

        # Role def name should be set
        assert red.role_def_name is not None
        assert blue.role_def_name is not None

    # Statistical checks (loose bounds — these should hold for 200 trials)
    assert evil_count > 0, "Should see at least some Evil RED players"
    assert good_count > 0, "Should see at least some Good RED players"
    # Both-Good should happen roughly 1/3 of the time
    assert both_good > 20, f"Both-Good should happen ~67 times, got {both_good}"
    assert both_good < 150, f"Both-Good too frequent: {both_good}"

    print(f"  Over {trials} trials: RED was Good {good_count}x, Evil {evil_count}x")
    print(f"  Both-Good occurred {both_good}x (expected ~{trials // 3})")
    print("  PASS")


def test_role_equipment():
    print("Testing role card equipment...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        role_def = get_player_role_def(state, pid)
        assert role_def is not None

        # Base roles (Human/???) should have role card equipped
        if ps.role_def_name in ("good_role_1", "bad_role_1"):
            assert player_has_role_equipped(state, pid), (
                f"Player {pid.name} with {ps.role_def_name} should have role equipped"
            )
            assert ps.role_card_id is not None
            assert ps.role_card_id in ps.equipment

    print("  Base role cards correctly equipped")
    print("  PASS")


def test_hp_initialization():
    print("Testing HP initialization...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        # With base roles, HP starts at 20
        assert ps.hp == 20, f"{pid.name} HP should be 20, got {ps.hp}"
        assert ps.hp_cap == 20, f"{pid.name} HP cap should be 20, got {ps.hp_cap}"
        assert not ps.is_dead

    print("  Both players at 20/20 HP")
    print("  PASS")


def test_empty_action_field():
    print("Testing empty action field...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        slots = state.action_field.slots_for(pid)
        for i, slot in enumerate(slots):
            assert slot.is_empty, f"{pid.name} slot {i} should be empty at start"

    print("  All 8 action slots empty")
    print("  PASS")


def test_empty_hands_and_manipulation():
    print("Testing empty hands and manipulation fields...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        assert ps.hand == (), f"{pid.name} hand should be empty"
        assert ps.manipulation_field.cards == (), f"{pid.name} manipulation should be empty"
        assert ps.refresh_pile == (), f"{pid.name} refresh pile should be empty"

    print("  Hands, manipulation fields, and refresh piles all empty")
    print("  PASS")


def test_card_registry_completeness():
    print("Testing card registry completeness...")
    state = create_initial_state(seed=42)

    # All card IDs in decks should be in the registry
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        for cid in ps.deck:
            assert cid in state.card_defs, f"Card {cid} in {pid.name}'s deck not in registry"
        for cid in ps.discard_pile:
            assert cid in state.card_defs, f"Card {cid} in {pid.name}'s discard not in registry"
        for eq in ps.equipment:
            if eq is not None:
                assert eq in state.card_defs, f"Equipment {eq} not in registry"

    for gid in state.guard_deck:
        assert gid in state.card_defs, f"Guard {gid} not in registry"

    # Red and Blue card IDs should be disjoint (except role cards are separate)
    red_ids = set(state.player(PlayerId.RED).deck)
    blue_ids = set(state.player(PlayerId.BLUE).deck)
    assert red_ids.isdisjoint(blue_ids), "Red and Blue deck card IDs should be disjoint"

    print(f"  Registry size: {len(state.card_defs)} entries")
    print("  All card IDs valid and disjoint between players")
    print("  PASS")


def test_determinism():
    print("Testing deterministic initialization...")
    s1 = create_initial_state(seed=777)
    s2 = create_initial_state(seed=777)

    # Same seed → same state
    assert s1.player(PlayerId.RED).deck == s2.player(PlayerId.RED).deck
    assert s1.player(PlayerId.BLUE).deck == s2.player(PlayerId.BLUE).deck
    assert s1.guard_deck == s2.guard_deck
    assert s1.priority == s2.priority
    assert s1.player(PlayerId.RED).alignment == s2.player(PlayerId.RED).alignment
    assert s1.player(PlayerId.BLUE).alignment == s2.player(PlayerId.BLUE).alignment

    # Different seed → (almost certainly) different state
    s3 = create_initial_state(seed=778)
    differ = (
        s1.player(PlayerId.RED).deck != s3.player(PlayerId.RED).deck
        or s1.priority != s3.priority
        or s1.player(PlayerId.RED).alignment != s3.player(PlayerId.RED).alignment
    )
    assert differ, "Different seeds should produce different states"

    print("  Same seed → identical state, different seed → different state")
    print("  PASS")


def test_shuffled_decks():
    print("Testing that decks are shuffled...")
    state = create_initial_state(seed=42)

    red = state.player(PlayerId.RED)
    blue = state.player(PlayerId.BLUE)

    # Decks should not be in the default sorted order
    # (extremely unlikely with a proper shuffle)
    red_names = [state.card_def(cid).name for cid in red.deck]
    sorted_names = sorted(red_names)
    assert red_names != sorted_names, "Red deck should be shuffled"

    # Red and Blue decks should contain the same card DEFINITIONS
    # but different CardIds
    red_name_sorted = sorted(state.card_def(cid).name for cid in red.deck)
    blue_name_sorted = sorted(state.card_def(cid).name for cid in blue.deck)
    assert red_name_sorted == blue_name_sorted, (
        "Both decks should have the same card composition"
    )

    print("  Decks are shuffled with matching composition")
    print("  PASS")


# ---------------------------------------------------------------------------
# Advanced role tests (using specific role pools)
# ---------------------------------------------------------------------------

def test_mutineer_setup():
    print("Testing Mutineer setup (discard role card)...")
    # Force both roles to include Mutineer by using a pool of just Mutineer
    # and base evil. We need to find a seed where RED gets Mutineer.
    found = False
    for seed in range(500):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_3", "good_role_3"],  # Two Mutineers
            evil_pool=["bad_role_1"],
        )
        red = state.player(PlayerId.RED)
        if red.role_def_name == "good_role_3":
            # Mutineer: role card should be discarded
            assert red.role_card_id is None, "Mutineer should have no role card"
            assert not player_has_role_equipped(state, PlayerId.RED)
            assert len(red.discard_pile) == 1, "Mutineer's role card should be in discard"
            assert red.alignment == Alignment.GOOD
            assert not player_can_call_guards(state, PlayerId.RED), (
                "Mutineer can't call guards (no role card equipped)"
            )
            assert player_can_mutiny(state, PlayerId.RED)
            found = True
            print(f"  Found Mutineer RED at seed={seed}")
            break

    assert found, "Should find a Mutineer RED in 500 seeds"
    print("  Role card discarded, abilities retained")
    print("  PASS")


def test_fool_role_setup():
    print("Testing Fool role setup (discard + add event copy)...")
    found = False
    for seed in range(500):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_4", "good_role_4"],  # Two Fools
            evil_pool=["bad_role_1"],
        )
        red = state.player(PlayerId.RED)
        if red.role_def_name == "good_role_4":
            # Fool role: role card discarded, extra Fool event added to deck
            assert red.role_card_id is None
            assert len(red.discard_pile) == 1

            # Deck should now have 71 cards (70 + 1 extra Fool event)
            assert len(red.deck) == 71, f"Fool's deck should be 71, got {len(red.deck)}"

            # Count Fool events in deck
            fool_count = sum(
                1 for cid in red.deck
                if state.card_def(cid).name == "major_0"
            )
            assert fool_count == 2, f"Should have 2 Fool events, got {fool_count}"

            found = True
            print(f"  Found Fool role RED at seed={seed}")
            break

    assert found, "Should find a Fool role RED in 500 seeds"
    print("  Role card discarded, 2 Fool events in deck (71 cards)")
    print("  PASS")


def test_leo_setup():
    print("Testing Leo setup (HP cap 9)...")
    found = False
    for seed in range(500):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_9"],  # Leo
        )
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            ps = state.player(pid)
            if ps.role_def_name == "bad_role_9":
                assert ps.hp == 9, f"Leo HP should be 9, got {ps.hp}"
                assert ps.hp_cap == 9, f"Leo HP cap should be 9, got {ps.hp_cap}"
                assert ps.alignment == Alignment.EVIL
                found = True
                print(f"  Found Leo at {pid.name}, seed={seed}")
                break
        if found:
            break

    assert found, "Should find Leo in 500 seeds"
    print("  HP and HP cap both 9")
    print("  PASS")


def test_two_armed_freak_setup():
    print("Testing Two-Armed Freak setup (two weapon slots)...")
    found = False
    for seed in range(500):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_6", "good_role_6"],  # Two-Armed Freak
            evil_pool=["bad_role_1"],
        )
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            ps = state.player(pid)
            if ps.role_def_name == "good_role_6":
                assert len(ps.weapon_slots) == 2, (
                    f"Two-Armed Freak should have 2 weapon slots, got {len(ps.weapon_slots)}"
                )
                assert ps.weapon_slots[0].parity == Parity.ODD
                assert ps.weapon_slots[1].parity == Parity.EVEN
                found = True
                print(f"  Found Two-Armed Freak at {pid.name}, seed={seed}")
                break
        if found:
            break

    assert found, "Should find Two-Armed Freak in 500 seeds"
    print("  Two weapon slots with ODD/EVEN parity")
    print("  PASS")


def test_guard_calling_eligibility():
    print("Testing guard calling eligibility...")

    # Base game: Good player with Human role can call guards
    for seed in range(100):
        state = create_initial_state(seed=seed)
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            ps = state.player(pid)
            if ps.role_def_name == "good_role_1":  # Human
                assert player_can_call_guards(state, pid), (
                    f"Human at {pid.name} should be able to call guards"
                )
            elif ps.role_def_name == "bad_role_1":  # ???
                assert not player_can_call_guards(state, pid), (
                    f"Evil at {pid.name} should not be able to call guards"
                )

    # Ocean cannot call guards
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_7", "good_role_7"],  # Ocean
            evil_pool=["bad_role_1"],
        )
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            ps = state.player(pid)
            if ps.role_def_name == "good_role_7":
                assert not player_can_call_guards(state, pid), (
                    "Ocean should not be able to call guards"
                )
                break
        else:
            continue
        break

    # Detective cannot call guards
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_8", "good_role_8"],  # Detective
            evil_pool=["bad_role_1"],
        )
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            ps = state.player(pid)
            if ps.role_def_name == "good_role_8":
                assert not player_can_call_guards(state, pid), (
                    "Detective should not be able to call guards"
                )
                break
        else:
            continue
        break

    print("  Human: can call; Evil/Ocean/Detective: cannot")
    print("  PASS")


def test_weapon_slot_defaults():
    print("Testing default weapon slot configuration...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        if ps.role_def_name != "good_role_6":  # Not Two-Armed Freak
            assert len(ps.weapon_slots) == 1
            ws = ps.weapon_slots[0]
            assert ws.weapon is None
            assert ws.kill_pile == ()
            assert ws.parity is None

    print("  Single weapon slot, empty, no parity")
    print("  PASS")


def test_phase_tracking_defaults():
    print("Testing phase tracking defaults...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = state.player(pid)
        assert not ps.has_eaten_this_phase
        assert ps.action_plays_made == 0
        assert not ps.devil_used_this_phase
        assert not ps.sun_used_this_phase
        assert not ps.is_dead
        assert not ps.action_phase_over

    print("  All tracking flags at defaults")
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 2 Validation")
    print("=" * 60)
    test_rng_determinism()
    test_basic_initialization()
    test_deck_sizes()
    test_role_assignment_base()
    test_role_equipment()
    test_hp_initialization()
    test_empty_action_field()
    test_empty_hands_and_manipulation()
    test_card_registry_completeness()
    test_determinism()
    test_shuffled_decks()
    test_mutineer_setup()
    test_fool_role_setup()
    test_leo_setup()
    test_two_armed_freak_setup()
    test_guard_calling_eligibility()
    test_weapon_slot_defaults()
    test_phase_tracking_defaults()
    print("=" * 60)
    print("ALL STAGE 2 TESTS PASSED")
    print("=" * 60)
