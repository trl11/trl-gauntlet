"""Note endpoints, which behave the same on a run and on a unit."""

from __future__ import annotations

import pytest


@pytest.fixture
def subjects(client, add_run):
    """One run and one unit, each addressable by its notes URL."""
    add_run("r1", unit_serial="SN1")
    return {"run": "/api/runs/r1/notes", "unit": "/api/units/SN1/notes"}


@pytest.mark.parametrize("kind", ["run", "unit"])
class TestNotes:
    def test_create_then_list(self, client, subjects, kind) -> None:
        created = client.post(subjects[kind], json={"body": "swapped the cable", "author": "gabe"})
        assert created.status_code == 201
        note = created.json()
        assert note["body"] == "swapped the cable"
        assert note["author"] == "gabe"
        assert note["created_at"].endswith("Z")
        assert client.get(subjects[kind]).json()["notes"] == [note]

    def test_author_is_optional(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": "no name"}).json()["author"] is None

    def test_newest_first(self, client, subjects, kind) -> None:
        client.post(subjects[kind], json={"body": "first"})
        client.post(subjects[kind], json={"body": "second"})
        assert [n["body"] for n in client.get(subjects[kind]).json()["notes"]] == ["second", "first"]

    def test_empty_body_is_422(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": "   "}).status_code == 422

    def test_delete(self, client, subjects, kind) -> None:
        note_id = client.post(subjects[kind], json={"body": "wrong"}).json()["id"]
        assert client.delete(f"{subjects[kind]}/{note_id}").json() == {"id": str(note_id), "deleted": True}
        assert client.get(subjects[kind]).json()["notes"] == []

    def test_deleting_an_unknown_note_is_404(self, client, subjects, kind) -> None:
        assert client.delete(f"{subjects[kind]}/999").status_code == 404

    def test_a_body_key_is_required(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"author": "gabe"}).status_code == 422

    def test_an_unexpected_key_is_422(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": "hi", "colour": "red"}).status_code == 422

    def test_a_non_string_body_is_422(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": ["hi"]}).status_code == 422

    def test_a_blank_author_is_stored_as_none(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": "hi", "author": "   "}).json()["author"] is None

    def test_the_body_is_trimmed(self, client, subjects, kind) -> None:
        assert client.post(subjects[kind], json={"body": "  spaced  "}).json()["body"] == "spaced"

    def test_a_non_numeric_note_id_is_422(self, client, subjects, kind) -> None:
        assert client.delete(f"{subjects[kind]}/abc").status_code == 422


class TestNotesAreNotShared:
    def test_a_run_note_is_not_a_unit_note(self, client, subjects) -> None:
        note_id = client.post(subjects["run"], json={"body": "on the run"}).json()["id"]
        assert client.get(subjects["unit"]).json()["notes"] == []
        assert client.delete(f"{subjects['unit']}/{note_id}").status_code == 404


class TestUnknownSubjects:
    def test_notes_on_an_unknown_run_are_404(self, client) -> None:
        assert client.get("/api/runs/nope/notes").status_code == 404
        assert client.post("/api/runs/nope/notes", json={"body": "hi"}).status_code == 404
        assert client.delete("/api/runs/nope/notes/1").status_code == 404


class TestNoteCounts:
    """What a listing says about the notes a run carries."""

    def test_a_run_without_notes_counts_none(self, client, subjects) -> None:
        assert client.get("/api/runs").json()["runs"][0]["note_count"] == 0

    def test_a_listing_counts_each_run_its_own_notes(self, client, subjects, add_run) -> None:
        add_run("r2")
        client.post(subjects["run"], json={"body": "one"})
        client.post(subjects["run"], json={"body": "two"})
        counts = {row["run_id"]: row["note_count"] for row in client.get("/api/runs").json()["runs"]}
        assert counts == {"r1": 2, "r2": 0}

    def test_one_run_counts_its_notes(self, client, subjects) -> None:
        client.post(subjects["run"], json={"body": "one"})
        assert client.get("/api/runs/r1").json()["note_count"] == 1

    def test_a_unit_note_is_not_counted_on_the_run(self, client, subjects) -> None:
        client.post(subjects["unit"], json={"body": "on the unit"})
        assert client.get("/api/runs/r1").json()["note_count"] == 0

    def test_a_deleted_note_stops_counting(self, client, subjects) -> None:
        note_id = client.post(subjects["run"], json={"body": "one"}).json()["id"]
        client.delete(f"{subjects['run']}/{note_id}")
        assert client.get("/api/runs/r1").json()["note_count"] == 0

    def test_a_unit_history_counts_them_too(self, client, subjects) -> None:
        client.post(subjects["run"], json={"body": "one"})
        history = client.get("/api/units/SN1/history").json()
        assert [row["note_count"] for row in history["runs"]] == [1]


class TestFilteringByNotes:
    def test_only_runs_with_notes_are_listed(self, client, subjects, add_run) -> None:
        add_run("r2")
        client.post(subjects["run"], json={"body": "one"})
        listed = client.get("/api/runs", params={"has_notes": "true"}).json()
        assert [row["run_id"] for row in listed["runs"]] == ["r1"]
        assert listed["total"] == 1

    def test_without_the_filter_every_run_is_listed(self, client, subjects, add_run) -> None:
        add_run("r2")
        client.post(subjects["run"], json={"body": "one"})
        assert client.get("/api/runs").json()["total"] == 2

    def test_the_filter_holds_alongside_another(self, client, subjects, add_run) -> None:
        add_run("r2", suite="beta")
        client.post(subjects["run"], json={"body": "one"})
        client.post("/api/runs/r2/notes", json={"body": "two"})
        listed = client.get("/api/runs", params={"has_notes": "true", "suite": "beta"}).json()
        assert [row["run_id"] for row in listed["runs"]] == ["r2"]

    def test_a_unit_note_does_not_bring_its_run_in(self, client, subjects) -> None:
        client.post(subjects["unit"], json={"body": "on the unit"})
        assert client.get("/api/runs", params={"has_notes": "true"}).json()["runs"] == []
