"""Stopping, aborting, and how a run's verdict decides its status."""

from __future__ import annotations

import asyncio
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app
from gauntlet.supervisor.supervisor import (
    _epoch,
    _read_verdict,
    _schedule,
    _snapshot_profile,
    _write_scratch_profile,
)

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


# Refuses the run the way the SDK does when the bench is unreachable: one
# `error:` line, no verdict, exit 2.
_REFUSES = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo "uut: connecting to trl@192.168.0.149"
    echo "error: ssh to trl@192.168.0.149: Authentication failed."
    exit 2
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


def wait_for_output(client: TestClient, run_id: str, timeout_s: float = 10.0) -> None:
    """Block until the suite has written its first line.

    A graceful stop is a signal, and one delivered before the suite installed
    its handler kills it instead — bash's default for SIGUSR1 is to terminate.
    A run reads as `running` from the moment its process is spawned, which is
    earlier than that, so a test that stops one waits for the suite itself to
    say it is up. An operator cannot lose this race: the run page has to render
    before there is a button to press.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        log = client.get(f"/api/runs/{run_id}/artifacts/test.log")
        if log.status_code == 200 and log.text.strip():
            return
        time.sleep(0.02)
    raise AssertionError(f"{run_id} wrote nothing within {timeout_s:g}s")


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


class _OwnableStub:
    """The smallest thing satisfying :class:`OwnableCapability`, for a run."""

    name = "camera"

    def __init__(self, *, opens: bool = True, opening_s: float = 0.0) -> None:
        self._owned = False
        self._opens = opens
        self._opening_s = opening_s

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"driver": "test", "unavailable_reason": "" if self._opens else "no frame arrived"}

    def instance_id(self) -> str:
        return "camera0"

    def owned(self) -> bool:
        return self._owned

    def own(self) -> bool:
        time.sleep(self._opening_s)
        self._owned = self._opens
        return self._owned

    def disown(self) -> None:
        self._owned = False


class TestCapabilityOwnership:
    def test_a_run_owns_and_releases_a_capability_it_did_not_find_owned(self, make_suite, settings) -> None:
        make_suite("busy", requires=["camera"], script=_GRACEFUL)
        with TestClient(create_app(settings)) as client:
            camera = _OwnableStub()
            client.app.state.capabilities.register(camera)
            run_id = start(client, "busy")
            assert camera.owned() is True
            client.post(f"/api/runs/{run_id}/stop")
            wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert camera.owned() is False

    def test_a_run_leaves_a_capability_it_found_already_owned(self, make_suite, settings) -> None:
        make_suite("brief", requires=["camera"])
        with TestClient(create_app(settings)) as client:
            camera = _OwnableStub()
            camera.own()
            client.app.state.capabilities.register(camera)
            run_id = start(client, "brief")
            wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert camera.owned() is True


class TestStop:
    def test_a_stopped_run_still_writes_its_verdict(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            run_id = start(client)
            wait_for_output(client, run_id)
            assert client.post(f"/api/runs/{run_id}/stop").json() == {"run_id": run_id, "status": "stopping"}
            finished = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert finished["status"] == "passed"
            assert client.get(f"/api/runs/{run_id}/artifacts/verdict.json").json()["stopped_early"] is True

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

    def test_an_error_run_reports_what_the_suite_said_over_the_exit_code(self, app_with) -> None:
        with app_with(slow=_REFUSES) as client:
            run_id = start(client)
            row = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert row["status"] == "error"
            assert row["fail_reason"] == "ssh to trl@192.168.0.149: Authentication failed."

    def test_an_error_run_that_said_nothing_falls_back_to_the_exit_code(self, app_with) -> None:
        with app_with(slow="#!/usr/bin/env bash\nexit 1\n") as client:
            run_id = start(client)
            row = wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
            assert row["status"] == "error"
            assert "without writing verdict.json" in row["fail_reason"]

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
        run_id = client.post("/api/runs", json={"suite": "alpha", "profile": "smoke.yaml"}).json()["run_id"]
        wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
        assert "description: fast" in client.get(f"/api/runs/{run_id}/artifacts/profile.yaml").text


class TestRunsThatNeverStart:
    def test_a_command_that_cannot_be_executed_is_an_error(self, make_suite, settings) -> None:
        make_suite("broken")
        (settings.suite_roots[0] / "broken" / "run.sh").chmod(0o644)

        with TestClient(create_app(settings)) as client:
            run_id = client.post("/api/runs", json={"suite": "broken"}).json()["run_id"]
            finished = wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})

            assert finished["status"] == "error"
            assert "failed to spawn" in finished["fail_reason"]

    def test_a_suite_needing_an_unregistered_instrument_is_rejected(self, make_suite, settings) -> None:
        make_suite("needy", requires=["laser_cutter"])

        with TestClient(create_app(settings)) as client:
            response = client.post("/api/runs", json={"suite": "needy"})

            assert response.status_code == 422
            assert "laser_cutter" in response.json()["detail"]

    def test_an_instrument_that_will_not_open_errors_the_run(self, make_suite, settings) -> None:
        """The bench has one, and it refused: that is a run that failed, not a bad request."""
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_OwnableStub(opens=False))
            started = client.post("/api/runs", json={"suite": "needy"})

            assert started.status_code == 201
            run_id = started.json()["run_id"]
            finished = wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})
            assert finished["status"] == "error"
            assert "camera could not be opened: no frame arrived" in finished["fail_reason"]

    def test_a_run_that_never_opened_its_instruments_reaches_history(self, make_suite, settings) -> None:
        """The row was written while the run still said `starting`."""
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_OwnableStub(opens=False))
            run_id = client.post("/api/runs", json={"suite": "needy"}).json()["run_id"]
            wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})

            listed = {row["run_id"]: row for row in client.get("/api/runs").json()["runs"]}
            assert listed[run_id]["status"] == "error"

    def test_the_wait_for_an_instrument_is_said_in_the_run_log(self, make_suite, settings) -> None:
        """The one stretch of a run with nothing else to show for itself."""
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_OwnableStub(opens=False))
            run_id = client.post("/api/runs", json={"suite": "needy"}).json()["run_id"]
            wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})

            written = client.get(f"/api/runs/{run_id}/artifacts/test.log").text
            assert "opening camera" in written
            assert "no frame arrived" in written

    def test_a_run_can_be_stopped_while_its_instruments_are_opening(self, make_suite, settings) -> None:
        """A `starting` run the operator cannot cancel is the same complaint again."""
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_OwnableStub(opening_s=2.0))
            run_id = client.post("/api/runs", json={"suite": "needy"}).json()["run_id"]

            assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
            finished = wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})
            assert finished["status"] == "aborted"
            assert finished["fail_reason"] == "stopped before the suite was started"
            # The operator waits out the driver's own timeout, so the log says why.
            written = client.get(f"/api/runs/{run_id}/artifacts/test.log").text
            assert "waiting for the instruments to finish opening" in written

    def test_stopping_before_the_spawn_still_releases_the_instrument(self, make_suite, settings) -> None:
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            camera = _OwnableStub(opening_s=2.0)
            client.app.state.capabilities.register(camera)
            run_id = client.post("/api/runs", json={"suite": "needy"}).json()["run_id"]
            client.post(f"/api/runs/{run_id}/stop")
            wait_for_status(client, run_id, {"error", "aborted", "failed", "passed"})

            assert camera.owned() is False

    def test_the_run_is_handed_back_before_its_instruments_open(self, make_suite, settings) -> None:
        """Opening is where a start waits, so it happens with the run already created."""
        make_suite("needy", requires=["camera"])

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_OwnableStub(opening_s=2.0))
            began = time.monotonic()
            started = client.post("/api/runs", json={"suite": "needy"})
            answered_in = time.monotonic() - began

            assert started.status_code == 201
            assert started.json()["status"] == "starting"
            assert answered_in < 1.0
            wait_for_status(client, started.json()["run_id"], {"error", "aborted", "failed", "passed"})


class TestSignallingAProcessThatHasGone:
    """The process can exit between the status check and the signal."""

    def _detach(self, client, run_id: str):
        handle = client.app.state.supervisor.get(run_id)
        original = handle.process.send_signal

        def _gone(_signum):
            raise ProcessLookupError("no such process")

        handle.process.send_signal = _gone
        return handle, original

    def _clean_up(self, client, handle, original, run_id: str) -> None:
        handle.process.send_signal = original
        client.post(f"/api/runs/{run_id}/abort")
        wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})

    def test_stopping_it_is_409(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            run_id = start(client)
            handle, original = self._detach(client, run_id)

            try:
                assert client.post(f"/api/runs/{run_id}/stop").status_code == 409
            finally:
                self._clean_up(client, handle, original, run_id)

    def test_aborting_it_is_409(self, app_with) -> None:
        with app_with(slow=_GRACEFUL) as client:
            run_id = start(client)
            handle, original = self._detach(client, run_id)

            try:
                assert client.post(f"/api/runs/{run_id}/abort").status_code == 409
            finally:
                self._clean_up(client, handle, original, run_id)


class TestEviction:
    def test_only_the_configured_number_of_finished_runs_stay_in_memory(self, app_with) -> None:
        with app_with(quick=script_writing('{"passed": true, "reason": ""}')) as client:
            client.app.state.supervisor._history_size = 2
            run_ids = []
            for _ in range(4):
                run_id = start(client, suite="quick")
                wait_for_status(client, run_id, {"passed", "failed", "error", "aborted"})
                run_ids.append(run_id)

            live = {handle.run_id for handle in client.app.state.supervisor.list_runs()}

            # Eviction runs when a run starts, so the last one to finish is
            # still held; which of the older ones went is not defined.
            assert len(live) < len(run_ids)
            # Every run is still in history, which is what the index is for.
            assert {row["run_id"] for row in client.get("/api/runs").json()["runs"]} == set(run_ids)


class TestSupervisorHelpers:
    def test_a_verdict_that_cannot_be_read_is_none(self, tmp_path) -> None:
        assert _read_verdict(tmp_path / "absent.json") is None

    def test_a_verdict_that_is_not_json_is_none(self, tmp_path) -> None:
        path = tmp_path / "verdict.json"
        path.write_text("{ truncated")

        assert _read_verdict(path) is None

    def test_a_verdict_that_does_not_match_the_contract_is_none(self, tmp_path) -> None:
        path = tmp_path / "verdict.json"
        path.write_text('{"passed": "sort of"}')

        assert _read_verdict(path) is None

    def test_a_conforming_verdict_is_parsed(self, tmp_path) -> None:
        path = tmp_path / "verdict.json"
        path.write_text('{"passed": false, "reason": "too hot"}')

        assert _read_verdict(path).reason == "too hot"

    def test_an_inline_profile_is_written_to_its_own_scratch_file(self, tmp_path) -> None:
        first = _write_scratch_profile(tmp_path, "alpha", "iterations: 1\n")
        second = _write_scratch_profile(tmp_path, "alpha", "iterations: 2\n")

        assert first.read_text() == "iterations: 1\n"
        assert first != second
        assert first.parent == tmp_path / "_scratch" / "alpha"

    def test_the_profile_snapshot_does_not_overwrite_an_existing_one(self, tmp_path) -> None:
        source = tmp_path / "quick.yaml"
        source.write_text("new\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "profile.yaml").write_text("already here\n")

        _snapshot_profile(source, run_dir)

        assert (run_dir / "profile.yaml").read_text() == "already here\n"

    def test_a_profile_snapshot_that_cannot_be_read_is_not_fatal(self, tmp_path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        _snapshot_profile(tmp_path / "absent.yaml", run_dir)

        assert not (run_dir / "profile.yaml").exists()

    def test_a_timestamp_that_cannot_be_parsed_falls_back_to_now(self) -> None:
        assert _epoch("not a timestamp") == pytest.approx(time.time(), abs=5)

    def test_a_well_formed_timestamp_is_parsed_as_utc(self) -> None:
        assert _epoch("2026-01-01T00:00:00Z") == 1_767_225_600.0

    def test_scheduling_onto_a_closed_loop_does_not_raise(self) -> None:
        loop = asyncio.new_event_loop()
        loop.close()

        async def _work() -> None:
            pass

        _schedule(loop, _work())


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
