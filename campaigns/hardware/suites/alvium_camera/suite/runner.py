"""Take a few stills from the camera and say whether it is working.

The question this suite answers is narrow: is there a camera, does it hand
over pictures, and are the pictures of anything. Every still is kept in
``frames/`` and named in ``metrics.images``, so a failure can be looked at
rather than only read about.

A still is judged on four things: it arrived, it is neither black nor blown
out, it has edges in it, and it is not byte for byte the frame before it. The
last is what catches an image path that has locked up while still answering,
which is the failure a camera check exists for and the one a frame count alone
will not see. The sensor temperature is recorded beside each one, because past
its limit the firmware shuts that path down and the resulting fault says
nothing about heat.
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
    warn,
)

from suite.camera import Camera, CameraError, Snapshot, Temperature
from suite.pattern import synthesise
from suite.profile import CameraCheckProfile

# How long the mock's sweep takes to cross the frame. Long enough that
# consecutive stills differ by a visible step at any usable sample period.
_MOCK_SWEEP_PERIOD_S = 6.0

# Where the granted instrument is kept for the length of the run. None for a
# mock run, which contacts nothing.
_CAMERA = "camera"
# The bytes of the last still kept, for telling a live camera from a frozen
# one. Only the previous frame is held: the comparison is with its neighbour,
# so keeping the run's images in memory would cost megabytes to no purpose.
_PREVIOUS = "previous_image"
_REPEATS = "repeats"

# What the camera calls a temperature it is content with. Anything else is its
# own complaint rather than a threshold chosen here.
_THERMAL_OK = "OK"

# What the driver for this camera calls itself in its state, which is how a
# suite tells it from any other camera answering the same capability.
_ALVIUM = "alvium"


def _setup(ctx: SuiteContext) -> None:
    """Take the instrument and report what it is."""
    profile: CameraCheckProfile = ctx.profile
    if profile.driver == "mock":
        info("driver=mock — no instrument contacted, frames are synthesised")
        ctx.extras[_CAMERA] = None
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
        # Not fatal: the stills are what the run is for, and the first one will
        # report the same fault with the same words.
        warn(f"{granted.instance_id}: could not read the camera's state: {exc}")
        return
    _is_alvium(state)
    _meter(camera, profile)
    form = state.get("format") or {}
    info(
        f"{granted.instance_id}: {state.get('serial', '?')} "
        f"{form.get('width', '?')}x{form.get('height', '?')} {form.get('pixel_format', '?')}, "
        f"scaling stills to {profile.max_width}px wide"
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


def _mock_snapshot(ctx: SuiteContext, profile: CameraCheckProfile) -> Snapshot:
    """A believable still, for a run with no camera to ask."""
    width = min(profile.max_width, 320)
    height = max(1, width * 3 // 4)
    image, measured = synthesise(ctx.elapsed_run_s / _MOCK_SWEEP_PERIOD_S, width, height)
    return Snapshot(
        height=int(measured["height"]),
        image=image,
        mean_luma=measured["mean_luma"],
        sharpness=measured["sharpness"],
        suffix=".png",
        width=int(measured["width"]),
    )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One still, written to `frames/` and judged."""
    profile: CameraCheckProfile = ctx.profile
    camera: Camera | None = ctx.extras.get(_CAMERA)
    phases: list[PhaseRecord] = []

    with PhaseTimer("snapshot", phases) as phase:
        phase.set_detail(width=str(profile.max_width))
        try:
            shot = _mock_snapshot(ctx, profile) if camera is None else camera.snapshot(max_width=profile.max_width)
        except CameraError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="no still",
            )

    with PhaseTimer("write", phases) as phase:
        # Named by iteration and zero padded, so the gallery and the directory
        # listing are both in the order the stills were taken.
        relative = f"frames/frame_{ictx.iteration:04d}{shot.suffix}"
        ctx.artifact(*relative.split("/")).write_bytes(shot.image)
        phase.set_detail(bytes=str(len(shot.image)))

    # Read after the still rather than before it, so a camera that has just
    # shut its image path down is asked while it is still the reason.
    heat = _temperature(camera)

    repeats = ctx.extras.get(_REPEATS, 0) + 1 if shot.image == ctx.extras.get(_PREVIOUS) else 0
    ctx.extras[_PREVIOUS] = shot.image
    ctx.extras[_REPEATS] = repeats

    reason = _fault(shot, heat, repeats, profile)
    return IterationOutcome(
        success=not reason,
        reason=reason,
        # Nested under the instrument, so the flattened names come out as
        # `camera.<measurement>` and the frontend groups them together.
        # `images` sits at the top because that is where the contract reads it.
        metrics={
            "camera": {
                "bytes": len(shot.image),
                "mainboard_c": heat.mainboard_c,
                "mean_luma": shot.mean_luma,
                "repeats": repeats,
                "sensor_c": heat.sensor_c,
                "sharpness": shot.sharpness,
            },
            "images": [relative],
        },
        phase_records=phases,
        summary=f"luma={shot.mean_luma:.1f} sharpness={shot.sharpness:.2f} {shot.width}x{shot.height}",
    )


def _temperature(camera: Camera | None) -> Temperature:
    """What the camera is reading, or nothing for a mock run.

    A camera that will not answer this is not itself a fault worth stopping
    for: the still is the measurement, and it has already been taken.
    """
    if camera is None:
        return Temperature(mainboard_c=0.0, sensor_c=0.0, status="")
    try:
        return camera.temperature()
    except CameraError as exc:
        warn(f"could not read the camera's temperature: {exc}")
        return Temperature(mainboard_c=0.0, sensor_c=0.0, status="")


def _fault(shot: Snapshot, heat: Temperature, repeats: int, profile: CameraCheckProfile) -> str:
    """Why this still is no good, or an empty string when it is fine."""
    if shot.mean_luma < profile.min_mean_luma:
        return f"frame is dark: mean luma {shot.mean_luma:.1f} below {profile.min_mean_luma:.1f}"
    if shot.mean_luma > profile.max_mean_luma:
        return f"frame is saturated: mean luma {shot.mean_luma:.1f} above {profile.max_mean_luma:.1f}"
    if shot.sharpness < profile.min_sharpness:
        return f"frame has no detail: sharpness {shot.sharpness:.2f} below {profile.min_sharpness:.2f}"
    if repeats > profile.max_identical_frames:
        return f"camera is repeating one frame: {repeats} identical stills in a row"
    # The camera's own word, which is what it acts on: past its limit it shuts
    # the image path down whatever this suite thinks of the number. Empty means
    # it would not say, which a mock run and an unreadable camera share.
    if heat.status and heat.status != _THERMAL_OK:
        return f"camera reports its temperature as {heat.status} ({heat.sensor_c:.1f}C)"
    if heat.sensor_c > profile.max_sensor_c:
        return f"sensor is at {heat.sensor_c:.1f}C, above {profile.max_sensor_c:.1f}C"
    return ""


def _evaluate(outcomes: list[IterationOutcome], profile: CameraCheckProfile) -> tuple[bool, str] | None:
    """The camera works when every still arrived and every one was usable.

    Nothing is tolerated here, unlike the dose fork: this is the check an
    operator runs to be told yes or no, and one bad frame out of five is a no.
    """
    if not outcomes:
        return False, "no stills taken"
    bad = [outcome for outcome in outcomes if not outcome.success]
    if bad:
        return False, f"{len(bad)} of {len(outcomes)} stills were not usable: {bad[0].reason}"
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
    profile: CameraCheckProfile,
) -> list[dict[str, object]]:
    """How many stills were kept, and the span the measurements covered."""
    luma = _series(outcomes, "mean_luma")
    sharpness = _series(outcomes, "sharpness")
    sensor = _series(outcomes, "sensor_c")
    rows: list[dict[str, object]] = [
        make_result("frames", "Stills", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    if luma:
        rows.append(make_result("luma_span", "Brightness min to max", f"{min(luma):.1f} to {max(luma):.1f}"))
    if sharpness:
        rows.append(
            make_result(
                "sharpness_mean",
                "Sharpness mean",
                round(sum(sharpness) / len(sharpness), 2),
                format="decimal",
                precision=2,
            )
        )
    if any(sensor):
        rows.append(
            make_result("sensor_c", "Sensor peak", round(max(sensor), 1), format="decimal", precision=1, unit="C")
        )
    return rows


def _hardware(ctx: SuiteContext, profile: CameraCheckProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    granted = ctx.extras.get(_CAMERA)
    return {
        "camera": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["camera"].instance_id if granted is not None else "",
            "max_width": str(profile.max_width),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: CameraCheckProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "frames": str(profile.frames),
        "max_width": str(profile.max_width),
        "sample_period_s": str(profile.sample_period_s),
    }


SPEC = SuiteSpec(
    name="alvium_camera",
    profile_model=CameraCheckProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    iteration_count=lambda p: p.frames,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
