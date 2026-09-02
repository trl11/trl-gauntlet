"""Measuring and drawing a captured stream of logic levels.

A logic analyzer sends one byte per sample and a bit per channel, so a capture
arrives with its eight signals interleaved a sample at a time. Taking one
channel out of that is a 256-entry translation, which is what makes measuring
a few million samples something an API request can do rather than something a
run has to wait on.

Edges are counted over a whole channel at once, as an integer: exclusive-or
with itself shifted along by one sample leaves a set bit wherever a sample
differs from the one before it, and ``int.bit_count`` counts them. The first
sample is masked off, having nothing before it to differ from.

The picture is drawn straight into an RGB buffer and written by
:func:`~gauntlet.instruments.imaging.encode_png`. A capture holds far more
samples than the plot has pixel columns, so each column stands for however
many samples fall in it and one holding both levels is drawn as the band
between them: a pulse too narrow to see is still there as a transition rather
than dropped.
"""

from __future__ import annotations

from typing import Any

from gauntlet.instruments.imaging import encode_png

CHANNEL_COUNT = 8

# The plot: a gutter wide enough for a channel number, then a column per pixel
# of capture, and one lane per channel.
_GUTTER = 22
_LANE_HEIGHT = 28
_MARGIN = 4
_PLOT_WIDTH = 720

# Where a lane's two levels sit inside it, measured from its top.
_HIGH_OFFSET = 5
_LOW_OFFSET = 21

_BACKGROUND = (17, 18, 22)
_GUIDE = (44, 46, 54)
_LABEL = (128, 132, 144)
_TRACE = (0, 214, 120)

# One channel's bit lifted out of every byte, a table per channel.
_COLUMN_TABLES = tuple(bytes((value >> channel) & 1 for value in range(256)) for channel in range(CHANNEL_COUNT))

# Three by five glyphs for the channel numbers, drawn at double size. Only the
# digits a channel number is spelled with are here.
_DIGITS: dict[str, tuple[str, ...]] = {
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
}
_DIGIT_SCALE = 2


def channel_column(samples: bytes, channel: int) -> bytes:
    """One channel's bit taken out of every sample, as a byte of 0 or 1 each."""
    if not 0 <= channel < CHANNEL_COUNT:
        raise ValueError(f"channel {channel} is not one of the {CHANNEL_COUNT} a sample holds")
    return samples.translate(_COLUMN_TABLES[channel])


def count_edges(column: bytes) -> int:
    """How many times a channel changed level over a capture."""
    if len(column) < 2:
        return 0
    stream = int.from_bytes(column, "big")
    changes = (stream ^ (stream >> 8)) & ((1 << (8 * (len(column) - 1))) - 1)
    return changes.bit_count()


def measure(column: bytes, rate_hz: float) -> dict[str, Any]:
    """What one channel did over a capture.

    ``frequency`` is the edge rate halved, which is the frequency of a signal
    that repeats and means nothing for one that does not — a line that changed
    once reads as half a cycle over the window. ``duty`` says which it was:
    a square wave sits near 50 and a line that moved once does not.
    """
    if not column or rate_hz <= 0:
        return {"duty": 0.0, "edges": 0, "frequency": 0.0, "level": 0}
    seconds = len(column) / rate_hz
    edges = count_edges(column)
    return {
        "duty": round(100.0 * column.count(1) / len(column), 1),
        "edges": edges,
        "frequency": round(edges / (2.0 * seconds), 1),
        "level": column[-1],
    }


def render(samples: bytes) -> bytes:
    """A capture as a PNG, channel 1 at the top and channel 8 at the bottom."""
    width = _GUTTER + _PLOT_WIDTH
    height = 2 * _MARGIN + CHANNEL_COUNT * _LANE_HEIGHT
    pixels = bytearray(bytes(_BACKGROUND) * (width * height))
    for channel in range(CHANNEL_COUNT):
        top = _MARGIN + channel * _LANE_HEIGHT
        _draw_guide(pixels, width, top + _LANE_HEIGHT - 1)
        _draw_digit(pixels, width, str(channel + 1), 6, top + _HIGH_OFFSET)
        _draw_trace(pixels, width, channel_column(samples, channel), top)
    return encode_png(pixels, width, height)


def _draw_column(pixels: bytearray, width: int, x: int, top: int, bottom: int, colour: tuple[int, int, int]) -> None:
    """A vertical run of pixels, ``top`` and ``bottom`` both drawn."""
    paint = bytes(colour)
    for y in range(top, bottom + 1):
        at = 3 * (y * width + x)
        pixels[at : at + 3] = paint


def _draw_digit(pixels: bytearray, width: int, digit: str, left: int, top: int) -> None:
    """One glyph, each of its cells a square of ``_DIGIT_SCALE`` pixels."""
    glyph = _DIGITS.get(digit)
    if glyph is None:
        return
    paint = bytes(_LABEL)
    for row, cells in enumerate(glyph):
        for cell, lit in enumerate(cells):
            if lit != "1":
                continue
            for y in range(top + row * _DIGIT_SCALE, top + (row + 1) * _DIGIT_SCALE):
                at = 3 * (y * width + left + cell * _DIGIT_SCALE)
                pixels[at : at + 3 * _DIGIT_SCALE] = paint * _DIGIT_SCALE


def _draw_guide(pixels: bytearray, width: int, y: int) -> None:
    """The line under one lane, so eight traces can be told apart."""
    paint = bytes(_GUIDE)
    at = 3 * (y * width + _GUTTER)
    pixels[at : at + 3 * _PLOT_WIDTH] = paint * _PLOT_WIDTH


def _draw_trace(pixels: bytearray, width: int, column: bytes, top: int) -> None:
    """One channel's levels across the plot.

    A pixel column is drawn as the span between the highest and lowest level
    the samples behind it reached, joined to the span beside it, so an edge
    lands as the vertical bar between the two levels however few samples wide
    it was.
    """
    total = len(column)
    if not total:
        return
    high_y = top + _HIGH_OFFSET
    low_y = top + _LOW_OFFSET
    previous: tuple[int, int] | None = None
    for x in range(_PLOT_WIDTH):
        start = x * total // _PLOT_WIDTH
        end = min(total, max(start + 1, (x + 1) * total // _PLOT_WIDTH))
        chunk = column[start:end]
        high = chunk.count(1)
        span = (high_y if high else low_y, high_y if high == len(chunk) else low_y)
        joined = span if previous is None else (min(span[0], previous[0]), max(span[1], previous[1]))
        _draw_column(pixels, width, _GUTTER + x, joined[0], joined[1], _TRACE)
        previous = span
