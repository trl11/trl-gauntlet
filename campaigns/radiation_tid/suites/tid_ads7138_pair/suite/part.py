"""One ADS7138 driven as a whole, rather than a register at a time.

Both parts on this bench take the same sequences, so the sequences live here
once and the runner holds two of these. Everything a check needs of a part is
a method: put the channels into one of the two shapes the bench uses, drive a
byte, read the digital inputs, measure a wire, take the spread of a static
level, and run the part's own calibration.
"""

from __future__ import annotations

import statistics
import time
from typing import Protocol

from suite.adc import (
    CAL_BIT,
    CHANNEL_SEL,
    CODE_MAX,
    CODE_SHIFT,
    DATA_CFG,
    GENERAL_CFG,
    GPI_VALUE,
    GPIO_CFG,
    GPO_DRIVE_CFG,
    GPO_VALUE,
    OPMODE_CFG,
    OSR_CFG,
    PIN_CFG,
    SEQUENCE_CFG,
    STATUS_CLEAR_BOR,
    SYSTEM_STATUS,
    AdcError,
)

# Written once at setup and read back every iteration. Manual mode, no
# oversampling, no appended status byte: everything the checks vary, they
# write themselves.
BASELINE = {
    DATA_CFG: 0x00,
    OPMODE_CFG: 0x00,
    OSR_CFG: 0x00,
    SEQUENCE_CFG: 0x00,
}

# How long the calibration bit is given to clear before it is called stuck.
# The part takes a few milliseconds; the ceiling is here so a part that never
# finishes fails the check rather than the run.
_CAL_TIMEOUT_S = 0.5


class Transport(Protocol):
    """What a part needs of whatever it is reached through.

    The bridge and the register model both satisfy it, which is what lets one
    set of sequences drive a real bench and a simulated one.
    """

    def read_data(self) -> int: ...

    def read_register(self, register: int) -> int: ...

    def write_register(self, register: int, value: int) -> None: ...


class Part:
    """One ADS7138, addressed through the bridge it is wired to."""

    def __init__(self, adc: Transport, name: str, *, settle_s: float = 0.002, vref_v: float = 3.3) -> None:
        self._adc = adc
        self._settle_s = settle_s
        self._vref_v = vref_v
        self.name = name

    def configure(self) -> None:
        """Clear the flags a power-up left and write the baseline registers.

        The brown-out flag is set by the power-up the part has already had, so
        it is cleared here and every bit seen afterwards is an event a run can
        attribute to the beam.
        """
        self._adc.write_register(SYSTEM_STATUS, STATUS_CLEAR_BOR)
        for register, value in BASELINE.items():
            self._adc.write_register(register, value)
        # Push-pull, because the other part's inputs offer no pullup for an
        # open drain and a wire nobody holds would read as noise.
        self._adc.write_register(GPO_DRIVE_CFG, 0xFF)

    def drive(self, byte: int) -> None:
        """Take all eight channels as outputs and put one byte on them."""
        self._adc.write_register(PIN_CFG, 0xFF)
        self._adc.write_register(GPIO_CFG, 0xFF)
        self._adc.write_register(GPO_VALUE, byte)
        time.sleep(self._settle_s)

    def listen_digital(self) -> None:
        """Take all eight channels as digital inputs."""
        self._adc.write_register(GPIO_CFG, 0x00)
        self._adc.write_register(PIN_CFG, 0xFF)

    def listen_analog(self) -> None:
        """Take all eight channels as analog inputs."""
        self._adc.write_register(PIN_CFG, 0x00)

    def release(self) -> None:
        """Stop driving, so the other part can have the wires."""
        self._adc.write_register(GPO_VALUE, 0x00)
        self._adc.write_register(GPIO_CFG, 0x00)

    def inputs(self) -> int:
        """What the eight digital inputs read."""
        return self._adc.read_register(GPI_VALUE)

    def millivolts(self, channel: int) -> float:
        """One channel converted, in millivolts."""
        return self.code(channel) * self._vref_v * 1000.0 / CODE_MAX

    def code(self, channel: int) -> int:
        """One channel converted, as the raw twelve-bit code."""
        self._adc.write_register(CHANNEL_SEL, channel)
        time.sleep(self._settle_s)
        return (self._adc.read_data() >> CODE_SHIFT) & CODE_MAX

    def spread(self, channel: int, samples: int) -> float:
        """The standard deviation of repeated conversions of one static wire.

        Nothing about the wire changes across the samples, so what is left is
        the part's own noise, which is the figure a dose moves.
        """
        self._adc.write_register(CHANNEL_SEL, channel)
        time.sleep(self._settle_s)
        codes = [(self._adc.read_data() >> CODE_SHIFT) & CODE_MAX for _ in range(samples)]
        return statistics.pstdev(codes)

    def saturated(self, channel: int) -> bool:
        """Is this channel's conversion pinned against an end of the scale.

        A reading at 0 or at full scale hides whatever spread is on it, so a
        check measuring noise has to be told to look somewhere else.
        """
        code = self.code(channel)
        return code == 0 or code == CODE_MAX

    def spread_at(self, channel: int, oversampling: int, samples: int) -> float:
        """The same spread, with the part averaging each conversion itself."""
        self._adc.write_register(OSR_CFG, oversampling)
        try:
            return self.spread(channel, samples)
        finally:
            self._adc.write_register(OSR_CFG, BASELINE[OSR_CFG])

    def calibrate(self) -> float:
        """Run the part's own offset calibration and time it, in milliseconds.

        Returns the ceiling when the bit never clears, which the caller reads
        as a part that has stopped calibrating rather than as a slow one.
        """
        general = self._adc.read_register(GENERAL_CFG)
        started = time.monotonic()
        self._adc.write_register(GENERAL_CFG, general | CAL_BIT)
        while time.monotonic() - started < _CAL_TIMEOUT_S:
            if not self._adc.read_register(GENERAL_CFG) & CAL_BIT:
                return (time.monotonic() - started) * 1000.0
        return _CAL_TIMEOUT_S * 1000.0

    def status(self) -> int:
        """The part's own health register."""
        return self._adc.read_register(SYSTEM_STATUS)

    def baseline_drift(self) -> list[int]:
        """Baseline registers that no longer read as they were written."""
        changed = []
        for register, value in BASELINE.items():
            if self._adc.read_register(register) != value:
                changed.append(register)
        return changed


def cal_timeout_ms() -> float:
    """What :meth:`Part.calibrate` answers for a calibration that never ends."""
    return _CAL_TIMEOUT_S * 1000.0


__all__ = ["BASELINE", "AdcError", "Part", "Transport", "cal_timeout_ms"]
