"""A synthesised still, for a run with no camera to ask.

A suite is independent of Gauntlet: it may import the SDK and the standard
library and nothing else. So the mock driver cannot borrow the simulated
camera the application ships, and writes its own image here instead. The PNG
writer is deliberately a second one — the alternative is a suite that only runs
where the application is installed, which is the thing the contract exists to
prevent.

The pattern is grey bars with a bar swept across them that inverts what it
passes over. Inverting rather than painting its own shade means two stills
taken at different moments are never identical, which is what the frozen-frame
check needs to have something to see.
"""

from __future__ import annotations

import struct
import zlib

_BARS = (16, 60, 104, 148, 192, 235)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def synthesise(phase: float, width: int, height: int) -> tuple[bytes, dict[str, float]]:
    """One still as PNG bytes, with the measurements a camera would report.

    ``phase`` moves the sweep: a whole number of turns puts it back where it
    started, so a caller passing elapsed seconds over a period gets a sweep
    that crosses the frame in that period.
    """
    if width < 2 or height < 1:
        raise ValueError(f"a {width}x{height} frame is too small to draw")

    bar_width = max(1, width // len(_BARS))
    sweep_at = int((phase % 1.0) * width)
    sweep_width = max(2, width // 16)
    swept = {(sweep_at + offset) % width for offset in range(sweep_width)}

    row = bytearray()
    for x in range(width):
        luma = _BARS[min(x // bar_width, len(_BARS) - 1)]
        if x in swept:
            luma = 255 - luma
        row += bytes((luma, luma, luma))

    pixels = bytearray(bytes(row) * height)
    return _encode_png(pixels, width, height), _measure(row, width, height)


def _measure(row: bytearray, width: int, height: int) -> dict[str, float]:
    """Brightness and edge detail, read off the one row the frame repeats."""
    lumas = [row[x * 3] for x in range(width)]
    edges = sum(abs(lumas[x] - lumas[x - 1]) for x in range(1, width))
    return {
        "height": float(height),
        "mean_luma": round(sum(lumas) / len(lumas), 2),
        "sharpness": round(edges / len(lumas), 3),
        "width": float(width),
    }


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
