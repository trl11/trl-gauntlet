"""Shared setup for the logic capture suite's tests.

The suite package lives beside these tests rather than being installed, so the
suite directory goes on ``sys.path`` the same way Gauntlet puts it there for a
real run.
"""

from __future__ import annotations

import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))
