"""RS422 counter link — gap accounting on a serial link to the unit.

The lab side sends ENQ probes and reads ``RADCOUNT <n>`` replies. Each tick
drains what arrived and records gaps and reordering against the expected
sequence.

Anomaly kinds: ``counter/gap``, ``counter/out_of_order``, ``link/silent``,
``link/read_error``.
"""

from __future__ import annotations

import contextlib
import queue
from dataclasses import dataclass
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
    warn,
)
from gauntlet_suite.remote import RemoteError, is_alive

from suite.link import CounterLink, LinkError
from suite.profile import Rs422Profile


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    tracker: CounterTracker
    link: CounterLink | None = None
    target: RemoteTarget | None = None
    synthetic_next: int = 0
    silent_ticks: int = 0
    alive_at_end: bool = False


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _setup(ctx: SuiteContext) -> None:
    profile: Rs422Profile = ctx.profile
    state = _State(anomalies=AnomalyLog(ctx.jsonl), tracker=CounterTracker())

    if profile.driver == "mock":
        info("driver=mock — no serial device, a contiguous counter stream is synthesised")
    else:
        link = CounterLink(
            device=profile.link.device,
            baud=profile.link.baud,
            bytesize=profile.link.bytesize,
            parity=profile.link.parity,
            stopbits=profile.link.stopbits,
            read_timeout_s=profile.link.read_timeout_s,
        )
        info(f"link open on {link.device} at {profile.link.baud} baud, probing at {profile.rate_hz:g} Hz")
        link.start(profile.rate_hz)
        state.link = link
        with contextlib.suppress(RemoteError):
            state.target = RemoteTarget.from_env(host=ctx.target)

    ctx.extras["state"] = state


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    if state.link is not None:
        with contextlib.suppress(Exception):
            state.link.stop()
    state.alive_at_end = is_alive(state.target) if state.target is not None else True


def _drain(state: _State, profile: Rs422Profile) -> list[int]:
    """Every counter value that arrived since the last tick."""
    if profile.driver == "mock":
        count = max(int(profile.rate_hz * profile.sample_period_s), 1)
        values = list(range(state.synthetic_next, state.synthetic_next + count))
        state.synthetic_next += count
        return values

    assert state.link is not None
    values = []
    while True:
        try:
            values.append(state.link.values.get_nowait())
        except queue.Empty:
            return values


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: Rs422Profile = ctx.profile
    state = _state(ctx)

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

    probes_sent = state.link.probes_sent if state.link is not None else received
    # The first tick fires before the sender has put anything on the wire, so
    # an empty tick there is warm-up rather than a silent link.
    warming_up = ictx.iteration == 1 and probes_sent == 0
    silent = received == 0 and not warming_up
    if silent:
        state.silent_ticks += 1
        state.anomalies.record("link", "silent", iteration=ictx.iteration, detail={"probes_sent": probes_sent})

    read_errors = state.link.read_errors if state.link is not None else 0
    if read_errors:
        warn(f"serial read errors: {read_errors}")

    return IterationOutcome(
        success=not silent,
        reason="no counter values received" if silent else "",
        metrics={
            "received": received,
            "missing": missing,
            "out_of_order": out_of_order,
            "received_total": state.tracker.received_total,
            "missing_total": state.tracker.missing_total,
            "out_of_order_total": state.tracker.out_of_order_total,
            "loss_pct": state.tracker.loss_pct,
            "probes_sent": probes_sent,
            "anomalies_total": state.anomalies.total(),
        },
        summary=f"recv={received} missing={missing}" if received or missing else "idle",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: Rs422Profile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no ticks completed"
    last = outcomes[-1].metrics
    criteria = profile.pass_criteria
    received = int(last.get("received_total", 0))
    missing = int(last.get("missing_total", 0))
    if received < criteria.min_received:
        return False, f"only {received} counter values received, need at least {criteria.min_received}"
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
    profile: Rs422Profile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    tracker = state.tracker
    rows = [
        make_result("ticks", "Ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "received_total",
            "Values received",
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


def _summary(_ctx: SuiteContext, profile: Rs422Profile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "rate_hz": str(profile.rate_hz),
        "device": profile.link.device,
        "baud": str(profile.link.baud),
    }


def _hardware(ctx: SuiteContext, profile: Rs422Profile) -> dict[str, dict[str, str]]:
    state = ctx.extras.get("state")
    device = state.link.device if isinstance(state, _State) and state.link is not None else profile.link.device
    return {
        "uut": {"driver": profile.driver, "host": ctx.target or ""},
        "link": {"device": device, "baud": str(profile.link.baud)},
    }


SPEC = SuiteSpec(
    name="rs422",
    profile_model=Rs422Profile,
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


__all__ = ["SPEC", "LinkError"]
