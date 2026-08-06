"""Instrument panel over the capability registry.

Instruments are capability providers seen from the operator's side. This module
is generic: everything it reports comes from the provider's own description,
state, and command list, so registering a new provider is enough to give it a
panel. A provider that does not implement an optional facet degrades to empty
state, no commands, or a 405.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from gauntlet.capabilities import (
    CapabilityProvider,
    CommandableCapability,
    CommandRejected,
    PresentableCapability,
    ReadableCapability,
    StatefulCapability,
)

router = APIRouter()


class CommandBody(BaseModel):
    """Request body for driving one instrument."""

    model_config = ConfigDict(extra="forbid")

    command: str
    args: dict[str, Any] = Field(default_factory=dict)


@router.get("/instruments")
async def list_instruments(request: Request) -> dict[str, Any]:
    """Every registered instrument, with its state and the commands it takes."""
    return {"instruments": _snapshot(request)}


@router.post("/instruments/rescan")
async def rescan_instruments(request: Request) -> dict[str, Any]:
    """Look for hardware again and report what is registered afterwards.

    Detection runs at startup, so this is what picks up an instrument attached
    since and what drops one that has gone. A provider driving hardware that
    still answers is left connected.
    """
    detect = getattr(request.app.state, "detect_instruments", None)
    if callable(detect):
        detect()
    return {"instruments": _snapshot(request)}


@router.get("/instruments/{name}")
async def get_instrument(request: Request, name: str) -> dict[str, Any]:
    """One instrument."""
    return _describe(_provider(request, name), _holder(request))


@router.post("/instruments/{name}/command")
async def post_command(request: Request, name: str, body: CommandBody) -> dict[str, Any]:
    """Drive one instrument and return the state the command left behind."""
    provider = _provider(request, name)
    if not isinstance(provider, CommandableCapability):
        raise HTTPException(status_code=405, detail=f"instrument {name!r} takes no commands")
    try:
        result = provider.command(body.command, dict(body.args))
    except CommandRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"state": _state(provider), "result": dict(result)}


def _commands(provider: CapabilityProvider) -> list[dict[str, Any]]:
    if not isinstance(provider, CommandableCapability):
        return []
    # `danger` is filled in here so every command carries the key, whether or
    # not its provider bothered to say the command energises something.
    return [{"danger": False, **command} for command in provider.commands()]


def _describe(provider: CapabilityProvider, holder: tuple[str, frozenset[str]] | None = None) -> dict[str, Any]:
    detail = provider.describe()
    available = provider.available()
    return {
        # The run driving this instrument, so the operator can see that taking
        # it over by hand would cut across a test. Empty when nothing holds it.
        "in_use_by": holder[0] if holder is not None and provider.name in holder[1] else "",
        "name": provider.name,
        "kind": detail.get("kind") or provider.name,
        "available": available,
        # Why the instrument cannot be used, in the provider's own words. Empty
        # when it is available or when the provider offers no explanation.
        "unavailable_reason": "" if available else detail.get("unavailable_reason", ""),
        "instance_id": provider.instance_id(),
        "description": detail.get("description") or detail.get("model") or "",
        "state": _state(provider),
        "commands": _commands(provider),
        **_presentation(provider),
    }


def _presentation(provider: CapabilityProvider) -> dict[str, Any]:
    """How the provider asks its state to be laid out.

    Empty for a provider that does not say, which is what makes the UI fall
    back to listing every state value as a row.
    """
    if not isinstance(provider, PresentableCapability):
        return {"connection": "", "primary_command": "", "readouts": []}
    return {
        "connection": provider.connection(),
        "primary_command": provider.primary_command(),
        "readouts": [dict(entry) for entry in provider.readouts()],
    }


def _holder(request: Request) -> tuple[str, frozenset[str]] | None:
    """The in-flight run and the capabilities its suite declared it drives.

    A suite names what it needs in its manifest, so that is what says which
    instruments a run is holding. Nothing here knows one instrument from
    another. ``None`` when no run is in flight, or when the running suite is no
    longer in the catalog.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    active = supervisor.active() if supervisor is not None else None
    if active is None:
        return None
    suite = request.app.state.catalog().get(active.suite)
    if suite is None:
        return None
    return active.run_id, frozenset(suite.manifest.requires)


def _provider(request: Request, name: str) -> CapabilityProvider:
    provider = request.app.state.capabilities.provider(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument {name!r}")
    return provider


def _snapshot(request: Request) -> list[dict[str, Any]]:
    registry = request.app.state.capabilities
    holder = _holder(request)
    return [_describe(registry.provider(name), holder) for name in registry.names()]


def _state(provider: CapabilityProvider) -> dict[str, Any]:
    """Structured state, falling back to a plain read for providers without it."""
    if isinstance(provider, StatefulCapability):
        return dict(provider.state())
    if isinstance(provider, ReadableCapability):
        return dict(provider.read())
    return {}
