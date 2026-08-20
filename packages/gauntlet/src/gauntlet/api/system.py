"""Health, settings, versions, contract schemas, and host telemetry.

The telemetry endpoints report what :mod:`gauntlet.api.host_stats` reads from
the host. ``cpu_percent`` is the one reading a single sample cannot give, so
the previous ``/proc/stat`` reading is kept on ``app.state``.
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from gauntlet_sdk.contract import CONTRACT_MODELS, CONTRACT_VERSION, json_schema

from gauntlet.api import host_stats

router = APIRouter()


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
        "temperatures": host_stats.temperatures(),
        "uptime_s": host_stats.uptime(),
        "process_count": host_stats.process_count(),
    }
