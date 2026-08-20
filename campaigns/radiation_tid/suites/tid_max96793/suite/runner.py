"""Watch the GMSL link while the MAX96793GTJ/VY+ is under the beam.

The serializer, in the camera head, is the part being irradiated. The deserializer sits
in the USB adapter outside the beam and is the known-good reference: one capture
crosses both, so a fault that appears at only one end says which end moved.

Every sample reads both chips and keeps a snapshot, because a link can fail in
ways that do not look alike. It can unlock outright, which is obvious. It can
stay locked while decode errors climb, which is the interesting case and the
one a frame count alone will not see. It can keep passing frames that have
stopped changing. And it can slow down, which the sequence numbers give away
before anything else does.

The chips' error counters clear when they are read, so a sample's count is the
errors since the previous sample. Gauntlet keeps the running totals, because it
is the only reader and a count consumed by a panel refresh would otherwise go
missing from the run.
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

from suite.link import Camera, LinkError, Reading, Snapshot
from suite.profile import TidMax96793Profile

_CAMERA = "camera"
_FRAME_BYTES = "frame_bytes"
_PREVIOUS_IMAGE = "previous_image"
_PREVIOUS_SEQUENCE = "previous_sequence"
_REPEATS = "repeats"


def _setup(ctx: SuiteContext) -> None:
    """Take the instrument, record what is on the link, and note the baseline."""
    profile: TidMax96793Profile = ctx.profile
    if profile.driver == "mock":
        info("driver=mock — no instrument contacted, the link is synthesised")
        ctx.extras[_CAMERA] = None
        ctx.extras[_FRAME_BYTES] = 0
        return

    granted = ctx.env.capability("camera")
    camera = Camera(granted.url)
    ctx.extras[_CAMERA] = camera
    try:
        state = camera.state()
    except LinkError as exc:
        warn(f"{granted.instance_id}: could not read the camera's state: {exc}")
        ctx.extras[_FRAME_BYTES] = 0
        return

    form = state.get("format") or {}
    ctx.extras[_FRAME_BYTES] = int(form.get("sizeimage") or 0)
    info(
        f"{granted.instance_id}: {form.get('width', '?')}x{form.get('height', '?')} "
        f"{form.get('fourcc', '?')}, {ctx.extras[_FRAME_BYTES]} bytes a frame"
    )

    try:
        reading = camera.link_status()
    except LinkError as exc:
        warn(f"could not read the GMSL link: {exc}")
        return
    if not reading.chips:
        warn("no GMSL chips answered — the link telemetry will be empty")
        return

    identity = reading.identity
    if identity.get("uuid"):
        info(
            f"adapter {identity['uuid']} hw {identity.get('hardware_revision', '?')} fw {identity.get('firmware_revision', '?')}"
        )
    for address, chip in sorted(reading.chips.items()):
        part = " <- under the beam" if address == profile.part_address else ""
        info(
            f"chip {address} id {chip.get('dev_id', '?')} rev {chip.get('dev_rev', '?')} "
            f"locked={bool(chip.get('locked'))}{part}"
        )
    if profile.part_address not in reading.chips:
        warn(
            f"the part under test at {profile.part_address} did not answer; "
            f"chips found: {', '.join(sorted(reading.chips)) or 'none'}"
        )
        warn("the camera head may not be powered")
    # The first read clears whatever accumulated before the run, so the counts
    # from here on belong to this run.
    info("baseline read taken — the chips' counters start from zero for this run")

    # One burst before the run starts, for two reasons: it reports the rate the
    # link is managing with the beam off, which is what later samples are
    # judged against, and it leaves the capture queue drained so the first
    # sample measures live frames rather than a backlog.
    try:
        baseline = camera.stream_stats(frames=profile.burst_frames)
    except LinkError as exc:
        warn(f"could not measure the stream: {exc}")
        return
    info(
        f"baseline stream: {baseline.get('fps', 0):.1f}fps {baseline.get('mbps', 0):.0f}Mbps "
        f"dropped={int(baseline.get('dropped', 0))} corrupt={int(baseline.get('corrupt', 0))}"
    )


def _mock_reading(ctx: SuiteContext, ictx: IterationContext) -> Reading:
    """A healthy link, for a run with nothing to ask."""
    profile: TidMax96793Profile = ctx.profile
    chip = {
        "address": profile.part_address,
        "decode_errors_a": 0,
        "decode_errors_a_total": 0,
        "decode_errors_b": 0,
        "decode_errors_b_total": 0,
        "dev_id": "0xb6",
        "dev_rev": "0x00",
        "errors_total": 0,
        "idle_errors": 0,
        "idle_errors_total": 0,
        "link_error": False,
        "locked": True,
        "saturated": False,
        "saturations": 0,
        "total_errors": 0,
        "unlocks": 0,
    }
    return Reading(chips={profile.part_address: chip}, identity={"uuid": "mock"})


def _mock_snapshot(ctx: SuiteContext, ictx: IterationContext) -> Snapshot:
    """A still that changes every sample, so the frozen-frame check has work."""
    step = ictx.iteration % 251
    return Snapshot(
        height=48,
        image=bytes([step]) * 64,
        mean_luma=float(64 + step % 32),
        sequence=ictx.iteration * 19,
        sharpness=4.0,
        suffix=".bin",
        width=64,
    )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One sample: read both ends of the link, then keep a frame."""
    profile: TidMax96793Profile = ctx.profile
    camera: Camera | None = ctx.extras.get(_CAMERA)
    phases: list[PhaseRecord] = []

    with PhaseTimer("link", phases) as phase:
        try:
            reading = _mock_reading(ctx, ictx) if camera is None else camera.link_status()
        except LinkError as exc:
            return IterationOutcome(
                success=False,
                reason=f"link unreadable: {exc}",
                metrics={},
                phase_records=phases,
                summary="link unreadable",
            )
        phase.set_detail(chips=str(len(reading.chips)))

    if reading.error:
        # The chips stopped answering. That is the measurement, so it is
        # recorded as a failed sample rather than ending the run.
        return IterationOutcome(
            success=False,
            reason=f"link stopped answering: {reading.error}",
            metrics={"link": {"answered": 0, "locked": 0}},
            phase_records=phases,
            summary="link gone",
        )

    part = reading.chip(profile.part_address)
    far = {address: chip for address, chip in reading.chips.items() if address != profile.part_address}

    shot: Snapshot | None = None
    snapshot_error = ""
    with PhaseTimer("stream", phases) as phase:
        try:
            stream = camera.stream_stats(frames=profile.burst_frames) if camera else _mock_stream(ctx)
        except LinkError as exc:
            stream = {"error": 1.0}
            phase.set_detail(error=str(exc))
        else:
            phase.set_detail(fps=f"{stream.get('fps', 0):.1f}")

    with PhaseTimer("snapshot", phases) as phase:
        phase.set_detail(width=str(profile.max_width))
        try:
            shot = _mock_snapshot(ctx, ictx) if camera is None else camera.snapshot(max_width=profile.max_width)
        except LinkError as exc:
            snapshot_error = str(exc)

    images: list[str] = []
    if shot is not None and profile.snapshot_every and ictx.iteration % profile.snapshot_every == 0:
        with PhaseTimer("write", phases) as phase:
            relative = f"frames/link_{ictx.iteration:05d}{shot.suffix}"
            ctx.artifact(*relative.split("/")).write_bytes(shot.image)
            phase.set_detail(bytes=str(len(shot.image)))
            images.append(relative)

    video = _video_metrics(ctx, stream, shot)
    repeats = ctx.extras.get(_REPEATS, 0)
    if shot is not None:
        repeats = repeats + 1 if shot.image == ctx.extras.get(_PREVIOUS_IMAGE) else 0
        ctx.extras[_PREVIOUS_IMAGE] = shot.image
    ctx.extras[_REPEATS] = repeats

    metrics: dict[str, Any] = {
        "link": _link_metrics(part, repeats),
        "video": video,
    }
    for address, chip in sorted(far.items()):
        metrics[f"far_{address}"] = {
            "errors": int(chip.get("total_errors") or 0),
            "errors_total": int(chip.get("errors_total") or 0),
            "locked": 1 if chip.get("locked") else 0,
        }
    if images:
        metrics["images"] = images

    reason = _fault(part, far, stream, shot, snapshot_error, repeats, profile)
    return IterationOutcome(
        success=not reason,
        reason=reason,
        metrics=metrics,
        phase_records=phases,
        summary=_summary(part, video, shot),
    )


def _link_metrics(part: dict[str, Any], repeats: int) -> dict[str, Any]:
    """The part's own figures, flattened for charting."""
    return {
        "answered": 1 if part else 0,
        "decode_errors_a": int(part.get("decode_errors_a") or 0),
        "decode_errors_b": int(part.get("decode_errors_b") or 0),
        "errors": int(part.get("total_errors") or 0),
        "errors_total": int(part.get("errors_total") or 0),
        "idle_errors": int(part.get("idle_errors") or 0),
        "link_error": 1 if part.get("link_error") else 0,
        "locked": 1 if part.get("locked") else 0,
        "repeats": repeats,
        "saturated": 1 if part.get("saturated") else 0,
        "unlocks": int(part.get("unlocks") or 0),
    }


def _video_metrics(ctx: SuiteContext, stream: dict[str, float], shot: Snapshot | None) -> dict[str, Any]:
    """What the link carried, measured rather than inferred.

    The rate comes from a burst of frames read back to back. Counting frames
    between samples instead would measure the sampling rate, because the driver
    stops capturing once every buffer is full and the frames arriving in the
    gap are never counted.
    """
    metrics: dict[str, Any] = {
        "arrived": 1 if shot is not None else 0,
        "corrupt": int(stream.get("corrupt", 0)),
        "dropped": int(stream.get("dropped", 0)),
        "fps": stream.get("fps", 0.0),
        "mbps": stream.get("mbps", 0.0),
    }
    if shot is not None:
        metrics["mean_luma"] = shot.mean_luma
        metrics["sequence"] = shot.sequence
        metrics["sharpness"] = shot.sharpness
    return metrics


def _mock_stream(ctx: SuiteContext) -> dict[str, float]:
    """A healthy burst, for a run with nothing to ask."""
    return {"bytes": 0.0, "corrupt": 0.0, "dropped": 0.0, "elapsed_s": 0.1, "fps": 19.4, "frames": 8.0, "mbps": 2574.0}


def _fault(
    part: dict[str, Any],
    far: dict[str, dict[str, Any]],
    stream: dict[str, float],
    shot: Snapshot | None,
    snapshot_error: str,
    repeats: int,
    profile: TidMax96793Profile,
) -> str:
    """Why this sample is bad, or an empty string when it is fine."""
    if not part:
        # Naming what did answer separates a wrong address from an unreachable
        # part: the adapter's own chip answers whether or not the link is up, so
        # a bus holding only that one places the fault across the link.
        if not far:
            return (
                f"no chip answered at {profile.part_address}, and no other chip answered either: "
                "the adapter's I2C tunnel is not responding"
            )
        return (
            f"no chip answered at {profile.part_address}; the bus holds {', '.join(sorted(far))}. "
            "The camera head is probably not powered: its supply is separate from the USB lead, "
            "and without it the adapter's own chip still answers while the part across the link "
            "stays silent. Check the head's power first, then that the profile names the right "
            "address for this part"
        )
    if not part.get("locked"):
        return f"the part at {profile.part_address} reports its GMSL link is down"
    errors = int(part.get("total_errors") or 0)
    if errors > profile.max_errors_per_sample:
        return f"{errors} link errors in one sample, above {profile.max_errors_per_sample}"
    if part.get("saturated"):
        return "a link error counter saturated: the true count is higher than reported"
    corrupt = int(stream.get("corrupt", 0))
    if corrupt > profile.max_corrupt_frames:
        return f"{corrupt} corrupt frames in one burst, above {profile.max_corrupt_frames}"
    dropped = int(stream.get("dropped", 0))
    if dropped > profile.max_dropped_frames:
        return f"{dropped} frames dropped in one burst, above {profile.max_dropped_frames}"
    if snapshot_error:
        return f"no frame: {snapshot_error}"
    if shot is not None and shot.mean_luma < profile.min_mean_luma:
        return f"frame is dark: mean luma {shot.mean_luma:.1f} below {profile.min_mean_luma:.1f}"
    return ""


def _summary(part: dict[str, Any], video: dict[str, Any], shot: Snapshot | None) -> str:
    """One line for the log, saying what the link did this sample."""
    lock = "locked" if part.get("locked") else "UNLOCKED"
    errors = int(part.get("total_errors") or 0)
    rate = video.get("mbps")
    tail = f" {rate:.0f}Mbps" if isinstance(rate, (int, float)) else ""
    dropped = video.get("dropped") or 0
    corrupt = video.get("corrupt") or 0
    faults = f" dropped={dropped} corrupt={corrupt}" if (dropped or corrupt) else ""
    return f"{lock} errors={errors}{faults}{tail}"


def _series(outcomes: list[IterationOutcome], group: str, key: str) -> list[float]:
    """Every value recorded for one measurement."""
    return [
        value
        for outcome in outcomes
        if isinstance(value := outcome.metrics.get(group, {}).get(key), (int, float)) and not isinstance(value, bool)
    ]


def _evaluate(outcomes: list[IterationOutcome], profile: TidMax96793Profile) -> tuple[bool, str] | None:
    """The part passes while its link stays up and its errors stay bounded."""
    if not outcomes:
        return False, "no samples taken"

    unlocked = sum(1 for value in _series(outcomes, "link", "locked") if value == 0)
    if unlocked > profile.max_unlocks:
        return False, f"the GMSL link was down for {unlocked} of {len(outcomes)} samples"

    totals = _series(outcomes, "link", "errors_total")
    if totals and totals[-1] > profile.max_total_errors:
        return False, f"{int(totals[-1])} link errors over the run, above {profile.max_total_errors}"

    missed = sum(1 for value in _series(outcomes, "video", "arrived") if value == 0)
    if missed > profile.max_missed_snapshots:
        return False, f"{missed} of {len(outcomes)} samples produced no frame"

    silent = sum(1 for value in _series(outcomes, "link", "answered") if value == 0)
    if silent:
        return False, f"the part stopped answering on {silent} of {len(outcomes)} samples"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidMax96793Profile,
) -> list[dict[str, object]]:
    """What the link did, as the numbers this test exists to report."""
    totals = _series(outcomes, "link", "errors_total")
    locked = _series(outcomes, "link", "locked")
    corrupt = _series(outcomes, "video", "corrupt")
    dropped = _series(outcomes, "video", "dropped")
    fps = _series(outcomes, "video", "fps")
    mbps = [value for value in _series(outcomes, "video", "mbps") if value > 0]

    rows: list[dict[str, object]] = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result("reception", "Reception", _reception(outcomes), format="text"),
        make_result("link_errors", "Link errors", int(totals[-1]) if totals else 0, format="int"),
        make_result(
            "unlocked_samples",
            "Samples with the link down",
            sum(1 for value in locked if value == 0),
            format="int",
        ),
        make_result("dropped_frames", "Dropped frames", int(sum(dropped)), format="int"),
        make_result("corrupt_frames", "Corrupt frames", int(sum(corrupt)), format="int"),
    ]
    if fps:
        rows.append(
            make_result(
                "frame_rate",
                "Frame rate mean",
                round(sum(fps) / len(fps), 1),
                format="decimal",
                precision=1,
                unit="fps",
            )
        )
    if mbps:
        rows.append(
            make_result(
                "data_rate_mbps",
                "Data rate mean",
                round(sum(mbps) / len(mbps), 1),
                format="decimal",
                precision=1,
                unit="Mbps",
            )
        )
        rows.append(make_result("data_rate_span", "Data rate min to max", f"{min(mbps):.0f} to {max(mbps):.0f} Mbps"))
    saturations = _series(outcomes, "link", "saturated")
    if any(saturations):
        rows.append(
            make_result(
                "saturated_samples",
                "Samples with a saturated counter",
                int(sum(saturations)),
                format="int",
            )
        )
    return rows


def _reception(outcomes: list[IterationOutcome]) -> str:
    """Image reception status, as the test plan asks for it."""
    arrived = sum(1 for value in _series(outcomes, "video", "arrived") if value == 1)
    return f"{arrived} of {len(outcomes)} samples returned an image"


def _hardware(ctx: SuiteContext, profile: TidMax96793Profile) -> dict[str, dict[str, str]]:
    """What the run was measured with, for the manifest."""
    return {
        "camera": {
            "driver": profile.driver,
            "instance": ctx.env.capabilities["camera"].instance_id if profile.driver == "real" else "",
            "part_address": profile.part_address,
            "part_under_beam": "MAX96793GTJ/VY+",
        }
    }


def _profile_summary(ctx: SuiteContext, profile: TidMax96793Profile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "max_errors_per_sample": str(profile.max_errors_per_sample),
        "part_address": profile.part_address,
        "sample_period_s": str(profile.sample_period_s),
    }


SPEC = SuiteSpec(
    name="tid_max96793",
    profile_model=TidMax96793Profile,
    setup=_setup,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    profile_summary=_profile_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
