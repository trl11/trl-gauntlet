"""Helpers a provider uses to declare its commands and readouts.

:class:`~gauntlet.capabilities.registry.CommandableCapability` and
:class:`~gauntlet.capabilities.registry.PresentableCapability` return plain
dictionaries. These build them, so every provider describes itself in the same
shape and the operator UI can stay generic. ``number_arg`` is the other half of
``command_field``: it reads back an argument the field declared.
"""

from __future__ import annotations

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


def number_arg(instrument: str, args: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
    """One numeric argument, rejected when it is missing or out of range."""
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejected(f"{instrument}: {key!r} must be a number")
    if not minimum <= value <= maximum:
        raise CommandRejected(f"{instrument}: {key!r} must be between {minimum} and {maximum}")
    return float(value)


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
