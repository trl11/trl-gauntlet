"""Moving one run out of an instance and into another."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from gauntlet.storage import SUBJECT_RUN, NotesIndex, RunRow, RunsIndex
from gauntlet.transfer import MANIFEST_NAME, TransferError, archive_name, export_run, import_run, read_export


def make_row(run_dir: Path, **overrides) -> RunRow:
    fields = {
        "run_id": "r1",
        "suite": "alpha",
        "status": "passed",
        "started_at": "2026-01-01T00:00:00Z",
        "run_dir": str(run_dir),
        "ended_at": "2026-01-01T00:01:00Z",
        "duration_s": 60.0,
        "verdict": "PASS",
        "profile": "smoke.yaml",
        "unit_serial": "SN1",
    }
    fields.update(overrides)
    return RunRow(**fields)


@pytest.fixture
def elsewhere(tmp_path: Path):
    """A second instance: its own database and its own runs directory."""
    data = tmp_path / "elsewhere"
    runs = RunsIndex(data / "runs.db")
    notes = NotesIndex(data / "runs.db")
    return SimpleNamespace(runs=runs, notes=notes, runs_dir=data / "runs")


class TestRoundTrip:
    def test_the_row_arrives_intact(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        row = make_row(make_run_dir())
        export_run(row, [], tmp_path / "r1.zip")

        imported = import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert imported.run_id == "r1"
        assert imported.status == "passed"
        assert imported.duration_s == 60.0
        assert imported.unit_serial == "SN1"
        assert elsewhere.runs.get("r1") is not None

    def test_the_run_directory_arrives_whole(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        export_run(make_row(make_run_dir()), [], tmp_path / "r1.zip")

        imported = import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        landed = Path(imported.run_dir)
        assert landed == elsewhere.runs_dir / "alpha" / "r1"
        assert json.loads((landed / "verdict.json").read_text())["passed"] is True
        assert (landed / "metrics.jsonl").read_text().startswith('{"kind":"iteration"')
        assert (landed / "frames" / "0001.png").read_bytes() == b"\x89PNG\r\n\x1a\n"

    def test_the_exporting_machines_path_does_not_travel(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        export_run(make_row(make_run_dir()), [], tmp_path / "r1.zip")

        manifest = json.loads(zipfile.ZipFile(tmp_path / "r1.zip").read(MANIFEST_NAME))
        assert "run_dir" not in manifest["run"]

    def test_a_run_with_no_verdict_still_travels(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        """Disk alone cannot rebuild one, which is why the row is in the archive."""
        run_dir = make_run_dir(verdict=None)
        row = make_row(run_dir, status="error", verdict="ERROR", fail_reason="interrupted")
        export_run(row, [], tmp_path / "r1.zip")

        imported = import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert imported.status == "error"
        assert imported.fail_reason == "interrupted"

    def test_a_run_whose_directory_is_gone_exports_as_its_row(self, elsewhere, tmp_path: Path) -> None:
        export_run(make_row(tmp_path / "not-here"), [], tmp_path / "r1.zip")

        imported = import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert imported.run_id == "r1"
        assert list(Path(imported.run_dir).iterdir()) == []

    def test_an_import_replaces_the_artifacts_rather_than_joining_them(
        self, make_run_dir, elsewhere, tmp_path: Path
    ) -> None:
        export_run(make_row(make_run_dir()), [], tmp_path / "r1.zip")
        landed = elsewhere.runs_dir / "alpha" / "r1"
        landed.mkdir(parents=True)
        (landed / "stale.log").write_text("from an earlier import")

        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert not (landed / "stale.log").exists()
        assert (landed / "verdict.json").is_file()

    def test_importing_twice_leaves_one_run(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        export_run(make_row(make_run_dir()), [], tmp_path / "r1.zip")

        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert elsewhere.runs.count() == 1


class TestNotes:
    def test_notes_travel_with_the_run(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        here = NotesIndex(tmp_path / "here.db")
        here.add(SUBJECT_RUN, "r1", "swapped the cable", "gabe", created_at="2026-01-01T00:02:00Z")
        here.add(SUBJECT_RUN, "r1", "reran it", None, created_at="2026-01-01T00:03:00Z")
        export_run(make_row(make_run_dir()), here.list(SUBJECT_RUN, "r1"), tmp_path / "r1.zip")

        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        landed = elsewhere.notes.list(SUBJECT_RUN, "r1")
        assert [n.body for n in landed] == ["reran it", "swapped the cable"]
        assert [n.author for n in landed] == [None, "gabe"]

    def test_a_note_keeps_the_time_it_was_written(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        here = NotesIndex(tmp_path / "here.db")
        here.add(SUBJECT_RUN, "r1", "swapped the cable", created_at="2026-01-01T00:02:00Z")
        export_run(make_row(make_run_dir()), here.list(SUBJECT_RUN, "r1"), tmp_path / "r1.zip")

        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert elsewhere.notes.list(SUBJECT_RUN, "r1")[0].created_at == "2026-01-01T00:02:00Z"

    def test_importing_twice_does_not_double_the_notes(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        here = NotesIndex(tmp_path / "here.db")
        here.add(SUBJECT_RUN, "r1", "swapped the cable")
        export_run(make_row(make_run_dir()), here.list(SUBJECT_RUN, "r1"), tmp_path / "r1.zip")

        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert elsewhere.notes.count(SUBJECT_RUN, "r1") == 1


class TestRefusals:
    def test_a_member_escaping_the_run_directory_is_refused(self, make_run_dir, elsewhere, tmp_path: Path) -> None:
        export_run(make_row(make_run_dir()), [], tmp_path / "r1.zip")
        with zipfile.ZipFile(tmp_path / "r1.zip", "a") as archive:
            archive.writestr("run/../../escaped.txt", "gotcha")

        landed = elsewhere.runs_dir / "alpha" / "r1"
        landed.mkdir(parents=True)
        (landed / "verdict.json").write_text('{"passed": true, "reason": ""}')

        with pytest.raises(TransferError, match="escapes"):
            import_run(tmp_path / "r1.zip", elsewhere.runs_dir, elsewhere.runs, elsewhere.notes)
        assert not (tmp_path / "escaped.txt").exists()
        assert (landed / "verdict.json").is_file()

    def test_a_run_id_that_is_not_a_directory_name_is_refused(self, tmp_path: Path) -> None:
        """The run id becomes a directory under the runs directory."""
        with zipfile.ZipFile(tmp_path / "sneaky.zip", "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({"apiVersion": 1, "run": {"run_id": "..", "suite": "alpha"}}))

        with pytest.raises(TransferError, match="run_id"):
            read_export(tmp_path / "sneaky.zip")

    def test_a_suite_that_is_not_a_directory_name_is_refused(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "sneaky.zip", "w") as archive:
            manifest = {"apiVersion": 1, "run": {"run_id": "r1", "suite": "../../etc"}}
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))

        with pytest.raises(TransferError, match="suite"):
            read_export(tmp_path / "sneaky.zip")

    def test_an_archive_with_no_manifest_is_refused(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "plain.zip", "w") as archive:
            archive.writestr("run/verdict.json", "{}")

        with pytest.raises(TransferError, match=MANIFEST_NAME):
            read_export(tmp_path / "plain.zip")

    def test_something_that_is_not_a_zip_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("this is not an archive")

        with pytest.raises(TransferError, match="zip"):
            read_export(tmp_path / "notes.txt")

    def test_a_later_export_version_is_refused(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "future.zip", "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({"apiVersion": 99, "run": {"run_id": "r1", "suite": "a"}}))

        with pytest.raises(TransferError, match="apiVersion"):
            read_export(tmp_path / "future.zip")

    def test_a_manifest_naming_no_run_is_refused(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "empty.zip", "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({"apiVersion": 1, "run": {}}))

        with pytest.raises(TransferError, match="names no run"):
            read_export(tmp_path / "empty.zip")


class TestArchiveName:
    def test_names_the_file_after_the_run(self) -> None:
        assert archive_name("2026-01-01T000000Z-ab12") == "2026-01-01T000000Z-ab12.gauntlet-run.zip"
