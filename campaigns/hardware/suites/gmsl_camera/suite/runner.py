"""Take a still from the camera on every sample, and keep every one.

Each snapshot is written into ``frames/`` and named in ``metrics.images``, the
path being relative to the run directory, which is what makes it appear in the
run's snapshot gallery. Its measurements go into metrics beside it, so
brightness and edge detail chart across the run the way any other reading does.

A snapshot is judged on three things: it arrived, it is neither black nor
blown out, and it is not byte for byte the frame before it. The last is what
catches a pipeline that has locked up while still answering, which is the
failure a camera test exists for and the one a frame count alone will not see.
"""

from __future__ import annotations

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

from suite.camera import Camera, CameraError, Snapshot
from suite.pattern import synthesise
from suite.profile import CameraSnapshotProfile

# How long the mock's sweep takes to cross the frame. Long enough that
# consecutive snapshots differ by a visible step at any usable sample period.
_MOCK_SWEEP_PERIOD_S = 6.0

# Where the granted instrument is kept for the length of the run. None for a
# mock run, which contacts nothing.
_CAMERA = "camera"
# The bytes of the last snapshot kept, for telling a live camera from a frozen
# one. Only the previous frame is held: the comparison is with its neighbour,
# so keeping the run's images in memory would cost megabytes to no purpose.
_PREVIOUS = "previous_image"
_REPEATS = "repeats"


def _setup(ctx: SuiteContext) -> None:
    """Take the instrument and report what it is set to."""
    profile: CameraSnapshotProfile = ctx.profile
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
        # Not fatal: the snapshots are what the run is for, and the first one
        # will report the same fault with the same words.
        warn(f"{granted.instance_id}: could not read the camera's state: {exc}")
        return
    form = state.get("format") or {}
    info(
        f"{granted.instance_id}: {form.get('width', '?')}x{form.get('height', '?')} "
        f"{form.get('fourcc', '?')}, scaling snapshots to {profile.max_width}px wide"
    )


def _mock_snapshot(ctx: SuiteContext, ictx: IterationContext) -> Snapshot:
    """A believable still, for a run with no camera to ask."""
    profile: CameraSnapshotProfile = ctx.profile
    width = min(profile.max_width, 320)
    height = max(1, width * 3 // 4)
    image, measured = synthesise(ctx.elapsed_run_s / _MOCK_SWEEP_PERIOD_S, width, height)
    return Snapshot(
        height=int(measured["height"]),
        image=image,
        mean_luma=measured["mean_luma"],
        sequence=ictx.iteration,
        sharpness=measured["sharpness"],
        suffix=".png",
        width=int(measured["width"]),
    )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One snapshot, written to `frames/` and judged."""
    profile: CameraSnapshotProfile = ctx.profile
    camera: Camera | None = ctx.extras.get(_CAMERA)
    phases: list[PhaseRecord] = []

    with PhaseTimer("snapshot", phases) as phase:
        phase.set_detail(width=str(profile.max_width))
        try:
            shot = _mock_snapshot(ctx, ictx) if camera is None else camera.snapshot(max_width=profile.max_width)
        except CameraError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                metrics={},
                phase_records=phases,
                summary="no snapshot",
            )

    with PhaseTimer("write", phases) as phase:
        # Named by iteration and zero padded, so the gallery and the directory
        # listing are both in the order the frames were taken.
        relative = f"frames/snapshot_{ictx.iteration:04d}{shot.suffix}"
        path = ctx.artifact(*relative.split("/"))
        path.write_bytes(shot.image)
        phase.set_detail(bytes=str(len(shot.image)))

    repeats = ctx.extras.get(_REPEATS, 0)
    frozen = shot.image == ctx.extras.get(_PREVIOUS)
    repeats = repeats + 1 if frozen else 0
    ctx.extras[_PREVIOUS] = shot.image
    ctx.extras[_REPEATS] = repeats

    reason = _fault(shot, repeats, profile)
    return IterationOutcome(
        success=not reason,
        reason=reason,
        # Nested under the instrument, so the flattened names come out as
        # `camera.<measurement>` and the frontend groups them together.
        # `images` sits at the top because that is where the contract reads it.
        metrics={
            "camera": {
                "bytes": len(shot.image),
                "mean_luma": shot.mean_luma,
                "repeats": repeats,
                "sequence": shot.sequence,
                "sharpness": shot.sharpness,
            },
            "images": [relative],
        },
        phase_records=phases,
        summary=f"luma={shot.mean_luma:.1f} sharpness={shot.sharpness:.2f} {shot.width}x{shot.height}",
    )


def _fault(shot: Snapshot, repeats: int, profile: CameraSnapshotProfile) -> str:
    """Why this snapshot is no good, or an empty string when it is fine."""
    if shot.mean_luma < profile.min_mean_luma:
        return f"frame is dark: mean luma {shot.mean_luma:.1f} below {profile.min_mean_luma:.1f}"
    if shot.mean_luma > profile.max_mean_luma:
        return f"frame is saturated: mean luma {shot.mean_luma:.1f} above {profile.max_mean_luma:.1f}"
    if shot.sharpness < profile.min_sharpness:
        return f"frame has no detail: sharpness {shot.sharpness:.2f} below {profile.min_sharpness:.2f}"
    if repeats > profile.max_identical_frames:
        return f"camera is repeating one frame: {repeats} identical snapshots in a row"
    return ""


def _series(outcomes: list[IterationOutcome], key: str) -> list[float]:
    """Every value recorded for one measurement, skipping the snapshots that failed."""
    return [
        value for outcome in outcomes if isinstance(value := outcome.metrics.get("camera", {}).get(key), (int, float))
    ]


def _evaluate(outcomes: list[IterationOutcome], profile: CameraSnapshotProfile) -> tuple[bool, str] | None:
    """A session is good when every snapshot arrived and every one was usable."""
    if not outcomes:
        return False, "no snapshots taken"
    missed = sum(1 for outcome in outcomes if not outcome.success)
    if missed > profile.max_missed_snapshots:
        first = next((outcome.reason for outcome in outcomes if not outcome.success), "")
        return False, f"{missed} of {len(outcomes)} snapshots were not usable: {first}"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: CameraSnapshotProfile,
) -> list[dict[str, object]]:
    """How many frames were kept, and the span the measurements covered."""
    luma = _series(outcomes, "mean_luma")
    sharpness = _series(outcomes, "sharpness")
    rows: list[dict[str, object]] = [
        make_result("snapshots", "Snapshots", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    if luma:
        rows.append(
            make_result(
                "mean_luma",
                "Brightness mean",
                round(sum(luma) / len(luma), 1),
                format="decimal",
                precision=1,
            )
        )
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
    return rows


def _hardware(ctx: SuiteContext, profile: CameraSnapshotProfile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    granted = ctx.extras.get(_CAMERA)
    return {
        "camera": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["camera"].instance_id if granted is not None else "",
            "max_width": str(profile.max_width),
        }
    }


def _profile_summary(ctx: SuiteContext, profile: CameraSnapshotProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "max_width": str(profile.max_width),
        "sample_period_s": str(profile.sample_period_s),
    }


SPEC = SuiteSpec(
    name="gmsl_camera",
    profile_model=CameraSnapshotProfile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
