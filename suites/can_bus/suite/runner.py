"""CAN counter link — gap accounting on a CAN bus between the unit and the lab.

A sender on the unit emits an incrementing counter; the lab host receives from
socketcan. Each tick drains what arrived and records gaps and reordering
against the expected sequence.

Anomaly kinds: ``counter/gap``, ``counter/out_of_order``, ``link/silent``,
``sender/exited``.
"""

from __future__ import annotations

import contextlib
import queue
from dataclasses import dataclass, field
from typing import Any

from gauntlet_suite import (
    AnomalyLog,
    CounterTracker,
    IterationContext,
    IterationOutcome,
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
)
from gauntlet_suite.remote import connect, is_alive

from suite import link as link_engine
from suite.profile import CanProfile


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    tracker: CounterTracker
    target: RemoteTarget | None = None
    client: Any = None
    receiver: link_engine.CounterReceiver | None = None
    sender_channel: Any = None
    stopped_services: list[str] = field(default_factory=list)
    synthetic_next: int = 0
    silent_ticks: int = 0
    alive_at_end: bool = False


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _setup(ctx: SuiteContext) -> None:
    profile: CanProfile = ctx.profile
    state = _State(anomalies=AnomalyLog(ctx.jsonl), tracker=CounterTracker())

    if profile.driver == "mock":
        info("driver=mock — no bus, a contiguous counter stream is synthesised")
        ctx.extras["state"] = state
        return

    target = RemoteTarget.from_env(host=ctx.target)
    info(f"connecting to {target.user}@{target.host}")
    client = connect(target)
    state.target = target
    state.client = client

    timeout = profile.link.ssh_timeout_s
    if profile.services_to_stop:
        state.stopped_services = link_engine.stop_services(client, profile.services_to_stop, timeout=timeout)
        if state.stopped_services:
            info(f"stopped for the run: {', '.join(state.stopped_services)}")

    if profile.link.configure_unit:
        info(f"configuring {profile.link.unit_iface} at {profile.link.bitrate} bit/s")
        link_engine.configure_unit_interface(client, profile.link.unit_iface, profile.link.bitrate, timeout=timeout)

    receiver = link_engine.CounterReceiver(profile.link.lab_iface, profile.link.arbitration_id)
    receiver.start()
    state.receiver = receiver
    info(f"receiving on {profile.link.lab_iface}, id {hex(profile.link.arbitration_id)}")

    remote_path = link_engine.install_sender(client, profile.link.install_dir, timeout=timeout)
    state.sender_channel = link_engine.start_sender(
        client, remote_path, profile.link.unit_iface, profile.link.arbitration_id, profile.rate_hz
    )
    info(f"sender running on the unit at {profile.rate_hz:g} Hz")

    ctx.extras["state"] = state


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    if state.sender_channel is not None:
        with contextlib.suppress(Exception):
            state.sender_channel.close()
    if state.receiver is not None:
        with contextlib.suppress(Exception):
            state.receiver.stop()
    if state.client is not None:
        profile: CanProfile = ctx.profile
        with contextlib.suppress(Exception):
            link_engine.start_services(state.client, state.stopped_services, timeout=profile.link.ssh_timeout_s)
        with contextlib.suppress(Exception):
            state.client.close()
    state.alive_at_end = is_alive(state.target) if state.target is not None else True


def _drain(state: _State, profile: CanProfile) -> list[int]:
    """Every counter value that arrived since the last tick."""
    if profile.driver == "mock":
        count = max(int(profile.rate_hz * profile.sample_period_s), 1)
        values = list(range(state.synthetic_next, state.synthetic_next + count))
        state.synthetic_next += count
        return values

    assert state.receiver is not None
    values = []
    while True:
        try:
            values.append(state.receiver.values.get_nowait())
        except queue.Empty:
            return values


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: CanProfile = ctx.profile
    state = _state(ctx)

    if state.sender_channel is not None and state.sender_channel.exit_status_ready():
        state.anomalies.record(
            "sender",
            "exited",
            iteration=ictx.iteration,
            detail={"exit_status": state.sender_channel.recv_exit_status()},
        )
        state.sender_channel = None

    received = missing = out_of_order = 0
    for value in _drain(state, profile):
        observation = state.tracker.observe(value)
        if observation.event == "out_of_order":
            out_of_order += 1
            state.anomalies.record(
                "counter",
                "out_of_order",
                iteration=ictx.iteration,
                detail={"expected": observation.expected, "observed": observation.observed},
            )
            continue
        if observation.event == "gap":
            missing += observation.missing
            state.anomalies.record(
                "counter",
                "gap",
                iteration=ictx.iteration,
                detail={
                    "expected": observation.expected,
                    "observed": observation.observed,
                    "missing": observation.missing,
                },
            )
        received += 1

    # The first tick fires before the sender has put a frame on the bus.
    warming_up = ictx.iteration == 1 and state.tracker.received_total == received == 0
    silent = received == 0 and not warming_up
    if silent:
        state.silent_ticks += 1
        state.anomalies.record("link", "silent", iteration=ictx.iteration, detail={})

    return IterationOutcome(
        success=not silent,
        reason="no counter frames received" if silent else "",
        metrics={
            "received": received,
            "missing": missing,
            "out_of_order": out_of_order,
            "received_total": state.tracker.received_total,
            "missing_total": state.tracker.missing_total,
            "out_of_order_total": state.tracker.out_of_order_total,
            "loss_pct": state.tracker.loss_pct,
            "anomalies_total": state.anomalies.total(),
        },
        summary=f"recv={received} missing={missing}" if received or missing else "idle",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: CanProfile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no ticks completed"
    last = outcomes[-1].metrics
    criteria = profile.pass_criteria
    received = int(last.get("received_total", 0))
    missing = int(last.get("missing_total", 0))
    if received < criteria.min_received:
        return False, f"only {received} counter frames received, need at least {criteria.min_received}"
    if missing > criteria.max_missing:
        return False, f"{missing} counter values missing, budget is {criteria.max_missing}"
    anomalies = int(last.get("anomalies_total", 0))
    if anomalies > criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    result: RunResult,
    profile: CanProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    tracker = state.tracker
    rows = [
        make_result("ticks", "Ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "received_total",
            "Frames received",
            tracker.received_total,
            format="int",
            highlight=tracker.received_total < profile.pass_criteria.min_received,
        ),
        make_result(
            "missing_total",
            "Values missing",
            tracker.missing_total,
            format="int",
            highlight=tracker.missing_total > profile.pass_criteria.max_missing,
        ),
        make_result("loss_pct", "Loss", tracker.loss_pct, unit="%", format="decimal", precision=4),
        make_result(
            "out_of_order_total",
            "Out of order",
            tracker.out_of_order_total,
            format="int",
            highlight=tracker.out_of_order_total > 0,
        ),
        make_result("silent_ticks", "Silent ticks", state.silent_ticks, format="int", highlight=state.silent_ticks > 0),
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


def _summary(_ctx: SuiteContext, profile: CanProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "rate_hz": str(profile.rate_hz),
        "unit_iface": profile.link.unit_iface,
        "lab_iface": profile.link.lab_iface,
        "bitrate": str(profile.link.bitrate),
        "arbitration_id": hex(profile.link.arbitration_id),
    }


def _hardware(ctx: SuiteContext, profile: CanProfile) -> dict[str, dict[str, str]]:
    return {
        "uut": {"driver": profile.driver, "host": ctx.target or "", "iface": profile.link.unit_iface},
        "lab": {"iface": profile.link.lab_iface, "bitrate": str(profile.link.bitrate)},
    }


SPEC = SuiteSpec(
    name="can_bus",
    profile_model=CanProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: float(p.duration_s),
    sample_period_seconds=lambda p: float(p.sample_period_s),
    setup=_setup,
    teardown=_teardown,
    profile_summary=_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
