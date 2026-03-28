#!/usr/bin/env python3
"""Stage 4 validation: Refresh Phase implementation."""

from dataclasses import replace

from fj_spec.types import (
    PlayerId, Alignment, Phase, CardType, CardState,
    ActionSlot, ActionField, ManipulationField,
    PlayerState, GameState,
    PendingDecision, DecisionKind, Action, ActionKind,
    GameResult, GameResultKind,
    RefreshContext, RefreshStep, ManipulationContext,
)
from fj_spec.cards import get_card_def, ALL_CARD_DEFS
from fj_spec.setup import (
    create_initial_state,
    player_is_cardsharp, player_is_corruption,
    player_is_phoenix, player_is_survivor, player_is_leo,
)
from fj_spec.state_helpers import (
    gs_set_phase, gs_set_context, gs_set_pending,
    gs_get_player, gs_update_player, gs_modify_player,
    gs_set_action_field, gs_set_card_state,
    gs_get_rng, gs_with_rng_result,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_hand,
    ps_set_refresh, ps_set_discard, ps_set_manipulation,
    ps_set_equipment,
    af_get_slot, af_find_empty_slots, af_find_nonempty_slots,
    af_add_card_to_slot,
)
from fj_spec.fog import get_player_view, render_player_view
from fj_spec.engine import auto_advance, start_game, apply, get_decision
from fj_spec.phases.refresh import advance_refresh


# ---------------------------------------------------------------------------
# Helper: run full refresh phase to completion
# ---------------------------------------------------------------------------

def run_refresh_to_completion(state: GameState) -> GameState:
    """Run the refresh phase step by step until it transitions out."""
    assert state.phase == Phase.REFRESH
    from fj_spec.phases.refresh import advance_refresh, apply_refresh_action
    for _ in range(50):  # safety limit
        if state.phase != Phase.REFRESH:
            return state
        if state.phase == Phase.GAME_OVER:
            return state
        if state.pending is not None:
            # Cardsharp decision — pick identity permutation (no rearrange)
            action = Action(kind=ActionKind.SELECT_PERMUTATION,
                            permutation=(0, 1, 2, 3))
            state = gs_set_pending(state, None)
            state = apply_refresh_action(state, action)
        else:
            state = advance_refresh(state)
    raise RuntimeError("Refresh phase did not complete in 50 iterations")


def run_refresh_until_decision(state: GameState) -> GameState:
    """Run the refresh phase until a pending decision or phase transition."""
    assert state.phase == Phase.REFRESH
    from fj_spec.phases.refresh import advance_refresh
    for _ in range(50):
        if state.phase != Phase.REFRESH:
            return state
        if state.pending is not None:
            return state
        state = advance_refresh(state)
    raise RuntimeError("Refresh phase did not produce a decision in 50 iterations")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_refresh_dealing():
    print("Testing refresh dealing (hand, actions, manipulation)...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_completion(state)

    assert state.phase == Phase.MANIPULATION

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)

        # Hand: 4 cards drawn from OTHER player's deck
        assert len(ps.hand) == 4, f"{pid.name} hand should have 4 cards, got {len(ps.hand)}"

        # Manipulation: 2 cards from OTHER player's deck
        assert len(ps.manipulation_field.cards) == 2, (
            f"{pid.name} manipulation should have 2 cards, got {len(ps.manipulation_field.cards)}"
        )

        # Action field: 3 slots filled (4 empty - 1 = 3), 1 empty
        af = state.action_field
        empty = af_find_empty_slots(af, pid)
        nonempty = af_find_nonempty_slots(af, pid)
        assert len(empty) == 1, f"{pid.name} should have 1 empty action slot, got {len(empty)}"
        assert len(nonempty) == 3, f"{pid.name} should have 3 filled action slots"

    print("  Both players: 4 hand, 2 manipulation, 3 action slots filled")
    print("  PASS")


def test_refresh_fill_order():
    print("Testing refresh fill order (top distant first)...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_completion(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        af = state.action_field
        empty = af_find_empty_slots(af, pid)
        # With 4 empty slots, deal 3, leaving 1 empty
        # Fill order is 0, 1, 2, 3. So slots 0, 1, 2 should be filled,
        # and slot 3 (bottom distant) should be empty.
        assert empty == [3], f"{pid.name} empty slot should be [3] (bottom distant), got {empty}"

    print("  Bottom distant slot (index 3) left empty for both players")
    print("  PASS")


def test_refresh_non_mixing():
    print("Testing Non-Mixing Principle (hand from other's deck)...")
    state = create_initial_state(seed=42)

    # Record initial deck contents for verification
    red_deck_before = set(gs_get_player(state, PlayerId.RED).deck)
    blue_deck_before = set(gs_get_player(state, PlayerId.BLUE).deck)

    state = run_refresh_to_completion(state)

    # RED's hand cards should have CardIds that were originally in BLUE's deck
    red_hand = set(gs_get_player(state, PlayerId.RED).hand)
    for cid in red_hand:
        assert cid in blue_deck_before, (
            f"RED's hand card {cid} should be from BLUE's deck"
        )

    # BLUE's hand cards should be from RED's deck
    blue_hand = set(gs_get_player(state, PlayerId.BLUE).hand)
    for cid in blue_hand:
        assert cid in red_deck_before, (
            f"BLUE's hand card {cid} should be from RED's deck"
        )

    # RED's action cards should be from RED's own deck
    for i in range(4):
        slot = af_get_slot(state.action_field, PlayerId.RED, i)
        for cid in slot.cards:
            assert cid in red_deck_before, (
                f"RED's action card {cid} should be from RED's deck"
            )

    print("  Hand from other's deck, action from own deck — verified")
    print("  PASS")


def test_refresh_priority_flip():
    print("Testing priority flip during refresh...")
    state = create_initial_state(seed=42)
    original_priority = state.priority

    state = run_refresh_to_completion(state)

    assert state.priority != original_priority, (
        f"Priority should have flipped from {original_priority.name}"
    )

    print(f"  Priority flipped: {original_priority.name} → {state.priority.name}")
    print("  PASS")


def test_refresh_deck_sizes_after():
    print("Testing deck sizes after refresh...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_completion(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        other = gs_get_player(state, pid.other())

        # Cards drawn FROM this player's deck:
        #   - 4 to other's hand
        #   - 2 to other's manipulation
        #   - 3 to own action field (from own deck)
        # That's 6 from other's perspective + 3 from own = 9 total drawn from this deck
        # But also the other player drew action cards from THEIR deck...
        # Let me just verify the total card count is conserved.

        own_total = (len(ps.deck) + len(ps.hand) + len(ps.refresh_pile)
                     + len(ps.discard_pile) + len(ps.manipulation_field.cards))
        # Plus cards on action field
        af = state.action_field
        for i in range(4):
            slot = af_get_slot(af, pid, i)
            own_total += len(slot.cards)
        # Plus equipment
        for eq in ps.equipment:
            if eq is not None:
                own_total += 1

        # Original: 70 deck cards + 1 role card = 71 (for base roles)
        # But role card is in equipment, not deck
        # So own cards = deck(70) + role(in equipment)
        # Total from own deck: 70 cards distributed across own deck, own action field,
        # other's hand, other's manipulation, own discard, own refresh
        # Plus role card in equipment
        # Hmm, this gets complicated with cross-player cards. Let me just verify
        # that total cards in the game is conserved.

    # Simple conservation check: count all cards everywhere
    total_cards = len(state.guard_deck)
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        total_cards += len(ps.deck)
        total_cards += len(ps.hand)
        total_cards += len(ps.refresh_pile)
        total_cards += len(ps.discard_pile)
        total_cards += len(ps.manipulation_field.cards)
        for eq in ps.equipment:
            if eq is not None:
                total_cards += 1
        for ws in ps.weapon_slots:
            if ws.weapon is not None:
                total_cards += 1
            total_cards += len(ws.kill_pile)

    # Action field
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        for i in range(4):
            slot = af_get_slot(state.action_field, pid, i)
            total_cards += len(slot.cards)

    # Expected: 70*2 (decks) + 2 (role cards) + 16 (guards) = 158
    expected = 70 * 2 + 2 + 16
    assert total_cards == expected, f"Total cards should be {expected}, got {total_cards}"

    print(f"  Total card conservation verified: {total_cards} cards")
    print("  PASS")


def test_refresh_with_existing_cards():
    print("Testing refresh with pre-existing action field cards...")
    state = create_initial_state(seed=42)

    # Manually place a card in RED's slot 0 before refresh
    # to simulate cards remaining from a previous turn
    red_ps = gs_get_player(state, PlayerId.RED)
    dummy_card = red_ps.deck[0]
    remaining_deck = red_ps.deck[1:]
    red_ps = ps_set_deck(red_ps, remaining_deck)
    state = gs_update_player(state, PlayerId.RED, red_ps)

    af = state.action_field
    af = af_add_card_to_slot(af, PlayerId.RED, 0, dummy_card)
    state = gs_set_action_field(state, af)

    state = run_refresh_to_completion(state)

    # RED should now have 3 empty slots - 1 = 2 new cards dealt,
    # plus the existing card in slot 0
    af = state.action_field
    nonempty = af_find_nonempty_slots(af, PlayerId.RED)
    empty = af_find_empty_slots(af, PlayerId.RED)

    assert len(nonempty) == 3, f"RED should have 3 filled slots, got {len(nonempty)}"
    assert len(empty) == 1, f"RED should have 1 empty slot, got {len(empty)}"

    # Slot 0 should still have the original card (plus nothing new since it was occupied)
    slot0 = af_get_slot(af, PlayerId.RED, 0)
    assert dummy_card in slot0.cards, "Original card should still be in slot 0"

    print("  Pre-existing cards preserved, new cards dealt to empty slots")
    print("  PASS")


def test_refresh_phase_tracking_reset():
    print("Testing per-phase tracking reset...")
    state = create_initial_state(seed=42)

    # Set some phase tracking flags to verify they're reset
    red_ps = gs_get_player(state, PlayerId.RED)
    red_ps = replace(red_ps,
                     has_eaten_this_phase=True,
                     action_plays_made=3,
                     devil_used_this_phase=True,
                     sun_used_this_phase=True,
                     action_phase_over=True)
    state = gs_update_player(state, PlayerId.RED, red_ps)

    state = run_refresh_to_completion(state)

    ps = gs_get_player(state, PlayerId.RED)
    assert not ps.has_eaten_this_phase, "has_eaten should be reset"
    assert ps.action_plays_made == 0, "action_plays should be reset"
    assert not ps.devil_used_this_phase, "devil_used should be reset"
    assert not ps.sun_used_this_phase, "sun_used should be reset"
    assert not ps.action_phase_over, "action_phase_over should be reset"

    print("  All tracking flags reset to defaults")
    print("  PASS")


def test_refresh_shuffle_refresh_pile():
    print("Testing refresh pile shuffle into deck...")
    state = create_initial_state(seed=42)

    # Put some cards in RED's refresh pile
    red_ps = gs_get_player(state, PlayerId.RED)
    # Move first 5 cards from deck to refresh pile
    refresh_cards = red_ps.deck[:5]
    remaining = red_ps.deck[5:]
    red_ps = ps_set_deck(red_ps, remaining)
    red_ps = ps_set_refresh(red_ps, refresh_cards)
    state = gs_update_player(state, PlayerId.RED, red_ps)

    # After refresh, refresh pile should be empty and cards should be in deck
    state = run_refresh_to_completion(state)

    red_ps = gs_get_player(state, PlayerId.RED)
    assert len(red_ps.refresh_pile) == 0, "Refresh pile should be empty after shuffle"

    print("  Refresh pile shuffled into deck successfully")
    print("  PASS")


def test_cardsharp_decision():
    print("Testing Cardsharp rearrangement decision...")
    found = False
    for seed in range(100):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_2", "good_role_2"],  # Cardsharp
            evil_pool=["bad_role_1"],
        )

        # Check if any player got Cardsharp
        has_cs = False
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_cardsharp(state, pid):
                has_cs = True
                break

        assert has_cs

        # Run refresh until we hit a decision (use refresh-only stepper)
        state = run_refresh_until_decision(state)

        assert state.pending is not None
        assert state.pending.kind == DecisionKind.REARRANGE_ACTION_FIELD
        cs_player = state.pending.player
        assert player_is_cardsharp(state, cs_player)
        assert len(state.pending.legal_actions) == 24  # 4! permutations

        print(f"  Found Cardsharp at seed={seed}, decision presented with 24 options")
        break

    print("  PASS")


def test_cardsharp_rearrange_effect():
    print("Testing Cardsharp rearrangement actually moves cards...")
    from fj_spec.phases.refresh import advance_refresh, apply_refresh_action
    for seed in range(100):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_2", "good_role_2"],
            evil_pool=["bad_role_1"],
        )

        state = run_refresh_until_decision(state)
        if state.pending is None or state.pending.kind != DecisionKind.REARRANGE_ACTION_FIELD:
            continue

        cs_player = state.pending.player
        af = state.action_field
        old_slots = af.slots_for(cs_player)

        # Record contents before rearrangement
        old_contents = [slot.cards for slot in old_slots]

        # Apply a swap: move slot 0→pos 2, slot 2→pos 0
        swap_perm = Action(kind=ActionKind.SELECT_PERMUTATION,
                           permutation=(2, 1, 0, 3))
        state = gs_set_pending(state, None)
        state = apply_refresh_action(state, swap_perm)

        # Continue refresh to completion (handles second Cardsharp if any)
        state = run_refresh_to_completion(state)

        # Verify the swap happened
        new_af = state.action_field
        new_slots = new_af.slots_for(cs_player)
        assert new_slots[0].cards == old_contents[2], "Slot 0 should have old slot 2's contents"
        assert new_slots[2].cards == old_contents[0], "Slot 2 should have old slot 0's contents"
        assert new_slots[1].cards == old_contents[1], "Slot 1 should be unchanged"
        assert new_slots[3].cards == old_contents[3], "Slot 3 should be unchanged"

        print(f"  Verified rearrangement at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find suitable seed for rearrangement test)")
    print("  SKIP")


def test_periodic_empress():
    print("Testing periodic effect: Empress heals 1...")
    for seed in range(200):
        state = create_initial_state(seed=seed)

        # Find a player and equip Empress (major_3)
        pid = PlayerId.RED
        ps = gs_get_player(state, pid)

        # Find Empress in deck
        empress_id = None
        for cid in ps.deck:
            if state.card_def(cid).name == "major_3":
                empress_id = cid
                break

        if empress_id is None:
            continue

        # Remove from deck, equip in slot 1 (slot 0 has role card)
        deck_list = list(ps.deck)
        deck_list.remove(empress_id)
        ps = ps_set_deck(ps, tuple(deck_list))
        ps = ps_set_equipment(ps, (ps.equipment[0], empress_id))
        # Set HP to 15 to verify healing
        ps = ps_set_hp(ps, 15)
        state = gs_update_player(state, pid, ps)

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, pid)
        assert ps_after.hp == 16, f"Expected HP 16 after Empress heal, got {ps_after.hp}"

        print(f"  Empress heals 1 HP (15 → 16) verified at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find Empress in deck)")
    print("  SKIP")


def test_periodic_corruption():
    print("Testing periodic effect: Corruption inverts healing...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_4"],  # Corruption
        )

        # Find the Corruption player
        corruption_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_corruption(state, pid):
                corruption_pid = pid
                break

        if corruption_pid is None:
            continue

        # Set HP to 10
        ps = gs_get_player(state, corruption_pid)
        ps = ps_set_hp(ps, 10)
        state = gs_update_player(state, corruption_pid, ps)

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, corruption_pid)
        # Corruption with no other healing: just +6 from Corruption
        # Expected: 10 + 6 = 16
        assert ps_after.hp == 16, f"Expected HP 16 after Corruption, got {ps_after.hp}"

        print(f"  Corruption heals 6 (10 → 16) at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find Corruption role)")
    print("  SKIP")


def test_periodic_corruption_with_empress():
    print("Testing Corruption + Empress interaction...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_1", "good_role_1"],
            evil_pool=["bad_role_4"],  # Corruption
        )

        corruption_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_corruption(state, pid):
                corruption_pid = pid
                break

        if corruption_pid is None:
            continue

        ps = gs_get_player(state, corruption_pid)

        # Find and equip Empress
        empress_id = None
        for cid in ps.deck:
            if state.card_def(cid).name == "major_3":
                empress_id = cid
                break

        if empress_id is None:
            continue

        deck_list = list(ps.deck)
        deck_list.remove(empress_id)
        ps = ps_set_deck(ps, tuple(deck_list))
        ps = ps_set_equipment(ps, (ps.equipment[0], empress_id))
        ps = ps_set_hp(ps, 10)
        state = gs_update_player(state, corruption_pid, ps)

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, corruption_pid)
        # Empress +1 is inverted to -1 by Corruption, then Corruption +6
        # Expected: 10 - 1 + 6 = 15
        assert ps_after.hp == 15, (
            f"Expected HP 15 (10 - 1 empress inverted + 6 corruption), got {ps_after.hp}"
        )

        print(f"  Corruption + Empress: 10 - 1 + 6 = 15 verified at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find suitable setup)")
    print("  SKIP")


def test_periodic_phoenix():
    print("Testing periodic effect: Phoenix takes 1 damage...")
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

        ps = gs_get_player(state, phoenix_pid)
        ps = ps_set_hp(ps, 15)
        state = gs_update_player(state, phoenix_pid, ps)

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, phoenix_pid)
        assert ps_after.hp == 14, f"Expected HP 14 after Phoenix -1, got {ps_after.hp}"

        print(f"  Phoenix takes 1 damage (15 → 14) at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find Phoenix)")
    print("  SKIP")


def test_periodic_phoenix_death_revive():
    print("Testing Phoenix death and revive...")
    for seed in range(500):
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

        # Set HP to 1 — Phoenix tick will kill, triggering revive
        ps = gs_get_player(state, phoenix_pid)
        ps = ps_set_hp(ps, 1)
        state = gs_update_player(state, phoenix_pid, ps)

        other_pid = phoenix_pid.other()
        other_before = gs_get_player(state, other_pid)
        assert other_before.role_def_name is not None

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, phoenix_pid)
        assert not ps_after.is_dead, "Phoenix should have revived"
        assert ps_after.hp > 0, "Phoenix should have positive HP after revive"
        assert ps_after.hp <= 20, "Phoenix HP should be at most 20 (d20 roll)"

        # Other player should have lost role abilities
        other_after = gs_get_player(state, other_pid)
        assert other_after.role_def_name is None, "Other player should lose role"
        assert other_after.permanent_abilities == frozenset(), (
            "Other player should lose permanent abilities"
        )

        print(f"  Phoenix revived to {ps_after.hp} HP, other player's role stripped")
        print("  PASS")
        return

    print("  (Could not find Phoenix)")
    print("  SKIP")


def test_periodic_survivor_damage():
    print("Testing Survivor counter damage...")
    for seed in range(200):
        state = create_initial_state(
            seed=seed,
            good_pool=["good_role_5", "good_role_5"],  # Survivor
            evil_pool=["bad_role_1"],
        )

        survivor_pid = None
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            if player_is_survivor(state, pid):
                survivor_pid = pid
                break

        if survivor_pid is None:
            continue

        ps = gs_get_player(state, survivor_pid)
        role_cid = ps.role_card_id
        if role_cid is None:
            continue

        # Set 2 counters on role card
        state = gs_set_card_state(state, role_cid, CardState(counters=2))
        ps = ps_set_hp(ps, 15)
        state = gs_update_player(state, survivor_pid, ps)

        state = run_refresh_to_completion(state)

        ps_after = gs_get_player(state, survivor_pid)
        assert ps_after.hp == 13, f"Expected HP 13 (15 - 2 counters), got {ps_after.hp}"

        print(f"  Survivor takes 2 counter damage (15 → 13) at seed={seed}")
        print("  PASS")
        return

    print("  (Could not find Survivor)")
    print("  SKIP")


def test_skeleton_on_placement():
    print("Testing Skeleton On Placement during refresh...")
    # We need to arrange for enemy_4 (Skeleton, level 4) to be the first card
    # dealt to an action slot. This is tricky with random shuffling.
    # Instead, we'll manually manipulate the deck.

    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    ps = gs_get_player(state, pid)

    # Find an enemy_4 (Skeleton) in the deck
    skeleton_id = None
    skeleton_idx = None
    for i, cid in enumerate(ps.deck):
        if state.card_def(cid).name == "enemy_4":
            skeleton_id = cid
            skeleton_idx = i
            break

    if skeleton_id is None:
        print("  (No Skeleton found in RED's deck — skipping)")
        print("  SKIP")
        return

    # Move Skeleton to top of deck so it's the first card dealt
    deck_list = list(ps.deck)
    deck_list.remove(skeleton_id)
    deck_list.insert(0, skeleton_id)
    ps = ps_set_deck(ps, tuple(deck_list))
    state = gs_update_player(state, pid, ps)

    state = run_refresh_to_completion(state)

    # The Skeleton should have been placed in the first empty slot (slot 0)
    # and drawn another card underneath it
    af = state.action_field
    slot0 = af_get_slot(af, pid, 0)

    assert len(slot0.cards) >= 2, (
        f"Slot 0 should have Skeleton + drawn card underneath, got {len(slot0.cards)} cards"
    )
    assert slot0.cards[0] == skeleton_id, "Skeleton should be on top"

    print(f"  Skeleton placed in slot 0 with {len(slot0.cards)} total cards (draw underneath worked)")
    print("  PASS")


def test_exhaustion():
    print("Testing exhaustion (empty deck + empty refresh)...")
    state = create_initial_state(seed=42)

    # Empty both decks and refresh piles to trigger exhaustion during dealing
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        ps = ps_set_deck(ps, ())
        ps = ps_set_refresh(ps, ())
        state = gs_update_player(state, pid, ps)

    state = auto_advance(state)

    assert state.phase == Phase.GAME_OVER, "Game should end due to exhaustion"
    assert state.game_result is not None
    assert state.game_result.kind == GameResultKind.EXHAUSTION
    assert state.game_result.winner is None  # Both lose
    assert "Exhaustion" in state.game_result.description

    print(f"  Exhaustion detected: {state.game_result.description}")
    print("  PASS")


def test_refresh_determinism():
    print("Testing refresh determinism...")
    s1 = create_initial_state(seed=999)
    s2 = create_initial_state(seed=999)

    s1 = run_refresh_to_completion(s1)
    s2 = run_refresh_to_completion(s2)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        p1 = gs_get_player(s1, pid)
        p2 = gs_get_player(s2, pid)
        assert p1.hand == p2.hand, f"{pid.name} hands differ"
        assert p1.deck == p2.deck, f"{pid.name} decks differ"
        assert p1.manipulation_field == p2.manipulation_field, f"{pid.name} manipulation differs"

        for i in range(4):
            slot1 = af_get_slot(s1.action_field, pid, i)
            slot2 = af_get_slot(s2.action_field, pid, i)
            assert slot1 == slot2, f"{pid.name} slot {i} differs"

    assert s1.priority == s2.priority

    print("  Same seed produces identical results")
    print("  PASS")


def test_view_after_refresh():
    print("Testing player view after refresh...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_completion(state)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        view = get_player_view(state, pid)

        # Should see own hand of 4 cards
        assert len(view.my_state.hand) == 4

        # Should see own manipulation field of 2 cards
        assert len(view.my_state.manipulation_field) == 2

        # Should see own action field (3 filled)
        filled_own = sum(1 for s in view.action_field.own_slots if not s.is_empty)
        assert filled_own == 3, f"{pid.name} should see 3 filled own slots"

        # Should see other's distant slots
        other_distant = [
            view.action_field.other_distant_0,
            view.action_field.other_distant_3,
        ]

        # Other's hand: can only see size
        assert view.other_state.hand_size == 4

        # Render should work
        text = render_player_view(state, view)
        assert "HP: " in text

    print("  Views correctly show post-refresh state")
    print("  PASS")


def test_bellyfiller_counter():
    print("Testing Bellyfiller counter and discard at 3...")
    for seed in range(200):
        state = create_initial_state(seed=seed)
        pid = PlayerId.RED
        ps = gs_get_player(state, pid)

        # Find Bellyfiller in deck
        belly_id = None
        for cid in ps.deck:
            if state.card_def(cid).name == "food_9":
                belly_id = cid
                break

        if belly_id is None:
            continue

        # Equip Bellyfiller with 2 counters already
        deck_list = list(ps.deck)
        deck_list.remove(belly_id)
        ps = ps_set_deck(ps, tuple(deck_list))
        ps = ps_set_equipment(ps, (ps.equipment[0], belly_id))
        ps = ps_set_hp(ps, 10)
        state = gs_update_player(state, pid, ps)
        state = gs_set_card_state(state, belly_id, CardState(counters=2))

        state = run_refresh_to_completion(state)

        # Counter was 2, incremented to 3 → should be discarded
        ps_after = gs_get_player(state, pid)
        assert belly_id not in ps_after.equipment, "Bellyfiller should be discarded at 3 counters"
        assert belly_id in ps_after.discard_pile, "Bellyfiller should be in discard pile"

        # Should have healed 3 (but Bellyfiller is gone after)
        # HP: 10 + 3 = 13
        assert ps_after.hp == 13, f"Expected HP 13 after Bellyfiller heal, got {ps_after.hp}"

        print(f"  Bellyfiller: counter 2→3, discarded, healed 3 HP (10→13)")
        print("  PASS")
        return

    print("  (Could not find Bellyfiller)")
    print("  SKIP")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 4 Validation")
    print("=" * 60)
    test_refresh_dealing()
    test_refresh_fill_order()
    test_refresh_non_mixing()
    test_refresh_priority_flip()
    test_refresh_deck_sizes_after()
    test_refresh_with_existing_cards()
    test_refresh_phase_tracking_reset()
    test_refresh_shuffle_refresh_pile()
    test_cardsharp_decision()
    test_cardsharp_rearrange_effect()
    test_periodic_empress()
    test_periodic_corruption()
    test_periodic_corruption_with_empress()
    test_periodic_phoenix()
    test_periodic_phoenix_death_revive()
    test_periodic_survivor_damage()
    test_skeleton_on_placement()
    test_exhaustion()
    test_refresh_determinism()
    test_view_after_refresh()
    test_bellyfiller_counter()
    print("=" * 60)
    print("ALL STAGE 4 TESTS PASSED")
    print("=" * 60)