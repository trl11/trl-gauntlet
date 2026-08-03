"""Pieces every mock instrument is built from.

The three simulated instruments describe their commands the same way and read
their arguments the same way, so those two jobs live here rather than once per
provider.
"""

from __future__ import annotations

import random
from typing import Any

from gauntlet.capabilities.registry import CommandRejected


def command_field(
    name: str,
    label: str,
    kind: str = "number",
    *,
    choices: tuple[str, ...] = (),
    maximum: float | None = None,
    minimum: float | None = None,
    unit: str = "",
) -> dict[str, Any]:
    """One argument a command takes, described for the operator UI."""
    return {
        "name": name,
        "label": label,
        "type": kind,
        "unit": unit,
        "min": minimum,
        "max": maximum,
        "choices": list(choices),
    }


def readout(
    key: str,
    label: str,
    *,
    group: str = "",
    precision: int | None = None,
    role: str = "headline",
    unit: str = "",
) -> dict[str, Any]:
    """One state value the operator UI draws, described for it.

    ``key`` is a dotted path into the provider's ``state()``.
    """
    return {
        "group": group,
        "key": key,
        "label": label,
        "precision": precision,
        "role": role,
        "unit": unit,
    }


def noise(seed: int, key: str, moment: float, amount: float) -> float:
    """Repeatable pseudo-noise, steady for a tenth of a second at a time."""
    tick = int(moment / 0.1)
    return random.Random(f"{seed}:{key}:{tick}").uniform(-amount, amount)


def number_arg(instrument: str, args: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
    """One numeric argument, rejected when it is missing or out of range."""
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejected(f"{instrument}: {key!r} must be a number")
    if not minimum <= value <= maximum:
        raise CommandRejected(f"{instrument}: {key!r} must be between {minimum} and {maximum}")
    return float(value)
