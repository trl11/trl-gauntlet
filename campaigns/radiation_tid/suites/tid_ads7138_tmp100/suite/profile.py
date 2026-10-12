"""Profile model for the ADS7138 and TMP100 total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from suite.sensor import PARTS


class TidAds7138Tmp100Profile(BaseModel):
    """What an operator can configure.

    Every field becomes a form control in the UI; ``description`` is its label.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` drives a register model of each part and contacts no instrument.",
    )
    ads7138_address: int = Field(
        default=0x10,
        ge=0x08,
        le=0x77,
        description="The ADS7138's 7-bit I2C address, selected by the board's ADDR strap.",
    )
    tmp100_address: int = Field(
        default=0x48,
        description="The TMP100's 7-bit I2C address, selected by its ADD0 and ADD1 pins.",
    )
    avdd_v: float = Field(default=3.3, gt=0, description="AVDD, which is also the ADC's reference.")
    ain0_min_v: float = Field(default=1.75, description="Lowest in-spec reading of AIN0, pin 15.")
    ain0_max_v: float = Field(default=1.95, description="Highest in-spec reading of AIN0, pin 15.")
    ain1_min_v: float = Field(default=1.75, description="Lowest in-spec reading of AIN1, pin 16.")
    ain1_max_v: float = Field(default=1.95, description="Highest in-spec reading of AIN1, pin 16.")
    toggle_probe: int = Field(default=2, description="The analyzer probe on GPIO3, pin 2, toggled every sample.")
    low_probe: int = Field(default=3, description="The analyzer probe on GPIO4, pin 3, held at 0.")
    high_probe: int = Field(default=5, description="The analyzer probe on GPIO6, pin 5, held at 1.")
    pulse_probe: int = Field(default=6, description="The analyzer probe on GPIO7, pin 6, the slow pulse.")
    min_c: float = Field(
        default=-55.0, description="Lowest temperature the TMP100 may read before a sample fails, in °C."
    )
    max_c: float = Field(
        default=125.0, description="Highest temperature the TMP100 may read before a sample fails, in °C."
    )
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to run. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(
        default=1.0,
        gt=0,
        description="Seconds between samples. Pins 2 and 6 change level once a sample, so their period is twice this.",
    )

    @model_validator(mode="after")
    def _consistent(self) -> TidAds7138Tmp100Profile:
        probes = [self.toggle_probe, self.low_probe, self.high_probe, self.pulse_probe]
        for probe in probes:
            if not 1 <= probe <= 8:
                raise ValueError(f"probe {probe} is not one of the analyzer's eight")
        if len(set(probes)) != len(probes):
            raise ValueError("two outputs name the same probe")
        if self.ain0_min_v >= self.ain0_max_v or self.ain1_min_v >= self.ain1_max_v:
            raise ValueError("an analog window's minimum must be below its maximum")
        if self.min_c >= self.max_c:
            raise ValueError("min_c must be below max_c")
        if self.tmp100_address not in PARTS["tmp100"].addresses:
            raise ValueError(f"the TMP100 cannot be strapped to 0x{self.tmp100_address:02x}")
        if self.tmp100_address == self.ads7138_address:
            raise ValueError("the two parts share one bus, so they cannot share an address")
        return self
