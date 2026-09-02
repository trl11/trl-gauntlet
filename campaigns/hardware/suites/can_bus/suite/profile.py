"""Profile model for the CAN counter-link suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LinkBlock(BaseModel):
    """The CAN link under test.

    The unit sends counter frames on ``unit_iface``; the lab host receives them
    from the socketcan interface ``lab_iface``.
    """

    model_config = ConfigDict(extra="forbid", title="Link")

    unit_iface: str = Field(default="can0", description="CAN interface on the unit under test.")
    lab_iface: str = Field(default="can0", description="socketcan interface on the lab host.")
    bitrate: int = Field(default=500_000, gt=0, description="Bus bitrate, applied when bringing the link up.")
    arbitration_id: int = Field(default=0x100, ge=0, description="CAN id the counter frames use.")
    configure_unit: bool = Field(
        default=True,
        description="Bring the unit's interface down, set the bitrate, and bring it up at setup.",
    )
    install_dir: str = Field(default="/tmp/gauntlet-can", description="Where the sender is installed on the unit.")
    ssh_timeout_s: float = Field(default=30.0, gt=0)


class PassCriteria(BaseModel):
    """Session budgets."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_missing: int = Field(default=0, ge=0, description="Counter values allowed to go missing.")
    max_anomalies: int = Field(default=10, ge=0, description="Non-counter anomalies tolerated.")
    min_received: int = Field(default=1, ge=0, description="Floor on values received.")
    require_alive_at_end: bool = True


class CanProfile(BaseModel):
    """A CAN counter-link session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a contiguous counter stream with no bus.",
    )
    duration_s: float = Field(
        default=60.0, ge=0, description="How long to exercise the link. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between accounting ticks.")
    rate_hz: float = Field(default=100.0, gt=0, description="Frames the unit sends per second.")
    services_to_stop: list[str] = Field(
        default_factory=list,
        description="Systemd units stopped on the unit for the run, then restarted, so they do not share the bus.",
    )
    link: LinkBlock = Field(default_factory=LinkBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
