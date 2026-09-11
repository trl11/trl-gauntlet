"""Gauntlet — a test-suite runner with a web UI.

Gauntlet launches anything conforming to the contract in
:mod:`gauntlet_sdk.contract`, streams its progress, and indexes what it left
behind. It knows nothing about any particular suite: suites declare themselves
in a ``suite.yaml``, and discovery does the rest.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"


def git_sha() -> str | None:
    """The commit this build was made from, or ``None`` when it cannot be known.

    A wheel, AppImage, or deb carries no ``.git``, so the hash is baked into
    ``_build_info.py`` by whichever Makefile target builds it. ``GAUNTLET_GIT_SHA``
    lets a container image set it at run time instead, since a Docker build
    stage does not share a working directory with the source it built from. A
    live checkout run through ``make run``/``make dev`` has taken neither path,
    so it falls back to asking git directly.
    """
    from_env = os.environ.get("GAUNTLET_GIT_SHA")
    if from_env:
        return from_env
    try:
        from gauntlet._build_info import GIT_SHA

        if GIT_SHA:
            return GIT_SHA
    except ImportError:
        pass
    from gauntlet_sdk.reporting.manifest import git_state

    return git_state(Path(__file__).resolve().parent).sha


__all__ = ["__version__", "git_sha"]
