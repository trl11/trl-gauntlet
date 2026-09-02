"""A synthesised capture, for a run with no analyzer to ask.

A suite is independent of Gauntlet: it may import the SDK and the standard
library and nothing else. So the mock driver cannot borrow the simulated
analyzer the application ships, and draws its own capture here instead. The
PNG writer is deliberately a second one — the alternative is a suite that only
runs where the application is installed, which is the thing the contract
exists to prevent.

Each channel is a square wave at half the frequency of the one above it, and
the whole pattern is shifted along by the moment it was taken, so two captures
are never quite the same.
"""

from __future__ import annotations

import struct
import zlib

CHANNEL_COUNT = 8

# The picture: a lane per channel, and a pixel column per slice of the window.
_LANE_HEIGHT = 24
_MARGIN = 4
_PLOT_WIDTH = 720

_BACKGROUND = (17, 18, 22)
_GUIDE = (44, 46, 54)
_TRACE = (0, 214, 120)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def half_period(channel: int) -> int:
    """Samples one channel holds a level for, doubling down the probes."""
    return 4 << channel


def columns(channel: int, rate_hz: int, window_s: float, phase: float) -> list[tuple[int, bool]]:
    """Each pixel column of one channel: the level it opens at, and whether it holds both.

    The whole capture is not built sample by sample: the picture has 720
    columns however many million samples are behind it, and every measurement
    below follows from the wave rather than from the stream. A column standing
    for more samples than the wave holds a level for carries both, and is
    drawn as the band between them rather than as whichever level happened to
    land on it.
    """
    samples = max(1, int(rate_hz * window_s))
    step = samples / _PLOT_WIDTH
    offset = int(phase * rate_hz)
    turn = half_period(channel)
    drawn = []
    for column in range(_PLOT_WIDTH):
        start = offset + int(column * step)
        end = offset + max(int((column + 1) * step), int(column * step) + 1)
        drawn.append((int((start // turn) % 2), start // turn != (end - 1) // turn))
    return drawn


def measure(channel: int, rate_hz: int, window_s: float) -> dict[str, float]:
    """What a probe would have read, given the wave it is carrying."""
    frequency = rate_hz / (2.0 * half_period(channel))
    cycles = frequency * window_s
    return {
        "duty": 50.0,
        "edges": round(2.0 * cycles),
        "frequency": round(frequency, 1),
        "level": 0,
    }


def synthesise(rate_hz: int, window_s: float, phase: float) -> tuple[bytes, dict[str, dict[str, float]]]:
    """One capture as PNG bytes, with the measurements an analyzer would report."""
    width = _PLOT_WIDTH
    height = 2 * _MARGIN + CHANNEL_COUNT * _LANE_HEIGHT
    pixels = bytearray(bytes(_BACKGROUND) * (width * height))
    channels: dict[str, dict[str, float]] = {}
    for channel in range(CHANNEL_COUNT):
        top = _MARGIN + channel * _LANE_HEIGHT
        drawn = columns(channel, rate_hz, window_s, phase)
        _draw_guide(pixels, width, top + _LANE_HEIGHT - 1)
        _draw_trace(pixels, width, drawn, top)
        reading = measure(channel, rate_hz, window_s)
        reading["level"] = float(drawn[-1][0])
        channels[str(channel + 1)] = reading
    return _encode_png(pixels, width, height), channels


def _draw_guide(pixels: bytearray, width: int, y: int) -> None:
    """The line under one lane, so eight traces can be told apart."""
    at = 3 * (y * width)
    pixels[at : at + 3 * width] = bytes(_GUIDE) * width


def _draw_trace(pixels: bytearray, width: int, drawn: list[tuple[int, bool]], top: int) -> None:
    """One channel's levels, each column joined to the one beside it.

    A column is the span between the highest and lowest level it holds, so an
    edge lands as the vertical bar between the two levels however narrow it
    was.
    """
    high_y = top + 4
    low_y = top + _LANE_HEIGHT - 8
    paint = bytes(_TRACE)
    previous: tuple[int, int] | None = None
    for x, (level, mixed) in enumerate(drawn):
        at_level = high_y if level else low_y
        span = (high_y, low_y) if mixed else (at_level, at_level)
        joined = span if previous is None else (min(span[0], previous[0]), max(span[1], previous[1]))
        for row in range(joined[0], joined[1] + 1):
            at = 3 * (row * width + x)
            pixels[at : at + 3] = paint
        previous = span


def _encode_png(pixels: bytearray, width: int, height: int) -> bytes:
    """Eight-bit RGB as a PNG, every scanline written with filter type 0."""
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : (y + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        [
            _PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(bytes(raw), 6)),
            _chunk(b"IEND", b""),
        ]
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, then a CRC over the last two."""
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
