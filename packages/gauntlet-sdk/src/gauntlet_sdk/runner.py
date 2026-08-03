"""SuiteSpec and run_suite — the declarative path to a conforming suite.

A suite describes itself as a :class:`SuiteSpec` and hands it to
:func:`run_suite`, which does everything the contract requires: resolve the
run directory, open the sinks, drive the iteration loop, and write
``manifest.json``, ``verdict.json`` and ``summary.md``. What remains for the
suite author is the ``iterate`` callable — the part that is actually specific
to what is being tested.

Suites that do not fit an iteration loop should skip this module and write the
artifacts directly; :mod:`gauntlet_sdk.reporting` is usable on its own.
"""

from __future__ import annotations

import contextlib
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gauntlet_sdk.context import SuiteContext
from gauntlet_sdk.environment import RunEnvironment, run_environment
from gauntlet_sdk.iteration import (
    IterationContext,
    IterationOutcome,
    IterationRunner,
    RunResult,
)
from gauntlet_sdk.profile import snapshot_profile, summarize_profile
from gauntlet_sdk.reporting.events_sink import EventsSink
from gauntlet_sdk.reporting.jsonl_sink import JsonlSink
from gauntlet_sdk.reporting.junit_sink import JUnitSink
from gauntlet_sdk.reporting.manifest import build_manifest, write_manifest
from gauntlet_sdk.reporting.summary import write_summary
from gauntlet_sdk.reporting.verdict import write_verdict

# Gauntlet's Stop button sends SIGUSR1: end the loop at the next boundary and
# report on what was collected. SIGTERM remains a hard abort.
_GRACEFUL_STOP_SIGNAL = getattr(signal, "SIGUSR1", None)


@dataclass
class SuiteSpec:
    """Declarative description of a sampled suite.

    ``iterate`` is the per-tick work and the only required callable. Set
    exactly one of ``duration_seconds`` or ``iteration_count`` to choose
    between a time-bounded and a cycle-bounded run.

    ``setup`` and ``teardown`` bracket the loop — open instruments in setup,
    stash handles on ``ctx.extras``, release them in teardown, which runs in a
    ``finally`` so it fires even when the run aborts.

    ``evaluate`` runs once at the end with every outcome and decides the
    aggregate verdict; returning ``None`` keeps the default of "nothing failed".
    The reporting hooks (``profile_summary``, ``hardware_summary``,
    ``verdict_results``, ``verdict_tests``) receive the live context so
    cross-iteration state reaches the written artifacts.
    """

    name: str
    profile_model: type[BaseModel]
    iterate: Callable[[SuiteContext, IterationContext], IterationOutcome]
    evaluate: Callable[[list[IterationOutcome], Any], tuple[bool, str] | None] | None = None
    duration_seconds: Callable[[Any], float] | None = None
    iteration_count: Callable[[Any], int] | None = None
    sample_period_seconds: Callable[[Any], float] = field(default=lambda p: float(getattr(p, "sample_period_s", 1.0)))
    cycle_delay_seconds: Callable[[Any], float] = field(default=lambda _p: 0.0)
    stop_on_failure: Callable[[Any], bool] = field(default=lambda _p: False)
    setup: Callable[[SuiteContext], None] | None = None
    teardown: Callable[[SuiteContext], None] | None = None
    profile_summary: Callable[[SuiteContext, Any], dict[str, str]] | None = None
    hardware_summary: Callable[[SuiteContext, Any], dict[str, dict[str, str]]] = field(default=lambda _c, _p: {})
    versions: Callable[[SuiteContext, Any], dict[str, str]] = field(default=lambda _c, _p: {})
    verdict_results: Callable[[SuiteContext, list[IterationOutcome], RunResult, Any], list[dict[str, Any]]] = field(
        default=lambda _c, _o, _r, _p: []
    )
    verdict_tests: Callable[[SuiteContext, list[IterationOutcome], Any], list[dict[str, Any]]] = field(
        default=lambda _c, _o, _p: []
    )

    def __post_init__(self) -> None:
        if self.duration_seconds is None and self.iteration_count is None:
            raise ValueError(f"SuiteSpec {self.name!r} must set duration_seconds or iteration_count")
        if self.duration_seconds is not None and self.iteration_count is not None:
            raise ValueError(f"SuiteSpec {self.name!r} must set exactly one of duration_seconds / iteration_count")


def run_suite(
    spec: SuiteSpec,
    profile: BaseModel,
    *,
    env: RunEnvironment | None = None,
    run_dir: Path | None = None,
    profile_path: Path | None = None,
    target: str | None = None,
    unit_serial: str | None = None,
) -> tuple[RunResult, Path]:
    """Execute one run end to end and return its result and run directory.

    Every artifact is on disk before this returns.
    """
    if not isinstance(profile, spec.profile_model):
        raise TypeError(f"profile is {type(profile).__name__}, expected {spec.profile_model.__name__}")

    env = env or run_environment(
        run_dir=run_dir,
        profile_path=profile_path,
        target=target,
        unit_serial=unit_serial,
        suite=spec.name,
    )
    run_dir = env.run_dir
    snapshot_profile(env.profile_path, run_dir)

    sample_period_s = float(spec.sample_period_seconds(profile))
    jsonl = JsonlSink(run_dir / "metrics.jsonl")
    events = EventsSink(run_dir / "events.sqlite")
    junit = JUnitSink(run_dir / "junit.xml", suite_name=spec.name)

    ctx = SuiteContext(
        suite_name=spec.name,
        env=env,
        profile=profile,
        jsonl=jsonl,
        events=events,
        junit=junit,
        sample_period_s=sample_period_s,
    )

    def _iterate(ictx: IterationContext) -> IterationOutcome:
        ctx.iteration_index = ictx.iteration
        return spec.iterate(ctx, ictx)

    runner_kwargs: dict[str, Any] = {
        "period_s": sample_period_s,
        "cycle_delay_s": float(spec.cycle_delay_seconds(profile)),
        "stop_on_failure": bool(spec.stop_on_failure(profile)),
    }
    if spec.duration_seconds is not None:
        runner_kwargs["max_duration_s"] = float(spec.duration_seconds(profile))
    else:
        assert spec.iteration_count is not None
        runner_kwargs["max_iterations"] = int(spec.iteration_count(profile))

    runner = IterationRunner(_iterate, **runner_kwargs)
    runner.add_sink(jsonl)
    runner.add_sink(events)
    runner.add_sink(junit)
    runner.add_end_sink(junit.bind())

    if spec.evaluate is not None:

        def _criteria(_result: RunResult, outcomes: list[IterationOutcome]) -> tuple[bool, str]:
            verdict = spec.evaluate(outcomes, profile) if spec.evaluate else None
            return verdict if verdict is not None else (True, "")

        runner.set_pass_criteria(_criteria)

    started_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    previous_handler = _install_graceful_stop(runner)
    try:
        setup_ok = False
        try:
            if spec.setup is not None:
                spec.setup(ctx)
            setup_ok = True
            result, outcomes = runner.run()
        finally:
            if setup_ok and spec.teardown is not None:
                with contextlib.suppress(Exception):
                    spec.teardown(ctx)
            for sink in (jsonl, events):
                with contextlib.suppress(Exception):
                    sink.close()

        summary_fn = spec.profile_summary
        write_manifest(
            run_dir / "manifest.json",
            build_manifest(
                suite=spec.name,
                run_id=env.run_id,
                started_at_utc=started_iso,
                target=env.target,
                unit_serial=env.unit_serial,
                profile_path=env.profile_path,
                profile_summary=summary_fn(ctx, profile) if summary_fn else summarize_profile(profile),
                hardware=spec.hardware_summary(ctx, profile),
                versions=spec.versions(ctx, profile),
            ),
        )
        write_verdict(
            run_dir / "verdict.json",
            result,
            results=spec.verdict_results(ctx, outcomes, result, profile),
            tests=spec.verdict_tests(ctx, outcomes, profile),
        )
        with contextlib.suppress(Exception):
            write_summary(run_dir, suite_name=spec.name)
        return result, run_dir
    finally:
        _restore_graceful_stop(previous_handler)


def _install_graceful_stop(runner: IterationRunner) -> Any:
    """Route the graceful-stop signal to the runner, if this thread can."""
    if _GRACEFUL_STOP_SIGNAL is None:
        return None

    def _handler(_signum: int, _frame: Any) -> None:
        runner.request_stop()

    try:
        return signal.signal(_GRACEFUL_STOP_SIGNAL, _handler)
    except (OSError, ValueError):
        return None


def _restore_graceful_stop(previous: Any) -> None:
    if _GRACEFUL_STOP_SIGNAL is None or previous is None:
        return
    with contextlib.suppress(OSError, ValueError):
        signal.signal(_GRACEFUL_STOP_SIGNAL, previous)
