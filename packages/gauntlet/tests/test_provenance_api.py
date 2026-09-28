"""Who started a run, where, and in which test session, and filtering on it."""

from __future__ import annotations

import json
import time
from pathlib import Path

from gauntlet.storage import RunRow

SIGNED_IN = {"operator": "Ada", "location": "Lab 2", "session": "TID week 1"}


def _wait_for_finish(client, run_id, timeout_s=20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"passed", "failed", "aborted", "error"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def _add(client, run_id: str, *, minute: int, serial: str | None = None, **provenance: str) -> None:
    started = f"2026-01-01T00:{minute:02d}:00Z"
    client.app.state.runs_index.upsert(
        RunRow(
            run_id=run_id,
            suite="alpha",
            status="passed",
            started_at=started,
            run_dir=f"/tmp/alpha/{run_id}",
            ended_at=started,
            unit_serial=serial,
            **provenance,
        )
    )


class TestStartingARun:
    def test_the_run_carries_who_started_it_where_and_in_which_session(self, client) -> None:
        run_id = client.post("/api/runs", json={"suite": "alpha", **SIGNED_IN}).json()["run_id"]
        finished = _wait_for_finish(client, run_id)
        assert {key: finished[key] for key in SIGNED_IN} == SIGNED_IN

    def test_it_is_written_beside_the_artifacts(self, client) -> None:
        started = client.post("/api/runs", json={"suite": "alpha", **SIGNED_IN}).json()
        _wait_for_finish(client, started["run_id"])
        record = json.loads((Path(started["run_dir"]) / "run.json").read_text())
        assert {key: record[key] for key in SIGNED_IN} == SIGNED_IN

    def test_blank_values_are_recorded_as_nothing(self, client) -> None:
        started = client.post("/api/runs", json={"suite": "alpha", "operator": "  ", "location": ""}).json()
        finished = _wait_for_finish(client, started["run_id"])
        assert (finished["operator"], finished["location"], finished["session"]) == (None, None, None)


class TestFilteringRuns:
    def test_by_location_and_session(self, client) -> None:
        _add(client, "r1", minute=1, location="Lab 2", session="week 1")
        _add(client, "r2", minute=2, location="Lab 2", session="week 2")
        _add(client, "r3", minute=3, location="Lab 3", session="week 1")

        def ids(**params: str) -> list[str]:
            return [row["run_id"] for row in client.get("/api/runs", params=params).json()["runs"]]

        assert ids(location="Lab 2") == ["r2", "r1"]
        assert ids(session="week 1") == ["r3", "r1"]
        assert ids(location="Lab 2", session="week 1") == ["r1"]

    def test_a_search_looks_at_the_operator(self, client) -> None:
        _add(client, "r1", minute=1, operator="Ada")
        _add(client, "r2", minute=2, operator="Grace")
        assert [row["run_id"] for row in client.get("/api/runs", params={"q": "grac"}).json()["runs"]] == ["r2"]

    def test_the_values_on_offer_are_the_ones_runs_carry(self, client) -> None:
        _add(client, "r1", minute=1, operator="Grace", location="lab 3", session="week 1")
        _add(client, "r2", minute=2, operator="Ada", location="Lab 2", session="week 1")
        _add(client, "r3", minute=3)
        assert client.get("/api/runs/provenance").json() == {
            "operators": ["Ada", "Grace"],
            "locations": ["Lab 2", "lab 3"],
            "sessions": ["week 1"],
        }


class TestFilteringUnits:
    def test_only_units_run_there_are_listed_and_only_those_runs_count(self, client) -> None:
        _add(client, "r1", minute=1, serial="SN1", location="Lab 2")
        _add(client, "r2", minute=2, serial="SN1", location="Lab 3")
        _add(client, "r3", minute=3, serial="SN2", location="Lab 3")
        units = client.get("/api/units", params={"location": "Lab 2"}).json()["units"]
        assert [(unit["serial"], unit["run_count"], unit["last_run"]["run_id"]) for unit in units] == [("SN1", 1, "r1")]

    def test_a_unit_known_only_from_a_note_is_left_out_of_a_filtered_list(self, client) -> None:
        _add(client, "r1", minute=1, serial="SN1", session="week 1")
        client.app.state.units_index.touch("SN9")
        assert {unit["serial"] for unit in client.get("/api/units").json()["units"]} == {"SN1", "SN9"}
        assert [unit["serial"] for unit in client.get("/api/units", params={"session": "week 1"}).json()["units"]] == [
            "SN1"
        ]


class TestNotes:
    def test_a_note_records_where_and_in_which_session_it_was_written(self, client) -> None:
        _add(client, "r1", minute=1, serial="SN1")
        body = {"body": "reseated the cable", "author": "Ada", "location": "Lab 2", "session": "week 1"}
        created = client.post("/api/runs/r1/notes", json=body).json()
        assert (created["author"], created["location"], created["session"]) == ("Ada", "Lab 2", "week 1")
        unit_note = client.post("/api/units/SN1/notes", json=body).json()
        assert unit_note["location"] == "Lab 2"
        assert client.get("/api/runs/r1/notes").json()["notes"][0]["session"] == "week 1"
