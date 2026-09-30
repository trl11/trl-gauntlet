"""Capture every configured analog input on every selected DAQ.

The selected units are configured once at setup, then scanned in turn on the
sample period until the duration is up. Each scan becomes one metrics record
with a group per unit, under the labels the profile gave the channels, so a
run measuring a 3V3 rail on two units charts `daq0.rail_3v3` and
`daq1.rail_3v3`.
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

from suite import daq
from suite.daq import DaqError, Unit
from suite.profile import DaqSelectProfile, selected_keys

# Where the units are kept for the length of the run. The mock run keeps units
# with no URL, which is what tells the iteration to synthesise its readings.
_UNITS = "units"

# The units a mock run pretends the bench holds when the profile names none.
_MOCK_UNITS = ("daq.0", "daq.1")


def _setup(ctx: SuiteContext) -> None:
    """Find the units and put every channel of each where the profile wants it."""
    profile: DaqSelectProfile = ctx.profile
    named = ", ".join(f"CH{c.channel} {c.mode} as {c.key}" for c in profile.channels)
    selected = selected_keys(profile.daqs)
    if profile.driver == "mock":
        units = [Unit(key, key.replace(".", ""), "") for key in selected or _MOCK_UNITS]
        ctx.extras[_UNITS] = units
        info(f"driver=mock — no instrument contacted, readings are synthesised: {named}")
        return

    units = daq.find_units(ctx.env.api_base, selected)
    # One exchange per unit for every channel: the instrument reloads its scan
    # list once rather than once per channel, and a bad mode in any row leaves
    # the whole unit as it was.
    rows = {c.channel: {"label": c.label, "mode": c.mode} for c in profile.channels}
    for unit in units:
        state = daq.configure(unit, rows)
        info(f"{unit.key} ({unit.instance}): {named}")
        for channel in profile.channels:
            settled = state.get(channel.channel, {})
            # The instrument is what says a channel ended up where it was put,
            # and it resolves an empty label to the channel's own number.
            if settled.get("mode") != channel.mode:
                warn(f"{unit.instance} CH{channel.channel} reports mode {settled.get('mode')!r}, not {channel.mode!r}")
    ctx.extras[_UNITS] = units


def _mock_reading(unit: str, channel_key: str, unit_of: str, elapsed_s: float, seed: int) -> float:
    """A believable reading, for a run with no instrument to ask."""
    rng = random.Random(f"{seed}:{unit}:{channel_key}")
    if unit_of == "C":
        return round(24.0 + 2.0 * math.sin(elapsed_s / 9.0) + rng.uniform(-0.15, 0.15), 4)
    return round(3.3 + 0.02 * math.sin(elapsed_s / 5.0) + rng.uniform(-0.004, 0.004), 6)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One scan of every selected unit, recorded under each unit's instance id."""
    profile: DaqSelectProfile = ctx.profile
    units: list[Unit] = ctx.extras[_UNITS]
    phases: list[PhaseRecord] = []
    metrics: dict[str, dict[str, float]] = {}
    missing: list[str] = []

    for unit in units:
        metrics[unit.instance] = {}
        with PhaseTimer(f"scan {unit.instance}", phases) as phase:
            phase.set_detail(channels=str(len(profile.channels)))
            if not unit.url:
                readings = {
                    c.channel: _mock_reading(unit.instance, c.key, c.unit, ictx.elapsed_run_s, ictx.iteration)
                    for c in profile.channels
                }
            else:
                try:
                    scanned = daq.sample(unit)
                except DaqError as exc:
                    missing.append(f"{unit.instance}: {exc}")
                    continue
                readings = {c.channel: scanned.get(c.channel, {}).get("value") for c in profile.channels}
        # A channel the unit did not return is absent from this record rather
        # than zero: a gap in the series is the truth, and a zero is a reading.
        metrics[unit.instance] = {
            c.key: readings[c.channel] for c in profile.channels if readings[c.channel] is not None
        }
        missing += [f"{unit.instance} CH{c.channel}" for c in profile.channels if readings[c.channel] is None]

    return IterationOutcome(
        success=not missing,
        reason=f"no reading from {', '.join(missing)}" if missing else "",
        metrics=metrics,
        phase_records=phases,
        summary=_summary(ctx, metrics),
    )


def _summary(ctx: SuiteContext, metrics: dict[str, dict[str, float]]) -> str:
    """The first channel of each unit, for the line the operator watches scroll."""
    units = {c.key: c.unit for c in ctx.profile.channels}
    shown = [
        f"{instance}.{key}={value:.4g}{units.get(key, '')}"
        for instance, values in metrics.items()
        for key, value in list(values.items())[:1]
    ]
    return " ".join(shown) or "no reading"


def _series(outcomes: list[IterationOutcome], instance: str, key: str) -> list[float]:
    """Every reading recorded for one channel of one unit, skipping the samples it missed."""
    return [
        value for outcome in outcomes if isinstance(value := outcome.metrics.get(instance, {}).get(key), (int, float))
    ]


def _evaluate(outcomes: list[IterationOutcome], profile: DaqSelectProfile) -> tuple[bool, str] | None:
    """A capture is good when it captured something from every channel of every unit.

    Every iteration carries a group for every unit, empty when the unit gave
    nothing, so the units are read off the outcomes.
    """
    if not outcomes:
        return False, "no samples collected"
    missed = sum(1 for outcome in outcomes if not outcome.success)
    if missed > profile.max_missed_samples:
        return False, f"{missed} of {len(outcomes)} samples missed a reading"
    instances = sorted({instance for outcome in outcomes for instance in outcome.metrics})
    silent = [
        f"{instance} {channel.key}"
        for instance in instances
        for channel in profile.channels
        if not _series(outcomes, instance, channel.key)
    ]
    if silent:
        return False, f"no reading at all from {', '.join(silent)}"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: DaqSelectProfile,
) -> list[dict[str, object]]:
    """Samples and duration, then the span each channel of each unit covered."""
    rows: list[dict[str, object]] = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    for unit in ctx.extras[_UNITS]:
        for channel in profile.channels:
            series = _series(outcomes, unit.instance, channel.key)
            if not series:
                continue
            name = f"{unit.instance} {channel.label or f'CH {channel.channel}'}"
            rows.append(
                make_result(
                    f"{unit.instance}_{channel.key}_mean",
                    f"{name} mean",
                    round(sum(series) / len(series), 4),
                    unit=channel.unit,
                    format="decimal",
                    precision=4,
                )
            )
            rows.append(
                make_result(
                    f"{unit.instance}_{channel.key}_span",
                    f"{name} min to max",
                    f"{min(series):.4g} to {max(series):.4g}",
                    unit=channel.unit,
                )
            )
    return rows


def _hardware(ctx: SuiteContext, profile: DaqSelectProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest: one entry per unit."""
    channels = ", ".join(f"CH{c.channel}={c.mode}" for c in profile.channels)
    return {
        unit.instance: {"driver": profile.driver, "instrument": unit.key, "channels": channels}
        for unit in ctx.extras.get(_UNITS, [])
    }


def _profile_summary(ctx: SuiteContext, profile: DaqSelectProfile) -> dict[str, str]:
    return {
        "daqs": profile.daqs or "every DAQ on the bench",
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "channels": ", ".join(f"CH{c.channel} {c.mode} {c.key}" for c in profile.channels),
    }


SPEC = SuiteSpec(
    name="daq_select",
    profile_model=DaqSelectProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
