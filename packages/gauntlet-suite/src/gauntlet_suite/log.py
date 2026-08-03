"""Stdout logging for suite runners.

Gauntlet timestamps captured stdout and infers a level from the ``warn:`` and
``error:`` prefixes these helpers emit.
"""

from __future__ import annotations

import sys
from typing import TextIO


def _emit(msg: str, *, stream: TextIO | None = None) -> None:
    print(msg, file=stream or sys.stdout, flush=True)


def info(msg: str) -> None:
    """Info-level checkpoint."""
    _emit(msg)


def warn(msg: str) -> None:
    """Warning-level checkpoint."""
    _emit(f"warn: {msg}")


def err(msg: str) -> None:
    """Error-level checkpoint."""
    _emit(f"error: {msg}")
