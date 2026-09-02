"""Profile model for the ADS7138 total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The rates and windows the analyzer takes. The instrument is the final
# authority and refuses anything else; these are here for the error an
# operator sees when a profile names something it does not offer.
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
WINDOWS = ("1ms", "10ms", "100ms")


class TidAds7138Profile(BaseModel):
    """What an operator can configure.

    Every field becomes a form control in the UI; ``description`` is its label.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` drives a register model of the part and contacts no instrument.",
    )
    address: int = Field(
        default=0x10,
        ge=0x08,
        le=0x77,
        description="The part's 7-bit I2C address, selected by the board's ADDR strap.",
    )
    probe_map: list[int] = Field(
        default_factory=lambda: [5, 3, 1, 7, 8, 6, 4, 2],
        min_length=8,
        max_length=8,
        description="The analyzer probe each of GPO0 to GPO7 is clipped to. Bench wiring, not a device setting.",
    )
    rate: str = Field(default="1mhz", description=f"Sample rate. One of: {', '.join(RATES)}.")
    window: str = Field(
        default="1ms",
        description=f"How much signal one capture covers. One of: {', '.join(WINDOWS)}.",
    )
    duration_s: float = Field(default=60.0, gt=0, description="How long to run.")
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")
    save_traces: bool = Field(
        default=True,
        description=(
            "Keep the samples of every capture, to be looked through afterwards. "
            "One byte per sample, so rate times window. Off for a long run, "
            "which would keep thousands."
        ),
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
    def _every_probe_once(self) -> TidAds7138Profile:
        """Eight distinct probes, one per output.

        Two outputs sharing a probe would compare one line twice and never
        look at the other.
        """
        for probe in self.probe_map:
            if not 1 <= probe <= 8:
                raise ValueError(f"probe {probe} is not one of the analyzer's eight")
        if len(set(self.probe_map)) != len(self.probe_map):
            raise ValueError("probe_map names a probe more than once")
        return self
