"""Watches a run's streaming instruments for a reading that leaves its limits.

Like :class:`~gauntlet.supervisor.recorder.InstrumentRecorder` this is
Gauntlet's and not the suite's: nothing reaches the suite process, and no
manifest declares it. It follows every instrument the run observes whose
provider streams, and it names none of them.

An operator sets a high limit, a low limit, or both on a channel. The first
scan outside a limit is an upset: the monitor keeps the scans from ``pre_s``
before it to ``post_s`` after it, writes them to ``upsets/upset_NNNN.csv``,
appends a line to ``test.log`` and publishes an ``upset`` event. A channel
makes one upset per excursion: it rearms only once the reading is back inside
its limits and its event has ended.

``upsets.json`` holds the thresholds, ``stop_after``, every event, and whether
the monitor stopped the run.
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet.capabilities import CapabilityRegistry, StreamingCapability, StreamSlice
from gauntlet.daq_recording import Recorder

log = logging.getLogger("gauntlet.supervisor.upsets")

SUMMARY_NAME = "upsets.json"
EVENTS_DIR = "upsets"

DEFAULT_WINDOW_S = 2.0
MAX_WINDOW_S = 30.0
MAX_STOP_AFTER = 1000

_POLL_S = 0.05
_RETRY_S = 3.0
_WRITE_EVERY_S = 1.0
_SLICE_LIMIT = 2000

# Scan times are sums of a period, so a window edge lands a rounding error off.
_EDGE_S = 1e-6

Scan = tuple[int, float, float, list[float | None]]


def check_stop_after(count: Any) -> None:
    """Refuse a ``stop_after`` that is not a whole number from 0 to the most allowed."""
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_STOP_AFTER:
        raise ValueError(f"stop_after must be a whole number from 0 to {MAX_STOP_AFTER}")


def check_limits(bounds: tuple[float | None, float | None]) -> None:
    """Refuse a limit that is not a finite number."""
    if not all(bound is None or math.isfinite(bound) for bound in bounds):
        raise ValueError("a limit must be a finite number")


def check_window(name: str, seconds: float | None) -> None:
    """Refuse a capture window outside 0 to the longest kept."""
    if seconds is not None and not (math.isfinite(seconds) and 0 <= seconds <= MAX_WINDOW_S):
        raise ValueError(f"{name} must be from 0 to {MAX_WINDOW_S:g} seconds")


@dataclass
class _Open:
    """An upset whose post window has not ended."""

    channel: str
    column: int
    direction: str
    limit: float
    value: float
    cross: Scan
    pre_s: float
    post_s: float
    scans: list[Scan]


@dataclass
class _Feed:
    """One followed instrument."""

    key: str
    instance_id: str
    provider: StreamingCapability
    channels: list[dict[str, Any]] = field(default_factory=list)
    limits: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    pre_s: float = DEFAULT_WINDOW_S
    post_s: float = DEFAULT_WINDOW_S
    armed: dict[str, bool] = field(default_factory=dict)
    open: list[_Open] = field(default_factory=list)
    recent: deque[Scan] = field(default_factory=deque)
    next_seq: int = 1
    leased: bool = False
    last_scan_at: float = 0.0
    retry_at: float = 0.0
    reported_down: bool = False
    recorder: Recorder | None = None


class UpsetMonitor:
    """Follows the streaming instruments of one run."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        keys: list[str],
        run_dir: Path,
        *,
        log_line: Callable[[str, str], None],
        publish: Callable[..., Any],
        stop_run: Callable[[], bool],
        initial: dict[str, Any] | None = None,
    ) -> None:
        self._initial = initial or {}
        self._log_line = log_line
        self._publish = publish
        self._run_dir = run_dir
        self._stop_run = stop_run
        self._feeds: dict[str, _Feed] = {}
        for key in keys:
            provider = registry.provider(key)
            if isinstance(provider, StreamingCapability):
                self._feeds[key] = _Feed(key, provider.instance_id(), provider, recorder=Recorder(run_dir, key))
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._stop_after = 0
        self._stopped_run = False
        self._closing = False
        self._began = 0.0
        self._dirty = False
        self._written_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Take a lease on every streaming instrument and begin watching."""
        if not self._feeds or self._thread is not None:
            return
        self._began = time.monotonic()
        self._apply_enabled()
        for feed in self._feeds.values():
            self._lease(feed)
        self._apply_initial()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gauntlet-upsets")
        self._thread.start()

    def stop(self) -> None:
        """Stop watching, end any open upset as truncated, and write the summary."""
        if self._thread is None:
            return
        self._closing = True
        self._stop.set()
        self._thread.join(timeout=_POLL_S + 5.0)
        self._thread = None
        for feed in self._feeds.values():
            self._end_open(feed, truncated=True)
            if feed.recorder is not None:
                feed.recorder.close()
            if feed.leased:
                feed.provider.stream_close()
                feed.leased = False
        self._flush(force=True)

    def channel_keys(self, instrument: str) -> list[str] | None:
        """The channels a followed instrument streams, ``None`` if it is not followed."""
        feed = self._feeds.get(instrument)
        return None if feed is None else [channel["key"] for channel in feed.channels]

    def followed(self) -> list[str]:
        """The instance keys this monitor follows."""
        return list(self._feeds)

    def set_stop_after(self, count: int) -> None:
        """Stop the run after this many upsets, never if zero."""
        check_stop_after(count)
        with self._lock:
            self._stop_after = count
            self._dirty = True

    def set_thresholds(
        self,
        instrument: str,
        limits: dict[str, tuple[float | None, float | None]],
        *,
        post_s: float | None = None,
        pre_s: float | None = None,
    ) -> None:
        """Replace one instrument's limits, as ``{channel: (low, high)}``."""
        feed = self._feeds.get(instrument)
        if feed is None:
            raise ValueError(f"{instrument!r} is not a streaming instrument this run observes")
        known = {channel["key"] for channel in feed.channels}
        for channel, bounds in limits.items():
            if channel not in known:
                raise ValueError(f"{instrument!r} has no channel {channel!r}")
            check_limits(bounds)
        check_window("pre_s", pre_s)
        check_window("post_s", post_s)
        with self._lock:
            feed.limits = {channel: bounds for channel, bounds in limits.items() if bounds != (None, None)}
            feed.pre_s = feed.pre_s if pre_s is None else pre_s
            feed.post_s = feed.post_s if post_s is None else post_s
            self._dirty = True

    def settings(self) -> dict[str, Any]:
        """The thresholds and ``stop_after`` as they stand."""
        with self._lock:
            return self._settings()

    def trace(self, instrument: str, since: int, *, display_hz: float = 0.0, tail_s: float = 0.0) -> StreamSlice | None:
        """Scans the instrument has streamed from ``since`` on, ``None`` if not followed.

        Recording is at the stream's own rate; a display cannot draw that, so
        ``display_hz`` keeps every nth scan, chosen by sequence number so two
        polls never disagree about which. ``tail_s`` starts a first request
        from the last seconds rather than the oldest scan held.
        """
        feed = self._feeds.get(instrument)
        if feed is None:
            return None
        first = tail_s > 0 and since <= 1
        streamed = feed.provider.stream_since(since, 10**9 if first else _SLICE_LIMIT)
        scans = streamed.scans
        if first and scans:
            scans = [scan for scan in scans if scan[1] >= scans[-1][1] - tail_s]
        if display_hz > 0:
            every = max(1, round(streamed.rate_hz / display_hz))
            scans = [scan for scan in scans if scan[0] % every == 0]
        return StreamSlice(streamed.channels, streamed.rate_hz, streamed.next_seq, scans)

    def _apply_enabled(self) -> None:
        """Put the channels the run was started with in or out of the stream, before it is leased."""
        for key, given in self._initial.get("instruments", {}).items():
            feed = self._feeds.get(key)
            if feed is None or not given.get("enabled"):
                continue
            try:
                feed.provider.stream_enable(given["enabled"])
            except ValueError as exc:
                self._log_line(
                    "WARN", f"DAQ monitor could not set the channels the run was started with for {key}: {exc}"
                )

    def _apply_initial(self) -> None:
        """Put the limits the run was started with in force, saying so when one cannot be."""
        try:
            if self._initial.get("stop_after") is not None:
                self.set_stop_after(self._initial["stop_after"])
        except ValueError as exc:
            self._log_line("WARN", f"DAQ monitor ignored the stop_after the run was started with: {exc}")
        for key, given in self._initial.get("instruments", {}).items():
            try:
                self.set_thresholds(
                    key, given.get("channels", {}), post_s=given.get("post_s"), pre_s=given.get("pre_s")
                )
            except ValueError as exc:
                self._log_line("WARN", f"DAQ monitor ignored the limits the run was started with for {key}: {exc}")

    def _settings(self) -> dict[str, Any]:
        return {
            "stop_after": self._stop_after,
            "thresholds": {
                key: {
                    "channels": {channel: {"high": high, "low": low} for channel, (low, high) in feed.limits.items()},
                    "post_s": feed.post_s,
                    "pre_s": feed.pre_s,
                }
                for key, feed in self._feeds.items()
            },
        }

    def _loop(self) -> None:
        while not self._stop.wait(_POLL_S):
            for feed in self._feeds.values():
                try:
                    self._follow(feed)
                except Exception:
                    log.exception("upset monitor failed following %s", feed.key)
            self._flush()

    def _lease(self, feed: _Feed) -> None:
        """Take a lease, or say once that the instrument will not stream."""
        feed.retry_at = time.monotonic() + _RETRY_S
        if not feed.provider.stream_open():
            if not feed.reported_down:
                feed.reported_down = True
                self._log_line("WARN", f"DAQ monitor cannot stream {feed.key}; retrying")
            return
        feed.leased = True
        feed.reported_down = False
        feed.last_scan_at = time.monotonic()
        streamed = feed.provider.stream_since(feed.next_seq, 1)
        feed.channels = streamed.channels
        feed.armed = {channel["key"]: True for channel in streamed.channels}

    def _follow(self, feed: _Feed) -> None:
        now = time.monotonic()
        if not feed.leased:
            if now >= feed.retry_at:
                self._lease(feed)
            return
        streamed = feed.provider.stream_since(feed.next_seq, _SLICE_LIMIT)
        if _layout(streamed.channels) != _layout(feed.channels):
            # The scan list changed under the stream, so windows in progress
            # would mix two layouts. A channel that left it keeps no limit.
            self._end_open(feed, truncated=True)
            feed.armed = {channel["key"]: True for channel in streamed.channels}
            feed.recent.clear()
            present = {channel["key"] for channel in streamed.channels}
            with self._lock:
                feed.limits = {key: bounds for key, bounds in feed.limits.items() if key in present}
                self._dirty = True
        feed.channels = streamed.channels
        if not streamed.scans:
            if now - feed.last_scan_at > max(2.0, 30.0 / max(streamed.rate_hz, 0.01)):
                self._drop(feed)
            return
        feed.last_scan_at = now
        feed.next_seq = streamed.next_seq
        self._record(feed, streamed)
        for scan in streamed.scans:
            self._take(feed, scan)

    def _record(self, feed: _Feed, streamed: StreamSlice) -> None:
        """Keep every scan, at the rate the instrument gave it, whatever the limits are."""
        if feed.recorder is None:
            return
        try:
            feed.recorder.write(streamed.channels, streamed.rate_hz, streamed.scans)
        except OSError as exc:
            self._log_line("ERROR", f"DAQ monitor could not record {feed.key}: {exc}")
            feed.recorder = None

    def _drop(self, feed: _Feed) -> None:
        """The instrument stopped answering: close what is open and try again later."""
        self._end_open(feed, truncated=True)
        self._log_line("WARN", f"DAQ monitor lost the stream from {feed.key}; retrying")
        feed.reported_down = True
        feed.provider.stream_close()
        feed.leased = False
        feed.retry_at = time.monotonic() + _RETRY_S

    def _take(self, feed: _Feed, scan: Scan) -> None:
        """Feed one new scan to the windows in progress, then look for a crossing."""
        feed.recent.append(scan)
        while feed.recent and scan[1] - feed.recent[0][1] > feed.pre_s + 1.0:
            feed.recent.popleft()
        for upset in list(feed.open):
            upset.scans.append(scan)
            if scan[1] - upset.cross[1] >= upset.post_s - _EDGE_S:
                self._finish(feed, upset, truncated=False)
        with self._lock:
            limits = dict(feed.limits)
        for column, channel in enumerate(feed.channels):
            key = channel["key"]
            bounds = limits.get(key)
            value = scan[3][column]
            if bounds is None or value is None:
                continue
            low, high = bounds
            direction, limit = None, 0.0
            if high is not None and value > high:
                direction, limit = "high", high
            elif low is not None and value < low:
                direction, limit = "low", low
            if direction is None:
                if not feed.armed.get(key, True) and not any(upset.channel == key for upset in feed.open):
                    feed.armed[key] = True
            elif feed.armed.get(key, True):
                feed.armed[key] = False
                window = [earlier for earlier in feed.recent if scan[1] - earlier[1] <= feed.pre_s + _EDGE_S]
                feed.open.append(_Open(key, column, direction, limit, value, scan, feed.pre_s, feed.post_s, window))
                if feed.post_s == 0:
                    self._finish(feed, feed.open[-1], truncated=False)

    def _end_open(self, feed: _Feed, *, truncated: bool) -> None:
        for upset in list(feed.open):
            self._finish(feed, upset, truncated=truncated)

    def _finish(self, feed: _Feed, upset: _Open, *, truncated: bool) -> None:
        """Write one upset's window and announce it."""
        feed.open.remove(upset)
        labels = [channel["label"] for channel in feed.channels]
        channel = feed.channels[upset.column]
        with self._lock:
            index = len(self._events) + 1
        name = f"upset_{index:04d}.csv"
        try:
            self._write_csv(self._run_dir / EVENTS_DIR / name, labels, upset)
        except OSError as exc:
            self._log_line("ERROR", f"DAQ monitor could not write {name}: {exc}")
            return
        event = {
            "at": datetime.fromtimestamp(upset.cross[2], tz=timezone.utc).isoformat(timespec="milliseconds"),
            "channel": upset.channel,
            "direction": upset.direction,
            "elapsed_s": round(upset.cross[1] - self._began, 3),
            "file": f"{EVENTS_DIR}/{name}",
            "index": index,
            "instance_id": feed.instance_id,
            "instrument": feed.key,
            "label": channel["label"],
            "limit": upset.limit,
            "post_s": upset.post_s,
            "pre_s": upset.pre_s,
            "truncated": truncated,
            "unit": channel["unit"],
            "value": upset.value,
        }
        with self._lock:
            self._events.append(event)
            self._dirty = True
            reached = (
                self._stop_after > 0
                and len(self._events) >= self._stop_after
                and not self._stopped_run
                and not self._closing
            )
        self._publish("upset", **event)
        unit = event["unit"]
        self._log_line(
            "WARN",
            f"DAQ event #{index} {feed.key} {event['label']} {upset.value:g}{unit} crossed {upset.direction} limit {upset.limit:g}{unit}",
        )
        if reached:
            self._stop_the_run(index)

    def _stop_the_run(self, count: int) -> None:
        with self._lock:
            self._stopped_run = True
        self._log_line("INFO", f"stopping the run after {count} DAQ events")
        if not self._stop_run():
            with self._lock:
                self._stopped_run = False
            self._log_line("INFO", "the run was already ending")

    @staticmethod
    def _write_csv(path: Path, labels: list[str], upset: _Open) -> None:
        """Write through a temporary name so a failure leaves no partial file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["t_s", *labels])
                for _, monotonic, _, values in upset.scans:
                    writer.writerow([f"{monotonic - upset.cross[1]:.4f}", *("" if v is None else v for v in values)])
            os.replace(temporary, path)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise

    def _flush(self, *, force: bool = False) -> None:
        """Rewrite ``upsets.json`` when something changed, at most once a second."""
        now = time.monotonic()
        with self._lock:
            if not self._dirty or (not force and now - self._written_at < _WRITE_EVERY_S):
                return
            body = {**self._settings(), "events": list(self._events), "stopped_run": self._stopped_run}
            self._dirty = False
        self._written_at = now
        path = self._run_dir / SUMMARY_NAME
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            self._log_line("ERROR", f"DAQ monitor could not write {SUMMARY_NAME}: {exc}")


def _layout(channels: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """What decides whether two windows can share a trace: the channels and their units, not their names."""
    return [(channel["key"], channel["unit"]) for channel in channels]
