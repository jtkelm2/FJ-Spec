"""
Fools' Journey — Executable Spec
RNG management: deterministic, serializable random state.

We track (seed, counter) so that given the same seed and the same sequence
of calls, we always get the same results.  Every function that consumes
randomness returns a new (rng, result) pair — no hidden mutation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RngState:
    """Immutable snapshot of RNG state."""
    seed: int
    counter: int  # how many draws have been consumed

    def _make_rng(self) -> random.Random:
        """Reconstruct a Random object at the correct position."""
        rng = random.Random(self.seed)
        # Fast-forward by consuming `counter` values
        for _ in range(self.counter):
            rng.random()
        return rng


def rng_create(seed: int) -> RngState:
    return RngState(seed=seed, counter=0)


def rng_randint(state: RngState, lo: int, hi: int) -> tuple[RngState, int]:
    """Draw a random integer in [lo, hi] inclusive."""
    rng = state._make_rng()
    # randint consumes a variable number of internal calls depending on range,
    # but for our purposes we count it as 1 logical draw.
    result = rng.randint(lo, hi)
    return RngState(seed=state.seed, counter=state.counter + 1), result


def rng_shuffle(state: RngState, items: list) -> tuple[RngState, list]:
    """Shuffle a list, returning new RngState and shuffled copy."""
    rng = state._make_rng()
    shuffled = list(items)
    rng.shuffle(shuffled)
    return RngState(seed=state.seed, counter=state.counter + 1), shuffled


def rng_choice(state: RngState, items: list) -> tuple[RngState, object]:
    """Pick a random element from a non-empty list."""
    rng = state._make_rng()
    result = rng.choice(items)
    return RngState(seed=state.seed, counter=state.counter + 1), result


def rng_d20(state: RngState) -> tuple[RngState, int]:
    """Roll a d20 (1-20)."""
    return rng_randint(state, 1, 20)


def rng_d10(state: RngState) -> tuple[RngState, int]:
    """Roll a d10 (1-10)."""
    return rng_randint(state, 1, 10)


def rng_d4(state: RngState) -> tuple[RngState, int]:
    """Roll a d4 (1-4)."""
    return rng_randint(state, 1, 4)
