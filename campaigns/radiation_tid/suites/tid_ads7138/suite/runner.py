"""Total ionising dose characterisation of the ADS7138QRTERQ1.

Every channel of the part is configured as a push-pull digital output and
wired to a probe of the logic analyzer. One iteration drives a pattern on
those eight outputs and then checks four things: what the analyzer saw on the
pins, what the part reports its own inputs are, whether the registers holding
that configuration still read back as they were written, and whether the part
still answers a conversion read with the fixed code, which exercises the
conversion data path without an analog source.

The patterns cycle through both rails, both alternations, and a walking one
and a walking zero, so an output stuck at a rail and a pair of outputs shorted
together both show up, and the pattern that found it is named in the
iteration.

Nothing drives the analog inputs on this bench, so no conversion of a channel
is recorded: a floating input measures the probe, not the part.
"""

from __future__ import annotations

import json
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

from suite.adc import (
    DATA_CFG,
    FIXED_PATTERN,
    FIXED_PATTERN_ON,
    GPI_VALUE,
    GPIO_CFG,
    GPO_DRIVE_CFG,
    GPO_VALUE,
    OPMODE_CFG,
    PIN_CFG,
    SEQUENCE_CFG,
    STATUS_CLEAR_BOR,
    STATUS_HEALTHY,
    SYSTEM_STATUS,
    Adc,
    AdcError,
    MockAdc,
)
from suite.analyzer import Analyzer, AnalyzerError, MockAnalyzer
from suite.profile import TidAds7138Profile

# Where the granted instruments are kept for the length of the run.
_ADC = "adc"
_ANALYZER = "analyzer"

# Every capture of a run goes in one file, appended a line at a time, so the
# run page can draw them on one timeline. The first line names the channels.
_CAPTURES = "traces/captures.jsonl"

# What setup writes, and what every iteration reads back. All eight channels
# are GPIOs, all eight are outputs, and all eight are push-pull, which the
# bench needs because an analyzer probe offers no pullup for an open drain.
_CONFIGURATION = {
    PIN_CFG: 0xFF,
    GPIO_CFG: 0xFF,
    GPO_DRIVE_CFG: 0xFF,
    DATA_CFG: 0x00,
    OPMODE_CFG: 0x00,
    SEQUENCE_CFG: 0x00,
}

_PATTERNS = (
    0x00,
    0xFF,
    0xAA,
    0x55,
    *(1 << bit for bit in range(8)),
    *(0xFF ^ (1 << bit) for bit in range(8)),
)


def pattern_for(iteration: int) -> int:
    """The byte this iteration drives on the outputs."""
    return _PATTERNS[iteration % len(_PATTERNS)]


def probes_to_byte(levels: dict[int, int], probe_map: list[int]) -> int:
    """The eight probe levels read back as the byte the outputs should hold."""
    value = 0
    for output, probe in enumerate(probe_map):
        if levels.get(probe):
            value |= 1 << output
    return value


def channel_labels(probe_map: list[int]) -> list[str]:
    """The output clipped to each of the analyzer's eight probes, probe 1 first."""
    labels = [""] * 8
    for output, probe in enumerate(probe_map):
        labels[probe - 1] = f"GPO{output}"
    return labels


def named_bits(value: int) -> str:
    """The outputs a difference covers, as `GPO2, GPO5`."""
    return ", ".join(f"GPO{bit}" for bit in range(8) if value & (1 << bit))


def _setup(ctx: SuiteContext) -> None:
    """Take both instruments and put the part into the state under test."""
    profile: TidAds7138Profile = ctx.profile
    if profile.driver == "mock":
        adc: Any = MockAdc()
        ctx.extras[_ADC] = adc
        ctx.extras[_ANALYZER] = MockAnalyzer(adc, profile.probe_map)
        info("driver=mock — no instrument contacted, the part is a register model")
    else:
        granted_i2c = ctx.env.capability("i2c")
        granted_logic = ctx.env.capability("logic")
        adc = Adc(granted_i2c.url, profile.address)
        ctx.extras[_ADC] = adc
        ctx.extras[_ANALYZER] = Analyzer(granted_logic.url)
        info(
            f"{granted_i2c.instance_id}: ADS7128 at 0x{profile.address:02x}, "
            f"{granted_logic.instance_id}: {profile.rate} over {profile.window}"
        )

    # The brown-out flag is set by the power-up the part has already had, so
    # it is cleared here and every bit seen afterwards is an event this run
    # can attribute to the beam.
    adc.write_register(SYSTEM_STATUS, STATUS_CLEAR_BOR)
    for register, value in _CONFIGURATION.items():
        adc.write_register(register, value)


def _teardown(ctx: SuiteContext) -> None:
    """Stop driving the outputs."""
    adc = ctx.extras.get(_ADC)
    if adc is None:
        return
    try:
        adc.write_register(GPO_VALUE, 0x00)
    except AdcError as exc:
        info(f"the outputs could not be cleared: {exc}")


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Drive one pattern and check what the part and the pins did with it."""
    profile: TidAds7138Profile = ctx.profile
    adc = ctx.extras[_ADC]
    analyzer = ctx.extras[_ANALYZER]
    pattern = pattern_for(ictx.iteration)
    phases: list[PhaseRecord] = []
    faults: list[str] = []

    with PhaseTimer("drive", phases) as phase:
        try:
            adc.write_register(GPO_VALUE, pattern)
        except AdcError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="the part stopped answering",
            )
        phase.set_detail(pattern=f"0x{pattern:02x}")

    with PhaseTimer("capture", phases) as phase:
        try:
            captured = analyzer.capture(profile.rate, profile.window)
        except AnalyzerError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="no capture",
            )
        on_pins = probes_to_byte(captured.levels(), profile.probe_map)
        phase.set_detail(pins=f"0x{on_pins:02x}")

    if on_pins != pattern:
        faults.append(f"the pins held 0x{on_pins:02x}, not 0x{pattern:02x} ({named_bits(on_pins ^ pattern)})")

    with PhaseTimer("read_back", phases) as phase:
        try:
            reported = adc.read_register(GPI_VALUE)
            registers = {register: adc.read_register(register) for register in _CONFIGURATION}
            status = adc.read_register(SYSTEM_STATUS)
            adc.write_register(DATA_CFG, FIXED_PATTERN_ON)
            fixed = adc.read_data()
            adc.write_register(DATA_CFG, _CONFIGURATION[DATA_CFG])
        except AdcError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="the part stopped answering",
            )
        phase.set_detail(status=f"0x{status:02x}")

    if reported != pattern:
        faults.append(f"GPI_VALUE reads 0x{reported:02x}, not 0x{pattern:02x} ({named_bits(reported ^ pattern)})")
    changed = [f"0x{register:02x}" for register, value in registers.items() if value != _CONFIGURATION[register]]
    if changed:
        faults.append(f"register {', '.join(changed)} no longer reads as it was written")
    if status != STATUS_HEALTHY:
        faults.append(f"SYSTEM_STATUS is 0x{status:02x}, not 0x{STATUS_HEALTHY:02x}")
    if fixed != FIXED_PATTERN:
        faults.append(f"a conversion read answered 0x{fixed:04x}, not the fixed 0x{FIXED_PATTERN:04x}")

    traces: list[str] = []
    if profile.save_traces and captured.samples_base64:
        path = ctx.artifact(*_CAPTURES.split("/"))
        # Whether this is the first capture is asked of the file rather than of
        # the iteration number, which does not start at zero.
        first = not path.exists()
        lines = []
        # The header carries the rate the analyzer captured at rather than the
        # one the profile asked for, and is written beside the first capture
        # because that is when the analyzer has reported it.
        if first:
            lines.append(json.dumps({"channels": channel_labels(profile.probe_map), "rate_hz": captured.rate_hz}))
        lines.append(
            json.dumps(
                {
                    "elapsed_run_s": round(ictx.elapsed_run_s, 6),
                    "iteration": ictx.iteration,
                    "samples": captured.samples,
                    "samples_base64": captured.samples_base64,
                }
            )
        )
        with path.open("a") as handle:
            handle.write("\n".join(lines) + "\n")
        # Every iteration names the file it appended to, which is what
        # `metrics.traces` means, and is what lets the run page count captures
        # rather than files.
        traces.append(_CAPTURES)

    metrics: dict[str, Any] = {
        "ads7128": {
            "faults": len(faults),
            "fixed_pattern": fixed,
            "pattern": pattern,
            "pins": on_pins,
            "reported": reported,
            "status": status,
        }
    }
    if traces:
        metrics["traces"] = traces

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics=metrics,
        phase_records=phases,
        summary=f"0x{pattern:02x} " + ("held" if not faults else faults[0]),
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidAds7138Profile) -> tuple[bool, str] | None:
    """Aggregate pass criteria: nothing failed, and something ran."""
    if not outcomes:
        return False, "no samples collected"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidAds7138Profile,
) -> list[dict[str, Any]]:
    """Headline figures shown at the top of the run summary."""
    failed = sum(1 for outcome in outcomes if not outcome.success)
    return [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("patterns", "Patterns driven", len(_PATTERNS), format="int"),
        make_result("failed", "Samples with a fault", failed, format="int"),
    ]


SPEC = SuiteSpec(
    name="tid_ads7138",
    profile_model=TidAds7138Profile,
    iterate=_iterate,
    evaluate=_evaluate,
    setup=_setup,
    teardown=_teardown,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    verdict_results=_results,
)
