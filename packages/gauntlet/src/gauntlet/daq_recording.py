"""Every scan a streaming instrument produced during a run, kept at full rate.

The instrument recorder keeps one reading a second, which cannot show what a
DAQ read in between. This keeps the scans themselves, in the run's ``daq/``
directory, so a run that is over can be looked at as closely as the
instrument measured it.

A recording is split into segments, a new one whenever the scan list changes,
because a row of values means something only against the channels it was
taken with. A segment is three files:

``<instrument>.<n>.json``
    Which instrument, its channels (``key``, ``label``, ``unit``), its rate.
``<instrument>.<n>.t``
    One little-endian float64 per scan: the UTC time it was read, in seconds.
``<instrument>.<n>.v``
    One little-endian float32 per channel per scan, in channel order, scan
    after scan. A reading the instrument did not give is NaN.

Fixed-size rows are what make a window of an hour-long recording cheap to
read: the time of a scan is found by bisection and its values by arithmetic,
without reading the rest.
"""

from __future__ import annotations

import bisect
import json
import math
import mmap
import sys
from array import array
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DIR = "daq"

# Points a window is drawn with at most, and least.
MAX_POINTS = 20_000
MIN_POINTS = 50

_BIG_ENDIAN = sys.byteorder != "little"

Scan = tuple[int, float, float, list[float | None]]


def _swapped(values: array) -> array:  # type: ignore[type-arg]
    """The array in file order, which is little-endian whatever the host is."""
    if _BIG_ENDIAN:
        values.byteswap()
    return values


class Recorder:
    """Writes the scans of one instrument as they arrive."""

    def __init__(self, run_dir: Path, instrument: str) -> None:
        self._dir = run_dir / DIR
        self._instrument = instrument
        self._segment = 0
        self._channels: list[dict[str, Any]] | None = None
        self._times: Any = None
        self._values: Any = None

    def write(self, channels: list[dict[str, Any]], rate_hz: float, scans: Sequence[Scan]) -> None:
        """Append scans taken with ``channels``, starting a segment when they changed."""
        if not scans:
            return
        if self._layout(channels) != self._layout(self._channels or []) or self._times is None:
            self._open(channels, rate_hz)
        elif channels != self._channels:
            self._describe(channels, rate_hz)
        times = array("d", (scan[2] for scan in scans))
        values = array("f")
        for scan in scans:
            values.extend(math.nan if value is None else value for value in scan[3])
        self._times.write(_swapped(times).tobytes())
        self._values.write(_swapped(values).tobytes())
        self._times.flush()
        self._values.flush()

    def close(self) -> None:
        for handle in (self._times, self._values):
            if handle is not None:
                handle.close()
        self._times = self._values = None

    @staticmethod
    def _layout(channels: list[dict[str, Any]]) -> list[tuple[str, str]]:
        return [(channel["key"], channel["unit"]) for channel in channels]

    def _stem(self) -> Path:
        return self._dir / f"{self._instrument}.{self._segment:03d}"

    def _open(self, channels: list[dict[str, Any]], rate_hz: float) -> None:
        self.close()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._segment += 1
        stem = self._stem()
        self._describe(channels, rate_hz)
        self._times = _file(stem, ".t").open("ab")
        self._values = _file(stem, ".v").open("ab")

    def _describe(self, channels: list[dict[str, Any]], rate_hz: float) -> None:
        self._channels = [
            {"key": channel["key"], "label": channel["label"], "unit": channel["unit"]} for channel in channels
        ]
        meta = {"channels": self._channels, "instrument": self._instrument, "rate_hz": rate_hz}
        _file(self._stem(), ".json").write_text(json.dumps(meta) + "\n", encoding="utf-8")


@dataclass
class _Segment:
    """One segment on disk."""

    channels: list[dict[str, Any]]
    instrument: str
    rate_hz: float
    stem: Path

    @property
    def rows(self) -> int:
        """Whole scans on disk, counting only those both files have."""
        try:
            scans = _file(self.stem, ".t").stat().st_size // 8
            values = _file(self.stem, ".v").stat().st_size // (4 * max(1, len(self.channels)))
        except OSError:
            return 0
        return min(scans, values)


def _file(stem: Path, extension: str) -> Path:
    """A segment's file. The stem has dots of its own, so the extension is added, not swapped."""
    return stem.with_name(stem.name + extension)


def _segments(run_dir: Path) -> list[_Segment]:
    found = []
    for meta in sorted((run_dir / DIR).glob("*.json")):
        try:
            body = json.loads(meta.read_text(encoding="utf-8"))
            found.append(
                _Segment(
                    body["channels"],
                    body["instrument"],
                    float(body["rate_hz"]),
                    meta.with_name(meta.name.removesuffix(".json")),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


@contextmanager
def _mapped(path: Path, kind: Literal["d", "f"]) -> Iterator[memoryview[float]]:
    """A file as an array of ``kind`` ('d' or 'f') values, without reading it all."""
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        view = memoryview(mapped)
        cast = view.cast(kind)
        try:
            yield cast
        finally:
            cast.release()
            view.release()


def _first_time(segment: _Segment) -> float | None:
    if segment.rows == 0:
        return None
    with _mapped(_file(segment.stem, ".t"), "d") as times:
        return float(times[0])


def recordings(run_dir: Path) -> dict[str, Any]:
    """What a run recorded: its instruments, and the span each covers in seconds.

    Times are seconds from ``origin``, the first scan any instrument gave.
    """
    segments = [segment for segment in _segments(run_dir) if segment.rows > 0]
    if not segments:
        return {"instruments": [], "origin": None}
    starts = {id(segment): _first_time(segment) or 0.0 for segment in segments}
    origin = min(starts.values())
    instruments: dict[str, dict[str, Any]] = {}
    for segment in segments:
        with _mapped(_file(segment.stem, ".t"), "d") as times:
            end = float(times[segment.rows - 1])
        entry = instruments.setdefault(
            segment.instrument,
            {"instrument": segment.instrument, "rows": 0, "start_s": starts[id(segment)] - origin},
        )
        entry.update(channels=segment.channels, end_s=end - origin, rate_hz=segment.rate_hz)
        entry["rows"] += segment.rows
    return {"instruments": list(instruments.values()), "origin": origin}


def window(run_dir: Path, instrument: str, start_s: float, end_s: float, points: int) -> dict[str, Any] | None:
    """The scans of ``instrument`` between two times, thinned to about ``points``.

    A window with no more scans than points comes back as it is. A larger one
    comes back as an envelope — each bucket's lowest and highest reading per
    channel — so a spike is still there however far the view is zoomed out.
    ``None`` when the run recorded nothing from the instrument.
    """
    points = max(MIN_POINTS, min(points, MAX_POINTS))
    segments = [segment for segment in _segments(run_dir) if segment.instrument == instrument and segment.rows > 0]
    if not segments:
        return None
    origin = recordings(run_dir)["origin"]
    parts = []
    for segment in segments:
        part = _window_of(segment, origin, start_s, end_s, points)
        if part is not None:
            parts.append({**part, "channels": segment.channels, "rate_hz": segment.rate_hz})
    return {"instrument": instrument, "origin": origin, "segments": parts}


def _window_of(segment: _Segment, origin: float, start_s: float, end_s: float, points: int) -> dict[str, Any] | None:
    rows = segment.rows
    n = len(segment.channels)
    with (
        _mapped(_file(segment.stem, ".t"), "d") as times,
        _mapped(_file(segment.stem, ".v"), "f") as values,
    ):
        first = bisect.bisect_left(times, origin + start_s, 0, rows)
        last = bisect.bisect_right(times, origin + end_s, first, rows)
        count = last - first
        if count <= 0:
            return None
        if count <= points:
            return {
                "kind": "raw",
                "points": [[times[row] - origin, *values[row * n : (row + 1) * n]] for row in range(first, last)],
            }
        buckets = points // 2
        size = count / buckets
        envelope = []
        for bucket in range(buckets):
            low = first + int(bucket * size)
            high = min(last, first + int((bucket + 1) * size))
            if high <= low:
                continue
            lows = [min(values[low * n + channel : high * n : n]) for channel in range(n)]
            highs = [max(values[low * n + channel : high * n : n]) for channel in range(n)]
            envelope.append([(times[low] + times[high - 1]) / 2 - origin, *lows, *highs])
        return {"kind": "envelope", "points": envelope}
