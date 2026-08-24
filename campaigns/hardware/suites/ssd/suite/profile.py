"""Profile model for the SSD endurance suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Device(BaseModel):
    """One SSD under test.

    ``device`` is the smartctl target and ``test_path`` is where the probe
    writes. Both must resolve to the same underlying storage.
    """

    model_config = ConfigDict(extra="forbid", title="Device")

    name: str = Field(description="Label used in metric keys and anomaly details.")
    device: str = Field(description="Block device, e.g. /dev/nvme0n1.")
    test_path: str = Field(description="Absolute path the probe writes to.")


def _default_devices() -> list[Device]:
    return [Device(name="nvme0", device="/dev/nvme0n1", test_path="/var/tmp/gauntlet_ssd_io.bin")]


class Unit(BaseModel):
    """One unit under test."""

    model_config = ConfigDict(extra="forbid", title="Unit")

    name: str = Field(description="Label keying this unit's metrics, verdict rows and anomalies.")
    host: str = Field(description="Address to connect to.")


class ProbeBlock(BaseModel):
    """What the per-tick probe does."""

    model_config = ConfigDict(extra="forbid", title="Probe")

    devices: list[Device] = Field(default_factory=_default_devices)
    test_size_mb: int = Field(default=64, ge=1, description="MiB written and read each tick.")
    verify_size_kb: int = Field(default=64, ge=1, description="KiB written and re-read for the SHA-256 compare.")
    read_floor_mbps: float | None = Field(default=None, description="Flag reads slower than this.")
    write_floor_mbps: float | None = Field(default=None, description="Flag writes slower than this.")
    ssh_timeout_s: float = Field(default=30.0, gt=0, description="Per-probe SSH timeout.")


class ProvisionBlock(BaseModel):
    """Prepare a unit whose disk is not already mounted.

    Disabled by default. When enabled the suite formats, mounts, and installs
    the probe before the first tick.
    """

    model_config = ConfigDict(extra="forbid", title="Provision")

    enabled: bool = False
    device: str = Field(default="/dev/nvme0n1", description="Disk to prepare. Formatting destroys its contents.")
    mount_point: str = "/mnt/ssd-test"
    filesystem: str = "ext4"
    format_device: bool = Field(default=True, description="Format before mounting. Destructive.")
    install_dir: str = Field(default="/tmp/gauntlet-ssd", description="Where the probe script is installed.")
    format_timeout_s: float = Field(default=180.0, gt=0)


class PassCriteria(BaseModel):
    """Session budgets."""

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_anomalies: int = Field(default=100, ge=0, description="Anomalies tolerated per unit.")
    max_verify_failures: int = Field(default=0, ge=0, description="Data miscompares tolerated per unit.")
    require_alive_at_end: bool = True


class MonitorBlock(BaseModel):
    """Background sampling of the unit while the session runs."""

    model_config = ConfigDict(extra="forbid", title="Monitor")

    enabled: bool = True
    sample_period_s: float = Field(default=10.0, gt=0)


class SsdProfile(BaseModel):
    """An SSD endurance session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises plausible numbers with no unit attached.",
    )
    duration_s: float = Field(default=3600.0, gt=0, description="How long to keep probing.")
    sample_period_s: float = Field(default=5.0, gt=0, description="Seconds between probe ticks.")
    units: list[Unit] = Field(
        default_factory=list,
        description="Units probed concurrently each tick. Empty means the single run target.",
    )
    probe: ProbeBlock = Field(default_factory=ProbeBlock)
    provision: ProvisionBlock = Field(default_factory=ProvisionBlock)
    monitor: MonitorBlock = Field(default_factory=MonitorBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
