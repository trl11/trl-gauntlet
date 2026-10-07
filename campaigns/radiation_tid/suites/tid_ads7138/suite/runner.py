"""Total ionising dose characterisation of the ADS7138QRTERQ1.

The part's eight channels are split three ways, as the board wires them:

    pin 15  AIN0   analog input     the part converts it, held to a window
    pin 16  AIN1   analog input     the part converts it, held to a window
    pin 1   GPIO2  input, tied to 1 the part must read 1
    pin 4   GPIO5  input, tied to 0 the part must read 0
    pin 2   GPIO3  output, toggled  flips each sample; the analyzer must follow
    pin 3   GPIO4  output, 0        the analyzer must see 0
    pin 5   GPIO6  output, 1        the analyzer must see 1
    pin 6   GPIO7  output, a pulse  flips each sample; the analyzer must follow

Each sample drives the outputs, captures them, reads the inputs and both
analog channels, and checks that the configuration and SYSTEM_STATUS still
read as written.
"""

from __future__ import annotations

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
    FULL_SCALE,
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

AIN0, AIN1 = 0, 1
INPUT_HIGH, INPUT_LOW = 2, 5
TOGGLE, OUTPUT_LOW, OUTPUT_HIGH, PULSE = 3, 4, 6, 7
_OUTPUTS = (1 << TOGGLE) | (1 << OUTPUT_LOW) | (1 << OUTPUT_HIGH) | (1 << PULSE)

# What setup writes, and what every sample reads back. AIN0 and AIN1 stay
# analog, the rest are GPIO, and the four outputs are push-pull because an
# analyzer probe offers no pullup for an open drain. SEQUENCE_CFG at zero is
# manual mode, where CHANNEL_SEL picks what the next read converts.
_CONFIGURATION = {
    PIN_CFG: 0xFF & ~((1 << AIN0) | (1 << AIN1)),
    GPIO_CFG: _OUTPUTS,
    GPO_DRIVE_CFG: _OUTPUTS,
    DATA_CFG: 0x00,
    OPMODE_CFG: 0x00,
    SEQUENCE_CFG: 0x00,
}

# Long enough to catch a glitch on a held output, short enough to stay well
# inside a one-second sample.
_RATE = "1mhz"
_WINDOW = "10ms"

_ADC = "adc"
_ANALYZER = "analyzer"


def driven_for(iteration: int) -> int:
    """The GPO_VALUE byte for one sample: GPIO4 low, GPIO6 high, GPIO3 and GPIO7 flipping.

    GPIO3 flips opposite to GPIO7, so a short between the two cannot pass.
    """
    flip = iteration & 1
    return (1 << OUTPUT_HIGH) | ((1 - flip) << TOGGLE) | (flip << PULSE)


def volts(code: int, avdd_v: float) -> float:
    """A 12-bit code as volts against AVDD."""
    return code * avdd_v / FULL_SCALE


def input_faults(read: int, driven: int) -> list[str]:
    """What the part's own GPI_VALUE says is out of spec."""
    faults = []
    if not read & (1 << INPUT_HIGH):
        faults.append("GPIO2 (pin 1) read 0, not 1")
    if read & (1 << INPUT_LOW):
        faults.append("GPIO5 (pin 4) read 1, not 0")
    if (read ^ driven) & _OUTPUTS:
        faults.append(f"the part read its outputs as 0x{read & _OUTPUTS:02x}, not 0x{driven:02x}")
    return faults


def pin_faults(captured: dict[int, tuple[int, int]], profile: TidAds7138Profile, driven: int) -> list[str]:
    """What the analyzer saw on the outputs that is out of spec."""
    faults = []
    for name, channel, probe in (
        ("GPIO3 (pin 2)", TOGGLE, profile.toggle_probe),
        ("GPIO4 (pin 3)", OUTPUT_LOW, profile.low_probe),
        ("GPIO6 (pin 5)", OUTPUT_HIGH, profile.high_probe),
        ("GPIO7 (pin 6)", PULSE, profile.pulse_probe),
    ):
        expected = (driven >> channel) & 1
        level, edges = captured.get(probe, (None, 0))
        if level != expected:
            faults.append(f"{name} sat at {level} on probe {probe}, not {expected}")
        elif edges:
            faults.append(f"{name} changed {edges} times on probe {probe} while held at {expected}")
    return faults


def analog_faults(readings: dict[int, float], profile: TidAds7138Profile) -> list[str]:
    """The analog readings outside their windows."""
    faults = []
    for channel, pin, low, high in (
        (AIN0, 15, profile.ain0_min_v, profile.ain0_max_v),
        (AIN1, 16, profile.ain1_min_v, profile.ain1_max_v),
    ):
        reading = readings[channel]
        if not low <= reading <= high:
            faults.append(f"AIN{channel} (pin {pin}) read {reading:.4f} V, outside {low} to {high} V")
    return faults


def _setup(ctx: SuiteContext) -> None:
    """Take both instruments and put the part into the state under test."""
    profile: TidAds7138Profile = ctx.profile
    probes = {
        TOGGLE: profile.toggle_probe,
        OUTPUT_LOW: profile.low_probe,
        OUTPUT_HIGH: profile.high_probe,
        PULSE: profile.pulse_probe,
    }
    if profile.driver == "mock":
        mid_window = (profile.ain0_min_v + profile.ain0_max_v) / 2
        adc: Any = MockAdc(high=INPUT_HIGH, low=INPUT_LOW, code=round(mid_window * FULL_SCALE / profile.avdd_v))
        ctx.extras[_ANALYZER] = MockAnalyzer(adc, probes)
        info("driver=mock — no instrument contacted, the part is a register model")
    else:
        granted_i2c = ctx.env.capability("i2c")
        granted_logic = ctx.env.capability("logic")
        adc = Adc(granted_i2c.url, profile.address)
        ctx.extras[_ANALYZER] = Analyzer(granted_logic.url)
        info(
            f"{granted_i2c.instance_id}: ADS7138 at 0x{profile.address:02x}, {granted_logic.instance_id}: probes {probes}"
        )
    ctx.extras[_ADC] = adc

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
    """Drive the outputs, then check the pins, the inputs, the analog channels and the registers."""
    profile: TidAds7138Profile = ctx.profile
    adc = ctx.extras[_ADC]
    analyzer = ctx.extras[_ANALYZER]
    driven = driven_for(ictx.iteration)
    phases: list[PhaseRecord] = []

    try:
        with PhaseTimer("drive", phases):
            adc.write_register(GPO_VALUE, driven)
        with PhaseTimer("capture", phases):
            captured = analyzer.capture(_RATE, _WINDOW)
        with PhaseTimer("read", phases) as phase:
            read = adc.read_register(GPI_VALUE)
            readings = {channel: volts(adc.convert(channel), profile.avdd_v) for channel in (AIN0, AIN1)}
            registers = {register: adc.read_register(register) for register in _CONFIGURATION}
            status = adc.read_register(SYSTEM_STATUS)
            phase.set_detail(status=f"0x{status:02x}")
    except (AdcError, AnalyzerError) as exc:
        return IterationOutcome(
            success=False,
            reason=str(exc),
            metrics={},
            phase_records=phases,
            summary="an instrument stopped answering",
        )

    faults = pin_faults(captured, profile, driven) + input_faults(read, driven) + analog_faults(readings, profile)
    changed = [f"0x{register:02x}" for register, value in registers.items() if value != _CONFIGURATION[register]]
    if changed:
        faults.append(f"register {', '.join(changed)} no longer reads as it was written")
    if status != STATUS_HEALTHY:
        faults.append(f"SYSTEM_STATUS is 0x{status:02x}, not 0x{STATUS_HEALTHY:02x}")

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics={
            "ads7138": {
                "ain0_v": round(readings[AIN0], 4),
                "ain1_v": round(readings[AIN1], 4),
                "faults": len(faults),
                "gpi": read,
                "pulse": (driven >> PULSE) & 1,
                "status": status,
            }
        },
        phase_records=phases,
        summary=f"AIN0 {readings[AIN0]:.3f} V, AIN1 {readings[AIN1]:.3f} V" + (f", {faults[0]}" if faults else ""),
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
    return [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("failed", "Samples with a fault", sum(1 for o in outcomes if not o.success), format="int"),
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
