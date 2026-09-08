"""A synthesised PMU3, so the suite runs with nothing attached.

It emits the frames a healthy board emits, on the same cadence, through the
same interface as ``board.Board``. What it is for is the smoke profile and the
conformance run: neither has a bench, and both have to exercise the parsing
and the accounting rather than skip them.
"""

from __future__ import annotations

import time

from suite.frames import PREFIX

_FW = "3.0.0-mock"

# The measured beat on a Curiosity HPC, which is what a real board's frames
# arrive at. Anything reading this stream is tuned to that rather than to 1 Hz.
_BEAT_MS = 1158.0


class MockBoard:
    """A board that answers, on a clock rather than a wire."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._seq = 0
        self._boot = 1
        self._emitted_boot = False
        self._next_ms = 0.0

    @property
    def device(self) -> str:
        return "mock"

    def close(self) -> None:
        return None

    def enable_stream(self, timeout_s: float) -> str:
        return self.report(timeout_s)

    def report(self, timeout_s: float) -> str:
        return f"frames=on seq={self._seq} ev=0 drop=0"

    def poll(self, timeout_s: float) -> list[str]:
        """Every frame due by now, after waiting out the timeout."""
        time.sleep(timeout_s)
        lines: list[str] = []
        if not self._emitted_boot:
            self._emitted_boot = True
            lines.append(self._frame("B", f"rst=POR,bits=0x0001,fw={_FW}"))
        now_ms = (time.monotonic() - self._started) * 1000.0
        while self._next_ms <= now_ms:
            lines.append(self._frame("T", "ev=0,drop=0"))
            self._next_ms += _BEAT_MS
        return lines

    def _frame(self, kind: str, tail: str) -> str:
        ms = int((time.monotonic() - self._started) * 1000.0)
        line = f"{PREFIX}{kind},{self._seq},{ms},{self._boot},{tail}"
        self._seq += 1
        return line
