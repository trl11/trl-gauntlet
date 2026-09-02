"""Piezo motion — repeated extend-and-return cycles driven over MQTT.

Every axis is homed at setup. Each cycle drives to the extended position,
dwells there, and returns home, recording the sample captured at the extended
endpoint so position and temperature reflect the loaded position.

Anomaly kinds: ``motion/target_not_reached``, ``motion/no_telemetry``,
``hardware/voltage_error``, ``hardware/overheat``, ``hardware/x_limit``,
``thermal/over_temperature``.
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
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    make_test,
)
from gauntlet_sdk.remote import RemoteError, is_alive

from suite.controller import ControllerError, PiezoController, Sample
from suite.profile import Axis, PiezoProfile


@dataclass
class _AxisState:
    """Per-axis accumulators."""

    moves: int = 0
    missed_targets: int = 0
    positions: list[int] = field(default_factory=list)
    last_temperature_c: float | None = None


@dataclass
class _State:
    """State held across cycles."""

    anomalies: AnomalyLog
    axes: dict[str, _AxisState] = field(default_factory=dict)
    controller: PiezoController | None = None
    target: RemoteTarget | None = None
    tests: list[dict[str, Any]] = field(default_factory=list)
    alive_at_end: bool = False


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _setup(ctx: SuiteContext) -> None:
    profile: PiezoProfile = ctx.profile
    state = _State(anomalies=AnomalyLog(ctx.jsonl))
    state.axes = {axis.name: _AxisState() for axis in profile.motion.axes}

    if profile.driver == "mock":
        info("driver=mock — no controller contacted, motion telemetry is synthesised")
        ctx.extras["state"] = state
        return

    target = RemoteTarget.from_env(host=ctx.target)
    state.target = target
    info(f"connecting to mqtt://{target.host}:{profile.mqtt.port}")
    controller = PiezoController(
        target.host,
        profile.mqtt.port,
        keepalive_s=profile.mqtt.keepalive_s,
        connect_timeout_s=profile.mqtt.connect_timeout_s,
    )
    state.controller = controller

    for axis in profile.motion.axes:
        controller.subscribe(axis.serial, axis.axis)
    for axis in profile.motion.axes:
        info(f"{axis.name}: homing to {axis.home_usteps}")
        controller.move(axis.serial, axis.axis, axis.home_usteps, axis.speed_hz)
        if (
            controller.wait_for_target(
                axis.serial, axis.axis, axis.home_usteps, timeout_s=profile.motion.move_timeout_s
            )
            is None
        ):
            raise ControllerError(f"{axis.name} did not reach home within {profile.motion.move_timeout_s:g}s")

    ctx.extras["state"] = state


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    profile: PiezoProfile = ctx.profile
    if state.controller is not None:
        # Leave the stage retracted so the rig is in a known position.
        for axis in profile.motion.axes:
            with contextlib.suppress(Exception):
                state.controller.move(axis.serial, axis.axis, axis.home_usteps, axis.speed_hz)
        with contextlib.suppress(Exception):
            state.controller.close()
    if state.target is not None:
        with contextlib.suppress(RemoteError):
            state.alive_at_end = is_alive(state.target)
    else:
        state.alive_at_end = True


def _synth_sample(position: int, cycle: int) -> Sample:
    """Telemetry for the mock driver."""
    return Sample(
        received_at=time.time(),
        payload={"position": position, "target_reached": True, "temperature": 31.0 + (cycle % 7) * 0.4},
    )


def _drive(
    state: _State,
    profile: PiezoProfile,
    axis: Axis,
    position: int,
    iteration: int,
) -> Sample | None:
    """Move one axis and wait for it to arrive."""
    axis_state = state.axes[axis.name]
    axis_state.moves += 1

    if profile.driver == "mock":
        return _synth_sample(position, iteration)

    assert state.controller is not None
    state.controller.move(axis.serial, axis.axis, position, axis.speed_hz)
    sample = state.controller.wait_for_target(axis.serial, axis.axis, position, timeout_s=profile.motion.move_timeout_s)
    if sample is None:
        axis_state.missed_targets += 1
        latest = state.controller.latest(axis.serial, axis.axis)
        if latest is None:
            state.anomalies.record(
                "motion", "no_telemetry", iteration=iteration, detail={"axis": axis.name, "target": position}
            )
        else:
            state.anomalies.record(
                "motion",
                "target_not_reached",
                iteration=iteration,
                detail={"axis": axis.name, "target": position, "observed": latest.position},
            )
    return sample


def _check_sample(state: _State, profile: PiezoProfile, axis: Axis, sample: Sample, iteration: int) -> None:
    """Record hardware faults and over-temperature from one sample."""
    axis_state = state.axes[axis.name]
    if sample.position is not None:
        axis_state.positions.append(sample.position)
    axis_state.last_temperature_c = sample.temperature_c

    for fault in sample.faults():
        state.anomalies.record("hardware", fault, iteration=iteration, detail={"axis": axis.name})

    limit = profile.motion.max_temperature_c
    if limit is not None and sample.temperature_c is not None and sample.temperature_c > limit:
        state.anomalies.record(
            "thermal",
            "over_temperature",
            iteration=iteration,
            detail={"axis": axis.name, "observed_c": sample.temperature_c, "limit_c": limit},
        )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: PiezoProfile = ctx.profile
    state = _state(ctx)
    started = time.monotonic()
    metrics: dict[str, Any] = {}
    failed: list[str] = []

    for axis in profile.motion.axes:
        extended = _drive(state, profile, axis, axis.extended_usteps, ictx.iteration)
        if extended is None:
            failed.append(f"{axis.name}: did not reach extended position")
        else:
            _check_sample(state, profile, axis, extended, ictx.iteration)
            metrics[axis.name] = {
                "position": extended.position,
                "temperature_c": extended.temperature_c,
                "missed_targets": state.axes[axis.name].missed_targets,
            }

        if profile.motion.extended_dwell_s:
            time.sleep(profile.motion.extended_dwell_s)

        if _drive(state, profile, axis, axis.home_usteps, ictx.iteration) is None:
            failed.append(f"{axis.name}: did not return home")

    metrics["anomalies_total"] = state.anomalies.total()
    duration = time.monotonic() - started
    state.tests.append(
        make_test(
            f"cycle-{ictx.iteration}",
            classname="piezo",
            outcome="pass" if not failed else "fail",
            duration_s=duration,
            message="; ".join(failed) or "cycle completed",
        )
    )
    return IterationOutcome(
        success=not failed,
        reason="; ".join(failed),
        metrics=metrics,
        summary=f"cycle {ictx.iteration}/{profile.cycles}" if profile.cycles else f"cycle {ictx.iteration}",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: PiezoProfile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no cycles completed"
    anomalies = int(outcomes[-1].metrics.get("anomalies_total", 0))
    if anomalies > profile.pass_criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {profile.pass_criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    result: RunResult,
    profile: PiezoProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    total_missed = sum(a.missed_targets for a in state.axes.values())
    rows = [
        make_result("cycles_requested", "Cycles requested", profile.cycles, format="int"),
        make_result(
            "cycles_completed",
            "Cycles completed",
            result.successes,
            format="int",
            highlight=result.successes < profile.cycles,
        ),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "missed_targets",
            "Missed targets",
            total_missed,
            format="int",
            highlight=total_missed > profile.pass_criteria.max_missed_targets,
        ),
    ]
    for axis in profile.motion.axes:
        axis_state = state.axes[axis.name]
        rows.append(make_result(f"{axis.name}.moves", f"{axis.name} — moves", axis_state.moves, format="int"))
        if axis_state.last_temperature_c is not None:
            rows.append(
                make_result(
                    f"{axis.name}.temperature_c",
                    f"{axis.name} — last temperature",
                    round(axis_state.last_temperature_c, 1),
                    unit="C",
                    format="decimal",
                    precision=1,
                )
            )
    rows += [
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


def _summary(_ctx: SuiteContext, profile: PiezoProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "cycles": str(profile.cycles),
        "axes": ", ".join(a.name for a in profile.motion.axes),
        "extended_dwell_s": str(profile.motion.extended_dwell_s),
        "mqtt_port": str(profile.mqtt.port),
    }


def _hardware(ctx: SuiteContext, profile: PiezoProfile) -> dict[str, dict[str, str]]:
    hardware = {"uut": {"driver": profile.driver, "host": ctx.target or ""}}
    hardware.update(
        {
            a.name: {"serial": a.serial, "axis": str(a.axis), "extended_usteps": str(a.extended_usteps)}
            for a in profile.motion.axes
        }
    )
    return hardware


SPEC = SuiteSpec(
    name="piezo",
    profile_model=PiezoProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    iteration_count=lambda p: int(p.cycles),
    sample_period_seconds=lambda _p: 0.0,
    cycle_delay_seconds=lambda p: float(p.cycle_delay_s),
    setup=_setup,
    teardown=_teardown,
    profile_summary=_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
    verdict_tests=_tests,
)
