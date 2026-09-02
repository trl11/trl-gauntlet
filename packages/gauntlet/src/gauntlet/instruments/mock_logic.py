"""A simulated eight-channel logic analyzer.

Every channel is a square wave, each half the frequency of the one above it,
so a capture at any rate shows something moving and the picture is the same
picture every time. It exists for development and for tests, and reaches an
operator only when ``simulated_instruments`` names it.
"""

from __future__ import annotations

import base64
import threading
from typing import Any

from gauntlet.capabilities.declare import command_field, command_row, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments import waveform
from gauntlet.instruments.fx2_logic import RATES, WINDOWS

# Longest channel label kept, as on the real analyzer.
_MAX_LABEL = 32

# How many samples the pattern takes to come back round, which is twice the
# period of the slowest channel.
_PERIOD = 4 << waveform.CHANNEL_COUNT


def pattern(samples: int) -> bytes:
    """A capture of the simulated probes, ``samples`` bytes of it.

    One period is built and repeated rather than every sample being computed,
    so a window of a few million samples costs no more than a short one.
    """
    if samples <= 0:
        return b""
    period = bytes(
        sum(((at // (4 << channel)) % 2) << channel for channel in range(waveform.CHANNEL_COUNT))
        for at in range(_PERIOD)
    )
    return (period * (samples // _PERIOD + 1))[:samples]


class MockLogic:
    """Capability provider that answers as an analyzer would, with no hardware."""

    name = "logic"

    def __init__(self, *, instance: str = "logic-sim") -> None:
        self._lock = threading.RLock()
        self._labels = {str(number): "" for number in range(1, waveform.CHANNEL_COUNT + 1)}
        self._captures = 0
        self._last_capture: dict[str, Any] = {"rate_hz": 0, "samples": 0, "window_s": 0.0}
        self._measured: dict[str, dict[str, Any]] = {}

    def available(self) -> bool:
        """A simulation is always there."""
        return True

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        with self._lock:
            if name == "capture":
                return self._capture(args)
            if name == "configure":
                return self._configure_channels(args)
            raise CommandRejected(f"logic has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers, as the real analyzer offers them."""
        with self._lock:
            rows = [command_row(name, f"CH {name}", {"label": label}) for name, label in sorted(self._labels.items())]
        return [
            {
                "name": "configure",
                "label": "Apply",
                "row_label": "Channel",
                "rows": rows,
                "fields": [command_field("label", "Label", "string")],
            },
            {
                "name": "capture",
                "label": "Capture",
                "fields": [
                    command_field("rate", "Sample rate", "string", choices=tuple(RATES)),
                    command_field("window", "Window", "string", choices=tuple(WINDOWS)),
                ],
                "returns": "image",
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return "simulated"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Eight simulated probes, each toggling at half the rate of the one above it.",
            "driver": "mock",
            "kind": "logic",
            "model": "Simulated logic analyzer",
            "unavailable_reason": "",
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return "logic-sim"

    def primary_command(self) -> str:
        """Taking one window of samples is what this panel is for."""
        return "capture"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """One reading per probe, named for whatever it is said to be clipped to."""
        with self._lock:
            labels = {name: self._label(name) for name in self._labels}
        entries = [
            readout(f"channels.{name}.level", labels[name], group="Channels") for name in sorted(labels, key=int)
        ]
        entries += [
            readout(
                f"channels.{name}.frequency",
                labels[name],
                group="Channels",
                precision=1,
                role="summary",
                unit="Hz",
            )
            for name in sorted(labels, key=int)
        ]
        entries.append(readout("captures", "Captures", role="viewer"))
        return entries

    def state(self) -> dict[str, Any]:
        """What each probe read over the last capture, and what that capture was."""
        with self._lock:
            channels = {}
            for name in sorted(self._labels, key=int):
                measured = self._measured.get(name, {})
                channels[name] = {
                    "duty": measured.get("duty"),
                    "edges": measured.get("edges"),
                    "frequency": measured.get("frequency"),
                    "label": self._label(name),
                    "level": measured.get("level"),
                }
            return {
                "captures": self._captures,
                "channels": channels,
                "connected": True,
                "last_capture": dict(self._last_capture),
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``.

        A capture answers with itself rather than with the state, because the
        picture of that window and what was measured in it are the whole
        result, and re-reading state would not carry the picture.
        """
        name = str(values.get("command", ""))
        result = self.command(name, dict(values.get("args") or {}))
        return result if name == "capture" else self.state()

    def _capture(self, args: dict[str, Any]) -> dict[str, Any]:
        """One window of the simulated pattern, measured and drawn."""
        rate_name = str(args.get("rate", next(iter(RATES))))
        window_name = str(args.get("window", next(iter(WINDOWS))))
        if rate_name not in RATES:
            raise CommandRejected(f"logic: 'rate' must be one of {', '.join(RATES)}")
        if window_name not in WINDOWS:
            raise CommandRejected(f"logic: 'window' must be one of {', '.join(WINDOWS)}")
        rate_hz = RATES[rate_name]
        window_s = WINDOWS[window_name]
        samples = pattern(int(rate_hz * window_s))

        self._captures += 1
        self._last_capture = {"rate_hz": rate_hz, "samples": len(samples), "window_s": window_s}
        self._measured = {
            name: waveform.measure(waveform.channel_column(samples, int(name) - 1), rate_hz) for name in self._labels
        }
        return {
            "channels": self.state()["channels"],
            "image_base64": base64.b64encode(waveform.render(samples)).decode(),
            "rate_hz": rate_hz,
            "samples": len(samples),
            "suffix": ".png",
            "window_s": window_s,
        }

    def _configure_channels(self, args: dict[str, Any]) -> dict[str, Any]:
        """Name any number of channels, leaving the rest alone."""
        rows = args.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise CommandRejected("logic: 'rows' must name at least one channel")
        labels: dict[str, str] = {}
        for key, values in rows.items():
            channel = str(key)
            if channel not in self._labels:
                raise CommandRejected(f"logic: no channel {key!r}")
            if not isinstance(values, dict):
                raise CommandRejected(f"logic: settings for channel {key!r} must be an object")
            if "label" in values:
                labels[channel] = " ".join(str(values["label"]).split())[:_MAX_LABEL]
        self._labels.update(labels)
        return {"channels": self.state()["channels"]}

    def _label(self, channel: str) -> str:
        """What a channel's readings are called, its number until it is named."""
        return self._labels[channel] or f"CH {channel}"
