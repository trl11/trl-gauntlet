"""Profile model for the PIC18F26K83 dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConsoleBlock(BaseModel):
    """The debug UART, which is the whole of the bench today.

    ``device`` set to ``auto`` takes the one candidate tty on the host and
    refuses when there are several, rather than guessing which of two boards
    is the one under test.
    """

    model_config = ConfigDict(extra="forbid", title="Console")

    device: str = Field(default="auto", description="Serial device, or `auto` to find the one tty.")
    baud: int = Field(default=115200, gt=0, description="Must match the firmware's UART, which is 115200 8N1.")
    read_timeout_s: float = Field(default=0.25, gt=0, description="How long one read waits for the wire.")
    reply_timeout_s: float = Field(
        default=6.0,
        gt=0,
        description="How long a typed key has to be answered. A streaming board hears one only between frames.",
    )
    settle_s: float = Field(
        default=3.0,
        ge=0,
        description="Seconds to let the board talk before the first tick is judged.",
    )


class PassCriteria(BaseModel):
    """What ends the run's claim that the part is still working."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    silence_timeout_s: float = Field(
        default=5.0,
        gt=0,
        description="Seconds without a beat before the part counts as stopped.",
    )
    allow_resets: int = Field(default=0, ge=0, description="Reboots tolerated before the session fails.")
    min_beats: int = Field(
        default=1,
        ge=0,
        description="Floor on beats received, so a silent board fails even with no measurable gaps.",
    )


class TidPic18f26k83Profile(BaseModel):
    """One dose session against a PIC18F26K83 running PMU3.

    The measurement is the firmware's own '@' telemetry stream, read over the
    debug UART. Detections are recorded and never fail a tick -- they are the
    data. What fails a tick is the part going quiet or rebooting.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises the stream and opens no port.",
    )
    duration_s: float = Field(
        default=300.0,
        ge=0,
        description="How long to sample. 0 runs until the operator stops the run.",
    )
    sample_period_s: float = Field(default=2.0, gt=0, description="Seconds between accounting ticks.")
    beat_ms: float = Field(
        default=1158.0,
        gt=0,
        description="Expected '@T' period. Loop-quantised, measured at 1158ms on a Curiosity HPC, not 1000.",
    )
    beat_tolerance_ms: float = Field(
        default=400.0,
        gt=0,
        description="Slack on that period, which is about one superloop pass.",
    )
    firmware_image: str = Field(
        default="auto",
        description="The .hex the part is programmed with, recorded with the run. `auto` finds the one in the tree.",
    )
    console: ConsoleBlock = Field(default_factory=ConsoleBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
