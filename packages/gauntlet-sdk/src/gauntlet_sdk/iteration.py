"""The iteration loop that drives sampled suites.

Configure a duration, an iteration count, or neither — with neither, the loop
runs until it is stopped. The runner handles cadence, sink fan-out, and stop
handling. Ctrl-C aborts the run; a graceful stop request completes the
in-flight iteration and rolls up a verdict from the samples collected.
"""

from __future__ import annotations

import contextlib
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from gauntlet_sdk.log import err, info
from gauntlet_sdk.phases import PhaseRecord


@dataclass
class IterationContext:
    """Per-iteration state handed to the iterate callable."""

    iteration: int
    start_time: float
    elapsed_run_s: float
    deadline: float | None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class IterationOutcome:
    """What one iteration produced.

    ``summary`` is appended to the iteration's log line.
    """

    success: bool
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    phase_records: list[PhaseRecord] = field(default_factory=list)
    summary: str = ""


@dataclass
class RunResult:
    """Aggregate outcome of the whole loop."""

    total_iterations: int
    successes: int
    failures: int
    started_at: float
    ended_at: float
    aborted: bool
    abort_reason: str = ""
    stopped_early: bool = False

    @property
    def duration_s(self) -> float:
        return self.ended_at - self.started_at

    @property
    def passed(self) -> bool:
        """Default criterion: nothing failed and the run was not aborted."""
        return not self.aborted and self.failures == 0


IterateFn = Callable[[IterationContext], IterationOutcome]
SinkFn = Callable[[IterationOutcome, IterationContext], None]
EndOfRunFn = Callable[[RunResult], None]
PassCriteriaFn = Callable[[RunResult, list[IterationOutcome]], "tuple[bool, str]"]


class IterationRunner:
    """Runs an iterate callable on a fixed cadence until done or stopped.

    With neither ``max_iterations`` nor ``max_duration_s``, the loop has no end
    of its own and runs until :meth:`request_stop` or :meth:`abort`.
    """

    def __init__(
        self,
        iterate: IterateFn,
        *,
        max_iterations: int | None = None,
        max_duration_s: float | None = None,
        period_s: float = 0.0,
        cycle_delay_s: float = 0.0,
        stop_on_failure: bool = False,
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        self._iterate = iterate
        self._max_iterations = max_iterations
        self._max_duration_s = max_duration_s
        self._period_s = period_s
        self._cycle_delay_s = cycle_delay_s
        self._stop_on_failure = stop_on_failure
        self._on_start = on_start
        self._on_stop = on_stop
        self._sinks: list[SinkFn] = []
        self._end_sinks: list[EndOfRunFn] = []
        self._pass_criteria: PassCriteriaFn | None = None
        self._stopped = False
        self._stop_graceful = False
        self._abort_reason = ""

    def add_sink(self, fn: SinkFn) -> None:
        """Register a per-iteration sink, called once per outcome."""
        self._sinks.append(fn)

    def add_end_sink(self, fn: EndOfRunFn) -> None:
        """Register an end-of-run callback."""
        self._end_sinks.append(fn)

    def set_pass_criteria(self, fn: PassCriteriaFn) -> None:
        """Replace the default pass criterion with an aggregate check."""
        self._pass_criteria = fn

    def abort(self, reason: str = "manual") -> None:
        """Stop the loop and mark the run aborted."""
        self._stopped = True
        self._abort_reason = reason

    def request_stop(self) -> None:
        """Stop at the next iteration boundary without marking the run aborted."""
        self._stopped = True
        self._stop_graceful = True

    def run(self) -> tuple[RunResult, list[IterationOutcome]]:
        """Drive the loop to completion and return the result with every outcome."""
        previous = _install(signal.SIGINT, self._on_interrupt)
        outcomes: list[IterationOutcome] = []
        started = time.time()
        index = 0
        try:
            if self._on_start is not None:
                self._on_start()
            deadline = started + self._max_duration_s if self._max_duration_s else None
            if self._max_iterations is not None:
                info(f"starting {self._max_iterations} iterations @ period={self._period_s:.1f}s")
            elif self._max_duration_s is not None:
                info(f"starting duration={self._max_duration_s:.0f}s @ period={self._period_s:.1f}s")
            else:
                info(f"starting until stopped @ period={self._period_s:.1f}s")

            while not self._stopped:
                if self._max_iterations is not None and index >= self._max_iterations:
                    break
                if deadline is not None and time.time() >= deadline:
                    break
                index += 1
                ctx = IterationContext(
                    iteration=index,
                    start_time=time.time(),
                    elapsed_run_s=time.time() - started,
                    deadline=deadline,
                )
                tick_started = time.monotonic()
                try:
                    outcome = self._iterate(ctx)
                except KeyboardInterrupt:
                    self._stopped = True
                    self._abort_reason = "keyboard_interrupt"
                    break
                except Exception as exc:
                    outcome = IterationOutcome(success=False, reason=f"exception: {exc}")
                outcomes.append(outcome)
                for sink in self._sinks:
                    sink(outcome, ctx)

                elapsed = time.monotonic() - tick_started
                if outcome.success:
                    detail = f"  {outcome.summary}" if outcome.summary else ""
                    info(f"iter {index}: ok ({elapsed:.2f}s){detail}")
                else:
                    err(f"iter {index}: FAIL {outcome.reason}")
                    if self._stop_on_failure:
                        self._stopped = True
                        self._abort_reason = f"failure: {outcome.reason}"
                        break

                remaining = max(0.0, self._period_s - elapsed) if self._period_s > 0 else 0.0
                if remaining + self._cycle_delay_s > 0:
                    self._sleep(remaining + self._cycle_delay_s)
        finally:
            if self._on_stop is not None:
                with contextlib.suppress(Exception):
                    self._on_stop()
            _restore(signal.SIGINT, previous)

        result = self._finalize(outcomes, started)
        for end_sink in self._end_sinks:
            with contextlib.suppress(Exception):
                end_sink(result)
        info(
            f"done {'PASS' if result.passed else 'FAIL'} iters={result.total_iterations} "
            f"ok={result.successes} fail={result.failures} duration={result.duration_s:.1f}s"
            + (f" abort={result.abort_reason}" if result.aborted else "")
        )
        return result, outcomes

    def _finalize(self, outcomes: list[IterationOutcome], started: float) -> RunResult:
        result = RunResult(
            total_iterations=len(outcomes),
            successes=sum(1 for o in outcomes if o.success),
            failures=sum(1 for o in outcomes if not o.success),
            started_at=started,
            ended_at=time.time(),
            aborted=self._stopped and bool(self._abort_reason),
            abort_reason=self._abort_reason,
            stopped_early=self._stop_graceful,
        )
        if self._pass_criteria is None or result.aborted:
            return result
        ok, reason = self._pass_criteria(result, outcomes)
        if ok:
            return result
        # A graceful stop leaves ``aborted`` clear so the verdict reads as a
        # pass/fail over the samples collected.
        return RunResult(
            total_iterations=result.total_iterations,
            successes=result.successes,
            failures=result.failures + 1,
            started_at=result.started_at,
            ended_at=result.ended_at,
            aborted=not self._stop_graceful,
            abort_reason=f"pass_criteria: {reason}",
            stopped_early=self._stop_graceful,
        )

    def _on_interrupt(self, _signum: int, _frame: FrameType | None) -> None:
        self._stopped = True
        self._abort_reason = "keyboard_interrupt"

    def _sleep(self, seconds: float) -> None:
        """Sleep in slices so a stop request lands promptly."""
        end = time.monotonic() + seconds
        while not self._stopped:
            now = time.monotonic()
            if now >= end:
                return
            time.sleep(min(0.5, end - now))


def _install(sig: int, handler: Callable[[int, FrameType | None], None]) -> Any:
    """Install a signal handler, tolerating threads where that is not allowed."""
    try:
        return signal.signal(sig, handler)
    except (OSError, ValueError):
        return None


def _restore(sig: int, previous: Any) -> None:
    if previous is None:
        return
    with contextlib.suppress(OSError, ValueError):
        signal.signal(sig, previous)
