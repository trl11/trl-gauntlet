"""Profile model for the SSD dose suite."""

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


class Unit(BaseModel):
    """One unit under test."""

    model_config = ConfigDict(extra="forbid", title="Unit")

    name: str = Field(description="Label keying this unit's metrics, verdict rows and anomalies.")
    host: str = Field(description="Address to connect to.")


class ProbeBlock(BaseModel):
    """What the per-tick probe does."""

    model_config = ConfigDict(extra="forbid", title="Probe")

    devices: list[Device] = Field(default_factory=_default_devices)
    test_size_mb: int = Field(default=256, ge=1, description="MiB written and read each tick.")
    verify_size_kb: int = Field(default=256, ge=1, description="KiB written and re-read for the SHA-256 compare.")
    read_floor_mbps: float | None = Field(default=None, description="Flag reads slower than this.")
    write_floor_mbps: float | None = Field(default=None, description="Flag writes slower than this.")
    ssh_timeout_s: float = Field(default=60.0, gt=0, description="Per-probe SSH timeout.")


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
    install_dir: str = Field(default="/tmp/gauntlet-tid-ssd", description="Where the probe script is installed.")
    format_timeout_s: float = Field(default=180.0, gt=0)


class DmesgBlock(BaseModel):
    """Kernel messages read from the unit each tick.

    A drive that starts throwing I/O errors or resetting its controller says so
    in the kernel log before the SMART counters catch up, so the log is worth a
    round-trip of its own.
    """

    model_config = ConfigDict(extra="forbid", title="Kernel log")

    enabled: bool = True
    patterns: list[str] = Field(
        default_factory=lambda: ["nvme", "I/O error", "blk_update_request", "EXT4-fs error"],
        description="Case-insensitive substrings that make a message worth recording.",
    )


class PsuBlock(BaseModel):
    """Bench supply readings, recorded beside the bandwidth.

    Supply current climbing with accumulated dose is a standard TID signature,
    so it is worth logging whenever the bench has a supply. The suite only ever
    reads: it never commands the supply, and it does not reserve it, so the
    operator keeps control of the rail during a beam run.
    """

    model_config = ConfigDict(extra="forbid", title="Bench supply")

    enabled: bool = Field(default=True, description="Log supply readings when Gauntlet offers a `psu` capability.")
    capability: str = Field(default="psu", description="Capability name to read.")
    timeout_s: float = Field(default=5.0, gt=0)


class MonitorBlock(BaseModel):
    """Background sampling of the unit while the session runs."""

    model_config = ConfigDict(extra="forbid", title="Monitor")

    enabled: bool = True
    sample_period_s: float = Field(default=30.0, gt=0)


class PassCriteria(BaseModel):
    """Session budgets.

    A dose run is characterisation, so these start loose. A part is expected to
    degrade and eventually fail; the verdict is about whether the measurement
    happened, not whether the part survived it. Tighten them once a baseline
    run exists.
    """

    model_config = ConfigDict(extra="forbid", title="Pass criteria")

    max_anomalies: int = Field(default=100000, ge=0, description="Anomalies tolerated per unit.")
    max_verify_failures: int = Field(default=100000, ge=0, description="Data miscompares tolerated per unit.")
    require_measurement: bool = Field(default=True, description="Fail when no tick ever returned a bandwidth figure.")
    require_device_at_end: bool = Field(
        default=False, description="Fail when the drive had left the bus. Off: a dead part is a result."
    )


class TidSsdProfile(BaseModel):
    """An SSD total ionising dose session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a degrading disk with no hardware attached.",
    )
    duration_s: float = Field(
        default=28800.0, ge=0, description="How long to keep probing. 0 runs until the operator stops the run."
    )
    sample_period_s: float = Field(default=30.0, gt=0, description="Seconds between probe ticks.")
    # Named here rather than left to `GAUNTLET_SSH_USER` and `GAUNTLET_SSH_KEY`:
    # a run started from the app inherits whatever shell launched the server, so
    # a login that lives only in an exported variable is one nobody remembers to
    # set, and the run fails against `root`.
    ssh_user: str = Field(default="trl", description="Login on the unit.")
    ssh_key_path: str = Field(
        default="",
        description="Private key to authenticate with. Empty uses the bundled engineering key.",
    )
    units: list[Unit] = Field(
        default_factory=list,
        description="Units probed concurrently each tick. Empty means the single run target.",
    )
    probe: ProbeBlock = Field(default_factory=ProbeBlock)
    provision: ProvisionBlock = Field(default_factory=ProvisionBlock)
    dmesg: DmesgBlock = Field(default_factory=DmesgBlock)
    psu: PsuBlock = Field(default_factory=PsuBlock)
    monitor: MonitorBlock = Field(default_factory=MonitorBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
