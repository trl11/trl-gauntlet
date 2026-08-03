"""Capabilities Gauntlet grants to a running suite.

Gauntlet holds the instrument serial ports. A suite declares what it needs in
``requires:``; Gauntlet verifies availability before spawning and passes an
HTTP endpoint the suite drives in place of the device.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CapabilityError(RuntimeError):
    """A required capability is not available."""


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
    """Something that can satisfy a named capability."""

    @property
    def name(self) -> str: ...

    def available(self) -> bool:
        """Is the backing hardware present and usable right now."""

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""


class CapabilityRegistry:
    """Tracks providers and issues grants for a run."""

    def __init__(self, *, api_base: str | None = None) -> None:
        self._providers: dict[str, CapabilityProvider] = {}
        self._api_base = api_base

    def register(self, provider: CapabilityProvider) -> None:
        """Add a provider, replacing any earlier one with the same name."""
        self._providers[provider.name] = provider

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
