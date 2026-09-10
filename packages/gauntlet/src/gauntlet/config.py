"""Settings, persisted to ``config.yaml`` in the data directory.

By default suites are read from ``./suites`` and artifacts written under
``./output``.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# What a role may be called. It ends up in an instance key, a URL path and an
# environment variable name, where the dot becomes a doubled underscore, so a
# role holding one of its own could not be read back out.
ROLE = re.compile(r"^[a-z0-9]+$")

# The setting naming where to find each capability's instrument. Each may
# instead name a role per instrument, where a bench holds more than one.
INSTRUMENT_SETTINGS = {
    "camera": "camera_device",
    "daq": "daq_serial",
    "i2c": "i2c_serial",
    "logic": "logic_serial",
    "psu": "psu_port",
}


def instrument_roles(setting: str | dict[str, str]) -> dict[str, str]:
    """Where to look for each instance of one instrument, keyed by role.

    A plain string is one instrument and no role, which is the empty key: a
    bench with one bridge registers it as ``i2c`` and its suites never learn
    that a role could have been given.
    """
    if isinstance(setting, dict):
        return dict(setting)
    return {"": setting}


def default_data_dir() -> Path:
    """Where config, databases, and logs live."""
    override = os.environ.get("GAUNTLET_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "output"


def default_suite_roots() -> list[Path]:
    """Directories searched for ``suite.yaml`` files.

    ``GAUNTLET_SUITE_PATH`` takes a colon-separated list.
    """
    env = os.environ.get("GAUNTLET_SUITE_PATH")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return [Path.cwd() / "suites"]


def default_campaign_roots() -> list[Path]:
    """Directories searched for ``campaign.yaml`` files.

    ``GAUNTLET_CAMPAIGN_PATH`` takes a colon-separated list.
    """
    env = os.environ.get("GAUNTLET_CAMPAIGN_PATH")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return [Path.cwd() / "campaigns"]


@dataclass
class Settings:
    """Runtime configuration."""

    # Every interface, so the app is reachable from another machine and from
    # outside a container. Suites always reach the API over loopback, so this
    # does not affect them.
    host: str = "0.0.0.0"
    port: int = 7100
    suite_roots: list[Path] = field(default_factory=default_suite_roots)
    # A campaign contributes its own suite directory to discovery, so pointing
    # one of these at a directory is all it takes to pick up the suites inside
    # it. Nothing is rebuilt; a rescan is enough.
    campaign_roots: list[Path] = field(default_factory=default_campaign_roots)
    data_dir: Path = field(default_factory=default_data_dir)
    runs_dir_override: Path | None = None
    profiles_dir_override: Path | None = None
    default_target: str = ""
    open_browser: bool = False
    log_level: str = "info"
    # Where to look for each instrument. "auto" probes, "" does not look at
    # all, and anything else is the serial port or USB serial number to use.
    # An instrument is registered only once its hardware answers, so nothing
    # simulated reaches the operator unless it is named below.
    #
    # A bench holding two of one instrument writes a mapping instead, naming
    # each device and what it is wired to:
    #
    #     i2c_serial:
    #       dut: "00ED940A"
    #       ref: "00EDF8B8"
    #
    # which registers them as `i2c.dut` and `i2c.ref`. Every device in a
    # mapping has to be named outright, because "auto" would hand both roles
    # the same one.
    psu_port: str | dict[str, str] = "auto"
    # Two drivers answer the acquisition capability, so this setting also says
    # which. A "daqmx:" prefix names something NI-DAQmx knows: a device by its
    # DAQmx name ("daqmx:cDAQ1Mod1"), or a gRPC device server and optionally a
    # device on it ("daqmx://host:31763/cDAQ1Mod1"), which is how Gauntlet in a
    # container reaches a driver installed on its host. Anything else is a
    # DI-2008 USB serial number. "auto" probes the DI-2008 and falls through to
    # the local NI-DAQmx, never to a server.
    daq_serial: str | dict[str, str] = "auto"
    # The bridge is a CP2112, told apart from another by its USB serial number
    # rather than a port: the kernel adapts it to an i2c-dev node itself, so
    # "auto" takes the first one the kernel has adapted.
    i2c_serial: str | dict[str, str] = "auto"
    # The logic analyzer is told apart from another by its USB serial number,
    # as the bridge is. Most of these boards carry none, so "auto" takes the
    # first one on the bus.
    logic_serial: str | dict[str, str] = "auto"
    # Where the fx2lafw firmware the analyzer runs on is. It is sigrok's and is
    # not shipped here, so "auto" searches the directories the
    # sigrok-firmware-fx2lafw package installs into; name a file or a
    # directory to load it from somewhere else.
    logic_firmware: str = "auto"
    # The camera is a /dev/video* node rather than a serial port, so "auto"
    # tries each capture node in turn and takes the first that streams a
    # format the encoder can write.
    camera_device: str | dict[str, str] = "auto"
    # What the camera's frames really carry. A GMSL adapter reports YUYV over
    # UVC while sending raw sensor data, and the UVC format code cannot tell
    # the two apart, so "auto" settles it by looking at a frame. Name a format
    # here when a camera should never be guessed at.
    camera_format: str = "auto"
    # Instruments to simulate instead of probing for, by name. Empty, so the
    # UI shows only hardware that is really attached; naming one here is for
    # development and tests.
    simulated_instruments: list[str] = field(default_factory=list)
    # Which instrument a suite means when it asks for a bare capability name on
    # a bench holding two of one, as `{"i2c": "dut"}`. This is the bench's
    # answer to a question a suite cannot settle: a suite needing any one bus
    # says `i2c` and has no way of knowing which is wired to what, and nothing
    # on the bus distinguishes two identical parts. Without an entry here a
    # bare name is refused rather than guessed at.
    default_instruments: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.suite_roots = [Path(p).expanduser() for p in self.suite_roots]
        self.campaign_roots = [Path(p).expanduser() for p in self.campaign_roots]
        self.data_dir = Path(self.data_dir).expanduser()
        for setting in INSTRUMENT_SETTINGS.values():
            _check_roles(setting, getattr(self, setting))
        _check_defaults(self.default_instruments, self)

    # Run artifacts and operator-authored profiles default to locations under
    # the data dir and are independently overridable.
    @property
    def runs_dir(self) -> Path:
        """Where run artifact directories are written."""
        return Path(self.runs_dir_override).expanduser() if self.runs_dir_override else self.data_dir / "runs"

    @property
    def profiles_dir(self) -> Path:
        """Where operator-authored profiles are saved."""
        return (
            Path(self.profiles_dir_override).expanduser() if self.profiles_dir_override else self.data_dir / "profiles"
        )

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.yaml"

    @property
    def runs_index_path(self) -> Path:
        return self.data_dir / "runs.sqlite"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "gauntlet.log"

    @property
    def api_base(self) -> str:
        """Loopback API base handed to suite subprocesses."""
        return f"http://127.0.0.1:{self.port}/api"

    def ensure_dirs(self) -> None:
        """Create every directory the app writes to."""
        for path in (self.data_dir, self.runs_dir, self.profiles_dir, self.log_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["suite_roots"] = [str(p) for p in self.suite_roots]
        payload["campaign_roots"] = [str(p) for p in self.campaign_roots]
        for key, value in payload.items():
            if isinstance(value, Path):
                payload[key] = str(value)
        # Resolved locations, rather than the null that means "derived".
        payload["runs_dir"] = str(self.runs_dir)
        payload["profiles_dir"] = str(self.profiles_dir)
        payload["runs_index_path"] = str(self.runs_index_path)
        return payload


def _check_roles(setting: str, value: str | dict[str, str]) -> None:
    """Reject a role mapping that could not name two instruments apart."""
    if not isinstance(value, dict):
        return
    for role, where in value.items():
        if not ROLE.match(role):
            raise ValueError(f"{setting}: role {role!r} may hold only lowercase letters and digits")
        if where in ("", "auto"):
            raise ValueError(f"{setting}: role {role!r} must name a device, because 'auto' would find the same one")
    named = list(value.values())
    if len(set(named)) != len(named):
        raise ValueError(f"{setting}: one device is given to more than one role")


def _check_defaults(defaults: dict[str, str], settings: Settings) -> None:
    """Reject a default naming a capability, or a role, that is not there."""
    for capability, role in defaults.items():
        setting = INSTRUMENT_SETTINGS.get(capability)
        if setting is None:
            known = ", ".join(sorted(INSTRUMENT_SETTINGS))
            raise ValueError(f"default_instruments: {capability!r} is not an instrument setting ({known})")
        roles = instrument_roles(getattr(settings, setting))
        if role not in roles:
            named = ", ".join(sorted(name for name in roles if name)) or "no roles"
            raise ValueError(f"default_instruments: {setting} has {named}, not {role!r}")


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    """Build settings from defaults, then ``config.yaml``, then overrides."""
    raw: dict[str, Any] = {}
    probe = Settings()
    if probe.config_path.is_file():
        try:
            loaded = yaml.safe_load(probe.config_path.read_text())
        except (OSError, yaml.YAMLError):
            loaded = None
        if isinstance(loaded, dict):
            raw = loaded
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})

    fields = {f for f in Settings.__dataclass_fields__}
    return Settings(**{k: v for k, v in raw.items() if k in fields})
