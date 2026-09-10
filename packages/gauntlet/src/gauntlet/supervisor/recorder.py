"""Records what the bench's instruments read while a run is in flight.

Every instrument a suite requires is recorded, and the operator may add any
other the bench has. Recording is Gauntlet's, not the suite's: nothing here
reaches the suite process, no manifest declares it, and a suite cannot tell
whether it happened.

Two files land in the run directory. ``instruments.jsonl`` is the trace, one
line per instrument per tick, and ``instruments.json`` is the summary written
when the run ends: every reading's count, extremes, mean and last value.

A reading is any number a provider publishes in ``state()``, found by walking
it rather than by knowing any instrument, so a provider that declares nothing
is still recorded. What a provider declares in ``readouts()`` supplies the
label, unit and precision the summary carries.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet.capabilities import (
    CapabilityRegistry,
    PresentableCapability,
    current_state,
)

log = logging.getLogger("gauntlet.supervisor.recorder")

# Seconds between ticks. The operator's panel polls state at about this rate
# while a run is in flight, so recording asks nothing of an instrument that
# watching it already does.
_INTERVAL_S = 1.0

TRACE_NAME = "instruments.jsonl"
SUMMARY_NAME = "instruments.json"


class InstrumentRecorder:
    """Samples a set of instruments for the duration of one run."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        keys: list[str],
        run_dir: Path,
        *,
        interval_s: float = _INTERVAL_S,
    ) -> None:
        self._registry = registry
        self._keys = list(keys)
        self._run_dir = run_dir
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # The summary is written by whoever stopped the recorder, which is not
        # the thread filling these in, and a provider slow to answer can leave
        # the two overlapping.
        self._lock = threading.Lock()
        self._readings: dict[str, dict[str, _Series]] = {key: {} for key in self._keys}
        self._ticks = 0
        self._began = 0.0

    def start(self) -> None:
        """Begin sampling on a background thread."""
        if not self._keys or self._thread is not None:
            return
        self._began = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gauntlet-instruments")
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling, take one last reading, and write the summary."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self._interval_s + 2.0)
        self._thread = None
        self._write_summary()

    def _loop(self) -> None:
        path = self._run_dir / TRACE_NAME
        try:
            trace = path.open("a", encoding="utf-8")
        except OSError:
            log.warning("cannot record instruments to %s", path)
            return
        with trace:
            while True:
                for line in self._tick():
                    trace.write(json.dumps(line) + "\n")
                trace.flush()
                if self._stop.wait(self._interval_s):
                    # One last reading, so a run short enough to finish inside
                    # a single interval still records something.
                    for line in self._tick():
                        trace.write(json.dumps(line) + "\n")
                    trace.flush()
                    return

    def _tick(self) -> list[dict[str, Any]]:
        """One reading of every instrument, as the lines to write."""
        self._ticks += 1
        elapsed = round(time.monotonic() - self._began, 3)
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = []
        for key in self._keys:
            values = self._read(key)
            if not values:
                continue
            with self._lock:
                for name, value in values.items():
                    self._readings[key].setdefault(name, _Series()).add(value)
            lines.append({"at": at, "instrument": key, "t": elapsed, "values": values})
        return lines

    def _read(self, key: str) -> dict[str, float]:
        """Every number one instrument publishes, or nothing if it did not answer.

        An instrument may be unplugged mid-run, and reading it is incidental to
        the test, so one that stops answering stops contributing rather than
        ending the run.
        """
        provider = self._registry.provider(key)
        if provider is None:
            return {}
        try:
            return numbers(current_state(provider))
        except Exception:
            log.warning("instrument %s did not answer while recording", key, exc_info=True)
            return {}

    def _write_summary(self) -> None:
        summary = {
            "interval_s": self._interval_s,
            "ticks": self._ticks,
            "instruments": [self._summarize(key) for key in self._keys],
        }
        with contextlib.suppress(OSError):
            (self._run_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2) + "\n")

    def _summarize(self, key: str) -> dict[str, Any]:
        provider = self._registry.provider(key)
        detail = provider.describe() if provider is not None else {}
        declared = _declared(provider)
        with self._lock:
            recorded = dict(self._readings[key])
        readings = []
        for name in sorted(recorded):
            series = recorded[name]
            meta = declared.get(name, {})
            readings.append(
                {
                    "key": name,
                    "label": meta.get("label") or name,
                    "unit": meta.get("unit", ""),
                    "precision": meta.get("precision"),
                    "group": meta.get("group", ""),
                    **series.summary(),
                }
            )
        return {
            "name": key,
            "kind": detail.get("kind", ""),
            "description": detail.get("description", ""),
            "readings": readings,
        }


class _Series:
    """Running count, extremes, mean and last value of one reading."""

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.minimum = 0.0
        self.maximum = 0.0
        self.last = 0.0

    def add(self, value: float) -> None:
        if self.count == 0:
            self.minimum = value
            self.maximum = value
        else:
            self.minimum = min(self.minimum, value)
            self.maximum = max(self.maximum, value)
        self.count += 1
        self.total += value
        self.last = value

    def summary(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "mean": self.total / self.count if self.count else 0.0,
            "last": self.last,
        }


def numbers(state: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    """Every number in a provider's state, keyed by its dotted path.

    A boolean is a number here, recorded as 0 or 1, so a supply's output or a
    camera's stream is in the trace beside the readings it explains.
    """
    found: dict[str, float] = {}
    for key, value in state.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            found.update(numbers(value, f"{path}."))
        elif isinstance(value, (int, float)):
            found[path] = float(value)
    return found


def _declared(provider: Any) -> dict[str, dict[str, Any]]:
    """How the provider asks each of its readings to be labelled, by key."""
    if not isinstance(provider, PresentableCapability):
        return {}
    return {entry["key"]: entry for entry in provider.readouts() if "key" in entry}
