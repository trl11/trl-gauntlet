"""Hardware trigger — SSH-driven GPIO pulse train.

Each iteration runs one high/low ``gpioset`` pair on the unit under test and
records whether it completed. Used to exercise a trigger line over a long
session and catch the point at which the unit stops responding.

Anomaly kinds, all against the ``trigger`` probe:

* ``command_failed`` — the remote command returned non-zero.
* ``command_timeout`` — it exceeded ``trigger.command_timeout_s``.
* ``command_error`` — the SSH transport failed while running it.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from gauntlet_sdk import (
    AnomalyLog,
    IterationContext,
    IterationOutcome,
    RemoteMonitor,
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    make_test,
)
from gauntlet_sdk.remote import CommandResult, RemoteError, connect, is_alive, run, shell_quote

from suite.profile import HardwareTriggerProfile


@dataclass
class _State:
    """State held across iterations."""

    anomalies: AnomalyLog
    target: RemoteTarget | None
    command: str
    client: Any = None
    monitor: RemoteMonitor | None = None
    runs_completed: int = 0
    last_exit_status: int | None = None
    last_duration_s: float = 0.0
    alive_at_end: bool = False
    tests: list[dict[str, Any]] = field(default_factory=list)


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _seconds(value: float) -> str:
    """Format for gpioset, which takes a bare integer for whole values."""
    return str(int(value)) if float(value).is_integer() else str(float(value))


def build_command(profile: HardwareTriggerProfile) -> str:
    """The pulse command, as run on the unit."""
    trigger = profile.trigger
    return (
        f"sudo gpioset -m time -s {_seconds(trigger.high_seconds)} {trigger.chip} {int(trigger.line)}=1; "
        f"sudo gpioset -m time -s {_seconds(trigger.low_seconds)} {trigger.chip} {int(trigger.line)}=0"
    )


def _setup(ctx: SuiteContext) -> None:
    profile: HardwareTriggerProfile = ctx.profile
    anomalies = AnomalyLog(ctx.jsonl)
    command = build_command(profile)

    if profile.driver == "mock":
        info("driver=mock — no unit contacted, pulses reported as successful")
        ctx.extras["state"] = _State(anomalies=anomalies, target=None, command=command)
        return

    target = RemoteTarget.from_env(host=ctx.target)
    info(f"connecting to {target.user}@{target.host}")
    client = connect(target)

    monitor = None
    if profile.monitor.enabled:
        monitor = RemoteMonitor(
            target,
            ctx.jsonl,
            period_s=profile.monitor.sample_period_s,
            anomalies=anomalies,
        )
        monitor.start()

    ctx.extras["state"] = _State(
        anomalies=anomalies,
        target=target,
        command=command,
        client=client,
        monitor=monitor,
    )


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    if state.monitor is not None:
        with contextlib.suppress(Exception):
            state.monitor.stop()
    if state.client is not None:
        with contextlib.suppress(Exception):
            state.client.close()
    if state.target is not None:
        state.alive_at_end = is_alive(state.target)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: HardwareTriggerProfile = ctx.profile
    state = _state(ctx)
    started = time.monotonic()
    success, reason = True, ""

    if profile.driver == "mock":
        time.sleep(min(profile.trigger.high_seconds + profile.trigger.low_seconds, 0.05))
        state.alive_at_end = True
    else:
        success, reason = _pulse(state, profile, ictx.iteration)

    if success:
        state.runs_completed += 1
    state.last_duration_s = time.monotonic() - started
    state.tests.append(
        make_test(
            f"pulse-{ictx.iteration}",
            classname="hardware_trigger",
            outcome="pass" if success else "fail",
            duration_s=state.last_duration_s,
            message=reason or "pulse completed",
        )
    )
    return IterationOutcome(
        success=success,
        reason=reason,
        metrics={
            "runs_completed": state.runs_completed,
            "last_duration_s": round(state.last_duration_s, 3),
            "last_exit_status": state.last_exit_status,
            "anomalies_total": state.anomalies.total(),
        },
        summary=f"{state.runs_completed}/{profile.runs} pulses" if profile.runs else f"{state.runs_completed} pulses",
    )


def _pulse(state: _State, profile: HardwareTriggerProfile, iteration: int) -> tuple[bool, str]:
    """Emit one pulse, classifying any failure into an anomaly kind."""
    command = f"bash -lc {shell_quote(state.command)}"
    try:
        result: CommandResult = run(state.client, command, timeout=profile.trigger.command_timeout_s)
    except TimeoutError as exc:
        state.last_exit_status = None
        state.anomalies.record("trigger", "command_timeout", iteration=iteration, detail={"error": str(exc)})
        return False, str(exc)
    except (RemoteError, OSError) as exc:
        state.last_exit_status = None
        reason = f"{type(exc).__name__}: {exc}"
        state.anomalies.record("trigger", "command_error", iteration=iteration, detail={"error": reason})
        return False, reason

    state.last_exit_status = result.exit_status
    if result.ok:
        return True, ""
    state.anomalies.record(
        "trigger",
        "command_failed",
        iteration=iteration,
        detail={
            "exit_status": result.exit_status,
            "stdout": result.stdout.strip()[-400:],
            "stderr": result.stderr.strip()[-400:],
        },
    )
    return False, f"remote command exited {result.exit_status}"


def _evaluate(outcomes: list[IterationOutcome], profile: HardwareTriggerProfile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no pulses were emitted"
    failures = sum(1 for o in outcomes if not o.success)
    if failures > profile.pass_criteria.max_failures:
        return False, f"{failures} failed pulses exceeds budget of {profile.pass_criteria.max_failures}"
    anomalies = int(outcomes[-1].metrics.get("anomalies_total", 0))
    if anomalies > profile.pass_criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {profile.pass_criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    _result: RunResult,
    profile: HardwareTriggerProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    failed = profile.runs - state.runs_completed
    rows = [
        make_result("runs_requested", "Pulses requested", profile.runs, format="int"),
        make_result(
            "runs_completed",
            "Pulses completed",
            state.runs_completed,
            format="int",
            highlight=state.runs_completed < profile.runs,
        ),
        make_result(
            "failures",
            "Failed pulses",
            failed,
            format="int",
            highlight=failed > profile.pass_criteria.max_failures,
        ),
        make_result(
            "anomalies_total",
            "Total anomalies",
            state.anomalies.total(),
            format="int",
            highlight=state.anomalies.total() > profile.pass_criteria.max_anomalies,
        ),
        make_result(
            "alive_at_end",
            "Unit alive at end",
            "yes" if state.alive_at_end else "no",
            highlight=not state.alive_at_end and profile.pass_criteria.require_alive_at_end,
        ),
    ]
    rows += [
        make_result(f"anomalies.{probe}", f"Anomalies — {probe}", count, format="int", highlight=count > 0)
        for probe, count in sorted(state.anomalies.counts().items())
    ]
    return rows


def _tests(ctx: SuiteContext, _outcomes: list[IterationOutcome], _profile: Any) -> list[dict[str, Any]]:
    return list(_state(ctx).tests)


def _summary(ctx: SuiteContext, profile: HardwareTriggerProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "runs": str(profile.runs),
        "chip": profile.trigger.chip,
        "line": str(profile.trigger.line),
        "pulse": f"{_seconds(profile.trigger.high_seconds)}s high / {_seconds(profile.trigger.low_seconds)}s low",
        "command": build_command(profile),
    }


def _hardware(ctx: SuiteContext, profile: HardwareTriggerProfile) -> dict[str, dict[str, str]]:
    return {"uut": {"driver": profile.driver, "host": ctx.target or ""}}


SPEC = SuiteSpec(
    name="hardware_trigger",
    profile_model=HardwareTriggerProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    iteration_count=lambda p: int(p.runs),
    sample_period_seconds=lambda _p: 0.0,
    cycle_delay_seconds=lambda p: float(p.cycle_delay_s),
    setup=_setup,
    teardown=_teardown,
    profile_summary=_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
    verdict_tests=_tests,
)
