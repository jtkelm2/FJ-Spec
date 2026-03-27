"""
Fools' Journey — Executable Spec
Stage 3: Fog-of-war view construction.

Constructs a PlayerView for a given player that reveals only the information
they are allowed to see according to the rules.
"""

from __future__ import annotations

from .types import (
    CardId, PlayerId,
    PlayerState, WeaponSlot,
    ActionSlot, ActionField,
    GameState, Phase,
    PendingDecision,
    PlayerView, VisiblePlayerState, FoggedPlayerState,
    VisibleActionField, FoggedWeaponSlot,
)
from .state_helpers import gs_get_player


def get_player_view(state: GameState, pid: PlayerId) -> PlayerView:
    """
    Construct the full view of the game from one player's perspective.

    What a player CAN see:
      - Their own: hand, manipulation field, equipment, weapon slots (incl. kill pile),
        HP, HP cap, alignment, role, permanent abilities, phase tracking flags
      - Their own deck/refresh/discard: SIZE only (face-down piles)
      - Other player: HP, deck/refresh/discard sizes, hand size,
        equipment (public), weapon slots (public), is_dead
      - Action field: own 4 slots fully visible, other's distant slots (0, 3) visible,
        other's hidden slots (1, 2) card count only
      - Shared: guard deck size, priority, phase, turn number

    What a player CANNOT see:
      - Other player's hand contents
      - Other player's manipulation field contents
      - Other player's hidden action slots (1, 2) card identities
      - Other player's alignment and role
      - Contents of any face-down pile (own or other's deck, refresh, discard)
    """
    me = gs_get_player(state, pid)
    other_pid = pid.other()
    them = gs_get_player(state, other_pid)

    my_view = _build_visible_state(me)
    their_view = _build_fogged_state(them)
    action_view = _build_action_field_view(state.action_field, pid)

    # Filter the pending decision: only show if it's for this player
    decision = state.pending if (state.pending and state.pending.player == pid) else None

    return PlayerView(
        me=pid,
        my_state=my_view,
        other_state=their_view,
        action_field=action_view,
        guard_deck_size=len(state.guard_deck),
        priority=state.priority,
        phase=state.phase,
        turn_number=state.turn_number,
        decision=decision,
    )


def _build_visible_state(ps: PlayerState) -> VisiblePlayerState:
    """Full view of a player's own state."""
    return VisiblePlayerState(
        hp=ps.hp,
        hp_cap=ps.hp_cap,
        alignment=ps.alignment,
        role_def_name=ps.role_def_name,
        permanent_abilities=ps.permanent_abilities,
        deck_size=len(ps.deck),
        refresh_pile_size=len(ps.refresh_pile),
        discard_pile_size=len(ps.discard_pile),
        hand=ps.hand,
        manipulation_field=ps.manipulation_field.cards,
        equipment=ps.equipment,
        weapon_slots=ps.weapon_slots,
        has_eaten_this_phase=ps.has_eaten_this_phase,
        action_plays_made=ps.action_plays_made,
        is_dead=ps.is_dead,
        action_phase_over=ps.action_phase_over,
    )


def _build_fogged_state(ps: PlayerState) -> FoggedPlayerState:
    """What one player can see of the other."""
    fogged_weapons = tuple(
        FoggedWeaponSlot(
            weapon=ws.weapon,
            kill_pile=ws.kill_pile,
            parity=ws.parity,
        )
        for ws in ps.weapon_slots
    )
    return FoggedPlayerState(
        hp=ps.hp,
        deck_size=len(ps.deck),
        refresh_pile_size=len(ps.refresh_pile),
        discard_pile_size=len(ps.discard_pile),
        hand_size=len(ps.hand),
        equipment=ps.equipment,
        weapon_slots=fogged_weapons,
        is_dead=ps.is_dead,
    )


def _build_action_field_view(af: ActionField, pid: PlayerId) -> VisibleActionField:
    """
    Build the action field from one player's perspective.
    Own slots: fully visible.
    Other's distant (0, 3): fully visible.
    Other's hidden (1, 2): card count only.
    """
    own_slots = af.slots_for(pid)
    other_slots = af.slots_for(pid.other())

    return VisibleActionField(
        own_slots=own_slots,
        other_distant_0=other_slots[0],
        other_distant_3=other_slots[3],
        other_hidden_1_count=len(other_slots[1].cards),
        other_hidden_2_count=len(other_slots[2].cards),
    )


# ---------------------------------------------------------------------------
# Text rendering for CLI / debugging
# ---------------------------------------------------------------------------

def describe_card(state: GameState, card_id: CardId) -> str:
    """Human-readable one-line description of a card."""
    cd = state.card_def(card_id)
    parts = []
    if cd.big_name:
        parts.append(cd.big_name)
    if cd.level is not None:
        parts.append(f"Lv{cd.level}")

    type_strs = sorted(t.name for t in cd.card_types)
    parts.append("/".join(type_strs))

    if cd.is_elusive:
        parts.append("[Elusive]")
    if cd.is_first:
        parts.append("[First]")

    name_part = f"({cd.name})" if cd.big_name else cd.name
    return f"{name_part} {' '.join(parts)}"


def describe_slot(state: GameState, slot: ActionSlot) -> str:
    """Human-readable description of an action slot."""
    if slot.is_empty:
        return "[empty]"
    descs = [describe_card(state, cid) for cid in slot.cards]
    return " / ".join(descs)


def render_player_view(state: GameState, view: PlayerView) -> str:
    """Render a full player view as a multi-line string for CLI display."""
    lines: list[str] = []
    pid = view.me
    other_pid = pid.other()
    my = view.my_state
    them = view.other_state

    lines.append(f"=== {pid.name}'s View === Turn {view.turn_number} | "
                 f"Phase: {view.phase.name} | Priority: {view.priority.name}")
    lines.append("")

    # My status
    lines.append(f"  HP: {my.hp}/{my.hp_cap}  "
                 f"Alignment: {my.alignment.name}  "
                 f"Role: {my.role_def_name or '(none)'}")

    # My equipment
    eq_strs = []
    for eq in my.equipment:
        if eq is not None:
            eq_strs.append(describe_card(state, eq))
        else:
            eq_strs.append("[empty]")
    lines.append(f"  Equipment: {' | '.join(eq_strs)}")

    # My weapons
    for i, ws in enumerate(my.weapon_slots):
        parity_str = f" ({ws.parity.name})" if ws.parity else ""
        if ws.weapon is not None:
            w_desc = describe_card(state, ws.weapon)
            kills = [describe_card(state, k) for k in ws.kill_pile]
            kill_str = ", ".join(kills) if kills else "(none)"
            lines.append(f"  Weapon{parity_str}: {w_desc}  Kills: {kill_str}")
        else:
            lines.append(f"  Weapon{parity_str}: [none]")

    # My hand
    if my.hand:
        hand_strs = [describe_card(state, c) for c in my.hand]
        lines.append(f"  Hand ({len(my.hand)}): {', '.join(hand_strs)}")
    else:
        lines.append("  Hand: (empty)")

    # My manipulation field
    if my.manipulation_field:
        manip_strs = [describe_card(state, c) for c in my.manipulation_field]
        lines.append(f"  Manipulation: {', '.join(manip_strs)}")

    # My piles
    lines.append(f"  Deck: {my.deck_size} | Refresh: {my.refresh_pile_size} | "
                 f"Discard: {my.discard_pile_size}")

    if my.permanent_abilities:
        lines.append(f"  Abilities: {', '.join(sorted(my.permanent_abilities))}")

    # Flags
    flags = []
    if my.has_eaten_this_phase:
        flags.append("eaten")
    if my.action_phase_over:
        flags.append("phase_over")
    if flags:
        lines.append(f"  Flags: {', '.join(flags)}  Plays: {my.action_plays_made}/3")

    lines.append("")

    # Other player status
    lines.append(f"--- {other_pid.name} (opponent) ---")
    lines.append(f"  HP: {them.hp}  Dead: {them.is_dead}")

    eq_strs = []
    for eq in them.equipment:
        if eq is not None:
            eq_strs.append(describe_card(state, eq))
        else:
            eq_strs.append("[empty]")
    lines.append(f"  Equipment: {' | '.join(eq_strs)}")

    for i, ws in enumerate(them.weapon_slots):
        parity_str = f" ({ws.parity.name})" if ws.parity else ""
        if ws.weapon is not None:
            w_desc = describe_card(state, ws.weapon)
            kills = [describe_card(state, k) for k in ws.kill_pile]
            kill_str = ", ".join(kills) if kills else "(none)"
            lines.append(f"  Weapon{parity_str}: {w_desc}  Kills: {kill_str}")
        else:
            lines.append(f"  Weapon{parity_str}: [none]")

    lines.append(f"  Hand: {them.hand_size} cards")
    lines.append(f"  Deck: {them.deck_size} | Refresh: {them.refresh_pile_size} | "
                 f"Discard: {them.discard_pile_size}")

    lines.append("")

    # Action field
    lines.append("--- Action Field ---")
    own_slots = view.action_field.own_slots
    slot_labels = ["Top Distant", "Top Hidden", "Bottom Hidden", "Bottom Distant"]
    for i, (slot, label) in enumerate(zip(own_slots, slot_labels)):
        lines.append(f"  {pid.name} {label}: {describe_slot(state, slot)}")

    # Other player's slots — distant visible, hidden fogged
    lines.append(f"  {other_pid.name} Top Distant: "
                 f"{describe_slot(state, view.action_field.other_distant_0)}")
    lines.append(f"  {other_pid.name} Top Hidden: "
                 f"({view.action_field.other_hidden_1_count} cards)")
    lines.append(f"  {other_pid.name} Bottom Hidden: "
                 f"({view.action_field.other_hidden_2_count} cards)")
    lines.append(f"  {other_pid.name} Bottom Distant: "
                 f"{describe_slot(state, view.action_field.other_distant_3)}")

    lines.append(f"  Guard deck: {view.guard_deck_size} cards")

    # Decision
    if view.decision:
        lines.append("")
        lines.append(f">>> DECISION: {view.decision.kind.name}")
        lines.append(f"    {view.decision.context_description}")
        for i, action in enumerate(view.decision.legal_actions):
            lines.append(f"    [{i}] {_describe_action(state, action)}")

    return "\n".join(lines)


def _describe_action(state: GameState, action: "Action") -> str:
    """Short description of an action for display."""
    from .types import ActionKind

    parts = [action.kind.name]
    if action.index is not None:
        parts.append(f"idx={action.index}")
    if action.card_id is not None:
        parts.append(describe_card(state, action.card_id))
    if action.slot_ref is not None:
        parts.append(f"slot={action.slot_ref.owner.name}[{action.slot_ref.index}]")
    if action.amount is not None:
        parts.append(f"amt={action.amount}")
    if action.flag is not None:
        parts.append(f"flag={action.flag}")
    if action.attack_mode is not None:
        parts.append(f"mode={action.attack_mode.name}")
    if action.kind == ActionKind.DECLINE:
        return "DECLINE (pass)"
    return " | ".join(parts)
