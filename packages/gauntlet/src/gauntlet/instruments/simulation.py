"""What the simulated instruments share.

The mocks are deterministic: every reading is a function of the instrument's
seed and the time elapsed since it was built, so a fixed clock replays a fixed
trace.
"""

from __future__ import annotations

import random


def noise(seed: int, key: str, moment: float, amount: float) -> float:
    """Repeatable pseudo-noise, steady for a tenth of a second at a time."""
    tick = int(moment / 0.1)
    return random.Random(f"{seed}:{key}:{tick}").uniform(-amount, amount)
