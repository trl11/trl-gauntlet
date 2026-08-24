"""Profile model for the DAQ capture suite."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Every mode the acquisition unit takes: a voltage range, or a thermocouple
# type it converts on board. Stated here for the error an operator sees when a
# profile names something else; the instrument is the final authority and
# refuses the rest.
VOLTAGE_MODES = ("10v", "5v", "2.5v", "1v", "500mv", "250mv", "100mv", "50mv", "25mv")
THERMOCOUPLE_MODES = ("tc_b", "tc_e", "tc_j", "tc_k", "tc_n", "tc_r", "tc_s", "tc_t")
MODES = VOLTAGE_MODES + THERMOCOUPLE_MODES

_SLUG = re.compile(r"[^a-z0-9]+")

# The shortest sample period worth asking for. One scan of the unit costs about
# 0.18s — it halts the scan list, captures, and stops again — so a shorter
# period would not be refused by the sample loop, which simply runs the next
# iteration late. Asking for 10Hz and silently getting 5 is worse than being
# told the rate is not available, so it is refused here instead.
MIN_SAMPLE_PERIOD_S = 0.25


def metric_key(label: str, channel: str) -> str:
    """The name a channel's readings are recorded under.

    A metric name is an identifier in a chart legend and a CSV header, so the
    operator's label is folded to lower case with runs of anything else turned
    into single underscores. A label that leaves nothing behind — punctuation
    only, or empty — falls back to the channel number, because a series has to
    be named something and the number is what the reading is called anyway.
    """
    slug = _SLUG.sub("_", label.strip().lower()).strip("_")
    return slug or f"ch{channel}"


class Channel(BaseModel):
    """One analog input: what it is wired to, and how to read it."""

    model_config = ConfigDict(extra="forbid", title="Channel")

    channel: str = Field(pattern=r"^[1-8]$", description="Analog input number, 1 to 8.")
    mode: str = Field(default="10v", description=f"One of: {', '.join(MODES)}.")
    label: str = Field(
        default="",
        max_length=32,
        description="What is wired to it. Names the reading everywhere, including its metric.",
    )

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        return value

    @property
    def unit(self) -> str:
        """The unit this channel reads in, given its mode."""
        return "C" if self.mode in THERMOCOUPLE_MODES else "V"

    @property
    def key(self) -> str:
        """The metric name this channel's readings are recorded under."""
        return metric_key(self.label, self.channel)


class DaqCaptureProfile(BaseModel):
    """A capture session: how long, how often, and what each channel is."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises readings and contacts no instrument.",
    )
    duration_s: float = Field(default=60.0, gt=0, description="How long to keep capturing.")
    sample_period_s: float = Field(
        default=1.0,
        ge=MIN_SAMPLE_PERIOD_S,
        description=f"Seconds between scans. The unit cannot scan faster than {MIN_SAMPLE_PERIOD_S}s.",
    )
    channels: list[Channel] = Field(
        default_factory=lambda: [Channel(channel="1", label="CH 1")],
        min_length=1,
        description="The inputs to configure and record. Anything not listed is left alone.",
    )
    max_missed_samples: int = Field(
        default=0,
        ge=0,
        description="Readings the unit may fail to return before the run fails.",
    )

    @model_validator(mode="after")
    def _channels_are_distinct(self) -> DaqCaptureProfile:
        """No channel twice, and no two channels under one metric name.

        Two rows for the same input would configure it twice and record it
        twice, and two labels folding to the same key would silently overwrite
        one series with the other.
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
