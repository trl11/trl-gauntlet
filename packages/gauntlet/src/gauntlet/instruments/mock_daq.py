"""Simulated data acquisition unit.

Registered by default so capability wiring is exercisable without hardware.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.simulation import noise

_ANALOG_COUNT = 8
_DIGITAL_COUNT = 2
_UNITS = ("V", "mV", "A", "C")


@dataclass
class _Analog:
    """One analog input: how its reading moves, and how it is scaled."""

    amplitude: float
    center: float
    period_s: float
    offset: float = 0.0
    range_v: float = 10.0
    unit: str = "V"


class MockDaq:
    """Eight analog inputs that drift slowly, plus two digital inputs.

    Each analog reading is a slow sine around its own centre with a little
    noise, less whatever offset the last tare recorded, clipped to the
    channel's range. Every value is a function of the seed and the time
    elapsed since the instrument was built, so a fixed clock and a fixed seed
    give the same readings every time.
    """

    name = "daq"

    def __init__(self, *, clock: Callable[[], float] = time.time, instance: str = "daq0", seed: int = 0) -> None:
        self._analog = {
            str(number): _Analog(
                amplitude=0.2 + 0.05 * number,
                center=0.5 * number,
                period_s=30.0 + 7.0 * number,
            )
            for number in range(1, _ANALOG_COUNT + 1)
        }
        self._clock = clock
        self._instance = instance
        self._lock = threading.Lock()
        self._seed = seed
        self._started = clock()

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        moment = self._clock() - self._started
        if name == "sample":
            return self._sample(moment)
        if name not in {"set_range", "tare"}:
            raise CommandRejected(f"daq has no command {name!r}")
        channel_name = str(args.get("channel", ""))
        channel = self._analog.get(channel_name)
        if channel is None:
            raise CommandRejected(f"daq has no channel {channel_name!r}")
        with self._lock:
            if name == "set_range":
                # Both arguments are read before either is stored, so a
                # rejected command leaves the channel as it was.
                range_v = number_arg("daq", args, "range_v", 0.1, 50.0)
                unit = str(args.get("unit", ""))
                if unit not in _UNITS:
                    raise CommandRejected(f"daq: 'unit' must be one of {', '.join(_UNITS)}")
                channel.range_v = range_v
                channel.unit = unit
                return {"channel": channel_name, "range_v": channel.range_v, "unit": channel.unit}
            channel.offset = round(self._raw(channel_name, channel, moment), 4)
            return {"channel": channel_name, "offset": channel.offset}

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        channels = tuple(self._analog)
        return [
            {
                "name": "set_range",
                "label": "Set Range",
                "fields": [
                    command_field("channel", "Channel", "string", choices=channels),
                    command_field("range_v", "Range", unit="V", minimum=0.1, maximum=50.0),
                    command_field("unit", "Unit", "string", choices=_UNITS),
                ],
            },
            {
                "name": "tare",
                "label": "Tare",
                "fields": [command_field("channel", "Channel", "string", choices=channels)],
            },
            {"name": "sample", "label": "Sample", "fields": []},
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Eight-channel analog acquisition with two digital inputs.",
            "driver": "mock",
            "kind": "daq",
            "model": "mock-daq",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking one scan is what an operator comes to this panel for."""
        return "sample"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """A tile per analog input, with its range and the digital lines below."""
        rows = []
        with self._lock:
            units = {name: channel.unit for name, channel in self._analog.items()}
        for name, unit in units.items():
            rows.append(readout(f"channels.{name}.value", f"CH {name}", group="Analog", precision=3, unit=unit))
        for name in units:
            rows.append(
                readout(
                    f"channels.{name}.range_v",
                    f"CH {name} range",
                    group="Analog",
                    precision=1,
                    role="summary",
                    unit="V",
                )
            )
        for number in range(1, _DIGITAL_COUNT + 1):
            rows.append(readout(f"digital.{number}", f"DI {number}", group="Digital", role="summary"))
        return rows

    def state(self) -> dict[str, Any]:
        """Every channel's configuration and its present reading."""
        moment = self._clock() - self._started
        with self._lock:
            channels = {name: self._channel_state(name, channel, moment) for name, channel in self._analog.items()}
        return {"channels": channels, "digital": _digital(moment)}

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _channel_state(self, name: str, channel: _Analog, moment: float) -> dict[str, Any]:
        value = self._raw(name, channel, moment) - channel.offset
        clipped = max(-channel.range_v, min(channel.range_v, value))
        return {
            "offset": channel.offset,
            "range_v": channel.range_v,
            "unit": channel.unit,
            "value": round(clipped, 4),
        }

    def _raw(self, name: str, channel: _Analog, moment: float) -> float:
        """The reading before the tare offset is taken off it."""
        angle = 2.0 * math.pi * moment / channel.period_s
        return channel.center + channel.amplitude * math.sin(angle) + noise(self._seed, name, moment, 0.004)

    def _sample(self, moment: float) -> dict[str, Any]:
        """One scan of every channel, as an acquisition unit would return it."""
        with self._lock:
            analog = {
                name: self._channel_state(name, channel, moment)["value"] for name, channel in self._analog.items()
            }
        return {"analog": analog, "digital": _digital(moment)}


def _digital(moment: float) -> dict[str, bool]:
    """Two digital lines, each toggling on its own fixed period."""
    return {str(number): (moment / (11.0 + 5.0 * number)) % 2.0 < 1.0 for number in range(1, _DIGITAL_COUNT + 1)}
