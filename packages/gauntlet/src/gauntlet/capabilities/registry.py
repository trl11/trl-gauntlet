"""Capabilities Gauntlet grants to a running suite.

Gauntlet holds the instrument serial ports. A suite declares what it needs in
``requires:``; Gauntlet verifies availability before spawning and passes an
HTTP endpoint the suite drives in place of the device.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class CapabilityError(RuntimeError):
    """A required capability is not available."""


class CommandRejected(ValueError):
    """A capability refused a command, because of its name or its arguments."""


@dataclass(frozen=True)
class Grant:
    """One capability handed to a run."""

    name: str
    instance_id: str
    url: str

    def as_env(self) -> dict[str, str]:
        """Environment variables the suite reads to find this capability."""
        upper = self.name.upper()
        return {
            f"GAUNTLET_CAP_{upper}_URL": self.url,
            f"GAUNTLET_CAP_{upper}_ID": self.instance_id,
        }


class CapabilityProvider(Protocol):
    """Something that can satisfy a named capability.

    These four members are the whole obligation. Reading, writing, reporting
    state, and accepting commands are optional facets, each declared as its own
    runtime-checkable protocol below; callers test for them and degrade when a
    provider does not implement one.
    """

    @property
    def name(self) -> str: ...

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""


@runtime_checkable
class ReadableCapability(Protocol):
    """A provider whose current values can be read."""

    def read(self) -> dict[str, Any]:
        """Current values."""


@runtime_checkable
class WritableCapability(Protocol):
    """A provider that accepts settings."""

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply settings and return the resulting values."""


@runtime_checkable
class StatefulCapability(Protocol):
    """A provider that publishes structured state for the operator UI."""

    def state(self) -> dict[str, Any]:
        """Everything the UI renders for this instrument."""


@runtime_checkable
class CommandableCapability(Protocol):
    """A provider that can be driven by named commands."""

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return its result.

        Raises :class:`CommandRejected` for an unknown command or an argument
        it cannot use.
        """

    def commands(self) -> list[dict[str, Any]]:
        """The commands on offer, each with the fields it takes."""


@runtime_checkable
class PresentableCapability(Protocol):
    """A provider that says how its state should be laid out.

    Without this facet the UI lists every value in ``state()`` as a key and a
    value. With it, the provider nominates which values are worth a large tile,
    which belong in the compact strip beneath them, and which command is the
    one an operator reaches for. Nothing here changes what the instrument does;
    it is presentation, declared by the side that knows the instrument.
    """

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""

    def primary_command(self) -> str:
        """Name of the command the panel gives its full width to."""

    def readouts(self) -> list[dict[str, Any]]:
        """Which state values to show, and how.

        Each entry names a dotted path into ``state()`` and how to draw it::

            {"key": "channels.1.voltage", "label": "Voltage", "unit": "V",
             "precision": 2, "role": "headline", "group": "Channel 1"}

        ``role`` is ``"headline"`` for a large tile or ``"summary"`` for a row
        in the compact strip. ``group`` splits a multi-channel instrument into
        sections and may be empty.
        """


class CapabilityRegistry:
    """Tracks providers and issues grants for a run."""

    def __init__(self, *, api_base: str | None = None) -> None:
        self._providers: dict[str, CapabilityProvider] = {}
        self._api_base = api_base

    def register(self, provider: CapabilityProvider) -> None:
        """Add a provider, replacing any earlier one with the same name."""
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> CapabilityProvider | None:
        """Drop a provider, returning it, or ``None`` if there was none.

        A capability nothing provides is missing rather than unavailable: it
        stops being offered to a suite and stops appearing to the operator.
        """
        return self._providers.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._providers)

    def provider(self, name: str) -> CapabilityProvider | None:
        """Look up a registered provider by name."""
        return self._providers.get(name)

    def missing(self, required: list[str]) -> list[str]:
        """Which of the required capabilities cannot be satisfied right now."""
        unmet = []
        for name in required:
            provider = self._providers.get(name)
            if provider is None or not provider.available():
                unmet.append(name)
        return unmet

    def grants(self, required: list[str]) -> list[Grant]:
        """Issue a grant per requirement, raising if any cannot be met."""
        unmet = self.missing(required)
        if unmet:
            known = ", ".join(self.names()) or "none"
            raise CapabilityError(f"cannot start: capability {', '.join(unmet)} unavailable (registered: {known})")
        if not required:
            return []
        if not self._api_base:
            raise CapabilityError("cannot start: capabilities requested but the API base URL is unknown")
        return [
            Grant(
                name=name,
                instance_id=self._providers[name].instance_id(),
                url=f"{self._api_base.rstrip('/')}/capabilities/{name}",
            )
            for name in required
        ]

    def environment(self, required: list[str]) -> dict[str, str]:
        """Grant every requirement and flatten the result into environment variables."""
        env: dict[str, str] = {}
        for grant in self.grants(required):
            env.update(grant.as_env())
        return env

    def snapshot(self) -> list[dict[str, str]]:
        """Describe every registered provider for the UI."""
        rows = []
        for name in self.names():
            provider = self._providers[name]
            rows.append(
                {
                    "name": name,
                    "available": str(provider.available()).lower(),
                    "instance_id": provider.instance_id(),
                    **provider.describe(),
                }
            )
        return rows
