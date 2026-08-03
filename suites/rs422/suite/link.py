"""Driving the lab end of the RS422 counter link.

A sender thread writes ENQ probes at the configured rate; a reader thread
parses ``RADCOUNT <n>`` replies and queues the values. The suite's iterate
drains that queue each tick, so serial timing is decoupled from the sample
cadence.
"""

from __future__ import annotations

import contextlib
import queue
import re
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


ENQ = b"\x05"

# FTDI FT232 family, the usual USB-RS422 adapter.
_FTDI_VID_PID = ("0403", "6001")

_RADCOUNT_RE = re.compile(r"RADCOUNT\s+(\d+)")


class LinkError(RuntimeError):
    """The serial link could not be opened or driven."""


def _serial() -> Any:
    try:
        import serial
    except ImportError as exc:
        raise LinkError("serial support needs pyserial: pip install pyserial") from exc
    return serial


def parse_radcount(line: str) -> int | None:
    """Extract the counter from one reply line, or ``None`` if absent."""
    match = _RADCOUNT_RE.search(line)
    return int(match.group(1)) if match else None


def resolve_device(configured: str) -> str:
    """Resolve ``auto`` to an FTDI adapter, or return the configured path."""
    if configured != "auto":
        return configured
    from serial.tools import list_ports

    vid, pid = _FTDI_VID_PID
    for port in list_ports.comports():
        hwid = (port.hwid or "").upper()
        if f"VID:PID={vid.upper()}:{pid.upper()}" in hwid.replace(" ", ""):
            return str(port.device)
    raise LinkError(f"no FTDI {vid}:{pid} adapter found; set link.device to an explicit path")


class CounterLink:
    """Open serial link with background sender and reader threads."""

    def __init__(self, *, device: str, baud: int, bytesize: int, parity: str, stopbits: int, read_timeout_s: float):
        serial = _serial()
        resolved = resolve_device(device)
        try:
            self._port = serial.Serial(
                port=resolved,
                baudrate=baud,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=read_timeout_s,
            )
        except Exception as exc:
            raise LinkError(f"opening {resolved}: {exc}") from exc
        self.device = resolved
        self.values: queue.Queue[int] = queue.Queue()
        self.probes_sent = 0
        self.read_errors = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, rate_hz: float) -> None:
        """Begin sending probes and reading replies."""
        self._threads = [
            threading.Thread(target=self._send_loop, args=(rate_hz,), daemon=True, name="rs422-send"),
            threading.Thread(target=self._read_loop, daemon=True, name="rs422-read"),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        """Stop both threads and close the port."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        with contextlib.suppress(Exception):
            self._port.close()

    def _send_loop(self, rate_hz: float) -> None:
        interval = 1.0 / rate_hz if rate_hz > 0 else 0.0
        while not self._stop.is_set():
            try:
                self._port.write(ENQ)
                self.probes_sent += 1
            except Exception:
                self.read_errors += 1
                return
            if interval:
                time.sleep(interval)

    def _read_loop(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self._port.read(256)
            except Exception:
                self.read_errors += 1
                return
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                value = parse_radcount(line.decode("ascii", errors="replace"))
                if value is not None:
                    self.values.put(value)
            # A reply that never terminates must not grow the buffer forever.
            if len(buffer) > 4096:
                buffer = buffer[-256:]
