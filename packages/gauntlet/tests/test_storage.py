"""Notes and units, driven against a real SQLite file."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from gauntlet.storage import (
    SUBJECT_RUN,
    SUBJECT_UNIT,
    NotesIndex,
    RunFilters,
    RunRow,
    RunsIndex,
    UnitConflict,
    UnitsIndex,
)


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return tmp_path / "runs.sqlite"


@pytest.fixture
def notes(index_path: Path) -> NotesIndex:
    return NotesIndex(index_path)


@pytest.fixture
def runs(index_path: Path) -> RunsIndex:
    return RunsIndex(index_path)


@pytest.fixture
def units(index_path: Path, notes: NotesIndex) -> UnitsIndex:
    return UnitsIndex(index_path, notes)


def _run(run_id: str, *, minute: int, serial: str | None, status: str = "passed") -> RunRow:
    started = f"2026-01-01T00:{minute:02d}:00Z"
    return RunRow(
        run_id=run_id,
        suite="alpha",
        status=status,
        started_at=started,
        run_dir=f"/runs/{run_id}",
        ended_at=started,
        unit_serial=serial,
    )


class TestRunsIndex:
    def test_delete_removes_the_row_and_returns_it(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        removed = runs.delete("r1")
        assert removed is not None
        assert removed.run_id == "r1"
        assert runs.get("r1") is None

    def test_deleting_an_unknown_run_is_none(self, runs: RunsIndex) -> None:
        assert runs.delete("nope") is None


class TestNotesIndex:
    def test_add_then_list(self, notes: NotesIndex) -> None:
        added = notes.add(SUBJECT_RUN, "r1", "looks fine", "gabe")
        assert added.id > 0
        assert [note.to_dict() for note in notes.list(SUBJECT_RUN, "r1")] == [added.to_dict()]

    def test_subjects_do_not_mix(self, notes: NotesIndex) -> None:
        notes.add(SUBJECT_RUN, "same", "on the run")
        notes.add(SUBJECT_UNIT, "same", "on the unit")
        assert [note.body for note in notes.list(SUBJECT_UNIT, "same")] == ["on the unit"]

    def test_newest_first(self, notes: NotesIndex) -> None:
        notes.add(SUBJECT_UNIT, "SN1", "first")
        notes.add(SUBJECT_UNIT, "SN1", "second")
        assert [note.body for note in notes.list(SUBJECT_UNIT, "SN1")] == ["second", "first"]

    def test_counts_by_subject(self, notes: NotesIndex) -> None:
        notes.add(SUBJECT_UNIT, "SN1", "a")
        notes.add(SUBJECT_UNIT, "SN1", "b")
        notes.add(SUBJECT_UNIT, "SN2", "c")
        assert notes.counts(SUBJECT_UNIT) == {"SN1": 2, "SN2": 1}
        assert notes.count(SUBJECT_UNIT, "SN1") == 2

    def test_delete(self, notes: NotesIndex) -> None:
        note = notes.add(SUBJECT_RUN, "r1", "wrong")
        assert notes.delete(note.id) is True
        assert notes.delete(note.id) is False
        assert notes.list(SUBJECT_RUN, "r1") == []

    def test_delete_subject(self, notes: NotesIndex) -> None:
        notes.add(SUBJECT_UNIT, "SN1", "a")
        notes.add(SUBJECT_UNIT, "SN1", "b")
        assert notes.delete_subject(SUBJECT_UNIT, "SN1") == 2
        assert notes.list(SUBJECT_UNIT, "SN1") == []

    def test_rename_subject(self, notes: NotesIndex) -> None:
        notes.add(SUBJECT_UNIT, "SN1", "a")
        assert notes.rename_subject(SUBJECT_UNIT, "SN1", "SN2") == 1
        assert [note.body for note in notes.list(SUBJECT_UNIT, "SN2")] == ["a"]

    def test_unknown_note_is_none(self, notes: NotesIndex) -> None:
        assert notes.get(404) is None

    def test_deleting_a_subject_with_no_notes_removes_nothing(self, notes: NotesIndex) -> None:
        assert notes.delete_subject(SUBJECT_UNIT, "SN9") == 0

    def test_renaming_a_subject_with_no_notes_moves_nothing(self, notes: NotesIndex) -> None:
        assert notes.rename_subject(SUBJECT_UNIT, "SN9", "SN8") == 0

    def test_counts_of_a_kind_with_no_notes_is_empty(self, notes: NotesIndex) -> None:
        assert notes.counts(SUBJECT_RUN) == {}
        assert notes.count(SUBJECT_RUN, "r1") == 0

    def test_the_index_can_be_closed(self, index_path: Path) -> None:
        index = NotesIndex(index_path)
        index.add(SUBJECT_RUN, "r1", "written before closing")
        index.close()
        with pytest.raises(sqlite3.ProgrammingError):
            index.list(SUBJECT_RUN, "r1")

    def test_it_creates_the_directory_it_is_given(self, tmp_path: Path) -> None:
        NotesIndex(tmp_path / "nested" / "deeper" / "notes.sqlite")
        assert (tmp_path / "nested" / "deeper").is_dir()

    def test_concurrent_writers_all_get_their_own_id(self, notes: NotesIndex) -> None:
        """Every thread shares one connection, so the lock has to serialise them."""
        written: list[int] = []
        lock = threading.Lock()

        def write(index: int) -> None:
            note = notes.add(SUBJECT_UNIT, "SN1", f"note {index}")
            with lock:
                written.append(note.id)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(set(written)) == 20
        assert notes.count(SUBJECT_UNIT, "SN1") == 20
        assert {note.id for note in notes.list(SUBJECT_UNIT, "SN1")} == set(written)


class TestUnitsIndex:
    def test_aggregates_run_rows(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        runs.upsert(_run("r2", minute=5, serial="SN1", status="failed"))
        runs.upsert(_run("r3", minute=1, serial="SN2"))

        unit = units.get("SN1")
        assert unit is not None
        assert (unit.run_count, unit.passed, unit.failed) == (2, 1, 1)
        assert unit.first_seen == "2026-01-01T00:00:00Z"
        assert unit.last_seen == "2026-01-01T00:05:00Z"
        assert unit.last_run == {
            "run_id": "r2",
            "suite": "alpha",
            "status": "failed",
            "ended_at": "2026-01-01T00:05:00Z",
        }
        assert [row.serial for row in units.list()] == ["SN1", "SN2"]

    def test_runs_without_a_serial_make_no_unit(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial=None))
        runs.upsert(_run("r2", minute=1, serial=""))
        assert units.list() == []

    def test_note_count_comes_from_the_notes(self, notes: NotesIndex, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        notes.add(SUBJECT_UNIT, "SN1", "swapped the harness")
        assert units.list()[0].note_count == 1

    def test_rename_rewrites_run_rows(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        renamed = units.rename("SN1", "SN2")
        moved = runs.get("r1")

        assert renamed is not None
        assert renamed.serial == "SN2"
        assert moved is not None
        assert moved.unit_serial == "SN2"
        assert units.get("SN1") is None

    def test_rename_moves_the_notes(self, notes: NotesIndex, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        notes.add(SUBJECT_UNIT, "SN1", "keep me")
        units.rename("SN1", "SN2")
        assert [note.body for note in notes.list(SUBJECT_UNIT, "SN2")] == ["keep me"]

    def test_rename_onto_a_taken_serial_conflicts(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        runs.upsert(_run("r2", minute=1, serial="SN2"))
        with pytest.raises(UnitConflict):
            units.rename("SN1", "SN2")

    def test_rename_of_an_unknown_unit_is_none(self, units: UnitsIndex) -> None:
        assert units.rename("nope", "SN9") is None

    def test_delete_keeps_the_run_rows(self, notes: NotesIndex, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        notes.add(SUBJECT_UNIT, "SN1", "gone with it")
        units.touch("SN1")

        assert units.delete("SN1") is True
        assert notes.list(SUBJECT_UNIT, "SN1") == []
        assert runs.get("r1") is not None
        # The runs still name the serial, so the unit is derived again.
        unit = units.get("SN1")
        assert unit is not None
        assert unit.note_count == 0

    def test_touch_keeps_a_unit_without_runs(self, units: UnitsIndex) -> None:
        units.touch("SN9")
        unit = units.get("SN9")
        assert unit is not None
        assert (unit.run_count, unit.last_run) == (0, None)
        assert [row.serial for row in units.list()] == ["SN9"]

    def test_unknown_unit_is_none(self, units: UnitsIndex) -> None:
        assert units.get("nope") is None

    def test_renaming_a_unit_to_its_own_serial_changes_nothing(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        renamed = units.rename("SN1", "SN1")
        assert renamed is not None
        assert renamed.serial == "SN1"
        assert renamed.run_count == 1

    def test_renaming_onto_a_serial_known_only_from_metadata_conflicts(
        self, runs: RunsIndex, units: UnitsIndex
    ) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        units.touch("SN2")
        with pytest.raises(UnitConflict):
            units.rename("SN1", "SN2")

    def test_renaming_a_unit_that_has_no_runs(self, units: UnitsIndex) -> None:
        units.touch("SN1")
        renamed = units.rename("SN1", "SN2")
        assert renamed is not None
        assert (renamed.serial, renamed.run_count) == ("SN2", 0)
        assert units.get("SN1") is None

    def test_deleting_a_unit_that_was_never_touched_reports_nothing_removed(self, units: UnitsIndex) -> None:
        assert units.delete("SN9") is False

    def test_touch_is_repeatable(self, units: UnitsIndex) -> None:
        units.touch("SN1")
        units.touch("SN1")
        assert [row.serial for row in units.list()] == ["SN1"]

    def test_a_unit_with_metadata_and_runs_is_listed_once(self, runs: RunsIndex, units: UnitsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        units.touch("SN1")
        listed = units.list()
        assert [row.serial for row in listed] == ["SN1"]
        assert listed[0].run_count == 1

    def test_the_index_can_be_closed(self, index_path: Path, notes: NotesIndex) -> None:
        index = UnitsIndex(index_path, notes)
        index.touch("SN1")
        index.close()
        with pytest.raises(sqlite3.ProgrammingError):
            index.list()


class TestRunFilters:
    def test_no_filters_counts_everything(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial=None))
        runs.upsert(_run("r2", minute=1, serial=None))
        assert runs.count() == 2

    def test_status_matches_any_of_the_listed_values(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial=None, status="passed"))
        runs.upsert(_run("r2", minute=1, serial=None, status="failed"))
        runs.upsert(_run("r3", minute=2, serial=None, status="error"))
        filters = RunFilters(status=("failed", "error"))
        assert {row.run_id for row in runs.list(filters)} == {"r2", "r3"}
        assert runs.count(filters) == 2

    def test_before_includes_the_whole_named_day(self, runs: RunsIndex) -> None:
        runs.upsert(_run("late", minute=59, serial=None))
        assert [row.run_id for row in runs.list(RunFilters(before="2026-01-01"))] == ["late"]

    def test_ascending_sort_by_a_named_column(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=1, serial=None))
        runs.upsert(_run("r2", minute=0, serial=None))
        ordered = runs.list(sort="run_id", descending=False)
        assert [row.run_id for row in ordered] == ["r1", "r2"]

    def test_an_unknown_sort_column_is_ignored(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial=None))
        runs.upsert(_run("r2", minute=1, serial=None))
        assert [row.run_id for row in runs.list(sort="'; DROP TABLE runs; --")] == ["r2", "r1"]

    def test_suite_and_serial_narrow_together(self, runs: RunsIndex) -> None:
        wanted = _run("mine", minute=0, serial="SN1")
        other_suite = _run("other-suite", minute=1, serial="SN1")
        other_suite.suite = "beta"
        runs.upsert(wanted)
        runs.upsert(other_suite)
        runs.upsert(_run("other-serial", minute=2, serial="SN2"))

        filters = RunFilters(suite="alpha", unit_serial="SN1")
        assert [row.run_id for row in runs.list(filters)] == ["mine"]
        assert runs.count(filters) == 1

    def test_after_excludes_what_came_before_it(self, runs: RunsIndex) -> None:
        runs.upsert(_run("early", minute=0, serial=None))
        runs.upsert(_run("late", minute=30, serial=None))
        assert [row.run_id for row in runs.list(RunFilters(after="2026-01-01T00:15:00Z"))] == ["late"]

    def test_a_page_can_be_offset(self, runs: RunsIndex) -> None:
        for minute in range(4):
            runs.upsert(_run(f"r{minute}", minute=minute, serial=None))
        assert [row.run_id for row in runs.list(limit=2, offset=1)] == ["r2", "r1"]

    def test_upsert_replaces_the_earlier_row(self, runs: RunsIndex) -> None:
        runs.upsert(_run("r1", minute=0, serial="SN1"))
        runs.upsert(_run("r1", minute=0, serial="SN2", status="failed"))
        row = runs.get("r1")
        assert row is not None
        assert (row.unit_serial, row.status) == ("SN2", "failed")
        assert runs.count() == 1

    def test_an_unknown_run_is_none(self, runs: RunsIndex) -> None:
        assert runs.get("nope") is None


class TestReconcileStale:
    def test_an_in_flight_run_from_a_previous_session_becomes_an_error(self, runs: RunsIndex) -> None:
        for index, status in enumerate(("starting", "running", "stopping", "aborting")):
            runs.upsert(_run(f"r{index}", minute=index, serial=None, status=status))
        runs.upsert(_run("finished", minute=9, serial=None, status="passed"))

        assert runs.reconcile_stale() == 4
        assert {row.status for row in runs.list() if row.run_id != "finished"} == {"error"}
        rescued = runs.get("r0")
        assert rescued is not None
        assert rescued.verdict == "ERROR"
        assert "interrupted" in (rescued.fail_reason or "")

    def test_a_finished_run_is_left_alone(self, runs: RunsIndex) -> None:
        runs.upsert(_run("done", minute=0, serial=None, status="passed"))
        assert runs.reconcile_stale() == 0
        row = runs.get("done")
        assert row is not None
        assert row.status == "passed"


class TestImportTree:
    def test_a_directory_that_does_not_exist_imports_nothing(self, runs: RunsIndex, tmp_path: Path) -> None:
        assert runs.import_tree(tmp_path / "absent") == 0

    def test_it_indexes_a_run_found_on_disk(self, runs: RunsIndex, tmp_path: Path) -> None:
        _write_run(
            tmp_path / "alpha" / "r1",
            verdict={
                "passed": True,
                "reason": "",
                "duration_s": 4.5,
                "started_at_utc": "2026-02-01T00:00:00Z",
                "ended_at_utc": "2026-02-01T00:00:04Z",
            },
            manifest={"suite": "alpha", "profile_path": "/suites/alpha/profiles/quick.yaml", "unit_serial": "SN1"},
        )

        assert runs.import_tree(tmp_path) == 1
        row = runs.get("r1")
        assert row is not None
        assert (row.suite, row.status, row.verdict) == ("alpha", "passed", "PASS")
        assert (row.profile, row.unit_serial, row.duration_s) == ("quick.yaml", "SN1", 4.5)

    def test_a_failed_and_an_aborted_run_are_told_apart(self, runs: RunsIndex, tmp_path: Path) -> None:
        _write_run(tmp_path / "alpha" / "failed", verdict={"passed": False, "reason": "rail sagged"})
        _write_run(tmp_path / "alpha" / "stopped", verdict={"passed": False, "reason": "asked", "aborted": True})

        runs.import_tree(tmp_path)
        assert [row.status for row in runs.list(sort="run_id", descending=False)] == ["failed", "aborted"]

    def test_a_run_already_indexed_is_not_imported_again(self, runs: RunsIndex, tmp_path: Path) -> None:
        _write_run(tmp_path / "alpha" / "r1", verdict={"passed": True, "reason": ""})
        assert runs.import_tree(tmp_path) == 1
        assert runs.import_tree(tmp_path) == 0

    def test_the_suite_falls_back_to_the_parent_directory(self, runs: RunsIndex, tmp_path: Path) -> None:
        _write_run(tmp_path / "beta" / "r1", verdict={"passed": True, "reason": ""}, manifest=None)
        runs.import_tree(tmp_path)
        row = runs.get("r1")
        assert row is not None
        assert row.suite == "beta"

    def test_an_unreadable_verdict_still_indexes_the_run_as_failed(self, runs: RunsIndex, tmp_path: Path) -> None:
        run_dir = tmp_path / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text("{ truncated")

        assert runs.import_tree(tmp_path) == 1
        row = runs.get("r1")
        assert row is not None
        assert (row.status, row.verdict, row.profile) == ("failed", "FAIL", None)


def _write_run(run_dir: Path, *, verdict: dict, manifest: dict | None = None) -> None:
    """A run directory as a suite leaves it behind."""
    run_dir.mkdir(parents=True)
    (run_dir / "verdict.json").write_text(json.dumps(verdict))
    if manifest is not None:
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
