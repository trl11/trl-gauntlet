"""The instrument panel API, and how it degrades for a provider that declares little."""

from __future__ import annotations

import textwrap
import time

_SLOW_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    sleep 30
    """
)


def _wait_until_idle(client, timeout_s=20.0) -> None:
    """Block until no run holds anything, so the next test starts clean."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        held = [i["in_use_by"] for i in client.get("/api/instruments").json()["instruments"]]
        if not any(held):
            return
        time.sleep(0.1)
    raise AssertionError(f"an instrument was still held after {timeout_s}s")


class TestInstrumentsApi:
    def test_lists_every_registered_instrument(self, client) -> None:
        instruments = client.get("/api/instruments").json()["instruments"]
        assert [i["name"] for i in instruments] == ["chamber", "daq", "i2c", "psu"]
        for instrument in instruments:
            assert instrument["available"] is True
            assert instrument["instance_id"]
            assert instrument["description"]
            assert instrument["kind"] == instrument["name"]

    def test_commands_declare_their_fields(self, client) -> None:
        psu = client.get("/api/instruments/psu").json()
        set_voltage = next(c for c in psu["commands"] if c["name"] == "set_voltage")
        voltage = next(f for f in set_voltage["fields"] if f["name"] == "voltage")
        assert voltage == {
            "name": "voltage",
            "label": "Voltage",
            "type": "number",
            "unit": "V",
            "min": 0.0,
            "max": 30.0,
            "choices": [],
        }

    def test_scan_reports_the_same_instruments(self, client) -> None:
        # The readings move between the two calls; the instruments do not.
        scanned = client.post("/api/instruments/rescan").json()["instruments"]
        listed = client.get("/api/instruments").json()["instruments"]
        assert [i["name"] for i in scanned] == [i["name"] for i in listed]
        assert [i["commands"] for i in scanned] == [i["commands"] for i in listed]

    def test_command_changes_the_state(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 7.5}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["result"] == {"channel": "1", "voltage_setpoint": 7.5}
        assert body["state"]["channels"]["1"]["voltage_setpoint"] == 7.5
        assert client.get("/api/instruments/psu").json()["state"]["channels"]["1"]["voltage_setpoint"] == 7.5

    def test_unknown_command_is_422(self, client) -> None:
        response = client.post("/api/instruments/psu/command", json={"command": "explode", "args": {}})
        assert response.status_code == 422
        assert "no command" in response.json()["detail"]

    def test_bad_argument_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 400}},
        )
        assert response.status_code == 422

    def test_unknown_instrument_is_404(self, client) -> None:
        assert client.get("/api/instruments/nope").status_code == 404
        assert client.post("/api/instruments/nope/command", json={"command": "x"}).status_code == 404

    def test_an_available_instrument_reports_no_reason(self, client) -> None:
        for instrument in client.get("/api/instruments").json()["instruments"]:
            assert instrument["unavailable_reason"] == ""

    def test_an_unavailable_instrument_reports_the_providers_reason(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        body = client.get("/api/instruments/bare").json()
        assert body["available"] is False
        assert body["unavailable_reason"] == "no device on /dev/ttyUSB9"

    def test_an_instrument_without_commands_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        response = client.post("/api/instruments/bare/command", json={"command": "x"})
        assert response.status_code == 405
        assert client.get("/api/instruments/bare").json()["commands"] == []
        assert client.get("/api/instruments/bare").json()["state"] == {}

    def test_a_provider_with_only_read_reports_that_as_its_state(self, client) -> None:
        client.app.state.capabilities.register(_ReadOnly())
        assert client.get("/api/instruments/readonly").json()["state"] == {"level": 3}

    def test_a_provider_that_names_no_kind_falls_back_to_its_name(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        assert client.get("/api/instruments/bare").json()["kind"] == "bare"

    def test_a_provider_with_a_model_but_no_description_uses_the_model(self, client) -> None:
        client.app.state.capabilities.register(_ReadOnly())
        assert client.get("/api/instruments/readonly").json()["description"] == "readonly-1"

    def test_a_body_without_a_command_is_422(self, client) -> None:
        assert client.post("/api/instruments/psu/command", json={"args": {}}).status_code == 422

    def test_an_unexpected_body_key_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_output", "args": {"channel": "1", "enabled": True}, "extra": 1},
        )
        assert response.status_code == 422

    def test_a_non_numeric_argument_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": "twelve"}},
        )
        assert response.status_code == 422
        assert "must be a number" in response.json()["detail"]

    def test_scan_reprobes_availability(self, client) -> None:
        flaky = _Flaky()
        client.app.state.capabilities.register(flaky)
        assert client.get("/api/instruments/flaky").json()["available"] is False
        flaky.present = True
        scanned = client.post("/api/instruments/rescan").json()["instruments"]
        assert next(i for i in scanned if i["name"] == "flaky")["available"] is True


class TestInstrumentsInUse:
    """Which run, if any, is driving each instrument."""

    def test_nothing_is_held_while_no_run_is_in_flight(self, client) -> None:
        for instrument in client.get("/api/instruments").json()["instruments"]:
            assert instrument["in_use_by"] == ""

    def test_a_run_holds_only_what_its_suite_requires(self, client, make_suite) -> None:
        make_suite("busy", requires=["psu"], script=_SLOW_SCRIPT)
        client.post("/api/suites/rescan")
        run_id = client.post("/api/runs", json={"suite": "busy"}).json()["run_id"]
        try:
            held = {i["name"]: i["in_use_by"] for i in client.get("/api/instruments").json()["instruments"]}
            assert held == {"chamber": "", "daq": "", "i2c": "", "psu": run_id}
        finally:
            client.post(f"/api/runs/{run_id}/abort")
        _wait_until_idle(client)

    def test_a_run_whose_suite_left_the_catalog_holds_nothing(self, client, make_suite) -> None:
        """`requires` is read from the manifest, so a rescan that drops the
        suite mid-run leaves nothing to say the instrument is held."""
        make_suite("busy", requires=["psu"], script=_SLOW_SCRIPT)
        client.post("/api/suites/rescan")
        run_id = client.post("/api/runs", json={"suite": "busy"}).json()["run_id"]
        try:
            (client.app.state.settings.suite_roots[0] / "busy" / "suite.yaml").unlink()
            client.post("/api/suites/rescan")

            held = {i["name"]: i["in_use_by"] for i in client.get("/api/instruments").json()["instruments"]}

            assert set(held.values()) == {""}
        finally:
            client.post(f"/api/runs/{run_id}/abort")
        _wait_until_idle(client)

    def test_a_finished_run_holds_nothing(self, client, make_suite) -> None:
        make_suite("brief", requires=["psu"])
        client.post("/api/suites/rescan")
        run_id = client.post("/api/runs", json={"suite": "brief"}).json()["run_id"]
        _wait_until_idle(client)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "passed"


class _Bare:
    """A provider with nothing but the four required members."""

    name = "bare"

    def available(self) -> bool:
        return False

    def describe(self) -> dict[str, str]:
        return {"description": "nothing to see", "unavailable_reason": "no device on /dev/ttyUSB9"}

    def instance_id(self) -> str:
        return "bare0"


class _Flaky:
    """A provider whose availability changes between probes."""

    name = "flaky"

    def __init__(self) -> None:
        self.present = False

    def available(self) -> bool:
        return self.present

    def describe(self) -> dict[str, str]:
        return {"description": "comes and goes"}

    def instance_id(self) -> str:
        return "flaky0"


class _ReadOnly:
    """A provider that can be read but publishes no structured state."""

    name = "readonly"

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"model": "readonly-1"}

    def instance_id(self) -> str:
        return "readonly0"

    def read(self) -> dict[str, object]:
        return {"level": 3}
