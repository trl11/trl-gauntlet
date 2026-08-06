"""End to end: the real ``system_stats`` suite, through the real supervisor.

Nothing here is stubbed. The app discovers the suite that ships with the
repository, spawns it as a subprocess, streams its events, and the assertions
are made against the verdict, the run index, and the files on disk. It takes a
few seconds, so it is marked ``e2e`` and excluded from ``make test``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app
from gauntlet.config import Settings

pytestmark = pytest.mark.e2e

# packages/gauntlet/tests -> the repository root, where suites/ lives.
REPO_ROOT = Path(__file__).resolve().parents[3]

SUITE = "system_stats"
PROFILE = "quick.yaml"
UNIT = "SN-E2E-001"


@pytest.fixture(scope="module")
def e2e_run(tmp_path_factory) -> dict[str, Any]:
    """Run the suite once and collect everything the assertions need.

    One run serves the whole module: it is the expensive part, and every test
    below reads a different facet of the same result.
    """
    data_dir = tmp_path_factory.mktemp("e2e")
    settings = Settings(
        host="127.0.0.1",
        port=7100,
        suite_roots=[REPO_ROOT / "suites"],
        data_dir=data_dir,
    )
    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/runs",
            json={"suite": SUITE, "profile": PROFILE, "unit_serial": UNIT},
        )
        assert started.status_code == 201, started.text
        run_id = started.json()["run_id"]

        events = _drain(client, run_id)
        return {
            "artifacts": client.get(f"/api/runs/{run_id}/artifacts").json(),
            "events": events,
            "history": client.get("/api/runs").json(),
            "manifest": client.get(f"/api/runs/{run_id}/artifacts/manifest.json").json(),
            "metrics": client.get(f"/api/runs/{run_id}/metrics").json(),
            "run": client.get(f"/api/runs/{run_id}").json(),
            "run_id": run_id,
            "unit": client.get(f"/api/units/{UNIT}").json(),
            "verdict": client.get(f"/api/runs/{run_id}/artifacts/verdict.json").json(),
        }


def _drain(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    """Consume the run's SSE stream until it reports the end."""
    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload["type"] == "end":
                break
    return events


class TestTheStream:
    def test_carries_every_kind_of_event_the_suite_produces(self, e2e_run) -> None:
        types = {event["type"] for event in e2e_run["events"]}
        assert {"end", "iteration", "log", "metrics", "phase", "status", "verdict"} <= types

    def test_the_sequence_numbers_never_go_backwards(self, e2e_run) -> None:
        numbered = [event["seq"] for event in e2e_run["events"] if "seq" in event]
        assert numbered == sorted(numbered)

    def test_the_last_status_matches_the_indexed_run(self, e2e_run) -> None:
        statuses = [event["status"] for event in e2e_run["events"] if event["type"] == "status"]
        assert statuses[-1] == e2e_run["run"]["status"]

    def test_the_verdict_event_agrees_with_the_file(self, e2e_run) -> None:
        published = next(event for event in e2e_run["events"] if event["type"] == "verdict")
        assert published["result"] == e2e_run["run"]["verdict"]

    def test_every_iteration_the_profile_asked_for_arrives(self, e2e_run) -> None:
        iterations = [event for event in e2e_run["events"] if event["type"] == "iteration"]
        assert len(iterations) == e2e_run["verdict"]["total_iterations"]


class TestTheVerdict:
    def test_the_run_passed(self, e2e_run) -> None:
        assert e2e_run["verdict"]["passed"] is True
        assert e2e_run["run"]["status"] == "passed"
        assert e2e_run["run"]["verdict"] == "PASS"

    def test_it_samples_more_than_once(self, e2e_run) -> None:
        # quick.yaml runs for 3s at a 0.25s period.
        assert e2e_run["verdict"]["total_iterations"] >= 2

    def test_every_check_is_reported_as_a_test_row(self, e2e_run) -> None:
        outcomes = {test["name"]: test["outcome"] for test in e2e_run["verdict"]["tests"]}
        assert outcomes
        assert "fail" not in outcomes.values()

    def test_it_carries_headline_results(self, e2e_run) -> None:
        keys = {result["key"] for result in e2e_run["verdict"]["results"]}
        assert {"anomalies", "cpu_mean", "cpu_peak", "duration"} <= keys


class TestTheIndexedRun:
    def test_the_run_is_in_history(self, e2e_run) -> None:
        assert e2e_run["run_id"] in {row["run_id"] for row in e2e_run["history"]["runs"]}

    def test_it_records_what_it_was_asked_to_run(self, e2e_run) -> None:
        row = e2e_run["run"]
        assert row["suite"] == SUITE
        assert row["profile"] == PROFILE
        assert row["unit_serial"] == UNIT

    def test_it_is_timed(self, e2e_run) -> None:
        row = e2e_run["run"]
        assert row["started_at"].endswith("Z")
        assert row["ended_at"].endswith("Z")
        assert row["duration_s"] > 0

    def test_the_unit_is_derived_from_it(self, e2e_run) -> None:
        unit = e2e_run["unit"]
        assert unit["serial"] == UNIT
        assert (unit["run_count"], unit["passed"], unit["failed"]) == (1, 1, 0)
        assert unit["last_run"]["run_id"] == e2e_run["run_id"]


class TestTheArtifacts:
    def test_the_suite_produced_everything_it_declares(self, e2e_run) -> None:
        written = {entry["path"] for entry in e2e_run["artifacts"]["artifacts"]}
        # One file per entry of the suite's `produces:`, plus its stdout.
        assert {
            "events.sqlite",
            "junit.xml",
            "manifest.json",
            "metrics.jsonl",
            "summary.md",
            "test.log",
            "verdict.json",
        } <= written

    def test_they_are_on_disk_in_the_run_directory(self, e2e_run) -> None:
        run_dir = Path(e2e_run["artifacts"]["run_dir"])
        assert run_dir.is_dir()
        for entry in e2e_run["artifacts"]["artifacts"]:
            assert (run_dir / entry["path"]).stat().st_size == entry["size"]

    def test_the_metrics_file_holds_one_record_per_iteration(self, e2e_run) -> None:
        iterations = [record for record in e2e_run["metrics"]["records"] if record.get("kind") == "iteration"]
        assert len(iterations) == e2e_run["verdict"]["total_iterations"]

    def test_the_manifest_records_how_the_suite_was_launched(self, e2e_run) -> None:
        manifest = e2e_run["manifest"]
        assert manifest["suite"] == SUITE
        assert manifest["run_id"] == e2e_run["run_id"]
