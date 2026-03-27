#!/usr/bin/env python3
"""Stage 3 validation: engine skeleton, state helpers, fog of war."""

from dataclasses import replace

from fj_spec.types import (
    PlayerId, Alignment, Phase, CardType, CardState,
    ActionSlot, ActionField, WeaponSlot, ManipulationField,
    PlayerState, GameState,
    PendingDecision, DecisionKind, Action, ActionKind,
    GameResult, GameResultKind,
    RefreshContext, ManipulationContext, ActionContext,
    SlotKind, classify_slot,
)
from fj_spec.cards import get_card_def, ALL_CARD_DEFS
from fj_spec.setup import create_initial_state
from fj_spec.state_helpers import (
    gs_set_phase, gs_set_pending, gs_set_result, gs_flip_priority,
    gs_get_player, gs_update_player, gs_modify_player,
    gs_set_action_field, gs_set_card_state, gs_set_guard_deck,
    gs_push_continuation, gs_pop_continuation,
    gs_get_rng, gs_with_rng_result, gs_increment_turn,
    ps_set_hp, ps_set_dead, ps_set_deck, ps_set_hand,
    ps_set_refresh, ps_set_discard, ps_set_manipulation,
    ps_set_equipment, ps_set_weapon_slots, ps_set_eaten,
    ps_set_action_plays, ps_set_action_phase_over,
    ps_draw_from_deck, ps_add_to_hand, ps_remove_from_hand,
    ps_add_to_refresh, ps_add_to_discard,
    ps_add_permanent_ability,
    af_get_slot, af_set_slot, af_add_card_to_slot,
    af_clear_slot, af_find_empty_slots, af_find_nonempty_slots,
)
from fj_spec.fog import get_player_view, render_player_view, describe_card
from fj_spec.engine import (
    get_decision, apply, auto_advance, start_game,
    IllegalActionError, GameOverError,
)
from fj_spec.rng import rng_create, rng_shuffle


# ---------------------------------------------------------------------------
# State helper tests
# ---------------------------------------------------------------------------

def test_state_helpers_basic():
    print("Testing state helpers (basic)...")
    state = create_initial_state(seed=42)

    # Flip priority
    orig_priority = state.priority
    flipped = gs_flip_priority(state)
    assert flipped.priority == orig_priority.other()
    assert flipped.priority != orig_priority

    # Increment turn
    t2 = gs_increment_turn(state)
    assert t2.turn_number == 2
    assert state.turn_number == 1  # original unchanged

    # Set phase
    manip = gs_set_phase(state, Phase.MANIPULATION, ManipulationContext())
    assert manip.phase == Phase.MANIPULATION
    assert isinstance(manip.phase_context, ManipulationContext)

    print("  PASS")


def test_player_state_helpers():
    print("Testing player state helpers...")
    state = create_initial_state(seed=42)
    pid = PlayerId.RED

    # Modify HP
    state2 = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 15))
    assert gs_get_player(state2, pid).hp == 15
    assert gs_get_player(state, pid).hp == 20  # original unchanged

    # HP capping
    state3 = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, 25))
    assert gs_get_player(state3, pid).hp == 20  # capped at hp_cap

    # HP floor
    state4 = gs_modify_player(state, pid, lambda ps: ps_set_hp(ps, -5))
    assert gs_get_player(state4, pid).hp == 0

    # Draw from deck
    ps = gs_get_player(state, pid)
    assert len(ps.deck) == 70
    ps2, drawn = ps_draw_from_deck(ps, 3)
    assert len(drawn) == 3
    assert len(ps2.deck) == 67
    # Drawn cards should be the first 3 from the deck
    assert drawn == ps.deck[:3]

    # Add to hand
    ps3 = ps_add_to_hand(ps2, drawn)
    assert len(ps3.hand) == 3
    assert ps3.hand == drawn

    # Remove from hand
    ps4 = ps_remove_from_hand(ps3, drawn[1])
    assert len(ps4.hand) == 2
    assert drawn[1] not in ps4.hand

    # Add to refresh / discard
    ps5 = ps_add_to_refresh(ps, (100, 101))
    assert ps5.refresh_pile == (100, 101)
    ps6 = ps_add_to_discard(ps, (200,))
    assert ps6.discard_pile == (200,)

    # Set eaten flag
    ps7 = ps_set_eaten(ps, True)
    assert ps7.has_eaten_this_phase

    # Permanent abilities
    ps8 = ps_add_permanent_ability(ps, "temperance_give_hp")
    assert "temperance_give_hp" in ps8.permanent_abilities
    ps9 = ps_add_permanent_ability(ps8, "devil_gamble")
    assert len(ps9.permanent_abilities) == 2

    print("  PASS")


def test_action_field_helpers():
    print("Testing action field helpers...")
    state = create_initial_state(seed=42)
    af = state.action_field

    # All slots should be empty
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        empties = af_find_empty_slots(af, pid)
        assert empties == [0, 1, 2, 3]
        nonempty = af_find_nonempty_slots(af, pid)
        assert nonempty == []

    # Add card to slot
    af2 = af_add_card_to_slot(af, PlayerId.RED, 0, card_id=999, position="top")
    slot = af_get_slot(af2, PlayerId.RED, 0)
    assert slot.cards == (999,)
    assert not slot.is_empty

    # Add another card underneath
    af3 = af_add_card_to_slot(af2, PlayerId.RED, 0, card_id=998, position="bottom")
    slot2 = af_get_slot(af3, PlayerId.RED, 0)
    assert slot2.cards == (999, 998)

    # Empty slots should now show 3 for RED
    empties2 = af_find_empty_slots(af3, PlayerId.RED)
    assert empties2 == [1, 2, 3]

    # Clear slot
    af4, cleared = af_clear_slot(af3, PlayerId.RED, 0)
    assert cleared == (999, 998)
    assert af_get_slot(af4, PlayerId.RED, 0).is_empty

    # Blue's slots should be unaffected throughout
    assert af_find_empty_slots(af3, PlayerId.BLUE) == [0, 1, 2, 3]

    print("  PASS")


def test_card_state_helpers():
    print("Testing card state helpers...")
    state = create_initial_state(seed=42)

    # Default card state
    cs = state.card_state(1)
    assert cs.counters == 0

    # Set card state
    state2 = gs_set_card_state(state, 1, CardState(counters=3))
    assert state2.card_state(1).counters == 3
    assert 1 in state2.card_states

    # Reset to default removes entry
    state3 = gs_set_card_state(state2, 1, CardState())
    assert state3.card_state(1).counters == 0
    assert 1 not in state3.card_states

    print("  PASS")


def test_continuation_stack():
    print("Testing continuation stack...")
    from fj_spec.types import Continuation

    state = create_initial_state(seed=42)
    assert state.continuation_stack == ()

    # Push
    cont1 = Continuation(kind="foo", data={"x": 1})
    state2 = gs_push_continuation(state, cont1)
    assert len(state2.continuation_stack) == 1

    cont2 = Continuation(kind="bar", data={"y": 2})
    state3 = gs_push_continuation(state2, cont2)
    assert len(state3.continuation_stack) == 2

    # Pop (LIFO)
    state4, popped = gs_pop_continuation(state3)
    assert popped is not None
    assert popped.kind == "bar"
    assert len(state4.continuation_stack) == 1

    state5, popped2 = gs_pop_continuation(state4)
    assert popped2 is not None
    assert popped2.kind == "foo"
    assert len(state5.continuation_stack) == 0

    # Pop empty
    state6, popped3 = gs_pop_continuation(state5)
    assert popped3 is None

    print("  PASS")


# ---------------------------------------------------------------------------
# Fog of war tests
# ---------------------------------------------------------------------------

def test_fog_basic():
    print("Testing fog of war (basic view construction)...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        view = get_player_view(state, pid)
        other_pid = pid.other()

        # Can see own state
        assert view.me == pid
        assert view.my_state.hp == 20
        assert view.my_state.alignment is not None  # Can see own alignment
        assert view.my_state.role_def_name is not None
        assert view.my_state.deck_size == 70

        # Cannot see other's hand (only size)
        assert view.other_state.hand_size == 0  # Empty at start
        assert view.other_state.hp == 20

        # Action field: all empty at start
        for slot in view.action_field.own_slots:
            assert slot.is_empty
        assert view.action_field.other_distant_0.is_empty
        assert view.action_field.other_distant_3.is_empty
        assert view.action_field.other_hidden_1_count == 0
        assert view.action_field.other_hidden_2_count == 0

        # Guard deck visible size
        assert view.guard_deck_size == 16

        # No decision pending at init
        assert view.decision is None

    print("  PASS")


def test_fog_hides_opponent_info():
    print("Testing fog of war (information hiding)...")
    state = create_initial_state(seed=42)

    # Put some cards on the action field to test visibility
    ps_red = gs_get_player(state, PlayerId.RED)
    # Use first 4 cards from red deck as test cards
    test_cards = ps_red.deck[:4]
    af = state.action_field

    # Place cards in RED's slots
    for i, cid in enumerate(test_cards):
        af = af_add_card_to_slot(af, PlayerId.RED, i, cid)

    state = gs_set_action_field(state, af)

    # From RED's perspective: can see all own slots
    red_view = get_player_view(state, PlayerId.RED)
    for i in range(4):
        assert not red_view.action_field.own_slots[i].is_empty

    # From BLUE's perspective: can see RED's distant (0, 3) but not hidden (1, 2)
    blue_view = get_player_view(state, PlayerId.BLUE)
    assert not blue_view.action_field.other_distant_0.is_empty  # RED slot 0
    assert not blue_view.action_field.other_distant_3.is_empty  # RED slot 3
    assert blue_view.action_field.other_hidden_1_count == 1  # RED slot 1: count only
    assert blue_view.action_field.other_hidden_2_count == 1  # RED slot 2: count only

    # BLUE can see the actual cards in RED's distant slots
    assert blue_view.action_field.other_distant_0.cards == (test_cards[0],)
    assert blue_view.action_field.other_distant_3.cards == (test_cards[3],)

    print("  PASS")


def test_fog_decision_filtering():
    print("Testing fog of war (decision filtering)...")
    state = create_initial_state(seed=42)

    # Create a dummy decision for RED
    decision = PendingDecision(
        player=PlayerId.RED,
        kind=DecisionKind.CHOOSE_MANIPULATE_OR_DUMP,
        legal_actions=(
            Action(kind=ActionKind.SELECT_INDEX, index=0),
            Action(kind=ActionKind.SELECT_INDEX, index=1),
        ),
        context_description="Choose: Manipulate or Dump",
    )
    state = gs_set_pending(state, decision)

    # RED should see the decision
    red_view = get_player_view(state, PlayerId.RED)
    assert red_view.decision is not None
    assert red_view.decision.kind == DecisionKind.CHOOSE_MANIPULATE_OR_DUMP

    # BLUE should NOT see RED's decision
    blue_view = get_player_view(state, PlayerId.BLUE)
    assert blue_view.decision is None

    print("  PASS")


def test_render_player_view():
    print("Testing player view rendering...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        view = get_player_view(state, pid)
        rendered = render_player_view(state, view)
        assert isinstance(rendered, str)
        assert len(rendered) > 100
        assert pid.name in rendered
        assert "HP: 20/20" in rendered
        assert "Turn 1" in rendered

    print("  Rendered views are non-empty and contain expected info")
    print("  PASS")


def test_describe_card():
    print("Testing card description...")
    state = create_initial_state(seed=42)

    # Find a known card in RED's deck
    ps = gs_get_player(state, PlayerId.RED)
    for cid in ps.deck:
        cd = state.card_def(cid)
        desc = describe_card(state, cid)
        assert isinstance(desc, str)
        assert len(desc) > 0
        # Elusive cards should show [Elusive]
        if cd.is_elusive:
            assert "[Elusive]" in desc
        if cd.is_first:
            assert "[First]" in desc

    print("  PASS")


# ---------------------------------------------------------------------------
# Engine skeleton tests
# ---------------------------------------------------------------------------

def test_engine_auto_advance_stubs():
    print("Testing engine auto_advance with phase transitions...")
    state = create_initial_state(seed=42)
    assert state.phase == Phase.REFRESH

    # With full refresh implementation, auto_advance processes all refresh steps,
    # then manipulation stub → action stub → refresh again (cycle).
    # Refresh should complete and transition to MANIPULATION.
    # Let's manually step through refresh to verify it works.

    # Step through refresh one step at a time
    from fj_spec.phases.refresh import advance_refresh
    s = state
    steps_seen = []
    for _ in range(20):  # safety limit
        ctx = s.phase_context
        if not isinstance(ctx, RefreshContext):
            break
        steps_seen.append(ctx.step.name)
        s = advance_refresh(s)
        if s.phase != Phase.REFRESH:
            break

    assert s.phase == Phase.MANIPULATION, f"Expected MANIPULATION, got {s.phase}"
    assert "MOON_RECORD" in steps_seen
    assert "DEAL_ALL" in steps_seen
    assert "PERIODIC_EFFECTS" in steps_seen

    # Verify dealing happened
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(s, pid)
        assert len(ps.hand) == 4, f"{pid.name} should have 4 cards in hand, got {len(ps.hand)}"
        assert len(ps.manipulation_field.cards) == 2, (
            f"{pid.name} should have 2 manipulation cards"
        )

    # Verify action field has cards (3 slots filled, 1 empty)
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        af = s.action_field
        empty = af_find_empty_slots(af, pid)
        assert len(empty) == 1, f"{pid.name} should have 1 empty slot, got {len(empty)}"

    # Verify manipulation stub → action, action stub → refresh
    from fj_spec.phases.manipulation import advance_manipulation
    s2 = advance_manipulation(s)
    assert s2.phase == Phase.ACTION

    from fj_spec.phases.action import advance_action
    s3 = advance_action(s2)
    assert s3.phase == Phase.REFRESH
    assert s3.turn_number == 2

    print(f"  Refresh steps: {' → '.join(steps_seen)}")
    print("  Phase cycle: REFRESH → MANIPULATION → ACTION → REFRESH (turn 2)")
    print("  PASS")


def test_engine_game_over_detection():
    print("Testing engine game-over detection...")
    state = create_initial_state(seed=42)

    # Kill RED player
    state2 = gs_modify_player(state, PlayerId.RED, ps_set_dead)
    assert gs_get_player(state2, PlayerId.RED).is_dead

    # auto_advance should detect death and end game
    state3 = auto_advance(state2)
    assert state3.phase == Phase.GAME_OVER
    assert state3.game_result is not None

    # Check result makes sense
    result = state3.game_result
    assert result.winner == PlayerId.BLUE

    red_alignment = gs_get_player(state2, PlayerId.RED).alignment
    if red_alignment == Alignment.EVIL:
        assert result.kind == GameResultKind.GOOD_KILLS_EVIL
    else:
        assert result.kind == GameResultKind.EVIL_KILLS_GOOD

    print(f"  RED ({red_alignment.name}) died → BLUE wins ({result.kind.name})")
    print("  PASS")


def test_engine_game_over_errors():
    print("Testing engine error handling...")
    state = create_initial_state(seed=42)

    # Kill a player to end the game
    state = gs_modify_player(state, PlayerId.RED, ps_set_dead)
    state = auto_advance(state)
    assert state.phase == Phase.GAME_OVER

    # Applying action to ended game should raise
    dummy_action = Action(kind=ActionKind.DECLINE)
    try:
        apply(state, dummy_action)
        assert False, "Should have raised GameOverError"
    except GameOverError:
        pass

    # get_decision on ended game should return None
    assert get_decision(state) is None

    print("  GameOverError raised correctly, get_decision returns None")
    print("  PASS")


def test_engine_illegal_action():
    print("Testing illegal action validation...")
    state = create_initial_state(seed=42)

    # Set up a fake pending decision with specific legal actions
    legal_action = Action(kind=ActionKind.SELECT_INDEX, index=0)
    decision = PendingDecision(
        player=PlayerId.RED,
        kind=DecisionKind.CHOOSE_MANIPULATE_OR_DUMP,
        legal_actions=(legal_action,),
        context_description="Test decision",
    )
    state = gs_set_pending(state, decision)

    # Submitting a non-legal action should raise
    illegal_action = Action(kind=ActionKind.SELECT_INDEX, index=99)
    try:
        apply(state, illegal_action)
        assert False, "Should have raised IllegalActionError"
    except IllegalActionError:
        pass

    print("  IllegalActionError raised for invalid action")
    print("  PASS")


def test_engine_pending_decision_flow():
    print("Testing pending decision flow...")
    state = create_initial_state(seed=42)

    # Initially no pending decision
    assert state.pending is None
    assert get_decision(state) is None or state.phase == Phase.GAME_OVER

    # Manually set a decision
    legal_action = Action(kind=ActionKind.SELECT_INDEX, index=0)
    decision = PendingDecision(
        player=PlayerId.RED,
        kind=DecisionKind.CHOOSE_MANIPULATE_OR_DUMP,
        legal_actions=(legal_action,),
        context_description="Test",
    )
    state = gs_set_pending(state, decision)
    assert get_decision(state) is not None
    assert get_decision(state).player == PlayerId.RED

    print("  PASS")


def test_win_condition_both_alignments():
    print("Testing win conditions for both alignment combinations...")

    # Test all 4 combinations of (dead_player_alignment, alive_player_alignment)
    for seed in range(200):
        state = create_initial_state(seed=seed)
        red = gs_get_player(state, PlayerId.RED)
        blue = gs_get_player(state, PlayerId.BLUE)

        # Test RED dying
        state_red_dead = gs_modify_player(state, PlayerId.RED, ps_set_dead)
        state_result = auto_advance(state_red_dead)
        result = state_result.game_result
        assert result is not None
        assert result.winner == PlayerId.BLUE

        if red.alignment == Alignment.EVIL:
            assert result.kind == GameResultKind.GOOD_KILLS_EVIL
        else:
            assert result.kind == GameResultKind.EVIL_KILLS_GOOD

        # Test BLUE dying
        state_blue_dead = gs_modify_player(state, PlayerId.BLUE, ps_set_dead)
        state_result2 = auto_advance(state_blue_dead)
        result2 = state_result2.game_result
        assert result2 is not None
        assert result2.winner == PlayerId.RED

        if blue.alignment == Alignment.EVIL:
            assert result2.kind == GameResultKind.GOOD_KILLS_EVIL
        else:
            assert result2.kind == GameResultKind.EVIL_KILLS_GOOD

    print("  All alignment combinations produce correct results over 200 seeds")
    print("  PASS")


# ---------------------------------------------------------------------------
# RNG in state tests
# ---------------------------------------------------------------------------

def test_rng_in_state():
    print("Testing RNG state management...")
    state = create_initial_state(seed=42)

    rng = gs_get_rng(state)
    assert rng.seed == state.rng_seed
    assert rng.counter == state.rng_counter

    # Simulate consuming some randomness
    from fj_spec.rng import rng_d20
    rng2, roll = rng_d20(rng)
    state2 = gs_with_rng_result(state, rng2)

    assert state2.rng_counter > state.rng_counter
    assert state2.rng_seed == state.rng_seed

    # Verify determinism: same sequence from same state
    rng_a = gs_get_rng(state)
    rng_b = gs_get_rng(state)
    _, roll_a = rng_d20(rng_a)
    _, roll_b = rng_d20(rng_b)
    assert roll_a == roll_b

    print("  PASS")


# ---------------------------------------------------------------------------
# Integration: full view at game start
# ---------------------------------------------------------------------------

def test_full_view_at_start():
    print("Testing full player view at game start...")
    state = create_initial_state(seed=42)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        view = get_player_view(state, pid)

        # Verify structural completeness
        assert view.me == pid
        assert view.my_state is not None
        assert view.other_state is not None
        assert view.action_field is not None
        assert view.phase == Phase.REFRESH
        assert view.turn_number == 1

        # Verify own state has role info
        assert view.my_state.role_def_name is not None
        assert view.my_state.alignment in (Alignment.GOOD, Alignment.EVIL)

        # Verify other state doesn't leak alignment
        # (FoggedPlayerState has no alignment field)
        assert not hasattr(view.other_state, 'alignment')
        assert not hasattr(view.other_state, 'role_def_name')

        # Render should work without errors
        text = render_player_view(state, view)
        assert isinstance(text, str)

    print("  Both player views are complete and well-formed")
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 3 Validation")
    print("=" * 60)
    test_state_helpers_basic()
    test_player_state_helpers()
    test_action_field_helpers()
    test_card_state_helpers()
    test_continuation_stack()
    test_fog_basic()
    test_fog_hides_opponent_info()
    test_fog_decision_filtering()
    test_render_player_view()
    test_describe_card()
    test_engine_auto_advance_stubs()
    test_engine_game_over_detection()
    test_engine_game_over_errors()
    test_engine_illegal_action()
    test_engine_pending_decision_flow()
    test_win_condition_both_alignments()
    test_rng_in_state()
    test_full_view_at_start()
    print("=" * 60)
    print("ALL STAGE 3 TESTS PASSED")
    print("=" * 60)
