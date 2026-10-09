"""Total ionising dose characterisation of a TMP100.

The sensor is read directly over I2C. Setup puts it in 12-bit continuous
conversion and writes both comparator limits; every iteration then reads the
temperature, fails it outside the profile's bounds, and checks that the
configuration and both limits still read back as written, which is where a
dosed register file shows itself first.

Not yet measured: the comparison against a thermocouple in the same chamber,
which is what separates drift in the part from a chamber that changed
temperature. The thermocouple is still to be added to the BOM.
"""

from __future__ import annotations

import time
from statistics import fmean
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

from suite.profile import TidTmp100Profile
from suite.sensor import (
    CONFIGURATION,
    CONVERSION_S,
    RESOLUTION_12_BIT,
    T_HIGH,
    T_HIGH_DEFAULT,
    T_LOW,
    T_LOW_DEFAULT,
    TEMPERATURE,
    MockTmp100,
    SensorError,
    Tmp100,
    celsius,
)

# Where the granted sensor is kept for the length of the run.
_SENSOR = "sensor"

# What setup writes, and what every iteration reads back.
_REGISTERS = {
    CONFIGURATION: RESOLUTION_12_BIT,
    T_LOW: T_LOW_DEFAULT,
    T_HIGH: T_HIGH_DEFAULT,
}

# Bit 7 of the configuration is the one-shot request, not a stored setting, so
# it is left out of the read-back comparison.
_CONFIGURATION_STORED = 0x7F


def register_faults(registers: dict[int, int]) -> list[str]:
    """The registers that no longer read as they were written."""
    faults = []
    for pointer, expected in _REGISTERS.items():
        actual = registers[pointer]
        if pointer == CONFIGURATION:
            actual &= _CONFIGURATION_STORED
        if actual != expected:
            faults.append(f"register 0x{pointer:02x} reads 0x{actual:x}, not 0x{expected:x}")
    return faults


def _setup(ctx: SuiteContext) -> None:
    """Take the bridge and put the sensor into the state under test."""
    profile: TidTmp100Profile = ctx.profile
    if profile.driver == "mock":
        sensor: Any = MockTmp100()
        info("driver=mock — no instrument contacted, the part is a register model")
    else:
        granted = ctx.env.capability("i2c")
        sensor = Tmp100(granted.url, profile.address)
        info(f"{granted.instance_id}: TMP100 at 0x{profile.address:02x}")
    ctx.extras[_SENSOR] = sensor

    for pointer, value in _REGISTERS.items():
        sensor.write_register(pointer, value)
    # The temperature register holds the last 9-bit result until the first
    # 12-bit conversion completes.
    if profile.driver != "mock":
        time.sleep(CONVERSION_S)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Read the temperature and check the registers still hold what was written."""
    profile: TidTmp100Profile = ctx.profile
    sensor = ctx.extras[_SENSOR]
    phases: list[PhaseRecord] = []

    with PhaseTimer("read", phases) as phase:
        try:
            temperature_c = celsius(sensor.read_register(TEMPERATURE))
            registers = {pointer: sensor.read_register(pointer) for pointer in _REGISTERS}
        except SensorError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="the part stopped answering",
            )
        phase.set_detail(temperature_c=temperature_c)

    faults = register_faults(registers)
    if not profile.min_c <= temperature_c <= profile.max_c:
        faults.append(f"{temperature_c:.4f} °C is outside {profile.min_c} to {profile.max_c} °C")

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics={
            "tmp100": {
                "configuration": registers[CONFIGURATION],
                "faults": len(faults),
                "temperature_c": temperature_c,
            }
        },
        phase_records=phases,
        summary=f"{temperature_c:.4f} °C" + ("" if not faults else f", {faults[0]}"),
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidTmp100Profile) -> tuple[bool, str] | None:
    """Aggregate pass criteria: nothing failed, and something ran."""
    if not outcomes:
        return False, "no samples collected"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidTmp100Profile,
) -> list[dict[str, Any]]:
    """Headline figures shown at the top of the run summary."""
    temperatures = [outcome.metrics["tmp100"]["temperature_c"] for outcome in outcomes if outcome.metrics]
    figures = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("failed", "Samples with a fault", sum(1 for o in outcomes if not o.success), format="int"),
    ]
    if temperatures:
        figures += [
            make_result("min_c", "Lowest", min(temperatures), format="decimal", unit="°C", precision=4),
            make_result("mean_c", "Mean", fmean(temperatures), format="decimal", unit="°C", precision=4),
            make_result("max_c", "Highest", max(temperatures), format="decimal", unit="°C", precision=4),
        ]
    return figures


SPEC = SuiteSpec(
    name="tid_tmp100",
    profile_model=TidTmp100Profile,
    iterate=_iterate,
    evaluate=_evaluate,
    setup=_setup,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    verdict_results=_results,
)
