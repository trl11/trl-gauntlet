"""Profile model for the camera snapshot suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The shortest period worth asking for. A shorter one would not be refused by
# the sample loop, which clamps its wait to zero and runs the next iteration
# late, so asking for 2Hz and silently getting 1 is worse than being told the
# rate is not available.
#
# Measured on the bench, converting one 3840x2160 YUYV frame and encoding the
# PNG: 0.12s at 480px wide, 0.46s at 960, 1.9s at 1920, 7.7s at full width.
# The cost is the pure-Python YUYV to RGB conversion rather than the deflate,
# so it tracks the output width and barely moves with the content. This floor
# clears the 960px default with room for the grab and the round trip; a wider
# snapshot needs a longer period chosen to match.
MIN_SAMPLE_PERIOD_S = 1.0


class CameraSnapshotProfile(BaseModel):
    """A snapshot session: how often, how large, and what counts as a good frame."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises frames and contacts no instrument.",
    )
    duration_s: float = Field(
        default=30.0, ge=0, description="How long to keep taking snapshots. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(
        default=2.0,
        gt=0,
        description=(
            f"Seconds between snapshots. A real camera cannot be read faster than "
            f"{MIN_SAMPLE_PERIOD_S}s; a mock run synthesises a small frame and may go quicker."
        ),
    )
    max_width: int = Field(
        default=960,
        ge=16,
        le=3840,
        description=(
            "Width to scale each snapshot to. The height follows the aspect ratio. "
            "Conversion cost rises with it: about 0.5s at 960, 1.9s at 1920 and "
            "7.7s at 3840, so a wide snapshot needs a sample period to match."
        ),
    )
    max_identical_frames: int = Field(
        default=3,
        ge=0,
        description=(
            "Consecutive byte-identical snapshots tolerated. A live camera varies by at least "
            "sensor noise, so a longer repeat is a frozen pipeline rather than a still scene."
        ),
    )
    max_missed_snapshots: int = Field(
        default=0,
        ge=0,
        description="Snapshots the camera may fail to return before the run fails.",
    )
    max_unlocked_reads: int = Field(
        default=0,
        ge=0,
        description=(
            "Staggered link-register reads that may report the chip unlocked before the run fails. "
            "One chip is read per sample, in turn, rather than every chip on every sample."
        ),
    )

    @model_validator(mode="after")
    def _sample_period_is_attainable(self) -> CameraSnapshotProfile:
        """Only the real driver pays the conversion cost the floor is drawn from."""
        if self.driver == "real" and self.sample_period_s < MIN_SAMPLE_PERIOD_S:
            raise ValueError(
                f"sample_period_s ({self.sample_period_s}) is below {MIN_SAMPLE_PERIOD_S}, "
                f"which is the shortest a real camera can be read at"
            )
        return self
