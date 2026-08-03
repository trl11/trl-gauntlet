"""Gauntlet — a test-suite runner with a web UI.

Gauntlet launches anything conforming to the contract in
:mod:`gauntlet_sdk.contract`, streams its progress, and indexes what it left
behind. It knows nothing about any particular suite: suites declare themselves
in a ``suite.yaml``, and discovery does the rest.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
