"""Profile model for the DAQmx capture suite."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SLUG = re.compile(r"[^a-z0-9]+")

# How NI names an analog input, and so how a profile names one. The module
# decides how many there are — four on an NI-9238, sixteen on some others — so
# the number is not bounded here and a channel the module does not have is
# refused by the instrument when the run configures it.
_CHANNEL = r"^ai\d+$"

# The shortest sample period worth asking for. One acquisition is a hundred
# samples at the module's own rate plus the round trip to the driver, which on
# the slowest module runs to about 0.1s, so a period under this would not be
# refused by the sample loop — it would simply run every iteration late.
MIN_SAMPLE_PERIOD_S = 0.25


def metric_key(label: str, channel: str) -> str:
    """The name a channel's readings are recorded under.

    A metric name is an identifier in a chart legend and a CSV header, so the
    operator's label is folded to lower case with runs of anything else turned
    into single underscores. A label that leaves nothing behind — punctuation
    only, or empty — falls back to the channel, because a series has to be
    named something and the channel is what the reading is called anyway.
    """
    slug = _SLUG.sub("_", label.strip().lower()).strip("_")
    return slug or channel


class Channel(BaseModel):
    """One analog input: what it is wired to, and the range to read it on."""

    model_config = ConfigDict(extra="forbid", title="Channel")

    channel: str = Field(pattern=_CHANNEL, description="Analog input, as NI names it: ai0, ai1, ...")
    mode: str = Field(
        default="",
        description="Voltage range to set, as the module names it. Empty leaves the range as it is.",
    )
    label: str = Field(
        default="",
        max_length=32,
        description="What is wired to it. Names the reading everywhere, including its metric.",
    )

    @field_validator("mode")
    @classmethod
    def _one_word(cls, value: str) -> str:
        """The ranges are the module's, so only their shape is checked here.

        A module fitted to the chassis publishes the ranges it takes and
        refuses the rest in its own words, which is a better error than a list
        written here could give: an NI-9238 has one range and another module
        has eight, and neither is knowable from a profile.
        """
        settled = value.strip()
        if settled and len(settled.split()) > 1:
            raise ValueError("mode is one word, the range as the module names it")
        return settled

    @property
    def unit(self) -> str:
        """The unit this channel reads in. Volts, on every DAQmx analog input."""
        return "V"

    @property
    def key(self) -> str:
        """The metric name this channel's readings are recorded under."""
        return metric_key(self.label, self.channel)


class DaqmxCaptureProfile(BaseModel):
    """A capture session: how long, how often, and what each channel is."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises readings and contacts no instrument.",
    )
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to keep capturing. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(
        default=1.0,
        ge=MIN_SAMPLE_PERIOD_S,
        description=f"Seconds between acquisitions. The module cannot be read faster than {MIN_SAMPLE_PERIOD_S}s.",
    )
    channels: list[Channel] = Field(
        default_factory=lambda: [Channel(channel="ai0", label="AI 0")],
        min_length=1,
        description="The inputs to configure and record. Anything not listed is left alone.",
    )
    max_missed_samples: int = Field(
        default=0,
        ge=0,
        description="Readings the module may fail to return before the run fails.",
    )

    @model_validator(mode="after")
    def _channels_are_distinct(self) -> DaqmxCaptureProfile:
        """No channel twice, and no two channels under one metric name.

        Two rows for the same input would configure it twice and record it
        twice, and two labels folding to the same key would silently overwrite
        one series with the other.
        """
        named = [channel.channel for channel in self.channels]
        duplicate = next((name for name in named if named.count(name) > 1), None)
        if duplicate is not None:
            raise ValueError(f"channel {duplicate} is listed more than once")
        keys = [channel.key for channel in self.channels]
        clash = next((key for key in keys if keys.count(key) > 1), None)
        if clash is not None:
            raise ValueError(f"two channels would record under the same metric name {clash!r}")
        return self
