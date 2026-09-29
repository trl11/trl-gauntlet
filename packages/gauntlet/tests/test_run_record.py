"""What Gauntlet writes into a run's directory so the directory alone rebuilds the run."""

from __future__ import annotations

import json
import time
from pathlib import Path

from gauntlet.storage import SUBJECT_RUN, NoteRow, NotesIndex, RunRow, RunsIndex
from gauntlet.storage.notes import read_notes_file, write_notes_file


def _wait_for_finish(client, run_id, timeout_s=20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"passed", "failed", "aborted", "error"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def _record(run_dir: str) -> dict:
    return json.loads((Path(run_dir) / "run.json").read_text())


class TestRunRecord:
    def test_a_finished_run_leaves_its_whole_row_on_disk(self, client) -> None:
        started = client.post("/api/runs", json={"suite": "alpha", "unit_serial": "SN1", "operator": "Ada"}).json()
        finished = _wait_for_finish(client, started["run_id"])
        record = _record(started["run_dir"])
        assert record["status"] == finished["status"] == "passed"
        assert (record["unit_serial"], record["operator"], record["ended_at"]) == (
            "SN1",
            "Ada",
            finished["ended_at"],
        )
        assert "run_dir" not in record

    def test_renaming_a_unit_rewrites_the_record_of_each_of_its_runs(self, client) -> None:
        started = client.post("/api/runs", json={"suite": "alpha", "unit_serial": "SN1"}).json()
        _wait_for_finish(client, started["run_id"])
        assert client.patch("/api/units/SN1", json={"serial": "SN2"}).status_code == 200
        assert _record(started["run_dir"])["unit_serial"] == "SN2"

    def test_a_run_with_no_verdict_is_rebuilt_from_its_record(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        first = RunsIndex(tmp_path / "one.db")
        first.upsert(
            RunRow(
                run_id="r1",
                suite="alpha",
                status="error",
                started_at="2026-01-01T00:00:00Z",
                run_dir=str(run_dir),
                verdict="ERROR",
                fail_reason="no verdict.json",
                target="10.0.0.2",
                unit_serial="SN1",
                profile="smoke.yaml",
            )
        )
        second = RunsIndex(tmp_path / "two.db")
        assert second.import_tree(tmp_path / "runs") == 1
        row = second.get("r1")
        assert row is not None
        assert (row.status, row.fail_reason, row.target, row.unit_serial, row.profile) == (
            "error",
            "no verdict.json",
            "10.0.0.2",
            "SN1",
            "smoke.yaml",
        )

    def test_a_record_left_in_flight_is_rebuilt_as_interrupted(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps({"run_id": "r1", "suite": "alpha", "status": "running", "started_at": "2026-01-01"})
        )
        runs = RunsIndex(tmp_path / "runs.db")
        runs.import_tree(tmp_path / "runs")
        row = runs.get("r1")
        assert row is not None
        assert (row.status, row.verdict) == ("error", "ERROR")
        assert row.fail_reason is not None and row.fail_reason.startswith("interrupted")


def _note(note_id: int, body: str, **fields: str) -> NoteRow:
    return NoteRow(
        id=note_id,
        subject_kind=SUBJECT_RUN,
        subject_id="r1",
        body=body,
        created_at=fields.pop("created_at", "2026-01-01T00:00:00Z"),
        **fields,
    )


class TestNotesFile:
    def test_notes_read_back_as_they_were_written(self, tmp_path: Path) -> None:
        notes = [
            _note(2, "second\n\n### not a heading\nstill the second", author="Grace", created_at="2026-01-02"),
            _note(1, "first", author="Ada", location="Lab 2", session="week 1"),
        ]
        write_notes_file(tmp_path, "r1", notes)
        text = (tmp_path / "notes.md").read_text()
        assert text.index("first") < text.index("second")
        read = read_notes_file(tmp_path)
        assert [(n.body, n.author, n.location, n.session) for n in read] == [
            ("first", "Ada", "Lab 2", "week 1"),
            ("second\n\n### not a heading\nstill the second", "Grace", None, None),
        ]

    def test_a_run_left_with_no_notes_has_no_file(self, tmp_path: Path) -> None:
        write_notes_file(tmp_path, "r1", [_note(1, "first")])
        write_notes_file(tmp_path, "r1", [])
        assert not (tmp_path / "notes.md").exists()

    def test_the_api_keeps_the_file_in_step_with_the_notes(self, client) -> None:
        started = client.post("/api/runs", json={"suite": "alpha"}).json()
        _wait_for_finish(client, started["run_id"])
        path = Path(started["run_dir"]) / "notes.md"
        note = client.post(f"/api/runs/{started['run_id']}/notes", json={"body": "reseated", "author": "Ada"}).json()
        assert "reseated" in path.read_text()
        client.delete(f"/api/runs/{started['run_id']}/notes/{note['id']}")
        assert not path.exists()

    def test_rebuilding_the_index_brings_the_notes_back(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text('{"passed": true}')
        write_notes_file(run_dir, "r1", [_note(1, "first", author="Ada"), _note(2, "second")])
        runs = RunsIndex(tmp_path / "runs.db")
        notes = NotesIndex(tmp_path / "runs.db")
        runs.import_tree(tmp_path / "runs", notes)
        assert [n.body for n in notes.list(SUBJECT_RUN, "r1")] == ["second", "first"]
