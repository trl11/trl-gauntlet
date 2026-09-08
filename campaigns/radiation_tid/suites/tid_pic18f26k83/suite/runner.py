"""Read the PIC's own telemetry stream, and record what it says and when.

Total ionising dose characterisation of the PIC18F26K83-E/SS running PMU3.
What is implemented is the half of the bench that exists today: the debug UART
and nothing else. ``README.md`` is the whole design, including the four
measurements this does not take yet -- supply current, brown-out trip point,
sequencer timing and converter drift -- and what each of them needs on the
bench first.

The measurement is the firmware's '@' frame stream, which was written for a
radiation campaign. Three things come off it:

*Liveness.* Every detector in that firmware runs inside the part being
measured, so a hung superloop stops them silently and the silence reads as a
clean run. The '@T' beat is the answer, and its absence is what fails a tick.

*Detections.* An '@E' line is a counter moving, not a fault. It is recorded as
an anomaly and the loop carries on, because a count of upsets binned against a
facility's fluence is the result of a dose session rather than an error in it.

*Time base.* Each frame carries the board's own millisecond count beside a
sequence number. Compared against the host's clock across a session, that is a
measurement of the part's oscillator, which is one of the parameters dose
moves -- at far lower resolution than a logic analyzer on the UART would give,
but for nothing, from frames that had to be read anyway.
"""

from __future__ import annotations

import time
from pathlib import Path
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

from suite import firmware, frames
from suite.board import Board, report_counters
from suite.console import Console, ConsoleError, describe_device, resolve_device
from suite.mock import MockBoard
from suite.profile import TidPic18f26k83Profile

_ANOMALIES = "anomalies"
_BOARD = "board"
_BOOT = "boot"
_FIRMWARE = "firmware"
_IMAGE = "image"
_HOST_ORIGIN = "host_origin"
_LAST_BEAT = "last_beat"
_CLOCK_PAIRS = "clock_pairs"
_LAST_SEEN = "last_seen"
_ORIGIN = "board_origin"
_RESETS = "resets"
_TOTALS = "totals"
_UNPARSED = "unparsed"

_PROBE = "stream"

# What the clock fit needs before it means anything. The noise on one point is
# a read timeout; over this many beats and this long a span it averages to well
# under the drift a dose session is looking for.
_CLOCK_MIN_BEATS = 30
_CLOCK_MIN_SPAN_S = 60.0


def _setup(ctx: SuiteContext) -> None:
    """Open the console, leave the frame stream on, and let the board talk."""
    profile: TidPic18f26k83Profile = ctx.profile
    ctx.extras[_ANOMALIES] = AnomalyLog(ctx.jsonl)
    ctx.extras[_BOOT] = None
    ctx.extras[_FIRMWARE] = {}
    ctx.extras[_IMAGE] = {}
    ctx.extras[_HOST_ORIGIN] = None
    ctx.extras[_CLOCK_PAIRS] = []
    ctx.extras[_LAST_BEAT] = None
    ctx.extras[_LAST_SEEN] = time.monotonic()
    ctx.extras[_ORIGIN] = None
    ctx.extras[_RESETS] = 0
    ctx.extras[_TOTALS] = {}
    ctx.extras[_UNPARSED] = 0

    if profile.driver == "mock":
        info("driver=mock — no port opened, the stream is synthesised")
        ctx.extras[_BOARD] = MockBoard()
        return

    device = resolve_device(profile.console.device, profile.console.baud, profile.console.reply_timeout_s)
    board = Board(Console(device, profile.console.baud))
    ctx.extras[_BOARD] = board
    line = board.enable_stream(profile.console.reply_timeout_s)
    counters = report_counters(line)
    info(f"{device} ({describe_device(device) or 'unknown'}): {line}")
    _record_firmware(ctx, board, profile)
    if counters.get("ev", 0) or counters.get("drop", 0):
        # Counters move whether or not the stream was on, so a board that was
        # detecting before anyone listened says so here and nowhere else.
        warn(
            f"the board counted {counters.get('ev', 0)} detections and "
            f"{counters.get('drop', 0)} drops before this run started"
        )
    if profile.console.settle_s:
        time.sleep(profile.console.settle_s)


def _record_firmware(ctx: SuiteContext, board: Board, profile: TidPic18f26k83Profile) -> None:
    """Ask the board what it is running, and check it against the tree.

    The board is asked rather than the boot frame read, because '@B' is emitted
    once when the stream is enabled and a run that joins a board already
    streaming never sees one.

    Nothing is programmed here and no image is copied. A part is flashed before
    a campaign, not before a run; what a run owes is a record of which image it
    was measuring, which is the name and the digest.
    """
    try:
        line = board.ask(firmware.version_key(), firmware.version_marker(), profile.console.reply_timeout_s)
    except ConsoleError as exc:
        warn(f"the board would not report its version: {exc}")
        return
    identity = firmware.identify(line)
    ctx.extras[_FIRMWARE] = identity
    info(f"running {line}")

    image = firmware.locate(profile.firmware_image, Path(__file__).resolve().parents[1])
    if image is None:
        warn("no firmware image in the tree to check the board against; its own report is the only record")
        return
    record = {"name": image.name, "sha256": firmware.digest(image)}
    declared = firmware.image_version(image)
    running = identity.get("fw", "")
    if declared and running and declared != running:
        record["mismatch"] = f"image is {declared}, board is running {running}"
        warn(
            f"the part is running {running} but the image in the tree is {declared}. "
            "Reflash it, or this run is not characterising what the tree says it is."
        )
    ctx.extras[_IMAGE] = record
    info(f"image {image.name} sha256={record['sha256'][:12]}")


def _teardown(ctx: SuiteContext) -> None:
    """Drop the port. The board keeps streaming, which is what a bench wants."""
    board = ctx.extras.get(_BOARD)
    if board is not None:
        board.close()


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Drain the wire, account for what came off it, and judge the silence."""
    profile: TidPic18f26k83Profile = ctx.profile
    phases: list[PhaseRecord] = []

    with PhaseTimer("read", phases) as phase:
        try:
            lines = _drain(ctx, ictx, profile)
        except ConsoleError as exc:
            return IterationOutcome(
                success=False,
                reason=str(exc),
                phase_records=phases,
                summary="console lost",
            )
        phase.set_detail(lines=len(lines))

    with PhaseTimer("account", phases):
        beats = _account(ctx, ictx, lines)

    gap_s = time.monotonic() - ctx.extras[_LAST_SEEN]
    metrics = _metrics(ctx, beats, gap_s)

    if gap_s > profile.pass_criteria.silence_timeout_s:
        _flag(ctx, ictx, "silent", {"gap_s": round(gap_s, 2)})
        return IterationOutcome(
            success=False,
            reason=f"no beat for {gap_s:.1f}s",
            metrics=metrics,
            phase_records=phases,
            summary=f"silent {gap_s:.1f}s",
        )

    totals: dict[str, int] = ctx.extras[_TOTALS]
    return IterationOutcome(
        success=True,
        metrics=metrics,
        phase_records=phases,
        summary=f"{beats} beats, {sum(totals.values())} detections, gap {gap_s:.1f}s",
    )


def _drain(ctx: SuiteContext, ictx: IterationContext, profile: TidPic18f26k83Profile) -> list[str]:
    """Read across the whole tick rather than once at the start of it.

    A beat sitting in the kernel's buffer until the next tick is timestamped
    when it was collected, not when it arrived, and the clock measurement is
    the difference between those two timestamps. Reading throughout bounds
    that error at one read timeout instead of one sample period.
    """
    board = ctx.extras[_BOARD]
    # Monotonic, not ictx.start_time: that one is wall clock, and the two are
    # not comparable.
    deadline = time.monotonic() + ctx.sample_period_s
    lines = board.poll(profile.console.read_timeout_s)
    while time.monotonic() < deadline:
        lines.extend(board.poll(profile.console.read_timeout_s))
    return lines


def _account(ctx: SuiteContext, ictx: IterationContext, lines: list[str]) -> int:
    """Fold one tick's lines into the running state, and count the beats."""
    beats = 0
    for line in lines:
        if not line.startswith(frames.PREFIX):
            continue
        frame = frames.parse(line)
        if frame is None:
            ctx.extras[_UNPARSED] += 1
            _flag(ctx, ictx, "unparsed", {"line": line[:120]})
            continue
        _reset_check(ctx, ictx, frame)
        if frame.kind == "B":
            _booted(ctx, ictx, frame)
        elif frame.kind == "T":
            beats += 1
            _beat(ctx, frame)
        elif frame.kind == "E":
            _detection(ctx, ictx, frame)
        elif frame.kind == "S":
            ctx.extras[_TOTALS].update(frames.counters(frame))
        ctx.extras[_LAST_SEEN] = time.monotonic()
    return beats


def _beat(ctx: SuiteContext, frame: frames.Frame) -> None:
    """Record one liveness beat, and where both clocks stood when it arrived.

    The host time is taken here rather than when the metrics are built, or a
    tick that drained no beat would compare a board clock that stood still
    against a host clock that did not, and report the gap as drift.
    """
    now = time.monotonic()
    if ctx.extras[_ORIGIN] is None:
        ctx.extras[_ORIGIN] = frame.ms
        ctx.extras[_HOST_ORIGIN] = now
    ctx.extras[_LAST_BEAT] = frame
    ctx.extras[_CLOCK_PAIRS].append((now - ctx.extras[_HOST_ORIGIN], float(frame.ms - ctx.extras[_ORIGIN])))


def _reset_check(ctx: SuiteContext, ictx: IterationContext, frame: frames.Frame) -> None:
    """A boot count that moved is the part having restarted under us."""
    previous = ctx.extras[_BOOT]
    if previous is None:
        ctx.extras[_BOOT] = frame.boot
        return
    if frame.boot == previous:
        return
    ctx.extras[_BOOT] = frame.boot
    ctx.extras[_RESETS] += 1
    # The clocks restart with the part, so a drift measured across a reboot
    # would be the reboot rather than the oscillator.
    ctx.extras[_ORIGIN] = None
    ctx.extras[_LAST_BEAT] = None
    ctx.extras[_CLOCK_PAIRS] = []
    _flag(ctx, ictx, "reset", {"boot": frame.boot, "previous": previous})
    warn(f"the part restarted: boot {previous} -> {frame.boot}")


def _booted(ctx: SuiteContext, ictx: IterationContext, frame: frames.Frame) -> None:
    """The '@B' line, which is what makes a gap in the log explicable."""
    if frame.fields.get("fw"):
        ctx.extras[_FIRMWARE] = dict(ctx.extras[_FIRMWARE], fw=frame.fields["fw"])
    _flag(
        ctx,
        ictx,
        "boot",
        {"cause": frame.code or frame.fields.get("rst", ""), "bits": frame.fields.get("bits", "")},
    )
    info(f"@B rst={frame.fields.get('rst', '?')} fw={frame.fields.get('fw', '?')}")


def _detection(ctx: SuiteContext, ictx: IterationContext, frame: frames.Frame) -> None:
    """One '@E' line. Data, not a failure, so nothing here fails a tick."""
    tag = frames.CODE_TAGS.get(frame.code, frame.code.lower())
    totals: dict[str, int] = ctx.extras[_TOTALS]
    totals[tag] = totals.get(tag, 0) + 1
    _flag(ctx, ictx, frame.code or "detection", {"ms": frame.ms, **frame.fields})


def _metrics(ctx: SuiteContext, beats: int, gap_s: float) -> dict[str, Any]:
    """What one tick contributes to the charts."""
    totals: dict[str, int] = ctx.extras[_TOTALS]
    metrics: dict[str, Any] = {
        "stream.beats": beats,
        "stream.gap_s": round(gap_s, 3),
        "stream.resets": ctx.extras[_RESETS],
        "stream.unparsed": ctx.extras[_UNPARSED],
        "events.total": sum(totals.values()),
    }
    for tag in frames.TAGS:
        metrics[f"events.{tag}"] = totals.get(tag, 0)
    drift = _clock_ppm(ctx)
    if drift is not None:
        metrics["stream.clock_ppm"] = round(drift, 1)
    beat = ctx.extras[_LAST_BEAT]
    if beat is not None:
        metrics["stream.seq"] = beat.seq
        metrics["stream.board_ms"] = beat.ms
    return metrics


def _clock_ppm(ctx: SuiteContext) -> float | None:
    """The board's own clock against the host's, in parts per million.

    Timer0 drives the millisecond count every frame carries, so this is the
    part's time base. It is not the firmware's absolute error that matters --
    that is a known -0.8% and this cannot separate it from the host's own
    accuracy -- but its movement across a session, which is the parameter dose
    walks.
    """
    pairs: list[tuple[float, float]] = ctx.extras[_CLOCK_PAIRS]
    # A beat is timestamped when it was collected rather than when its first
    # bit landed, so every point carries up to one read timeout of noise. Two
    # endpoints would be that noise; a line through all of them averages it
    # down, and a short baseline is refused outright.
    if len(pairs) < _CLOCK_MIN_BEATS or pairs[-1][0] < _CLOCK_MIN_SPAN_S:
        return None
    count = len(pairs)
    mean_host = sum(host for host, _ in pairs) / count
    mean_board = sum(board for _, board in pairs) / count
    covariance = sum((host - mean_host) * (board - mean_board) for host, board in pairs)
    variance = sum((host - mean_host) ** 2 for host, _ in pairs)
    if variance <= 0.0:
        return None
    return (covariance / variance / 1000.0 - 1.0) * 1e6


def _flag(ctx: SuiteContext, ictx: IterationContext, kind: str, detail: dict[str, Any]) -> None:
    """Record one anomaly against the stream."""
    ctx.extras[_ANOMALIES].record(_PROBE, kind, iteration=ictx.iteration, detail=detail)


def _evaluate(outcomes: list[IterationOutcome], profile: TidPic18f26k83Profile) -> tuple[bool, str] | None:
    """Pass unless the part stopped being the part."""
    if not outcomes:
        return False, "no samples collected"
    beats = sum(int(outcome.metrics.get("stream.beats", 0)) for outcome in outcomes)
    if beats < profile.pass_criteria.min_beats:
        return False, f"{beats} beats received, wanted at least {profile.pass_criteria.min_beats}"
    resets = max(int(outcome.metrics.get("stream.resets", 0)) for outcome in outcomes)
    if resets > profile.pass_criteria.allow_resets:
        return False, f"the part restarted {resets} times, allowed {profile.pass_criteria.allow_resets}"
    return None


def _hardware(ctx: SuiteContext, profile: TidPic18f26k83Profile) -> dict[str, dict[str, str]]:
    """What the run was taken against, for the manifest."""
    board = ctx.extras.get(_BOARD)
    identity: dict[str, str] = ctx.extras.get(_FIRMWARE) or {}
    image: dict[str, str] = ctx.extras.get(_IMAGE) or {}
    summary = {
        "dut": {
            "component": "PIC18F26K83-E/SS",
            "console": board.device if board is not None else profile.console.device,
            "firmware": identity.get("fw", ""),
            "api": identity.get("api", ""),
            "build": identity.get("build", ""),
        }
    }
    if image:
        summary["firmware_image"] = image
    return summary


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidPic18f26k83Profile,
) -> list[dict[str, Any]]:
    """Headline figures shown at the top of the run summary."""
    totals: dict[str, int] = ctx.extras[_TOTALS]
    figures = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("beats", "Beats", sum(int(o.metrics.get("stream.beats", 0)) for o in outcomes), format="int"),
        make_result("detections", "Detections", sum(totals.values()), format="int"),
        make_result("resets", "Resets", ctx.extras[_RESETS], format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    drift = _clock_ppm(ctx)
    if drift is not None:
        figures.insert(
            3, make_result("clock_ppm", "Clock drift", round(drift, 1), format="decimal", precision=1, unit="ppm")
        )
    return figures


SPEC = SuiteSpec(
    name="tid_pic18f26k83",
    profile_model=TidPic18f26k83Profile,
    iterate=_iterate,
    evaluate=_evaluate,
    setup=_setup,
    teardown=_teardown,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    hardware_summary=_hardware,
    verdict_results=_results,
)
