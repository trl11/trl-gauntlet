"""The TMP100 under test, as this suite reaches it.

Gauntlet owns the bridge. A suite naming ``i2c`` in ``requires:`` is granted a
URL and drives it over HTTP, so nothing here opens a device node or knows what
a CP2112 is. ``urllib`` rather than a client library, because the SDK depends
on pydantic and pyyaml and a suite may not add to that.

Every register is reached by writing its pointer and reading it back. The
configuration register is one byte; the other three are two.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

TEMPERATURE = 0x00
CONFIGURATION = 0x01
T_LOW = 0x02
T_HIGH = 0x03

# R1 and R0 set: 12-bit conversions, 0.0625 °C a step, up to 600 ms each.
RESOLUTION_12_BIT = 0x60
CONVERSION_S = 0.6

# The comparator limits out of reset, 75 °C and 80 °C. The TMP100 has no
# ALERT pin, so they drive nothing and serve only as two known words to read
# back.
T_LOW_DEFAULT = 0x4B00
T_HIGH_DEFAULT = 0x5000


class SensorError(RuntimeError):
    """The bridge refused a transaction, or could not be reached."""


def celsius(raw: int) -> float:
    """A temperature register's 16 bits in degrees: 12 left-aligned bits, signed."""
    if raw & 0x8000:
        raw -= 0x10000
    return (raw >> 4) * 0.0625


def width(pointer: int) -> int:
    """How many bytes the register at this pointer holds."""
    return 1 if pointer == CONFIGURATION else 2


class Tmp100:
    """One TMP100 on the granted ``i2c`` capability."""

    def __init__(self, url: str, address: int, *, timeout_s: float = 10.0) -> None:
        self._address = address
        self._timeout_s = timeout_s
        self._url = url

    def read_register(self, pointer: int) -> int:
        """One register's contents, most significant byte first."""
        length = width(pointer)
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
        data = value.to_bytes(width(pointer), "big").hex()
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


class MockTmp100:
    """The part as a register file reading a steady 25 °C, for a run that contacts no bridge."""

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
