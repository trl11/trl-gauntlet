"""The frontend's captured API responses, checked against what the API emits.

``frontend/src/test/fixtures/api.json`` holds real responses, and the frontend
types every export from it so ``tsc`` fails when a body and ``api/types.ts``
disagree. Nothing on this side checked that the capture still matches the
server, so a field added here stayed absent there until someone recaptured by
hand.

The suites in the capture are whatever was installed the day it was taken, so
the catalog is not compared. What is compared is the shape a profile entry has
and the two things Gauntlet derives rather than stores: the label shown for a
filename, and the order profiles are served in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gauntlet.suites.discovery import ProfileInfo, list_profiles

FIXTURE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "test" / "fixtures" / "api.json"

PROFILE_KEYS = {"name", "path", "user_authored"}


def _captured() -> Any:
    if not FIXTURE.is_file():
        pytest.skip(f"{FIXTURE} is not in this checkout")
    return json.loads(FIXTURE.read_text())


def _profile_entries(node: Any) -> list[dict[str, Any]]:
    """Every captured profile entry, wherever it sits in the capture."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.keys() >= PROFILE_KEYS:
            found.append(node)
        for value in node.values():
            found.extend(_profile_entries(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_profile_entries(value))
    return found


def _profile_lists(node: Any) -> list[list[dict[str, Any]]]:
    """Every captured list that is a profile list."""
    found: list[list[dict[str, Any]]] = []
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_profile_lists(value))
    elif isinstance(node, list):
        if node and all(isinstance(e, dict) and e.keys() >= PROFILE_KEYS for e in node):
            found.append(node)
        for value in node:
            found.extend(_profile_lists(value))
    return found


class TestCapturedProfiles:
    def test_the_capture_holds_profiles_to_compare(self):
        assert _profile_entries(_captured())

    def test_every_captured_entry_has_the_fields_the_api_serves(self, make_suite):
        served = set(list_profiles(make_suite("alpha"))[0].to_dict())

        for entry in _profile_entries(_captured()):
            assert set(entry) == served, entry["name"]

    def test_every_captured_label_is_the_one_gauntlet_derives(self):
        for entry in _profile_entries(_captured()):
            derived = ProfileInfo(name=entry["name"], path=Path(entry["path"])).label
            assert entry["label"] == derived, entry["name"]

    def test_every_captured_list_is_in_the_order_the_api_serves(self):
        for entries in _profile_lists(_captured()):
            names = [entry["name"] for entry in entries]
            assert names == sorted(names, key=_order), names


def _order(name: str) -> tuple[int, str]:
    """The listing order, restated so a change to it fails this test loudly."""
    return (0 if Path(name).stem == "smoke" else 1, name)
