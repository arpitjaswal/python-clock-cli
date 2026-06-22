"""Wake Challenge engine — the 'prove you're awake' dismiss gate.

Pure generators + verifier so they're unit-testable. Randomness is seeded by
caller-supplied ints (not time/os) to keep it deterministic under test; the
live clock seeds from os.urandom.
"""
from __future__ import annotations

import operator
import random

_OPS = {"+": operator.add, "-": operator.sub, "×": operator.mul}


class Challenge:
    """A single posed challenge: a prompt to show and an expected answer."""

    def __init__(self, prompt: str, answer: str):
        self.prompt = prompt
        self.answer = answer

    def check(self, given: str) -> bool:
        return given.strip() == self.answer.strip()


def _digits_for_level(level: int) -> int:
    """Operand magnitude grows with escalation level (0-based)."""
    return 10 ** (1 + min(level, 4))  # 10, 100, 1000, ... capped


def make_math(rng: random.Random, level: int = 0) -> Challenge:
    bound = _digits_for_level(level)
    a, b = rng.randrange(bound), rng.randrange(bound)
    sym = rng.choice(list(_OPS))
    if sym == "-" and b > a:
        a, b = b, a  # keep it non-negative
    return Challenge(f"{a} {sym} {b}", str(_OPS[sym](a, b)))


_PHRASES = [
    "the early bird gets the worm",
    "rise and shine sleepyhead",
    "carpe diem seize the day",
    "good morning starshine",
    "time to make it count",
]


def make_typing(rng: random.Random, level: int = 0) -> Challenge:
    # Escalate by requiring more phrases chained together.
    n = 1 + min(level, 2)
    phrase = " / ".join(rng.choice(_PHRASES) for _ in range(n))
    return Challenge(f'type exactly:  "{phrase}"', phrase)


def make_sequence(rng: random.Random, level: int = 0) -> Challenge:
    length = 4 + min(level, 6)
    seq = "".join(str(rng.randrange(10)) for _ in range(length))
    return Challenge(f"repeat this sequence:  {seq}", seq)


_MAKERS = {"math": make_math, "typing": make_typing, "sequence": make_sequence}


def make_challenge(kind: str, rng: random.Random, level: int = 0) -> Challenge:
    maker = _MAKERS.get(kind, make_math)
    return maker(rng, level)
