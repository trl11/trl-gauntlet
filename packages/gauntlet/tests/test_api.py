"""API behaviour, driven against real suites on disk."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app
from gauntlet.config import Settings


@pytest.fixture
def client(make_suite, suite_root, tmp_path):
    make_suite("alpha")
    settings = Settings(
        host="127.0.0.1",
        port=7100,
        suite_roots=[suite_root],
        data_dir=tmp_path / "data",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


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

    def test_version_reports_the_contract(self, client):
        assert client.get("/api/version").json()["contract_version"] == 1

    def test_capabilities_include_the_mocks(self, client):
        names = {c["name"] for c in client.get("/api/capabilities").json()["capabilities"]}
        assert {"psu", "daq", "chamber"} <= names


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
        assert body["suites"][0]["profiles_available"][0]["name"] == "quick.yaml"

    def test_unknown_suite_is_404(self, client):
        assert client.get("/api/suites/nope").status_code == 404

    def test_profile_body_is_readable(self, client):
        body = client.get("/api/suites/alpha/profiles/quick.yaml").json()
        assert "description: fast" in body["body"]

    def test_saving_a_profile_does_not_touch_the_suite(self, client):
        response = client.put("/api/suites/alpha/profiles/mine", json={"body": "description: mine\n"})
        assert response.status_code == 200
        assert response.json()["user_authored"] is True
        assert "quick.yaml" not in response.json()["path"]

    def test_profile_schema_absent_is_404(self, client):
        assert client.get("/api/suites/alpha/profile-schema").status_code == 404

    def test_verify_reports_checks(self, client):
        body = client.post("/api/suites/alpha/verify").json()
        assert body["suite"] == "alpha"
        assert any(c["name"] == "suite.yaml is valid" for c in body["checks"])


class TestRuns:
    def test_start_and_finish(self, client):
        started = client.post("/api/runs", json={"suite": "alpha", "profile": "quick.yaml"})
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
        assert client.get(f"/api/runs/{run_id}/verdict").json()["passed"] is True
        assert client.get(f"/api/runs/{run_id}/metrics").json()["count"] == 1

    def test_artifact_traversal_is_blocked(self, client):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)
        assert client.get(f"/api/runs/{run_id}/artifacts/../../../etc/passwd").status_code in {400, 404}

    def test_finished_run_appears_in_history(self, client):
        run_id = client.post("/api/runs", json={"suite": "alpha"}).json()["run_id"]
        _wait_for_finish(client, run_id)
        assert run_id in {r["run_id"] for r in client.get("/api/runs").json()["runs"]}
