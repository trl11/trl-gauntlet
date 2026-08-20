"""Profile model for the MAX96793 GMSL3 Serializer TID suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Reading every chip is roughly two dozen I2C transactions over the link, and a
# snapshot converts a 4K frame on top of that. Measured together on the bench
# they need about a second, so a shorter period would be clamped to zero wait
# and run late rather than refused.
MIN_SAMPLE_PERIOD_S = 2.0


class TidMax96793Profile(BaseModel):
    """One irradiation of the MAX96793GTJ/VY+, and what counts as a healthy link."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a healthy link and contacts no instrument.",
    )
    duration_s: float = Field(default=300.0, gt=0, description="How long to watch the link.")
    sample_period_s: float = Field(
        default=2.0,
        gt=0,
        description=(
            f"Seconds between samples. A real link cannot be read faster than "
            f"{MIN_SAMPLE_PERIOD_S}s; a mock run may go quicker."
        ),
    )
    part_address: str = Field(
        default="0x84",
        pattern="^0x[0-9a-fA-F]{2}$",
        description=(
            "I2C address of the MAX96793GTJ/VY+, the part under the beam. Both ends are "
            "recorded either way; this is the one the verdict is about."
        ),
    )
    max_width: int = Field(
        default=960,
        ge=16,
        le=3840,
        description="Width to scale each snapshot to. Conversion cost rises with it.",
    )
    max_errors_per_sample: int = Field(
        default=200,
        ge=0,
        description=(
            "Link errors in one sample before that sample fails. The counters "
            "clear when read, so this is a rate, not a total. Set it above the "
            "baseline the part shows before the beam is on."
        ),
    )
    max_total_errors: int = Field(
        default=100000,
        ge=0,
        description="Link errors across the whole run before the run fails.",
    )
    max_unlocks: int = Field(
        default=0,
        ge=0,
        description="Samples the part may report its link down before the run fails.",
    )
    burst_frames: int = Field(
        default=8,
        ge=2,
        le=120,
        description=(
            "Frames read back to back each sample to measure the rate. The "
            "link runs at about 19fps, so eight frames costs about 0.4s."
        ),
    )
    max_corrupt_frames: int = Field(
        default=0,
        ge=0,
        description="Corrupt frames in one burst before that sample fails.",
    )
    max_dropped_frames: int = Field(
        default=8,
        ge=0,
        description=(
            "Frames missing from one burst before that sample fails. A healthy "
            "link on this bench usually drops none and occasionally a few, "
            "which is host contention rather than the link: bursts measured "
            "back to back give 19.4fps and 2574Mbps, and the dips come with "
            "load. Narrow this to what the part shows with the beam off, "
            "because a threshold that cries wolf during an irradiation is "
            "worse than none."
        ),
    )
    max_missed_snapshots: int = Field(
        default=0,
        ge=0,
        description="Snapshots that may fail to arrive before the run fails.",
    )
    min_mean_luma: float = Field(default=1.0, ge=0, description="Below this a frame counts as dark.")
    snapshot_every: int = Field(
        default=1,
        ge=0,
        description=(
            "Keep one snapshot every N samples, or 0 to keep none. A long "
            "irradiation at 1Hz writes a lot of frames otherwise."
        ),
    )

    @model_validator(mode="after")
    def _check_sample_period(self) -> TidMax96793Profile:
        """A real link has a floor a mock does not."""
        if self.driver == "real" and self.sample_period_s < MIN_SAMPLE_PERIOD_S:
            raise ValueError(
                f"sample_period_s must be at least {MIN_SAMPLE_PERIOD_S}s for driver 'real', got {self.sample_period_s}"
            )
        return self
