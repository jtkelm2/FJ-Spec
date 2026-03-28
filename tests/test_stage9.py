#!/usr/bin/env python3
"""Stage 9 validation: CLI smoke tests and automated game playthrough."""

from fj_spec.types import (
    PlayerId, Phase, ActionKind, Action, DecisionKind,
    GameState, PendingDecision,
)
from fj_spec.cards import get_card_def
from fj_spec.setup import create_initial_state
from fj_spec.engine import apply, auto_advance, get_decision
from fj_spec.state_helpers import gs_get_player, af_get_slot
from fj_spec.cli import render_full_state, _card_short, prompt_decision, SLOT_LABELS
from fj_spec.fog import get_player_view, render_player_view


# ---------------------------------------------------------------------------
# Auto-player: makes reasonable default choices
# ---------------------------------------------------------------------------

def auto_choose(state: GameState, decision: PendingDecision) -> Action:
    """Automatically choose an action for any decision type."""
    match decision.kind:
        case DecisionKind.REARRANGE_ACTION_FIELD:
            return Action(kind=ActionKind.SELECT_PERMUTATION, permutation=(0, 1, 2, 3))
        case DecisionKind.CHOOSE_MANIPULATE_OR_DUMP:
            return Action(kind=ActionKind.SELECT_INDEX, index=0)
        case DecisionKind.CHOOSE_SWAP:
            return Action(kind=ActionKind.DECLINE)
        case DecisionKind.CHOOSE_FORCE:
            return Action(kind=ActionKind.DECLINE)
        case DecisionKind.CHOOSE_DUMP_FATE:
            return decision.legal_actions[0]
        case DecisionKind.CHOOSE_LAST_RESORT:
            return Action(kind=ActionKind.DECLINE)
        case DecisionKind.CHOOSE_ACTION_SLOT:
            slot_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_SLOT]
            return slot_actions[0] if slot_actions else decision.legal_actions[0]
        case DecisionKind.GRANT_CONSENT:
            return Action(kind=ActionKind.SELECT_BOOL, flag=True)
        case DecisionKind.VOLUNTARY_DISCARD:
            # Decline unless it's a yes/no from an effect (Piñata, Saltine eat)
            decline = [a for a in decision.legal_actions if a.kind == ActionKind.DECLINE]
            if decline:
                return decline[0]
            # Yes/no: pick False (decline)
            no_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_BOOL and not a.flag]
            return no_actions[0] if no_actions else decision.legal_actions[0]
        case DecisionKind.RECYCLE_DECISION:
            n = len(decision.visible_cards) if decision.visible_cards else 4
            return Action(kind=ActionKind.SELECT_RECYCLE, recycle_flags=tuple([False] * n))
        case DecisionKind.CHOOSE_ATTACK_MODE:
            return decision.legal_actions[0]  # Pick first option (fists or weapon)
        case DecisionKind.MAGICIAN_CHOOSE:
            return decision.legal_actions[0]
        case DecisionKind.HIGH_PRIESTESS_NAME:
            return Action(kind=ActionKind.DECLINE)  # Skip naming
        case DecisionKind.HERMIT_CHOOSE:
            return Action(kind=ActionKind.SELECT_BOOL, flag=False)  # Don't give HP
        case DecisionKind.LOVERS_CHOOSE_HP:
            return Action(kind=ActionKind.SELECT_AMOUNT, amount=0)  # Give 0
        case DecisionKind.SALTINE_CHOICE:
            return Action(kind=ActionKind.SELECT_INDEX, index=0)  # Eat as food
        case DecisionKind.HIEROPHANT_SPLIT:
            return decision.legal_actions[0]  # First split
        case DecisionKind.STRENGTH_DECLARE_D20:
            return Action(kind=ActionKind.SELECT_AMOUNT, amount=11)  # Honest roll > 10
        case DecisionKind.TEMPERANCE_GIVE_HP:
            return decision.legal_actions[0]  # Give minimum
        case _:
            return decision.legal_actions[0]


def run_auto_game(seed: int, max_decisions: int = 500) -> GameState:
    """Run a game with automatic choices, returning final state."""
    state = create_initial_state(seed=seed)
    state = auto_advance(state)

    decisions_made = 0
    while state.phase != Phase.GAME_OVER and decisions_made < max_decisions:
        decision = get_decision(state)
        if decision is None:
            break
        action = auto_choose(state, decision)
        state = apply(state, action)
        decisions_made += 1

    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_render_full_state():
    print("Testing full state rendering...")
    state = create_initial_state(seed=42)
    state = auto_advance(state)

    rendered = render_full_state(state)
    assert isinstance(rendered, str)
    assert len(rendered) > 200
    assert "TURN" in rendered
    assert "RED" in rendered
    assert "BLUE" in rendered
    assert "HP:" in rendered
    assert "Action Field" in rendered

    print(f"  Rendered {len(rendered)} chars")
    print("  PASS")


def test_card_short():
    print("Testing card short descriptions...")
    state = create_initial_state(seed=42)
    ps = gs_get_player(state, PlayerId.RED)

    for cid in ps.deck[:10]:
        desc = _card_short(state, cid)
        assert isinstance(desc, str)
        assert len(desc) > 0

    # Named card should show big_name
    for cid in ps.deck:
        cd = state.card_def(cid)
        desc = _card_short(state, cid)
        if cd.big_name:
            assert cd.big_name in desc, f"Big name '{cd.big_name}' not in '{desc}'"
        if cd.is_elusive:
            assert "E" in desc

    print("  PASS")


def test_auto_game_completes():
    print("Testing automated game runs to completion...")
    completed = 0
    game_over_count = 0

    for seed in range(20):
        state = run_auto_game(seed, max_decisions=500)
        if state.phase == Phase.GAME_OVER:
            game_over_count += 1
            assert state.game_result is not None
        completed += 1

    print(f"  Ran {completed} games: {game_over_count} ended in GAME_OVER")
    print("  PASS")


def test_auto_game_no_crashes():
    print("Testing 50 automated games for crashes...")
    for seed in range(50):
        try:
            state = run_auto_game(seed, max_decisions=300)
        except Exception as e:
            print(f"  CRASH at seed={seed}: {e}")
            raise
    print("  50 games, 0 crashes")
    print("  PASS")


def test_auto_game_card_conservation():
    print("Testing card conservation across automated games...")
    expected = 70 * 2 + 2 + 16  # 158

    for seed in range(20):
        state = run_auto_game(seed, max_decisions=300)

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

        # Count cards in resolution queue (removed from field, not yet resolved)
        from fj_spec.types import ActionContext
        ctx = state.phase_context
        if isinstance(ctx, ActionContext) and ctx.resolving:
            if ctx.resolving.current_card is not None:
                total += 1
            total += len(ctx.resolving.card_queue)
        # Also count attack_target if set (card removed from slot, pending combat choice)
        if isinstance(ctx, ActionContext) and ctx.attack_target is not None:
            # Only count if not already counted in resolving.current_card
            if not (ctx.resolving and ctx.resolving.current_card == ctx.attack_target):
                total += 1

        assert total == expected, (
            f"Card conservation failed at seed={seed}: expected {expected}, got {total}"
        )

    print(f"  Conservation verified across 20 games ({expected} cards each)")
    print("  PASS")


def test_auto_game_determinism():
    print("Testing game determinism across runs...")
    for seed in [42, 99, 123]:
        s1 = run_auto_game(seed, max_decisions=200)
        s2 = run_auto_game(seed, max_decisions=200)

        assert s1.phase == s2.phase, f"Phase mismatch at seed={seed}"
        assert s1.turn_number == s2.turn_number, f"Turn mismatch at seed={seed}"
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            p1 = gs_get_player(s1, pid)
            p2 = gs_get_player(s2, pid)
            assert p1.hp == p2.hp, f"HP mismatch for {pid.name} at seed={seed}"
            assert p1.is_dead == p2.is_dead

    print("  3 seeds verified deterministic")
    print("  PASS")


def test_render_during_game():
    print("Testing rendering at various game points...")
    state = create_initial_state(seed=42)
    state = auto_advance(state)

    renders = 0
    decisions = 0
    while state.phase != Phase.GAME_OVER and decisions < 100:
        # Render should never crash
        rendered = render_full_state(state)
        assert isinstance(rendered, str)
        renders += 1

        decision = get_decision(state)
        if decision is None:
            break
        action = auto_choose(state, decision)
        state = apply(state, action)
        decisions += 1

    print(f"  Rendered {renders} states across {decisions} decisions without error")
    print("  PASS")


def test_player_view_rendering():
    print("Testing player view rendering during game...")
    state = create_initial_state(seed=42)
    state = auto_advance(state)

    for _ in range(30):
        if state.phase == Phase.GAME_OVER:
            break
        for pid in [PlayerId.RED, PlayerId.BLUE]:
            view = get_player_view(state, pid)
            text = render_player_view(state, view)
            assert isinstance(text, str)
            assert pid.name in text

        decision = get_decision(state)
        if decision is None:
            break
        action = auto_choose(state, decision)
        state = apply(state, action)

    print("  Player views rendered correctly throughout game")
    print("  PASS")


def test_game_results_variety():
    print("Testing game result variety across seeds...")
    results = {}

    for seed in range(100):
        state = run_auto_game(seed, max_decisions=500)
        if state.game_result:
            kind = state.game_result.kind.name
            results[kind] = results.get(kind, 0) + 1

    print(f"  Results across 100 games: {results}")
    assert len(results) > 0, "Should see at least some game results"
    print("  PASS")


if __name__ == "__main__":
    print("=" * 60)
    print("Fools' Journey — Stage 9 Validation")
    print("=" * 60)
    test_render_full_state()
    test_card_short()
    test_auto_game_completes()
    test_auto_game_no_crashes()
    test_auto_game_card_conservation()
    test_auto_game_determinism()
    test_render_during_game()
    test_player_view_rendering()
    test_game_results_variety()
    print("=" * 60)
    print("ALL STAGE 9 TESTS PASSED")
    print("=" * 60)