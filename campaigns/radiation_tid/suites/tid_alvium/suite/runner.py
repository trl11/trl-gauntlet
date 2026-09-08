"""Measure the camera on every tick, and keep going whatever it does.

Each tick reads both temperatures, takes one still, writes it into ``frames/``
and names it in ``metrics.images``, so every frame of the exposure can be
looked at afterwards rather than only read about.

The first ``baseline_frames`` stills fix what the rest are compared against.
Absolute brightness and edge detail say little on their own — they are as much
the scene and the lens as the part — so what is recorded beside them is the
drift from that baseline, which is the shape a degrading sensor makes.

Nothing here stops the run. A part that darkens, goes soft or freezes is the
result of a dose session rather than an error, so each is recorded as an
anomaly and the loop carries on measuring; the session is recorded, never
aborted, and every frame and reading up to the end is kept. A camera that has
stopped answering is the one case worth acting on, because the thermal
shutdown latches until the camera restarts and the rest of the beam slot would
otherwise be lost — so the suite reboots it and takes it again.

A still that never arrived is still counted as a failed iteration, which is
what the verdict is taken over. Reading the run means reading the anomalies
beside it: `failed` here means the camera missed frames, not that the
measurement was lost.
"""

from __future__ import annotations

import time
from typing import Any

from gauntlet_sdk import (
    AnomalyLog,
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

from suite.anomaly import flag
from suite.camera import Camera, CameraError, Snapshot, Temperature
from suite.mock import MockCamera
from suite.profile import CameraDoseProfile

_ANOMALIES = "anomalies"
_BASELINE = "baseline"
_CAMERA = "camera"
_MISSES = "consecutive_misses"
# The bytes of the last still kept, for telling a live camera from a frozen
# one. Only the previous frame is held: the comparison is with its neighbour,
# so keeping the exposure's images in memory would cost gigabytes.
_PREVIOUS = "previous_image"
_REPEATS = "repeats"
_RESETS = "resets"

_PROBE = "camera"

# What the camera calls a temperature it is content with. Anything else is its
# own complaint rather than a threshold chosen here.
_THERMAL_OK = "OK"

# What the driver for this camera calls itself in its state, which is how a
# suite tells it from any other camera answering the same capability.
_ALVIUM = "alvium"


def _setup(ctx: SuiteContext) -> None:
    """Take the camera and record what it is, so the run names its own part."""
    profile: CameraDoseProfile = ctx.profile
    ctx.extras[_ANOMALIES] = AnomalyLog(ctx.jsonl)
    ctx.extras[_RESETS] = 0
    ctx.extras[_MISSES] = 0

    if profile.driver == "mock":
        info("driver=mock — no instrument contacted, the camera is synthesised")
        ctx.extras[_CAMERA] = MockCamera()
        return

    granted = ctx.env.capability("camera")
    camera = Camera(granted.url)
    ctx.extras[_CAMERA] = camera
    try:
        camera.own()
    except CameraError as exc:
        warn(f"{granted.instance_id}: could not open the camera: {exc}")
    try:
        state = camera.state()
    except CameraError as exc:
        warn(f"{granted.instance_id}: could not read the camera's state: {exc}")
        return
    _is_alvium(state)
    _meter(camera, profile)
    if profile.exposure_us <= 0:
        warn(
            "exposure is left on the camera's own metering, which compensates for the sensor "
            "dimming — the very thing this run measures. Pin exposure_us before an exposure."
        )
    form = state.get("format") or {}
    heat = _temperature(camera)
    info(
        f"{granted.instance_id}: {state.get('serial', '?')} "
        f"{form.get('width', '?')}x{form.get('height', '?')} {form.get('pixel_format', '?')}, "
        f"sensor {heat.sensor_c:.1f}C, scaling stills to {profile.max_width}px wide"
    )


def _is_alvium(state: dict[str, Any]) -> None:
    """Stop the run unless an Allied Vision camera is what was granted.

    Several drivers answer the ``camera`` capability and the operator's
    ``camera_device`` setting decides which. `"auto"` takes a capture node
    when no Allied Vision camera was on the bus at scan time, so a bench with
    a webcam in it can hand this suite the webcam — which would measure
    something real, report it as the camera under test, and be wrong about
    every one of the readings this suite exists for.

    Checked before anything else, because a wrong camera is not a bad result:
    it is a run that should not have started.
    """
    driver = str(state.get("driver", ""))
    if driver != _ALVIUM:
        raise RuntimeError(
            f"the camera capability is backed by {driver or 'another driver'}, not an Allied Vision "
            f"camera ({state.get('node') or 'unknown device'}). Set camera_device to the camera's "
            f"serial to pin it, then rescan the instruments."
        )


def _meter(camera: Camera, profile: Any) -> None:
    """Pin the exposure the profile asked for, or leave the camera metering.

    A camera opens metering for itself, and `reset` puts it back that way, so
    a pinned exposure has to be applied whenever the connection is new.
    """
    if profile.exposure_us <= 0:
        return
    try:
        settled = camera.set_exposure(profile.exposure_us)
    except CameraError as exc:
        warn(f"could not pin the exposure at {profile.exposure_us:.0f}us: {exc}")
        return
    info(f"exposure pinned at {settled:.0f}us")


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One still and both temperatures, measured against the baseline."""
    profile: CameraDoseProfile = ctx.profile
    camera: Camera | MockCamera = ctx.extras[_CAMERA]
    anomalies: AnomalyLog = ctx.extras[_ANOMALIES]
    phases: list[PhaseRecord] = []

    with PhaseTimer("snapshot", phases) as phase:
        phase.set_detail(width=str(profile.max_width))
        try:
            shot = camera.snapshot(max_width=profile.max_width)
        except CameraError as exc:
            return _missed(ctx, ictx, exc, phases)

    ctx.extras[_MISSES] = 0

    with PhaseTimer("write", phases) as phase:
        # Named by iteration and zero padded, so the gallery and the directory
        # listing are both in the order the stills were taken.
        relative = f"frames/frame_{ictx.iteration:05d}{shot.suffix}"
        ctx.artifact(*relative.split("/")).write_bytes(shot.image)
        phase.set_detail(bytes=str(len(shot.image)))

    with PhaseTimer("temperature", phases):
        heat = _temperature(camera)

    repeats = ctx.extras.get(_REPEATS, 0) + 1 if shot.image == ctx.extras.get(_PREVIOUS) else 0
    ctx.extras[_PREVIOUS] = shot.image
    ctx.extras[_REPEATS] = repeats

    baseline = _baseline(ctx, shot, profile)
    drift = shot.mean_luma - baseline.mean_luma if baseline else 0.0
    detail = shot.sharpness / baseline.sharpness if baseline and baseline.sharpness else 1.0
    _flag_drift(ctx, ictx, shot, heat, repeats, drift, detail, profile)

    return IterationOutcome(
        success=True,
        reason="",
        # Nested under the instrument, so the flattened names come out as
        # `camera.<measurement>` and the frontend groups them together.
        # `images` sits at the top because that is where the contract reads it.
        metrics={
            "camera": {
                "anomalies": anomalies.total(),
                "bytes": len(shot.image),
                "detail_ratio": round(detail, 3),
                "luma_drift": round(drift, 2),
                "mainboard_c": heat.mainboard_c,
                "mean_luma": shot.mean_luma,
                "repeats": repeats,
                "resets": ctx.extras[_RESETS],
                "sensor_c": heat.sensor_c,
                "sharpness": shot.sharpness,
            },
            "images": [relative],
        },
        phase_records=phases,
        summary=(
            f"luma={shot.mean_luma:.1f} ({drift:+.1f}) sharpness={shot.sharpness:.2f} sensor={heat.sensor_c:.1f}C"
        ),
    )


def _missed(
    ctx: SuiteContext,
    ictx: IterationContext,
    exc: CameraError,
    phases: list[PhaseRecord],
) -> IterationOutcome:
    """A still that did not arrive: record it, then try to get the camera back.

    The iteration is recorded as a failure so the miss is counted and shown,
    but the run does not stop: the exposure continues whatever the camera is
    doing, and a camera that comes back carries on measuring.
    """
    anomalies: AnomalyLog = ctx.extras[_ANOMALIES]
    misses = ctx.extras[_MISSES] + 1
    ctx.extras[_MISSES] = misses

    heat = _temperature(ctx.extras[_CAMERA])
    flag(
        anomalies,
        _PROBE,
        "snapshot_failed",
        iteration=ictx.iteration,
        message=f"no still: {exc} (sensor {heat.sensor_c:.1f}C, {heat.status or 'thermal status unreadable'})",
        detail={"error": str(exc), "consecutive": misses, "sensor_c": heat.sensor_c, "status": heat.status},
    )
    _recover(ctx, ictx, misses)

    return IterationOutcome(
        success=False,
        reason=str(exc),
        metrics={
            "camera": {
                "anomalies": anomalies.total(),
                "mainboard_c": heat.mainboard_c,
                "resets": ctx.extras[_RESETS],
                "sensor_c": heat.sensor_c,
            }
        },
        phase_records=phases,
        summary="no still",
    )


def _recover(ctx: SuiteContext, ictx: IterationContext, misses: int) -> None:
    """Reboot the camera and take it again, once it has missed enough stills.

    A single miss is not worth a reboot — a frame can arrive incomplete on its
    own — so this waits for a run of them. The reboot leaves the bus for about
    a second and the camera comes back on a new address, which is what the
    settle is for: taking it again before then finds nothing.
    """
    profile: CameraDoseProfile = ctx.profile
    anomalies: AnomalyLog = ctx.extras[_ANOMALIES]
    camera: Camera | MockCamera = ctx.extras[_CAMERA]
    if not profile.recovery.enabled or misses < profile.recovery.after_failures:
        return
    if ctx.extras[_RESETS] >= profile.recovery.max_resets:
        return

    ctx.extras[_RESETS] += 1
    flag(
        anomalies,
        _PROBE,
        "camera_reset",
        iteration=ictx.iteration,
        message=f"rebooting the camera after {misses} stills in a row did not arrive",
        detail={"consecutive": misses, "resets": ctx.extras[_RESETS]},
    )
    try:
        camera.reset()
    except CameraError as exc:
        warn(f"the camera would not reboot: {exc}")
        return
    time.sleep(profile.recovery.settle_s)
    try:
        camera.own()
    except CameraError as exc:
        warn(f"the camera did not come back: {exc}")
        return
    # A reboot restores the camera's own metering, so a run that pinned an
    # exposure has to pin it again or the rest of the session is measured
    # against a different one.
    _meter(camera, profile)
    # Cleared so the frozen-frame check starts again rather than comparing the
    # first frame after the reboot with the last one before it.
    ctx.extras[_PREVIOUS] = None
    ctx.extras[_MISSES] = 0
    info("the camera came back")


def _baseline(ctx: SuiteContext, shot: Snapshot, profile: CameraDoseProfile) -> Snapshot | None:
    """What drift is measured from, fixed once enough stills have arrived.

    Held as the mean of the opening stills rather than the first one, so a
    single odd frame at the start does not become the reference for the whole
    exposure. Returns None until there are enough, which is when nothing has a
    drift to report yet.
    """
    opening: list[Snapshot] = ctx.extras.setdefault(_BASELINE, [])
    if isinstance(opening, Snapshot):
        return opening
    opening.append(shot)
    if len(opening) < profile.baseline_frames:
        return None
    fixed = Snapshot(
        height=shot.height,
        image=b"",
        mean_luma=sum(frame.mean_luma for frame in opening) / len(opening),
        sharpness=sum(frame.sharpness for frame in opening) / len(opening),
        suffix=shot.suffix,
        width=shot.width,
    )
    ctx.extras[_BASELINE] = fixed
    info(f"baseline over {len(opening)} stills: luma={fixed.mean_luma:.1f} sharpness={fixed.sharpness:.2f}")
    return fixed


def _flag_drift(
    ctx: SuiteContext,
    ictx: IterationContext,
    shot: Snapshot,
    heat: Temperature,
    repeats: int,
    drift: float,
    detail: float,
    profile: CameraDoseProfile,
) -> None:
    """Record what this still says about the part, without failing the run."""
    anomalies: AnomalyLog = ctx.extras[_ANOMALIES]
    if abs(drift) > profile.max_luma_drift:
        flag(
            anomalies,
            _PROBE,
            "brightness_drift",
            iteration=ictx.iteration,
            message=f"brightness has moved {drift:+.1f} from the baseline, past {profile.max_luma_drift:.1f}",
            detail={"drift": round(drift, 2), "mean_luma": shot.mean_luma},
        )
    if detail < profile.max_sharpness_drop:
        flag(
            anomalies,
            _PROBE,
            "detail_lost",
            iteration=ictx.iteration,
            message=f"edge detail is {detail:.0%} of the baseline, below {profile.max_sharpness_drop:.0%}",
            detail={"ratio": round(detail, 3), "sharpness": shot.sharpness},
        )
    if repeats > profile.max_identical_frames:
        flag(
            anomalies,
            _PROBE,
            "frozen_frame",
            iteration=ictx.iteration,
            message=f"the camera is repeating one frame: {repeats} identical stills in a row",
            detail={"repeats": repeats},
        )
    # The camera's own word, which is what it acts on: past its limit it shuts
    # the image path down whatever this suite thinks of the number.
    if heat.status and heat.status != _THERMAL_OK:
        flag(
            anomalies,
            _PROBE,
            "thermal_status",
            iteration=ictx.iteration,
            message=f"the camera reports its temperature as {heat.status} ({heat.sensor_c:.1f}C)",
            detail={"mainboard_c": heat.mainboard_c, "sensor_c": heat.sensor_c, "status": heat.status},
        )
    if heat.sensor_c > profile.max_sensor_c:
        flag(
            anomalies,
            _PROBE,
            "sensor_hot",
            iteration=ictx.iteration,
            message=f"sensor is at {heat.sensor_c:.1f}C, above {profile.max_sensor_c:.1f}C",
            detail={"mainboard_c": heat.mainboard_c, "sensor_c": heat.sensor_c, "status": heat.status},
        )


def _temperature(camera: Camera | MockCamera) -> Temperature:
    """What the camera is reading, or nothing when it will not say.

    A camera that has shut its image path down still answers this, which is
    what makes it worth asking for after a still has failed. One that will not
    answer it either has gone from the bus.
    """
    try:
        return camera.temperature()
    except CameraError as exc:
        warn(f"could not read the camera's temperature: {exc}")
        return Temperature(mainboard_c=0.0, sensor_c=0.0, status="")


def _evaluate(outcomes: list[IterationOutcome], profile: CameraDoseProfile) -> tuple[bool, str] | None:
    """A session that took no still at all is not a pass.

    Every other way of failing is already counted: a still that did not arrive
    is a failed iteration, and the drift a part shows is an anomaly rather
    than a fault, because degrading is what a dose run is watching for.
    """
    if profile.pass_criteria.require_measurement and not any(outcome.success for outcome in outcomes):
        return False, "no still arrived in the whole session"
    return True, ""


def _series(outcomes: list[IterationOutcome], key: str) -> list[float]:
    """Every value recorded for one measurement, skipping the stills that failed."""
    return [
        value for outcome in outcomes if isinstance(value := outcome.metrics.get("camera", {}).get(key), (int, float))
    ]


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: CameraDoseProfile,
) -> list[dict[str, object]]:
    """What the exposure did to the part, as the numbers to read first."""
    anomalies: AnomalyLog = ctx.extras[_ANOMALIES]
    luma = _series(outcomes, "mean_luma")
    drift = _series(outcomes, "luma_drift")
    detail = _series(outcomes, "detail_ratio")
    sensor = _series(outcomes, "sensor_c")
    missed = sum(1 for outcome in outcomes if not outcome.success)

    rows: list[dict[str, object]] = [
        make_result("stills", "Stills", len(luma), format="int"),
        make_result("missed", "Stills missed", missed, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("anomalies", "Anomalies", anomalies.total(), format="int"),
        make_result("resets", "Camera reboots", ctx.extras[_RESETS], format="int"),
    ]
    if drift:
        rows.append(
            make_result(
                "luma_drift",
                "Brightness drift, last",
                round(drift[-1], 1),
                format="decimal",
                precision=1,
            )
        )
    if detail:
        rows.append(
            make_result("detail_ratio", "Edge detail, last", round(detail[-1], 3), format="decimal", precision=3)
        )
    if sensor:
        rows.append(
            make_result("sensor_c", "Sensor peak", round(max(sensor), 1), format="decimal", precision=1, unit="C")
        )
    return rows


def _hardware(ctx: SuiteContext, profile: CameraDoseProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    return {
        "camera": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["camera"].instance_id if profile.driver == "real" else "",
            "max_width": str(profile.max_width),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: CameraDoseProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "max_width": str(profile.max_width),
        "sample_period_s": str(profile.sample_period_s),
    }


SPEC = SuiteSpec(
    name="tid_alvium",
    profile_model=CameraDoseProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
