"""Profile model for the LAN7430 total ionising dose suite."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InterfaceBlock(BaseModel):
    """The network interface the controller presents on the host.

    ``address`` is the ingress side of the measurement: the address on the
    unit that the lab connects to, which the iperf3 server binds to. The egress
    side — which of the lab host's own addresses the traffic leaves from — is
    ``iperf.lab_address``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="eth1", description="Interface the LAN7430 presents on the host.")
    address: str = Field(
        default="",
        description="Address on the unit the lab connects to. Empty reads it from the unit's interface.",
    )
    expected_speed_mbps: int = Field(default=1000, gt=0, description="Link speed a healthy part negotiates.")
    pci_slot: str = Field(
        default="",
        description="PCI slot of the controller, e.g. 0000:01:00.0. Empty derives it from the interface.",
    )


class IperfBlock(BaseModel):
    """The throughput measurement itself.

    The server runs on the unit bound to the controller's own address, so the
    traffic has to cross the part under test rather than the host's built-in
    interface.
    """

    model_config = ConfigDict(extra="forbid")

    lab_address: str = Field(
        default="",
        description="Lab-side address the traffic leaves from. Empty lets routing choose.",
    )
    port: int = Field(default=5201, ge=1, le=65535, description="Port the server listens on.")
    stream_s: float = Field(default=5.0, gt=0, description="Seconds of traffic per direction, per tick.")
    omit_s: float = Field(default=1.0, ge=0, description="Leading seconds discarded, to skip TCP slow start.")
    parallel: int = Field(default=1, ge=1, le=32, description="Parallel streams per direction.")
    udp_enabled: bool = Field(default=True, description="Also measure UDP loss and jitter each tick.")
    udp_bitrate: str = Field(default="900M", description="Offered UDP rate, in iperf3's notation.")
    udp_stream_s: float = Field(default=3.0, gt=0, description="Seconds of UDP traffic per tick.")
    client_timeout_s: float = Field(default=120.0, gt=0, description="Wall-clock limit on one iperf3 client run.")
    server_start_timeout_s: float = Field(default=20.0, gt=0, description="How long to wait for the server to listen.")
    # Per-tick floors, distinct from the session averages in `pass_criteria`.
    # A dose run wants the tick degradation started at, which an average over
    # the whole session cannot show.
    tx_floor_mbps: float | None = Field(default=None, description="Flag ticks transmitting slower than this.")
    rx_floor_mbps: float | None = Field(default=None, description="Flag ticks receiving slower than this.")
    udp_loss_ceiling_pct: float | None = Field(default=None, description="Flag ticks losing more UDP than this.")


class OtpBlock(BaseModel):
    """Cyclical integrity checks on the controller's OTP.

    Read-only. OTP is one-time-programmable, so nothing here ever writes to
    it; the check is a read compared against the image captured at setup.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Read and compare the OTP every tick.")
    length: int = Field(default=512, ge=1, le=65536, description="Bytes to read from the start of the OTP.")
    check_registers: bool = Field(default=True, description="Also hash the register dump and compare it.")


class DmesgBlock(BaseModel):
    """Kernel messages the controller and its PCIe link produce."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Collect new kernel messages each tick.")
    patterns: list[str] = Field(
        default=["lan743x", "aer", "pcieport", "Hardware Error", "link is not ready"],
        description="Case-insensitive substrings that make a kernel line interesting.",
    )
    max_lines: int = Field(default=40, ge=1, description="Most lines carried back per tick.")


class PsuBlock(BaseModel):
    """Bench supply readings, recorded beside the throughput.

    Supply current climbing with accumulated dose is a standard TID signature,
    so it is worth logging whenever the bench has a supply. The suite only ever
    reads: it never commands the supply, and it does not reserve it, so the
    operator keeps control of the rail during a beam run.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Log supply readings when Gauntlet offers a `psu` capability.")
    capability: str = Field(default="psu", description="Capability name to read.")
    timeout_s: float = Field(default=5.0, gt=0, description="Per-read timeout.")


class PassCriteria(BaseModel):
    """Session gates.

    The defaults are deliberately permissive: the first beam runs are for
    characterisation, and a floor set before the part has been measured would
    only encode a guess. Tighten them once a baseline run exists.
    """

    model_config = ConfigDict(extra="forbid")

    min_avg_tx_mbps: float = Field(default=1.0, ge=0, description="Session-average unit-to-lab floor.")
    min_avg_rx_mbps: float = Field(default=1.0, ge=0, description="Session-average lab-to-unit floor.")
    max_udp_loss_pct: float = Field(default=100.0, ge=0, le=100, description="Session-average UDP loss ceiling.")
    max_anomalies: int = Field(default=1000000, ge=0, description="Total anomaly budget.")
    require_otp_stable: bool = Field(default=False, description="Fail if the OTP ever read back changed.")
    require_link_at_end: bool = Field(default=False, description="Fail if the link is down when the session ends.")
    require_measurement: bool = Field(default=True, description="Fail if no throughput sample was ever taken.")


class TidLan7430Profile(BaseModel):
    """A LAN7430 total ionising dose session."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    driver: str = Field(
        default="real",
        pattern="^(real|mock)$",
        description="`mock` synthesises a degrading part with no hardware attached.",
    )
    duration_s: float = Field(default=3600.0, gt=0, description="How long to keep testing.")
    sample_period_s: float = Field(default=30.0, gt=0, description="Seconds between measurement ticks.")
    ssh_timeout_s: float = Field(default=60.0, gt=0, description="Per-command SSH timeout.")
    # Named here rather than left to `GAUNTLET_SSH_USER` and `GAUNTLET_SSH_KEY`:
    # a run started from the app inherits whatever shell launched the server, so
    # a login that lives only in an exported variable is one nobody remembers to
    # set, and the run fails against `root`.
    ssh_user: str = Field(default="trl", description="Login on the unit.")
    ssh_key_path: str = Field(
        default="",
        description="Private key to authenticate with. Empty uses the bundled engineering key.",
    )
    install_dir: str = Field(default="/tmp/gauntlet-tid-lan7430", description="Where the collector is installed.")
    interface: InterfaceBlock = Field(default_factory=InterfaceBlock)
    iperf: IperfBlock = Field(default_factory=IperfBlock)
    otp: OtpBlock = Field(default_factory=OtpBlock)
    dmesg: DmesgBlock = Field(default_factory=DmesgBlock)
    psu: PsuBlock = Field(default_factory=PsuBlock)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)
