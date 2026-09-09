"""Exporting a run over the API and taking one in from somewhere else."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from gauntlet.transfer import MANIFEST_NAME


@pytest.fixture
def exported(client, add_run, make_run_dir) -> bytes:
    """One finished run with a note against it, as an archive."""
    add_run("r1", run_dir=make_run_dir(), unit_serial="SN1")
    client.post("/api/runs/r1/notes", json={"body": "swapped the cable", "author": "gabe"})
    return client.get("/api/runs/r1/export").content


def manifest(archive: bytes) -> dict:
    return json.loads(zipfile.ZipFile(io.BytesIO(archive)).read(MANIFEST_NAME))


class TestExport:
    def test_offers_the_archive_under_the_run_id(self, client, add_run, make_run_dir) -> None:
        add_run("r1", run_dir=make_run_dir())

        response = client.get("/api/runs/r1/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "r1.gauntlet-run.zip" in response.headers["content-disposition"]

    def test_carries_the_run_and_its_notes(self, exported: bytes) -> None:
        body = manifest(exported)
        assert body["run"]["run_id"] == "r1"
        assert body["run"]["unit_serial"] == "SN1"
        assert [note["body"] for note in body["notes"]] == ["swapped the cable"]

    def test_carries_the_artifacts(self, exported: bytes) -> None:
        names = set(zipfile.ZipFile(io.BytesIO(exported)).namelist())
        assert {"run/verdict.json", "run/metrics.jsonl", "run/frames/0001.png"} <= names

    def test_an_unknown_run_is_404(self, client) -> None:
        assert client.get("/api/runs/nope/export").status_code == 404

    def test_a_run_still_in_flight_is_409(self, client, add_run, make_run_dir, monkeypatch) -> None:
        add_run("r1", run_dir=make_run_dir())
        monkeypatch.setattr(client.app.state.supervisor, "get", lambda _: SimpleNamespace(finished=False))

        assert client.get("/api/runs/r1/export").status_code == 409


class TestImport:
    def test_a_deleted_run_can_be_put_back(self, client, exported: bytes) -> None:
        client.delete("/api/runs/r1")
        assert client.get("/api/runs/r1").status_code == 404

        created = client.post("/api/runs/import", content=exported)
        assert created.status_code == 201
        assert created.json()["run_id"] == "r1"
        assert client.get("/api/runs/r1").json()["unit_serial"] == "SN1"

    def test_its_artifacts_are_readable_again(self, client, exported: bytes) -> None:
        client.delete("/api/runs/r1")
        client.post("/api/runs/import", content=exported)

        listed = client.get("/api/runs/r1/artifacts").json()
        assert {entry["path"] for entry in listed["artifacts"]} >= {"verdict.json", "frames/0001.png"}
        assert Path(listed["run_dir"]).is_relative_to(client.app.state.settings.runs_dir)

    def test_its_notes_come_back(self, client, exported: bytes) -> None:
        client.delete("/api/runs/r1")
        client.post("/api/runs/import", content=exported)

        assert [n["body"] for n in client.get("/api/runs/r1/notes").json()["notes"]] == ["swapped the cable"]

    def test_its_unit_is_derived_again(self, client, exported: bytes) -> None:
        client.delete("/api/runs/r1?runs=true")
        client.post("/api/runs/import", content=exported)

        assert client.get("/api/units/SN1").json()["run_count"] == 1

    def test_a_run_already_here_is_409(self, client, exported: bytes) -> None:
        assert client.post("/api/runs/import", content=exported).status_code == 409

    def test_overwrite_replaces_it(self, client, exported: bytes) -> None:
        assert client.post("/api/runs/import?overwrite=true", content=exported).status_code == 201
        assert client.get("/api/runs").json()["total"] == 1

    def test_a_suite_this_instance_does_not_have_still_arrives(self, client, add_run, make_run_dir) -> None:
        """Run history does not depend on the catalog, which is what makes a
        bench-to-laptop transfer worth having."""
        add_run("r2", run_dir=make_run_dir(), suite="not_installed_here")
        archive = client.get("/api/runs/r2/export").content
        client.delete("/api/runs/r2")

        assert client.post("/api/runs/import", content=archive).status_code == 201
        run = client.get("/api/runs/r2").json()
        assert run["suite"] == "not_installed_here"
        assert run["campaign"] is None

    def test_something_that_is_not_an_export_is_422(self, client) -> None:
        assert client.post("/api/runs/import", content=b"not a zip").status_code == 422
