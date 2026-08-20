"""Simulated camera.

Registered only when ``simulated_instruments`` names it, so the snapshot path
stays exercisable without a camera on the bench while an ordinary bench shows
only what is really attached to it.

It synthesises a YUYV frame and hands it to the same encoder the real driver
uses, so what a mock run writes into ``frames/`` is a real image produced by
the code a bench run exercises.
"""

from __future__ import annotations

import base64
import threading
import time
from collections.abc import Callable
from typing import Any

from gauntlet.capabilities.declare import readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.imaging import encode_frame, image_suffix
from gauntlet.instruments.v4l2 import PIXELFORMAT_YUYV, Frame, fourcc

_HEIGHT = 480
_WIDTH = 640

# Eight colour bars as luma and chroma, which is what a YUYV frame carries
# directly: white, yellow, cyan, green, magenta, red, blue, black.
_BARS = (
    (235, 128, 128),
    (210, 16, 146),
    (170, 166, 16),
    (145, 54, 34),
    (106, 202, 222),
    (81, 90, 240),
    (41, 240, 110),
    (16, 128, 128),
)


class MockCamera:
    """A colour-bar generator with a bar that sweeps across it.

    The sweep is what makes successive snapshots differ, so a suite checking
    that a camera is still producing new pictures rather than repeating one
    has something to see.
    """

    name = "camera"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        instance: str = "camera0",
        sweep_period_s: float = 6.0,
    ) -> None:
        self._clock = clock
        self._instance = instance
        self._last_frame: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._snapshots = 0
        self._started = clock()
        self._sweep_period_s = sweep_period_s

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        if name != "snapshot":
            raise CommandRejected(f"camera has no command {name!r}")
        return self._snapshot(args)

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        return [
            {
                "name": "snapshot",
                "label": "Take Snapshot",
                "fields": [],
                "returns": "image",
            }
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Colour bars with a sweeping bar, as still images.",
            "driver": "mock",
            "kind": "camera",
            "model": "mock-camera",
            "resolution": f"{_WIDTH}x{_HEIGHT} YUYV",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking a picture is what an operator comes to this panel for."""
        return "snapshot"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """What the camera is set to, and what the last snapshot looked like."""
        return [
            readout("format.width", "Width", group="Format", role="summary", unit="px"),
            readout("format.height", "Height", group="Format", role="summary", unit="px"),
            readout("format.fourcc", "Pixel format", group="Format", role="summary"),
            readout("last_frame.mean_luma", "Brightness", group="Last snapshot", precision=1),
            readout("last_frame.sharpness", "Sharpness", group="Last snapshot", precision=2),
            readout("snapshots", "Snapshots", group="Last snapshot", role="summary"),
        ]

    def state(self) -> dict[str, Any]:
        """The format on offer and what was measured from the last snapshot."""
        with self._lock:
            return {
                "format": {"fourcc": "YUYV", "height": _HEIGHT, "width": _WIDTH},
                "last_frame": dict(self._last_frame),
                "node": "",
                "snapshots": self._snapshots,
                "streaming": True,
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        name = str(values.get("command", ""))
        result = self.command(name, dict(values.get("args") or {}))
        if name == "snapshot":
            return result
        return self.state()

    def _snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        """One synthesised still, encoded the way a real frame would be."""
        max_width = args.get("max_width")
        # Absent means the frame's own width, which is what the panel asks for.
        width = int(max_width) if isinstance(max_width, (int, float)) and not isinstance(max_width, bool) else _WIDTH
        if not 16 <= width <= _WIDTH:
            raise CommandRejected(f"camera: 'max_width' must be between 16 and {_WIDTH}")

        with self._lock:
            self._snapshots += 1
            sequence = self._snapshots
            moment = self._clock() - self._started
        frame = Frame(
            data=_pattern(moment / self._sweep_period_s),
            height=_HEIGHT,
            pixelformat=PIXELFORMAT_YUYV,
            sequence=sequence,
            width=_WIDTH,
        )
        payload, measured = encode_frame(frame, max_width=width)

        with self._lock:
            self._last_frame = {"bytes": len(payload), "sequence": sequence, **measured}
            last = dict(self._last_frame)
        return {
            "image_base64": base64.b64encode(payload).decode(),
            "suffix": image_suffix(PIXELFORMAT_YUYV),
            "source": {"width": _WIDTH, "height": _HEIGHT, "fourcc": fourcc(PIXELFORMAT_YUYV)},
            **last,
        }


def _pattern(phase: float) -> bytes:
    """One YUYV frame: colour bars, with a bar swept across them.

    The sweep inverts the luma beneath it rather than painting a colour of its
    own. A bar of its own would be invisible over the one bar that matches it,
    which would make two snapshots taken while it crossed that bar identical
    and a check for a frozen picture wrong. No bar's luma is near the middle of
    the range, so inverting always shows.

    It wraps rather than running off the edge, so every frame has the same
    brightness and only the position of the sweep separates them.

    Built a row at a time and repeated, because every row of this pattern is
    the same and generating 640 pixels once is cheaper than generating them
    480 times.
    """
    bar_width = _WIDTH // len(_BARS)
    sweep_at = int((phase % 1.0) * _WIDTH) & ~1
    sweep_width = _WIDTH // 16

    swept = {(sweep_at + offset) % _WIDTH for offset in range(0, sweep_width, 2)}
    row = bytearray()
    for x in range(0, _WIDTH, 2):
        luma_left, blue, red = _BARS[min(x // bar_width, len(_BARS) - 1)]
        luma_right, _, _ = _BARS[min((x + 1) // bar_width, len(_BARS) - 1)]
        if x in swept:
            luma_left = 255 - luma_left
            luma_right = 255 - luma_right
        row += bytes((luma_left, blue, luma_right, red))
    return bytes(row) * _HEIGHT
