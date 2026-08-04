"""Simulated thermal chamber.

Registered only when ``simulated_instruments`` names it, so capability
wiring stays exercisable without hardware while an ordinary bench shows
only the instruments really attached to it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.simulation import noise

_AMBIENT_C = 22.0

# Degrees the air moves per second while the chamber is driving.
_RATE_C_PER_S = 0.4


class MockChamber:
    """A chamber whose air temperature ramps toward its setpoint.

    While it is running with the door shut, the air travels toward the
    setpoint at a fixed rate; otherwise it falls back toward ambient. The ramp
    advances with the clock and the reported reading adds a little noise, so a
    fixed clock and a fixed seed give the same readings every time.
    """

    name = "chamber"

    def __init__(self, *, clock: Callable[[], float] = time.time, instance: str = "chamber0", seed: int = 0) -> None:
        self._actual_c = _AMBIENT_C
        self._advanced = clock()
        self._clock = clock
        self._door_open = False
        self._instance = instance
        self._lock = threading.Lock()
        self._running = False
        self._seed = seed
        self._setpoint_c = 25.0
        self._started = self._advanced

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it changed."""
        if name not in {"set_setpoint", "start", "stop"}:
            raise CommandRejected(f"chamber has no command {name!r}")
        with self._lock:
            self._advance()
            if name == "set_setpoint":
                self._setpoint_c = number_arg("chamber", args, "celsius", -40.0, 180.0)
                return {"setpoint_c": self._setpoint_c}
            self._running = name == "start"
            return {"running": self._running}

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        return [
            {
                "name": "set_setpoint",
                "label": "Set Setpoint",
                "fields": [command_field("celsius", "Setpoint", unit="C", minimum=-40.0, maximum=180.0)],
            },
            {"danger": True, "name": "start", "label": "Start", "fields": []},
            {"name": "stop", "label": "Stop", "fields": []},
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Thermal chamber with a ramping air temperature.",
            "driver": "mock",
            "kind": "chamber",
            "model": "mock-chamber",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Starting the chamber drives the air, so it gets the full width."""
        return "start"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """The air temperature as a tile, with what it is heading for below."""
        return [
            readout("actual_c", "Air", precision=2, unit="C"),
            readout("setpoint_c", "Setpoint", precision=1, unit="C"),
            readout("ambient_c", "Ambient", precision=1, role="summary", unit="C"),
            readout("door_open", "Door open", role="summary"),
            readout("running", "Running", role="summary"),
        ]

    def state(self) -> dict[str, Any]:
        """The chamber as of now, with the ramp advanced to this moment."""
        with self._lock:
            self._advance()
            moment = self._advanced - self._started
            return {
                "actual_c": round(self._actual_c + noise(self._seed, "actual_c", moment, 0.05), 2),
                "ambient_c": _AMBIENT_C,
                "door_open": self._door_open,
                "running": self._running,
                "setpoint_c": self._setpoint_c,
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _advance(self) -> None:
        """Move the air temperature toward whatever it is heading for."""
        now = self._clock()
        step = _RATE_C_PER_S * max(now - self._advanced, 0.0)
        self._advanced = now
        goal = self._setpoint_c if self._running and not self._door_open else _AMBIENT_C
        if abs(goal - self._actual_c) <= step:
            self._actual_c = goal
        elif goal > self._actual_c:
            self._actual_c += step
        else:
            self._actual_c -= step
