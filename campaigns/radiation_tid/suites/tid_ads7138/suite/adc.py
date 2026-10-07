"""The ADS7138 under test, as this suite reaches it.

Gauntlet owns the bridge. A suite naming ``i2c`` in ``requires:`` is granted a
URL and drives it over HTTP, so nothing here opens a device node or knows what
a CP2112 is. ``urllib`` rather than a client library, because the SDK depends
on pydantic and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

SYSTEM_STATUS = 0x00
DATA_CFG = 0x02
OPMODE_CFG = 0x04
PIN_CFG = 0x05
GPIO_CFG = 0x07
GPO_DRIVE_CFG = 0x09
GPO_VALUE = 0x0B
GPI_VALUE = 0x0D
SEQUENCE_CFG = 0x10
CHANNEL_SEL = 0x11

OP_READ = 0x10
OP_WRITE = 0x08

# SYSTEM_STATUS bit 7 reads 1 on a healthy part. Every other bit is an event:
# a brown-out, a CRC error on the power-up configuration, or one on incoming
# data.
STATUS_HEALTHY = 0x80
# Written to SYSTEM_STATUS to clear the brown-out flag, which is set by the
# power-up the part has already had before a run starts.
STATUS_CLEAR_BOR = 0x01

FULL_SCALE = 4096


class AdcError(RuntimeError):
    """The bridge refused a transaction, or could not be reached."""


class Adc:
    """One ADS7138 on the granted ``i2c`` capability."""

    def __init__(self, url: str, address: int, *, timeout_s: float = 10.0) -> None:
        self._address = address
        self._timeout_s = timeout_s
        self._url = url

    def convert(self, channel: int) -> int:
        """One 12-bit conversion of an analog channel, in manual mode."""
        self.write_register(CHANNEL_SEL, channel)
        # The frame after a channel change can still carry the previous
        # channel's result, so it is read and dropped.
        self._read_word()
        return self._read_word() >> 4

    def read_register(self, register: int) -> int:
        """One register's contents."""
        raw = self._transfer(
            {
                "command": "write_read",
                "args": {
                    "address": self._address,
                    "data": f"{OP_READ:02x}{register:02x}",
                    "read_length": 1,
                },
            }
        )
        if len(raw) != 1:
            raise AdcError(f"register 0x{register:02x} answered {len(raw)} bytes, not 1")
        return raw[0]

    def write_register(self, register: int, value: int) -> None:
        """Set one register."""
        self._transfer(
            {
                "command": "write",
                "args": {"address": self._address, "data": f"{OP_WRITE:02x}{register:02x}{value:02x}"},
            }
        )

    def _read_word(self) -> int:
        raw = self._transfer({"command": "read", "args": {"address": self._address, "length": 2}})
        if len(raw) != 2:
            raise AdcError(f"a conversion read answered {len(raw)} bytes, not 2")
        return (raw[0] << 8) | raw[1]

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
            raise AdcError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise AdcError(f"{body['command']}: {exc}") from exc
        return bytes.fromhex(str(payload.get("data_hex") or ""))


class MockAdc:
    """The part on its board as a register file, for a run that contacts no bridge.

    The inputs read as the board wires them, an output reads back what it
    drives, and every analog channel converts to ``code``.
    """

    def __init__(self, *, high: int, low: int, code: int) -> None:
        self._code = code
        self._high = high
        self._low = low
        self._registers = {SYSTEM_STATUS: STATUS_HEALTHY}

    def convert(self, channel: int) -> int:
        """Every channel converts to the same code."""
        self._registers[CHANNEL_SEL] = channel
        return self._code

    def read_register(self, register: int) -> int:
        """One register's contents."""
        if register == GPI_VALUE:
            outputs = self._registers.get(GPIO_CFG, 0)
            return (self._registers.get(GPO_VALUE, 0) & outputs) | ((1 << self._high) & ~outputs)
        return self._registers.get(register, 0)

    def write_register(self, register: int, value: int) -> None:
        """Set one register."""
        if register == SYSTEM_STATUS:
            self._registers[SYSTEM_STATUS] = STATUS_HEALTHY
            return
        self._registers[register] = value


def _detail(error: urllib.error.HTTPError) -> str:
    """What the bridge said, for a transaction it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "i2c is unavailable: ..." and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"
