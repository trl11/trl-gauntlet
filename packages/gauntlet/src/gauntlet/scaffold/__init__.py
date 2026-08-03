"""Generates a new suite directory from a template.

Drives ``gauntlet new-suite`` and ``gauntlet templates``. Templates ship with
the package, so scaffolding works from an installed Gauntlet.
"""

from __future__ import annotations

from gauntlet.scaffold import generator
from gauntlet.scaffold.generator import (
    Placeholders,
    ScaffoldError,
    available_templates,
    render,
)

__all__ = [
    "Placeholders",
    "ScaffoldError",
    "available_templates",
    "generator",
    "render",
]
