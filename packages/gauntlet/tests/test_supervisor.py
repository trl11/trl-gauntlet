"""Stopping, aborting, and how a run's verdict decides its status."""

from __future__ import annotations

import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app

# Runs until told to stop, then writes a passing verdict and exits.
_GRACEFUL = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    run_dir="${GAUNTLET_RUN_DIR:-}"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --run-dir) run_dir="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    mkdir -p "$run_dir"
    finish() {
        echo '{"passed": true, "reason": "", "total_iterations": 1, "stopped_early": true}' > "$run_dir/verdict.json"
        exit 0
    }
    trap finish USR1
    echo "waiting"
    for _ in $(seq 1 600); do sleep 0.1; done
    """
)

# Ignores the graceful signal, so only SIGTERM ends it. Writes no verdict.
_STUBBORN = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    trap '' USR1
    echo "waiting"
    for _ in $(seq 1 600); do sleep 0.1; done
    """
)


def script_writing(verdict: str) -> str:
    """A suite that writes exactly this text as its verdict and exits."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        run_dir="${{GAUNTLET_RUN_DIR:-}}"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --run-dir) run_dir="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        mkdir -p "$run_dir"
        cat > "$run_dir/verdict.json" <<'VERDICT'
        {verdict}
        VERDICT
        """
    )


def wait_for_status(client: TestClient, run_id: str, wanted: set[str], timeout_s: float = 20.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {wanted}")


def start(client: TestClient, suite: str = "slow") -> str:
    started = client.post("/api/runs", json={"suite": suite})
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    wait_for_status(client, run_id, {"running", "passed", "failed", "error", "aborted"})
    return run_id


@pytest.fixture
def app_with(make_suite, settings):
    """Build an app around suites the test describes."""

    def _build(**suites: str) -> TestClient:
        for key, script in suites.items():
            make_suite(key, script=script)
        return TestClient(create_app(settings))

    return _build


class TestStop:
    def test_a_stopped_run_still_writes_its_verdict(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            run_id = start(client)
            assert client.post(f"/api/runs/{run_id}/stop").json() == {"run_id": run_id, "status": "stopping"}
            finished = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert finished["status"] == "passed"
            assert client.get(f"/api/runs/{run_id}/verdict").json()["stopped_early"] is True

    def test_stopping_a_finished_run_is_409(self, app_with) -> None:
        with app_with(slow=script_writing('{"passed": true, "reason": ""}')) as client:
            run_id = start(client)
            wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert client.post(f"/api/runs/{run_id}/stop").status_code == 409

    def test_stopping_an_unknown_run_is_409(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            assert client.post("/api/runs/nope/stop").status_code == 409

    def test_a_suite_that_declares_no_signal_is_aborted_instead(self, make_suite, settings) -> None:
        make_suite("slow", script=_STUBBORN, exec={"command": ["./run.sh"], "graceful_stop_signal": "NONE"})
        with TestClient(create_app(settings)) as client:
            run_id = start(client)
            assert client.post(f"/api/runs/{run_id}/stop").json()["status"] == "stopping"
            # No verdict was written, so the run is recorded as an error.
            assert wait_for_status(client, run_id, {"error", "aborted"})["status"] == "error"


class TestAbort:
    def test_an_aborted_run_without_a_verdict_is_an_error(self, app_with) -> None:
        with app_with(slow=_STUBBORN) as client:
            run_id = start(client)
            assert client.post(f"/api/runs/{run_id}/abort").json() == {"run_id": run_id, "status": "aborting"}
            finished = wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})
            assert finished["status"] == "error"
            assert "without writing verdict.json" in finished["fail_reason"]

    def test_aborting_a_finished_run_is_409(self, app_with) -> None:
        with app_with(slow=script_writing('{"passed": true, "reason": ""}')) as client:
            run_id = start(client)
            wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert client.post(f"/api/runs/{run_id}/abort").status_code == 409

    def test_aborting_an_unknown_run_is_409(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            assert client.post("/api/runs/nope/abort").status_code == 409


class TestTheVerdictDecidesTheStatus:
    def test_a_passing_verdict(self, app_with) -> None:
        with app_with(slow=script_writing('{"passed": true, "reason": ""}')) as client:
            run_id = start(client)
            row = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert (row["status"], row["verdict"], row["fail_reason"]) == ("passed", "PASS", None)

    def test_a_failing_verdict(self, app_with) -> None:
        with app_with(slow=script_writing('{"passed": false, "reason": "rail sagged"}')) as client:
            run_id = start(client)
            row = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert (row["status"], row["verdict"], row["fail_reason"]) == ("failed", "FAIL", "rail sagged")

    def test_a_verdict_that_says_it_was_aborted(self, app_with) -> None:
        body = '{"passed": false, "reason": "operator stopped it", "aborted": true}'
        with app_with(slow=script_writing(body)) as client:
            run_id = start(client)
            row = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert (row["status"], row["verdict"]) == ("aborted", "ABORTED")

    def test_a_verdict_that_is_not_json_is_an_error(self, app_with) -> None:
        with app_with(slow=script_writing("{ truncated")) as client:
            run_id = start(client)
            assert wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})["status"] == "error"

    def test_a_verdict_that_does_not_match_the_contract_is_an_error(self, app_with) -> None:
        with app_with(slow=script_writing('{"passed": "sort of"}')) as client:
            run_id = start(client)
            assert wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})["status"] == "error"


class TestInlineProfiles:
    def test_a_profile_body_is_run_without_being_saved(self, client) -> None:
        started = client.post(
            "/api/runs",
            json={"suite": "alpha", "profile_body": "description: inline\niterations: 1\n"},
        )
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})

        assert client.get("/api/suites/alpha/profiles/inline").status_code == 404
        listed = client.get(f"/api/runs/{run_id}/artifacts").json()["artifacts"]
        assert "profile.yaml" in {entry["path"] for entry in listed}

    def test_the_named_profile_is_copied_into_the_run(self, client) -> None:
        run_id = client.post("/api/runs", json={"suite": "alpha", "profile": "quick.yaml"}).json()["run_id"]
        wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
        assert "description: fast" in client.get(f"/api/runs/{run_id}/artifacts/profile.yaml").text


class TestRunIdentifiers:
    def test_runs_finishing_in_the_same_second_keep_their_own_directories(self, app_with) -> None:
        script = script_writing('{"passed": true, "reason": ""}')
        with app_with(quick=script) as client:
            run_ids = []
            for _ in range(6):
                run_id = start(client, suite="quick")
                wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
                run_ids.append(run_id)

            assert len(set(run_ids)) == len(run_ids)
            listed = client.get("/api/runs", params={"suite": "quick"}).json()["runs"]
            assert {row["run_id"] for row in listed} == set(run_ids)
            assert len({client.get(f"/api/runs/{r}").json()["run_dir"] for r in run_ids}) == len(run_ids)
