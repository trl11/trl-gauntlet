"""Choosing what backs each instrument, and whether it exists at all.

An instrument is registered only while its hardware answers, so the operator
sees the bench as it really is: unplug a device and the next scan drops it,
plug one in and the next scan picks it up. Nothing simulated is registered
unless ``simulated_instruments`` names it, which is for development and tests.

Settings say where to look. ``"auto"`` probes, ``""`` does not look at all, and
anything else is the serial port or USB serial number to use. An explicitly
named device stays registered even when it goes quiet, reporting why through
``unavailable_reason`` — the operator said there is one there, so its absence
is a fault to show rather than something to hide.

Detection runs at startup and again on every operator scan.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gauntlet.capabilities.registry import CapabilityProvider, CapabilityRegistry
from gauntlet.config import Settings
from gauntlet.instruments.di2008_daq import Di2008Daq
from gauntlet.instruments.hm310t_psu import Hm310tPsu, candidate_ports
from gauntlet.instruments.mock_chamber import MockChamber
from gauntlet.instruments.mock_daq import MockDaq
from gauntlet.instruments.mock_psu import MockPsu

log = logging.getLogger("gauntlet.instruments.detect")


def detect_instruments(registry: CapabilityRegistry, settings: Settings) -> None:
    """Register every instrument that answers, and drop every one that does not."""
    simulated = set(settings.simulated_instruments)
    # The chamber has no driver for real hardware, so it exists only while it
    # is being simulated.
    _settle(registry, "chamber", MockChamber if "chamber" in simulated else _absent)
    _settle(registry, "daq", MockDaq if "daq" in simulated else lambda: _daq(settings.daq_serial))
    _settle(registry, "psu", MockPsu if "psu" in simulated else lambda: _psu(settings.psu_port))


def is_simulated(provider: CapabilityProvider) -> bool:
    """Is this provider a simulation rather than a device.

    Every simulated instrument reports ``driver: "mock"``, which keeps this
    from having to know their class names.
    """
    return provider.describe().get("driver", "") == "mock"


def _absent() -> None:
    """Nothing at all, for an instrument with nothing to back it."""
    return None


def _close(provider: CapabilityProvider) -> None:
    """Release whatever a provider holds, for one that holds anything."""
    release = getattr(provider, "close", None)
    if callable(release):
        release()


def _daq(serial: str) -> CapabilityProvider | None:
    if not serial:
        return None
    if serial != "auto":
        return Di2008Daq(serial_filter=serial)
    daq = Di2008Daq()
    if daq.available():
        return daq
    _close(daq)
    return None


def _drop(registry: CapabilityRegistry, name: str) -> None:
    """Unregister an instrument, releasing whatever it held."""
    gone = registry.unregister(name)
    if gone is not None:
        log.info("instrument %s: no longer present", name)
        _close(gone)


def _psu(port: str) -> CapabilityProvider | None:
    if not port:
        return None
    if port != "auto":
        return Hm310tPsu(port)
    for candidate in candidate_ports():
        psu = Hm310tPsu(candidate)
        if psu.available():
            return psu
        _close(psu)
    return None


def _settle(registry: CapabilityRegistry, name: str, build: Callable[[], CapabilityProvider | None]) -> None:
    """Register what ``build`` returns, unless what is registered is better.

    A working device is never rebuilt: doing so would drop the connection the
    panel is reading through. A simulation is left in place when the choice
    lands on a simulation again, so a scan does not restart it. Building
    nothing means nothing is there, and the instrument is dropped.
    """
    existing = registry.provider(name)
    if existing is not None and not is_simulated(existing) and existing.available():
        return
    provider = build()
    if provider is None:
        _drop(registry, name)
        return
    if existing is not None and is_simulated(existing) and is_simulated(provider):
        return
    if existing is not None:
        _close(existing)
    log.info("instrument %s: %s", name, provider.describe().get("model", provider.name))
    registry.register(provider)
