"""Turns a run request plus a suite manifest into argv and an environment.

Overrides not declared in the manifest are rejected.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_suite.contract import OverrideSpec

from gauntlet.suites.manifest import LoadedSuite


class LaunchError(ValueError):
    """A run request cannot be turned into a valid command line."""


@dataclass
class RunRequest:
    """What an operator asked for."""

    suite: str
    profile: str | None = None
    target: str | None = None
    unit_serial: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    profile_body: str | None = None


@dataclass
class Launch:
    """A fully resolved command line, ready to spawn."""

    argv: list[str]
    cwd: Path
    env: dict[str, str]
    graceful_stop_signal: str


def suite_environment(suite: LoadedSuite) -> dict[str, str]:
    """Base environment for any process belonging to a suite.

    Used for the run and for auxiliary commands such as the profile-schema
    probe.
    """
    env = dict(os.environ)
    # `python` in a manifest resolves to the interpreter Gauntlet runs under.
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    # A suite's package lives inside its directory, so the suite needs no
    # install step and the caller sets no PYTHONPATH.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(suite.workdir), str(suite.directory), *filter(None, [env.get("PYTHONPATH", "")])]
    )
    env.update(
        {
            "GAUNTLET_SUITE": suite.key,
            "GAUNTLET_SUITE_DIR": str(suite.directory),
            # Suite stdout is captured and re-rendered, so colour codes would
            # arrive as literal escape bytes in the log view.
            "NO_COLOR": "1",
            "PY_COLORS": "0",
            "FORCE_COLOR": "0",
            "TERM": "dumb",
        }
    )
    return env


def build_launch(
    suite: LoadedSuite,
    request: RunRequest,
    *,
    run_id: str,
    run_dir: Path,
    profile_path: Path | None,
    api_base: str | None = None,
    capability_env: dict[str, str] | None = None,
) -> Launch:
    """Assemble argv and environment for one run."""
    spec = suite.manifest.exec
    argv = list(spec.command)

    values: dict[str, str | None] = {
        "profile": str(profile_path) if profile_path else None,
        "run_dir": str(run_dir),
        "run_id": run_id,
        "target": request.target,
        "unit_serial": request.unit_serial,
    }
    for key, flag in spec.args.items():
        value = values.get(key)
        if value:
            argv += [flag, value]

    argv += _override_argv(suite, request.overrides)

    env = suite_environment(suite)
    env.update(
        {
            "GAUNTLET_RUN_DIR": str(run_dir),
            "GAUNTLET_RUN_ID": run_id,
        }
    )
    if profile_path is not None:
        env["GAUNTLET_PROFILE"] = str(profile_path)
    if request.target:
        env["GAUNTLET_TARGET"] = request.target
    if request.unit_serial:
        env["GAUNTLET_UNIT_SERIAL"] = request.unit_serial
    if api_base:
        env["GAUNTLET_API"] = api_base
    if capability_env:
        env.update(capability_env)
    env.update(spec.env)

    return Launch(
        argv=argv,
        cwd=suite.workdir,
        env=env,
        graceful_stop_signal=spec.graceful_stop_signal,
    )


def _override_argv(suite: LoadedSuite, overrides: dict[str, Any]) -> list[str]:
    argv: list[str] = []
    for name, value in overrides.items():
        if value is None:
            continue
        declared = suite.manifest.override(name)
        if declared is None:
            available = ", ".join(sorted(o.name for o in suite.manifest.overrides)) or "none"
            raise LaunchError(f"suite {suite.key!r} does not declare override {name!r} (declared: {available})")
        argv += _render_override(declared, value)
    return argv


def _render_override(spec: OverrideSpec, value: Any) -> list[str]:
    if spec.type == "boolean":
        return [spec.flag] if _as_bool(value) else []
    if spec.type in {"integer", "number"}:
        try:
            number = int(value) if spec.type == "integer" else float(value)
        except (TypeError, ValueError):
            raise LaunchError(f"override {spec.name!r} expects a {spec.type}, got {value!r}") from None
        return [spec.flag, str(number)]
    text = str(value)
    if spec.choices and text not in spec.choices:
        raise LaunchError(f"override {spec.name!r} must be one of {', '.join(spec.choices)}, got {text!r}")
    return [spec.flag, text]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "on", "true", "yes"}
