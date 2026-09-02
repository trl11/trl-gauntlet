"""Health, settings, versions, contract schemas, host telemetry, and power.

The telemetry endpoints report what :mod:`gauntlet.api.host_stats` reads from
the host. ``cpu_percent`` is the one reading a single sample cannot give, so
the previous ``/proc/stat`` reading is kept on ``app.state``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from gauntlet_sdk.contract import CONTRACT_MODELS, CONTRACT_VERSION, json_schema
from pydantic import BaseModel, ConfigDict

from gauntlet.api import host_stats

log = logging.getLogger("gauntlet.api.system")

router = APIRouter()

# Long enough to reach logind and be told the request was accepted, short
# enough that a host which will not answer does not hold the request open. The
# machine goes down after this returns, not during it.
_POWER_TIMEOUT_S = 10.0

_POWER_COMMANDS = {"poweroff": "poweroff", "reboot": "reboot"}


class PowerBody(BaseModel):
    """Which way to take the host down."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["poweroff", "reboot"]


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Current settings."""
    return request.app.state.settings.to_dict()


@router.get("/schemas")
async def get_schemas() -> dict[str, Any]:
    """Names of the contract schemas available."""
    return {"schemas": sorted(CONTRACT_MODELS)}


@router.get("/schemas/{name}")
async def get_schema(name: str) -> dict[str, Any]:
    """JSON Schema for one contract model, generated from the pydantic source."""
    try:
        return json_schema(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    """Host facts and versions, none of which change while the app is running.

    Everything the retired `/version` reported is here: the host was already
    described alongside the app's own version, and answering both from one
    place stops `platform.platform()` being served twice under two names.
    """
    from gauntlet_sdk import __version__ as sdk_version

    from gauntlet import __version__ as app_version

    info = host_stats.static_info(app_version, sys.version.split()[0])
    info["gauntlet_sdk"] = sdk_version
    info["contract_version"] = CONTRACT_VERSION
    return info


@router.get("/system/data")
async def system_data(request: Request) -> dict[str, Any]:
    """A sample of what the host is doing right now.

    ``cpu_percent`` is null on the first call: percentages come from the
    difference between two readings of ``/proc/stat``, and the first request
    has nothing to compare against.
    """
    previous = request.app.state.cpu_sample
    current = host_stats.cpu_times()
    request.app.state.cpu_sample = current
    overall, per_core = host_stats.cpu_percent(previous, current)
    return {
        "cpu_percent": overall,
        "cpu_per_core": per_core,
        "load_avg": host_stats.load_avg(),
        "memory": host_stats.memory(),
        "swap": host_stats.swap(),
        "disks": host_stats.disks(),
        # The one an operator cares about: where this Gauntlet writes its runs,
        # rather than whichever mount happens to be fullest.
        "disk": host_stats.disk_for(request.app.state.settings.runs_dir),
        "interfaces": host_stats.interfaces(),
        "temperatures": host_stats.temperatures(),
        "uptime_s": host_stats.uptime(),
        "process_count": host_stats.process_count(),
    }


@router.post("/system/power")
async def system_power(request: Request, body: PowerBody) -> dict[str, Any]:
    """Take this host down, so a rig needs neither a login nor its power switch.

    Refused while a run is in flight: a suite mid-measurement is the one thing
    on the bench that cannot be restarted from where it left off, and an
    operator who meant to stop it can.

    ``systemctl`` rather than a signal of our own, because logind is what
    unmounts the disks and stops the units in order. It needs no root: the
    services run as the operator, and logind lets a local user power off the
    machine they are the only one on. A host that refuses says so, and the
    reason reaches the operator rather than the log alone.
    """
    action = _POWER_COMMANDS[body.action]

    active = request.app.state.supervisor.active()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{active.suite} is running as {active.run_id}; stop it first",
        )

    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise HTTPException(status_code=503, detail="there is no systemctl on this host")

    log.warning("%s requested through the API", action)
    try:
        finished = subprocess.run(
            [systemctl, action],
            capture_output=True,
            text=True,
            timeout=_POWER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"{action} did not answer in time") from exc
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip()
        # Polkit allows this without a password only for a user with an active
        # local session, and a rig serves from a lingering user manager that
        # has none. Saying which step installs the rule is the difference
        # between a fixable message and a puzzle, because the account really
        # does have the right and only from a console.
        if "authentication required" in detail.lower():
            detail = f"{detail} Run setup-host.sh on this bench to install the polkit rule that allows it."
        raise HTTPException(status_code=502, detail=detail or f"{action} was refused")

    return {"action": body.action, "status": "accepted"}
