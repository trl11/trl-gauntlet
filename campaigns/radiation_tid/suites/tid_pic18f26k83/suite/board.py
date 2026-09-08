"""The PMU3 board under test, as this suite reaches it.

One debug UART carries three things at once: the human console, the event
lines, and the '@' frame stream this run measures. All this module does is get
the stream turned on and hand the lines up; parsing them is ``frames.py``.

The link is half-duplex and the firmware mutes its receiver while its
transmitter is enabled, so a board that is streaming swallows keystrokes. That
is the normal state here rather than an edge case: the stream flag is
persisted, so every run after the first one types at a board that is already
talking. Every key is therefore repeated until it is answered, which lands one
in the gap between frames.
"""

from __future__ import annotations

import re
import time

from suite.console import Console, ConsoleError

# Console 'n' reports the stream's state and every counter on one line, and
# '@' toggles the stream and prints the same line back. Reading before typing
# is what keeps a board that already had the stream on -- restored from its
# stored configuration -- from being toggled off by a blind '@'.
_KEY_REPORT = "n"
_KEY_TOGGLE = "@"

# How often a key is repeated while waiting. A beat is about 1.16s apart, so
# this puts several attempts inside the silence between two of them.
_RETRY_S = 0.4

# Attempts at getting the stream on. Each one re-reads the state first, so a
# toggle that was swallowed is retried and one that landed is not undone.
_ENABLE_ATTEMPTS = 3

_STATE_RE = re.compile(r"\bframes=(on|off)\b")


class Board:
    """A PMU3 on an open console."""

    def __init__(self, console: Console) -> None:
        self._console = console

    @property
    def device(self) -> str:
        return self._console.device

    def close(self) -> None:
        self._console.close()

    def poll(self, timeout_s: float) -> list[str]:
        """Whatever arrived on the wire, human text and frames alike."""
        return self._console.read_lines(timeout_s)

    def ask(self, key: str, marker: str, timeout_s: float) -> str:
        """Type one console key until it is answered, and return that reply.

        The key is repeated rather than sent once, because a streaming board
        cannot hear one sent while it is transmitting. Only keys that report
        something may be asked this way -- repeating a key that toggles would
        undo itself.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            self._console.send(key)
            line = self._console.wait_for(marker, min(_RETRY_S, timeout_s))
            if line is not None:
                return line
            if time.monotonic() >= deadline:
                raise ConsoleError(f"{self.device}: no {marker!r} reply to {key!r} in {timeout_s}s")

    def report(self, timeout_s: float) -> str:
        """Ask for the stream's state and counters, and return that line."""
        return self.ask(_KEY_REPORT, "frames=", timeout_s)

    def enable_stream(self, timeout_s: float) -> str:
        """Leave the frame stream on, and return the line that says so.

        The state is read before each toggle, so a '@' the board did not hear
        is sent again and one it did hear is not undone.
        """
        line = self.report(timeout_s)
        for _ in range(_ENABLE_ATTEMPTS):
            if streaming(line):
                return line
            self._console.send(_KEY_TOGGLE)
            line = self.report(timeout_s)
        raise ConsoleError(f"{self.device}: the frame stream would not turn on: {line}")


def streaming(report_line: str) -> bool:
    """Whether a console report says the stream is on."""
    match = _STATE_RE.search(report_line)
    return match is not None and match.group(1) == "on"


def report_counters(report_line: str) -> dict[str, int]:
    """The numeric fields of a console report, by name.

    The report spells its per-code counters with the long CODE words while an
    '@S' frame spells them short, so this keeps whatever it was given and the
    caller does the matching it needs.
    """
    counters: dict[str, int] = {}
    for token in report_line.split():
        key, sep, value = token.partition("=")
        if not sep:
            continue
        try:
            counters[key] = int(value)
        except ValueError:
            continue
    return counters
