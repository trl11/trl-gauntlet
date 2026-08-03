"""Health, version, settings, capability status, and host telemetry.

The telemetry endpoints report what :mod:`gauntlet.api.host_stats` reads from
the host. ``cpu_percent`` is the one reading a single sample cannot give, so
the previous ``/proc/stat`` reading is kept on ``app.state``.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from gauntlet_suite.contract import CONTRACT_VERSION

from gauntlet.api import host_stats
from gauntlet.capabilities import CapabilityProvider, ReadableCapability, WritableCapability

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict[str, Any]:
    """Versions of the app, the SDK, and the contract they speak."""
    from gauntlet_suite import __version__ as sdk_version

    from gauntlet import __version__ as app_version

    return {
        "gauntlet": app_version,
        "gauntlet_suite": sdk_version,
        "contract_version": CONTRACT_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Current settings."""
    return request.app.state.settings.to_dict()


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    """Host facts that do not change while the app is running."""
    from gauntlet import __version__ as app_version

    return host_stats.static_info(app_version, sys.version.split()[0])


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
        "temperatures": host_stats.temperatures(),
        "uptime_s": host_stats.uptime(),
        "process_count": host_stats.process_count(),
    }


@router.get("/capabilities")
async def get_capabilities(request: Request) -> dict[str, Any]:
    """Registered capability providers and whether each is usable now."""
    return {"capabilities": request.app.state.capabilities.snapshot()}


@router.get("/capabilities/{name}")
async def read_capability(request: Request, name: str) -> dict[str, Any]:
    """Read one capability's state.

    Suites drive this endpoint in place of opening the device directly.
    """
    provider = _provider(request, name)
    if not isinstance(provider, ReadableCapability):
        raise HTTPException(status_code=405, detail=f"capability {name!r} is not readable")
    return dict(provider.read())


@router.post("/capabilities/{name}")
async def write_capability(request: Request, name: str, values: dict[str, Any]) -> dict[str, Any]:
    """Apply settings to one capability."""
    provider = _provider(request, name)
    if not isinstance(provider, WritableCapability):
        raise HTTPException(status_code=405, detail=f"capability {name!r} is not writable")
    return dict(provider.write(values))


def _provider(request: Request, name: str) -> CapabilityProvider:
    provider: CapabilityProvider | None = request.app.state.capabilities.provider(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown capability {name!r}")
    return provider
