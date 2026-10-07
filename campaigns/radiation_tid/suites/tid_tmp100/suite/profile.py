"""Profile model for the TMP100 total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TidTmp100Profile(BaseModel):
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
    address: int = Field(
        default=0x48,
        ge=0x48,
        le=0x4F,
        description="The part's 7-bit I2C address, selected by the board's ADD0 and ADD1 straps.",
    )
    min_c: float = Field(default=-55.0, description="Lowest temperature a sample may read before it fails, in °C.")
    max_c: float = Field(default=125.0, description="Highest temperature a sample may read before it fails, in °C.")
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to run. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")

    @model_validator(mode="after")
    def _bounds_in_order(self) -> TidTmp100Profile:
        if self.min_c >= self.max_c:
            raise ValueError("min_c must be below max_c")
        return self
