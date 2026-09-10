"""Profile model for the paired ADS7138 total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The oversampling settings the sweep visits, as OSR_CFG values: one
# conversion, then eight, thirty-two and a hundred and twenty-eight averaged.
# Four points rather than all eight, because the sweep is the slowest check in
# an iteration and the shape is what is being watched, not the curve.
OVERSAMPLING = (0, 3, 5, 7)


class TidAds7138PairProfile(BaseModel):
    """What an operator can configure.

    Every field becomes a form control in the UI; ``description`` is its label.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` drives a register model of both parts and contacts no instrument.",
    )
    dut_address: int = Field(
        default=0x10,
        ge=0x08,
        le=0x77,
        description="The 7-bit address of the part in the beam, set by its board's ADDR strap.",
    )
    ref_address: int = Field(
        default=0x10,
        ge=0x08,
        le=0x77,
        description="The 7-bit address of the reference part. The two may share one, being on separate bridges.",
    )
    channel_map: list[int] = Field(
        default_factory=lambda: [0, 1, 3, 2, 4, 5, 6, 7],
        min_length=8,
        max_length=8,
        description="The reference channel each channel 0 to 7 of the part under test is wired to. Bench wiring.",
    )
    vref_v: float = Field(default=3.3, gt=0, description="The parts' reference voltage, for turning codes into volts.")
    vol_max_mv: float = Field(
        default=400.0,
        ge=0,
        description="Highest a driven-low output may sit before it counts as a fault.",
    )
    voh_min_mv: float = Field(
        default=2900.0,
        ge=0,
        description="Lowest a driven-high output may sit before it counts as a fault.",
    )
    noise_max_lsb: float = Field(
        default=4.0,
        gt=0,
        description="Widest the spread of repeated conversions of one static wire may be, in codes.",
    )
    noise_samples: int = Field(
        default=32, ge=4, le=256, description="Conversions taken of a static wire to measure that spread."
    )
    oversampling_samples: int = Field(
        default=24, ge=4, le=128, description="Conversions taken at each oversampling setting."
    )
    settle_s: float = Field(
        default=0.002,
        ge=0,
        le=1.0,
        description="Wait between putting a level on a wire and measuring it.",
    )
    duration_s: float = Field(
        default=600.0, ge=0, description="How long to run. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=5.0, gt=0, description="Seconds between samples.")

    @model_validator(mode="after")
    def _every_channel_once(self) -> TidAds7138PairProfile:
        """Eight distinct reference channels, one per channel under test.

        Two wires landing on one reference channel would compare one twice and
        never look at the other.
        """
        for channel in self.channel_map:
            if not 0 <= channel <= 7:
                raise ValueError(f"channel {channel} is not one of the part's eight")
        if len(set(self.channel_map)) != len(self.channel_map):
            raise ValueError("channel_map names a reference channel more than once")
        if self.voh_min_mv <= self.vol_max_mv:
            raise ValueError("voh_min_mv must be above vol_max_mv, or no level could pass both")
        return self
