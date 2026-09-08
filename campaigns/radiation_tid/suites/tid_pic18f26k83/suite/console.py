"""The debug UART, opened with the standard library and nothing else.

115200 8N1 on a tty is ``termios`` and ``os.read``, so this suite installs
nothing to reach the board. That matters more here than consistency with the
other serial suite in the tree: this one runs in a beam room off a deployed
bundle, and a missing dependency there is a beam slot rather than an error
message.

The link is half-duplex -- the firmware mutes its receiver while its
transmitter is enabled -- so a keystroke sent to a board that is already
streaming can be swallowed. Everything typed here is typed once, at connect,
before the stream is turned on.
"""

from __future__ import annotations

import glob
import os
import select
import termios
import time
import tty
from pathlib import Path

_BAUD_CODES: dict[int, int] = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

# A PKOB4 on a Curiosity HPC bridges the target's UART to a CDC-ACM node; a
# TTL serial cable on the same pins arrives as a ttyUSB. Either is the console
# and a bench can have both, along with the programmer's own port and whatever
# else is plugged in, so which one it is has to be asked rather than counted.
_AUTO_GLOBS = ("/dev/ttyACM*", "/dev/ttyUSB*")

# What a board says back when asked for its frame-stream state. Nothing else
# on a bench answers with this.
_ANSWER = "frames="


class ConsoleError(RuntimeError):
    """The port could not be opened, or the board did not answer."""


# Console 'n', which reports the frame stream's state. board.py types the same
# key; it is repeated here because resolving a port happens before there is a
# board to ask through.
_PROBE_KEY = "n"


def describe_device(device: str) -> str:
    """What USB says the port is, for the log. Empty when it will not say."""
    node = Path("/sys/class/tty") / Path(device).name / "device"
    for parents in ("..", "../.."):
        base = node / parents
        vendor = _read(base / "manufacturer")
        product = _read(base / "product")
        if vendor or product:
            return " ".join(part for part in (vendor, product) if part)
    return ""


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def resolve_device(configured: str, baud: int, timeout_s: float) -> str:
    """Resolve ``auto`` by asking each candidate tty whether it is the board.

    Enumeration cannot answer this. A bench carries the programmer's own port
    beside the console, and which node is which depends on how the UART was
    brought out, so the port is identified by the board replying on it rather
    than by counting what is attached.

    The probe types one console character at each candidate in turn, so a port
    that is not a board sees a single stray byte.
    """
    if configured != "auto":
        return configured
    found = sorted(path for pattern in _AUTO_GLOBS for path in glob.glob(pattern))
    if not found:
        raise ConsoleError("no /dev/ttyACM* or /dev/ttyUSB* found; set console.device to a path")
    if len(found) == 1:
        return found[0]
    for device in found:
        if _answers(device, baud, timeout_s):
            return device
    tried = ", ".join(f"{device} ({describe_device(device) or 'unknown'})" for device in found)
    raise ConsoleError(
        f"no board answered on any candidate port ({tried}); "
        "check the console wiring and the baud, or set console.device to a path"
    )


def _answers(device: str, baud: int, timeout_s: float) -> bool:
    """Whether a PMU3 replies on this port."""
    try:
        console = Console(device, baud)
    except ConsoleError:
        return False
    try:
        console.send(_PROBE_KEY)
        return console.wait_for(_ANSWER, timeout_s) is not None
    except ConsoleError:
        return False
    finally:
        console.close()


class Console:
    """One open debug UART, read as whole lines."""

    def __init__(self, device: str, baud: int) -> None:
        if baud not in _BAUD_CODES:
            raise ConsoleError(f"unsupported baud {baud}; one of {sorted(_BAUD_CODES)}")
        self.device = device
        self._baud = baud
        self._buffer = ""
        self._pending: list[str] = []
        try:
            self._fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            raise ConsoleError(f"cannot open {device}: {exc}") from exc
        try:
            self._configure()
        except OSError as exc:
            os.close(self._fd)
            raise ConsoleError(f"cannot configure {device}: {exc}") from exc

    def _configure(self) -> None:
        tty.setraw(self._fd)
        attrs = termios.tcgetattr(self._fd)
        attrs[2] = (attrs[2] & ~termios.CSIZE) | termios.CS8
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
        attrs[2] |= termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = _BAUD_CODES[self._baud]
        # A read returns whatever has arrived rather than blocking for a count;
        # select above it is what waits.
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        termios.tcflush(self._fd, termios.TCIOFLUSH)

    def close(self) -> None:
        """Drop the port, leaving the board doing whatever it was doing."""
        os.close(self._fd)

    def send(self, keys: str) -> None:
        """Type at the console. One character per command, no terminator."""
        try:
            os.write(self._fd, keys.encode("ascii"))
        except OSError as exc:
            raise ConsoleError(f"cannot write to {self.device}: {exc}") from exc

    def read_lines(self, timeout_s: float) -> list[str]:
        """Every complete line that arrived within the timeout.

        A partial line is held for the next call rather than returned, so a
        frame split across two reads is parsed once and whole.
        """
        ready, _, _ = select.select([self._fd], [], [], timeout_s)
        if ready:
            try:
                chunk = os.read(self._fd, 4096)
            except OSError as exc:
                raise ConsoleError(f"cannot read from {self.device}: {exc}") from exc
            self._buffer += chunk.decode("ascii", errors="replace")
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        held, self._pending = self._pending, []
        return held + [line.strip("\r") for line in lines]

    def wait_for(self, needle: str, timeout_s: float) -> str | None:
        """The first line containing ``needle``, or ``None`` by the deadline.

        A reply is emitted from the firmware's main loop, so it arrives up to
        one superloop pass after the key that asked for it -- about 100ms, and
        several hundred on a board whose own I2C is timing out. One read is
        not long enough to wait for that.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            lines = self.read_lines(min(0.2, timeout_s))
            for index, line in enumerate(lines):
                if needle in line:
                    # Everything else in the batch is held rather than dropped.
                    # The board announces itself with a boot frame the moment
                    # the stream is enabled, and that frame arrives interleaved
                    # with the reply being waited for here.
                    self._pending.extend(lines[index + 1 :])
                    return line
                self._pending.append(line)
            if time.monotonic() >= deadline:
                return None
