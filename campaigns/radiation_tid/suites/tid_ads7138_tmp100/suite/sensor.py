"""The temperature sensor under test, as this suite reaches it.

Gauntlet owns the bridge. A suite naming ``i2c`` in ``requires:`` is granted a
URL and drives it over HTTP, so nothing here opens a device node or knows what
a CP2112 is. ``urllib`` rather than a client library, because the SDK depends
on pydantic and pyyaml and a suite may not add to that.

The TMP100 and TMP112 share a register map: the same four pointers, the same
12-bit left-aligned temperature, the same reset limits. They differ in the
configuration register, which ``PARTS`` describes per part.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

TEMPERATURE = 0x00
CONFIGURATION = 0x01
T_LOW = 0x02
T_HIGH = 0x03

# The comparator limits out of reset, 75 °C and 80 °C. Nothing on the bench
# watches an ALERT pin, so they serve only as two known words to read back.
T_LOW_DEFAULT = 0x4B00
T_HIGH_DEFAULT = 0x5000


@dataclass(frozen=True)
class Part:
    """Where one part's configuration register departs from the shared map."""

    addresses: range
    configuration: int
    configuration_bytes: int
    configuration_stored: int
    conversion_s: float


PARTS = {
    # One byte. R1 and R0 set for 12-bit conversions, up to 600 ms each. Bit 7
    # is the one-shot request, not a stored setting.
    "tmp100": Part(range(0x48, 0x50), 0x60, 1, 0x7F, 0.6),
    # Two bytes, written as the reset value: R1 and R0 are fixed at 12 bits,
    # and 4 Hz conversions of up to 35 ms each. Bit 15 is the one-shot request
    # and bit 5 the live ALERT state, so neither is a stored setting.
    "tmp112": Part(range(0x48, 0x4C), 0x60A0, 2, 0x7FDF, 0.035),
}


class SensorError(RuntimeError):
    """The bridge refused a transaction, or could not be reached."""


def celsius(raw: int) -> float:
    """A temperature register's 16 bits in degrees: 12 left-aligned bits, signed."""
    if raw & 0x8000:
        raw -= 0x10000
    return (raw >> 4) * 0.0625


def width(part: Part, pointer: int) -> int:
    """How many bytes the register at this pointer holds."""
    return part.configuration_bytes if pointer == CONFIGURATION else 2


class Sensor:
    """One sensor on the granted ``i2c`` capability."""

    def __init__(self, url: str, address: int, part: Part, *, timeout_s: float = 10.0) -> None:
        self._address = address
        self._part = part
        self._timeout_s = timeout_s
        self._url = url

    def read_register(self, pointer: int) -> int:
        """One register's contents, most significant byte first."""
        length = width(self._part, pointer)
        raw = self._transfer(
            {
                "command": "write_read",
                "args": {"address": self._address, "data": f"{pointer:02x}", "read_length": length},
            }
        )
        if len(raw) != length:
            raise SensorError(f"register 0x{pointer:02x} answered {len(raw)} bytes, not {length}")
        return int.from_bytes(raw, "big")

    def write_register(self, pointer: int, value: int) -> None:
        """Set one register."""
        data = value.to_bytes(width(self._part, pointer), "big").hex()
        self._transfer({"command": "write", "args": {"address": self._address, "data": f"{pointer:02x}{data}"}})

    def _transfer(self, body: dict[str, Any]) -> bytes:
        """Run one transaction and return the bytes it read back."""
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise SensorError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise SensorError(f"{body['command']}: {exc}") from exc
        return bytes.fromhex(str(payload.get("data_hex") or ""))


class MockSensor:
    """Either part as a register file reading a steady 25 °C, for a run that contacts no bridge."""

    def __init__(self) -> None:
        self._registers = {TEMPERATURE: 0x1900, CONFIGURATION: 0x00, T_LOW: T_LOW_DEFAULT, T_HIGH: T_HIGH_DEFAULT}

    def read_register(self, pointer: int) -> int:
        """One register's contents."""
        return self._registers[pointer]

    def write_register(self, pointer: int, value: int) -> None:
        """Set one register. The temperature register is read-only, as on the part."""
        if pointer != TEMPERATURE:
            self._registers[pointer] = value


def _detail(error: urllib.error.HTTPError) -> str:
    """What the bridge said, for a transaction it explained."""
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"
