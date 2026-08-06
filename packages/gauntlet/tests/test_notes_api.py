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
