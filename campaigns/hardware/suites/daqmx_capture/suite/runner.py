"""Capture the module's analog inputs for the length of the run.

The channels are configured once at setup, then acquired on the sample period
until the duration is up. Each acquisition becomes one metrics record, under
the labels the channels carry, so a run that measures a 3V3 rail charts
`daq.rail_3v3` rather than `daq.channels.ai0.value`.

Every channel the module has is recorded, not only the ones the profile lists.
The module converts all of them at once whatever the profile says, so leaving
one out of the record would throw away a reading already taken — and an input
nobody named is exactly where an unexpected signal turns up. What the profile
lists is what the run configures and what its verdict is taken over; an
unlisted channel is recorded under whatever the module calls it.

A floating input on a delta-sigma module does not read zero: it pins at the
over-range rail and sits there, unmoving, which is why a channel whose readings
never change at all is called out in the verdict. It looks like a broken driver
and it is an unwired input.
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
from suite.profile import DaqmxCaptureProfile, metric_key

# Where the granted instrument is kept for the length of the run. None for a
# mock run, which contacts nothing.
_DAQ = "daq"

# What each recorded series is called, by metric name, filled in as the run
# goes. A channel the profile did not list is named by the module, so the
# names are not known until it has been asked.
_NAMES = "names"


def _setup(ctx: SuiteContext) -> None:
    """Take the module and put every channel where the profile wants it."""
    profile: DaqmxCaptureProfile = ctx.profile
    named = ", ".join(f"{c.channel} {c.mode or 'as-is'} as {c.key}" for c in profile.channels)
    if profile.driver == "mock":
        info(f"driver=mock — no instrument contacted, readings are synthesised: {named}")
        ctx.extras[_DAQ] = None
        return

    granted = ctx.env.capability("daq")
    daq = Daq(granted.url)
    # One exchange for every channel: a bad range in any row leaves the whole
    # module as it was rather than half configured. A row carries the range
    # only when the profile named one, so a module with a single fixed range
    # needs no profile written for it.
    rows = {c.channel: ({"label": c.label, "mode": c.mode} if c.mode else {"label": c.label}) for c in profile.channels}
    state = daq.configure(rows)["channels"]
    ctx.extras[_DAQ] = daq
    info(f"{granted.instance_id}: {named}")
    for channel in profile.channels:
        settled = state.get(channel.channel, {})
        # The module is what says a channel ended up where it was put, and it
        # resolves an empty label to the channel's own name.
        if channel.mode and settled.get("mode") != channel.mode:
            warn(f"{channel.channel} reports range {settled.get('mode')!r}, not {channel.mode!r}")


def _mock_reading(channel_key: str, elapsed_s: float, seed: int) -> float:
    """A believable reading, for a run with no module to ask."""
    rng = random.Random(f"{seed}:{channel_key}")
    return round(0.05 + 0.002 * math.sin(elapsed_s / 5.0) + rng.uniform(-0.0002, 0.0002), 6)


def _named(profile: DaqmxCaptureProfile, acquired: dict[str, dict[str, object]]) -> dict[str, tuple[str, str]]:
    """The metric name and label to record each acquired channel under.

    The profile has the first word on the channels it lists, and the module
    names the rest with whatever label it is carrying. A name already taken
    falls back to the channel itself, so two labels that fold to one metric
    name cannot quietly overwrite one series with the other.
    """
    listed = {channel.channel: channel for channel in profile.channels}
    named: dict[str, tuple[str, str]] = {}
    taken: set[str] = set()
    for name in list(listed) + [name for name in acquired if name not in listed]:
        channel = listed.get(name)
        label = str(acquired.get(name, {}).get("label") or "") if channel is None else channel.label
        key = channel.key if channel is not None else metric_key(label, name)
        if key in taken:
            key = name
        if key in taken:
            continue
        taken.add(key)
        named[name] = (key, label or name)
    return named


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One acquisition of the module, every channel recorded under its label."""
    profile: DaqmxCaptureProfile = ctx.profile
    daq: Daq | None = ctx.extras.get(_DAQ)
    phases: list[PhaseRecord] = []

    with PhaseTimer("acquire", phases) as phase:
        if daq is None:
            # Nothing was asked, so nothing but the profile says what exists.
            acquired: dict[str, dict[str, object]] = {
                c.channel: {"label": c.label, "value": _mock_reading(c.key, ictx.elapsed_run_s, ictx.iteration)}
                for c in profile.channels
            }
        else:
            try:
                acquired = daq.sample()
            except DaqError as exc:
                return IterationOutcome(
                    success=False,
                    reason=str(exc),
                    metrics={},
                    phase_records=phases,
                    summary="no acquisition",
                )
        named = _named(profile, acquired)
        phase.set_detail(channels=str(len(named)))

    names: dict[str, str] = ctx.extras.setdefault(_NAMES, {})
    values = {}
    for channel, (key, label) in named.items():
        reading = acquired.get(channel, {}).get("value")
        names[key] = label
        # A channel the module did not return is absent from this record rather
        # than zero: a gap in the series is the truth, and a zero is a reading.
        if isinstance(reading, (int, float)):
            values[key] = float(reading)

    # Only the channels the profile asked for decide whether the sample was
    # good. One it did not name is recorded when it arrives and not missed
    # when it does not.
    missing = [c.channel for c in profile.channels if c.key not in values]
    faults = [fault for c in profile.channels if c.key in values and (fault := c.fault(values[c.key]))]

    return IterationOutcome(
        success=not missing and not faults,
        reason=f"no reading from {', '.join(missing)}" if missing else "; ".join(faults),
        # Nested under the instrument, so the flattened names come out as
        # `daq.<label>` and the frontend groups the whole module together.
        metrics={"daq": values},
        phase_records=phases,
        summary=_summary(values),
    )


def _summary(values: dict[str, float]) -> str:
    """The first channel or two, for the line the operator watches scroll."""
    shown = [f"{key}={value:.6g}V" for key, value in list(values.items())[:2]]
    return " ".join(shown) or "no reading"


def _series(outcomes: list[IterationOutcome], key: str) -> list[float]:
    """Every reading recorded for one channel, skipping the samples it missed."""
    return [value for outcome in outcomes if isinstance(value := outcome.metrics.get("daq", {}).get(key), (int, float))]


def _evaluate(outcomes: list[IterationOutcome], profile: DaqmxCaptureProfile) -> tuple[bool, str] | None:
    """A capture is good when every channel read, read in range, and moved.

    The last of those is what a 24-bit converter makes possible: a real input
    carries at least converter noise, so a series identical to the microvolt
    across the whole run is not a quiet signal but an input with nothing on it.
    """
    if not outcomes:
        return False, "no samples collected"
    unusable = [outcome for outcome in outcomes if not outcome.success]
    if len(unusable) > profile.max_missed_samples:
        return False, f"{len(unusable)} of {len(outcomes)} samples were not usable: {unusable[0].reason}"
    silent = [channel.key for channel in profile.channels if not _series(outcomes, channel.key)]
    if silent:
        return False, f"no reading at all from {', '.join(silent)}"
    frozen = [
        channel.key
        for channel in profile.channels
        if len(series := _series(outcomes, channel.key)) > 1 and min(series) == max(series)
    ]
    if frozen:
        return False, f"{', '.join(frozen)} never changed across the run; the input is probably unwired"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: DaqmxCaptureProfile,
) -> list[dict[str, object]]:
    """Samples and duration, then the span every recorded channel covered.

    The channels the profile named come first, in the order it named them, and
    whatever else the module returned follows: a reading nobody asked for is
    still worth the two rows, and it is not what the run was about.
    """
    rows: list[dict[str, object]] = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    names: dict[str, str] = ctx.extras.get(_NAMES, {})
    listed = [channel.key for channel in profile.channels]
    for key in listed + sorted(name for name in names if name not in listed):
        series = _series(outcomes, key)
        if not series:
            continue
        name = names.get(key, key)
        rows.append(
            make_result(
                f"{key}_mean",
                f"{name} mean",
                round(sum(series) / len(series), 6),
                unit="V",
                format="decimal",
                precision=6,
            )
        )
        rows.append(
            make_result(
                f"{key}_span",
                f"{name} min to max",
                f"{min(series):.6g} to {max(series):.6g}",
                unit="V",
            )
        )
    return rows


def _hardware(ctx: SuiteContext, profile: DaqmxCaptureProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    granted = ctx.extras.get(_DAQ)
    return {
        "daq": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["daq"].instance_id if granted is not None else "",
            "channels": ", ".join(f"{c.channel}={c.mode or 'as-is'}" for c in profile.channels),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: DaqmxCaptureProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "channels": ", ".join(f"{c.channel} {c.mode or 'as-is'} {c.key}" for c in profile.channels),
    }


SPEC = SuiteSpec(
    name="daqmx_capture",
    profile_model=DaqmxCaptureProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
