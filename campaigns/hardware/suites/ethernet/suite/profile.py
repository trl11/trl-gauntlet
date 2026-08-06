"""Profile model for the Ethernet throughput suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProbeBlock(BaseModel):
    """The per-tick speed test."""

    model_config = ConfigDict(extra="forbid")

    transfer_mb: int = Field(default=64, ge=1, description="MiB pushed in each direction per tick.")
    server_port: int = Field(default=5301, ge=1, le=65535, description="Port the lab-side server listens on.")
    lab_host: str = Field(
        default="",
        description="Address the unit connects back to. Empty uses the local end of the SSH socket.",
    )
    install_dir: str = Field(default="/tmp/gauntlet-ethernet", description="Where the probe is installed on the unit.")
    ssh_timeout_s: float = Field(default=60.0, gt=0, description="Per-tick SSH timeout.")
    socket_timeout_s: float = Field(default=30.0, gt=0, description="Socket timeout inside the probe.")
    tx_floor_mbps: float | None = Field(default=None, description="Flag ticks transmitting slower than this.")
    rx_floor_mbps: float | None = Field(default=None, description="Flag ticks receiving slower than this.")


class PassCriteria(BaseModel):
    """Session budgets. The headline gate is average throughput."""

    model_config = ConfigDict(extra="forbid")

    min_avg_tx_mbps: float = Field(default=100.0, ge=0, description="Session-average unit-to-lab floor.")
    min_avg_rx_mbps: float = Field(default=100.0, ge=0, description="Session-average lab-to-unit floor.")
    max_anomalies: int = Field(default=100, ge=0)
    require_alive_at_end: bool = True


class MonitorBlock(BaseModel):
    """Background sampling of the unit while the session runs."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    sample_period_s: float = Field(default=10.0, gt=0)


class EthernetProfile(BaseModel):
    """An Ethernet throughput session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises throughput with no unit attached.",
    )
    duration_s: float = Field(default=3600.0, gt=0, description="How long to keep testing.")
    sample_period_s: float = Field(default=10.0, gt=0, description="Seconds between speed tests.")
    probe: ProbeBlock = Field(default_factory=ProbeBlock)
    monitor: MonitorBlock = Field(default_factory=MonitorBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
