"""Reads the environment half of the Gauntlet contract.

A suite launched by Gauntlet is told where to write and what it is testing
entirely through environment variables. A suite launched by hand from a
terminal gets none of them, so every accessor has a sensible standalone
fallback and :func:`run_environment` reports which mode it is in.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR_VAR = "GAUNTLET_RUN_DIR"
RUN_ID_VAR = "GAUNTLET_RUN_ID"
SUITE_VAR = "GAUNTLET_SUITE"
SUITE_DIR_VAR = "GAUNTLET_SUITE_DIR"
PROFILE_VAR = "GAUNTLET_PROFILE"
TARGET_VAR = "GAUNTLET_TARGET"
UNIT_SERIAL_VAR = "GAUNTLET_UNIT_SERIAL"
API_VAR = "GAUNTLET_API"

_CAPABILITY_PREFIX = "GAUNTLET_CAP_"


@dataclass(frozen=True)
class Capability:
    """An instrument Gauntlet is lending to this run."""

    name: str
    url: str
    instance_id: str = ""


@dataclass(frozen=True)
class RunEnvironment:
    """Everything Gauntlet told this process about the run."""

    run_dir: Path
    run_id: str
    suite: str = ""
    suite_dir: Path | None = None
    profile_path: Path | None = None
    target: str | None = None
    unit_serial: str | None = None
    api_base: str | None = None
    capabilities: dict[str, Capability] = field(default_factory=dict)
    supervised: bool = False

    def capability(self, name: str) -> Capability:
        """Return a granted capability, or raise if Gauntlet did not grant it."""
        try:
            return self.capabilities[name]
        except KeyError:
            granted = ", ".join(sorted(self.capabilities)) or "none"
            raise LookupError(
                f"capability {name!r} was not granted (granted: {granted}). "
                f"Add it to the `requires:` list in suite.yaml, or run under Gauntlet."
            ) from None


def _read_capabilities(env: dict[str, str]) -> dict[str, Capability]:
    """Collect ``GAUNTLET_CAP_<NAME>_URL`` / ``_ID`` pairs."""
    found: dict[str, Capability] = {}
    for key, value in env.items():
        if not key.startswith(_CAPABILITY_PREFIX) or not key.endswith("_URL") or not value:
            continue
        name = key[len(_CAPABILITY_PREFIX) : -len("_URL")].lower()
        if not name:
            continue
        found[name] = Capability(
            name=name,
            url=value,
            instance_id=env.get(f"{_CAPABILITY_PREFIX}{name.upper()}_ID", ""),
        )
    return found


def run_environment(
    *,
    run_dir: Path | None = None,
    profile_path: Path | None = None,
    target: str | None = None,
    unit_serial: str | None = None,
    suite: str = "",
) -> RunEnvironment:
    """Resolve the run environment, letting explicit arguments win over the env.

    Command-line flags take precedence so a suite stays runnable by hand.
    When ``GAUNTLET_RUN_DIR`` is absent and no directory is given, output
    lands under ``./gauntlet-runs/<suite>/<run-id>/``.
    """
    env = dict(os.environ)
    supervised = bool(env.get(RUN_DIR_VAR))
    suite_name = suite or env.get(SUITE_VAR, "") or "suite"

    resolved_id = env.get(RUN_ID_VAR) or new_run_id()
    if run_dir is not None:
        resolved_dir = run_dir
    elif supervised:
        resolved_dir = Path(env[RUN_DIR_VAR])
    else:
        resolved_dir = Path.cwd() / "gauntlet-runs" / suite_name / resolved_id
    resolved_dir.mkdir(parents=True, exist_ok=True)

    env_profile = env.get(PROFILE_VAR)
    suite_dir = env.get(SUITE_DIR_VAR)

    return RunEnvironment(
        run_dir=resolved_dir,
        run_id=resolved_id,
        suite=suite_name,
        suite_dir=Path(suite_dir) if suite_dir else None,
        profile_path=profile_path or (Path(env_profile) if env_profile else None),
        target=target or env.get(TARGET_VAR) or None,
        unit_serial=unit_serial or env.get(UNIT_SERIAL_VAR) or None,
        api_base=env.get(API_VAR) or None,
        capabilities=_read_capabilities(env),
        supervised=supervised,
    )


def new_run_id() -> str:
    """UTC timestamp plus four random characters, e.g. ``20260802T151304Z-4a2f``.

    The random part keeps two runs started in the same second apart, which is
    ordinary for a suite that finishes quickly.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"
