"""Simulated instruments.

Registered by default so capability wiring is exercisable without hardware.
"""

from __future__ import annotations

import threading
from typing import Any


class MockInstrument:
    """An in-memory instrument that accepts any command and records state.

    Reads return the last written state plus derived measured values.
    """

    def __init__(self, name: str, *, instance: str = "", state: dict[str, Any] | None = None) -> None:
        self._name = name
        self._instance = instance or f"{name}0"
        self._state: dict[str, Any] = {"output": False, "voltage_v": 0.0, "current_a": 0.0}
        if state:
            self._state.update(state)
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return True

    def instance_id(self) -> str:
        return self._instance

    def describe(self) -> dict[str, str]:
        return {"driver": "mock", "model": f"mock-{self._name}"}

    def read(self) -> dict[str, Any]:
        """Current instrument state."""
        with self._lock:
            state = dict(self._state)
        if state.get("output"):
            # Measured values model a fixed resistive load.
            state["measured_v"] = float(state.get("voltage_v", 0.0))
            state["measured_a"] = round(float(state.get("voltage_v", 0.0)) * 0.1, 3)
        else:
            state["measured_v"] = 0.0
            state["measured_a"] = 0.0
        return state

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply settings and return the resulting state."""
        with self._lock:
            self._state.update(values)
        return self.read()
