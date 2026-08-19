"""Turning a captured frame into a file, and measuring what is in it.

A camera on this bench streams 4K YUYV, which is 16 MB a frame and no use as
an artifact. Converting and scaling in one pass produces something an operator
can open, and reads only the pixels that survive the scaling rather than all
of them.

PNG is written with `zlib` and `struct` for the same reason the capture layer
uses ioctls: the format is four chunks, and an artifact kept for a run is
worth keeping losslessly. JPEG is Pillow's, because a panel refreshing
continuously wants a tenth of the bytes and does not need every one of them
back. An MJPEG frame is already a JPEG and is written out byte for byte.
"""

from __future__ import annotations

import io
import struct
import zlib
from typing import Any

from PIL import Image

from gauntlet.instruments.v4l2 import PIXELFORMAT_MJPG, PIXELFORMAT_YUYV, Frame, fourcc

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# What a frame really carries, which is not always what the driver calls it.
# A GMSL adapter mislabels raw sensor data as YUYV to fit the UVC format list,
# so the encoding is resolved per frame rather than taken from the format.
ENCODING_AUTO = "auto"
ENCODING_RAW10_RGGB = "raw10_rggb"
ENCODING_YUYV = "yuyv"
FRAME_ENCODINGS = (ENCODING_AUTO, ENCODING_RAW10_RGGB, ENCODING_YUYV)

# The largest sample a 10-bit sensor produces, in a 16-bit little-endian word.
_RAW10_CEILING = 1023


class ImageError(RuntimeError):
    """A frame arrived in a format this module cannot write out."""


def image_suffix(pixelformat: int) -> str:
    """The file extension a frame in this format is written with."""
    if pixelformat == PIXELFORMAT_MJPG:
        return ".jpg"
    return ".png"


def encode_frame(
    frame: Frame,
    *,
    max_width: int = 960,
    encoding: str = ENCODING_AUTO,
    lossy: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    """One frame as file bytes, with what could be measured from it.

    `lossy` writes JPEG instead of PNG, for a viewer that wants the bytes
    rather than every pixel. What is measured is measured before either, so a
    reading does not depend on which was written.

    MJPEG is passed through, so nothing is measured from it: decoding a JPEG to
    read its luma would cost more than the measurement is worth here.

    `encoding` says what the frame really carries. The default resolves it from
    the frame, because a driver reporting YUYV is not proof the sensor sent it.
    """
    if frame.pixelformat == PIXELFORMAT_MJPG:
        return frame.data, {"width": frame.width, "height": frame.height}
    if frame.pixelformat != PIXELFORMAT_YUYV:
        raise ImageError(f"cannot write a {fourcc(frame.pixelformat)!r} frame")

    resolved = resolve_encoding(frame, encoding)
    if resolved == ENCODING_RAW10_RGGB:
        # A cell is two pixels wide, so the step that lands inside the width
        # asked for is measured in cells, not pixels.
        step = _step_for(frame.width // 2, max_width)
        pixels, width, height = raw10_rggb_to_rgb(frame.data, frame.width, frame.height, step=step)
        white_balance(pixels)
    else:
        step = _step_for(frame.width, max_width)
        pixels, width, height = yuyv_to_rgb(frame.data, frame.width, frame.height, step=step)
    # Widened because the encoding is named alongside the numbers measured.
    measured: dict[str, Any] = dict(measure(pixels, width, height))
    measured.update({"encoding": resolved, "height": height, "scale": step, "width": width})
    if lossy:
        return encode_jpeg(pixels, width, height), measured
    return encode_png(pixels, width, height), measured


def yuyv_to_rgb(data: bytes, width: int, height: int, *, step: int = 1) -> tuple[bytearray, int, int]:
    """Packed YUYV to RGB, taking every `step`-th pixel in both directions.

    YUYV carries a luma per pixel and a chroma pair per two pixels, so each
    four bytes is `Y0 U Y1 V`. Sampling rather than averaging keeps this to one
    pass over the pixels that are kept, which is what makes a 4K frame cheap
    enough to convert between iterations.
    """
    if step < 1:
        raise ImageError("step must be at least 1")
    stride = width * 2
    if len(data) < stride * height:
        raise ImageError(f"frame is {len(data)} bytes, a {width}x{height} YUYV frame is {stride * height}")

    out_width = (width + step - 1) // step
    out_height = (height + step - 1) // step
    pixels = bytearray(out_width * out_height * 3)
    at = 0
    for out_y in range(out_height):
        row = (out_y * step) * stride
        for out_x in range(out_width):
            source_x = out_x * step
            # Both pixels of a pair share one chroma sample, and which of the
            # two luma bytes belongs to this pixel depends on its parity.
            base = row + (source_x >> 1) * 4
            luma = data[base] if source_x % 2 == 0 else data[base + 2]
            blue_diff = data[base + 1] - 128
            red_diff = data[base + 3] - 128
            pixels[at] = _clamp(luma + 1.402 * red_diff)
            pixels[at + 1] = _clamp(luma - 0.344136 * blue_diff - 0.714136 * red_diff)
            pixels[at + 2] = _clamp(luma + 1.772 * blue_diff)
            at += 3
    return pixels, out_width, out_height


def looks_like_raw10(data: bytes, *, samples: int = 4096) -> bool:
    """Whether a frame is 10-bit sensor data rather than the YUYV it claims.

    Read as 16-bit little-endian words, RAW10 never exceeds 1023, so the high
    byte of every word is at most 3. Packed YUYV puts a chroma byte there and
    fills the whole range, and a frame whose chroma is that low throughout
    would have no colour in it at all. Words are read from across the frame
    rather than from the start, because a dark or clipped corner is not
    representative.

    This is a strong signal, not a guarantee, which is why `camera_format`
    can state the answer instead.
    """
    words = len(data) // 2
    if not words:
        return False
    stride = max(1, words // samples)
    return all(data[index * 2 + 1] <= _RAW10_CEILING >> 8 for index in range(0, words, stride))


def raw10_rggb_to_rgb(data: bytes, width: int, height: int, *, step: int = 1) -> tuple[bytearray, int, int]:
    """RAW10 Bayer in RGGB order to RGB, one output pixel per 2x2 cell.

    A Bayer cell already carries one red, one blue and two green samples, so
    binning it is a demosaic that needs no interpolation and no neighbours.
    That costs a quarter of the reads a full-resolution demosaic would and is
    cheaper than the packed-YUYV path at the same output width, because it
    reads one cell rather than every pixel.

    `step` counts cells, so a step of two takes every other cell in both
    directions. Samples are 16-bit little-endian and 10 bits wide, and are
    shifted down to a byte.
    """
    if step < 1:
        raise ImageError("step must be at least 1")
    stride = width * 2
    if len(data) < stride * height:
        raise ImageError(f"frame is {len(data)} bytes, a {width}x{height} raw frame is {stride * height}")

    cells_across = width // 2
    cells_down = height // 2
    out_width = (cells_across + step - 1) // step
    out_height = (cells_down + step - 1) // step
    pixels = bytearray(out_width * out_height * 3)
    at = 0
    for out_y in range(out_height):
        top = (out_y * step * 2) * stride
        bottom = top + stride
        for out_x in range(out_width):
            # Two bytes a sample, and the red sample opens the cell.
            left = (out_x * step * 2) * 2
            red = data[top + left] | (data[top + left + 1] << 8)
            green_top = data[top + left + 2] | (data[top + left + 3] << 8)
            green_bottom = data[bottom + left] | (data[bottom + left + 1] << 8)
            blue = data[bottom + left + 2] | (data[bottom + left + 3] << 8)
            pixels[at] = red >> 2
            pixels[at + 1] = ((green_top + green_bottom) >> 1) >> 2
            pixels[at + 2] = blue >> 2
            at += 3
    return pixels, out_width, out_height


def white_balance(pixels: bytearray) -> None:
    """Scale each channel to the mean of all three, in place.

    Raw sensor output is green-dominated because a Bayer cell samples green
    twice, so a correct decode still looks green until the channels are
    levelled. Gray-world assumes the scene averages to neutral, which is a
    guess, but it is the guess that needs nothing known about the scene or the
    sensor's own calibration.
    """
    if not pixels:
        return
    totals = [sum(pixels[channel::3]) for channel in range(3)]
    target = sum(totals) / 3
    for channel, total in enumerate(totals):
        if not total:
            continue
        gain = target / total
        if 0.99 < gain < 1.01:
            continue
        for at in range(channel, len(pixels), 3):
            pixels[at] = _clamp(pixels[at] * gain)


def resolve_encoding(frame: Frame, encoding: str) -> str:
    """Which decode a frame gets, settling `auto` by looking at the frame."""
    if encoding not in FRAME_ENCODINGS:
        raise ImageError(f"unknown camera format {encoding!r}")
    if encoding != ENCODING_AUTO:
        return encoding
    return ENCODING_RAW10_RGGB if looks_like_raw10(frame.data) else ENCODING_YUYV


def measure(pixels: bytearray, width: int, height: int) -> dict[str, float]:
    """Mean brightness and an edge score, for deciding whether a frame is any good.

    `mean_luma` catches a camera producing black, white or nothing. `sharpness`
    is the mean absolute difference between neighbouring samples along a row,
    which falls towards zero for a defocused or blank image and is what
    separates a lens cap from a picture of something.

    Every other column is read rather than every one, which halves the cost on
    an image already scaled down. Detail finer than two columns therefore falls
    between the samples, which for judging focus is no loss: an image whose only
    detail is at the sampling limit has none of the broad structure this is
    looking for.
    """
    if not width or not height:
        return {"mean_luma": 0.0, "sharpness": 0.0}
    total = 0
    edges = 0
    samples = 0
    for y in range(height):
        row = y * width * 3
        previous = None
        for x in range(0, width, 2):
            at = row + x * 3
            luma = (pixels[at] * 299 + pixels[at + 1] * 587 + pixels[at + 2] * 114) // 1000
            total += luma
            samples += 1
            if previous is not None:
                edges += abs(luma - previous)
            previous = luma
    if not samples:
        return {"mean_luma": 0.0, "sharpness": 0.0}
    return {
        "mean_luma": round(total / samples, 2),
        "sharpness": round(edges / samples, 3),
    }


def encode_jpeg(pixels: bytearray, width: int, height: int, *, quality: int = 80) -> bytes:
    """RGB bytes as a JPEG.

    For a view being refreshed rather than kept: a frame lands in roughly a
    tenth of the bytes a PNG of it takes, which is the difference between a
    panel that keeps up and one that waits on the wire.
    """
    image = Image.frombytes("RGB", (width, height), bytes(pixels))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def encode_png(pixels: bytearray, width: int, height: int, *, level: int = 6) -> bytes:
    """Eight-bit RGB as a PNG.

    Every scanline is written with filter type 0. Filtering would compress
    better, but choosing a filter per line costs more time than the bytes are
    worth for an artifact written once an iteration.
    """
    expected = width * height * 3
    if len(pixels) != expected:
        raise ImageError(f"{len(pixels)} bytes of pixels, a {width}x{height} RGB image needs {expected}")

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
            _chunk(b"IDAT", zlib.compress(bytes(raw), level)),
            _chunk(b"IEND", b""),
        ]
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, then a CRC over the last two."""
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _clamp(value: float) -> int:
    """One colour component, held inside a byte."""
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value)


def _step_for(width: int, max_width: int) -> int:
    """How many pixels to advance to land inside the width asked for."""
    if max_width < 1 or width <= max_width:
        return 1
    return -(-width // max_width)
