"""Profile model for the camera dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The shortest period worth asking for. A shorter one would not be refused by
# the sample loop, which clamps its wait to zero and runs the next iteration
# late. Subsampling a 4512x4512 frame and deflating the PNG is pure Python and
# costs a large fraction of a second at the 960px default; a wider still needs
# a longer period chosen to match.
MIN_SAMPLE_PERIOD_S = 1.0


class Recovery(BaseModel):
    """What to do about a camera that has stopped handing over frames.

    The camera latches its thermal shutdown until it restarts, and a dose run
    that stopped there would lose the rest of the exposure. Rebooting is the
    only recovery that does not involve reaching into the chamber, so the
    suite does it itself and records that it did.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Reboot the camera when it stops answering.")
    after_failures: int = Field(
        default=3,
        ge=1,
        description="Consecutive failed stills before the camera is rebooted. A single miss is not worth a reboot.",
    )
    settle_s: float = Field(
        default=10.0,
        ge=0,
        description=(
            "Seconds to wait after a reboot before trying again. The camera leaves the bus for "
            "about a second and comes back on a new address, which the driver has to notice."
        ),
    )
    max_resets: int = Field(
        default=20,
        ge=0,
        description=(
            "Reboots allowed across the run. A camera needing more than this is not coming back, "
            "and the run is better spent recording that than power-cycling it for hours."
        ),
    )


class PassCriteria(BaseModel):
    """What makes the session a pass, beyond every still having arrived.

    A missed still already fails the session: the runner's verdict is that
    nothing failed, and a suite's criteria can only add to it. So the one
    thing left to state is that a session which took no stills at all is not
    a pass either, which the default rule would otherwise call one.
    """

    model_config = ConfigDict(extra="forbid")

    require_measurement: bool = Field(
        default=True,
        description="Fail a session that collected no usable still at all.",
    )


class CameraDoseProfile(BaseModel):
    """A dose session: how long, how often, and what counts as an anomaly."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a degrading camera and contacts no instrument.",
    )
    duration_s: float = Field(
        default=28800.0,
        ge=0,
        description="How long to keep sampling. 0 runs until the operator stops the run.",
    )
    sample_period_s: float = Field(
        default=30.0,
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
    baseline_frames: int = Field(
        default=5,
        ge=1,
        description=(
            "Stills averaged at the start of the run to fix the baseline that drift is measured "
            "against. Taken under the beam like every other, so the first few carry the least dose "
            "rather than none."
        ),
    )
    max_luma_drift: float = Field(
        default=40.0,
        gt=0,
        description="Brightness change from the baseline that is recorded as an anomaly.",
    )
    max_sharpness_drop: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description=(
            "Fraction of the baseline edge detail that may be lost before it is recorded as an "
            "anomaly. 0.5 is half the detail gone."
        ),
    )
    max_identical_frames: int = Field(
        default=3,
        ge=0,
        description=(
            "Consecutive byte-identical stills tolerated. A live sensor varies by at least its "
            "own noise, so a longer repeat is a frozen image path rather than a still scene."
        ),
    )
    max_sensor_c: float = Field(
        default=85.0,
        gt=0,
        description=(
            "Sensor temperature above which an anomaly is recorded. Not a failure: heat is a "
            "condition of the run, and a run that stopped for it would lose the beam slot. It is "
            "recorded so a fault can be told apart from dose when the run is read. An unmounted "
            "1800 U sits around 70C while working perfectly, so this is set above that."
        ),
    )
    recovery: Recovery = Field(default_factory=Recovery)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)

    @model_validator(mode="after")
    def _sample_period_is_attainable(self) -> CameraDoseProfile:
        """Only the real driver pays the scaling cost the floor is drawn from."""
        if self.driver == "real" and self.sample_period_s < MIN_SAMPLE_PERIOD_S:
            raise ValueError(
                f"sample_period_s ({self.sample_period_s}) is below {MIN_SAMPLE_PERIOD_S}, "
                f"which is the shortest a real camera can be read at"
            )
        return self
