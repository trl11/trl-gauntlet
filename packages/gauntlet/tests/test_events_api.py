"""The SSE endpoint: replay, live tail, keepalive, and the end of a run."""

from __future__ import annotations

import json
import textwrap
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gauntlet.api import runs as runs_api
from gauntlet.app import create_app

# Emits one log line per phase, then a verdict, over about half a second.
_SLOW_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    run_dir="${GAUNTLET_RUN_DIR:-}"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --run-dir) run_dir="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    mkdir -p "$run_dir"
    for step in 1 2 3; do
        echo "step $step"
        sleep 0.1
    done
    echo '{"kind":"iteration","iteration":1,"timestamp":1,"success":true,"metrics":{"v":1}}' > "$run_dir/metrics.jsonl"
    echo '{"passed": true, "reason": "", "total_iterations": 1}' > "$run_dir/verdict.json"
    """
)


def read_events(client: TestClient, run_id: str, **params: Any) -> list[dict[str, Any]]:
    """Consume one SSE stream to its end frame."""
    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/runs/{run_id}/events", params=params) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload["type"] == "end":
                break
    return events


@pytest.fixture
def slow_client(make_suite, settings):
    """An app serving one suite that takes long enough to watch live."""
    make_suite("slow", script=_SLOW_SCRIPT)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestEventStream:
    def test_streams_a_run_from_start_to_end(self, slow_client) -> None:
        run_id = slow_client.post("/api/runs", json={"suite": "slow"}).json()["run_id"]

        events = read_events(slow_client, run_id)
        types = [event["type"] for event in events]
        assert types[-1] == "end"
        assert "log" in types
        assert "verdict" in types
        assert [event["seq"] for event in events if "seq" in event] == sorted(
            event["seq"] for event in events if "seq" in event
        )

    def test_the_log_lines_the_suite_printed_arrive(self, slow_client) -> None:
        run_id = slow_client.post("/api/runs", json={"suite": "slow"}).json()["run_id"]

        logs = [event for event in read_events(slow_client, run_id) if event["type"] == "log"]
        assert [event["message"] for event in logs] == ["step 1", "step 2", "step 3"]
        assert {event["level"] for event in logs} == {"info"}

    def test_since_replays_only_what_came_after_it(self, slow_client) -> None:
        run_id = slow_client.post("/api/runs", json={"suite": "slow"}).json()["run_id"]
        everything = read_events(slow_client, run_id)
        numbered = [event for event in everything if "seq" in event]

        tail = read_events(slow_client, run_id, since=numbered[0]["seq"])
        assert [event["seq"] for event in tail if "seq" in event] == [event["seq"] for event in numbered[1:]]

    def test_a_finished_run_replays_and_then_ends(self, slow_client) -> None:
        run_id = slow_client.post("/api/runs", json={"suite": "slow"}).json()["run_id"]
        read_events(slow_client, run_id)

        replayed = read_events(slow_client, run_id)
        assert replayed[-1]["type"] == "end"
        assert any(event["type"] == "verdict" for event in replayed)

    def test_a_run_with_no_live_stream_is_404(self, slow_client, add_run) -> None:
        add_run("historical")
        assert slow_client.get("/api/runs/historical/events").status_code == 404
        assert slow_client.get("/api/runs/nope/events").status_code == 404

    def test_a_quiet_stream_sends_a_keepalive(self, monkeypatch, slow_client) -> None:
        monkeypatch.setattr(runs_api, "_HEARTBEAT_S", 0.05)
        run_id = slow_client.post("/api/runs", json={"suite": "slow"}).json()["run_id"]

        comments = []
        with slow_client.stream("GET", f"/api/runs/{run_id}/events") as response:
            for line in response.iter_lines():
                if line.startswith(":"):
                    comments.append(line)
                if line.startswith("data: ") and json.loads(line[len("data: ") :])["type"] == "end":
                    break
        assert ": keepalive" in comments
