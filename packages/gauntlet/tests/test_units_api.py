"""Unit endpoints, driven against a real app and a real index."""

from __future__ import annotations


class TestUnits:
    def test_lists_units_derived_from_runs(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        add_run("r2", unit_serial="SN1", status="failed")
        add_run("r3", unit_serial="SN2")

        units = client.get("/api/units").json()["units"]
        first = units[0]
        assert [unit["serial"] for unit in units] == ["SN2", "SN1"]
        assert (first["run_count"], first["passed"], first["failed"]) == (1, 1, 0)
        assert first["last_run"]["run_id"] == "r3"
        assert first["note_count"] == 0

    def test_one_unit_carries_its_notes(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        client.post("/api/units/SN1/notes", json={"body": "reflowed U7"})

        body = client.get("/api/units/SN1").json()
        assert body["serial"] == "SN1"
        assert [note["body"] for note in body["notes"]] == ["reflowed U7"]

    def test_history_lists_the_runs(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        add_run("r2", unit_serial="SN2")
        add_run("r3", unit_serial="SN1")

        runs = client.get("/api/units/SN1/history").json()["runs"]
        assert [run["run_id"] for run in runs] == ["r3", "r1"]

    def test_rename_rewrites_the_run_rows(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")

        renamed = client.patch("/api/units/SN1", json={"serial": "SN2"})
        assert renamed.status_code == 200
        assert renamed.json()["serial"] == "SN2"
        assert client.get("/api/runs/r1").json()["unit_serial"] == "SN2"
        assert client.get("/api/units/SN1").status_code == 404

    def test_rename_onto_a_taken_serial_is_409(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        add_run("r2", unit_serial="SN2")
        assert client.patch("/api/units/SN1", json={"serial": "SN2"}).status_code == 409

    def test_rename_to_an_empty_serial_is_422(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        assert client.patch("/api/units/SN1", json={"serial": "  "}).status_code == 422

    def test_delete_keeps_the_runs(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        client.post("/api/units/SN1/notes", json={"body": "gone"})

        body = client.delete("/api/units/SN1").json()
        assert body == {"serial": "SN1", "deleted": True, "deleted_runs": 0}
        assert client.get("/api/runs/r1").status_code == 200
        assert client.get("/api/units/SN1").json()["notes"] == []

    def test_delete_with_runs_takes_the_runs_too(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        add_run("r2", unit_serial="SN1")
        add_run("r3", unit_serial="SN2")
        client.post("/api/units/SN1/notes", json={"body": "gone"})

        body = client.delete("/api/units/SN1", params={"runs": "true"}).json()
        assert body == {"serial": "SN1", "deleted": True, "deleted_runs": 2}
        assert client.get("/api/runs/r1").status_code == 404
        assert client.get("/api/runs/r2").status_code == 404
        assert client.get("/api/units/SN1").status_code == 404
        assert client.get("/api/runs/r3").status_code == 200
        assert [unit["serial"] for unit in client.get("/api/units").json()["units"]] == ["SN2"]

    def test_delete_with_runs_removes_the_run_directories(self, client, add_run) -> None:
        run_dir = client.app.state.settings.runs_dir / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text("{}")
        add_run("r1", run_dir=run_dir, unit_serial="SN1")

        client.delete("/api/units/SN1", params={"runs": "true"})
        assert not run_dir.exists()

    def test_delete_with_runs_is_refused_while_one_is_in_flight(self, client, add_run, monkeypatch) -> None:
        add_run("r1", unit_serial="SN1")
        add_run("r2", unit_serial="SN1")

        class _Live:
            finished = False

        monkeypatch.setattr(client.app.state.supervisor, "get", lambda run_id: _Live() if run_id == "r2" else None)

        assert client.delete("/api/units/SN1", params={"runs": "true"}).status_code == 409
        assert client.get("/api/runs/r1").status_code == 200
        assert client.get("/api/units/SN1").status_code == 200

    def test_delete_without_runs_leaves_the_unit_derivable(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")

        client.delete("/api/units/SN1")
        assert client.get("/api/units/SN1").json()["run_count"] == 1

    def test_unknown_unit_is_404(self, client) -> None:
        assert client.get("/api/units/nope").status_code == 404
        assert client.get("/api/units/nope/history").status_code == 404
        assert client.get("/api/units/nope/notes").status_code == 404
        assert client.delete("/api/units/nope").status_code == 404
        assert client.patch("/api/units/nope", json={"serial": "SN9"}).status_code == 404
        assert client.post("/api/units/nope/notes", json={"body": "hi"}).status_code == 404
        assert client.delete("/api/units/nope/notes/1").status_code == 404

    def test_no_units_lists_nothing(self, client) -> None:
        assert client.get("/api/units").json() == {"units": []}

    def test_renaming_to_the_same_serial_is_accepted(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        assert client.patch("/api/units/SN1", json={"serial": "SN1"}).json()["serial"] == "SN1"

    def test_a_rename_body_without_a_serial_is_422(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        assert client.patch("/api/units/SN1", json={}).status_code == 422

    def test_an_unexpected_rename_body_key_is_422(self, client, add_run) -> None:
        add_run("r1", unit_serial="SN1")
        assert client.patch("/api/units/SN1", json={"serial": "SN2", "note": "x"}).status_code == 422

    def test_history_pages(self, client, add_run) -> None:
        for index in range(3):
            add_run(f"r{index}", unit_serial="SN1")
        body = client.get("/api/units/SN1/history", params={"limit": 2, "offset": 1}).json()
        assert [run["run_id"] for run in body["runs"]] == ["r1", "r0"]
        assert body["total"] == 3
