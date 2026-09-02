"""Profile model for the logic capture suite."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The rates the analyzer takes. Each divides its 48 or 30 MHz clock exactly,
# which is what makes it a divisor rather than a setting. Stated here for the
# error an operator sees when a profile names something else; the instrument
# is the final authority and refuses the rest.
RATES = (
    "24mhz",
    "16mhz",
    "12mhz",
    "8mhz",
    "6mhz",
    "4mhz",
    "3mhz",
    "2mhz",
    "1mhz",
    "500khz",
    "250khz",
    "200khz",
    "100khz",
    "50khz",
    "25khz",
    "20khz",
)

# How much of the signal one capture covers.
WINDOWS = ("1ms", "10ms", "100ms")

# What a probe is there to confirm. `any` records the line without judging it,
# which is the right answer for one being watched rather than tested.
EXPECTATIONS = ("any", "active", "high", "low")

_SLUG = re.compile(r"[^a-z0-9]+")


def metric_key(label: str, channel: str) -> str:
    """The name a channel's readings are recorded under.

    A metric name is an identifier in a chart legend and a CSV header, so the
    operator's label is folded to lower case with runs of anything else turned
    into single underscores. A label that leaves nothing behind falls back to
    the channel number, because a series has to be named something.
    """
    slug = _SLUG.sub("_", label.strip().lower()).strip("_")
    return slug or f"ch{channel}"


class Channel(BaseModel):
    """One probe: what it is clipped to, and what it is expected to do."""

    model_config = ConfigDict(extra="forbid", title="Channel")

    channel: str = Field(pattern=r"^[1-8]$", description="Probe number, 1 to 8.")
    label: str = Field(
        default="",
        max_length=32,
        description="What it is clipped to. Names the reading everywhere, including its metric.",
    )
    expect: str = Field(
        default="any",
        description=f"One of: {', '.join(EXPECTATIONS)}. `any` records the line without judging it.",
    )

    @field_validator("expect")
    @classmethod
    def _known_expectation(cls, value: str) -> str:
        if value not in EXPECTATIONS:
            raise ValueError(f"expect must be one of {', '.join(EXPECTATIONS)}")
        return value

    @property
    def key(self) -> str:
        """The metric name this probe's readings are recorded under."""
        return metric_key(self.label, self.channel)


class LogicCaptureProfile(BaseModel):
    """A capture session: how often, how wide a window, and what each probe is."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a capture and contacts no instrument.",
    )
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to keep capturing. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between captures.")
    rate: str = Field(default="24mhz", description=f"Sample rate. One of: {', '.join(RATES)}.")
    window: str = Field(
        default="10ms",
        description=f"How much signal one capture covers. One of: {', '.join(WINDOWS)}.",
    )
    channels: list[Channel] = Field(
        default_factory=lambda: [Channel(channel="1")],
        min_length=1,
        description="The probes to name and record. The analyzer captures all eight either way.",
    )
    save_traces: bool = Field(
        default=True,
        description="Keep a picture of every capture. Off for a long run, which would keep thousands.",
    )
    max_failed_samples: int = Field(
        default=0,
        ge=0,
        description="Captures that may miss what a probe expects before the run fails.",
    )

    @field_validator("rate")
    @classmethod
    def _known_rate(cls, value: str) -> str:
        if value not in RATES:
            raise ValueError(f"rate must be one of {', '.join(RATES)}")
        return value

    @field_validator("window")
    @classmethod
    def _known_window(cls, value: str) -> str:
        if value not in WINDOWS:
            raise ValueError(f"window must be one of {', '.join(WINDOWS)}")
        return value

    @model_validator(mode="after")
    def _channels_are_distinct(self) -> LogicCaptureProfile:
        """No probe twice, and no two probes under one metric name.

        Two rows for the same probe would record it twice, and two labels
        folding to the same key would silently overwrite one series with the
        other.
        """
        numbers = [channel.channel for channel in self.channels]
        duplicate = next((n for n in numbers if numbers.count(n) > 1), None)
        if duplicate is not None:
            raise ValueError(f"channel {duplicate} is listed more than once")
        keys = [channel.key for channel in self.channels]
        clash = next((k for k in keys if keys.count(k) > 1), None)
        if clash is not None:
            raise ValueError(f"two channels would record under the same metric name {clash!r}")
        return self
