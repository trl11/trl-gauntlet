"""The endpoints a running suite drives in place of opening a device.

Gauntlet holds the instrument. A suite names what it needs in ``requires:``,
is granted a URL under this router, and reads and writes the device through it.
The path segment is the instance key, so a bench holding two bridges serves
them at ``/capabilities/i2c.dut`` and ``/capabilities/i2c.ref``.
:mod:`gauntlet.api.instruments` serves the same providers to the operator; this
is the half the suite process sees.

There is no listing here on purpose. A suite is handed the capabilities it was
granted and discovers nothing else, and the operator sees the bench through
``GET /api/instruments``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gauntlet.capabilities import (
    CapabilityProvider,
    CommandRejected,
    ReadableCapability,
    WritableCapability,
)

router = APIRouter()


@router.get("/capabilities/{key}")
async def read_capability(request: Request, key: str) -> dict[str, Any]:
    """Read one capability's state."""
    provider = _provider(request, key)
    if not isinstance(provider, ReadableCapability):
        raise HTTPException(status_code=405, detail=f"capability {key!r} is not readable")
    return dict(provider.read())


@router.post("/capabilities/{key}")
async def write_capability(request: Request, key: str, values: dict[str, Any]) -> dict[str, Any]:
    """Apply settings to one capability.

    A provider that refuses answers 422 carrying its own words, the same way
    ``POST /api/instruments/{key}/command`` does. A suite is the only caller
    here, so a rejection it can read is the difference between a run that
    reports what it asked for wrongly and one that reports a server fault.
    """
    provider = _provider(request, key)
    if not isinstance(provider, WritableCapability):
        raise HTTPException(status_code=405, detail=f"capability {key!r} is not writable")
    try:
        return dict(provider.write(values))
    except CommandRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _provider(request: Request, key: str) -> CapabilityProvider:
    """The provider one granted URL names.

    The path segment is an instance key, so ``i2c`` and ``i2c.dut`` are two
    different instruments and a suite reaches whichever one it was granted.
    """
    provider: CapabilityProvider | None = request.app.state.capabilities.provider(key)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown capability {key!r}")
    return provider
