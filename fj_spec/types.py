"""
Fools' Journey — Executable Spec
Stage 1: Core types, enums, and dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

CardId = int  # Unique runtime identity for every physical card instance


class PlayerId(Enum):
    RED = 0
    BLUE = 1

    def other(self) -> PlayerId:
        return PlayerId.BLUE if self is PlayerId.RED else PlayerId.RED


# ---------------------------------------------------------------------------
# Card taxonomy
# ---------------------------------------------------------------------------

class CardType(Enum):
    """Primary card type. A card may have multiple types (e.g. Event+Equipment)."""
    FOOD = auto()
    ENEMY = auto()
    BOSS = auto()
    WEAPON = auto()
    EQUIPMENT = auto()
    EVENT = auto()
    GUARD = auto()
    ROLE_GOOD = auto()
    ROLE_EVIL = auto()


class Alignment(Enum):
    GOOD = auto()
    EVIL = auto()


class Parity(Enum):
    """For Two-Armed Freak's weapon slot constraints."""
    ODD = auto()
    EVEN = auto()


# ---------------------------------------------------------------------------
# Effect system
# ---------------------------------------------------------------------------

class Trigger(Enum):
    """When an effect fires."""
    ON_RESOLVE = auto()           # Before type-default processing
    ON_RESOLVE_AFTER = auto()     # After type-default processing (e.g. "after eating")
    ON_PLACEMENT = auto()         # When placed onto an action slot (any source)
    ON_KILL = auto()              # Card enters kill pile or is discarded during Action Phase
    ON_DISCARD = auto()           # Card is discarded during Action Phase
    AFTER_DEATH = auto()          # Boss killed → permanent ability for killer
    WHILE_EQUIPPED = auto()       # Continuous while in equipment slot
    AS_WEAPON = auto()            # Continuous while wielded
    REFRESH_PHASE_END = auto()    # End-of-Refresh periodic (Empress, Bellyfiller, etc.)
    ACTION_PHASE_START = auto()   # Start of Action Phase resets
    ACTION_PHASE_END = auto()     # Elusive cleanup, etc.
    PREVENT_RUN = auto()          # Guard: forbids Running last resort
    SETUP = auto()                # One-time setup instruction (Mutineer, Fool role)
    CONTINUOUS = auto()           # Always-on modifier (Corruption, Food Fighter, etc.)


@dataclass(frozen=True)
class EffectDef:
    """One trigger–handler pair on a card."""
    trigger: Trigger
    handler: str  # Key into the handler registry (e.g. "skeleton_draw_underneath")


# ---------------------------------------------------------------------------
# Card definition (static, created once)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardDef:
    """Immutable definition of a unique card design."""
    name: str                        # Internal id: "major_21", "enemy_5", etc.
    big_name: str                    # Display name: "The World", "" for unnamed
    level: int | None                # Numeric value; None for cards without one
    card_types: frozenset[CardType]
    text: str                        # Rules text for display
    is_elusive: bool
    is_first: bool
    effects: tuple[EffectDef, ...]   # Ordered effect definitions


# ---------------------------------------------------------------------------
# Card runtime state (only for cards that accumulate counters, etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CardState:
    counters: int = 0


# ---------------------------------------------------------------------------
# Board structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionSlot:
    """One of the 8 action slots. Cards are ordered top-to-bottom (index 0 = top)."""
    cards: tuple[CardId, ...] = ()

    @property
    def is_empty(self) -> bool:
        return len(self.cards) == 0


@dataclass(frozen=True)
class ActionField:
    """
    The shared action field: 4 slots per player.
    Slot indices per player:
        0 = top distant
        1 = top hidden
        2 = bottom hidden
        3 = bottom distant
    """
    red_slots: tuple[ActionSlot, ActionSlot, ActionSlot, ActionSlot]
    blue_slots: tuple[ActionSlot, ActionSlot, ActionSlot, ActionSlot]

    def slots_for(self, player: PlayerId) -> tuple[ActionSlot, ...]:
        return self.red_slots if player is PlayerId.RED else self.blue_slots

    def with_slots(self, player: PlayerId,
                   slots: tuple[ActionSlot, ActionSlot, ActionSlot, ActionSlot]) -> ActionField:
        if player is PlayerId.RED:
            return ActionField(red_slots=slots, blue_slots=self.blue_slots)
        return ActionField(red_slots=self.red_slots, blue_slots=slots)


class SlotKind(Enum):
    """Classification of a slot relative to the resolver."""
    OWN_DISTANT = auto()
    OWN_HIDDEN = auto()
    OTHER_HIDDEN = auto()
    OTHER_DISTANT = auto()


DISTANT_INDICES = frozenset({0, 3})
HIDDEN_INDICES = frozenset({1, 2})


def classify_slot(resolver: PlayerId, slot_owner: PlayerId, slot_index: int) -> SlotKind:
    is_own = resolver is slot_owner
    is_distant = slot_index in DISTANT_INDICES
    if is_own:
        return SlotKind.OWN_DISTANT if is_distant else SlotKind.OWN_HIDDEN
    return SlotKind.OTHER_DISTANT if is_distant else SlotKind.OTHER_HIDDEN


# Fill order for action slots (Refresh Phase step 3, Running, Manipulation dealing)
ACTION_FILL_ORDER: tuple[int, ...] = (0, 1, 2, 3)
# 0=top distant, 1=top hidden, 2=bottom hidden, 3=bottom distant


@dataclass(frozen=True)
class WeaponSlot:
    weapon: CardId | None = None
    kill_pile: tuple[CardId, ...] = ()  # Most recent = last element
    parity: Parity | None = None        # None for normal single-slot players


@dataclass(frozen=True)
class ManipulationField:
    """Two (or three during dealing) manipulation slots for one player."""
    cards: tuple[CardId, ...] = ()  # Up to 3 cards during the dealing sub-phase


# ---------------------------------------------------------------------------
# Player state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlayerState:
    hp: int = 20
    hp_cap: int = 20
    alignment: Alignment = Alignment.GOOD
    role_def_name: str | None = None        # CardDef.name of assigned role (permanent)
    role_card_id: CardId | None = None      # The physical role card (None if discarded)
    permanent_abilities: frozenset[str] = frozenset()  # Handler keys from After Death bosses

    deck: tuple[CardId, ...] = ()
    refresh_pile: tuple[CardId, ...] = ()
    discard_pile: tuple[CardId, ...] = ()
    hand: tuple[CardId, ...] = ()
    manipulation_field: ManipulationField = field(default_factory=ManipulationField)

    equipment: tuple[CardId | None, CardId | None] = (None, None)
    weapon_slots: tuple[WeaponSlot, ...] = (WeaponSlot(),)  # 1 normally, 2 for Two-Armed Freak

    # Per-action-phase tracking
    has_eaten_this_phase: bool = False
    action_plays_made: int = 0
    devil_used_this_phase: bool = False
    sun_used_this_phase: bool = False

    # Flags
    is_dead: bool = False
    action_phase_over: bool = False


# ---------------------------------------------------------------------------
# Phase tracking
# ---------------------------------------------------------------------------

class Phase(Enum):
    SETUP = auto()
    REFRESH = auto()
    MANIPULATION = auto()
    ACTION = auto()
    GAME_OVER = auto()


class ManipChoice(Enum):
    MANIPULATE = auto()
    DUMP = auto()


# --- Phase contexts ---

@dataclass(frozen=True)
class SetupContext:
    """No sub-state needed; setup is fully automated."""
    pass


class RefreshStep(Enum):
    MOON_RECORD = auto()         # Record HP for Moon deviation cap
    SHUFFLE_REFRESH = auto()     # Shuffle refresh pile into deck
    DEAL_ALL = auto()            # Deal hand, actions, manipulation (all deterministic)
    FLIP_PRIORITY = auto()       # Flip the priority tracker
    CARDSHARP_REARRANGE = auto() # Cardsharp decision point (if applicable)
    PERIODIC_EFFECTS = auto()    # End-of-phase periodic effects
    DONE = auto()                # Transition to Manipulation


@dataclass(frozen=True)
class RefreshContext:
    step: RefreshStep = RefreshStep.MOON_RECORD
    cardsharp_player: PlayerId | None = None  # Which Cardsharp is pending decision
    moon_recorded_hp: tuple[int, int] = (0, 0)  # HP recorded for Moon deviation (RED, BLUE)


class ManipStep(Enum):
    CHOOSE = auto()            # Players choosing manipulate or dump
    SWAP_OR_DUMP = auto()      # Current player doing swaps (manipulate) or choosing fates (dump)
    FORCE_OFFER = auto()       # Current player offered chance to force (manipulate only)
    DEALING = auto()           # Automated: draw third card, shuffle
    FORCE_CARD_CHOOSE = auto() # Forcing player chooses which card to send
    DONE = auto()


@dataclass(frozen=True)
class ManipulationContext:
    step: ManipStep = ManipStep.CHOOSE
    current_player: PlayerId | None = None  # Who is currently being prompted
    red_choice: ManipChoice | None = None
    blue_choice: ManipChoice | None = None
    red_done: bool = False  # Finished swapping/dumping
    blue_done: bool = False
    red_forcing: CardId | None = None   # Equipment card flipped for forcing
    blue_forcing: CardId | None = None
    dealing_player: PlayerId | None = None  # Which player's dealing is being processed


class ActionStep(Enum):
    """Sub-steps within the Action Phase."""
    LAST_RESORT_OFFER = auto()   # Offer last resort before first play
    CHOOSE_SLOT = auto()         # Active player chooses a slot
    CONSENT_CHECK = auto()       # Other player grants/denies consent
    RESOLVING_SLOT = auto()      # Processing cards in a slot
    VOLUNTARY_DISCARD = auto()   # Between-card discard window
    RESOLVE_FROM_DECK = auto()   # No legal slots → resolve from deck
    ATTACK_CHOICE = auto()       # Player chooses fists or weapon
    EFFECT_DECISION = auto()     # Mid-resolution effect needs player input
    NEXT_TURN = auto()           # Switch to next player's turn
    ELUSIVE_CLEANUP = auto()     # End-of-phase Elusive refresh
    RUNNING_PREP = auto()        # Running sub-phase: other player draws 4
    RUNNING_DECIDE = auto()      # Running sub-phase: recycling decisions
    RUNNING_DEAL = auto()        # Running sub-phase: shuffle and deal
    GUARDS = auto()              # Calling the guards
    MUTINY = auto()              # Mutiny combat
    DONE = auto()


@dataclass(frozen=True)
class EffectContext:
    """State for a mid-resolution effect that needs player input."""
    handler: str = ""                       # Which handler to resume
    card_id: CardId = 0                     # Card that triggered the effect
    resolver: PlayerId = PlayerId.RED
    data: dict = field(default_factory=dict)  # Handler-specific state


@dataclass(frozen=True)
class ResolutionContext:
    """Tracks the resolution of a single action slot (and possibly nested resolutions)."""
    slot_owner: PlayerId = PlayerId.RED
    slot_index: int = 0
    card_queue: tuple[CardId, ...] = ()       # Remaining cards in slot to resolve
    current_card: CardId | None = None
    sub_resolution: ResolutionContext | None = None  # For Fool/Magician nested resolves
    on_resolve_done: bool = False  # True if ON_RESOLVE already fired (resuming from effect decision)


@dataclass(frozen=True)
class RunningContext:
    """Sub-state while Running last resort is in progress."""
    runner: PlayerId = PlayerId.RED
    drawn_cards: tuple[CardId, ...] = ()     # 4 cards the other player sees
    replacement_cards: tuple[CardId, ...] = ()  # Cards drawn as replacements


@dataclass(frozen=True)
class ConsentRequest:
    """Pending consent request for resolving another player's slot."""
    requester: PlayerId = PlayerId.RED
    slot_owner: PlayerId = PlayerId.BLUE
    slot_index: int = 0


@dataclass(frozen=True)
class ActionContext:
    step: ActionStep = ActionStep.LAST_RESORT_OFFER
    current_turn: PlayerId = PlayerId.RED
    plays_remaining_red: int = 3
    plays_remaining_blue: int = 3
    last_resort_offered_red: bool = False
    last_resort_offered_blue: bool = False
    last_resort_used_red: bool = False
    last_resort_used_blue: bool = False
    resolving: ResolutionContext | None = None
    running: RunningContext | None = None
    consent_request: ConsentRequest | None = None
    effect_ctx: EffectContext | None = None     # Mid-resolution effect state
    attack_target: CardId | None = None          # Enemy being fought (for ATTACK_CHOICE)
    # Track consumed plays per turn for alternation
    red_eaten_this_phase: bool = False
    blue_eaten_this_phase: bool = False


# Union type for phase context
PhaseContext = SetupContext | RefreshContext | ManipulationContext | ActionContext


# ---------------------------------------------------------------------------
# Decision points and actions
# ---------------------------------------------------------------------------

class DecisionKind(Enum):
    # Manipulation
    CHOOSE_MANIPULATE_OR_DUMP = auto()
    CHOOSE_SWAP = auto()
    CHOOSE_FORCE = auto()
    CHOOSE_DUMP_FATE = auto()
    CHOOSE_FORCE_CARD = auto()       # Which manipulation card to force-send

    # Cardsharp
    REARRANGE_ACTION_FIELD = auto()

    # Action Phase — top level
    CHOOSE_LAST_RESORT = auto()
    CHOOSE_ACTION_SLOT = auto()
    GRANT_CONSENT = auto()

    # Running
    RECYCLE_DECISION = auto()

    # Combat
    CHOOSE_ATTACK_MODE = auto()

    # Equipment management
    CHOOSE_EQUIPMENT_TO_DISCARD = auto()

    # Card-specific
    MAGICIAN_CHOOSE = auto()
    HIGH_PRIESTESS_NAME = auto()
    HIGH_PRIESTESS_EFFECT = auto()
    HERMIT_CHOOSE = auto()
    LOVERS_CHOOSE_HP = auto()
    HIEROPHANT_SPLIT = auto()
    DEVIL_DECLARE = auto()
    CHARIOT_PREVENT = auto()
    STRENGTH_DECLARE_D20 = auto()
    VOLUNTARY_DISCARD = auto()
    SALTINE_CHOICE = auto()
    FAT_SANDWICH_EAT = auto()
    SURVIVOR_EXTRA_ACTION = auto()
    POET_REFRESH_CHOICE = auto()
    TEMPERANCE_GIVE_HP = auto()
    SUN_RESOLVE_SLOT = auto()
    WORLD_ROLE_REDIRECT = auto()     # The World role: redirect kill to other's refresh
    FETCH_STICK_CHOICE = auto()      # Other player forced to wield


# ---------------------------------------------------------------------------
# Actions (what the player submits)
# ---------------------------------------------------------------------------

class ActionKind(Enum):
    # Simple choices
    SELECT_INDEX = auto()       # Choose one from a list by index
    SELECT_CARD = auto()        # Choose a specific CardId
    SELECT_SLOT = auto()        # Choose (owner, slot_index)
    SELECT_AMOUNT = auto()      # Choose a numeric amount (HP to give, etc.)
    SELECT_BOOL = auto()        # Yes/no
    SELECT_PERMUTATION = auto() # Reorder a list of CardIds
    SELECT_SWAP = auto()        # (manip_card, hand_card) pair
    SELECT_DUMP_FATES = auto()  # List of (CardId, discard|refresh) pairs
    SELECT_RECYCLE = auto()     # List of bools (per card: recycle?)
    SELECT_SPLIT = auto()       # Hierophant: two groups of CardIds
    SELECT_ATTACK = auto()      # AttackMode choice
    DECLINE = auto()            # Pass / decline optional action


class AttackMode(Enum):
    FISTS = auto()
    WEAPON = auto()
    WEAPON_LEFT = auto()   # Two-Armed Freak: odd slot
    WEAPON_RIGHT = auto()  # Two-Armed Freak: even slot


class DumpFate(Enum):
    DISCARD = auto()
    REFRESH = auto()


@dataclass(frozen=True)
class SlotRef:
    """Reference to a specific action slot."""
    owner: PlayerId
    index: int  # 0-3


@dataclass(frozen=True)
class SwapPair:
    """A swap between manipulation field and hand."""
    manip_card: CardId
    hand_card: CardId


@dataclass(frozen=True)
class DumpFateChoice:
    card: CardId
    fate: DumpFate


@dataclass(frozen=True)
class HierophantSplit:
    """How to split 6 cards into two piles for Hierophant."""
    pile_for_self: tuple[CardId, ...]
    pile_for_other: tuple[CardId, ...]


@dataclass(frozen=True)
class Action:
    """A player's response to a PendingDecision."""
    kind: ActionKind
    # Payload — exactly one of these is set based on kind
    index: int | None = None
    card_id: CardId | None = None
    slot_ref: SlotRef | None = None
    amount: int | None = None
    flag: bool | None = None
    permutation: tuple[CardId, ...] | None = None
    swap: SwapPair | None = None
    dump_fates: tuple[DumpFateChoice, ...] | None = None
    recycle_flags: tuple[bool, ...] | None = None
    split: HierophantSplit | None = None
    attack_mode: AttackMode | None = None


# ---------------------------------------------------------------------------
# Decision descriptor (what the engine offers to the player)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PendingDecision:
    player: PlayerId
    kind: DecisionKind
    legal_actions: tuple[Action, ...]
    context_description: str = ""
    # Optional hints for display
    visible_cards: tuple[CardId, ...] = ()  # Cards the player can see for this decision


# ---------------------------------------------------------------------------
# Continuation (for nested resolution)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Continuation:
    """Enough data to resume after a sub-decision completes."""
    kind: str                  # Handler to resume
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Game result
# ---------------------------------------------------------------------------

class GameResultKind(Enum):
    GOOD_COOPERATIVE_WIN = auto()  # Both Good, both Worlds dead, jointly announced
    GOOD_KILLS_EVIL = auto()       # Good player kills Evil player
    EVIL_KILLS_GOOD = auto()       # Evil player kills Good player
    EXHAUSTION = auto()            # Both lose — deck and refresh empty
    LEO_PERMANENT_DEATH = auto()   # Leo's HP cap reached 0


@dataclass(frozen=True)
class GameResult:
    kind: GameResultKind
    winner: PlayerId | None         # None for draws/exhaustion
    description: str = ""


# ---------------------------------------------------------------------------
# Fog of war — player views
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FoggedWeaponSlot:
    weapon: CardId | None = None
    kill_pile: tuple[CardId, ...] = ()
    parity: Parity | None = None


@dataclass(frozen=True)
class VisiblePlayerState:
    """Full view of the player's own state."""
    hp: int
    hp_cap: int
    alignment: Alignment
    role_def_name: str | None
    permanent_abilities: frozenset[str]

    deck_size: int              # Can't see own deck contents
    refresh_pile_size: int      # Can't see own refresh pile contents
    discard_pile_size: int
    hand: tuple[CardId, ...]    # Can see own hand
    manipulation_field: tuple[CardId, ...]

    equipment: tuple[CardId | None, CardId | None]
    weapon_slots: tuple[WeaponSlot, ...]  # Full view including kill pile

    has_eaten_this_phase: bool
    action_plays_made: int
    is_dead: bool
    action_phase_over: bool


@dataclass(frozen=True)
class FoggedPlayerState:
    """What one player can see of the other."""
    hp: int
    deck_size: int
    refresh_pile_size: int
    discard_pile_size: int
    hand_size: int
    equipment: tuple[CardId | None, CardId | None]  # Public
    weapon_slots: tuple[FoggedWeaponSlot, ...]       # Public
    is_dead: bool


@dataclass(frozen=True)
class VisibleActionField:
    """
    Action field from one player's perspective.
    Own slots: fully visible. Other's distant: visible. Other's hidden: card count only.
    """
    own_slots: tuple[ActionSlot, ActionSlot, ActionSlot, ActionSlot]
    other_distant_0: ActionSlot   # Other's slot index 0 (top distant)
    other_distant_3: ActionSlot   # Other's slot index 3 (bottom distant)
    other_hidden_1_count: int     # Number of cards in other's top hidden
    other_hidden_2_count: int     # Number of cards in other's bottom hidden


@dataclass(frozen=True)
class PlayerView:
    """Complete view of the game from one player's perspective."""
    me: PlayerId
    my_state: VisiblePlayerState
    other_state: FoggedPlayerState
    action_field: VisibleActionField

    guard_deck_size: int
    priority: PlayerId
    phase: Phase
    turn_number: int

    decision: PendingDecision | None


# ---------------------------------------------------------------------------
# Top-level game state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GameState:
    # Random state (serialized as seed + call count for reproducibility)
    rng_seed: int
    rng_counter: int  # Number of RNG calls made so far

    phase: Phase
    phase_context: PhaseContext
    priority: PlayerId
    turn_number: int
    game_result: GameResult | None

    # Per-player
    players: tuple[PlayerState, PlayerState]  # Indexed by PlayerId.value

    # Shared
    guard_deck: tuple[CardId, ...]
    action_field: ActionField

    # Card data (carried in state for self-containment)
    card_defs: dict[CardId, CardDef]       # CardId → definition
    card_states: dict[CardId, CardState]   # Only non-default entries

    # Decision & continuation
    pending: PendingDecision | None
    continuation_stack: tuple[Continuation, ...]

    # --- Accessors ---

    def player(self, pid: PlayerId) -> PlayerState:
        return self.players[pid.value]

    def with_player(self, pid: PlayerId, ps: PlayerState) -> GameState:
        players = list(self.players)
        players[pid.value] = ps
        return GameState(
            rng_seed=self.rng_seed,
            rng_counter=self.rng_counter,
            phase=self.phase,
            phase_context=self.phase_context,
            priority=self.priority,
            turn_number=self.turn_number,
            game_result=self.game_result,
            players=(players[0], players[1]),
            guard_deck=self.guard_deck,
            action_field=self.action_field,
            card_defs=self.card_defs,
            card_states=self.card_states,
            pending=self.pending,
            continuation_stack=self.continuation_stack,
        )

    def with_action_field(self, af: ActionField) -> GameState:
        return GameState(
            rng_seed=self.rng_seed,
            rng_counter=self.rng_counter,
            phase=self.phase,
            phase_context=self.phase_context,
            priority=self.priority,
            turn_number=self.turn_number,
            game_result=self.game_result,
            players=self.players,
            guard_deck=self.guard_deck,
            action_field=af,
            card_defs=self.card_defs,
            card_states=self.card_states,
            pending=self.pending,
            continuation_stack=self.continuation_stack,
        )

    def card_def(self, card_id: CardId) -> CardDef:
        return self.card_defs[card_id]

    def card_state(self, card_id: CardId) -> CardState:
        return self.card_states.get(card_id, CardState())

    def with_card_state(self, card_id: CardId, cs: CardState) -> GameState:
        new_states = dict(self.card_states)
        if cs == CardState():
            new_states.pop(card_id, None)
        else:
            new_states[card_id] = cs
        return GameState(
            rng_seed=self.rng_seed,
            rng_counter=self.rng_counter,
            phase=self.phase,
            phase_context=self.phase_context,
            priority=self.priority,
            turn_number=self.turn_number,
            game_result=self.game_result,
            players=self.players,
            guard_deck=self.guard_deck,
            action_field=self.action_field,
            card_defs=self.card_defs,
            card_states=new_states,
            pending=self.pending,
            continuation_stack=self.continuation_stack,
        )