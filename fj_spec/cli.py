#!/usr/bin/env python3
"""
Fools' Journey — Executable Spec
Stage 9: CLI player interface.

A debug-mode text-based interface that runs a full game interactively.
Both players' full states are visible (no fog enforcement).
Decisions are presented with human-friendly input formats.

Usage:
    python -m fj_spec.cli [--seed N] [--good ROLE ROLE] [--evil ROLE]
"""

from __future__ import annotations

import sys
import argparse

from .types import (
    CardId, PlayerId, Phase, Alignment,
    GameState, Action, ActionKind, AttackMode,
    PendingDecision, DecisionKind,
    ActionSlot, ActionField,
    SlotRef, SwapPair, DumpFateChoice, DumpFate,
    HierophantSplit,
)
from .cards import get_card_def, ALL_CARD_DEFS
from .setup import create_initial_state
from .engine import apply, auto_advance, start_game, get_decision, GameOverError
from .fog import (
    get_player_view, render_player_view,
    describe_card, describe_slot,
)
from .state_helpers import (
    gs_get_player, af_get_slot, af_find_empty_slots, af_find_nonempty_slots,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

SLOT_LABELS = {0: "Top Distant", 1: "Top Hidden", 2: "Bottom Hidden", 3: "Bottom Distant"}


def render_full_state(state: GameState) -> str:
    """Render the full game state (debug mode: both players visible)."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append(f"  TURN {state.turn_number}  |  Phase: {state.phase.name}  |  "
                 f"Priority: {state.priority.name}")
    lines.append("=" * 72)

    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        lines.append("")
        marker = " *** " if (state.pending and state.pending.player == pid) else "     "
        lines.append(f"--- {pid.name}{marker}---")

        align_str = ps.alignment.name
        role_str = ps.role_def_name or "(none)"
        role_cd = get_card_def(ps.role_def_name) if ps.role_def_name else None
        role_display = role_cd.big_name if role_cd and role_cd.big_name else role_str

        lines.append(f"  HP: {ps.hp}/{ps.hp_cap}  |  {align_str}  |  Role: {role_display}")

        if ps.is_dead:
            lines.append("  *** DEAD ***")

        # Equipment
        eq_parts = []
        for i, eq in enumerate(ps.equipment):
            if eq is not None:
                eq_parts.append(f"[{i}] {_card_short(state, eq)}")
            else:
                eq_parts.append(f"[{i}] -")
        lines.append(f"  Equip: {' | '.join(eq_parts)}")

        # Weapons
        for i, ws in enumerate(ps.weapon_slots):
            par = f" ({ws.parity.name})" if ws.parity else ""
            if ws.weapon is not None:
                kills = ", ".join(_card_short(state, k) for k in ws.kill_pile)
                lines.append(f"  Weapon{par}: {_card_short(state, ws.weapon)}  "
                             f"Kills: [{kills}]")
            else:
                lines.append(f"  Weapon{par}: -")

        # Hand
        if ps.hand:
            h = ", ".join(_card_short(state, c) for c in ps.hand)
            lines.append(f"  Hand ({len(ps.hand)}): {h}")
        else:
            lines.append(f"  Hand: -")

        # Manipulation
        if ps.manipulation_field.cards:
            m = ", ".join(_card_short(state, c) for c in ps.manipulation_field.cards)
            lines.append(f"  Manip: {m}")

        # Piles
        lines.append(f"  Deck: {len(ps.deck)}  |  Refresh: {len(ps.refresh_pile)}  |  "
                     f"Discard: {len(ps.discard_pile)}")

        # Abilities
        if ps.permanent_abilities:
            lines.append(f"  Abilities: {', '.join(sorted(ps.permanent_abilities))}")

        # Flags
        flags = []
        if ps.has_eaten_this_phase:
            flags.append("eaten")
        if ps.action_phase_over:
            flags.append("phase_over")
        if ps.action_plays_made > 0:
            flags.append(f"plays={ps.action_plays_made}")
        if flags:
            lines.append(f"  Flags: {', '.join(flags)}")

    # Action field
    lines.append("")
    lines.append("--- Action Field ---")
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        for i in range(4):
            slot = af_get_slot(state.action_field, pid, i)
            label = f"  {pid.name} {SLOT_LABELS[i]}"
            if slot.is_empty:
                lines.append(f"{label:35s} -")
            else:
                cards_str = " / ".join(_card_short(state, c) for c in slot.cards)
                lines.append(f"{label:35s} {cards_str}")

    lines.append(f"  Guard deck: {len(state.guard_deck)} cards")
    lines.append("")

    return "\n".join(lines)


def _card_short(state: GameState, card_id: CardId) -> str:
    """Short card description: name or big_name + level + tags."""
    cd = state.card_def(card_id)
    lv = f" Lv{cd.level}" if cd.level is not None else ""
    tags = ""
    if cd.is_elusive:
        tags += "E"
    if cd.is_first:
        tags += "F"
    tag_str = f"[{tags}]" if tags else ""

    if cd.big_name:
        return f"{cd.big_name}{lv}{tag_str}"
    else:
        return f"{cd.name}{lv}{tag_str}"


# ---------------------------------------------------------------------------
# Decision prompting
# ---------------------------------------------------------------------------

def prompt_decision(state: GameState, decision: PendingDecision) -> Action:
    """Prompt the player for a decision and return their chosen Action."""
    print(f"\n>>> {decision.player.name}'s decision: {decision.kind.name}")
    if decision.context_description:
        for line in decision.context_description.split("\n"):
            print(f"    {line}")
    print()

    match decision.kind:
        case DecisionKind.CHOOSE_MANIPULATE_OR_DUMP:
            return _prompt_manipulate_or_dump(state, decision)
        case DecisionKind.CHOOSE_SWAP:
            return _prompt_swap(state, decision)
        case DecisionKind.CHOOSE_DUMP_FATE:
            return _prompt_dump_fate(state, decision)
        case DecisionKind.CHOOSE_FORCE:
            return _prompt_force(state, decision)
        case DecisionKind.CHOOSE_FORCE_CARD:
            return _prompt_force_card(state, decision)
        case DecisionKind.REARRANGE_ACTION_FIELD:
            return _prompt_rearrange(state, decision)
        case DecisionKind.CHOOSE_LAST_RESORT:
            return _prompt_last_resort(state, decision)
        case DecisionKind.CHOOSE_ACTION_SLOT:
            return _prompt_action_slot(state, decision)
        case DecisionKind.GRANT_CONSENT:
            return _prompt_consent(state, decision)
        case DecisionKind.RECYCLE_DECISION:
            return _prompt_recycle(state, decision)
        case DecisionKind.VOLUNTARY_DISCARD:
            return _prompt_voluntary_discard(state, decision)
        case _:
            return _prompt_generic(state, decision)


def _prompt_manipulate_or_dump(state, decision):
    print("  [0] Manipulate  [1] Dump")
    idx = _read_int("Choice", 0, 1)
    return Action(kind=ActionKind.SELECT_INDEX, index=idx)


def _prompt_swap(state, decision):
    pid = decision.player
    ps = gs_get_player(state, pid)

    swap_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_SWAP]
    decline = [a for a in decision.legal_actions if a.kind == ActionKind.DECLINE]

    print("  Manipulation field:")
    for i, cid in enumerate(ps.manipulation_field.cards):
        print(f"    M{i}: {_card_short(state, cid)}")
    print("  Hand:")
    for i, cid in enumerate(ps.hand):
        print(f"    H{i}: {_card_short(state, cid)}")
    print(f"  [d] Done swapping")

    while True:
        raw = input("  Swap (e.g. 'M0 H2') or 'd' to finish: ").strip()
        if raw.lower() == 'd':
            return decline[0]
        parts = raw.upper().split()
        if len(parts) == 2:
            try:
                m_idx = int(parts[0].replace("M", ""))
                h_idx = int(parts[1].replace("H", ""))
                manip_cards = ps.manipulation_field.cards
                hand_cards = ps.hand
                if 0 <= m_idx < len(manip_cards) and 0 <= h_idx < len(hand_cards):
                    swap = SwapPair(manip_card=manip_cards[m_idx], hand_card=hand_cards[h_idx])
                    action = Action(kind=ActionKind.SELECT_SWAP, swap=swap)
                    if action in decision.legal_actions:
                        return action
            except (ValueError, IndexError):
                pass
        print("  Invalid input. Try 'M0 H2' or 'd'.")


def _prompt_dump_fate(state, decision):
    pid = decision.player
    ps = gs_get_player(state, pid)

    non_elusive = []
    for cid in ps.hand:
        cd = state.card_def(cid)
        if cd.is_elusive:
            print(f"  {_card_short(state, cid)} [Elusive — auto-refreshed]")
        else:
            non_elusive.append(cid)

    if not non_elusive:
        return decision.legal_actions[0]

    for i, cid in enumerate(non_elusive):
        print(f"  [{i}] {_card_short(state, cid)}")

    print(f"\n  For each card, type 'd' (discard) or 'r' (refresh).")
    print(f"  Example for {len(non_elusive)} cards: {'d' * len(non_elusive)}")

    while True:
        raw = input("  Fates: ").strip().lower()
        if len(raw) == len(non_elusive) and all(c in ('d', 'r') for c in raw):
            fates = tuple(
                DumpFateChoice(
                    card=non_elusive[i],
                    fate=DumpFate.DISCARD if raw[i] == 'd' else DumpFate.REFRESH,
                )
                for i in range(len(non_elusive))
            )
            action = Action(kind=ActionKind.SELECT_DUMP_FATES, dump_fates=fates)
            if action in decision.legal_actions:
                return action
        print(f"  Enter exactly {len(non_elusive)} characters, each 'd' or 'r'.")


def _prompt_force(state, decision):
    card_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_CARD]
    decline = [a for a in decision.legal_actions if a.kind == ActionKind.DECLINE]

    print("  Sacrifice equipment to force (choose which card to send):")
    for i, a in enumerate(card_actions):
        print(f"  [{i}] {_card_short(state, a.card_id)}")
    print(f"  [n] No forcing")

    raw = input("  Choice: ").strip().lower()
    if raw == 'n':
        return decline[0]
    try:
        idx = int(raw)
        if 0 <= idx < len(card_actions):
            return card_actions[idx]
    except ValueError:
        pass
    print("  Invalid — defaulting to no forcing.")
    return decline[0]


def _prompt_force_card(state, decision):
    print("  Choose which card to send to the other player:")
    card_actions = decision.legal_actions
    for i, a in enumerate(card_actions):
        print(f"  [{i}] {_card_short(state, a.card_id)}")
    idx = _read_int("Choice", 0, len(card_actions) - 1)
    return card_actions[idx]


def _prompt_rearrange(state, decision):
    pid = decision.player
    slots = state.action_field.slots_for(pid)

    print("  Current action field:")
    for i in range(4):
        slot = slots[i]
        label = SLOT_LABELS[i]
        if slot.is_empty:
            print(f"    [{i}] {label}: -")
        else:
            cards = " / ".join(_card_short(state, c) for c in slot.cards)
            print(f"    [{i}] {label}: {cards}")

    print("  Enter new order as 4 digits (e.g. '2013' moves slot 0→pos 2, 1→pos 0, etc.)")
    print("  Or press Enter for no change.")

    while True:
        raw = input("  Permutation: ").strip()
        if raw == "":
            return Action(kind=ActionKind.SELECT_PERMUTATION, permutation=(0, 1, 2, 3))
        if len(raw) == 4 and all(c in "0123" for c in raw):
            perm = tuple(int(c) for c in raw)
            if sorted(perm) == [0, 1, 2, 3]:
                action = Action(kind=ActionKind.SELECT_PERMUTATION, permutation=perm)
                if action in decision.legal_actions:
                    return action
        print("  Enter 4 unique digits 0-3, or press Enter.")


def _prompt_last_resort(state, decision):
    options = []
    for a in decision.legal_actions:
        if a.kind == ActionKind.SELECT_INDEX:
            labels = {0: "Run", 1: "Call the Guards", 2: "Mutiny"}
            options.append((a.index, labels.get(a.index, f"Option {a.index}"), a))
        elif a.kind == ActionKind.DECLINE:
            options.append((-1, "No Last Resort", a))

    for idx, label, _ in options:
        if idx >= 0:
            print(f"  [{idx}] {label}")
        else:
            print(f"  [n] {label}")

    while True:
        raw = input("  Choice: ").strip().lower()
        if raw == 'n':
            for idx, _, a in options:
                if idx == -1:
                    return a
        try:
            choice = int(raw)
            for idx, _, a in options:
                if idx == choice:
                    return a
        except ValueError:
            pass
        print("  Invalid choice.")


def _prompt_action_slot(state, decision):
    pid = decision.player
    slot_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_SLOT]
    vd_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_CARD]

    print("  Available slots:")
    for i, a in enumerate(slot_actions):
        sr = a.slot_ref
        slot = af_get_slot(state.action_field, sr.owner, sr.index)
        label = f"{sr.owner.name} {SLOT_LABELS[sr.index]}"
        cards = " / ".join(_card_short(state, c) for c in slot.cards)
        print(f"  [{i}] {label}: {cards}")

    if vd_actions:
        print("  Voluntary discards:")
        for j, a in enumerate(vd_actions):
            print(f"  [v{j}] Discard {_card_short(state, a.card_id)}")

    while True:
        raw = input("  Choice: ").strip().lower()
        if raw.startswith("v") and vd_actions:
            try:
                idx = int(raw[1:])
                if 0 <= idx < len(vd_actions):
                    return vd_actions[idx]
            except ValueError:
                pass
        else:
            try:
                idx = int(raw)
                if 0 <= idx < len(slot_actions):
                    return slot_actions[idx]
            except ValueError:
                pass
        print("  Invalid choice.")


def _prompt_consent(state, decision):
    print("  [y] Grant consent  [n] Deny")
    while True:
        raw = input("  Choice: ").strip().lower()
        if raw in ('y', 'yes', '1', 'true'):
            return Action(kind=ActionKind.SELECT_BOOL, flag=True)
        if raw in ('n', 'no', '0', 'false'):
            return Action(kind=ActionKind.SELECT_BOOL, flag=False)
        print("  Enter 'y' or 'n'.")


def _prompt_recycle(state, decision):
    cards = decision.visible_cards
    print("  Cards drawn (choose which to recycle):")
    for i, cid in enumerate(cards):
        print(f"  [{i}] {_card_short(state, cid)}")

    print(f"  Enter flags: 'k'=keep, 'r'=recycle. Example: {'k' * len(cards)}")

    while True:
        raw = input("  Recycle: ").strip().lower()
        if len(raw) == len(cards) and all(c in ('k', 'r') for c in raw):
            flags = tuple(c == 'r' for c in raw)
            action = Action(kind=ActionKind.SELECT_RECYCLE, recycle_flags=flags)
            if action in decision.legal_actions:
                return action
        print(f"  Enter exactly {len(cards)} characters, each 'k' or 'r'.")


def _prompt_voluntary_discard(state, decision):
    card_actions = [a for a in decision.legal_actions if a.kind == ActionKind.SELECT_CARD]
    decline = [a for a in decision.legal_actions if a.kind == ActionKind.DECLINE]

    if not card_actions:
        return decline[0]

    print("  Voluntary discard (between card resolutions):")
    for i, a in enumerate(card_actions):
        print(f"  [{i}] Discard {_card_short(state, a.card_id)}")
    print(f"  [n] Continue without discarding")

    while True:
        raw = input("  Choice: ").strip().lower()
        if raw == 'n':
            return decline[0]
        try:
            idx = int(raw)
            if 0 <= idx < len(card_actions):
                return card_actions[idx]
        except ValueError:
            pass
        print("  Invalid choice.")


def _prompt_generic(state, decision):
    """Fallback: show all actions with indices."""
    print("  Options:")
    for i, a in enumerate(decision.legal_actions):
        print(f"  [{i}] {_describe_action_short(state, a)}")
    idx = _read_int("Choice", 0, len(decision.legal_actions) - 1)
    return decision.legal_actions[idx]


def _describe_action_short(state, action):
    parts = [action.kind.name]
    if action.card_id is not None:
        parts.append(_card_short(state, action.card_id))
    if action.slot_ref is not None:
        parts.append(f"{action.slot_ref.owner.name}[{action.slot_ref.index}]")
    if action.amount is not None:
        parts.append(f"amt={action.amount}")
    if action.flag is not None:
        parts.append(f"{'yes' if action.flag else 'no'}")
    if action.attack_mode is not None:
        parts.append(action.attack_mode.name)
    if action.kind == ActionKind.DECLINE:
        return "Pass / decline"
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def _read_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        raw = input(f"  {prompt} [{lo}-{hi}]: ").strip()
        try:
            val = int(raw)
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        print(f"  Enter a number between {lo} and {hi}.")


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def run_game(
    seed: int = 42,
    good_pool: list[str] | None = None,
    evil_pool: list[str] | None = None,
) -> None:
    """Run an interactive game in the terminal."""
    print("\n" + "=" * 72)
    print("  FOOLS' JOURNEY — Interactive CLI (Debug Mode)")
    print("=" * 72)

    state = create_initial_state(seed=seed, good_pool=good_pool, evil_pool=evil_pool)
    print(f"\n  Seed: {seed}")
    for pid in [PlayerId.RED, PlayerId.BLUE]:
        ps = gs_get_player(state, pid)
        role_cd = get_card_def(ps.role_def_name) if ps.role_def_name else None
        rname = role_cd.big_name if role_cd and role_cd.big_name else ps.role_def_name
        print(f"  {pid.name}: {ps.alignment.name} — {rname}")
    print()

    # Auto-advance to first decision
    state = auto_advance(state)

    turn_count = 0
    while state.phase != Phase.GAME_OVER:
        # Show state
        print(render_full_state(state))

        decision = get_decision(state)
        if decision is None:
            # No decision but not game over — shouldn't happen after auto_advance
            print("  [!] No decision and not game over — advancing...")
            state = auto_advance(state)
            continue

        # Prompt the player
        try:
            action = prompt_decision(state, decision)
        except (EOFError, KeyboardInterrupt):
            print("\n\n  Game aborted by user.")
            return

        # Apply
        try:
            state = apply(state, action)
        except Exception as e:
            print(f"\n  [ERROR] {e}")
            print("  Retrying...")
            continue

        turn_count += 1

    # Game over
    print(render_full_state(state))
    print("=" * 72)
    print("  GAME OVER")
    if state.game_result:
        print(f"  Result: {state.game_result.kind.name}")
        if state.game_result.winner:
            print(f"  Winner: {state.game_result.winner.name}")
        print(f"  {state.game_result.description}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fools' Journey — CLI")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--good", nargs="+", default=None,
                        help="Good role pool (e.g. good_role_1 good_role_1)")
    parser.add_argument("--evil", nargs="+", default=None,
                        help="Evil role pool (e.g. bad_role_1)")
    args = parser.parse_args()

    run_game(seed=args.seed, good_pool=args.good, evil_pool=args.evil)


if __name__ == "__main__":
    main()