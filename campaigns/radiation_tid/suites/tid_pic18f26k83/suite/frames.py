"""The '@' telemetry stream, as lines off the wire.

The PMU3 firmware emits a machine-readable frame stream on the same debug UART
as its human console, described in ``docs/reference/firmware.md`` sections 4.11
and 7.4 of ``extras/trl-pmu-firmware``. It exists for exactly this test: a
count of detections a host can timestamp and bin against a facility's fluence,
and a liveness beat that turns silence into a measurement rather than an
ambiguity.

Nothing here opens a port. Everything a wire can produce -- a truncated line, a
corrupted field, a frame from a part that just rebooted -- is a string, so all
of it is exercised without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A frame's leading character, and the whole discrimination scheme between this
# stream and the console text sharing the wire. No human-facing line may start
# with one, which the firmware's own tests pin.
PREFIX = "@"

# The CODE word an '@E' line carries, against the short tag the '@S' summary
# and console 'n' count the same detection under. Both spellings reach a host;
# metrics are keyed on the short one so a detection and its running total line
# up in the same chart.
CODE_TAGS: dict[str, str] = {
    "CFGWORD": "cfg",
    "FAULT": "flt",
    "FLASHCRC": "flash",
    "LATCH": "lat",
    "SCRUBREF": "ref",
    "SFRUPSET": "sfr",
    "SRAMSTUCK": "stuck",
    "SRAMUPSET": "sram",
    "TEST": "test",
}

TAGS: tuple[str, ...] = tuple(sorted(CODE_TAGS.values()))

# seq is a 16-bit RAM counter and wraps rather than saturating, because a host
# takes differences of it.
_SEQ_MODULUS = 65536


@dataclass
class Frame:
    """One parsed line of the stream.

    ``kind`` is ``B`` for the boot line, ``T`` for the liveness beat, ``E`` for
    a detection and ``S`` for the periodic summary of every counter.
    """

    kind: str
    seq: int
    ms: int
    boot: int
    code: str = ""
    fields: dict[str, str] = field(default_factory=dict)


def parse(line: str) -> Frame | None:
    """One line as a frame, or ``None`` if it is not one.

    A malformed frame is not an error to raise. A corrupted line is itself
    something this run is looking for, so the caller counts what did not parse
    rather than stopping on it.
    """
    text = line.strip()
    if not text.startswith(PREFIX):
        return None
    parts = text[len(PREFIX) :].split(",")
    if len(parts) < 4:
        return None
    kind = parts[0]
    if len(kind) != 1:
        return None
    try:
        seq, ms, boot = (int(part) for part in parts[1:4])
    except ValueError:
        return None

    code = ""
    fields: dict[str, str] = {}
    for part in parts[4:]:
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key] = value
        elif not code:
            code = part
    return Frame(kind=kind, seq=seq, ms=ms, boot=boot, code=code, fields=fields)


def seq_delta(previous: int, current: int) -> int:
    """Frames between two sequence numbers, across the 16-bit wrap."""
    return (current - previous) % _SEQ_MODULUS


def classify(previous: Frame, current: Frame, beat_ms: float, tolerance_ms: float) -> str:
    """What the gap between two beats says, per section 7.4's table.

    ``seq`` and ``ms`` are redundant on purpose and the redundancy is the
    detector: ``seq`` is a RAM counter and so an upset target, ``ms`` comes
    from Timer0. Neither alone separates a corrupted counter from a lost frame
    from a stopped clock.
    """
    frames = seq_delta(previous.seq, current.seq)
    elapsed = current.ms - previous.ms
    if frames < 1:
        return "seq_stalled"
    if abs(elapsed - beat_ms) <= tolerance_ms:
        return "healthy" if frames == 1 else "seq_jumped"
    if abs(elapsed - beat_ms * frames) <= tolerance_ms * frames:
        return "frames_lost" if frames > 1 else "healthy"
    return "clock_moved"


def counters(frame: Frame) -> dict[str, int]:
    """The per-code totals an '@S' summary carries."""
    totals: dict[str, int] = {}
    for tag in (*TAGS, "drop"):
        raw = frame.fields.get(tag)
        if raw is None:
            continue
        try:
            totals[tag] = int(raw)
        except ValueError:
            continue
    return totals
