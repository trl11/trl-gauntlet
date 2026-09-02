"""Capture the probes of the logic analyzer for the length of the run.

The probes are named once at setup, then one window of samples is taken on
every sample period. Each capture becomes one metrics record under the labels
the profile gave the probes, so a run watching an I2C bus charts
`logic.scl.frequency` rather than `logic.channels.1.frequency`, and the
picture of the capture is written into `traces/` and named in
`metrics.traces`, which is what puts it in the run's Traces tab.

What a probe is expected to be doing is the profile's to say. A line declared
`active` that stopped moving fails the capture it stopped in, which is the
whole reason to watch a clock during an irradiation.
"""

from __future__ import annotations

import base64
from typing import Any

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
)

from suite import pattern
from suite.analyzer import Analyzer, AnalyzerError, Capture
from suite.profile import Channel, LogicCaptureProfile

# Where the granted instrument is kept for the length of the run. None for a
# mock run, which contacts nothing.
_ANALYZER = "analyzer"

# Rates as the profile names them, in hertz. The instrument takes the name;
# this is only for the mock, which has no instrument to ask.
_RATE_HZ = {
    "24mhz": 24_000_000,
    "16mhz": 16_000_000,
    "12mhz": 12_000_000,
    "8mhz": 8_000_000,
    "6mhz": 6_000_000,
    "4mhz": 4_000_000,
    "3mhz": 3_000_000,
    "2mhz": 2_000_000,
    "1mhz": 1_000_000,
    "500khz": 500_000,
    "250khz": 250_000,
    "200khz": 200_000,
    "100khz": 100_000,
    "50khz": 50_000,
    "25khz": 25_000,
    "20khz": 20_000,
}
_WINDOW_S = {"1ms": 0.001, "10ms": 0.01, "100ms": 0.1}


def _setup(ctx: SuiteContext) -> None:
    """Take the instrument and name every probe the profile lists."""
    profile: LogicCaptureProfile = ctx.profile
    named = ", ".join(f"CH{c.channel} {c.expect} as {c.key}" for c in profile.channels)
    if profile.driver == "mock":
        info(f"driver=mock — no instrument contacted, captures are synthesised: {named}")
        ctx.extras[_ANALYZER] = None
        return

    granted = ctx.env.capability("logic")
    analyzer = Analyzer(granted.url)
    rows = {c.channel: {"label": c.label} for c in profile.channels if c.label}
    if rows:
        analyzer.configure(rows)
    ctx.extras[_ANALYZER] = analyzer
    info(f"{granted.instance_id}: {profile.rate} over {profile.window}, {named}")


def _capture(ctx: SuiteContext, ictx: IterationContext) -> Capture:
    """One window of samples, from the instrument or from the mock."""
    profile: LogicCaptureProfile = ctx.profile
    analyzer: Analyzer | None = ctx.extras.get(_ANALYZER)
    if analyzer is not None:
        return analyzer.capture(profile.rate, profile.window)

    rate_hz = _RATE_HZ[profile.rate]
    window_s = _WINDOW_S[profile.window]
    image, channels = pattern.synthesise(rate_hz, window_s, ictx.elapsed_run_s)
    return Capture(
        {
            "channels": channels,
            "image_base64": base64.b64encode(image).decode(),
            "rate_hz": rate_hz,
            "samples": int(rate_hz * window_s),
            "window_s": window_s,
        }
    )


def _fault(channel: Channel, reading: dict[str, Any]) -> str:
    """Why this probe's reading is no good, or an empty string when it is fine."""
    level = reading.get("level")
    if channel.expect == "active" and not reading.get("edges"):
        return f"{channel.key} did not move"
    if channel.expect == "high" and level != 1:
        return f"{channel.key} is not high"
    if channel.expect == "low" and level != 0:
        return f"{channel.key} is not low"
    return ""


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One capture, measured against what each probe is expected to be doing."""
    profile: LogicCaptureProfile = ctx.profile
    phases: list[PhaseRecord] = []

    with PhaseTimer("capture", phases) as phase:
        try:
            captured = _capture(ctx, ictx)
        except AnalyzerError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="no capture",
            )
        phase.set_detail(samples=str(captured.samples))

    traces: list[str] = []
    if profile.save_traces and captured.image:
        with PhaseTimer("trace", phases) as phase:
            relative = f"traces/capture_{ictx.iteration:04d}.png"
            ctx.artifact(*relative.split("/")).write_bytes(captured.image)
            phase.set_detail(bytes=str(len(captured.image)))
            traces.append(relative)

    values: dict[str, dict[str, float]] = {}
    faults: list[str] = []
    for channel in profile.channels:
        reading = captured.channels.get(channel.channel)
        if not reading:
            faults.append(f"{channel.key} was not reported")
            continue
        values[channel.key] = {
            "duty": float(reading.get("duty") or 0.0),
            "frequency": float(reading.get("frequency") or 0.0),
            "level": float(reading.get("level") or 0),
        }
        fault = _fault(channel, reading)
        if fault:
            faults.append(fault)

    # Nested under the instrument, so the flattened names come out as
    # `logic.<label>.<measurement>` and the frontend groups them together.
    # `traces` sits at the top because that is where the contract reads it.
    metrics: dict[str, object] = {"logic": values}
    if traces:
        metrics["traces"] = traces

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics=metrics,
        phase_records=phases,
        summary=_summary(values),
    )


def _summary(values: dict[str, dict[str, float]]) -> str:
    """The first probe or two, for the line the operator watches scroll."""
    shown = [f"{key}={reading['frequency']:.4g}Hz" for key, reading in list(values.items())[:2]]
    return " ".join(shown) or "nothing captured"


def _series(outcomes: list[IterationOutcome], key: str, measurement: str) -> list[float]:
    """One probe's readings of one measurement, skipping the captures it missed."""
    readings = []
    for outcome in outcomes:
        probe = outcome.metrics.get("logic", {})
        value = probe.get(key, {}).get(measurement) if isinstance(probe, dict) else None
        if isinstance(value, (int, float)):
            readings.append(float(value))
    return readings


def _evaluate(outcomes: list[IterationOutcome], profile: LogicCaptureProfile) -> tuple[bool, str] | None:
    """A capture session is good when every probe did what it was said to do."""
    if not outcomes:
        return False, "no captures taken"
    failed = sum(1 for outcome in outcomes if not outcome.success)
    if failed > profile.max_failed_samples:
        first = next((outcome.reason for outcome in outcomes if not outcome.success), "")
        return False, f"{failed} of {len(outcomes)} captures failed: {first}"
    silent = [c.key for c in profile.channels if not _series(outcomes, c.key, "frequency")]
    if silent:
        return False, f"nothing recorded at all from {', '.join(silent)}"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: LogicCaptureProfile,
) -> list[dict[str, object]]:
    """Captures and duration, then what each probe was doing across them."""
    rows: list[dict[str, object]] = [
        make_result("captures", "Captures", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    for channel in profile.channels:
        frequencies = _series(outcomes, channel.key, "frequency")
        if not frequencies:
            continue
        name = channel.label or f"CH {channel.channel}"
        rows.append(
            make_result(
                f"{channel.key}_frequency",
                f"{name} mean frequency",
                round(sum(frequencies) / len(frequencies), 1),
                unit="Hz",
                format="decimal",
                precision=1,
            )
        )
        rows.append(
            make_result(
                f"{channel.key}_span",
                f"{name} min to max",
                f"{min(frequencies):.4g} to {max(frequencies):.4g}",
                unit="Hz",
            )
        )
    return rows


def _hardware(ctx: SuiteContext, profile: LogicCaptureProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    granted = ctx.extras.get(_ANALYZER)
    return {
        "logic": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["logic"].instance_id if granted is not None else "",
            "capture": f"{profile.rate} over {profile.window}",
            "channels": ", ".join(f"CH{c.channel}={c.expect}" for c in profile.channels),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: LogicCaptureProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "rate": profile.rate,
        "window": profile.window,
        "channels": ", ".join(f"CH{c.channel} {c.expect} {c.key}" for c in profile.channels),
    }


SPEC = SuiteSpec(
    name="logic_capture",
    profile_model=LogicCaptureProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
