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
import socket
from collections.abc import Callable

from gauntlet.capabilities.registry import CapabilityProvider, CapabilityRegistry
from gauntlet.config import Settings
from gauntlet.instruments.alvium_camera import AlviumCamera
from gauntlet.instruments.cp2112_i2c import Cp2112I2c, candidate_adapters
from gauntlet.instruments.di2008_daq import Di2008Daq
from gauntlet.instruments.fx2_logic import Fx2Logic
from gauntlet.instruments.hm310t_psu import Hm310tPsu, candidate_ports
from gauntlet.instruments.mock_camera import MockCamera
from gauntlet.instruments.mock_chamber import MockChamber
from gauntlet.instruments.mock_daq import MockDaq
from gauntlet.instruments.mock_i2c import MockI2c
from gauntlet.instruments.mock_logic import MockLogic
from gauntlet.instruments.mock_psu import MockPsu
from gauntlet.instruments.ni_daqmx import NiDaqmxDaq, parse_target
from gauntlet.instruments.uvc_camera import UvcCamera

log = logging.getLogger("gauntlet.instruments.detect")

# What marks a ``daq`` setting as naming an NI-DAQmx device rather than a
# DI-2008 serial number.
_DAQMX_PREFIX = "daqmx:"

# The one gRPC device server address worth looking for. NI's driver is a kernel
# module, so Gauntlet in a container has none of its own and reaches the host's
# through this; the devcontainer publishes the host under this name. It is a
# fixed property of running in a container rather than a bench someone had to
# configure, which is what makes it findable where another address is not.
_DAQMX_SERVER = "host.docker.internal:31763"

# Longest a probe of that address may block. A ceiling rather than a cost: an
# unknown name fails in the resolver and a closed port is refused at once, so a
# bench with no server there pays neither.
_PROBE_TIMEOUT_S = 0.25


def detect_instruments(registry: CapabilityRegistry, settings: Settings) -> None:
    """Register every instrument that answers, and drop every one that does not."""
    simulated = set(settings.simulated_instruments)
    _settle(
        registry,
        "camera",
        MockCamera if "camera" in simulated else lambda: _camera(settings.camera_device, settings.camera_format),
    )
    # The chamber has no driver for real hardware, so it exists only while it
    # is being simulated.
    _settle(registry, "chamber", MockChamber if "chamber" in simulated else _absent)
    _settle(registry, "daq", MockDaq if "daq" in simulated else lambda: _daq(settings.daq_serial))
    _settle(registry, "i2c", MockI2c if "i2c" in simulated else lambda: _i2c(settings.i2c_serial))
    _settle(
        registry,
        "logic",
        MockLogic if "logic" in simulated else lambda: _logic(settings.logic_serial, settings.logic_firmware),
    )
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


def _camera(device: str, frame_format: str = "auto") -> CapabilityProvider | None:
    """The camera, if one is asked for and a candidate is present.

    Two drivers answer this capability and the setting says which. A
    ``/dev/video*`` path is a UVC camera; anything else names an Allied
    Vision camera by serial, because a USB3 Vision camera has no node to
    name. ``"auto"`` prefers an Allied Vision camera when one is on the bus:
    it is an instrument somebody attached deliberately, where a capture node
    is whatever the host happens to have, a built-in webcam included.

    A named Allied Vision camera is registered whether or not it answers, the
    way a named port is for every other instrument: the operator said there is
    one there, so its absence is reported through `unavailable_reason` rather
    than hidden. Only `"auto"` falls through to a capture node.

    Registering it never opens it: `available()` only looks at sysfs, and
    nothing owns the device until an operator or a run does.
    """
    if not device:
        return None
    if device != "auto" and not device.startswith("/"):
        return AlviumCamera(serial_filter=device)
    if device == "auto":
        alvium = AlviumCamera()
        if alvium.available():
            return alvium
    camera = UvcCamera(device="" if device == "auto" else device, frame_format=frame_format)
    if camera.available():
        return camera
    return None


def _daq(serial: str) -> CapabilityProvider | None:
    """The acquisition unit, if one is asked for and one answers.

    Two drivers answer this capability and the setting says which. A
    ``daqmx:`` prefix names something NI-DAQmx knows — a device by its DAQmx
    name, or a gRPC device server and optionally a device on it; anything else
    is a DI-2008 USB serial number. The prefix is what tells them apart,
    because a DAQmx device name and a USB serial number are both bare strings
    and neither can be recognised on sight.

    ``"auto"`` probes the DI-2008 first and falls through to the local
    NI-DAQmx, so a bench that has always had a DI-2008 keeps it when an NI
    module is added beside it. Last it tries the gRPC device server on the
    container's host, which is the only address it looks for and the only way
    an NI module is reachable from inside a container at all. Any other server
    is named in the setting: an address in general is somewhere to connect
    rather than something to find.
    """
    if not serial:
        return None
    if serial.startswith(_DAQMX_PREFIX):
        return NiDaqmxDaq(target=parse_target(serial[len(_DAQMX_PREFIX) :]))
    if serial != "auto":
        return Di2008Daq(serial_filter=serial)
    daq = Di2008Daq()
    if daq.available():
        return daq
    _close(daq)
    module = NiDaqmxDaq()
    if module.available():
        return module
    _close(module)
    if not _listening(_DAQMX_SERVER):
        return None
    module = NiDaqmxDaq(target=parse_target(f"//{_DAQMX_SERVER}"))
    if module.available():
        return module
    _close(module)
    return None


def _i2c(serial: str) -> CapabilityProvider | None:
    """The CP2112 bridge, if one is asked for and one answers.

    The kernel adapts it to an ordinary ``i2c-dev`` node itself, so there is
    nothing to open speculatively: a candidate that is not there does not
    appear in ``candidate_adapters()`` at all.
    """
    if not serial:
        return None
    for node, adapter_serial in candidate_adapters():
        if serial != "auto" and serial != adapter_serial:
            continue
        bridge = Cp2112I2c(node, instance=f"i2c-{adapter_serial or node.rsplit('-', 1)[-1]}")
        if bridge.available():
            return bridge
        _close(bridge)
    return None


def _listening(address: str) -> bool:
    """Is anything accepting connections at ``host:port``.

    Asked before building a provider for a server, so a scan on a bench that
    has none neither blocks nor logs a driver error it can do nothing about.
    """
    host, _, port = address.partition(":")
    try:
        with socket.create_connection((host, int(port)), _PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _drop(registry: CapabilityRegistry, name: str) -> None:
    """Unregister an instrument, releasing whatever it held."""
    gone = registry.unregister(name)
    if gone is not None:
        log.info("instrument %s: no longer present", name)
        _close(gone)


def _logic(serial: str, firmware: str) -> CapabilityProvider | None:
    """The logic analyzer, if one is asked for and a board is on the bus.

    Registration turns on the board being there rather than on it being
    usable. One that arrived without firmware is not usable until it has been
    loaded and has come back on the bus, and an operator who can see it
    waiting reads that from ``unavailable_reason`` rather than from an empty
    Instruments page.
    """
    if not serial:
        return None
    if serial != "auto":
        return Fx2Logic(firmware=firmware, serial_filter=serial)
    analyzer = Fx2Logic(firmware=firmware)
    if analyzer.attached():
        return analyzer
    _close(analyzer)
    return None


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
