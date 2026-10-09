"""Total ionising dose characterisation of a TMP100 or TMP112 temperature sensor.

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

from suite.profile import TidTemperatureSensorProfile
from suite.sensor import (
    CONFIGURATION,
    PARTS,
    T_HIGH,
    T_HIGH_DEFAULT,
    T_LOW,
    T_LOW_DEFAULT,
    TEMPERATURE,
    MockSensor,
    Part,
    Sensor,
    SensorError,
    celsius,
)

# Where the granted sensor is kept for the length of the run.
_SENSOR = "sensor"


def written(part: Part) -> dict[int, int]:
    """What setup writes, and what every iteration reads back."""
    return {CONFIGURATION: part.configuration, T_LOW: T_LOW_DEFAULT, T_HIGH: T_HIGH_DEFAULT}


def register_faults(part: Part, registers: dict[int, int]) -> list[str]:
    """The registers that no longer read as they were written."""
    faults = []
    for pointer, expected in written(part).items():
        actual = registers[pointer]
        if pointer == CONFIGURATION:
            actual &= part.configuration_stored
            expected &= part.configuration_stored
        if actual != expected:
            faults.append(f"register 0x{pointer:02x} reads 0x{actual:x}, not 0x{expected:x}")
    return faults


def _setup(ctx: SuiteContext) -> None:
    """Take the bridge and put the sensor into the state under test."""
    profile: TidTemperatureSensorProfile = ctx.profile
    part = PARTS[profile.part]
    if profile.driver == "mock":
        sensor: Any = MockSensor()
        info(f"driver=mock — no instrument contacted, the {profile.part.upper()} is a register model")
    else:
        granted = ctx.env.capability("i2c")
        sensor = Sensor(granted.url, profile.address, part)
        info(f"{granted.instance_id}: {profile.part.upper()} at 0x{profile.address:02x}")
    ctx.extras[_SENSOR] = sensor

    for pointer, value in written(part).items():
        sensor.write_register(pointer, value)
    # The temperature register holds the last result at the old resolution
    # until the first conversion at the new one completes.
    if profile.driver != "mock":
        time.sleep(part.conversion_s)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Read the temperature and check the registers still hold what was written."""
    profile: TidTemperatureSensorProfile = ctx.profile
    part = PARTS[profile.part]
    sensor = ctx.extras[_SENSOR]
    phases: list[PhaseRecord] = []

    with PhaseTimer("read", phases) as phase:
        try:
            temperature_c = celsius(sensor.read_register(TEMPERATURE))
            registers = {pointer: sensor.read_register(pointer) for pointer in written(part)}
        except SensorError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="the part stopped answering",
            )
        phase.set_detail(temperature_c=temperature_c)

    faults = register_faults(part, registers)
    if not profile.min_c <= temperature_c <= profile.max_c:
        faults.append(f"{temperature_c:.4f} °C is outside {profile.min_c} to {profile.max_c} °C")

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics={
            "sensor": {
                "configuration": registers[CONFIGURATION],
                "faults": len(faults),
                "temperature_c": temperature_c,
            }
        },
        phase_records=phases,
        summary=f"{temperature_c:.4f} °C" + ("" if not faults else f", {faults[0]}"),
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidTemperatureSensorProfile) -> tuple[bool, str] | None:
    """Aggregate pass criteria: nothing failed, and something ran."""
    if not outcomes:
        return False, "no samples collected"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidTemperatureSensorProfile,
) -> list[dict[str, Any]]:
    """Headline figures shown at the top of the run summary."""
    temperatures = [outcome.metrics["sensor"]["temperature_c"] for outcome in outcomes if outcome.metrics]
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
    name="tid_temperature_sensor",
    profile_model=TidTemperatureSensorProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    setup=_setup,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    verdict_results=_results,
)
