"""Health, version, settings, and capability status."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from gauntlet_suite.contract import CONTRACT_VERSION

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
    if not hasattr(provider, "read"):
        raise HTTPException(status_code=405, detail=f"capability {name!r} is not readable")
    return dict(provider.read())


@router.post("/capabilities/{name}")
async def write_capability(request: Request, name: str, values: dict[str, Any]) -> dict[str, Any]:
    """Apply settings to one capability."""
    provider = _provider(request, name)
    if not hasattr(provider, "write"):
        raise HTTPException(status_code=405, detail=f"capability {name!r} is not writable")
    return dict(provider.write(values))


def _provider(request: Request, name: str) -> Any:
    provider = request.app.state.capabilities.provider(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown capability {name!r}")
    return provider
