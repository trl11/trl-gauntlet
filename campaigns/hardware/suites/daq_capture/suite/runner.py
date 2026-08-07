"""Capture every configured analog input for the length of the run.

The channels are configured once at setup, then scanned on the sample period
until the duration is up. Each scan becomes one metrics record, under the
labels the profile gave the channels, so a run that measures a 3V3 rail charts
`daq.rail_3v3` rather than `daq.channels.1.value`.
"""

from __future__ import annotations

import math
import random

from gauntlet_sdk import (
    IterationContext,
    IterationOutcome,
    PhaseRecord,
    PhaseTimer,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    warn,
)

from suite.daq import Daq, DaqError
from suite.profile import DaqCaptureProfile

# Where the granted instrument is kept for the length of the run. None for a
# mock run, which contacts nothing.
_DAQ = "daq"


def _setup(ctx: SuiteContext) -> None:
    """Take the instrument and put every channel where the profile wants it."""
    profile: DaqCaptureProfile = ctx.profile
    named = ", ".join(f"CH{c.channel} {c.mode} as {c.key}" for c in profile.channels)
    if profile.driver == "mock":
        info(f"driver=mock — no instrument contacted, readings are synthesised: {named}")
        ctx.extras[_DAQ] = None
        return

    granted = ctx.env.capability("daq")
    daq = Daq(granted.url)
    # One exchange for every channel: the instrument reloads its scan list once
    # rather than once per channel, and a bad mode in any row leaves the whole
    # unit as it was.
    rows = {c.channel: {"label": c.label, "mode": c.mode} for c in profile.channels}
    state = daq.configure(rows)["channels"]
    ctx.extras[_DAQ] = daq
    info(f"{granted.instance_id}: {named}")
    for channel in profile.channels:
        settled = state.get(channel.channel, {})
        # The instrument is what says a channel ended up where it was put, and
        # it resolves an empty label to the channel's own number.
        if settled.get("mode") != channel.mode:
            warn(f"CH{channel.channel} reports mode {settled.get('mode')!r}, not {channel.mode!r}")


def _mock_reading(channel_key: str, unit: str, elapsed_s: float, seed: int) -> float:
    """A believable reading, for a run with no instrument to ask."""
    rng = random.Random(f"{seed}:{channel_key}")
    if unit == "C":
        return round(24.0 + 2.0 * math.sin(elapsed_s / 9.0) + rng.uniform(-0.15, 0.15), 4)
    return round(3.3 + 0.02 * math.sin(elapsed_s / 5.0) + rng.uniform(-0.004, 0.004), 6)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One scan of every configured channel, recorded under its label."""
    profile: DaqCaptureProfile = ctx.profile
    daq: Daq | None = ctx.extras.get(_DAQ)
    phases: list[PhaseRecord] = []

    with PhaseTimer("scan", phases) as phase:
        phase.set_detail(channels=str(len(profile.channels)))
        if daq is None:
            readings = {
                c.channel: _mock_reading(c.key, c.unit, ictx.elapsed_run_s, ictx.iteration) for c in profile.channels
            }
        else:
            try:
                scanned = daq.sample()
            except DaqError as exc:
                return IterationOutcome(
                    success=False,
                    reason=str(exc),
                    metrics={},
                    phase_records=phases,
                    summary="no scan",
                )
            readings = {c.channel: scanned.get(c.channel, {}).get("value") for c in profile.channels}

    # A channel the unit did not return is absent from this record rather than
    # zero: a gap in the series is the truth, and a zero is a reading.
    values = {c.key: readings[c.channel] for c in profile.channels if readings[c.channel] is not None}
    missing = [f"CH{c.channel}" for c in profile.channels if readings[c.channel] is None]

    return IterationOutcome(
        success=not missing,
        reason=f"no reading from {', '.join(missing)}" if missing else "",
        # Nested under the instrument, so the flattened names come out as
        # `daq.<label>` and the frontend groups the whole unit together.
        metrics={"daq": values},
        phase_records=phases,
        summary=_summary(ctx, values),
    )


def _summary(ctx: SuiteContext, values: dict[str, float]) -> str:
    """The first channel or two, for the line the operator watches scroll."""
    units = {c.key: c.unit for c in ctx.profile.channels}
    shown = [f"{key}={value:.4g}{units.get(key, '')}" for key, value in list(values.items())[:2]]
    return " ".join(shown) or "no reading"


def _series(outcomes: list[IterationOutcome], key: str) -> list[float]:
    """Every reading recorded for one channel, skipping the samples it missed."""
    return [value for outcome in outcomes if isinstance(value := outcome.metrics.get("daq", {}).get(key), (int, float))]


def _evaluate(outcomes: list[IterationOutcome], profile: DaqCaptureProfile) -> tuple[bool, str] | None:
    """A capture is good when it captured something from every channel."""
    if not outcomes:
        return False, "no samples collected"
    missed = sum(1 for outcome in outcomes if not outcome.success)
    if missed > profile.max_missed_samples:
        return False, f"{missed} of {len(outcomes)} samples missed a reading"
    silent = [channel.key for channel in profile.channels if not _series(outcomes, channel.key)]
    if silent:
        return False, f"no reading at all from {', '.join(silent)}"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: DaqCaptureProfile,
) -> list[dict[str, object]]:
    """Samples and duration, then the span each channel covered."""
    rows: list[dict[str, object]] = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    for channel in profile.channels:
        series = _series(outcomes, channel.key)
        if not series:
            continue
        name = channel.label or f"CH {channel.channel}"
        rows.append(
            make_result(
                f"{channel.key}_mean",
                f"{name} mean",
                round(sum(series) / len(series), 4),
                unit=channel.unit,
                format="decimal",
                precision=4,
            )
        )
        rows.append(
            make_result(
                f"{channel.key}_span",
                f"{name} min to max",
                f"{min(series):.4g} to {max(series):.4g}",
                unit=channel.unit,
            )
        )
    return rows


def _hardware(ctx: SuiteContext, profile: DaqCaptureProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    granted = ctx.extras.get(_DAQ)
    return {
        "daq": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["daq"].instance_id if granted is not None else "",
            "channels": ", ".join(f"CH{c.channel}={c.mode}" for c in profile.channels),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: DaqCaptureProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "channels": ", ".join(f"CH{c.channel} {c.mode} {c.key}" for c in profile.channels),
    }


SPEC = SuiteSpec(
    name="daq_capture",
    profile_model=DaqCaptureProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
