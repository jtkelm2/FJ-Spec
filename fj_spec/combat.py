"""
Fools' Journey — Executable Spec
Stage 7: Combat and damage/healing pipeline.

Combat resolution:
  1. Player chooses: fists or weapon (which weapon for Two-Armed Freak)
  2. Weapon dulling check: can only use weapon if kill pile's top enemy
     has level >= current enemy's level (or kill pile is empty)
  3. Calculate damage: max(0, enemy_level - attack_value)
  4. Apply damage through pipeline
  5. If player survives: kill the enemy
     - Fists: enemy goes to discard
     - Weapon: enemy goes to kill pile
  6. On Kill triggers

Damage pipeline:
  - Chariot interrupt: if equipped, may discard to prevent (decision)
  - Moon deviation cap: HP cannot deviate more than 10 from recorded value
  - Apply to HP
  - Death check: Star revive (d4), Phoenix revive (d20), Leo revive (cap-1)
  - HP floor of 0

Healing pipeline:
  - Corruption: non-Corruption healing becomes damage
  - HP cap enforcement
"""

from __future__ import annotations

from dataclasses import replace
from typing import NamedTuple

from .types import (
    CardId, CardDef, CardState, CardType, Parity,
    PlayerId,
    PlayerState, WeaponSlot,
    GameState,
    ActionContext,
    Trigger,
)
from .cards import has_trigger, CardType as CT
from .state_helpers import (
    gs_get_player, gs_update_player,
    gs_set_card_state, gs_get_rng, gs_with_rng_result,
    ps_set_hp, ps_set_hp_uncapped, ps_set_dead,
    ps_set_equipment, ps_set_weapon_slots, ps_set_hp_cap,
    ps_add_to_discard, ps_add_to_refresh,
    ps_add_permanent_ability,
)
from .setup import (
    player_is_corruption, player_is_phoenix, player_is_leo,
)
from .rng import rng_d4, rng_d20


# ---------------------------------------------------------------------------
# Damage source tracking
# ---------------------------------------------------------------------------

class DamageSource:
    """Tag for the source of damage, so pipeline stages can distinguish."""
    COMBAT = "combat"
    DISTANCE_PENALTY = "distance_penalty"
    CARD_EFFECT = "card_effect"
    SELF_INFLICTED = "self_inflicted"
    CORRUPTION_TICK = "corruption_tick"
    PHOENIX_TICK = "phoenix_tick"
    SURVIVOR_TICK = "survivor_tick"
    FOOD_PENALTY = "food_penalty"


class HealSource:
    """Tag for the source of healing."""
    FOOD = "food"
    CARD_EFFECT = "card_effect"
    CORRUPTION = "corruption"
    EMPRESS = "empress"
    BELLYFILLER = "bellyfiller"
    TEMPERANCE = "temperance"
    STAR_REVIVE = "star_revive"
    PHOENIX_REVIVE = "phoenix_revive"
    LEO_REVIVE = "leo_revive"


# ---------------------------------------------------------------------------
# Core damage pipeline
# ---------------------------------------------------------------------------

def apply_damage(
    state: GameState,
    player: PlayerId,
    amount: int,
    source: str = DamageSource.CARD_EFFECT,
) -> GameState:
    """
    Apply damage through the full pipeline:
      1. Moon deviation cap (clamp damage so HP doesn't deviate > 10)
      2. Apply to HP
      3. Check death → Star/Phoenix/Leo revive

    Chariot interrupt is NOT handled here — it requires a player decision
    and will be handled in Stage 8 via the effect system.

    Returns updated GameState.
    """
    if amount <= 0:
        return state

    ps = gs_get_player(state, player)
    if ps.is_dead:
        return state

    # --- Moon deviation cap ---
    amount = _apply_moon_cap_to_damage(state, player, amount)

    if amount <= 0:
        return state

    # --- Apply damage ---
    new_hp = ps.hp - amount
    if new_hp <= 0:
        # Check for revive mechanics before marking dead
        state = _apply_lethal_damage(state, player, new_hp, source)
    else:
        ps = gs_get_player(state, player)
        ps = ps_set_hp(ps, new_hp)
        state = gs_update_player(state, player, ps)

    return state


def apply_healing(
    state: GameState,
    player: PlayerId,
    amount: int,
    source: str = HealSource.CARD_EFFECT,
) -> GameState:
    """
    Apply healing through the pipeline:
      1. Corruption check: non-Corruption healing becomes damage
      2. Moon deviation cap
      3. HP cap enforcement

    Returns updated GameState.
    """
    if amount <= 0:
        return state

    ps = gs_get_player(state, player)
    if ps.is_dead:
        return state

    # --- Corruption inversion ---
    if player_is_corruption(state, player) and source != HealSource.CORRUPTION:
        # All non-Corruption healing becomes damage
        return apply_damage(state, player, amount, DamageSource.CORRUPTION_TICK)

    # --- Moon deviation cap ---
    amount = _apply_moon_cap_to_healing(state, player, amount)

    if amount <= 0:
        return state

    # --- Apply healing with HP cap ---
    new_hp = min(ps.hp + amount, ps.hp_cap)
    ps = ps_set_hp(ps, new_hp)
    state = gs_update_player(state, player, ps)

    return state


def set_hp_direct(
    state: GameState,
    player: PlayerId,
    target_hp: int,
) -> GameState:
    """
    Directly set HP to a specific value (e.g., Wheel of Fortune).
    Still respects Moon deviation cap and HP cap/floor.
    Does NOT trigger Corruption inversion (it's not healing or damage).
    """
    ps = gs_get_player(state, player)
    if ps.is_dead:
        return state

    target_hp = max(0, min(target_hp, ps.hp_cap))

    # Moon deviation cap
    target_hp = _clamp_by_moon(state, player, target_hp)

    if target_hp <= 0:
        return _apply_lethal_damage(state, player, target_hp, DamageSource.CARD_EFFECT)

    ps = gs_get_player(state, player)
    ps = ps_set_hp(ps, target_hp)
    state = gs_update_player(state, player, ps)
    return state


# ---------------------------------------------------------------------------
# Moon deviation cap
# ---------------------------------------------------------------------------

def _get_moon_recorded_hp(state: GameState, player: PlayerId) -> int | None:
    """Get the recorded HP for Moon deviation cap, or None if not active."""
    ps = gs_get_player(state, player)
    if "moon_deviation_cap" not in ps.permanent_abilities:
        return None
    # The recorded HP is stored in the RefreshContext's moon_recorded_hp
    # But during Action Phase, we need it from the last refresh.
    # Store it on the player state or context... For now, use the context.
    from .types import RefreshContext, ActionContext
    ctx = state.phase_context
    if isinstance(ctx, ActionContext):
        # During action phase, moon HP was recorded at start of last Refresh
        # We need to carry it forward. For simplicity, store on the context.
        # Actually, the deviation cap should use the value recorded at start
        # of the CURRENT Refresh Phase and persist through the Action Phase.
        # Let's store it on the player's permanent_abilities as a special marker.
        pass

    # Simplified: check if player has the ability, use a stored value
    # For now, we'll look at the game state's phase_context chain
    # This is a known simplification — the recorded HP should be stored
    # more robustly. For now, we skip Moon if we can't find the value.
    return None


def _apply_moon_cap_to_damage(state: GameState, player: PlayerId, amount: int) -> int:
    """Reduce damage amount if it would violate Moon deviation cap."""
    recorded = _get_moon_recorded_hp(state, player)
    if recorded is None:
        return amount

    ps = gs_get_player(state, player)
    min_allowed = max(0, recorded - 10)
    resulting_hp = ps.hp - amount
    if resulting_hp < min_allowed:
        # Clamp: only deal enough damage to reach min_allowed
        amount = max(0, ps.hp - min_allowed)
    return amount


def _apply_moon_cap_to_healing(state: GameState, player: PlayerId, amount: int) -> int:
    """Reduce healing amount if it would violate Moon deviation cap."""
    recorded = _get_moon_recorded_hp(state, player)
    if recorded is None:
        return amount

    ps = gs_get_player(state, player)
    max_allowed = min(ps.hp_cap, recorded + 10)
    resulting_hp = ps.hp + amount
    if resulting_hp > max_allowed:
        amount = max(0, max_allowed - ps.hp)
    return amount


def _clamp_by_moon(state: GameState, player: PlayerId, target_hp: int) -> int:
    """Clamp a target HP value by Moon deviation cap."""
    recorded = _get_moon_recorded_hp(state, player)
    if recorded is None:
        return target_hp
    min_allowed = max(0, recorded - 10)
    max_allowed = recorded + 10
    return max(min_allowed, min(max_allowed, target_hp))


# ---------------------------------------------------------------------------
# Lethal damage and revive mechanics
# ---------------------------------------------------------------------------

def _apply_lethal_damage(
    state: GameState,
    player: PlayerId,
    resulting_hp: int,
    source: str,
) -> GameState:
    """
    Handle lethal damage: check Star, Phoenix, Leo revive before death.

    Priority: Star > Phoenix > Leo (in practice only one applies per player).
    """
    rng = gs_get_rng(state)

    # --- The Star: if equipped, revive to d4 HP ---
    star_id = _find_equipped_named(state, player, "major_17")
    if star_id is not None:
        # Discard Star
        state = _discard_equipment_by_id(state, player, star_id)
        # Revive to d4 HP
        rng, roll = rng_d4(rng)
        state = gs_with_rng_result(state, rng)
        ps = gs_get_player(state, player)
        ps = ps_set_hp(ps, min(roll, ps.hp_cap))
        # Refresh all action cards and end Action Phase
        ps = replace(ps, action_phase_over=True)
        state = gs_update_player(state, player, ps)
        # Refresh action field cards
        state = _refresh_player_action_field(state, player)
        return state

    # --- Phoenix: revive to d20 HP, strip other's role ---
    if player_is_phoenix(state, player):
        rng, roll = rng_d20(rng)
        state = gs_with_rng_result(state, rng)
        ps = gs_get_player(state, player)
        ps = ps_set_hp(ps, min(roll, ps.hp_cap))
        state = gs_update_player(state, player, ps)
        # Strip other player's role abilities
        other_pid = player.other()
        other_ps = gs_get_player(state, other_pid)
        other_ps = replace(other_ps,
                           role_def_name=None,
                           permanent_abilities=frozenset())
        state = gs_update_player(state, other_pid, other_ps)
        return state

    # --- Leo: reduce HP cap by 1, revive to full ---
    if player_is_leo(state, player):
        ps = gs_get_player(state, player)
        new_cap = ps.hp_cap - 1
        if new_cap <= 0:
            # Permanent death
            ps = ps_set_dead(ps)
            state = gs_update_player(state, player, ps)
            return state
        ps = ps_set_hp_cap(ps, new_cap)
        ps = ps_set_hp(ps, new_cap)  # Revive to full (new cap)
        state = gs_update_player(state, player, ps)
        return state

    # --- No revive: player dies ---
    ps = gs_get_player(state, player)
    ps = ps_set_dead(ps)
    state = gs_update_player(state, player, ps)
    return state


def _refresh_player_action_field(state: GameState, player: PlayerId) -> GameState:
    """Refresh all cards on a player's action field to their refresh pile."""
    from .state_helpers import af_clear_slot, gs_set_action_field
    af = state.action_field
    ps = gs_get_player(state, player)

    for i in range(4):
        af, cleared = af_clear_slot(af, player, i)
        if cleared:
            ps = replace(ps, refresh_pile=ps.refresh_pile + cleared)

    state = gs_set_action_field(state, af)
    state = gs_update_player(state, player, ps)
    return state


# ---------------------------------------------------------------------------
# Combat resolution
# ---------------------------------------------------------------------------

class CombatResult(NamedTuple):
    """Result of combat resolution."""
    damage_taken: int
    killed: bool        # Did the player kill the enemy?
    attack_mode: str    # "fists" or "weapon"
    weapon_slot_idx: int | None  # Which weapon slot was used


def resolve_combat(
    state: GameState,
    resolver: PlayerId,
    enemy_id: CardId,
    attack_mode: str,
    weapon_slot_idx: int = 0,
) -> GameState:
    """
    Resolve combat between a player and an enemy/boss/guard.

    Args:
        state: Current game state
        resolver: Player fighting the enemy
        enemy_id: CardId of the enemy being fought
        attack_mode: "fists" or "weapon"
        weapon_slot_idx: Which weapon slot to use (for Two-Armed Freak)

    Returns:
        Updated GameState after combat.
    """
    ps = gs_get_player(state, resolver)
    cd = state.card_def(enemy_id)
    enemy_level = cd.level or 0

    if attack_mode == "fists":
        # Fists: attack value 0, enemy goes to discard
        damage = enemy_level
        state = apply_damage(state, resolver, damage, DamageSource.COMBAT)

        ps = gs_get_player(state, resolver)
        if not ps.is_dead:
            # Kill: enemy goes to discard
            ps = replace(ps, discard_pile=ps.discard_pile + (enemy_id,))
            state = gs_update_player(state, resolver, ps)
        # If dead, enemy stays (not killed)
        return state

    else:
        # Weapon attack
        ws = ps.weapon_slots[weapon_slot_idx]
        assert ws.weapon is not None, "Cannot attack with weapon: no weapon equipped"

        weapon_cd = state.card_def(ws.weapon)
        weapon_level = weapon_cd.level or 0

        # Emperor bonus: +1 if Emperor equipped
        if _has_equipped_named(state, resolver, "major_4"):
            weapon_level += 1

        # Strength counter penalty (Good only): -1 per counter
        if ws.weapon is not None:
            strength_cd = state.card_def(ws.weapon)
            if strength_cd.name == "major_8":
                # Check if resolver is Good
                if ps.alignment.name == "GOOD":
                    cs = state.card_state(ws.weapon)
                    weapon_level -= cs.counters

        damage = max(0, enemy_level - weapon_level)
        state = apply_damage(state, resolver, damage, DamageSource.COMBAT)

        ps = gs_get_player(state, resolver)
        if not ps.is_dead:
            # Kill: enemy goes to kill pile
            weapon_slots = list(ps.weapon_slots)
            ws = weapon_slots[weapon_slot_idx]
            new_kill_pile = ws.kill_pile + (enemy_id,)
            weapon_slots[weapon_slot_idx] = WeaponSlot(
                weapon=ws.weapon,
                kill_pile=new_kill_pile,
                parity=ws.parity,
            )
            ps = replace(ps, weapon_slots=tuple(weapon_slots))
            state = gs_update_player(state, resolver, ps)

        return state


def can_use_weapon(
    state: GameState,
    resolver: PlayerId,
    enemy_id: CardId,
    weapon_slot_idx: int = 0,
) -> bool:
    """
    Check if a player can use their weapon against a specific enemy.

    Weapon dulling rule: can only attack if kill pile is empty OR
    the most recent enemy in kill pile has level >= enemy's level.
    """
    ps = gs_get_player(state, resolver)
    if weapon_slot_idx >= len(ps.weapon_slots):
        return False

    ws = ps.weapon_slots[weapon_slot_idx]
    if ws.weapon is None:
        return False

    enemy_cd = state.card_def(enemy_id)
    enemy_level = enemy_cd.level or 0

    # Parity check for Two-Armed Freak
    if ws.parity is not None:
        if ws.parity == Parity.ODD and enemy_level % 2 == 0:
            return False
        if ws.parity == Parity.EVEN and enemy_level % 2 == 1:
            return False

    # Dulling check
    if ws.kill_pile:
        last_kill_id = ws.kill_pile[-1]
        last_kill_cd = state.card_def(last_kill_id)
        last_kill_level = last_kill_cd.level or 0
        if last_kill_level < enemy_level:
            return False

    return True


def get_attack_options(
    state: GameState,
    resolver: PlayerId,
    enemy_id: CardId,
) -> list[tuple[str, int]]:
    """
    Get available attack modes for a given enemy.

    Returns list of (mode_name, weapon_slot_idx) tuples.
    Always includes ("fists", -1).
    """
    options: list[tuple[str, int]] = [("fists", -1)]

    ps = gs_get_player(state, resolver)
    for i, ws in enumerate(ps.weapon_slots):
        if ws.weapon is not None and can_use_weapon(state, resolver, enemy_id, i):
            if ws.parity == Parity.ODD:
                options.append(("weapon_left", i))
            elif ws.parity == Parity.EVEN:
                options.append(("weapon_right", i))
            else:
                options.append(("weapon", i))

    return options


# ---------------------------------------------------------------------------
# Mutiny combat
# ---------------------------------------------------------------------------

def resolve_mutiny(state: GameState, attacker: PlayerId) -> GameState:
    """
    Resolve Mutiny: attack the other player as an enemy.

    Enemy level = their HP + weapon sharpness.
    Weapon sharpness = weapon level if kill pile empty, else min(weapon level, last kill level).
    For Two-Armed Freak: use the higher of the two weapons' sharpness.
    """
    defender = attacker.other()
    defender_ps = gs_get_player(state, defender)

    # Calculate weapon sharpness
    sharpness = _calculate_weapon_sharpness(state, defender)
    enemy_level = defender_ps.hp + sharpness

    # Mutineer attacks with their own weapon or fists
    attacker_ps = gs_get_player(state, attacker)
    attack_value = 0
    for ws in attacker_ps.weapon_slots:
        if ws.weapon is not None:
            weapon_cd = state.card_def(ws.weapon)
            wl = weapon_cd.level or 0
            if _has_equipped_named(state, attacker, "major_4"):
                wl += 1
            attack_value = max(attack_value, wl)

    damage = max(0, enemy_level - attack_value)
    state = apply_damage(state, attacker, damage, DamageSource.COMBAT)

    attacker_ps = gs_get_player(state, attacker)
    if not attacker_ps.is_dead:
        # Attacker survived → defender dies
        defender_ps = gs_get_player(state, defender)
        defender_ps = ps_set_dead(defender_ps)
        state = gs_update_player(state, defender, defender_ps)

    return state


def _calculate_weapon_sharpness(state: GameState, player: PlayerId) -> int:
    """
    Calculate weapon sharpness for Mutiny defense.

    Sharpness = weapon level if kill pile empty,
    else min(weapon level, last kill's level).
    For Two-Armed Freak: max of both weapons' sharpness.
    """
    ps = gs_get_player(state, player)
    max_sharpness = 0

    for ws in ps.weapon_slots:
        if ws.weapon is None:
            continue
        weapon_cd = state.card_def(ws.weapon)
        weapon_level = weapon_cd.level or 0

        if _has_equipped_named(state, player, "major_4"):
            weapon_level += 1

        if not ws.kill_pile:
            sharpness = weapon_level
        else:
            last_kill_cd = state.card_def(ws.kill_pile[-1])
            last_kill_level = last_kill_cd.level or 0
            sharpness = min(weapon_level, last_kill_level)

        max_sharpness = max(max_sharpness, sharpness)

    return max_sharpness


# ---------------------------------------------------------------------------
# Equipment helpers
# ---------------------------------------------------------------------------

def _find_equipped_named(state: GameState, pid: PlayerId, card_name: str) -> CardId | None:
    ps = gs_get_player(state, pid)
    for eq_id in ps.equipment:
        if eq_id is not None and state.card_def(eq_id).name == card_name:
            return eq_id
    return None


def _has_equipped_named(state: GameState, pid: PlayerId, card_name: str) -> bool:
    return _find_equipped_named(state, pid, card_name) is not None


def _discard_equipment_by_id(state: GameState, pid: PlayerId, card_id: CardId) -> GameState:
    ps = gs_get_player(state, pid)
    equipment = list(ps.equipment)
    for i, eq in enumerate(equipment):
        if eq == card_id:
            equipment[i] = None
            break
    ps = replace(ps,
                 equipment=(equipment[0], equipment[1]),
                 discard_pile=ps.discard_pile + (card_id,))
    if ps.role_card_id == card_id:
        ps = replace(ps, role_card_id=None)
    state = gs_update_player(state, pid, ps)
    return state