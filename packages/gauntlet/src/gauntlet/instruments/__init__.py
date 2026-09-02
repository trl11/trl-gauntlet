"""Instruments Gauntlet drives on a suite's behalf.

Each one is a capability provider: it satisfies
:class:`~gauntlet.capabilities.registry.CapabilityProvider` and whichever
optional facets it can, and is registered with the
:class:`~gauntlet.capabilities.registry.CapabilityRegistry` at startup.

Each instrument has a simulated provider and may have a driver for a real
device beside it under the same name.
:func:`~gauntlet.instruments.detect.detect_instruments` decides which of them
is registered, and registers a simulation only when the settings ask for one.
"""

from __future__ import annotations

from gauntlet.instruments.detect import detect_instruments, is_simulated
from gauntlet.instruments.di2008_daq import Di2008Daq
from gauntlet.instruments.fx2_logic import Fx2Logic
from gauntlet.instruments.hm310t_psu import Hm310tPsu
from gauntlet.instruments.mock_camera import MockCamera
from gauntlet.instruments.mock_chamber import MockChamber
from gauntlet.instruments.mock_daq import MockDaq
from gauntlet.instruments.mock_logic import MockLogic
from gauntlet.instruments.mock_psu import MockPsu
from gauntlet.instruments.uvc_camera import UvcCamera

__all__ = [
    "Di2008Daq",
    "Fx2Logic",
    "Hm310tPsu",
    "MockCamera",
    "MockChamber",
    "MockDaq",
    "MockLogic",
    "MockPsu",
    "UvcCamera",
    "detect_instruments",
    "is_simulated",
]
