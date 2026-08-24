"""Profile model for the RS422 counter-link suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LinkBlock(BaseModel):
    """The lab end of the serial link.

    ``device`` set to ``auto`` resolves an FTDI USB-serial adapter by USB id,
    which avoids depending on ``/dev/ttyUSBn`` enumeration order.
    """

    model_config = ConfigDict(extra="forbid", title="Link")

    device: str = Field(default="auto", description="Serial device, or `auto` to resolve an FTDI adapter.")
    baud: int = Field(default=115200, gt=0, description="Must match the unit's console baud.")
    bytesize: int = Field(default=8, ge=5, le=8)
    parity: str = Field(default="N", pattern="^[NEOMS]$")
    stopbits: int = Field(default=1, ge=1, le=2)
    read_timeout_s: float = Field(default=0.5, gt=0)


class PassCriteria(BaseModel):
    """Session budgets."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_missing: int = Field(default=0, ge=0, description="Counter values allowed to go missing.")
    max_anomalies: int = Field(default=10, ge=0, description="Non-counter anomalies tolerated.")
    min_received: int = Field(
        default=1,
        ge=0,
        description="Floor on values received, so a silent link fails even with no measurable gaps.",
    )
    require_alive_at_end: bool = True


class Rs422Profile(BaseModel):
    """An RS422 counter-link session.

    The lab side sends ASCII ENQ (0x05) probes at ``rate_hz`` and reads the
    ``RADCOUNT <n>`` replies, recording gaps between expected and observed
    counter values.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a contiguous counter stream with no hardware.",
    )
    duration_s: float = Field(default=60.0, gt=0, description="How long to exercise the link.")
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between accounting ticks.")
    rate_hz: float = Field(default=100.0, gt=0, description="Probes sent per second.")
    link: LinkBlock = Field(default_factory=LinkBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
