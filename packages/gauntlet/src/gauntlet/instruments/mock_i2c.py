"""Simulated I2C bridge.

Registered only when ``simulated_instruments`` names it, so capability
wiring stays exercisable without hardware while an ordinary bench shows
only the instruments really attached to it. There is one simulated device on
the bus, at 0x48, that answers a read with a temperature that drifts a
little each time — enough to exercise a suite that reads a sensor without
claiming to model any real one.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.simulation import noise

_SIM_ADDRESS = 0x48
_SIM_TEMPERATURE_C = 24.0


def _to_hex(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)


class MockI2c:
    """Bridge with one simulated device on the bus, at ``_SIM_ADDRESS``.

    A read of it returns two bytes carrying the temperature in
    hundredths of a degree, big-endian, the shape a real sensor would use;
    a read of any other address is refused, the way a real bus refuses one
    with nothing answering at it.
    """

    name = "i2c"

    def __init__(self, *, clock: Callable[[], float] = time.time, instance: str = "i2c0", seed: int = 0) -> None:
        self._clock = clock
        self._instance = instance
        self._last: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._seed = seed
        self._started = clock()

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it moved."""
        if name not in {"read", "write", "write_read"}:
            raise CommandRejected(f"i2c has no command {name!r}")
        address = int(number_arg("i2c", args, "address", 0, 0x7F))
        with self._lock:
            if name == "write":
                data = _parse_hex(args)
                result = {"address": address, "data_hex": _to_hex(data), "direction": "write", "length": len(data)}
            else:
                length = int(number_arg("i2c", args, "length", 1, 512))
                read = self._read(address, length)
                direction = "read" if name == "read" else "write_read"
                result = {"address": address, "data_hex": _to_hex(read), "direction": direction, "length": length}
            self._last = result
            return result

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        address_field = command_field("address", "Address (7-bit)", minimum=0, maximum=0x7F)
        length_field = command_field("length", "Length", minimum=1, maximum=512)
        data_field = command_field("data", "Data (hex)", "string")
        return [
            {"name": "write", "label": "Write", "fields": [address_field, data_field]},
            {"name": "read", "label": "Read", "fields": [address_field, length_field]},
            {
                "name": "write_read",
                "label": "Write then Read",
                "fields": [address_field, data_field, length_field],
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "USB-to-I2C bridge for driving I2C/SMBus devices under test.",
            "driver": "mock",
            "kind": "i2c",
            "model": "mock-i2c",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """A read is what an operator comes to this panel to try."""
        return "read"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """The last transaction's address, direction and bytes."""
        return [
            readout("address", "Address", role="summary"),
            readout("direction", "Direction", role="summary"),
            readout("data_hex", "Data", role="headline"),
            readout("length", "Length", role="summary", unit="B"),
        ]

    def state(self) -> dict[str, Any]:
        """The last transaction this bridge carried, or an empty one."""
        with self._lock:
            if not self._last:
                return {"address": None, "data_hex": None, "direction": None, "length": None}
            return dict(self._last)

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _read(self, address: int, length: int) -> bytes:
        if address != _SIM_ADDRESS:
            raise CommandRejected(f"i2c: nothing answering at address 0x{address:02x}")
        moment = self._clock() - self._started
        centidegrees = round((_SIM_TEMPERATURE_C + noise(self._seed, "temperature", moment, 0.2)) * 100)
        data = centidegrees.to_bytes(2, "big", signed=True)
        return (data * ((length // 2) + 1))[:length]


def _parse_hex(args: dict[str, Any]) -> bytes:
    text = str(args.get("data", "")).strip().replace(" ", "").replace(":", "")
    if not text:
        return b""
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise CommandRejected(f"i2c: 'data' is not valid hex: {exc}") from exc
