"""Profile model for the piezo motion suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Axis(BaseModel):
    """One piezo axis under test.

    ``serial`` and ``axis`` identify the MQTT topic namespace
    ``piezo/{serial}/axis/{axis}/``, where the controller publishes telemetry
    and accepts commands on the ``update`` sibling.
    """

    model_config = ConfigDict(extra="forbid", title="Axis")

    name: str = Field(description="Label used in metric keys and anomaly details.")
    serial: str = Field(description="Controller serial in the MQTT topic.")
    axis: int = Field(default=0, ge=0, description="Axis index on the controller.")
    home_usteps: int = Field(default=0, description="Retracted reference position.")
    extended_usteps: int = Field(default=2_700_008, description="Far-end target each cycle drives to.")
    speed_hz: int = Field(default=200, gt=0, description="Move speed.")


def _default_axes() -> list[Axis]:
    return [Axis(name="axis0", serial="0", axis=0)]


class MotionBlock(BaseModel):
    """The move cycle.

    Every axis is homed at setup. Each cycle drives to ``extended_usteps``,
    dwells, then returns home.
    """

    model_config = ConfigDict(extra="forbid", title="Motion")

    axes: list[Axis] = Field(default_factory=_default_axes)
    extended_dwell_s: float = Field(default=1.0, ge=0, description="Seconds held at the extended position.")
    move_timeout_s: float = Field(default=30.0, gt=0, description="How long to wait for a move to report arrival.")
    sample_timeout_s: float = Field(default=5.0, gt=0, description="How long to wait for any telemetry sample.")
    max_temperature_c: float | None = Field(default=None, description="Flag controller temperature above this.")


class MqttBlock(BaseModel):
    """Connection to the broker carrying the controller's telemetry."""

    model_config = ConfigDict(extra="forbid", title="MQTT")

    port: int = Field(default=1883, ge=1, le=65535)
    keepalive_s: int = Field(default=30, gt=0)
    connect_timeout_s: float = Field(default=10.0, gt=0)


class PassCriteria(BaseModel):
    """Session budgets."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_anomalies: int = Field(default=100, ge=0)
    max_missed_targets: int = Field(default=0, ge=0, description="Moves allowed to not reach their target.")
    require_alive_at_end: bool = True


class PiezoProfile(BaseModel):
    """A piezo motion session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises motion telemetry with no controller.",
    )
    cycles: int = Field(default=100, ge=1, description="Extend-and-return cycles in this session.")
    cycle_delay_s: float = Field(default=0.0, ge=0, description="Pause between cycles.")
    mqtt: MqttBlock = Field(default_factory=MqttBlock)
    motion: MotionBlock = Field(default_factory=MotionBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
