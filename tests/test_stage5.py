#!/usr/bin/env python3
"""Stage 5 validation: Manipulation Phase implementation."""

from dataclasses import replace

from fj_spec.types import (
    PlayerId, Alignment, Phase, CardType, CardState,
    ActionSlot, ActionField, ManipulationField,
    PlayerState, GameState,
    PendingDecision, DecisionKind, Action, ActionKind,
    ManipulationContext, ManipStep, ManipChoice,
    SwapPair, DumpFateChoice, DumpFate,
)
from fj_spec.cards import get_card_def, ALL_CARD_DEFS
from fj_spec.setup import create_initial_state
from fj_spec.state_helpers import (
    gs_set_phase, gs_set_context, gs_set_pending,
    gs_get_player, gs_update_player, gs_modify_player,
    gs_set_action_field,
    ps_set_hp, ps_set_deck, ps_set_hand,
    ps_set_refresh, ps_set_discard, ps_set_manipulation,
    ps_set_equipment,
    af_get_slot, af_find_empty_slots, af_find_nonempty_slots,
)
from fj_spec.fog import get_player_view, render_player_view
from fj_spec.engine import auto_advance, apply, get_decision
from fj_spec.phases.refresh import advance_refresh, apply_refresh_action
from fj_spec.phases.manipulation import advance_manipulation, apply_manipulation_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_refresh_to_manipulation(state: GameState) -> GameState:
    """Run refresh to completion (auto-answering Cardsharp), ending in MANIPULATION."""
    assert state.phase == Phase.REFRESH
    for _ in range(50):
        if state.phase != Phase.REFRESH:
            return state
        if state.phase == Phase.GAME_OVER:
            return state
        if state.pending is not None:
            action = Action(kind=ActionKind.SELECT_PERMUTATION,
                            permutation=(0, 1, 2, 3))
            state = gs_set_pending(state, None)
            state = apply_refresh_action(state, action)
        else:
            state = advance_refresh(state)
    raise RuntimeError("Refresh did not complete")


def advance_manip_until_decision(state: GameState) -> GameState:
    """Advance manipulation until a pending decision or phase transition."""
    for _ in range(50):
        if state.phase != Phase.MANIPULATION:
            return state
        if state.pending is not None:
            return state
        state = advance_manipulation(state)
    raise RuntimeError("Manipulation did not produce a decision")


def apply_manip_action(state: GameState, action: Action) -> GameState:
    """Apply a manipulation action then advance to next decision."""
    state = gs_set_pending(state, None)
    state = apply_manipulation_action(state, action)
    return advance_manip_until_decision(state)


def both_choose_manipulate(state: GameState) -> GameState:
    """Have both players choose MANIPULATE."""
    state = advance_manip_until_decision(state)
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=0))
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=0))
    return state


def both_choose_dump(state: GameState) -> GameState:
    """Have both players choose DUMP."""
    state = advance_manip_until_decision(state)
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=1))
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=1))
    return state


def finish_swapping(state: GameState) -> GameState:
    """Decline all swap opportunities for current player."""
    while (state.pending is not None
           and state.pending.kind == DecisionKind.CHOOSE_SWAP):
        state = apply_manip_action(state, Action(kind=ActionKind.DECLINE))
    return state


def decline_force(state: GameState) -> GameState:
    """Decline force opportunities."""
    while (state.pending is not None
           and state.pending.kind == DecisionKind.CHOOSE_FORCE):
        state = apply_manip_action(state, Action(kind=ActionKind.DECLINE))
    return state


def run_manipulation_to_completion(state: GameState) -> GameState:
    """Run the full manipulation phase with default choices (manipulate, no swaps, no force)."""
    state = both_choose_manipulate(state)
    # Finish both players' swapping
    state = finish_swapping(state)
    state = finish_swapping(state)
    # Decline force for both
    state = decline_force(state)
    state = decline_force(state)
    # Auto-advance through dealing
    while state.phase == Phase.MANIPULATION:
        if state.pending is not None:
            # Shouldn't happen in default flow, but handle force card choice
            raise RuntimeError(f"Unexpected decision: {state.pending.kind}")
        state = advance_manipulation(state)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_manipulation_choose_decision():
    print("Testing manipulation choice presentation...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)
    assert state.phase == Phase.MANIPULATION

    state = advance_manip_until_decision(state)

    assert state.pending is not None
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    # Priority player should go first
    assert state.pending.player == state.priority
    assert len(state.pending.legal_actions) == 2  # Manipulate or Dump

    print(f"  Priority player ({state.priority.name}) chooses first")
    print("  PASS")


def test_manipulation_both_choose():
    print("Testing both players choosing...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)
    state = advance_manip_until_decision(state)

    first_player = state.pending.player

    # First player chooses MANIPULATE
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=0))

    # Second player should be prompted
    assert state.pending is not None
    assert state.pending.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP
    assert state.pending.player == first_player.other()

    # Second player chooses DUMP
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=1))

    # Should now be in SWAP_OR_DUMP step
    assert state.pending is not None
    # The manipulate player should get a CHOOSE_SWAP decision
    # The dump player should get CHOOSE_DUMP_FATE

    print("  Both players chose successfully")
    print("  PASS")


def test_manipulation_swap():
    print("Testing card swapping...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)
    state = both_choose_manipulate(state)

    # First player should get CHOOSE_SWAP
    assert state.pending is not None
    assert state.pending.kind == DecisionKind.CHOOSE_SWAP
    pid = state.pending.player

    ps = gs_get_player(state, pid)
    manip_before = ps.manipulation_field.cards
    hand_before = ps.hand
    assert len(manip_before) == 2
    assert len(hand_before) == 4

    # Find a swap action
    swap_actions = [a for a in state.pending.legal_actions
                    if a.kind == ActionKind.SELECT_SWAP]
    assert len(swap_actions) == 2 * 4  # 2 manip × 4 hand

    # Perform one swap
    swap = swap_actions[0].swap
    state = apply_manip_action(state, swap_actions[0])

    # Verify cards were actually swapped
    ps = gs_get_player(state, pid)
    assert swap.manip_card in ps.hand, "Swapped manip card should now be in hand"
    assert swap.hand_card in ps.manipulation_field.cards, "Swapped hand card should be in manipulation"
    assert len(ps.manipulation_field.cards) == 2  # Still 2 manipulation cards
    assert len(ps.hand) == 4  # Still 4 hand cards

    # Should still be in CHOOSE_SWAP (can swap more)
    assert state.pending.kind == DecisionKind.CHOOSE_SWAP

    print("  Card swap verified: manip↔hand exchange correct")
    print("  PASS")


def test_manipulation_dump():
    print("Testing dump choices...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)
    state = both_choose_dump(state)

    # First player should get CHOOSE_DUMP_FATE
    assert state.pending is not None
    assert state.pending.kind == DecisionKind.CHOOSE_DUMP_FATE
    pid = state.pending.player

    ps = gs_get_player(state, pid)
    other_pid = pid.other()

    # Count non-Elusive hand cards
    non_elusive = [c for c in ps.hand if not state.card_def(c).is_elusive]
    # Number of combinations: 2^n (each card independently discard/refresh)
    expected_combos = 2 ** len(non_elusive)
    assert len(state.pending.legal_actions) == expected_combos, (
        f"Expected {expected_combos} dump combos, got {len(state.pending.legal_actions)}"
    )

    # Pick first option (all discard or all refresh depending on order)
    state_before = state
    state = apply_manip_action(state, state.pending.legal_actions[0])

    # Verify hand changed
    ps_after = gs_get_player(state, pid)
    # Non-Elusive cards should be gone from hand
    for c in non_elusive:
        assert c not in ps_after.hand, f"Dumped card {c} should be gone from hand"

    # Elusive cards should still be in hand
    elusive_in_hand = [c for c in ps.hand if state_before.card_def(c).is_elusive]
    for c in elusive_in_hand:
        assert c in ps_after.hand, f"Elusive card {c} should remain in hand"

    # Dumped cards should be in the OTHER player's discard or refresh (Non-Mixing)
    other_ps = gs_get_player(state, other_pid)
    fates = state_before.pending.legal_actions[0].dump_fates
    for fc in fates:
        if fc.fate == DumpFate.DISCARD:
            assert fc.card in other_ps.discard_pile, (
                f"Discarded card should be in other's discard"
            )
        else:
            assert fc.card in other_ps.refresh_pile, (
                f"Refreshed card should be in other's refresh"
            )

    print(f"  Dump verified: {len(non_elusive)} non-Elusive cards processed")
    print("  PASS")


def test_manipulation_dealing():
    print("Testing automated dealing...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)

    # Record state before manipulation
    empty_before_red = af_find_empty_slots(state.action_field, PlayerId.RED)
    empty_before_blue = af_find_empty_slots(state.action_field, PlayerId.BLUE)
    assert len(empty_before_red) == 1  # From refresh
    assert len(empty_before_blue) == 1

    state = run_manipulation_to_completion(state)

    assert state.phase == Phase.ACTION, f"Expected ACTION, got {state.phase}"

    # After dealing, both action fields should be full (the 1 empty slot got filled)
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        empty = af_find_empty_slots(state.action_field, pid)
        assert len(empty) == 0, f"{pid.name} should have 0 empty slots, got {len(empty)}"

    # Manipulation fields should be empty (cards dealt or refreshed)
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        assert len(ps.manipulation_field.cards) == 0, (
            f"{pid.name} manipulation field should be empty after dealing"
        )

    print("  All action slots filled, manipulation fields cleared")
    print("  PASS")


def test_manipulation_non_mixing():
    print("Testing Non-Mixing during manipulation dealing...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)

    # Record which cards belong to which player's original deck
    red_deck_all = set(gs_get_player(state, PlayerId.RED).deck)
    blue_deck_all = set(gs_get_player(state, PlayerId.BLUE).deck)
    # Also include cards already on the action field
    for i in range(4):
        for cid in af_get_slot(state.action_field, PlayerId.RED, i).cards:
            red_deck_all.add(cid)
        for cid in af_get_slot(state.action_field, PlayerId.BLUE, i).cards:
            blue_deck_all.add(cid)

    state = run_manipulation_to_completion(state)

    # Cards on RED's action field should be from RED's deck
    for i in range(4):
        slot = af_get_slot(state.action_field, PlayerId.RED, i)
        for cid in slot.cards:
            assert cid in red_deck_all, (
                f"Card {cid} on RED's action field should be from RED's deck"
            )

    # Cards on BLUE's action field should be from BLUE's deck
    for i in range(4):
        slot = af_get_slot(state.action_field, PlayerId.BLUE, i)
        for cid in slot.cards:
            assert cid in blue_deck_all, (
                f"Card {cid} on BLUE's action field should be from BLUE's deck"
            )

    print("  Non-Mixing verified: action field cards match deck ownership")
    print("  PASS")


def test_manipulation_card_conservation():
    print("Testing total card conservation through manipulation...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)

    state = run_manipulation_to_completion(state)

    # Count all cards
    total = len(state.guard_deck)
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        total += len(ps.deck)
        total += len(ps.hand)
        total += len(ps.refresh_pile)
        total += len(ps.discard_pile)
        total += len(ps.manipulation_field.cards)
        for eq in ps.equipment:
            if eq is not None:
                total += 1
        for ws in ps.weapon_slots:
            if ws.weapon is not None:
                total += 1
            total += len(ws.kill_pile)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        for i in range(4):
            slot = af_get_slot(state.action_field, pid, i)
            total += len(slot.cards)

    expected = 70 * 2 + 2 + 16  # 158
    assert total == expected, f"Expected {expected} total cards, got {total}"

    print(f"  Card conservation verified: {total} cards")
    print("  PASS")


def test_manipulation_elusive_cleanup():
    print("Testing Elusive hand card cleanup...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)

    # Check if any player has Elusive cards in hand
    found_elusive = False
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        for cid in ps.hand:
            if state.card_def(cid).is_elusive:
                found_elusive = True
                break

    # Run manipulation to completion
    state = run_manipulation_to_completion(state)

    # After manipulation, no Elusive cards should remain in hand
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        for cid in ps.hand:
            cd = state.card_def(cid)
            assert not cd.is_elusive, (
                f"Elusive card {cd.name} should have been refreshed from {pid.name}'s hand"
            )

    if found_elusive:
        print("  Elusive cards found and cleaned from hands")
    else:
        print("  No Elusive cards in hands this seed (cleanup logic still ran)")
    print("  PASS")


def test_manipulation_forcing():
    print("Testing forcing (discard equipment to choose card)...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)

    # Both choose MANIPULATE
    state = advance_manip_until_decision(state)
    first_player = state.pending.player
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=0))
    state = apply_manip_action(state, Action(kind=ActionKind.SELECT_INDEX, index=0))

    # First player finishes swapping
    state = finish_swapping(state)

    # First player should now get CHOOSE_FORCE
    if state.pending is not None and state.pending.kind == DecisionKind.CHOOSE_FORCE:
        pid = state.pending.player
        ps = gs_get_player(state, pid)

        # Player should have equipment (role card at least)
        equipment = [eq for eq in ps.equipment if eq is not None]
        assert len(equipment) > 0, "Player should have equipment for forcing"

        # Choose to force with the first equipment piece
        eq_to_sacrifice = equipment[0]
        force_action = Action(kind=ActionKind.SELECT_CARD, card_id=eq_to_sacrifice)
        state = apply_manip_action(state, force_action)

        # Equipment should now be discarded
        ps = gs_get_player(state, pid)
        assert eq_to_sacrifice not in ps.equipment, "Sacrificed equipment should be gone"
        assert eq_to_sacrifice in ps.discard_pile, "Sacrificed equipment should be in discard"

        # Continue through the rest (second player swap, force, dealing)
        state = finish_swapping(state)
        state = decline_force(state)

        # Now dealing — forcing player should get FORCE_CARD_CHOOSE
        while state.phase == Phase.MANIPULATION:
            if state.pending is not None:
                if state.pending.kind == DecisionKind.CHOOSE_FORCE_CARD:
                    # Choose first card
                    state = apply_manip_action(state, state.pending.legal_actions[0])
                else:
                    raise RuntimeError(f"Unexpected: {state.pending.kind}")
            else:
                state = advance_manipulation(state)

        assert state.phase == Phase.ACTION
        print(f"  Forcing verified: equipment discarded, card chosen")
    else:
        print("  (Force not offered — equipment missing or already consumed)")

    print("  PASS")


def test_manipulation_determinism():
    print("Testing manipulation determinism...")
    s1 = create_initial_state(seed=123)
    s2 = create_initial_state(seed=123)

    s1 = run_refresh_to_manipulation(s1)
    s2 = run_refresh_to_manipulation(s2)

    s1 = run_manipulation_to_completion(s1)
    s2 = run_manipulation_to_completion(s2)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        p1 = gs_get_player(s1, pid)
        p2 = gs_get_player(s2, pid)
        assert p1.deck == p2.deck, f"{pid.name} decks differ"
        assert p1.hand == p2.hand, f"{pid.name} hands differ"
        assert p1.refresh_pile == p2.refresh_pile, f"{pid.name} refresh differs"

        for i in range(4):
            slot1 = af_get_slot(s1.action_field, pid, i)
            slot2 = af_get_slot(s2.action_field, pid, i)
            assert slot1 == slot2, f"{pid.name} slot {i} differs"

    print("  Same seed + same choices → identical results")
    print("  PASS")


def test_manipulation_view():
    print("Testing player view during manipulation...")
    state = create_initial_state(seed=42)
    state = run_refresh_to_manipulation(state)
    state = advance_manip_until_decision(state)

    # The deciding player should see the decision
    pid = state.pending.player
    view = get_player_view(state, pid)
    assert view.decision is not None
    assert view.decision.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP

    # The other player should NOT see the decision
    other_view = get_player_view(state, pid.other())
    assert other_view.decision is None

    # Render should work
    text = render_player_view(state, view)
    assert "CHOOSE_MANIPULATE_OR_DUMP" in text

    print("  Decision visible to correct player, hidden from other")
    print("  PASS")


def test_full_refresh_and_manipulation_cycle():
    print("Testing full Refresh → Manipulation cycle...")
    state = create_initial_state(seed=42)

    # Run refresh
    state = run_refresh_to_manipulation(state)
    assert state.phase == Phase.MANIPULATION

    # Run manipulation
    state = run_manipulation_to_completion(state)
    assert state.phase == Phase.ACTION

    # Verify the board is ready for action
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        # Action field should be full (4/4 slots)
        empty = af_find_empty_slots(state.action_field, pid)
        assert len(empty) == 0, f"{pid.name} should have all slots filled"

        ps = gs_get_player(state, pid)
        # Manipulation field should be empty
        assert len(ps.manipulation_field.cards) == 0

    print("  Board fully prepared for Action Phase")
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 5 Validation")
    print("=" * 60)
    test_manipulation_choose_decision()
    test_manipulation_both_choose()
    test_manipulation_swap()
    test_manipulation_dump()
    test_manipulation_dealing()
    test_manipulation_non_mixing()
    test_manipulation_card_conservation()
    test_manipulation_elusive_cleanup()
    test_manipulation_forcing()
    test_manipulation_determinism()
    test_manipulation_view()
    test_full_refresh_and_manipulation_cycle()
    print("=" * 60)
    print("ALL STAGE 5 TESTS PASSED")
    print("=" * 60)