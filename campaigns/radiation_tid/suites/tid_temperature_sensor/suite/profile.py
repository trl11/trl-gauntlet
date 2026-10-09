"""Profile model for the temperature sensor total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from suite.sensor import PARTS


class TidTemperatureSensorProfile(BaseModel):
    """What an operator can configure.

    Every field becomes a form control in the UI; ``description`` is its label.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` reads a register model of the part and contacts no instrument.",
    )
    part: str = Field(default="tmp100", pattern="^(tmp100|tmp112)$", description="Which sensor is under test.")
    address: int = Field(
        default=0x48,
        description="The part's 7-bit I2C address, selected by its ADD pins.",
    )
    min_c: float = Field(default=-55.0, description="Lowest temperature a sample may read before it fails, in °C.")
    max_c: float = Field(default=125.0, description="Highest temperature a sample may read before it fails, in °C.")
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to run. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")

    @model_validator(mode="after")
    def _consistent(self) -> TidTemperatureSensorProfile:
        if self.min_c >= self.max_c:
            raise ValueError("min_c must be below max_c")
        if self.address not in PARTS[self.part].addresses:
            raise ValueError(f"the {self.part.upper()} cannot be strapped to 0x{self.address:02x}")
        return self
