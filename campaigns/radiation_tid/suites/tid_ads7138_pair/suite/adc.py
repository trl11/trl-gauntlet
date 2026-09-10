"""One ADS7138 as this suite reaches it, and a simulation of a pair of them.

Gauntlet owns both bridges. A suite naming ``i2c.dut`` and ``i2c.ref`` in
``requires:`` is granted a URL for each and drives them over HTTP, so nothing
here opens a device node or knows what a CP2112 is.

The transport is three calls — read a register, write a register, read the
conversion result — and the sequences built from them live in
:mod:`suite.part`. ``urllib`` rather than a client library, because the SDK
depends on pydantic and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from typing import Any

CHANNEL_SEL = 0x11
DATA_CFG = 0x02
GENERAL_CFG = 0x01
GPIO_CFG = 0x07
GPI_VALUE = 0x0D
GPO_DRIVE_CFG = 0x09
GPO_VALUE = 0x0B
OPMODE_CFG = 0x04
OSR_CFG = 0x03
PIN_CFG = 0x05
SEQUENCE_CFG = 0x10
SYSTEM_STATUS = 0x00

OP_READ = 0x10
OP_WRITE = 0x08

# SYSTEM_STATUS bit 7 reads 1 on a part that is answering at all.
STATUS_ALIVE = 0x80
# Of the rest, only three are faults: the brown-out flag and the two CRC
# errors. The others report what the part is doing rather than what has gone
# wrong — an oversampled conversion having finished sets one of them, and it
# stays set — so testing the register for equality with 0x80 would call
# ordinary operation a failure.
STATUS_FAULTS = 0x07
STATUS_FAULT_NAMES = {0x01: "brown-out", 0x02: "a CRC error in its fuses", 0x04: "a CRC error on incoming data"}
# Written to SYSTEM_STATUS to clear the brown-out flag, which is set by the
# power-up the part has already had before a run starts.
STATUS_CLEAR_BOR = 0x01

# GENERAL_CFG bit 1 starts the part's own offset calibration and reads back as
# 1 until it has finished, which is how long it took is measured.
CAL_BIT = 0x02

# A conversion result is twelve bits left-aligned in the sixteen a read
# returns.
CODE_SHIFT = 4
CODE_MAX = 0xFFF


class AdcError(RuntimeError):
    """The bridge refused a transaction, or could not be reached."""


class Adc:
    """One ADS7138 on one granted ``i2c`` capability."""

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


class MockBench:
    """The eight wires between the two parts, for a run that contacts nothing.

    A wire is indexed by the channel of the part under test it lands on, and
    the reference part reaches the same wire through the channel map, so the
    simulation crosses the pair exactly as the harness does. What a driven
    wire settles at is the levels a real push-pull output holds, which is what
    lets the same thresholds be exercised without a bench.
    """

    def __init__(self, channel_map: list[int], *, vref_v: float = 3.3, seed: int = 0) -> None:
        self._channel_map = channel_map
        self._noise = random.Random(seed)
        self._vref_v = vref_v
        self._volts = [0.0] * 8

    def drive(self, side: str, byte: int, driving: int) -> None:
        """Put one part's outputs on the wires it is driving."""
        for channel in range(8):
            if not driving & (1 << channel):
                continue
            high = bool(byte & (1 << channel))
            self._volts[self._wire(side, channel)] = 3.28 if high else 0.017

    def level(self, side: str, channel: int) -> float:
        """What one part's channel sees, with a little noise on top."""
        volts = self._volts[self._wire(side, channel)]
        return volts + self._noise.gauss(0.0, 0.0009)

    def code(self, side: str, channel: int, oversampling: int) -> int:
        """One conversion, averaged the way the part's own oversampling does."""
        samples = [self.level(side, channel) for _ in range(oversampling)]
        volts = sum(samples) / len(samples)
        return max(0, min(CODE_MAX, round(volts / self._vref_v * CODE_MAX)))

    def _wire(self, side: str, channel: int) -> int:
        """The wire one part's channel is on, indexed by the DUT's numbering."""
        if side == "dut":
            return channel
        return self._channel_map.index(channel)


class MockAdc:
    """The part as a register file on a simulated bench.

    It answers as the real one does for everything the runner asks of it: a
    written register reads back, a driven output reaches the other part
    through :class:`MockBench`, a conversion read answers with the level on
    the wire, and oversampling quietens it.
    """

    def __init__(self, bench: MockBench, side: str) -> None:
        self._bench = bench
        self._registers = {SYSTEM_STATUS: STATUS_ALIVE}
        self._side = side

    def read_data(self) -> int:
        """A conversion of whichever channel is selected."""
        channel = self._registers.get(CHANNEL_SEL, 0) & 0x07
        if self._registers.get(PIN_CFG, 0) & (1 << channel):
            # The channel is a GPIO rather than an analog input, so there is
            # nothing for the converter to look at.
            return 0
        oversampling = 1 << (self._registers.get(OSR_CFG, 0) & 0x07)
        return self._bench.code(self._side, channel, oversampling) << CODE_SHIFT

    def read_register(self, register: int) -> int:
        """One register's contents."""
        if register == GPI_VALUE:
            return self._inputs()
        return self._registers.get(register, 0)

    def write_register(self, register: int, value: int) -> None:
        """Set one register."""
        if register == SYSTEM_STATUS:
            self._registers[SYSTEM_STATUS] = STATUS_ALIVE
            return
        if register == GENERAL_CFG:
            # Calibration finishes before the bit is read back, as it does on
            # a part that takes a few milliseconds over it.
            self._registers[GENERAL_CFG] = value & ~CAL_BIT
            return
        self._registers[register] = value
        if register in (GPO_VALUE, PIN_CFG, GPIO_CFG):
            self._drive()

    def _drive(self) -> None:
        """Put the outputs this part is configured to drive on the wires."""
        driving = self._registers.get(PIN_CFG, 0) & self._registers.get(GPIO_CFG, 0)
        self._bench.drive(self._side, self._registers.get(GPO_VALUE, 0), driving)

    def _inputs(self) -> int:
        """What the digital inputs read, one bit per channel above threshold."""
        listening = self._registers.get(PIN_CFG, 0) & ~self._registers.get(GPIO_CFG, 0)
        value = 0
        for channel in range(8):
            if listening & (1 << channel) and self._bench.level(self._side, channel) > 1.65:
                value |= 1 << channel
        return value


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
