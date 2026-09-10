"""Total ionising dose characterisation of an ADS7138 against an identical part.

Two ADS7138s are wired channel to channel, each on its own CP2112 bridge. One
sits in the beam and one does not, so the two are the same part under two
doses and the pair is its own control: what moves on the irradiated part and
not on the reference is the dose, with the supply, the room and the harness
common to both.

Every channel can be a push-pull output or an analog input, and the harness is
the same wire either way, so an iteration drives each part in turn and has the
other one witness it. That gives four views of the part in the beam:

  * what the reference reads on its digital inputs while the part drives a
    pattern, and the reverse, which finds an output or an input stuck at a
    rail or shorted to its neighbour;
  * what the reference's converter makes of the levels the part drives, which
    is the output stage measured in millivolts rather than in ones and zeroes,
    so drive strength is watched as it degrades rather than once it fails;
  * what the part's own converter makes of the levels the reference drives,
    which is the input path and the ADC, against a source that is not being
    dosed;
  * what each part reports of itself — the spread of repeated conversions of
    one static wire, whether its own oversampling still quietens that spread,
    how long its offset calibration takes, and whether its configuration
    registers and status still read as they should.

Only one part is in the beam, so a figure that moves on it and not on the
reference is attributable, and both halves of every paired measurement are
reported rather than only their difference.
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

from suite.adc import CODE_MAX, STATUS_ALIVE, STATUS_FAULT_NAMES, STATUS_FAULTS, Adc, AdcError, MockAdc, MockBench
from suite.part import Part, cal_timeout_ms
from suite.profile import OVERSAMPLING, TidAds7138PairProfile

# Where the two parts are kept for the length of the run.
_DUT = "dut"
_REF = "ref"
# The channel and the rail each part takes its static measurements on, settled
# once at setup because the answer differs between the two parts.
_QUIET = "quiet"

# The wire a static measurement is taken on, at the numbering of the part in
# the beam. Any one would do; naming it keeps the spread of one iteration
# comparable with the next.
_QUIET_CHANNEL = 0

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


def invert(mapping: list[int]) -> list[int]:
    """The channel map read from the other part's end."""
    reverse = [0] * len(mapping)
    for source, destination in enumerate(mapping):
        reverse[destination] = source
    return reverse


def across(byte: int, mapping: list[int]) -> int:
    """One part's byte as the part at the other end of the harness sees it."""
    value = 0
    for source, destination in enumerate(mapping):
        if byte & (1 << source):
            value |= 1 << destination
    return value


def named_bits(value: int, mapping: list[int]) -> str:
    """The channels a difference covers, named at the driving end."""
    reverse = invert(mapping)
    return ", ".join(f"CH{reverse[bit]}" for bit in range(8) if value & (1 << bit))


def link(driver: Part, listener: Part, pattern: int, mapping: list[int]) -> tuple[int, str]:
    """Drive one byte and report what the far part's digital inputs read."""
    listener.listen_digital()
    driver.drive(pattern)
    seen = listener.inputs()
    expected = across(pattern, mapping)
    if seen == expected:
        return seen, ""
    return seen, (
        f"{driver.name} drove 0x{pattern:02x}, {listener.name} read 0x{seen:02x} "
        f"and not 0x{expected:02x} ({named_bits(seen ^ expected, mapping)})"
    )


def levels(driver: Part, listener: Part, mapping: list[int]) -> tuple[list[float], list[float]]:
    """What the far part's converter makes of each wire, driven low then high.

    Both lists are indexed by the driving part's own channel numbering, so a
    caller reads them against the part it is characterising rather than
    against the harness.
    """
    driver.drive(0x00)
    listener.listen_analog()
    low = [listener.millivolts(mapping[channel]) for channel in range(8)]
    driver.drive(0xFF)
    high = [listener.millivolts(mapping[channel]) for channel in range(8)]
    return low, high


def quiet_rail(measurer: Part, driver: Part, channel: int) -> int:
    """Which rail to hold the static channel at while this part measures it.

    A conversion pinned at either end of the scale hides the spread the noise
    and oversampling checks are looking for, and the two parts do not sit the
    same distance from the rails: one reads a driven low as nothing at all,
    the other reads a driven high as full scale. So each is given whichever
    rail its own converter has the most room inside, settled once against the
    pair actually on the bench rather than assumed.
    """
    room = {}
    for byte in (0x00, 0xFF):
        driver.drive(byte)
        measurer.listen_analog()
        code = measurer.code(channel)
        room[byte] = min(code, CODE_MAX - code)
    return max(room, key=lambda byte: room[byte])


def oversampling_spreads(part: Part, channel: int, samples: int) -> list[float]:
    """The spread of one static wire at each oversampling setting in the sweep.

    Reported rather than judged. A healthy part gets quieter as it averages
    more, but how much quieter depends on where in the scale the wire sits —
    near a rail the spread floors at about a code and stops falling — and the
    spread of a few dozen conversions is an estimate with its own error, so a
    threshold on it would fail iterations that only sampled unluckily. What a
    dose does to it shows in the figure across a run, beside the reference's.
    """
    return [part.spread_at(channel, setting, samples) for setting in OVERSAMPLING]


def _setup(ctx: SuiteContext) -> None:
    """Take both bridges and put both parts into the state under test."""
    profile: TidAds7138PairProfile = ctx.profile
    if profile.driver == "mock":
        bench = MockBench(profile.channel_map, vref_v=profile.vref_v)
        dut_adc: Any = MockAdc(bench, "dut")
        ref_adc: Any = MockAdc(bench, "ref")
        info("driver=mock — no instrument contacted, both parts are a register model")
    else:
        granted_dut = ctx.env.capability("i2c.dut")
        granted_ref = ctx.env.capability("i2c.ref")
        dut_adc = Adc(granted_dut.url, profile.dut_address)
        ref_adc = Adc(granted_ref.url, profile.ref_address)
        info(
            f"{granted_dut.instance_id}: part in the beam at 0x{profile.dut_address:02x}, "
            f"{granted_ref.instance_id}: reference at 0x{profile.ref_address:02x}"
        )

    for key, adc, name in ((_DUT, dut_adc, "the part in the beam"), (_REF, ref_adc, "the reference")):
        part = Part(adc, name, settle_s=profile.settle_s, vref_v=profile.vref_v)
        part.configure()
        ctx.extras[key] = part

    dut: Part = ctx.extras[_DUT]
    ref: Part = ctx.extras[_REF]
    dut_channel = _QUIET_CHANNEL
    ref_channel = profile.channel_map[_QUIET_CHANNEL]
    ctx.extras[_QUIET] = {
        _DUT: (dut_channel, quiet_rail(dut, ref, dut_channel)),
        _REF: (ref_channel, quiet_rail(ref, dut, ref_channel)),
    }
    for key, part in ((_DUT, dut), (_REF, ref)):
        channel, rail = ctx.extras[_QUIET][key]
        info(f"{part.name}: measuring its own noise on channel {channel} held 0x{rail:02x}")
    dut.release()
    ref.release()


def _teardown(ctx: SuiteContext) -> None:
    """Stop both parts driving the harness."""
    for key in (_DUT, _REF):
        part = ctx.extras.get(key)
        if part is None:
            continue
        try:
            part.release()
        except AdcError as exc:
            info(f"{part.name} could not be released: {exc}")


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Put the pair through every check once and report what each part did."""
    profile: TidAds7138PairProfile = ctx.profile
    dut: Part = ctx.extras[_DUT]
    ref: Part = ctx.extras[_REF]
    forward = profile.channel_map
    backward = invert(forward)
    quiet = ctx.extras[_QUIET]
    pattern = pattern_for(ictx.iteration)
    phases: list[PhaseRecord] = []
    faults: list[str] = []

    try:
        with PhaseTimer("link", phases) as phase:
            dut_drives, complaint = link(dut, ref, pattern, forward)
            if complaint:
                faults.append(complaint)
            ref.release()
            ref_drives, complaint = link(ref, dut, pattern, backward)
            if complaint:
                faults.append(complaint)
            dut.release()
            phase.set_detail(pattern=f"0x{pattern:02x}")

        with PhaseTimer("drive", phases) as phase:
            dut_low, dut_high = levels(dut, ref, forward)
            # The worst channel of the eight, which is the one that fails
            # first. A high output is measured against a reference of the same
            # supply, so it reads full scale until it starts to sag: this
            # figure can fall but cannot rise.
            vol_mv = max(dut_low)
            voh_mv = min(dut_high)
            phase.set_detail(vol_mv=round(vol_mv, 1), voh_mv=round(voh_mv, 1))

        with PhaseTimer("sense", phases) as phase:
            dut.release()
            ref_low, ref_high = levels(ref, dut, backward)
            sense_low_mv = sum(ref_low) / len(ref_low)
            sense_high_mv = sum(ref_high) / len(ref_high)
            phase.set_detail(low_mv=round(sense_low_mv, 1), high_mv=round(sense_high_mv, 1))

        # Each part measures a wire the other is holding at a rail, so what
        # spread is left is the measuring part's own.
        with PhaseTimer("noise_dut", phases) as phase:
            dut_channel, dut_rail = quiet[_DUT]
            ref.drive(dut_rail)
            dut.listen_analog()
            dut_noise = dut.spread(dut_channel, profile.noise_samples)
            dut_spreads = oversampling_spreads(dut, dut_channel, profile.oversampling_samples)
            phase.set_detail(spread_lsb=round(dut_noise, 2))

        with PhaseTimer("noise_ref", phases) as phase:
            ref_channel, ref_rail = quiet[_REF]
            ref.release()
            dut.drive(ref_rail)
            ref.listen_analog()
            ref_noise = ref.spread(ref_channel, profile.noise_samples)
            ref_spreads = oversampling_spreads(ref, ref_channel, profile.oversampling_samples)
            phase.set_detail(spread_lsb=round(ref_noise, 2))

        with PhaseTimer("health", phases) as phase:
            dut.release()
            readings = {}
            for key, part in ((_DUT, dut), (_REF, ref)):
                readings[key] = {
                    "cal_ms": part.calibrate(),
                    "drift": part.baseline_drift(),
                    "status": part.status(),
                }
            phase.set_detail(status=f"0x{readings[_DUT]['status']:02x}")
    except AdcError as exc:
        return IterationOutcome(
            success=False,
            reason=str(exc),
            metrics={},
            phase_records=phases,
            summary="a part stopped answering",
        )

    if vol_mv > profile.vol_max_mv:
        faults.append(f"the part in the beam holds a low output at {vol_mv:.0f} mV, above {profile.vol_max_mv:.0f} mV")
    if voh_mv < profile.voh_min_mv:
        faults.append(f"the part in the beam holds a high output at {voh_mv:.0f} mV, below {profile.voh_min_mv:.0f} mV")
    if dut_noise > profile.noise_max_lsb:
        faults.append(f"the part in the beam spreads {dut_noise:.2f} codes, above {profile.noise_max_lsb:.2f}")
    for key, part in ((_DUT, dut), (_REF, ref)):
        reading = readings[key]
        status = reading["status"]
        if not status & STATUS_ALIVE:
            faults.append(f"{part.name} reports SYSTEM_STATUS 0x{status:02x}, which is not a part that is answering")
        elif status & STATUS_FAULTS:
            named = ", ".join(name for bit, name in STATUS_FAULT_NAMES.items() if status & bit)
            faults.append(f"{part.name} reports {named}")
        if reading["drift"]:
            registers = ", ".join(f"0x{register:02x}" for register in reading["drift"])
            faults.append(f"{part.name} no longer reads register {registers} as it was written")
        if reading["cal_ms"] >= cal_timeout_ms():
            faults.append(f"{part.name} did not finish calibrating")

    metrics: dict[str, Any] = {
        "ads7138": {
            "faults": len(faults),
            "pattern": pattern,
            "dut_drive": {"vol_mv": round(vol_mv, 2), "voh_mv": round(voh_mv, 2)},
            "dut_sense": {"low_mv": round(sense_low_mv, 2), "high_mv": round(sense_high_mv, 2)},
            "link": {"dut_drives": dut_drives, "ref_drives": ref_drives},
            "dut": {
                "cal_ms": round(readings[_DUT]["cal_ms"], 3),
                "noise_lsb": round(dut_noise, 3),
                "osr_lsb": round(dut_spreads[-1], 3),
                "status": readings[_DUT]["status"],
            },
            "ref": {
                "cal_ms": round(readings[_REF]["cal_ms"], 3),
                "noise_lsb": round(ref_noise, 3),
                "osr_lsb": round(ref_spreads[-1], 3),
                "status": readings[_REF]["status"],
            },
            # The pair's whole point: the reference is not in the beam, so
            # what separates the two is what the dose has done.
            "delta": {
                "noise_lsb": round(dut_noise - ref_noise, 3),
                "cal_ms": round(readings[_DUT]["cal_ms"] - readings[_REF]["cal_ms"], 3),
                "osr_lsb": round(dut_spreads[-1] - ref_spreads[-1], 3),
            },
        }
    }

    return IterationOutcome(
        success=not faults,
        reason="; ".join(faults),
        metrics=metrics,
        phase_records=phases,
        summary=(f"0x{pattern:02x} held, {voh_mv:.0f}/{vol_mv:.0f} mV" if not faults else faults[0]),
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidAds7138PairProfile) -> tuple[bool, str] | None:
    """Aggregate pass criteria: nothing failed, and something ran."""
    if not outcomes:
        return False, "no samples collected"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidAds7138PairProfile,
) -> list[dict[str, Any]]:
    """Headline figures shown at the top of the run summary."""
    failed = sum(1 for outcome in outcomes if not outcome.success)
    rows = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("failed", "Samples with a fault", failed, format="int"),
    ]
    lows = [_metric(outcome, "dut_drive", "vol_mv") for outcome in outcomes]
    highs = [_metric(outcome, "dut_drive", "voh_mv") for outcome in outcomes]
    if any(value is not None for value in highs):
        rows.append(
            make_result(
                "voh_min",
                "Lowest high level driven",
                round(min(value for value in highs if value is not None), 1),
                format="decimal",
                precision=1,
                unit="mV",
            )
        )
        rows.append(
            make_result(
                "vol_max",
                "Highest low level driven",
                round(max(value for value in lows if value is not None), 1),
                format="decimal",
                precision=1,
                unit="mV",
            )
        )
    return rows


def _metric(outcome: IterationOutcome, group: str, name: str) -> float | None:
    """One figure out of an iteration's metrics, for a run summarising them."""
    values = outcome.metrics.get("ads7138")
    if not isinstance(values, dict):
        return None
    inner = values.get(group)
    if not isinstance(inner, dict):
        return None
    figure = inner.get(name)
    return float(figure) if isinstance(figure, (int, float)) else None


SPEC = SuiteSpec(
    name="tid_ads7138_pair",
    profile_model=TidAds7138PairProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    setup=_setup,
    teardown=_teardown,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    verdict_results=_results,
)
