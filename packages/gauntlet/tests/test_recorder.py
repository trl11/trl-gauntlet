"""What a run records from the bench's instruments while it is in flight."""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gauntlet.app import create_app
from gauntlet.capabilities import CapabilityRegistry, readout
from gauntlet.supervisor.recorder import InstrumentRecorder, labels, numbers

# Writes a passing verdict and exits, so a run is over almost as soon as it
# starts and the recording is whatever one tick caught.
_BRIEF = textwrap.dedent(
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
    echo '{"passed": true, "reason": ""}' > "$run_dir/verdict.json"
    """
)


class _Supply:
    """A stateful provider whose readings climb by one volt a reading."""

    name = "psu"

    def __init__(self) -> None:
        self.volts = 0.0

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"description": "a bench supply", "driver": "test", "kind": "psu"}

    def instance_id(self) -> str:
        return "psu0"

    def state(self) -> dict[str, object]:
        self.volts += 1.0
        return {"output_enabled": True, "port": "/dev/null", "voltage": self.volts}

    def connection(self) -> str:
        return "nowhere"

    def primary_command(self) -> str:
        return ""

    def readouts(self) -> list[dict[str, object]]:
        return [readout("voltage", "Voltage", precision=2, unit="V")]


class _Daq:
    """A provider whose channels carry the names an operator gave them."""

    name = "daq"

    def __init__(self, named: str = "") -> None:
        self.named = named

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"driver": "test", "kind": "daq"}

    def instance_id(self) -> str:
        return "daq0"

    def state(self) -> dict[str, object]:
        return {
            "channels": {
                "ai0": {"label": self.named or "AI 0", "mode": "500mv", "unit": "V", "value": 0.5},
                "ai1": {"label": "AI 1", "mode": "500mv", "unit": "V", "value": 0.25},
            }
        }

    def connection(self) -> str:
        return "nowhere"

    def primary_command(self) -> str:
        return ""

    def readouts(self) -> list[dict[str, object]]:
        return [readout("channels.ai0.value", self.named or "AI 0", precision=6, unit="V")]


class _Silent:
    """A provider that refuses to be read, the way an unplugged one does."""

    name = "daq"

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"driver": "test", "kind": "daq"}

    def instance_id(self) -> str:
        return "daq0"

    def state(self) -> dict[str, object]:
        raise OSError("the device has gone")


def record(providers: dict[str, object], run_dir: Path, ticks: int = 2) -> dict:
    """Run a recorder over these providers for `ticks` readings and return the summary."""
    registry = CapabilityRegistry(api_base="http://127.0.0.1:7100/api")
    for provider in providers.values():
        registry.register(provider)  # type: ignore[arg-type]
    recorder = InstrumentRecorder(registry, list(providers), run_dir, interval_s=0.01)
    recorder.start()
    deadline = time.time() + 5.0
    trace = run_dir / "instruments.jsonl"
    while time.time() < deadline:
        if trace.is_file() and len(trace.read_text().splitlines()) >= ticks:
            break
        time.sleep(0.01)
    recorder.stop()
    return json.loads((run_dir / "instruments.json").read_text())


class TestNumbers:
    def test_walks_nested_state_into_dotted_paths(self) -> None:
        state = {"channels": {"ai0": {"value": 0.5}}, "samples": 3}
        assert numbers(state) == {"channels.ai0.value": 0.5, "samples": 3.0}

    def test_a_boolean_is_recorded_as_one_or_zero(self) -> None:
        assert numbers({"output_enabled": True, "streaming": False}) == {
            "output_enabled": 1.0,
            "streaming": 0.0,
        }

    def test_what_is_not_a_number_is_left_out(self) -> None:
        assert numbers({"node": "/dev/video0", "mode": None, "value": 2}) == {"value": 2.0}


class TestLabels:
    def test_a_channel_is_called_what_its_provider_calls_it(self) -> None:
        state = {"channels": {"ai0": {"label": "Rail 3V3", "mode": "500mv", "value": 0.5}}}
        assert labels(state) == {"channels.ai0.value": "Rail 3V3"}

    def test_one_name_over_several_numbers_keeps_them_apart(self) -> None:
        state = {"channels": {"3": {"label": "CLK", "duty": 50.0, "frequency": 1000.0}}}
        assert labels(state) == {
            "channels.3.duty": "CLK duty",
            "channels.3.frequency": "CLK frequency",
        }

    def test_state_that_names_nothing_names_nothing(self) -> None:
        assert labels({"voltage": 5.0, "channels": {"ai0": {"value": 0.5}}}) == {}


class TestRecording:
    def test_the_trace_carries_a_line_per_instrument_per_tick(self, tmp_path: Path) -> None:
        record({"psu": _Supply()}, tmp_path, ticks=3)
        lines = [json.loads(line) for line in (tmp_path / "instruments.jsonl").read_text().splitlines()]
        assert len(lines) >= 3
        assert lines[0]["instrument"] == "psu"
        assert lines[0]["values"]["voltage"] == 1.0
        assert lines[1]["t"] >= lines[0]["t"]

    def test_the_summary_carries_the_extremes_and_the_mean(self, tmp_path: Path) -> None:
        summary = record({"psu": _Supply()}, tmp_path, ticks=4)
        reading = next(
            entry
            for instrument in summary["instruments"]
            for entry in instrument["readings"]
            if entry["key"] == "voltage"
        )
        assert reading["min"] == 1.0
        assert reading["max"] == reading["last"] == float(reading["count"])
        assert reading["mean"] == pytest.approx((reading["count"] + 1) / 2)

    def test_a_reading_is_labelled_the_way_its_provider_declared_it(self, tmp_path: Path) -> None:
        summary = record({"psu": _Supply()}, tmp_path)
        readings = {entry["key"]: entry for entry in summary["instruments"][0]["readings"]}
        assert readings["voltage"]["label"] == "Voltage"
        assert readings["voltage"]["unit"] == "V"
        assert readings["voltage"]["precision"] == 2
        # Nothing declared it, so it is recorded under its own key.
        assert readings["output_enabled"]["label"] == "output_enabled"

    def test_a_reading_is_shown_under_the_name_the_operator_gave_its_channel(self, tmp_path: Path) -> None:
        summary = record({"daq": _Daq(named="Rail 3V3")}, tmp_path)
        readings = {entry["key"]: entry for entry in summary["instruments"][0]["readings"]}
        # The key is the path through the provider's state, the same on every
        # bench, and only what it is called follows the operator.
        assert readings["channels.ai0.value"]["label"] == "Rail 3V3"
        assert readings["channels.ai1.value"]["label"] == "AI 1"

    def test_the_name_outlives_an_instrument_that_goes_away(self, tmp_path: Path) -> None:
        daq = _Daq(named="Rail 3V3")
        registry = CapabilityRegistry(api_base="http://127.0.0.1:7100/api")
        registry.register(daq)  # type: ignore[arg-type]
        recorder = InstrumentRecorder(registry, ["daq"], tmp_path, interval_s=0.01)
        recorder.start()
        # The trace file is opened before the first reading is taken, so its
        # existence is not enough: the instrument may only go away once
        # something has actually been recorded from it.
        trace = tmp_path / "instruments.jsonl"
        deadline = time.time() + 5.0
        while time.time() < deadline and not (trace.is_file() and trace.read_text().splitlines()):
            time.sleep(0.01)
        registry.unregister("daq")
        recorder.stop()
        summary = json.loads((tmp_path / "instruments.json").read_text())
        readings = {entry["key"]: entry for entry in summary["instruments"][0]["readings"]}
        assert readings["channels.ai0.value"]["label"] == "Rail 3V3"

    def test_an_instrument_that_stops_answering_does_not_stop_the_rest(self, tmp_path: Path) -> None:
        summary = record({"daq": _Silent(), "psu": _Supply()}, tmp_path)
        recorded = {instrument["name"]: instrument["readings"] for instrument in summary["instruments"]}
        assert recorded["daq"] == []
        assert [entry["key"] for entry in recorded["psu"]] == ["output_enabled", "voltage"]


class TestWhatARunRecords:
    def test_a_run_records_the_instruments_its_suite_requires(self, make_suite, settings) -> None:
        make_suite("brief", requires=["psu"], script=_BRIEF)
        with TestClient(create_app(settings)) as client:
            run_id = _run(client, "brief")
            summary = client.get(f"/api/runs/{run_id}/artifacts/instruments.json").json()
            assert [instrument["name"] for instrument in summary["instruments"]] == ["psu"]
            assert summary["instruments"][0]["readings"]

    def test_a_run_records_what_the_operator_asked_for_as_well(self, make_suite, settings) -> None:
        make_suite("brief", requires=["psu"], script=_BRIEF)
        with TestClient(create_app(settings)) as client:
            run_id = _run(client, "brief", observe=["daq", "psu"])
            summary = client.get(f"/api/runs/{run_id}/artifacts/instruments.json").json()
            assert [instrument["name"] for instrument in summary["instruments"]] == ["psu", "daq"]

    def test_a_run_requiring_nothing_records_nothing(self, make_suite, settings) -> None:
        make_suite("brief", script=_BRIEF)
        with TestClient(create_app(settings)) as client:
            run_id = _run(client, "brief")
            assert client.get(f"/api/runs/{run_id}/artifacts/instruments.jsonl").status_code == 404

    def test_an_instrument_this_bench_does_not_have_is_refused(self, make_suite, settings) -> None:
        make_suite("brief", script=_BRIEF)
        with TestClient(create_app(settings)) as client:
            refused = client.post("/api/runs", json={"suite": "brief", "observe": ["telescope"]})
            assert refused.status_code == 422
            assert "telescope" in refused.json()["detail"]

    def test_an_unavailable_instrument_is_refused(self, make_suite, settings) -> None:
        make_suite("brief", script=_BRIEF)
        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_Unavailable())
            refused = client.post("/api/runs", json={"suite": "brief", "observe": ["logic"]})
            assert refused.status_code == 422
            assert "not available" in refused.json()["detail"]


class _Unavailable:
    """A registered instrument whose hardware does not answer."""

    name = "logic"

    def available(self) -> bool:
        return False

    def describe(self) -> dict[str, str]:
        return {"driver": "test", "unavailable_reason": "nothing on the bus"}

    def instance_id(self) -> str:
        return "logic0"


def _run(client: TestClient, suite: str, observe: list[str] | None = None, timeout_s: float = 10.0) -> str:
    """Start a run, wait for it to finish, and return its id."""
    body: dict[str, object] = {"suite": suite}
    if observe is not None:
        body["observe"] = observe
    started = client.post("/api/runs", json=body)
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if client.get(f"/api/runs/{run_id}").json()["status"] in {"aborted", "error", "failed", "passed"}:
            return run_id
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never finished")
