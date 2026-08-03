"""Settings, persisted to ``config.yaml`` in the data directory.

By default suites are read from ``./suites`` and artifacts written under
``./output``.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


@dataclass
class Settings:
    """Runtime configuration."""

    # Every interface, so the app is reachable from another machine and from
    # outside a container. Suites always reach the API over loopback, so this
    # does not affect them.
    host: str = "0.0.0.0"
    port: int = 7100
    suite_roots: list[Path] = field(default_factory=default_suite_roots)
    data_dir: Path = field(default_factory=default_data_dir)
    runs_dir_override: Path | None = None
    profiles_dir_override: Path | None = None
    default_target: str = ""
    open_browser: bool = False
    log_level: str = "info"

    def __post_init__(self) -> None:
        self.suite_roots = [Path(p).expanduser() for p in self.suite_roots]
        self.data_dir = Path(self.data_dir).expanduser()

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
        for key, value in payload.items():
            if isinstance(value, Path):
                payload[key] = str(value)
        # Resolved locations, rather than the null that means "derived".
        payload["runs_dir"] = str(self.runs_dir)
        payload["profiles_dir"] = str(self.profiles_dir)
        payload["runs_index_path"] = str(self.runs_index_path)
        return payload


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
