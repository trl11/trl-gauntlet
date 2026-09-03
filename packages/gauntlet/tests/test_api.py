"""API behaviour, driven against real suites on disk."""

from __future__ import annotations

import subprocess
import time

from gauntlet.api import system


def _wait_for_finish(client, run_id, timeout_s=20.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"passed", "failed", "aborted", "error"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


class TestSystem:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_system_info_reports_the_contract(self, client):
        assert client.get("/api/system/info").json()["contract_version"] == 1

    def test_there_is_no_separate_version_endpoint(self, client):
        assert client.get("/api/version").status_code == 404

    def test_instruments_include_the_mocks(self, client):
        names = {i["name"] for i in client.get("/api/instruments").json()["instruments"]}
        assert {"psu", "daq", "chamber"} <= names


class TestPower:
    """`POST /api/system/power`, which takes the host down.

    Every test replaces `subprocess.run`, so nothing here can reach logind:
    a test that really powered the machine off would take the suite with it.
    """

    def _spy(self, monkeypatch, returncode=0, stderr=""):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

        monkeypatch.setattr(system.subprocess, "run", fake_run)
        monkeypatch.setattr(system.shutil, "which", lambda _name: "/bin/systemctl")
        return calls

    def test_poweroff_asks_systemctl(self, client, monkeypatch):
        calls = self._spy(monkeypatch)
        body = client.post("/api/system/power", json={"action": "poweroff"})
        assert body.status_code == 200
        assert body.json() == {"action": "poweroff", "status": "accepted"}
        assert calls == [["/bin/systemctl", "poweroff"]]

    def test_reboot_asks_systemctl(self, client, monkeypatch):
        calls = self._spy(monkeypatch)
        assert client.post("/api/system/power", json={"action": "reboot"}).status_code == 200
        assert calls == [["/bin/systemctl", "reboot"]]

    def test_an_unknown_action_is_refused(self, client, monkeypatch):
        calls = self._spy(monkeypatch)
        assert client.post("/api/system/power", json={"action": "halt"}).status_code == 422
        assert calls == []

    def test_a_run_in_flight_holds_the_host_up(self, client, monkeypatch):
        """The one thing on a bench that cannot resume from where it left off."""
        calls = self._spy(monkeypatch)

        class _Live:
            finished = False
            run_id = "20260101T000000Z-0001"
            suite = "alpha"

        monkeypatch.setattr(client.app.state.supervisor, "active", lambda: _Live())
        refused = client.post("/api/system/power", json={"action": "poweroff"})
        assert refused.status_code == 409
        assert "alpha" in refused.json()["detail"]
        assert calls == []

    def test_a_refusal_reaches_the_operator(self, client, monkeypatch):
        """Polkit's own words, plus the step that grants the right.

        A rig serves from a lingering user manager with no session, which is
        exactly the case logind refuses, so this is what an operator meets on
        a bench where `setup-host.sh` has not been run.
        """
        self._spy(monkeypatch, returncode=1, stderr="Interactive authentication required.")
        refused = client.post("/api/system/power", json={"action": "poweroff"})
        assert refused.status_code == 502
        detail = refused.json()["detail"]
        assert "Interactive authentication required." in detail
        assert "setup-host.sh" in detail

    def test_an_unexplained_refusal_is_passed_through(self, client, monkeypatch):
        self._spy(monkeypatch, returncode=1, stderr="Failed to start poweroff.target.")
        refused = client.post("/api/system/power", json={"action": "poweroff"})
        assert refused.json()["detail"] == "Failed to start poweroff.target."

    def test_a_host_without_systemctl_says_so(self, client, monkeypatch):
        self._spy(monkeypatch)
        monkeypatch.setattr(system.shutil, "which", lambda _name: None)
        unavailable = client.post("/api/system/power", json={"action": "poweroff"})
        assert unavailable.status_code == 503


class TestSchemas:
    def test_lists_the_contract_models(self, client):
        assert "verdict" in client.get("/api/schemas").json()["schemas"]

    def test_serves_a_generated_schema(self, client):
        schema = client.get("/api/schemas/suite").json()
        assert schema["type"] == "object"
        assert "key" in schema["properties"]

    def test_unknown_schema_is_404(self, client):
        assert client.get("/api/schemas/nope").status_code == 404


class TestSuites:
    def test_lists_discovered_suites(self, client):
        body = client.get("/api/suites").json()
        assert [s["key"] for s in body["suites"]] == ["alpha"]
        assert body["suites"][0]["profiles_available"][0]["name"] == "smoke.yaml"

    def test_unknown_suite_is_404(self, client):
        assert client.get("/api/suites/nope").status_code == 404

    def test_profile_body_is_readable(self, client):
        body = client.get("/api/suites/alpha/profiles/smoke.yaml").json()
        assert "description: fast" in body["body"]

    def test_saving_a_profile_does_not_touch_the_suite(self, client):
        response = client.put("/api/suites/alpha/profiles/mine", json={"body": "description: mine\n"})
        assert response.status_code == 200
        assert response.json()["user_authored"] is True
        assert "smoke.yaml" not in response.json()["path"]

    def test_profile_schema_absent_is_404(self, client):
        assert client.get("/api/suites/alpha/profile-schema").status_code == 404

    def test_verify_reports_checks(self, client):
        body = client.post("/api/suites/alpha/verify").json()
        assert body["suite"] == "alpha"
        assert any(c["name"] == "suite.yaml is valid" for c in body["checks"])


class TestRuns:
    def test_start_and_finish(self, client):
        started = client.post("/api/runs", json={"suite": "alpha", "profile": "smoke.yaml"})
        assert started.status_code == 201
        run_id = started.json()["run_id"]

        finished = _wait_for_finish(client, run_id)
        assert finished["status"] == "passed"
        assert finished["verdict"] == "PASS"

    def test_unknown_suite_is_rejected(self, client):
        response = client.post("/api/runs", json={"suite": "nope"})
        assert response.status_code == 422
        assert "unknown suite" in response.json()["detail"]

    def test_unknown_profile_is_rejected(self, client):
        response = client.post("/api/runs", json={"suite": "alpha", "profile": "nope.yaml"})
        assert response.status_code == 422

    def test_undeclared_override_is_rejected(self, client):
        response = client.post("/api/runs", json={"suite": "alpha", "overrides": {"whatever": 1}})
        assert response.status_code == 422
        assert "does not declare override" in response.json()["detail"]

    def test_second_concurrent_run_is_refused(self, client, monkeypatch):
        first = client.post("/api/runs", json={"suite": "alpha"}).json()
        # The first run may already have finished; only assert the conflict
        # when it is genuinely still in flight.
        if client.get(f"/api/runs/{first['run_id']}").json()["status"] not in {"passed", "failed", "error"}:
            assert client.post("/api/runs", json={"suite": "alpha"}).status_code == 409
        _wait_for_finish(client, first["run_id"])

    def test_artifacts_are_listed_and_readable(self, client):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)

        listed = client.get(f"/api/runs/{run_id}/artifacts").json()
        assert {"verdict.json", "metrics.jsonl"} <= {a["path"] for a in listed["artifacts"]}
        assert client.get(f"/api/runs/{run_id}/artifacts/verdict.json").json()["passed"] is True
        assert client.get(f"/api/runs/{run_id}/metrics").json()["count"] == 1

    def test_artifact_traversal_is_blocked(self, client):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)
        assert client.get(f"/api/runs/{run_id}/artifacts/../../../etc/passwd").status_code in {400, 404}

    def test_finished_run_appears_in_history(self, client):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)
        assert run_id in {r["run_id"] for r in client.get("/api/runs").json()["runs"]}


class TestRunLookup:
    def test_a_finished_run_missing_from_the_index_is_answered_from_memory(self, client, monkeypatch):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)
        monkeypatch.setattr(client.app.state.runs_index, "get", lambda _run_id: None)

        body = client.get(f"/api/runs/{run_id}").json()

        assert body["run_id"] == run_id
        assert body["status"] == "passed"

    def test_an_unknown_run_is_404(self, client):
        assert client.get("/api/runs/nope").status_code == 404


class TestRunDelete:
    def test_delete_removes_the_row_the_notes_and_the_directory(self, client, add_run):
        run_dir = client.app.state.settings.runs_dir / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text("{}")
        add_run("r1", run_dir=run_dir)
        client.post("/api/runs/r1/notes", json={"body": "gone"})

        assert client.delete("/api/runs/r1").json() == {"id": "r1", "deleted": True}
        assert client.get("/api/runs/r1").status_code == 404
        assert not run_dir.exists()

    def test_delete_leaves_a_directory_outside_runs_dir_alone(self, client, add_run, tmp_path):
        outside = tmp_path / "elsewhere" / "r1"
        outside.mkdir(parents=True)
        add_run("r1", run_dir=outside)

        assert client.delete("/api/runs/r1").json() == {"id": "r1", "deleted": True}
        assert outside.exists()

    def test_delete_refuses_a_run_still_in_flight(self, client, add_run, monkeypatch):
        add_run("r1", status="running")

        class _Live:
            finished = False

        monkeypatch.setattr(client.app.state.supervisor, "get", lambda _run_id: _Live())

        response = client.delete("/api/runs/r1")

        assert response.status_code == 409
        assert client.app.state.runs_index.get("r1") is not None

    def test_unknown_run_delete_is_404(self, client):
        assert client.delete("/api/runs/nope").status_code == 404


class TestRunHistoryFilters:
    def test_total_counts_every_match_not_just_the_page(self, client, add_run):
        for index in range(5):
            add_run(f"r{index}")
        body = client.get("/api/runs", params={"limit": 2}).json()
        assert len(body["runs"]) == 2
        assert body["total"] == 5

    def test_status_may_be_repeated(self, client, add_run):
        add_run("passed-one", status="passed")
        add_run("failed-one", status="failed")
        add_run("errored-one", status="error")
        body = client.get("/api/runs", params=[("status", "failed"), ("status", "error")]).json()
        assert {r["run_id"] for r in body["runs"]} == {"failed-one", "errored-one"}
        assert body["total"] == 2

    def test_date_bounds_are_inclusive_of_the_whole_day(self, client, add_run):
        add_run("early", started_at="2026-03-01T23:59:59Z")
        add_run("wanted", started_at="2026-03-02T12:00:00Z")
        add_run("late", started_at="2026-03-03T00:00:01Z")
        body = client.get("/api/runs", params={"after": "2026-03-02", "before": "2026-03-02"}).json()
        assert [r["run_id"] for r in body["runs"]] == ["wanted"]

    def test_sort_column_and_direction_are_honoured(self, client, add_run):
        add_run("b-run", suite="beta")
        add_run("a-run", suite="alpha")
        body = client.get("/api/runs", params={"sort": "suite", "direction": "asc"}).json()
        assert [r["suite"] for r in body["runs"]] == ["alpha", "beta"]

    def test_unknown_sort_column_falls_back_to_started_at(self, client, add_run):
        add_run("first")
        add_run("second")
        body = client.get("/api/runs", params={"sort": "run_dir; DROP TABLE runs"}).json()
        assert [r["run_id"] for r in body["runs"]] == ["second", "first"]

    def test_unit_history_reports_a_total(self, client, add_run):
        add_run("one", unit_serial="SN-1")
        add_run("two", unit_serial="SN-1")
        body = client.get("/api/units/SN-1/history", params={"limit": 1}).json()
        assert len(body["runs"]) == 1
        assert body["total"] == 2


class TestSettingsPayload:
    def test_reports_the_resolved_runs_index_path(self, client):
        body = client.get("/api/settings").json()
        assert body["runs_index_path"].endswith("runs.sqlite")


class TestRenameFollowsTheIndex:
    def test_a_finished_run_is_answered_from_the_index(self, client):
        """A rename rewrites the index row, and the run must report the new serial."""
        run_id = client.post("/api/runs", json={"suite": "alpha", "unit_serial": "SN-OLD"}).json()["run_id"]
        _wait_for_finish(client, run_id)

        assert client.patch("/api/units/SN-OLD", json={"serial": "SN-NEW"}).status_code == 200
        assert client.get(f"/api/runs/{run_id}").json()["unit_serial"] == "SN-NEW"
        listed = client.get("/api/runs", params={"unit_serial": "SN-NEW"}).json()
        assert [r["unit_serial"] for r in listed["runs"]] == ["SN-NEW"]
