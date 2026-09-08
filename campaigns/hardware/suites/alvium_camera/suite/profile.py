"""Profile model for the Alvium camera check."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The shortest period worth asking for. A shorter one would not be refused by
# the sample loop, which clamps its wait to zero and runs the next iteration
# late, so asking for 4Hz and silently getting 1 is worse than being told the
# rate is not available.
#
# The sensor debayers on its own board and sends RGB, so there is no colour
# conversion to pay for, but subsampling a 4512x4512 frame and deflating the
# PNG is still pure Python and still costs a large fraction of a second at the
# 960px default. A wider still needs a longer period chosen to match.
MIN_SAMPLE_PERIOD_S = 1.0


class CameraCheckProfile(BaseModel):
    """A short session: how many stills, how large, and what counts as a good one."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises frames and contacts no instrument.",
    )
    frames: int = Field(
        default=5,
        ge=1,
        le=100,
        description="How many stills to take before deciding. The run ends when it has them.",
    )
    sample_period_s: float = Field(
        default=1.5,
        gt=0,
        description=(
            f"Seconds between stills. A real camera cannot be read faster than "
            f"{MIN_SAMPLE_PERIOD_S}s; a mock run synthesises a small frame and may go quicker."
        ),
    )
    max_width: int = Field(
        default=960,
        ge=16,
        le=4512,
        description=(
            "Width to scale each still to. Anything at or above the camera's own width takes "
            "the whole frame. The height follows the aspect ratio."
        ),
    )
    exposure_us: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Exposure to pin the camera at, in microseconds. Zero leaves the camera metering "
            "for itself, which copes with whatever the scene is but compensates for a sensor "
            "that is dimming — so a run measuring that has to pin a value."
        ),
    )
    min_mean_luma: float = Field(
        default=4.0,
        ge=0,
        le=255,
        description="Below this the frame is dark enough to count as no picture.",
    )
    max_mean_luma: float = Field(
        default=250.0,
        ge=0,
        le=255,
        description="Above this the frame is saturated enough to count as no picture.",
    )
    min_sharpness: float = Field(
        default=0.5,
        ge=0,
        description="Mean difference between neighbouring pixels. Near zero is a blank or defocused frame.",
    )
    max_identical_frames: int = Field(
        default=1,
        ge=0,
        description=(
            "Consecutive byte-identical stills tolerated. A live sensor varies by at least its "
            "own noise, so a repeat is a frozen image path rather than a still scene."
        ),
    )
    max_sensor_c: float = Field(
        default=85.0,
        gt=0,
        description=(
            "Sensor temperature above which the check fails. A backstop rather than the main "
            "signal: the camera reports its own thermal status and that is what is judged "
            "first. An unmounted 1800 U sits around 70C while working perfectly, so this is "
            "set above that and below the shutdown."
        ),
    )

    @model_validator(mode="after")
    def _sample_period_is_attainable(self) -> CameraCheckProfile:
        """Only the real driver pays the scaling cost the floor is drawn from."""
        if self.driver == "real" and self.sample_period_s < MIN_SAMPLE_PERIOD_S:
            raise ValueError(
                f"sample_period_s ({self.sample_period_s}) is below {MIN_SAMPLE_PERIOD_S}, "
                f"which is the shortest a real camera can be read at"
            )
        return self

    @model_validator(mode="after")
    def _brightness_range_is_usable(self) -> CameraCheckProfile:
        """The brightness window has to leave something inside it."""
        if self.min_mean_luma >= self.max_mean_luma:
            raise ValueError(f"min_mean_luma ({self.min_mean_luma}) must be below max_mean_luma ({self.max_mean_luma})")
        return self
