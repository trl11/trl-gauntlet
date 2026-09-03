"""Profile model for the SSD check."""

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
    return [Device(name="nvme0", device="/dev/nvme0n1", test_path="/mnt/ssd-test/gauntlet_ssd_io.bin")]


class ProbeBlock(BaseModel):
    """What the per-tick probe does."""

    model_config = ConfigDict(extra="forbid", title="Probe")

    devices: list[Device] = Field(default_factory=_default_devices)
    test_size_mb: int = Field(default=64, ge=1, description="MiB written and read each tick.")
    verify_size_kb: int = Field(default=64, ge=1, description="KiB written and re-read for the SHA-256 compare.")
    read_floor_mbps: float | None = Field(default=None, description="Flag reads slower than this.")
    write_floor_mbps: float | None = Field(default=None, description="Flag writes slower than this.")
    ssh_timeout_s: float = Field(default=30.0, gt=0, description="Per-probe SSH timeout.")


class PassCriteria(BaseModel):
    """What a working disk has to manage.

    The budget is zero: this run is short enough that anything anomalous in it
    is a reason to look at the disk rather than a rate to stay under.
    """

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_anomalies: int = Field(default=0, ge=0, description="Anomalies tolerated across the run.")


class SsdProfile(BaseModel):
    """An SSD check."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises plausible numbers with no unit attached.",
    )
    duration_s: float = Field(default=30.0, ge=0, description="How long to keep probing.")
    sample_period_s: float = Field(default=10.0, gt=0, description="Seconds between probe ticks.")
    # Named here rather than left to `GAUNTLET_SSH_USER` and `GAUNTLET_SSH_KEY`:
    # a run started from the app inherits whatever shell launched the server, so
    # a login that lives only in an exported variable is one nobody remembers to
    # set, and the run fails against `root`.
    ssh_user: str = Field(default="trl", description="Login on the unit.")
    ssh_key_path: str = Field(
        default="",
        description="Private key to authenticate with. Empty uses the bundled engineering key.",
    )
    probe: ProbeBlock = Field(default_factory=ProbeBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
