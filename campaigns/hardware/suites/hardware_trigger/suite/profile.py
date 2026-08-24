"""Profile model for the hardware-trigger suite.

Gauntlet renders a form from the JSON Schema of this model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TriggerBlock(BaseModel):
    """The GPIO pulse to emit each iteration."""

    model_config = ConfigDict(extra="forbid", title="Trigger")

    chip: str = Field(default="gpiochip0", description="GPIO chip device name on the unit.")
    line: int = Field(default=43, ge=0, description="GPIO line number to drive.")
    high_seconds: float = Field(default=1.0, gt=0, description="Seconds to hold the line high.")
    low_seconds: float = Field(default=1.0, gt=0, description="Seconds to hold the line low.")
    command_timeout_s: float = Field(
        default=30.0,
        gt=0,
        description="Give up on the remote command after this long. Must exceed high+low.",
    )


class PassCriteria(BaseModel):
    """Session budgets."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_failures: int = Field(default=0, ge=0, description="Failed pulses tolerated.")
    max_anomalies: int = Field(default=10, ge=0, description="Anomalies tolerated.")
    require_alive_at_end: bool = Field(
        default=True,
        description="Fail if the unit is unreachable when the session ends.",
    )


class MonitorBlock(BaseModel):
    """Background sampling of the unit while the session runs."""

    model_config = ConfigDict(extra="forbid", title="Monitor")

    enabled: bool = Field(default=True, description="Sample load, memory and disk between pulses.")
    sample_period_s: float = Field(default=5.0, gt=0, description="Seconds between samples.")


class HardwareTriggerProfile(BaseModel):
    """A hardware-trigger session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` runs the whole flow with no unit attached.",
    )
    runs: int = Field(default=5, ge=1, description="Pulses to emit in this session.")
    cycle_delay_s: float = Field(default=0.0, ge=0, description="Pause between pulses.")
    trigger: TriggerBlock = Field(default_factory=TriggerBlock)
    monitor: MonitorBlock = Field(default_factory=MonitorBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
