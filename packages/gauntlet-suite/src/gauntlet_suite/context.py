"""State threaded through every iteration of a run.

One instance per run, passed to every ``iterate`` call. Suites stash their own
long-lived handles — an open instrument, a running aggregator — in
:attr:`SuiteContext.extras` during ``setup`` and read them back per tick,
rather than reaching for module-level globals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_suite.environment import RunEnvironment
from gauntlet_suite.reporting.events_sink import EventsSink
from gauntlet_suite.reporting.jsonl_sink import JsonlSink
from gauntlet_suite.reporting.junit_sink import JUnitSink


@dataclass
class SuiteContext:
    """Long-lived per-run state."""

    suite_name: str
    env: RunEnvironment
    profile: Any
    jsonl: JsonlSink
    events: EventsSink
    junit: JUnitSink
    sample_period_s: float
    started_at_monotonic: float = field(default_factory=time.monotonic)
    iteration_index: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def run_dir(self) -> Path:
        return self.env.run_dir

    @property
    def run_id(self) -> str:
        return self.env.run_id

    @property
    def target(self) -> str | None:
        """Address of the unit under test, when the run named one."""
        return self.env.target

    @property
    def elapsed_run_s(self) -> float:
        """Seconds since the runner took control."""
        return time.monotonic() - self.started_at_monotonic

    def artifact(self, *parts: str) -> Path:
        """Resolve a path inside the run directory, creating parent dirs."""
        path = self.run_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
