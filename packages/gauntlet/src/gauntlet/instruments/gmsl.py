"""GMSL link telemetry, read through the camera's UVC extension unit.

A GMSL camera reaches the host through a serializer in the camera head and a
deserializer in the adapter. Both are I2C devices, and Leopard Imaging's
adapters tunnel I2C over a vendor extension unit on the same USB connection
that carries video, so the link can be interrogated without wiring anything to
the board.

The extension unit's selectors and the layout of its I2C transaction follow
Leopard's own open-source tool, `LI01/linux_camera_tool`. Eleven of its fifteen
selectors match this adapter exactly. The register meanings come from the
Maxim GMSL2/3 Linux drivers and from what the parts on this bench report;
Analog Devices' user guides for the MAX96792A and MAX96793 are the authority
and are listed as missing in `docs/datasheets/README.md`.

**Nothing here writes.** A read cannot disturb the link. A write can, and a
serializer that drops its link part-way through an irradiation looks exactly
like a radiation effect, which would cost the run its meaning rather than just
its data. That rules out clearing a saturated counter, so a counter already at
its ceiling is reported as saturated rather than quietly used as a baseline.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path

# uvcvideo's private ioctl for reaching an extension unit.
_UVC_SET_CUR = 0x01
_UVC_GET_CUR = 0x81

# The unit and selector Leopard puts its I2C tunnel behind, and the size of the
# transaction buffer it expects.
XU_UNIT = 3
XU_GENERIC_I2C_RW = 0x10
XU_UUID_HWFW_REV = 0x07
_I2C_PAYLOAD_BYTES = 262
_IDENTITY_BYTES = 49

# Byte 0 of the transaction: the top bit picks the direction and the rest is
# the width of the register address. The MAX9679x address 16 bits.
_READ_16BIT_ADDRESS = 0x02

# Registers common to the GMSL2/3 serializers and deserializers.
REG_ADDRESS = 0x0000
REG_DEV_ID = 0x000D
REG_DEV_REV = 0x000E
REG_CTRL3 = 0x0013
REG_DECODE_ERRORS_A = 0x0022
REG_DECODE_ERRORS_B = 0x0023
REG_IDLE_ERRORS = 0x0024

_CTRL3_LOCKED = 1 << 3
_CTRL3_ERROR = 1 << 0

# An eight-bit counter that has stopped counting.
_COUNTER_CEILING = 0xFF

# The chips answer over a link, so a transaction needs a moment between the
# request and collecting its answer.
_TRANSACTION_SETTLE_S = 0.004


class GmslError(RuntimeError):
    """The extension unit could not be reached, or answered nothing usable."""


class _XuQuery(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


UVCIOC_CTRL_QUERY = (3 << 30) | (ctypes.sizeof(_XuQuery) << 16) | (ord("u") << 8) | 0x21


@dataclass(frozen=True)
class ChipStatus:
    """What one end of the link reports about itself.

    **The error counters clear when they are read**, measured on this bench: a
    second read straight after the first returns zero. So each count is the
    errors since the previous read, not a total since power-up, and whoever
    reads them consumes them. Only one reader may poll a chip, or the counts
    are split between them and both under-report. `UvcCamera` is that reader
    and accumulates the totals.
    """

    address: int
    decode_errors_a: int
    decode_errors_b: int
    dev_id: int
    dev_rev: int
    idle_errors: int
    link_error: bool
    locked: bool

    @property
    def saturated(self) -> bool:
        """Did a counter hit its ceiling, making the interval's count a floor.

        A saturated counter means at least 255 errors since the last read and
        an unknown number beyond, so a run that saturates has to be read more
        often before its counts mean anything.
        """
        return _COUNTER_CEILING in (self.decode_errors_a, self.decode_errors_b, self.idle_errors)

    @property
    def total_errors(self) -> int:
        """Every error counter this interval added together, one line per chip."""
        return self.decode_errors_a + self.decode_errors_b + self.idle_errors

    def as_dict(self) -> dict[str, object]:
        """The status as metrics, with the address as its hexadecimal name."""
        return {
            "address": f"0x{self.address:02x}",
            "decode_errors_a": self.decode_errors_a,
            "decode_errors_b": self.decode_errors_b,
            "dev_id": f"0x{self.dev_id:02x}",
            "dev_rev": f"0x{self.dev_rev:02x}",
            "idle_errors": self.idle_errors,
            "link_error": self.link_error,
            "locked": self.locked,
            "saturated": self.saturated,
            "total_errors": self.total_errors,
        }


class GmslLink:
    """The GMSL chips behind one capture node.

    Its own descriptor is opened rather than borrowing the one the camera
    streams through, because an extension unit answers on any descriptor and
    sharing one would tie this to the capture layer's locking.
    """

    def __init__(self, path: Path) -> None:
        self._fd: int | None = None
        self._path = path

    def close(self) -> None:
        """Release the descriptor."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def identity(self) -> dict[str, str]:
        """The adapter's own hardware revision, firmware revision and UUID.

        Worth recording on every run: the UUID is what ties a result to one
        physical unit, which a campaign that irradiates parts to destruction
        needs and a serial number printed on a box does not give.
        """
        raw = self._query(XU_UUID_HWFW_REV, _IDENTITY_BYTES, _UVC_GET_CUR)
        hardware = raw[0] | (raw[1] << 8)
        return {
            # The top four bits of the hardware revision are a datatype tag.
            "datatype": f"0x{hardware & 0xF000:04x}",
            "firmware_revision": str(raw[2] | (raw[3] << 8)),
            "hardware_revision": str(hardware & ~0xF000),
            "uuid": raw[4:_IDENTITY_BYTES].decode("ascii", "replace").strip("\x00"),
        }

    def open(self) -> None:
        """Open the node for control transfers only, never for capture."""
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self._path, os.O_RDWR)
        except OSError as exc:
            raise GmslError(f"{self._path}: {exc.strerror or exc}") from exc

    def read_register(self, address: int, register: int) -> int:
        """One eight-bit register from the chip at this I2C address."""
        request = [
            _READ_16BIT_ADDRESS,
            0,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (register >> 8) & 0xFF,
            register & 0xFF,
        ]
        request += [0] * (_I2C_PAYLOAD_BYTES - len(request))
        self._query(XU_GENERIC_I2C_RW, _I2C_PAYLOAD_BYTES, _UVC_SET_CUR, request)
        time.sleep(_TRANSACTION_SETTLE_S)
        return self._query(XU_GENERIC_I2C_RW, _I2C_PAYLOAD_BYTES, _UVC_GET_CUR, request)[6]

    def scan(self) -> list[int]:
        """Every I2C address a GMSL chip answers on, lowest first.

        A chip is recognised by its first register holding its own address,
        which is what tells a real answer from a bus reading back nothing.
        """
        found = []
        for address in range(0x02, 0x100, 2):
            try:
                if self.read_register(address, REG_ADDRESS) == address:
                    found.append(address)
            except (GmslError, OSError):
                continue
        return found

    def status(self, address: int) -> ChipStatus:
        """Identity, lock state and every error counter from one chip."""
        control = self.read_register(address, REG_CTRL3)
        return ChipStatus(
            address=address,
            decode_errors_a=self.read_register(address, REG_DECODE_ERRORS_A),
            decode_errors_b=self.read_register(address, REG_DECODE_ERRORS_B),
            dev_id=self.read_register(address, REG_DEV_ID),
            dev_rev=self.read_register(address, REG_DEV_REV),
            idle_errors=self.read_register(address, REG_IDLE_ERRORS),
            link_error=bool(control & _CTRL3_ERROR),
            locked=bool(control & _CTRL3_LOCKED),
        )

    def _query(self, selector: int, length: int, direction: int, payload: list[int] | None = None) -> bytes:
        """One extension unit transfer, in whichever direction was asked for."""
        if self._fd is None:
            raise GmslError(f"{self._path}: not open")
        buffer = (ctypes.c_uint8 * length)(*(payload or [0] * length))
        query = _XuQuery(
            XU_UNIT,
            selector,
            direction,
            length,
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint8)),
        )
        try:
            fcntl.ioctl(self._fd, UVCIOC_CTRL_QUERY, query)
        except OSError as exc:
            raise GmslError(f"{self._path}: extension unit {selector:#04x}: {exc.strerror or exc}") from exc
        return bytes(buffer)
