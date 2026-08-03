"""The endpoints a running suite drives in place of opening a device.

Gauntlet holds the instrument. A suite names what it needs in ``requires:``,
is granted a URL under this router, and reads and writes the device through it.
:mod:`gauntlet.api.instruments` serves the same providers to the operator; this
is the half the suite process sees.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gauntlet.capabilities import CapabilityProvider, ReadableCapability, WritableCapability

router = APIRouter()


@router.get("/capabilities")
async def get_capabilities(request: Request) -> dict[str, Any]:
    """Registered capability providers and whether each is usable now."""
    return {"capabilities": request.app.state.capabilities.snapshot()}


@router.get("/capabilities/{name}")
async def read_capability(request: Request, name: str) -> dict[str, Any]:
    """Read one capability's state."""
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
