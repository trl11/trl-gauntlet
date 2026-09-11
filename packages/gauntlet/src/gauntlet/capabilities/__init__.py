"""What Gauntlet grants a run, and how a provider declares itself.

The concrete providers live in :mod:`gauntlet.instruments`.
"""

from __future__ import annotations

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import (
    CapabilityError,
    CapabilityProvider,
    CapabilityRegistry,
    CommandableCapability,
    CommandRejected,
    Grant,
    OwnableCapability,
    PresentableCapability,
    ReadableCapability,
    StatefulCapability,
    WritableCapability,
    capability_of,
    current_state,
    instance_key,
    role_of,
)

__all__ = [
    "CapabilityError",
    "CapabilityProvider",
    "CapabilityRegistry",
    "CommandRejected",
    "CommandableCapability",
    "Grant",
    "OwnableCapability",
    "PresentableCapability",
    "ReadableCapability",
    "StatefulCapability",
    "WritableCapability",
    "capability_of",
    "command_field",
    "current_state",
    "instance_key",
    "number_arg",
    "readout",
    "role_of",
]
