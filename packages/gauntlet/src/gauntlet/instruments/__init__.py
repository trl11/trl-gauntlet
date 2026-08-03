"""Instruments Gauntlet drives on a suite's behalf.

Each one is a capability provider: it satisfies
:class:`~gauntlet.capabilities.registry.CapabilityProvider` and whichever
optional facets it can, and is registered with the
:class:`~gauntlet.capabilities.registry.CapabilityRegistry` at startup. The
three shipped here are simulated, so the wiring is exercisable without
hardware; a real driver belongs beside them under the same name.
"""

from __future__ import annotations

from gauntlet.instruments.mock_chamber import MockChamber
from gauntlet.instruments.mock_daq import MockDaq
from gauntlet.instruments.mock_psu import MockPsu

__all__ = [
    "MockChamber",
    "MockDaq",
    "MockPsu",
]
