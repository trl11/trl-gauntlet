"""Simulated bench power supply.

Registered by default so capability wiring is exercisable without hardware.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gauntlet.capabilities.mock_instrument import command_field, noise, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected

# Amps the fixed resistive load draws per volt on the rail.
_LOAD_SIEMENS = 0.11

# Output resistance, which is what makes the rail sag as current rises.
_SOURCE_OHMS = 0.11

_SETPOINTS = {"1": 12.0, "2": 5.0}


@dataclass
class _Channel:
    """Settings an operator holds for one output channel."""

    voltage_setpoint: float
    current_limit: float = 2.0
    output_enabled: bool = False


class MockPsu:
    """Two-channel supply whose readback follows its setpoints.

    While a channel's output is on, its readback voltage sags under the load
    and both readings carry a little noise. Everything is a function of the
    seed and the time elapsed since the instrument was built, so a fixed clock
    and a fixed seed give the same readings every time.
    """

    name = "psu"

    def __init__(self, *, clock: Callable[[], float] = time.time, instance: str = "psu0", seed: int = 0) -> None:
        self._channels = {name: _Channel(voltage_setpoint=volts) for name, volts in _SETPOINTS.items()}
        self._clock = clock
        self._instance = instance
        self._lock = threading.Lock()
        self._seed = seed
        self._started = clock()

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it changed."""
        if name not in {"set_current_limit", "set_output", "set_voltage"}:
            raise CommandRejected(f"psu has no command {name!r}")
        channel_name = str(args.get("channel", ""))
        channel = self._channels.get(channel_name)
        if channel is None:
            raise CommandRejected(f"psu has no channel {channel_name!r}")
        with self._lock:
            if name == "set_voltage":
                channel.voltage_setpoint = number_arg("psu", args, "voltage", 0.0, 30.0)
                return {"channel": channel_name, "voltage_setpoint": channel.voltage_setpoint}
            if name == "set_current_limit":
                channel.current_limit = number_arg("psu", args, "current", 0.0, 5.0)
                return {"channel": channel_name, "current_limit": channel.current_limit}
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                raise CommandRejected("psu: 'enabled' must be true or false")
            channel.output_enabled = enabled
            return {"channel": channel_name, "output_enabled": channel.output_enabled}

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        channels = tuple(self._channels)
        return [
            {
                "name": "set_voltage",
                "label": "Set Voltage",
                "fields": [
                    command_field("channel", "Channel", "string", choices=channels),
                    command_field("voltage", "Voltage", unit="V", minimum=0.0, maximum=30.0),
                ],
            },
            {
                "name": "set_current_limit",
                "label": "Set Current Limit",
                "fields": [
                    command_field("channel", "Channel", "string", choices=channels),
                    command_field("current", "Current", unit="A", minimum=0.0, maximum=5.0),
                ],
            },
            {
                "danger": True,
                "name": "set_output",
                "label": "Set Output",
                "fields": [
                    command_field("channel", "Channel", "string", choices=channels),
                    command_field("enabled", "Enabled", "boolean"),
                ],
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Two-channel bench supply with readback and current limiting.",
            "driver": "mock",
            "kind": "psu",
            "model": "mock-psu",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Enabling an output energises the rail, so it gets the full width."""
        return "set_output"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """Readback as tiles per channel, with the setpoints beneath them."""
        rows = []
        for name in self._channels:
            group = f"Channel {name}"
            rows += [
                readout(f"channels.{name}.voltage", "Voltage", group=group, precision=2, unit="V"),
                readout(f"channels.{name}.current", "Current", group=group, precision=3, unit="A"),
                readout(f"channels.{name}.power", "Power", group=group, precision=2, unit="W"),
                readout(
                    f"channels.{name}.voltage_setpoint",
                    "Set V",
                    group=group,
                    precision=1,
                    role="summary",
                    unit="V",
                ),
                readout(
                    f"channels.{name}.current_limit",
                    "Limit I",
                    group=group,
                    precision=1,
                    role="summary",
                    unit="A",
                ),
                readout(f"channels.{name}.output_enabled", "Output", group=group, role="summary"),
            ]
        return rows

    def state(self) -> dict[str, Any]:
        """Every channel's settings and its readback, as of now."""
        moment = self._clock() - self._started
        with self._lock:
            channels = {name: self._channel_state(name, channel, moment) for name, channel in self._channels.items()}
        return {"channels": channels}

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _channel_state(self, name: str, channel: _Channel, moment: float) -> dict[str, Any]:
        voltage, current = 0.0, 0.0
        if channel.output_enabled:
            current = min(channel.voltage_setpoint * _LOAD_SIEMENS, channel.current_limit)
            voltage = channel.voltage_setpoint - current * _SOURCE_OHMS
            voltage = max(voltage + noise(self._seed, f"{name}.voltage", moment, 0.004), 0.0)
            current = max(current + noise(self._seed, f"{name}.current", moment, 0.003), 0.0)
        return {
            "current": round(current, 3),
            "current_limit": channel.current_limit,
            "output_enabled": channel.output_enabled,
            "power": round(voltage * current, 3),
            "voltage": round(voltage, 3),
            "voltage_setpoint": channel.voltage_setpoint,
        }
