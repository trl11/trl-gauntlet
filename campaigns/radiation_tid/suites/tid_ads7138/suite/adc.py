"""The ADS7128 under test, as this suite reaches it.

Gauntlet owns the bridge. A suite naming ``i2c`` in ``requires:`` is granted a
URL and drives it over HTTP, so nothing here opens a device node or knows what
a CP2112 is.

The transport is three calls — read a register, write a register, read the
conversion result — and the sequences built from them live in the runner.
``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DATA_CFG = 0x02
GENERAL_CFG = 0x01
GPIO_CFG = 0x07
GPI_VALUE = 0x0D
GPO_DRIVE_CFG = 0x09
GPO_VALUE = 0x0B
OPMODE_CFG = 0x04
PIN_CFG = 0x05
SEQUENCE_CFG = 0x10
SYSTEM_STATUS = 0x00

OP_READ = 0x10
OP_WRITE = 0x08

# SYSTEM_STATUS bit 7 reads 1 on a healthy part. Every other bit is an event:
# a brown-out, a CRC error on the power-up configuration, or one on incoming
# data.
STATUS_HEALTHY = 0x80
# Written to SYSTEM_STATUS to clear the brown-out flag, which is set by the
# power-up the part has already had before a run starts.
STATUS_CLEAR_BOR = 0x01

# DATA_CFG bit 7 makes the part answer a conversion read with a fixed code
# instead of a measurement, and this is that code, left-aligned in 16 bits.
FIXED_PATTERN = 0xA5A0
FIXED_PATTERN_ON = 0x80


class AdcError(RuntimeError):
    """The bridge refused a transaction, or could not be reached."""


class Adc:
    """One ADS7128 on the granted ``i2c`` capability."""

    def __init__(self, url: str, address: int, *, timeout_s: float = 10.0) -> None:
        self._address = address
        self._timeout_s = timeout_s
        self._url = url

    def read_data(self) -> int:
        """The two bytes of a conversion read, as one 16-bit word."""
        raw = self._transfer({"command": "read", "args": {"address": self._address, "length": 2}})
        if len(raw) != 2:
            raise AdcError(f"a conversion read answered {len(raw)} bytes, not 2")
        return (raw[0] << 8) | raw[1]

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
    """The part as a register file, for a run that contacts no bridge.

    It answers as the real one does for everything the runner asks of it: a
    written register reads back, the digital inputs mirror the outputs a
    channel is configured to drive, and a conversion read answers with the
    fixed code while that is switched on.
    """

    def __init__(self) -> None:
        self._registers = {SYSTEM_STATUS: STATUS_HEALTHY}

    def read_data(self) -> int:
        """The fixed code while it is switched on, and zero otherwise."""
        if self._registers.get(DATA_CFG, 0) & FIXED_PATTERN_ON:
            return FIXED_PATTERN
        return 0

    def read_register(self, register: int) -> int:
        """One register's contents."""
        if register == GPI_VALUE:
            driving = self._registers.get(PIN_CFG, 0) & self._registers.get(GPIO_CFG, 0)
            return self._registers.get(GPO_VALUE, 0) & driving
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
