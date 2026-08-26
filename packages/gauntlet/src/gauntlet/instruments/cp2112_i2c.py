"""Silicon Labs CP2112 USB-to-I2C bridge, over the kernel's own adapter.

The CP2112 is a HID device, but the kernel ships a ``hid-cp2112`` driver that
speaks the vendor's report protocol itself and registers an ordinary
``i2c-dev`` adapter on top of it — ``/dev/i2c-N``, named
``"CP2112 SMBus Bridge on hidrawN"``. So this driver never touches HID
reports; it opens that node and issues ``I2C_RDWR`` like any other I2C master,
which is also what lets ``write_read`` use a real repeated start rather than
two transactions with a stop between them.

``I2C_RDWR`` takes a C array of ``i2c_msg`` structs, which is built with
``ctypes`` rather than a new dependency — the same reasoning that keeps
``gauntlet.api.host_stats`` off a telemetry package.
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected

log = logging.getLogger("gauntlet.instruments.cp2112")

VENDOR_ID = 0x10C4
PRODUCT_ID = 0xEA90

_ADDRESS_MAX = 0x7F
_LENGTH_MAX = 512

_I2C_RDWR = 0x0707
_I2C_M_RD = 0x0001

_ADAPTER_NAME = re.compile(r"^CP2112 SMBus Bridge on (hidraw\d+)$")


class _I2cMsg(ctypes.Structure):
    """``struct i2c_msg`` from ``linux/i2c.h``, one entry per transaction."""

    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class _I2cRdwrData(ctypes.Structure):
    """``struct i2c_rdwr_ioctl_data``, the argument ``I2C_RDWR`` takes."""

    _fields_ = [("msgs", ctypes.POINTER(_I2cMsg)), ("nmsgs", ctypes.c_uint32)]


def candidate_adapters() -> list[tuple[str, str]]:
    """``(/dev/i2c-N, serial)`` for every CP2112 bridge the kernel has adapted.

    The adapter's name is how a CP2112 among possibly several I2C adapters on
    the host is told apart from the others; the serial comes from the hidraw
    device underneath it, for a caller asking for one bridge among several.
    """
    adapters = []
    for name_path in sorted(glob.glob("/sys/class/i2c-dev/i2c-*/name")):
        try:
            name = Path(name_path).read_text().strip()
        except OSError:
            continue
        match = _ADAPTER_NAME.match(name)
        if match is None:
            continue
        adapter = Path(name_path).parent.name  # "i2c-18"
        adapters.append((f"/dev/{adapter}", _hidraw_serial(match.group(1))))
    return adapters


def _hidraw_serial(hidraw: str) -> str:
    """The USB serial number of the hidraw node a CP2112 adapter sits on."""
    try:
        text = Path(f"/sys/class/hidraw/{hidraw}/device/uevent").read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("HID_UNIQ="):
            return line.split("=", 1)[1]
    return ""


def _parse_hex(args: dict[str, Any], key: str) -> bytes:
    """Bytes an operator or a suite wrote as hex, spaces allowed."""
    text = str(args.get(key, "")).strip().replace(" ", "").replace(":", "")
    if not text:
        return b""
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise CommandRejected(f"i2c: {key!r} is not valid hex: {exc}") from exc


def _to_hex(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)


class Cp2112I2c:
    """Capability provider for a CP2112 bridge, addressed generically.

    Unlike the PSU or the DAQ there is no fixed device on the other end of the
    bus — a suite names the address and the bytes itself, the way it would
    with any I2C bridge. ``write_read`` exists because a register read is a
    write of the register address followed by a read with no stop in between,
    which a plain write then a plain read cannot reproduce.
    """

    name = "i2c"

    def __init__(
        self,
        node: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        instance: str = "i2c0",
        probe_interval_s: float = 3.0,
    ) -> None:
        self._clock = clock
        self._fd: int | None = None
        self._instance = instance
        self._last: dict[str, Any] = {}
        self._last_probe = clock() - probe_interval_s
        self._lock = threading.Lock()
        self._node = node
        self._probe_interval_s = probe_interval_s
        self._unavailable_reason = "not yet probed"

    def available(self) -> bool:
        """Is the adapter node open right now.

        Polled by the UI on every refresh, so a missing bridge is re-probed at
        most once per ``probe_interval_s``.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Drop the adapter file descriptor."""
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one I2C transaction and return what it moved."""
        if name not in {"read", "write", "write_read"}:
            raise CommandRejected(f"i2c has no command {name!r}")
        with self._lock:
            if not self._connect():
                raise CommandRejected(f"i2c is unavailable: {self._unavailable_reason}")
            address = int(number_arg("i2c", args, "address", 0, _ADDRESS_MAX))
            if name == "write":
                data = _parse_hex(args, "data")
                self._transfer([(address, 0, data)])
                result = {"address": address, "data_hex": _to_hex(data), "direction": "write", "length": len(data)}
            elif name == "read":
                length = int(number_arg("i2c", args, "length", 1, _LENGTH_MAX))
                (read,) = self._transfer([(address, _I2C_M_RD, bytes(length))])
                result = {"address": address, "data_hex": _to_hex(read), "direction": "read", "length": length}
            else:
                data = _parse_hex(args, "data")
                length = int(number_arg("i2c", args, "length", 1, _LENGTH_MAX))
                _, read = self._transfer([(address, 0, data), (address, _I2C_M_RD, bytes(length))])
                result = {"address": address, "data_hex": _to_hex(read), "direction": "write_read", "length": length}
            self._last = result
            return result

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        address_field = command_field("address", "Address (7-bit)", minimum=0, maximum=_ADDRESS_MAX)
        length_field = command_field("length", "Length", minimum=1, maximum=_LENGTH_MAX)
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
        return f"{self._node} (I2C/SMBus)"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "USB-to-I2C bridge for driving I2C/SMBus devices under test.",
            "driver": "cp2112",
            "kind": "i2c",
            "model": "CP2112",
            "unavailable_reason": self._unavailable_reason,
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
            connected = self._connect()
        if not connected or not self._last:
            return {"address": None, "data_hex": None, "direction": None, "length": None}
        return dict(self._last)

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _connect(self) -> bool:
        """Open the adapter node if it is not already open."""
        if self._fd is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now
        try:
            self._fd = os.open(self._node, os.O_RDWR)
        except OSError as exc:
            self._unavailable_reason = f"cannot open {self._node}: {exc}"
            return False
        self._unavailable_reason = ""
        return True

    def _disconnect(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError as exc:
            log.debug("closing %s: %s", self._node, exc)

    def _transfer(self, messages: list[tuple[int, int, bytes]]) -> list[bytes]:
        """Run one or more I2C messages back to back, with no stop between them.

        Each message is ``(address, flags, buffer)``; a write's buffer is sent
        and a read's is filled. Returns each message's buffer afterwards, so a
        write echoes what it sent and a read carries what came back.
        """
        buffers = [(ctypes.c_uint8 * len(data)).from_buffer_copy(data) for _, _, data in messages]
        msgs = (_I2cMsg * len(messages))(
            *(
                _I2cMsg(addr=addr, flags=flags, len=len(data), buf=ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)))
                for (addr, flags, data), buf in zip(messages, buffers, strict=True)
            )
        )
        request = _I2cRdwrData(msgs=msgs, nmsgs=len(messages))
        fd = self._fd
        if fd is None:
            raise CommandRejected("i2c: not connected")
        try:
            # `request` itself, not its address: ctypes exposes the buffer
            # protocol, so fcntl.ioctl takes a pointer to its actual bytes.
            # ``addressof()`` would hand the ioctl the address as a plain
            # int argument instead of a pointer to read from, and that
            # address routinely exceeds what fcntl packs into a C int on a
            # 64-bit heap, raising OverflowError before the ioctl even runs.
            fcntl.ioctl(fd, _I2C_RDWR, request)
        except OSError as exc:
            self._unavailable_reason = f"I2C transfer failed: {exc}"
            self._disconnect()
            raise CommandRejected(f"i2c: transfer failed: {exc}") from exc
        return [bytes(buf) for buf in buffers]
