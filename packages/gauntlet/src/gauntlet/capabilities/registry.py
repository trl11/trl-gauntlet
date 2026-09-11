"""Capabilities Gauntlet grants to a running suite.

Gauntlet holds the instrument serial ports. A suite declares what it needs in
``requires:``; Gauntlet verifies availability before spawning and passes an
HTTP endpoint the suite drives in place of the device.

A bench may hold two of one instrument, so a provider is registered under an
instance key rather than under its capability name: ``i2c`` where there is one
bridge, ``i2c.dut`` and ``i2c.ref`` where there are two. The role is the
bench's word for what the instrument is wired to, bound in ``config.yaml``, so
a suite asks for a job rather than for a serial number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


def instance_key(name: str, role: str = "") -> str:
    """The key one instance of a capability is registered and addressed under."""
    return f"{name}.{role}" if role else name


def capability_of(key: str) -> str:
    """The capability name an instance key belongs to."""
    return key.partition(".")[0]


def role_of(key: str) -> str:
    """The role an instance key carries, empty for the only one of its kind."""
    return key.partition(".")[2]


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
        """Environment variables the suite reads to find this capability.

        A dot is not legal in an environment variable name, so ``i2c.dut``
        becomes ``I2C__DUT``. The separator is doubled to leave a capability
        name its own single underscores, which is what lets the SDK tell
        ``laser_cutter`` from ``i2c.dut`` reading only the variable.
        """
        upper = self.name.upper().replace(".", "__")
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
    value. With it, the provider nominates which values the display burns
    large, which belong in the row beneath them, and which command is the one
    an operator reaches for. Nothing here changes what the instrument does; it
    is presentation, declared by the side that knows the instrument.
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

        ``role`` is ``"headline"`` for a reading the display burns large or
        ``"summary"`` for the row beneath it. ``group`` splits a multi-channel
        instrument into sections and may be empty.
        """


@runtime_checkable
class OwnableCapability(Protocol):
    """A provider whose device is claimed only while something owns it.

    A camera answering ``available()`` does not mean its capture node is
    open: neither an operator's panel poll nor a suite reading what it was
    granted touches hardware on its own. Something has to own it first — the
    panel's latching key, or, for exactly the run's duration,
    :meth:`CapabilityRegistry.claim_for_run`.
    """

    def owned(self) -> bool:
        """Is the device open right now."""

    def own(self) -> bool:
        """Open the device unless it already is, and report whether it now is."""

    def disown(self) -> None:
        """Close the device."""


def current_state(provider: CapabilityProvider) -> dict[str, Any]:
    """Structured state, falling back to a plain read for providers without it."""
    if isinstance(provider, StatefulCapability):
        return dict(provider.state())
    if isinstance(provider, ReadableCapability):
        return dict(provider.read())
    return {}


class CapabilityRegistry:
    """Tracks providers and issues grants for a run."""

    def __init__(self, *, api_base: str | None = None) -> None:
        self._providers: dict[str, CapabilityProvider] = {}
        self._api_base = api_base
        self._defaults: dict[str, str] = {}

    def register(self, provider: CapabilityProvider, *, role: str = "") -> None:
        """Add a provider, replacing any earlier one under the same key.

        A bench holding one of an instrument gives it no role, and it is
        registered under its capability name. A bench holding two gives each a
        role, and they are registered as ``i2c.dut`` and ``i2c.ref``.
        """
        self._providers[instance_key(provider.name, role)] = provider

    def unregister(self, key: str) -> CapabilityProvider | None:
        """Drop a provider, returning it, or ``None`` if there was none.

        A capability nothing provides is missing rather than unavailable: it
        stops being offered to a suite and stops appearing to the operator.
        """
        return self._providers.pop(key, None)

    def instance_keys(self) -> list[str]:
        """Every registered instance key."""
        return sorted(self._providers)

    def set_defaults(self, defaults: dict[str, str]) -> None:
        """Which role a bare capability name means, per capability.

        The bench's answer to a question a suite cannot settle. A suite asking
        for a bare ``i2c`` wants any one bus and has no way of telling two
        apart, so where there are two the operator says which, and that choice
        reaches the run through the grant rather than being guessed at here.
        """
        self._defaults = dict(defaults)

    def provider(self, key: str) -> CapabilityProvider | None:
        """Look up a registered provider by instance key."""
        return self._providers.get(key)

    def resolve(self, required: str) -> list[str]:
        """Instance keys one ``requires:`` entry could name.

        An entry carrying a role names one instrument exactly. A bare
        capability name matches every instance of it, which is one on a bench
        that binds no roles: that is what lets a suite needing a single bus
        stay silent about a distinction its bench may not draw. Where a bench
        holds two, the bare name means whichever the bench has made the
        default, and is otherwise an ambiguity to refuse rather than settle,
        because the two buses go to different places.
        """
        if required in self._providers:
            return [required]
        matches = sorted(key for key in self._providers if capability_of(key) == required)
        if len(matches) > 1:
            preferred = instance_key(required, self._defaults.get(required, ""))
            if preferred in self._providers:
                return [preferred]
        return matches

    def missing(self, required: list[str]) -> list[str]:
        """Which of the required capabilities cannot be satisfied right now.

        An ambiguous entry counts as unsatisfied, so a suite naming a bare
        capability the bench has two of is offered no run to start.
        """
        unmet = []
        for name in required:
            keys = self.resolve(name)
            if len(keys) != 1 or not self._providers[keys[0]].available():
                unmet.append(name)
        return unmet

    def grants(self, required: list[str]) -> list[Grant]:
        """Issue a grant per requirement, raising if any cannot be met."""
        for name in required:
            keys = self.resolve(name)
            if len(keys) > 1:
                raise CapabilityError(
                    f"cannot start: {name!r} names {len(keys)} instruments on this bench "
                    f"({', '.join(keys)}); name one of them in requires:"
                )
        unmet = self.missing(required)
        if unmet:
            known = ", ".join(self.instance_keys()) or "none"
            raise CapabilityError(f"cannot start: capability {', '.join(unmet)} unavailable (registered: {known})")
        if not required:
            return []
        if not self._api_base:
            raise CapabilityError("cannot start: capabilities requested but the API base URL is unknown")
        grants = []
        for name in required:
            key = self.resolve(name)[0]
            grants.append(
                Grant(
                    name=name,
                    instance_id=self._providers[key].instance_id(),
                    url=f"{self._api_base.rstrip('/')}/capabilities/{key}",
                )
            )
        return grants

    def claim_for_run(self, required: list[str]) -> Callable[[], None]:
        """Own every ownable requirement not already owned, for the run's duration.

        A capability an operator already has open by hand is left exactly as
        found — a run never disowns what it did not own itself — so the
        release this returns closes only what it opened, and calling it
        leaves the bench exactly as it was before the run started.
        """
        claimed: list[OwnableCapability] = []
        for name in required:
            keys = self.resolve(name)
            provider = self._providers[keys[0]] if len(keys) == 1 else None
            if not isinstance(provider, OwnableCapability) or provider.owned():
                continue
            if not provider.own():
                for owned in reversed(claimed):
                    owned.disown()
                raise CapabilityError(f"cannot start: {name} could not be opened{_because(provider)}")
            claimed.append(provider)

        def _release() -> None:
            while claimed:
                claimed.pop().disown()

        return _release

    def close_all(self) -> None:
        """Release every provider's device, for a process that is shutting down.

        A device is normally released when the process dies, but a driver
        holding a library's own handle may not survive being torn down that
        way: the GenTL layer the Allied Vision camera is reached through
        faults inside its own teardown and takes the process down with it.
        Closing deliberately is what avoids that.
        """
        for provider in self._providers.values():
            release = getattr(provider, "close", None)
            if callable(release):
                release()

    def environment(self, required: list[str]) -> dict[str, str]:
        """Grant every requirement and flatten the result into environment variables."""
        env: dict[str, str] = {}
        for grant in self.grants(required):
            env.update(grant.as_env())
        return env

    def snapshot(self) -> list[dict[str, str]]:
        """Describe every registered provider for the UI."""
        rows = []
        for key in self.instance_keys():
            provider = self._providers[key]
            rows.append(
                {
                    "name": key,
                    "available": str(provider.available()).lower(),
                    "instance_id": provider.instance_id(),
                    **provider.describe(),
                }
            )
        return rows


def _because(provider: CapabilityProvider) -> str:
    """Why the provider says it is unusable, when it says anything.

    A driver that refused to open knows the reason and a run that could not
    start is where it is worth reading, so it is carried into the rejection
    rather than left for whoever thinks to poll the panel afterwards.
    """
    reason = provider.describe().get("unavailable_reason", "")
    return f": {reason}" if reason else ""
