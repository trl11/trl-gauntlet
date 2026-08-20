"""Application startup: what it recovers on boot, and how it serves the bundle."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app
from gauntlet.storage import RunRow, RunsIndex


def _bundle(directory) -> None:
    (directory / "index.html").write_text("<!doctype html><title>Gauntlet</title><div id='root'></div>")
    (directory / "assets").mkdir()
    (directory / "assets" / "app.js").write_text("console.log('gauntlet')")


class TestStartupRecovery:
    def test_a_run_left_in_progress_is_marked_interrupted(self, make_suite, settings):
        make_suite("alpha")
        settings.ensure_dirs()
        index = RunsIndex(settings.runs_index_path)
        index.upsert(
            RunRow(
                run_id="interrupted-1",
                suite="alpha",
                status="running",
                started_at="2026-01-01T00:00:00Z",
                run_dir=str(settings.runs_dir / "alpha" / "interrupted-1"),
            )
        )
        index.close()

        with TestClient(create_app(settings)) as client:
            row = client.get("/api/runs/interrupted-1").json()

        assert row["status"] == "error"
        assert "interrupted" in row["fail_reason"]

    def test_a_run_directory_on_disk_is_imported_into_the_index(self, make_suite, settings):
        make_suite("alpha")
        run_dir = settings.runs_dir / "alpha" / "20260101T000000Z-abcd"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text(json.dumps({"passed": True, "reason": ""}))

        with TestClient(create_app(settings)) as client:
            row = client.get("/api/runs/20260101T000000Z-abcd").json()

        assert row["suite"] == "alpha"
        assert row["status"] == "passed"

    def test_a_discovery_error_does_not_stop_startup(self, make_suite, settings):
        make_suite("alpha")
        broken = settings.suite_roots[0] / "broken"
        broken.mkdir()
        (broken / "suite.yaml").write_text("apiVersion: 99\nkey: broken\n")

        with TestClient(create_app(settings)) as client:
            assert client.get("/api/suites").json()["errors"]
            assert client.get("/api/health").status_code == 200


class TestWithoutABundle:
    def test_the_root_explains_how_to_build_one(self, settings, web_dist):
        with TestClient(create_app(settings)) as fresh:
            response = fresh.get("/")

        assert response.status_code == 200
        assert "make frontend" in response.text
        assert "/docs" in response.text

    def test_the_api_still_answers(self, settings, web_dist):
        with TestClient(create_app(settings)) as fresh:
            assert fresh.get("/api/health").status_code == 200


class TestWithABundle:
    @pytest.fixture
    def served(self, settings, web_dist):
        _bundle(web_dist)
        with TestClient(create_app(settings)) as fresh:
            yield fresh

    def test_the_root_serves_the_index(self, served):
        response = served.get("/")

        assert response.status_code == 200
        assert "<div id='root'></div>" in response.text

    def test_assets_are_served_from_the_bundle(self, served):
        assert served.get("/assets/app.js").text == "console.log('gauntlet')"

    def test_an_unknown_route_falls_back_to_the_index_for_the_router(self, served):
        response = served.get("/units")

        assert response.status_code == 200
        assert "<div id='root'></div>" in response.text

    def test_a_real_file_in_the_bundle_is_served_as_itself(self, served, web_dist):
        (web_dist / "favicon.svg").write_text("<svg/>")

        assert served.get("/favicon.svg").text == "<svg/>"

    def test_an_unknown_api_path_is_a_json_404_not_the_shell(self, served):
        response = served.get("/api/nope")

        assert response.status_code == 404
        assert response.json()["detail"] == "not found"

    def test_the_bare_api_prefix_is_a_json_404(self, served):
        assert served.get("/api").status_code == 404

    def test_a_path_escaping_the_bundle_is_rejected(self, served):
        response = served.get("/..%2F..%2Fetc%2Fpasswd")

        assert response.status_code == 400
