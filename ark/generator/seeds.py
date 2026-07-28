"""
Centralizes how Ark's generator uses randomness.

Every seeded decision the generator makes flows through one explicit
random.Random instance and the small set of helpers below — never
Python's global `random` module state (ambient randomness is exactly what
would break the seed -> identical-estate guarantee).

Determinism gotcha this module exists partly to guard against: Python's
built-in `set` does NOT iterate in a stable order across process runs for
string elements (hash randomization, PYTHONHASHSEED, is on by default).
Every function here takes/returns *lists* (or samples from a list), never
a set, so that "the same seed produces the same estate" holds regardless
of PYTHONHASHSEED — callers must not pass sets into these helpers either.
"""

from __future__ import annotations

import random


def make_rng(seed: int) -> random.Random:
    return random.Random(seed)


def draw_nouns(rng: random.Random, vocabulary: list[str], k: int) -> list[str]:
    """Deterministically draw k names from vocabulary, given rng.

    If k <= len(vocabulary), returns k distinct entries (a seeded shuffle,
    not a random.sample, so the same rng-state consumption pattern is used
    regardless of k — keeping this function's determinism easy to reason
    about). If k > len(vocabulary), cycles back through a second pass of
    the same shuffled order with a numeric suffix (vocabulary exhaustion
    fallback) rather than raising — generation should degrade gracefully
    for large estate sizes, not fail.
    """
    pool = list(vocabulary)
    rng.shuffle(pool)

    result: list[str] = []
    cycle = 0
    idx = 0
    while len(result) < k:
        if idx >= len(pool):
            idx = 0
            cycle += 1
        noun = pool[idx] if cycle == 0 else f"{pool[idx]}{cycle + 1}"
        result.append(noun)
        idx += 1
    return result


def sample_subset(rng: random.Random, population: list[str], density: float) -> list[str]:
    """Pick a fan-out-sized subset of population, size driven by density
    in [0.0, 1.0]. Returns [] if population is empty; otherwise always at
    least 1 and at most len(population) items. `population` must be a
    list (stable order), never a set.
    """
    if not population:
        return []
    k = max(1, min(len(population), round(density * len(population))))
    return rng.sample(population, k)


def decide(rng: random.Random, probability: float) -> bool:
    """A single Bernoulli draw: True with probability `probability`."""
    return rng.random() < probability
