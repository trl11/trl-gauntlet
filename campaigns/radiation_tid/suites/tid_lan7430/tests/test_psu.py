"""Reading the bench supply, and carrying on without one."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from suite.psu import PsuReader

READING = {
    "current": 0.412,
    "current_limit": 2.0,
    "output_enabled": True,
    "power": 2.06,
    "voltage": 5.001,
    "voltage_setpoint": 5.0,
}


def _answer_with(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Make the capability answer ``payload``."""

    class Reply(io.StringIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: Reply(json.dumps(payload)))


def _refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the capability unreachable."""

    def fail(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fail)


def test_a_reading_carries_only_what_changes_tick_to_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_with(monkeypatch, READING)

    reading = PsuReader("http://host/api/capabilities/psu").read()

    assert reading == {"current": 0.412, "power": 2.06, "voltage": 5.001}


def test_a_supply_that_is_unreachable_reads_as_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse(monkeypatch)

    assert PsuReader("http://host/api/capabilities/psu").read() is None


def test_values_the_supply_reports_as_null_are_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_with(monkeypatch, {"current": None, "power": None, "voltage": None})

    assert PsuReader("http://host/api/capabilities/psu").read() == {}


def test_discovery_finds_a_supply_the_bench_has(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer_with(monkeypatch, READING)

    reader = PsuReader.discover("http://host/api", "psu")

    assert reader is not None


def test_discovery_answers_nothing_on_a_bench_with_no_supply(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse(monkeypatch)

    assert PsuReader.discover("http://host/api", "psu") is None


def test_discovery_answers_nothing_when_the_run_has_no_api(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse(monkeypatch)

    assert PsuReader.discover(None, "psu") is None


def test_the_url_is_built_the_way_a_grant_would_have_built_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Reply(io.StringIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    def capture(request: Any, **_kwargs: object) -> Reply:
        seen.append(request.full_url)
        return Reply(json.dumps(READING))

    monkeypatch.setattr(urllib.request, "urlopen", capture)

    PsuReader.discover("http://127.0.0.1:7100/api/", "psu")

    assert seen[0] == "http://127.0.0.1:7100/api/capabilities/psu"
